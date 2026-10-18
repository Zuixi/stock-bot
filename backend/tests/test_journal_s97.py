"""spec §S9.7 Phase A —— **净成本 / 基准 / Wilson 区间 / `pass` 一等公民**（真 PG + 纯函数）。

这一组用例只钉 Phase A 的四件事，每条都对应 spec 的一句验收判据：

1. **净成本**（§S9.7.2）：``net_outcome_pct = outcome_pct - 0.12`` 逐值断言（不是"约等于"），
   ``net_excess_pct = net_outcome_pct - bench_pct``；常量 ``COST_ROUND_TRIP_BPS = 12``
   与统计产物版本成对钉死（改一个不改另一个 → 这里红），并要求源码里写清假设与日期。
2. **基准**（§S9.7.3）：三种 ``bet_type`` 各自命中正确基准（``bench_pct`` 与**手工计算**
   对照：行业等权 = 成员收益算术平均；沪深300 = 指数起止）；无行业 → 沪深300 且
   ``bench_fallback = true``；``bench_basis_note`` 说人话。
3. **`pass` 一等公民**（§S9.7.6）：``by_kind`` 合计 = ``n``；``pass`` 计入判断统计、
   **不计入收益统计**（``avg_excess_pct`` / ``avg_net_excess_pct`` 排除它）。
4. **Wilson 区间**（§S9.7.7）：``0/0 → null`` · ``4/6 ≈ [0.30, 0.90]`` · ``5/5`` 不高估；
   ``stats.share_ci`` 给 ``n / successes / ci_low / ci_high``；``discipline_break_share``
   仍是 ``number | null``（**类型不变**）。

合成数据刻意用库里不存在的代码/行业（``T970xx`` / ``T97Lx``）与未来日期（2026-10），
所有写入包在**回滚**事务里（Ruling 17：跑完库与跑前一致）；库不可达时 skip 而非红。

手工计算对照表（下面每条都用它，改种子必须同时改这里）：

================  ====================  ======================================
标的              期初 → 期末           涨幅
================  ====================  ======================================
``T97001``        100 → 110             +10%
``T97002``        50 → 52.5             +5%
``T97003``        20 → 20               0%
``T97004``（无行业） 100 → 110            +10%
沪深300           4000 → 4100           +2.5%
行业等权          (10% + 5% + 0%) / 3   **+5%**
================  ====================  ======================================
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.index_daily import IndexDaily
from app.models.journal import BET_TYPES, DecisionJournalEntry, DecisionJournalReview
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.models.sw_industry import SwIndustryClass, SwIndustryMember
from app.services import journal_service as svc

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

AS_OF = date(2026, 10, 16)
DUE_ON = date(2026, 10, 21)
MAIN = "T97001"
MEMBER_A = "T97002"
MEMBER_B = "T97003"
ORPHAN = "T97004"  # 有行情、**没有**申万归属 → 基准必须退化为沪深300
L1, L2, L3 = "T97L1", "T97L2", "T97L3"
SYMBOLS = (MAIN, MEMBER_A, MEMBER_B, ORPHAN)

#: 起止收盘（手工计算对照表的第一列）
CLOSE_START = {MAIN: 100.0, MEMBER_A: 50.0, MEMBER_B: 20.0, ORPHAN: 100.0}
CLOSE_END = {MAIN: 110.0, MEMBER_A: 52.5, MEMBER_B: 20.0, ORPHAN: 110.0}
#: 沪深300 起止
HS300_START, HS300_END = 4000.0, 4100.0

#: 手工算出来的三个基准（**不调用被测实现**，避免"用实现证明实现"）
EXPECT_INDUSTRY_BENCH = round(
    sum((CLOSE_END[s] / CLOSE_START[s] - 1) * 100 for s in (MAIN, MEMBER_A, MEMBER_B)) / 3, 4
)
EXPECT_HS300_BENCH = round((HS300_END / HS300_START - 1) * 100, 4)
EXPECT_MAIN_OUTCOME = round((CLOSE_END[MAIN] / CLOSE_START[MAIN] - 1) * 100, 4)

#: spec §S9.7.7 的固定文案（契约 §4 逐字）
NOTE_MULTI = "未做多重比较校正：按 45 个桶检索时总会有桶看起来显著"


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


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


async def _with_rollback(factory: Any) -> Any:
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await factory(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    """合成底座：4 只股票（3 只有行业）+ 每个两日行情 + 沪深300 两日。"""
    await session.execute(
        insert(Stock),
        [
            {
                "exchange": "Shanghai_Stocks",
                "symbol": symbol,
                "name": f"测试{symbol}",
                "category": "A股",
                "asof": datetime(2026, 10, 16, 18, 0),
            }
            for symbol in SYMBOLS
        ],
    )
    id_of = {
        str(symbol): int(stock_id)
        for stock_id, symbol in (
            await session.execute(select(Stock.id, Stock.symbol).where(Stock.symbol.in_(SYMBOLS)))
        ).all()
    }
    await session.execute(
        insert(SwIndustryClass),
        [
            {"industry_code": L1, "level": 1, "industry_name": "测试一级", "parent_code": None},
            {"industry_code": L2, "level": 2, "industry_name": "测试二级", "parent_code": L1},
            {"industry_code": L3, "level": 3, "industry_name": "测试三级", "parent_code": L2},
        ],
    )
    # ORPHAN 故意不入申万（无行业归属 → 基准退化）
    await session.execute(
        insert(SwIndustryMember),
        [
            {"industry_code": L3, "stock_code": s, "symbol": s, "stock_name": s}
            for s in (MAIN, MEMBER_A, MEMBER_B)
        ],
    )
    quotes: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        start, end = CLOSE_START[symbol], CLOSE_END[symbol]
        for trade_date, close, low, high in (
            (AS_OF, start, start, start),
            (DUE_ON, end, min(start, end), max(start, end)),
        ):
            quotes.append(
                {
                    "stock_id": id_of[symbol],
                    "trade_date": trade_date,
                    "open": close,
                    "high": high,
                    "low": low,
                    "close": close,
                    "pre_close": start,
                    "amount": 1_000_000.0,
                }
            )
    await session.execute(insert(DailyQuote), quotes)
    await session.execute(
        insert(IndexDaily),
        [
            {"ts_code": svc.HS300_TS_CODE, "trade_date": AS_OF, "close": HS300_START},
            {"ts_code": svc.HS300_TS_CODE, "trade_date": DUE_ON, "close": HS300_END},
        ],
    )
    await session.flush()


def _snapshot(
    symbol: str | None,
    *,
    bet_type: str = "direction",
    sector: bool | None = None,
    with_bench: bool = True,
) -> dict[str, Any]:
    """造一份创建时的快照（``with_bench=False`` 用来模拟 s9.r1 的历史行）。

    ``sector`` 缺省按"这只股票到底有没有申万归属"决定：``ORPHAN`` 的快照在真实
    创建流程里 ``sector_flow_state`` 就是 ``null``（``no_sector_data``），所以它的
    ``bench_spec`` 必须退化——手造快照不能比真实流程更"理想"。
    """
    if sector is None:
        sector = symbol in (MAIN, MEMBER_A, MEMBER_B)
    snap: dict[str, Any] = {
        "version": svc.SNAPSHOT_VERSION if with_bench else "s9.r1",
        "as_of": AS_OF.isoformat(),
        "quote_as_of": AS_OF.isoformat() if symbol else None,
        "symbol": symbol,
        "price": CLOSE_START[symbol] if symbol else None,
        "sector_flow_state": (
            {"code": L1, "name": "测试一级", "pct": 0.0, "rank": 0.0} if sector and symbol else None
        ),
        "missing_reason": {},
    }
    if with_bench:
        spec = svc.bench_spec(
            symbol=symbol,
            bet_type=bet_type,
            sector=(
                snap["sector_flow_state"] if isinstance(snap["sector_flow_state"], dict) else None
            ),
        )
        snap["bench_spec"] = spec
        snap["bench_basis_note"] = svc.bench_note(spec)
    return snap


def _entry(
    *,
    user_id: uuid.UUID | None = None,
    symbol: str | None = MAIN,
    kind: str = "watch",
    bet_type: str = "direction",
    snapshot: dict[str, Any] | None = None,
    **overrides: Any,
) -> DecisionJournalEntry:
    payload: dict[str, Any] = {
        "user_id": user_id or uuid.uuid4(),
        "symbol": symbol,
        "kind": kind,
        "bet_type": bet_type,
        "thesis": "猪价见底，产能去化加速",
        "invalidation": {"kind": "price_below", "value": 50.0},
        "horizon_days": 7,
        "snapshot": snapshot if snapshot is not None else _snapshot(symbol, bet_type=bet_type),
        "created_at": datetime(2026, 10, 16, 15, 0),
        "due_on": DUE_ON,
    }
    payload.update(overrides)
    return DecisionJournalEntry(**payload)


async def _evaluate(session: AsyncSession, entry: DecisionJournalEntry) -> dict[str, Any]:
    session.add(entry)
    await session.flush()
    return await svc.evaluate_entry(session, entry, as_of=DUE_ON)


# ---------------------------------------------------------------------------
# §S9.7.3 基准：三种 bet_type 各自命中（与手工计算对照）
# ---------------------------------------------------------------------------


@requires_pg
async def test_direction_bench_is_industry_equal_weight() -> None:
    """``direction`` → 所属申万一级**成分股等权平均收益**（不是指数行情，须诚实标注）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        return await _evaluate(session, _entry(symbol=MAIN, bet_type="direction"))

    result = await _with_rollback(_go)
    assert EXPECT_INDUSTRY_BENCH == pytest.approx(5.0, abs=1e-9)  # 手工：10/5/0 的均值
    assert result["outcome_pct"] == pytest.approx(EXPECT_MAIN_OUTCOME, abs=1e-4)
    assert result["bench_pct"] == pytest.approx(EXPECT_INDUSTRY_BENCH, abs=1e-4)
    assert result["excess_pct"] == pytest.approx(
        EXPECT_MAIN_OUTCOME - EXPECT_INDUSTRY_BENCH, abs=1e-4
    )
    assert result["bench_fallback"] is False
    assert result["bench_basis_note"] == svc.BENCH_NOTE_INDUSTRY
    assert "非指数行情" in result["bench_basis_note"], "用成分股等权算的必须诚实标注"


