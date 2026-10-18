"""``/api/v1/journal`` 契约（spec §S9.1/§S9.4）：鉴权 / 201 / 422 / 409 / 404 / stats 形状。

四组断言，每组对应 spec 的一句验收判据：

1. **不可篡改**（§S9.2 门禁）：路由表里 journal 只有 ``GET``/``POST``——
   源码里出现 ``@router.patch`` / ``@router.put`` / ``@router.delete`` 就红；
2. **3 行必填 + 三枚举**（§S9.1）：缺 ``thesis`` / ``horizon_days=15`` /
   ``invalidation`` 形状不对 → **422**；
3. **一次复盘一条**（§S9.4）：二次复盘 → **409**；非本人 → **404**（不泄露"有没有这条"）；
4. **PIT 在 API 层也成立**：创建后行情再涨，``GET /journal/{id}`` 的 ``outcome_pct``
   仍以**快照价**为起点（创建后新插的 K 线只影响期末价，不影响起始价）。

**真 PG + 真路由**：``get_db`` 被替换成"同一个连接上的会话"，整个用例包在一个**回滚**
事务里（Ruling 17：跑完库与跑前一致）。鉴权用静态 RSA 公钥 + 自签 assertion
（与 ``test_watchlist_triage_api.py`` 同一套做法），**故意只给空权限**：
决策日志是"登录即可"，不需要 ``research:read``。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.deps import get_db
from app.api.v1 import journal as journal_api
from app.api.v1 import router as api_v1_router
from app.config import settings
from app.core.auth.jwks import jwks_client
from app.models.index_daily import IndexDaily
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.services import journal_service as svc

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

SYMBOL = "T90101"
QUOTE_DAYS = 30
TODAY = svc.today_sh()
#: 快照落点：今天之前最近的"交易日"（合成数据，不用真实日历）
SNAPSHOT_DAY = TODAY - timedelta(days=1)
REVIEW_DAY = TODAY


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


_PG_OK = _pg_reachable()

requires_pg = pytest.mark.skipif(
    not _PG_OK, reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


# ── 鉴权：静态 RSA 公钥 + 自签 assertion（空权限 = 登录即可） ────────────────


@pytest.fixture(scope="session")
def rsa_key_pair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    return priv, priv.public_key(), "test-journal-key-2026"


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
        "username": "journal_user",
        "roles": ["researcher"],
        # 故意**不给** research:read：决策日志是"登录即可"
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
async def journal_app() -> AsyncGenerator[tuple[FastAPI, AsyncSession], None]:
    engine = create_async_engine(ASYNC_URL)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn)
    app = FastAPI(title="Journal API Test App")
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


async def _seed(session: AsyncSession) -> None:
    """合成底座：1 只股票 + 30 天行情（末收 100）+ 沪深300 两天。"""
    await session.execute(
        insert(Stock),
        [
            {
                "exchange": "Shanghai_Stocks",
                "symbol": SYMBOL,
                "name": f"测试{SYMBOL}",
                "category": "A股",
                "asof": datetime(2026, 10, 16, 18, 0),
            }
        ],
    )
    stock_id = int(
        (await session.execute(select(Stock.id).where(Stock.symbol == SYMBOL))).scalar_one()
    )
    days = [SNAPSHOT_DAY - timedelta(days=QUOTE_DAYS - 1 - i) for i in range(QUOTE_DAYS)]
    await session.execute(
        insert(DailyQuote),
        [
            {
                "stock_id": stock_id,
                "trade_date": day,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "pre_close": 100.0,
                "amount": 1_000_000.0,
            }
            for day in days
        ],
    )
    await session.execute(
        insert(IndexDaily),
        [
            {"ts_code": svc.HS300_TS_CODE, "trade_date": SNAPSHOT_DAY, "close": 4000.0},
            {"ts_code": svc.HS300_TS_CODE, "trade_date": REVIEW_DAY, "close": 4040.0},
        ],
    )
    await session.flush()


async def _post(
    app: FastAPI, path: str, *, token: str | None, json: dict[str, Any] | None = None
) -> Any:
    headers = {"X-Principal-Assertion": token} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, headers=headers, json=json)


async def _get(
    app: FastAPI,
    path: str,
    *,
    token: str | None,
    params: dict[str, Any] | None = None,
) -> Any:
    headers = {"X-Principal-Assertion": token} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, headers=headers, params=params)


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": SYMBOL,
        "kind": "watch",
        "thesis": "猪价见底，产能去化加速",
        "invalidation": {"kind": "price_below", "value": 90.0},
        "horizon_days": 7,
    }
    payload.update(overrides)
    return payload


async def _create(app: FastAPI, token: str, **overrides: Any) -> Any:
    return await _post(app, "/api/v1/journal", token=token, json=_payload(**overrides))


# ── 不可篡改：路由表 + 源码 grep ────────────────────────────────────────────


def test_source_has_no_patch_put_delete_decorators() -> None:
    r"""spec §S9.2 门禁：路由源码里**没有** ``@router.patch/put/delete`` 装饰器。

    断言按**行首装饰器**匹配（``^\s*@router\.(patch|put|delete)``）：docstring 里
    解释"为什么不允许"时必然要写出这几个词，用裸子串匹配会把注释判成违规
    （判据必须能区分"代码里有"和"文档里说不能有"）。
    """
    source = Path(journal_api.__file__).read_text(encoding="utf-8")
    found = re.findall(r"^[ \t]*@router\.(?:patch|put|delete)\b", source, re.MULTILINE)
    assert found == [], f"决策日志不得提供 PATCH/PUT/DELETE（创建后不可改）：{found}"


@requires_pg
async def test_routes_expose_only_get_and_post(journal_app: tuple[FastAPI, AsyncSession]) -> None:
    """整张路由表里 journal 只有 GET/POST（含未来被别人加进来的路由）。"""
    app, _ = journal_app
    seen: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/journal"):
            continue
        for method in getattr(route, "methods", set()) or set():
            seen.add(method)
    assert seen == {"GET", "POST"}, f"journal 路由出现了非 GET/POST 方法：{seen}"


# ── 鉴权 ───────────────────────────────────────────────────────────────────


@requires_pg
async def test_every_endpoint_requires_login(journal_app: tuple[FastAPI, AsyncSession]) -> None:
    app, session = journal_app
    await _seed(session)
    assert (await _get(app, "/api/v1/journal", token=None)).status_code == 401
    assert (await _get(app, "/api/v1/journal/stats", token=None)).status_code == 401
    assert (await _get(app, "/api/v1/journal/1", token=None)).status_code == 401
    assert (await _post(app, "/api/v1/journal", token=None, json=_payload())).status_code == 401
    assert (
        await _post(app, "/api/v1/journal/1/review", token=None, json={"verdict": "luck"})
    ).status_code == 401


# ── 创建：201 + 快照落库 + 422 ─────────────────────────────────────────────


@requires_pg
async def test_create_returns_201_with_persisted_snapshot(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    app, session = journal_app
    await _seed(session)
    token = _token(rsa_key_pair)
    resp = await _create(app, token)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["symbol"] == SYMBOL
    assert body["horizon_days"] == 7
    assert body["due_on"] == (TODAY + timedelta(days=7)).isoformat()
    assert body["status"] == "open"
    # 快照是**后端落库**的（不是前端拼的）：价格等字段来自行情库
    snapshot = body["snapshot"]
    assert snapshot["price"] == 100.0
    assert snapshot["quote_as_of"] == SNAPSHOT_DAY.isoformat()
    assert "missing_reason" in snapshot
    # 列表/创建响应自带"快照摘要（1 行）"（spec §S9.1；前端优先用它，缺省才自己拼）
    assert body["snapshot_summary"].startswith("价格 100.00 元")
    # 列表立刻能看到（open）
    listed = (await _get(app, "/api/v1/journal", token=token, params={"status": "open"})).json()
    assert [item["id"] for item in listed["items"]] == [body["id"]]
    assert listed["snapshot_summary"]["open"] == 1
    assert listed["snapshot_summary"]["due"] == 0
    assert listed["disclaimer"] == journal_api.DISCLAIMER
    # 列表项**不**逐条算结果（避免 N+1）；结果在单条详情里
    assert listed["items"][0]["evaluation"] is None
    # 未到期的单条详情也不给"结果"（spec §S9.4：到期后、复盘前才展示）
    detail = (await _get(app, f"/api/v1/journal/{body['id']}", token=token)).json()
    assert detail["status"] == "open"
    assert detail["evaluation"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"thesis": ""},
        {},
        {"horizon_days": 15},
        {"invalidation": {"kind": "price_below"}},
        {"invalidation": {"kind": "price_below", "value": -1}},
        {"invalidation": {"kind": "someday", "value": "2026-12-31"}},
        {"kind": "hunch"},
        {"symbol": "X" * 17},
    ],
)
@requires_pg
async def test_create_rejects_invalid_payload_with_422(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    overrides: dict[str, Any],
) -> None:
    app, session = journal_app
    await _seed(session)
    payload = _payload(**overrides)
    if overrides == {}:
        payload.pop("thesis")  # 缺一必填 → 422
    resp = await _post(app, "/api/v1/journal", token=_token(rsa_key_pair), json=payload)
    assert resp.status_code == 422, resp.text


@requires_pg
async def test_list_rejects_unknown_status(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """``status`` 只接受三态：越界 → 422（不是静默返回全部）。"""
    app, session = journal_app
    await _seed(session)
    resp = await _get(
        app,
        "/api/v1/journal",
        token=_token(rsa_key_pair),
        params={"status": "archived"},
    )
    assert resp.status_code == 422, resp.text


# ── 复盘：PIT / 409 / 404 / 422 ────────────────────────────────────────────


async def _make_due(session: AsyncSession, entry_id: int) -> None:
    """把条目改成"今天到期"，并补一根今天的 K 线（期末 110）——起始价仍取快照 100。"""
    stock_id = int(
        (await session.execute(select(Stock.id).where(Stock.symbol == SYMBOL))).scalar_one()
    )
    await session.execute(
        insert(DailyQuote),
        [
            {
                "stock_id": stock_id,
                "trade_date": REVIEW_DAY,
                "open": 105.0,
                "high": 111.0,
                "low": 104.0,
                "close": 110.0,
                "pre_close": 100.0,
                "amount": 2_000_000.0,
            }
        ],
    )
    await session.execute(
        text("UPDATE decision_journal_entries SET due_on = :due WHERE id = :entry_id"),
        {"due": REVIEW_DAY, "entry_id": entry_id},
    )
    await session.flush()


@requires_pg
async def test_review_is_201_and_then_409(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    app, session = journal_app
    await _seed(session)
    token = _token(rsa_key_pair)
    created = (await _create(app, token)).json()
    await _make_due(session, created["id"])

    # 到期列表项**自带**自动结果（前端复盘弹窗只读列表项，不调单条详情）
    due_list = (await _get(app, "/api/v1/journal", token=token, params={"status": "due"})).json()
    assert [item["id"] for item in due_list["items"]] == [created["id"]]
    assert due_list["items"][0]["evaluation"]["outcome_pct"] == pytest.approx(10.0, abs=1e-4)
    assert not due_list["truncated"]

    # 到期自动结果（单条详情）：起始价 = 快照价 100，期末 110 ⇒ +10%
    detail = (await _get(app, f"/api/v1/journal/{created['id']}", token=token)).json()
    evaluation = detail["evaluation"]
    assert evaluation["outcome_pct"] == pytest.approx(10.0, abs=1e-4)
    assert evaluation["bench_pct"] == pytest.approx(1.0, abs=1e-4)  # 沪深300 4000→4040
    assert evaluation["excess_pct"] == pytest.approx(9.0, abs=1e-4)
    assert detail["status"] == "due"

    first = await _post(
        app,
        f"/api/v1/journal/{created['id']}/review",
        token=token,
        json={"verdict": "discipline_break", "note": "追高"},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["verdict"] == "discipline_break"
    assert body["outcome_pct"] == pytest.approx(10.0, abs=1e-4)
    assert body["invalidation_hit"] is False  # 窗口最低 104 > 阈值 90
    # 已复盘 → 移到 reviewed，二次复盘 409（不是静默覆盖）
    reviewed = (
        await _get(app, "/api/v1/journal", token=token, params={"status": "reviewed"})
    ).json()
    assert [item["id"] for item in reviewed["items"]] == [created["id"]]
    second = await _post(
        app,
        f"/api/v1/journal/{created['id']}/review",
        token=token,
        json={"verdict": "luck"},
    )
    assert second.status_code == 409, second.text


@requires_pg
async def test_review_rejects_verdict_outside_enum(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    app, session = journal_app
    await _seed(session)
    token = _token(rsa_key_pair)
    created = (await _create(app, token)).json()
    await _make_due(session, created["id"])
    for body in ({"verdict": "gambling"}, {}):
        resp = await _post(app, f"/api/v1/journal/{created['id']}/review", token=token, json=body)
        assert resp.status_code == 422, resp.text


@requires_pg
async def test_foreign_entry_is_404(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """非本人条目 → 404（GET 与 review 都是）：不泄露"别人有没有这条"。"""
    app, session = journal_app
    await _seed(session)
    owner = _token(rsa_key_pair)
    created = (await _create(app, owner)).json()
    await _make_due(session, created["id"])
    foreign = _token(rsa_key_pair)
    assert (await _get(app, f"/api/v1/journal/{created['id']}", token=foreign)).status_code == 404
    assert (
        await _post(
            app,
            f"/api/v1/journal/{created['id']}/review",
            token=foreign,
            json={"verdict": "luck"},
        )
    ).status_code == 404
    # 不存在 id 也是 404
    assert (await _get(app, "/api/v1/journal/999999999", token=owner)).status_code == 404


# ── stats ──────────────────────────────────────────────────────────────────


@requires_pg
async def test_stats_shape_and_sample_gate(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    app, session = journal_app
    await _seed(session)
    token = _token(rsa_key_pair)
    created = (await _create(app, token)).json()
    await _make_due(session, created["id"])
    await _post(
        app,
        f"/api/v1/journal/{created['id']}/review",
        token=token,
        json={"verdict": "luck"},
    )
    stats = (await _get(app, "/api/v1/journal/stats", token=token)).json()
    assert set(stats) >= {
        "n",
        "reviewed_n",
        "by_verdict",
        "by_attribution",
        "avg_excess_pct",
        "discipline_break_share",
    }
    assert stats["n"] == 1
    assert stats["reviewed_n"] == 1
    assert sum(stats["by_attribution"].values()) == stats["reviewed_n"]
    assert stats["by_attribution"]["luck"] == 1
    assert stats["by_verdict"]["luck"] == 1
    assert isinstance(stats["by_verdict"]["luck"], int)
    # 样本不足（<5）：占比为 null，不是 0
    assert stats["sample_sufficient"] is False
    assert stats["discipline_break_share"] is None
    assert set(stats["by_verdict"]) >= set(svc.VERDICTS)
