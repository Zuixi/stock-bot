"""``/api/v1/content`` 读面契约（S3.x）：列表 / 详情 / 用户状态隔离。

钉的四条产品判据：

1. **``/news`` 级公开**：匿名 ``GET /api/v1/content?content_type=announcement`` → 200，
   且 ``user_state`` 是 **``null``**（不是 ``"new"``）——前端据此区分"没登录"与
   "标记为未读"；
2. **详情才有指标**：列表 ``metrics`` 恒为空列表、详情非空（列表不展示指标，30 条 × N
   指标会把响应撑大数倍）；详情 ``entities`` 非空；
3. **用户状态是私有的**：A 标"已读"之后，**B 仍是 ``null``**，A 是 ``"read"``
   （事实层没有用户状态列，写在 ``user_content_state``）；
4. **成对实体过滤**：只给 ``entity_type`` → **422**（单独按类型查会扫全表）；
   匿名带 ``status`` 过滤 → **401**（筛的是"我的"状态）。

**真 PG + 真路由**：``get_db`` 换成"同一个连接上的会话"，整个用例包在一个**回滚**
事务里（跑完库与跑前一致）；``finally`` 里另做一次显式删除，失败路径也不留脏行。
鉴权用静态 RSA 公钥 + 自签 assertion（与 ``test_journal_api.py`` 同一套做法）。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.deps import get_db
from app.api.v1 import router as api_v1_router
from app.config import settings
from app.core.auth.jwks import jwks_client
from app.models.content import ContentEntity, ContentItem, ContentMetric, UserContentState

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

#: 测试数据前缀：清理与断言都只认它，绝不碰真实内容行
TEST_SOURCE = "test"
#: 排他性发布时点：未来的时间戳保证两条测试行排在本页最前（否则被真实内容挤出第一页）
PUBLISHED = datetime(2099, 1, 2, 9, 30, tzinfo=UTC)


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


def _content_tables_ready() -> bool:
    """四张内容表是否都在（共享测试库会被别的用例 ``alembic downgrade`` 抽掉）。

    ``tests/conftest.py`` 的 ``_schema_at_head`` docstring 已记录这个坑：库的迁移
    状态是共享资源，别的用例真跑 ``downgrade`` 时表会暂时消失。此时跳过而不是报
    ``UndefinedTableError``（那是环境问题，不是本文件契约问题）。
    """

    async def _go() -> bool:
        conn = await asyncpg.connect(SYNC_URL)
        try:
            found = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
                ["content_items", "content_entities", "content_metrics", "user_content_state"],
            )
            return len(found) == 4
        finally:
            await conn.close()

    try:
        return asyncio.run(_go())
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_reachable() or not _content_tables_ready(),
    reason=(
        f"PostgreSQL unavailable at {SYNC_URL} or content tables missing "
        "(set TEST_DATABASE_URL; 共享测试库可能正被别的用例 downgrade)"
    ),
)


# ── 鉴权：静态 RSA 公钥 + 自签 assertion（空权限 = 登录即可） ────────────────


@pytest.fixture(scope="session")
def rsa_key_pair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    return priv, priv.public_key(), "test-content-key-2026"


@pytest.fixture(autouse=True)
def _static_jwks(rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]) -> None:
    _, pub, kid = rsa_key_pair
    jwks_client.set_static_key(kid, pub)


def _token(
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    user_id: uuid.UUID | None = None,
) -> str:
    priv, _, kid = rsa_key_pair
    now = int(time.time())
    payload = {
        "iss": settings.auth_issuer,
        "sub": str(user_id or uuid.uuid4()),
        "aud": settings.auth_audience,
        "username": "content_user",
        "roles": ["researcher"],
        # 故意**不给** research:read：内容读面是"登录即可 / 匿名可读"
        "permissions": [],
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": f"ast_{uuid.uuid4().hex}",
        "session_id": f"sess_{uuid.uuid4().hex[:8]}",
    }
    return jwt.encode(payload, priv, algorithm="RS256", headers={"kid": kid, "typ": "JWT"})


# ── 测试应用：真 v1 路由 + 回滚事务上的真会话 ────────────────────────────────


@pytest_asyncio.fixture
async def content_app() -> AsyncGenerator[tuple[FastAPI, AsyncSession], None]:
    engine = create_async_engine(ASYNC_URL)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn)
    app = FastAPI(title="Content API Test App")
    app.include_router(api_v1_router)

    async def _override_db() -> AsyncGenerator[AsyncSession, None]:
        # 每个请求一个新会话（同一连接 / 同一事务）：共享未提交的种子数据
        request_session = AsyncSession(bind=conn)
        try:
            yield request_session
        finally:
            await request_session.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        yield app, session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[int, int]:
    """两条``announcement``（一条带实体+指标）+ 一条 ``news``，返回两条 announcement 的 id。"""
    tag = uuid.uuid4().hex[:8]
    rows = [
        {
            "content_type": "announcement",
            "source_name": TEST_SOURCE,
            "publisher_tier": "legal",
            "publisher": "测试交易所",
            "title": f"测试公告A {tag}",
            "published_at": PUBLISHED,
            "url": f"https://example.test/content/{tag}/a",
            "raw_hash": f"{tag}a"[:64],
            "extracted": {"subtype": "performance_forecast"},
            "review_status": "auto",
        },
        {
            "content_type": "announcement",
            "source_name": TEST_SOURCE,
            "publisher_tier": "legal",
            "publisher": "测试交易所",
            "title": f"测试公告B {tag}",
            "published_at": PUBLISHED.replace(day=1),
            "url": f"https://example.test/content/{tag}/b",
            "raw_hash": f"{tag}b"[:64],
            "extracted": {},
            "review_status": "auto",
        },
        {
            "content_type": "news",
            "source_name": TEST_SOURCE,
            "publisher_tier": "media",
            "publisher": "测试媒体",
            "title": f"测试快讯 {tag}",
            "published_at": PUBLISHED.replace(day=1),
            "url": f"https://example.test/content/{tag}/c",
            "raw_hash": f"{tag}c"[:64],
            "extracted": {},
            "review_status": "auto",
        },
    ]
    await session.execute(insert(ContentItem), rows)

    ids = (
        await session.execute(
            select(ContentItem.id, ContentItem.title).where(
                ContentItem.raw_hash.in_([r["raw_hash"] for r in rows])
            )
        )
    ).all()
    by_title = {title: int(cid) for cid, title in ids}
    first = by_title[f"测试公告A {tag}"]
    second = by_title[f"测试公告B {tag}"]

    await session.execute(
        insert(ContentEntity),
        [{"content_id": first, "entity_type": "stock", "entity_id": "600000"}],
    )
    await session.execute(
        insert(ContentMetric),
        [
            {
                "content_id": first,
                "entity_type": "stock",
                "entity_id": "600000",
                "metric_key": "net_profit",
                "period": "2026H1",
                "value": 1234.5,
                "unit": "元",
                "source_span": {"kind": "structured", "field": "net_profit"},
            }
        ],
    )
    await session.flush()
    return first, second


async def _cleanup(session: AsyncSession, content_ids: list[int]) -> None:
    """显式删除测试行（子表靠 ``ON DELETE CASCADE``）；失败路径也不留脏行。

    只按**本次插入的 id** 删：``source_name`` 是共享测试前缀，按它删会误伤其他
    测试文件的行。
    """
    if content_ids:
        await session.execute(
            delete(UserContentState).where(UserContentState.content_id.in_(content_ids))
        )
        await session.execute(delete(ContentItem).where(ContentItem.id.in_(content_ids)))
    await session.flush()


async def _get(
    app: FastAPI,
    path: str,
    *,
    token: str | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    headers = {"X-Principal-Assertion": token} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers=headers, params=params)


async def _patch(app: FastAPI, path: str, *, token: str | None, json: dict[str, Any]) -> Any:
    headers = {"X-Principal-Assertion": token} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.patch(path, headers=headers, json=json)


# ── 用例 ────────────────────────────────────────────────────────────────────


@requires_pg
async def test_list_is_public_and_user_state_is_null_for_anonymous(
    content_app: tuple[FastAPI, AsyncSession],
) -> None:
    app, session = content_app
    content_ids: list[int] = []
    try:
        first, _ = await _seed(session)
        content_ids = [first]

        resp = await _get(app, "/api/v1/content", params={"content_type": "announcement"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 裸数组：前端直接当列表用，没有 {items: …} 信封
        assert isinstance(body, list) and body, body
        ours = [row for row in body if row["id"] == first]
        assert ours, "测试内容行不在第一页（发布时点排序失效）"
        item = ours[0]
        assert item["content_type"] == "announcement"
        assert item["source_name"] == TEST_SOURCE
        assert item["user_state"] is None, "匿名读到的 user_state 必须是 null（不是 'new'）"
        assert item["metrics"] == [], "列表响应不下发指标（详情才填充）"
        assert [e["entity_id"] for e in item["entities"]] == ["600000"]
        assert all(row["user_state"] is None for row in body)
        # 类型过滤生效：news 行不出现
        assert all(row["content_type"] == "announcement" for row in body)
    finally:
        await _cleanup(session, content_ids)


@requires_pg
async def test_detail_returns_entities_and_metrics(
    content_app: tuple[FastAPI, AsyncSession],
) -> None:
    app, session = content_app
    content_ids: list[int] = []
    try:
        first, _ = await _seed(session)
        content_ids = [first]

        resp = await _get(app, f"/api/v1/content/{first}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == first
        assert body["entities"], "详情必须带实体（否则前端无法跳转个股）"
        assert body["entities"][0]["entity_type"] == "stock"
        assert body["metrics"], "详情必须带抽取指标"
        metric = body["metrics"][0]
        assert metric["metric_key"] == "net_profit"
        assert metric["period"] == "2026H1"
        assert metric["value"] == pytest.approx(1234.5)
        assert metric["source_span"]["kind"] == "structured", "没有原文定位就没有溯源"

        missing = await _get(app, "/api/v1/content/999999999")
        assert missing.status_code == 404
    finally:
        await _cleanup(session, content_ids)


@requires_pg
async def test_user_state_is_per_user(
    content_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    app, session = content_app
    content_ids: list[int] = []
    try:
        first, _ = await _seed(session)
        content_ids = [first]
        user_a = _token(rsa_key_pair)
        user_b = _token(rsa_key_pair)

        patched = await _patch(
            app, f"/api/v1/content/{first}/state", token=user_a, json={"status": "read"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json() == {"status": "read"}

        # B 看不到 A 的已读（事实层没有用户状态列）
        b_body = (await _get(app, f"/api/v1/content/{first}", token=user_b)).json()
        assert b_body["user_state"] is None
        # A 自己看得到
        a_body = (await _get(app, f"/api/v1/content/{first}", token=user_a)).json()
        assert a_body["user_state"] == "read"

        # 列表也带同样的状态；status 过滤只命中 A 的那条
        a_list = (await _get(app, "/api/v1/content", token=user_a)).json()
        assert next(row for row in a_list if row["id"] == first)["user_state"] == "read"
        a_filtered = (await _get(app, "/api/v1/content", token=user_a, params={"status": "read"}))
        assert a_filtered.status_code == 200, a_filtered.text
        assert first in [row["id"] for row in a_filtered.json()]
        b_filtered = (await _get(app, "/api/v1/content", token=user_b, params={"status": "read"}))
        assert b_filtered.status_code == 200
        assert first not in [row["id"] for row in b_filtered.json()]

        # 再次 PATCH 同一状态是幂等的（不是 409）
        again = await _patch(
            app, f"/api/v1/content/{first}/state", token=user_a, json={"status": "read"}
        )
        assert again.status_code == 200
    finally:
        await _cleanup(session, content_ids)


@requires_pg
async def test_entity_filter_requires_both_params(
    content_app: tuple[FastAPI, AsyncSession],
) -> None:
    app, session = content_app
    content_ids: list[int] = []
    try:
        first, _ = await _seed(session)
        content_ids = [first]

        resp = await _get(app, "/api/v1/content", params={"entity_type": "stock"})
        assert resp.status_code == 422, "只给 entity_type 无法定位实体，必须 422"

        ok = await _get(
            app, "/api/v1/content", params={"entity_type": "stock", "entity_id": "600000"}
        )
        assert ok.status_code == 200, ok.text
        assert first in [row["id"] for row in ok.json()]
    finally:
        await _cleanup(session, content_ids)


@requires_pg
async def test_status_filter_and_state_patch_require_login(
    content_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    app, session = content_app
    content_ids: list[int] = []
    try:
        first, _ = await _seed(session)
        content_ids = [first]

        anon = await _get(app, "/api/v1/content", params={"status": "read"})
        assert anon.status_code == 401, "匿名带 status（筛的是'我的'状态）→ 401"

        anon_patch = await _patch(
            app, f"/api/v1/content/{first}/state", token=None, json={"status": "read"}
        )
        assert anon_patch.status_code == 401

        token = _token(rsa_key_pair)
        # 状态入参只有四枚举：``new`` / 拼错的值 → 422（不是默默写成别的状态）
        bad = await _patch(
            app, f"/api/v1/content/{first}/state", token=token, json={"status": "new"}
        )
        assert bad.status_code == 422
        # 内容不存在 → 404（不是静默 200）
        missing = await _patch(
            app, "/api/v1/content/999999999/state", token=token, json={"status": "read"}
        )
        assert missing.status_code == 404
    finally:
        await _cleanup(session, content_ids)
