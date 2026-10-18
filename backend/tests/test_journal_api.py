"""``/api/v1/journal`` 契约（spec §S9.1/§S9.4）：鉴权 / 201 / 422 / 409 / 404 / stats 形状。

四组断言 + Phase B1 一组，每组对应 spec 的一句验收判据：

1. **不可篡改**（§S9.2 门禁）：路由表里 journal 只有 ``GET``/``POST``——
   源码里出现 ``@router.patch`` / ``@router.put`` / ``@router.delete`` 就红；
2. **3 行必填 + 三枚举**（§S9.1）：缺 ``thesis`` / ``horizon_days=15`` /
   ``invalidation`` 形状不对 → **422**；
3. **一次复盘一条**（§S9.4）：二次复盘 → **409**；非本人 → **404**（不泄露"有没有这条"）；
4. **PIT 在 API 层也成立**：创建后行情再涨，``GET /journal/{id}`` 的 ``outcome_pct``
   仍以**快照价**为起点（创建后新插的 K 线只影响期末价，不影响起始价）。
5. **Phase B1 闸**（§S9.7.1/§S9.7.5/§S9.7.8）：``position_size`` / ``confidence`` 越界 → **422**；
   自由文本失效条件 → **422 + 指导文案**；``reviewable=false`` 的条目复盘 → **422**（结果仍展示）。

**真 PG + 真路由**：``get_db`` 被替换成"同一个连接上的会话"，整个用例包在一个**回滚**
事务里（Ruling 17：跑完库与跑前一致）。鉴权用静态 RSA 公钥 + 自签 assertion
（与 ``test_watchlist_triage_api.py`` 同一套做法），**故意只给空权限**：
决策日志是"登录即可"，不需要 ``research:read``。
"""

from __future__ import annotations

import asyncio
import csv
import io
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
    # ★ spec §S9.7.3：bet_type 缺省 direction，基准口径**创建时**落进快照；
    # 该标的没有申万归属 → 退化为沪深300 且诚实标注 fallback（不是假装行业基准）
    assert body["bet_type"] == "direction"
    assert snapshot["bench_spec"]["bet_type"] == "direction"
    assert snapshot["bench_spec"]["kind"] == svc.BENCH_KIND_HS300
    assert snapshot["bench_spec"]["fallback"] is True
    assert snapshot["bench_basis_note"] == svc.BENCH_NOTE_FALLBACK
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
        {"bet_type": "hunch"},
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


# ── §S9.7.1 / §S9.7.5 / §S9.7.8：仓位 / 信心 / 可证伪闸 ─────────────────────