@requires_pg
async def test_relative_bench_is_also_industry_equal_weight() -> None:
    """``relative``（赌跑赢同行）→ 同行业等权平均；与 ``direction`` 数值一致但口径独立。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        return await _evaluate(session, _entry(symbol=MAIN, bet_type="relative"))

    result = await _with_rollback(_go)
    assert result["bench_pct"] == pytest.approx(EXPECT_INDUSTRY_BENCH, abs=1e-4)
    assert result["bench_fallback"] is False
    assert result["bench_basis_note"] == svc.BENCH_NOTE_INDUSTRY


@requires_pg
async def test_market_bet_bench_is_hs300_even_when_industry_exists() -> None:
    """``market`` → 沪深300（**指定的**基准，不是退化）：即使标的属于申万一级也不走行业。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        return await _evaluate(session, _entry(symbol=MAIN, bet_type="market"))

    result = await _with_rollback(_go)
    assert result["bench_pct"] == pytest.approx(EXPECT_HS300_BENCH, abs=1e-4)
    assert result["bench_pct"] != pytest.approx(EXPECT_INDUSTRY_BENCH, abs=1e-4)
    assert result["bench_fallback"] is False, "显式 market 是用户选的基准，不是退化"
    assert result["bench_basis_note"] == svc.BENCH_NOTE_MARKET
    # outcome 仍然看标的自己（只有基准换成沪深300）
    assert result["outcome_pct"] == pytest.approx(EXPECT_MAIN_OUTCOME, abs=1e-4)


@requires_pg
async def test_no_industry_falls_back_to_hs300_and_flags_it() -> None:
    """无申万归属 → 沪深300 且 ``bench_fallback = true``（诚实标注，不假装是行业基准）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        return await _evaluate(session, _entry(symbol=ORPHAN, bet_type="direction"))

    result = await _with_rollback(_go)
    assert result["bench_pct"] == pytest.approx(EXPECT_HS300_BENCH, abs=1e-4)
    assert result["bench_fallback"] is True
    assert result["bench_basis_note"] == svc.BENCH_NOTE_FALLBACK
    assert result["outcome_pct"] == pytest.approx(
        round((CLOSE_END[ORPHAN] / CLOSE_START[ORPHAN] - 1) * 100, 4), abs=1e-4
    )


@requires_pg
async def test_market_level_entry_with_market_bet_is_not_a_fallback() -> None:
    """无 symbol + ``bet_type=market``：outcome 与 bench 都是沪深300，且**不算退化**。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        entry = _entry(symbol=None, bet_type="market")
        return await _evaluate(session, entry)

    result = await _with_rollback(_go)
    assert result["outcome_pct"] == pytest.approx(EXPECT_HS300_BENCH, abs=1e-4)
    assert result["bench_pct"] == pytest.approx(EXPECT_HS300_BENCH, abs=1e-4)
    assert result["excess_pct"] == pytest.approx(0.0, abs=1e-6)
    assert result["bench_fallback"] is False
    assert result["bench_basis_note"] == svc.BENCH_NOTE_MARKET