@pytest.mark.parametrize("position_size", ["half", "LIGHT", "", 2])
@requires_pg
async def test_create_rejects_position_size_outside_enum(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    position_size: Any,
) -> None:
    """spec §S9.7.1：``position_size`` 只接受 ``none|light|normal|heavy``，越界 422。"""
    app, session = journal_app
    await _seed(session)
    resp = await _post(
        app,
        "/api/v1/journal",
        token=_token(rsa_key_pair),
        json=_payload(position_size=position_size),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("confidence", [0, 6, -1, 2.5, 100])
@requires_pg
async def test_create_rejects_confidence_outside_1_to_5(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    confidence: Any,
) -> None:
    """spec §S9.7.5：``confidence`` 不在 1..5 → 422（不静默夹到边界）。"""
    app, session = journal_app
    await _seed(session)
    resp = await _post(
        app,
        "/api/v1/journal",
        token=_token(rsa_key_pair),
        json=_payload(confidence=confidence),
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "invalidation",
    [
        {"kind": "logic_broken", "value": "逻辑破坏"},
        {"kind": "text", "value": "跌穿成本就撤"},
    ],
)
@requires_pg
async def test_create_rejects_free_text_invalidation_with_422_and_guidance(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
    invalidation: dict[str, Any],
) -> None:
    """spec §S9.7.8：自由文本失效条件 → 422 **且**给"怎么改"的指导文案。"""
    app, session = journal_app
    await _seed(session)
    resp = await _post(
        app,
        "/api/v1/journal",
        token=_token(rsa_key_pair),
        json=_payload(invalidation=invalidation),
    )
    assert resp.status_code == 422, resp.text
    detail = str(resp.json())
    assert "价格或日期" in detail, "422 必须告诉用户可观测形态，而不是只说校验失败"
    for kind in ("price_below", "price_above", "date_only"):
        assert kind in detail, f"指导文案应列出可观测形态：{kind}"


@requires_pg
async def test_create_persists_position_size_and_confidence_and_echoes_them(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """创建可传、列表/详情带出；不传时 ``position_size=none`` / ``confidence=null``。"""
    app, session = journal_app
    await _seed(session)
    token = _token(rsa_key_pair)
    chosen = (await _create(app, token, position_size="heavy", confidence=4)).json()
    assert chosen["position_size"] == "heavy"
    assert chosen["confidence"] == 4
    assert chosen["reviewable"] is True
    default = (await _create(app, token)).json()
    assert default["position_size"] == "none"
    assert default["confidence"] is None, "没填信心 → null（不是默认 3，否则校准样本被污染）"

    listed = (await _get(app, "/api/v1/journal", token=token, params={"status": "open"})).json()
    by_id = {item["id"]: item for item in listed["items"]}
    assert by_id[chosen["id"]]["position_size"] == "heavy"
    assert by_id[chosen["id"]]["confidence"] == 4
    assert by_id[default["id"]]["position_size"] == "none"
    assert by_id[default["id"]]["confidence"] is None
    detail = (await _get(app, f"/api/v1/journal/{chosen['id']}", token=token)).json()
    assert (detail["position_size"], detail["confidence"], detail["reviewable"]) == (
        "heavy",
        4,
        True,
    )


async def _insert_unobservable_due_entry(session: AsyncSession, user_id: uuid.UUID) -> int:
    """直接写一条**历史形态**的条目（自由文本失效条件，当前 API 已不允许创建）。"""
    from app.models.journal import DecisionJournalEntry

    entry = DecisionJournalEntry(
        user_id=user_id,
        symbol=SYMBOL,
        kind="watch",
        bet_type="direction",
        position_size="none",
        thesis="逻辑破坏就撤（历史自由文本）",
        invalidation={"kind": "logic_broken", "value": "逻辑破坏"},
        horizon_days=7,
        snapshot={
            "version": "s9.r1",
            "as_of": SNAPSHOT_DAY.isoformat(),
            "quote_as_of": SNAPSHOT_DAY.isoformat(),
            "symbol": SYMBOL,
            "price": 100.0,
            "sector_flow_state": None,
            "missing_reason": {},
        },
        created_at=datetime(2026, 10, 1, 15, 0),
        due_on=REVIEW_DAY,
    )
    session.add(entry)
    await session.flush()
    return int(entry.id)


@requires_pg
async def test_unobservable_entry_is_reviewable_false_and_review_is_rejected(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """spec §S9.7.8：``reviewable=false`` 的条目**复盘端点拒绝**（422 + 明确文案）。

    库里仍可看到结果（``evaluation`` 照常下发），只是不给"归因"这个动作。
    """
    app, session = journal_app
    await _seed(session)
    user_id = uuid.uuid4()
    token = _token(rsa_key_pair, user_id)
    entry_id = await _insert_unobservable_due_entry(session, user_id)

    detail = (await _get(app, f"/api/v1/journal/{entry_id}", token=token)).json()
    assert detail["reviewable"] is False
    assert detail["status"] == "due"
    assert detail["evaluation"] is not None, "不可复盘 ≠ 不给结果：结果照常展示"
    assert detail["evaluation"]["invalidation_observable"] is False

    resp = await _post(
        app, f"/api/v1/journal/{entry_id}/review", token=token, json={"verdict": "thesis_wrong"}
    )
    assert resp.status_code == 422, resp.text
    message = str(resp.json())
    assert "不可复盘" in message
    assert "不可观测" in message


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
    # ★ spec §S9.7.2：净口径随 evaluation 一起下发（毛 − 12bp，逐值可核）
    assert evaluation["net_outcome_pct"] == pytest.approx(9.88, abs=1e-4)
    assert evaluation["net_excess_pct"] == pytest.approx(8.88, abs=1e-4)
    # ★ spec §S9.7.3：基准是沪深300（无行业 → fallback），文案由后端下发
    assert evaluation["bench_fallback"] is True
    assert evaluation["bench_basis_note"] == svc.BENCH_NOTE_FALLBACK
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
        # ★ Phase A（spec §S9.7.6/§S9.7.2/§S9.7.7）
        "by_kind",
        "avg_net_excess_pct",
        "share_ci",
        "net_cost_bps",
        "multiple_comparison_note",
        "avg_excess_pct",
        "discipline_break_share",
        # ★ Phase B2（spec §S9.7.1/§S9.7.5/§S9.7.9）
        "by_size",
        "avg_weighted_excess_pct",
        "by_kind_median",
        "calibration",
        "calibration_note",
        "export_hint",
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
    # ★ pass 分栏在（合计 = n），净口径与成本常量在（一条 watch：excess 9.0 → 净 8.88）
    assert stats["by_kind"] == {"watch": 1, "buy": 0, "sell": 0, "pass": 0, "hold": 0}
    assert sum(stats["by_kind"].values()) == stats["n"]
    assert stats["avg_excess_pct"] == pytest.approx(9.0, abs=1e-4)
    assert stats["avg_net_excess_pct"] == pytest.approx(8.88, abs=1e-4)
    assert stats["net_cost_bps"] == 12
    assert stats["multiple_comparison_note"] == svc.MULTIPLE_COMPARISON_NOTE
    # ★ 比例带区间（n=1 → 区间很宽，但绝不是 [1,1]）
    assert stats["share_ci"]["win"]["n"] == 1
    assert stats["share_ci"]["win"]["successes"] == 1
    assert stats["share_ci"]["win"]["ci_low"] == pytest.approx(0.2065, abs=1e-4)
    # ★ Phase B2：仓位分栏（合计 = n）、每类中位净超额、校准样本不足、导出地址
    assert stats["by_size"] == {"none": 1, "light": 0, "normal": 0, "heavy": 0}
    assert sum(stats["by_size"].values()) == stats["n"]
    assert stats["avg_weighted_excess_pct"] is None, "只有 none（权重 0）→ 没有可加权样本"
    assert stats["by_kind_median"]["watch"] == {
        "count": 1,
        "median_net_excess_pct": pytest.approx(8.88, abs=1e-4),
    }
    assert stats["calibration"] is None, "1 < 20 → 不给曲线"
    assert "样本不足" in stats["calibration_note"]
    assert stats["export_hint"] == "/api/v1/journal/export?format=csv"


# ── §S9.7.9 导出：只含本人 + 固定列序 + BOM ─────────────────────────────────


@requires_pg
async def test_export_returns_only_the_owners_entries(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """两个用户交叉：A 的导出里有且只有 A 的条目（JSON 与 CSV 都断言）。"""
    app, session = journal_app
    await _seed(session)
    alice = _token(rsa_key_pair)
    bob = _token(rsa_key_pair)
    mine = (await _create(app, alice, thesis="A 的判断：猪价见底")).json()
    theirs = (await _create(app, bob, thesis="B 的判断：白酒提价")).json()

    json_resp = await _get(app, "/api/v1/journal/export", token=alice, params={"format": "json"})
    assert json_resp.status_code == 200, json_resp.text
    assert "attachment" in json_resp.headers["content-disposition"]
    payload = json_resp.json()
    assert payload["count"] == 1
    assert [entry["id"] for entry in payload["entries"]] == [mine["id"]]
    assert payload["entries"][0]["snapshot"]["price"] == 100.0
    assert payload["entries"][0]["thesis"] == "A 的判断：猪价见底"
    assert theirs["id"] not in [entry["id"] for entry in payload["entries"]]
    assert "B 的判断" not in json_resp.text

    csv_resp = await _get(app, "/api/v1/journal/export", token=alice, params={"format": "csv"})
    assert csv_resp.status_code == 200, csv_resp.text
    assert "attachment" in csv_resp.headers["content-disposition"]
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.content.startswith(b"\xef\xbb\xbf"), "CSV 必须有 UTF-8 BOM"
    text = csv_resp.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[0] == list(svc.EXPORT_CSV_COLUMNS)
    assert [row[0] for row in rows[1:]] == [str(mine["id"])]
    assert "A 的判断：猪价见底" in text
    assert "B 的判断" not in text, "导出不得含他人数据（spec §S9.7.9 的验收判据）"


@requires_pg
async def test_export_rejects_unknown_format(
    journal_app: tuple[FastAPI, AsyncSession],
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """``format`` 只接受 ``json`` / ``csv``：越界 **422**（不静默退回 json）。"""
    app, session = journal_app
    await _seed(session)
    resp = await _get(
        app, "/api/v1/journal/export", token=_token(rsa_key_pair), params={"format": "xlsx"}
    )
    assert resp.status_code == 422, resp.text