@requires_pg
async def test_market_level_entry_with_direction_bet_is_a_fallback() -> None:
    """无 symbol 但 ``bet_type=direction``：本来要行业基准 → 只能沪深300，标 ``fallback``。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        return await _evaluate(session, _entry(symbol=None, bet_type="direction"))

    result = await _with_rollback(_go)
    assert result["bench_pct"] == pytest.approx(EXPECT_HS300_BENCH, abs=1e-4)
    assert result["bench_fallback"] is True
    assert result["bench_basis_note"] == svc.BENCH_NOTE_FALLBACK


@requires_pg
async def test_legacy_snapshot_without_bench_spec_uses_the_column() -> None:
    """s9.r1 的历史行没有 ``bench_spec``：按列上的 ``bet_type`` 兜底，不崩也不改口径。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        legacy_direction = await _evaluate(
            session,
            _entry(
                symbol=MAIN,
                bet_type="direction",
                snapshot=_snapshot(MAIN, with_bench=False),
            ),
        )
        legacy_market = await _evaluate(
            session,
            _entry(
                symbol=MAIN,
                bet_type="market",
                snapshot=_snapshot(MAIN, with_bench=False),
            ),
        )
        return legacy_direction, legacy_market

    direction, market = await _with_rollback(_go)
    assert direction["bench_pct"] == pytest.approx(EXPECT_INDUSTRY_BENCH, abs=1e-4)
    assert direction["bench_fallback"] is False
    assert market["bench_pct"] == pytest.approx(EXPECT_HS300_BENCH, abs=1e-4)
    assert market["bench_fallback"] is False


@requires_pg
async def test_bench_spec_is_frozen_into_the_snapshot_at_creation() -> None:
    """``bench_spec`` 落进 ``snapshot``（创建时固化）：三种 bet_type 各写各的。"""

    async def _go(session: AsyncSession) -> dict[str, dict[str, Any]]:
        await _seed(session)
        out: dict[str, dict[str, Any]] = {}
        for bet_type in BET_TYPES:
            entry = _entry(symbol=MAIN, bet_type=bet_type)
            session.add(entry)
            await session.flush()
            out[bet_type] = entry.snapshot["bench_spec"]
        orphan = _entry(symbol=ORPHAN)
        session.add(orphan)
        await session.flush()
        out["orphan"] = orphan.snapshot["bench_spec"]
        return out

    specs = await _with_rollback(_go)
    assert specs["direction"] == {
        "bet_type": "direction",
        "kind": svc.BENCH_KIND_INDUSTRY,
        "industry_code": L1,
        "industry_name": "测试一级",
        "fallback": False,
    }
    assert specs["relative"]["kind"] == svc.BENCH_KIND_INDUSTRY
    assert specs["relative"]["bet_type"] == "relative"
    assert specs["market"] == {
        "bet_type": "market",
        "kind": svc.BENCH_KIND_HS300,
        "industry_code": None,
        "industry_name": None,
        "fallback": False,
    }
    assert specs["orphan"]["kind"] == svc.BENCH_KIND_HS300
    assert specs["orphan"]["fallback"] is True


@requires_pg
async def test_create_entry_writes_bet_type_column_and_snapshot_together() -> None:
    """``create_entry`` 同时落**列**（聚合用）与 ``snapshot.bench_spec``（不可变证据）。"""

    async def _go(session: AsyncSession) -> DecisionJournalEntry:
        await _seed(session)
        from app.schemas.journal import JournalEntryCreate

        payload = JournalEntryCreate(
            symbol=MAIN,
            kind="watch",
            bet_type="relative",
            thesis="猪价见底，产能去化加速",
            invalidation={"kind": "price_below", "value": 50.0},
            horizon_days=7,
        )
        return await svc.create_entry(
            session, uuid.uuid4(), payload, now=datetime(2026, 10, 16, 15, 30)
        )

    entry = await _with_rollback(_go)
    assert entry.bet_type == "relative"
    assert entry.snapshot["bench_spec"]["bet_type"] == "relative"
    assert entry.snapshot["bench_spec"]["industry_code"] == L1
    assert entry.snapshot["bench_basis_note"] == svc.BENCH_NOTE_INDUSTRY


# ---------------------------------------------------------------------------
# §S9.7.2 净成本：net = gross - 0.12（逐值断言）
# ---------------------------------------------------------------------------


@requires_pg
async def test_net_is_gross_minus_12bp_verbatim() -> None:
    """``net_outcome_pct = outcome_pct - 0.12`` / ``net_excess_pct = net_outcome - bench``。

    逐值断言（不是"约等于某个数"）：+10% → +9.88%，行业基准 5% → 净超额 +4.88%；
    市场基准 2.5% → 净超额 +7.38%；市场级条目净超额恰好 −0.12（毛超额 0）。
    """

    async def _go(session: AsyncSession) -> dict[str, dict[str, Any]]:
        await _seed(session)
        return {
            "industry": await _evaluate(session, _entry(symbol=MAIN, bet_type="direction")),
            "hs300": await _evaluate(session, _entry(symbol=MAIN, bet_type="market")),
            "market": await _evaluate(session, _entry(symbol=None, bet_type="market")),
        }

    results = await _with_rollback(_go)
    industry = results["industry"]
    assert industry["outcome_pct"] == 10.0
    assert industry["net_outcome_pct"] == 9.88
    assert industry["bench_pct"] == 5.0
    assert industry["net_excess_pct"] == 4.88
    assert industry["excess_pct"] == 5.0
    # 净 = 毛 − 0.12，四个数各自都成立（不是只在一条上凑出来的）
    assert industry["net_outcome_pct"] == pytest.approx(
        industry["outcome_pct"] - svc.COST_ROUND_TRIP_PCT, abs=1e-6
    )
    assert industry["net_excess_pct"] == pytest.approx(
        industry["net_outcome_pct"] - industry["bench_pct"], abs=1e-6
    )

    hs300 = results["hs300"]
    assert (hs300["outcome_pct"], hs300["net_outcome_pct"], hs300["bench_pct"]) == (
        10.0,
        9.88,
        2.5,
    )
    assert hs300["net_excess_pct"] == 7.38

    market = results["market"]
    assert market["excess_pct"] == 0.0
    assert market["net_excess_pct"] == -0.12
    assert market["net_outcome_pct"] == 2.38


@requires_pg
async def test_net_fields_are_null_with_reason_when_gross_is_missing() -> None:
    """缺数**不补 0**：毛缺失 → 净也 null，并在 ``missing_reason`` 写明原因。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        entry = _entry(symbol="T97999", snapshot=_snapshot(None, with_bench=False))
        return await _evaluate(session, entry)

    result = await _with_rollback(_go)
    assert result["net_outcome_pct"] is None
    assert result["net_excess_pct"] is None
    assert result["net_outcome_pct"] != 0
    assert result["missing_reason"]["net_outcome_pct"] == "snapshot_price_missing"
    assert result["missing_reason"]["net_excess_pct"] == "snapshot_price_missing"


def test_cost_constant_and_stats_version_are_pinned() -> None:
    """常量成对钉死 + 源码里必须有假设与日期（spec §S9.7.2「写在常量里并注明日期与假设」）。"""
    assert svc.COST_ROUND_TRIP_BPS == 12
    assert svc.COST_ROUND_TRIP_PCT == 0.12
    # 改成本常量必须同时改统计产物版本（spec 的验收判据）→ 两者一起钉在这里。
    # Phase B2：``share_ci.win`` 的分母排除 pass（裁定 2）= 区间口径变了 → 从
    # ``s97.a1`` 升到 ``s97.b1``（常量注释里写明了原因）
    assert svc.JOURNAL_STATS_VERSION == "s97.b1"
    source = inspect.getsource(svc)
    assert "2026-10-19" in source, "常量必须注明假设的日期（改了要一起改）"
    for assumption in ("佣金", "印花税", "过户费", "滑点", "保守"):
        assert assumption in source, f"常量的假设说明缺：{assumption}"


# ---------------------------------------------------------------------------
# §S9.7.6 pass 一等公民：计入判断、不计入收益
# ---------------------------------------------------------------------------


@requires_pg
async def test_stats_counts_pass_in_judgement_but_excludes_it_from_returns() -> None:
    """``by_kind`` 合计 = ``n``；``avg_excess_pct`` / ``avg_net_excess_pct`` 排除 ``pass``。

    构造：watch(+4.0) / buy(−2.0) / **pass(+100.0)** 三条已复盘 + pass / hold 两条未复盘。
    若 ``pass`` 混进收益统计，均值会被 100 拽到 34 —— 断言它必须还是 1.0。
    """

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        user = uuid.uuid4()
        reviews = (
            ("watch", 4.0, "discipline_break"),
            ("buy", -2.0, "luck"),
            ("pass", 100.0, "luck"),
        )
        for kind, excess, verdict in reviews:
            entry = _entry(user_id=user, kind=kind)
            session.add(entry)
            await session.flush()
            session.add(
                DecisionJournalReview(
                    entry_id=entry.id,
                    outcome_pct=1.0,
                    bench_pct=1.0,
                    excess_pct=excess,
                    invalidation_hit=False,
                    verdict=verdict,
                )
            )
        for kind in ("pass", "hold"):
            session.add(_entry(user_id=user, kind=kind))
        await session.flush()
        empty_user = uuid.uuid4()
        return await svc.stats(session, user), await svc.stats(session, empty_user)

    stats, empty = await _with_rollback(_go)
    assert stats["n"] == 5
    assert stats["reviewed_n"] == 3
    assert stats["pending_n"] == 2
    # ★ pass 有独立分栏，且五栏合计 = n（spec §S9.7.6）
    assert stats["by_kind"] == {"watch": 1, "buy": 1, "sell": 0, "pass": 2, "hold": 1}
    assert sum(stats["by_kind"].values()) == stats["n"]
    assert set(stats["by_kind"]) == set(svc.ENTRY_KINDS)
    # ★ 收益统计排除 pass：(4.0 + (−2.0)) / 2 = 1.0，不是 (4 − 2 + 100) / 3 = 34
    assert stats["avg_excess_pct"] == 1.0
    assert stats["avg_net_excess_pct"] == 0.88
    assert stats["avg_net_excess_pct"] == pytest.approx(stats["avg_excess_pct"] - 0.12, abs=1e-4)
    # 既有字段类型不变：占比仍是 number|null
    assert stats["discipline_break_share"] is None  # reviewed_n=3 < 5
    assert sum(stats["by_verdict"].values()) == stats["reviewed_n"]

    # 空用户：一切归零但不给假区间（Wilson 的 0/0 → null）
    assert empty["n"] == 0
    assert set(empty["by_kind"]) == set(svc.ENTRY_KINDS)
    assert sum(empty["by_kind"].values()) == 0
    assert empty["avg_excess_pct"] is None
    assert empty["avg_net_excess_pct"] is None
    assert empty["share_ci"] == {"discipline_break": None, "win": None}


# ---------------------------------------------------------------------------
# §S9.7.7 Wilson 区间（纯函数 + stats 集成）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("successes", "n", "expected"),
    [
        # 0/0 → null：没有样本时任何区间都是编造的
        (0, 0, None),
        # 1/1 不高估：Wald 会给 [1.0, 1.0]（假精确），Wilson 给 [0.21, 1.00]
        (1, 1, {"n": 1, "successes": 1, "ci_low": 0.2065, "ci_high": 1.0}),
        # spec 逐字举例：4/6 ≈ [0.30, 0.90]
        (4, 6, {"n": 6, "successes": 4, "ci_low": 0.3, "ci_high": 0.9032}),
        # 5/5 也不是 [1,1]：下界 0.5655，那才是"5 次里 5 次对"能支撑的结论
        (5, 5, {"n": 5, "successes": 5, "ci_low": 0.5655, "ci_high": 1.0}),
        (0, 6, {"n": 6, "successes": 0, "ci_low": 0.0, "ci_high": 0.3903}),
    ],
)
def test_wilson_interval_boundaries(
    successes: int, n: int, expected: dict[str, Any] | None
) -> None:
    result = svc.wilson_interval(successes, n)
    assert result == expected


def test_wilson_interval_is_monotone_in_successes() -> None:
    """区间随成功数单调右移、且永远包住点估计（Wilson 的基本性质，防手抄反）。"""
    previous_low = -1.0
    for successes in range(6):
        interval = svc.wilson_interval(successes, 5)
        assert interval is not None
        point = successes / 5
        assert interval["ci_low"] <= point <= interval["ci_high"]
        assert interval["ci_low"] >= previous_low
        previous_low = interval["ci_low"]
    # 越界输入夹到 [0, n]（不抛异常，也不给出 >1 的比例）
    assert svc.wilson_interval(9, 5) == svc.wilson_interval(5, 5)


@requires_pg
async def test_stats_share_ci_reports_n_successes_and_interval() -> None:
    """``share_ci`` = ``{n, successes, ci_low, ci_high}``；``win`` 用**净超额 > 0** 判定。

    边界（``excess = 0.12`` ⇒ 净超额 0）**不算赢**：+0.20 / +0.50 / +4.00 赢，0.12 / −2.00 不赢。
    """

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        for index, excess in enumerate((4.0, -2.0, 0.5, 0.12, 0.2)):
            entry = _entry(user_id=user)
            session.add(entry)
            await session.flush()
            session.add(
                DecisionJournalReview(
                    entry_id=entry.id,
                    outcome_pct=excess + 1.0,
                    bench_pct=1.0,
                    excess_pct=excess,
                    invalidation_hit=False,
                    verdict="discipline_break" if index < 2 else "luck",
                )
            )
        await session.flush()
        return await svc.stats(session, user)

    stats = await _with_rollback(_go)
    assert stats["reviewed_n"] == 5
    assert stats["sample_sufficient"] is True
    assert isinstance(stats["discipline_break_share"], float)
    assert stats["discipline_break_share"] == pytest.approx(0.4)

    discipline = stats["share_ci"]["discipline_break"]
    assert discipline == {
        "n": 5,
        "successes": 2,
        "ci_low": 0.1176,
        "ci_high": 0.7693,
    }
    win = stats["share_ci"]["win"]
    assert win is not None
    assert win["n"] == 5
    assert win["successes"] == 3, "净超额 > 0 才算赢（excess=0.12 净超额为 0，不算）"
    assert win["ci_low"] == pytest.approx(0.2307, abs=1e-4)
    assert win["ci_high"] == pytest.approx(0.8824, abs=1e-4)
    assert win["ci_low"] < win["successes"] / win["n"] < win["ci_high"]

    # 口径常量随统计一起下发（前端原样展示，不写死 12 / 不自己编文案）
    assert stats["net_cost_bps"] == svc.COST_ROUND_TRIP_BPS == 12
    assert stats["multiple_comparison_note"] == NOTE_MULTI == svc.MULTIPLE_COMPARISON_NOTE


# ---------------------------------------------------------------------------
# 迁移：只加列（+ 一条 check），不删不改不搬数据
# ---------------------------------------------------------------------------

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "a7d3e9f1b2c4_add_journal_bet_type.py"


def test_migration_only_adds_bet_type_column() -> None:
    """spec 的硬约束：迁移**只加列/索引**（``bet_type`` 带默认值、``NOT NULL``）。

    反向证据逐条钉死：没有 ``create_table`` / ``drop_*`` / ``alter_column`` /
    ``op.execute``（不搬数据、不改既有列），``server_default='direction'`` 让 PG
    直接回填既有行。
    """
    source = _MIGRATION.read_text(encoding="utf-8")
    assert 'op.add_column(\n        "decision_journal_entries"' in source
    assert "sa.String(length=16)" in source
    assert 'server_default="direction"' in source
    assert "nullable=False" in source
    for forbidden in (
        "create_table",
        "drop_column",
        "drop_table",
        "alter_column",
        "op.execute(",
        "op.bulk_insert",
        "op.create_index",
        "op.drop_constraint",
    ):
        assert forbidden not in source.split("def downgrade")[0], (
            f"upgrade 阶段不该出现 {forbidden}（迁移只做加法）"
        )
    assert 'down_revision: str | None = "c64df59c0e86"' in source
    # 三枚举与模型的 BET_TYPES 一致（迁移里的谓词是字面量，靠这里对拍）
    for bet_type in BET_TYPES:
        assert f"'{bet_type}'" in source
    assert len(re.findall(r"revision: str = \"[0-9a-f]+\"", source)) == 1


def test_schema_bet_type_matches_the_model_enum() -> None:
    """``BetType`` 与 ``BET_TYPES`` 逐字一致（漂移 = 422 只拦一半）。"""
    from typing import get_args

    from app.schemas.journal import BetType

    assert tuple(get_args(BetType)) == BET_TYPES


# ---------------------------------------------------------------------------
# 反 N+1：stats 的查询数与条数无关（spec 契约 §6：统计额外 ≤1 条聚合）
# ---------------------------------------------------------------------------


async def _with_rollback_conn(factory: Any) -> Any:
    """和 ``_with_rollback`` 一样回滚，但把连接一起交给用例（数 SQL 用）。"""
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await factory(session, conn)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _count_queries(conn: Any, coro_factory: Any) -> tuple[Any, int, list[str]]:
    from sqlalchemy import event

    counter = {"n": 0}
    statements: list[str] = []

    def _count(_conn: Any, _cursor: Any, statement: str, *_a: Any, **_k: Any) -> None:
        counter["n"] += 1
        statements.append(statement)

    event.listen(conn.sync_engine, "before_cursor_execute", _count)
    try:
        result = await coro_factory()
    finally:
        event.remove(conn.sync_engine, "before_cursor_execute", _count)
    return result, counter["n"], statements


@requires_pg
async def test_stats_query_count_is_constant_and_read_only() -> None:
    """3 条与 15 条的 SQL 条数相同（≤2 条聚合），且全是只读 SELECT。"""

    async def _seed_user(session: AsyncSession, user: uuid.UUID, count: int) -> None:
        for _ in range(count):
            entry = _entry(user_id=user)
            session.add(entry)
            await session.flush()
            session.add(
                DecisionJournalReview(
                    entry_id=entry.id,
                    outcome_pct=1.0,
                    bench_pct=0.5,
                    excess_pct=0.5,
                    invalidation_hit=False,
                    verdict="luck",
                )
            )
        await session.flush()

    async def _go(session: AsyncSession, conn: Any) -> tuple[int, int, list[str]]:
        await _seed(session)
        user = uuid.uuid4()
        await _seed_user(session, user, 3)
        _, small, _ = await _count_queries(conn, lambda: svc.stats(session, user))
        await _seed_user(session, user, 12)
        _, large, statements = await _count_queries(conn, lambda: svc.stats(session, user))
        return small, large, statements

    small, large, statements = await _with_rollback_conn(_go)
    assert small == large, f"stats 的查询数随条数增长（N+1）：3 条 {small} → 15 条 {large}"
    assert large <= 2, f"stats 用了 {large} 条查询（契约要求额外 ≤1 条聚合）"
    for statement in statements:
        assert statement.lstrip().upper().startswith(("SELECT", "WITH")), (
            f"stats 必须只读，出现写语句：{statement[:60]}"
        )
