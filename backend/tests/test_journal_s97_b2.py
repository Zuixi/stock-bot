"""spec §S9.7 Phase B2 —— **路径 / 校准 / 仓位统计 / 导出**（真 PG + 纯函数）。

这一组钉 Phase B2 的四件事，每条对应契约的一句验收判据：

1. **路径**（§S9.7.4）：``mae_pct`` / ``first_touch_invalidation`` /
   ``touched_then_reversed`` / ``days_to_invalidation`` 逐值可核；构造"先跌破失效线、
   到期反而收涨"的序列 → ``touched_then_reversed = true``（这就是"纪律问题"的那条证据）。
2. **校准**（§S9.7.5）：``hit_rate`` / ``brier`` 一律用 **``net_excess_pct > 0``** 判对；
   总样本 ``n < 20`` → ``calibration = null`` + 原因；每档 ``n < 10`` 不 ``sufficient``。
3. **仓位**（§S9.7.1）：``by_size`` 是判断条数（合计 = ``n``）、``avg_weighted_excess_pct``
   按权重加权且 **``none`` 排除**（它的收益再离谱也不能拉动加权值）。
4. **导出**（§S9.7.9）：只含本人（两个用户交叉断言）、CSV 带 **UTF-8 BOM** + 固定列序。

合成数据刻意用库里不存在的代码（``T980xx``）与未来日期（2026-10），所有写入包在**回滚**
事务里（Ruling 17：跑完库与跑前一致）；库不可达时 skip 而非红。
"""

from __future__ import annotations

import asyncio
import csv
import inspect
import io
import json
import os
import uuid
from datetime import date, datetime
from typing import Any

import asyncpg
import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.index_daily import IndexDaily
from app.models.journal import (
    CONFIDENCE_VALUES,
    POSITION_SIZES,
    DecisionJournalEntry,
    DecisionJournalReview,
)
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.services import journal_service as svc

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

AS_OF = date(2026, 10, 16)
DAY_1 = date(2026, 10, 19)
DAY_2 = date(2026, 10, 20)
DUE_ON = date(2026, 10, 21)
#: 主标的：先跌破（10-19 低 96）再反弹（到期收 110）——`touched_then_reversed` 的正例
MAIN = "T98001"
#: 反向标的：跌破后继续走弱（到期收 95）——触达了但**不是**"反转"
WEAK = "T98002"
PRICE = 100.0
HS300_START, HS300_END = 4000.0, 4040.0


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


requires_pg = pytest.mark.skipif(
    not _pg_reachable(), reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


# ---------------------------------------------------------------------------
# 夹具（回滚事务）
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


async def _stock_id(session: AsyncSession, symbol: str) -> int:
    return int((await session.execute(select(Stock.id).where(Stock.symbol == symbol))).scalar_one())


async def _seed(session: AsyncSession) -> None:
    """两只标的的日线（含区间极值）+ 沪深300 起止。

    ``MAIN``：100 →（10-19 低 96）→（10-20 低 95）→ 到期收 **110**（先跌破后反弹）
    ``WEAK``：100 →（10-19 低 90）→ 到期收 **95**（触达失效线后继续跌）
    """
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
            for symbol in (MAIN, WEAK)
        ],
    )
    main_id = await _stock_id(session, MAIN)
    weak_id = await _stock_id(session, WEAK)
    await session.execute(
        insert(DailyQuote),
        [
            # 入场日（不算进区间：区间是 (入场交易日, due_on]）
            _quote(main_id, AS_OF, low=100.0, high=100.0, close=100.0),
            _quote(main_id, DAY_1, low=96.0, high=102.0, close=99.0),
            _quote(main_id, DAY_2, low=95.0, high=104.0, close=101.0),
            _quote(main_id, DUE_ON, low=98.0, high=106.0, close=110.0),
            _quote(weak_id, AS_OF, low=100.0, high=100.0, close=100.0),
            _quote(weak_id, DAY_1, low=90.0, high=99.0, close=92.0),
            _quote(weak_id, DUE_ON, low=93.0, high=96.0, close=95.0),
        ],
    )
    await session.execute(
        insert(IndexDaily),
        [
            {"ts_code": svc.HS300_TS_CODE, "trade_date": AS_OF, "close": HS300_START},
            {"ts_code": svc.HS300_TS_CODE, "trade_date": DUE_ON, "close": HS300_END},
        ],
    )
    await session.flush()


def _quote(stock_id: int, day: date, *, low: float, high: float, close: float) -> dict[str, Any]:
    return {
        "stock_id": stock_id,
        "trade_date": day,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "pre_close": PRICE,
        "amount": 1_000_000.0,
    }


def _snapshot(symbol: str | None = MAIN, price: float | None = PRICE) -> dict[str, Any]:
    return {
        "version": "s9.r1",
        "as_of": AS_OF.isoformat(),
        "quote_as_of": AS_OF.isoformat(),
        "symbol": symbol,
        "price": price,
        "sector_flow_state": None,
        "missing_reason": {},
    }


def _entry(**overrides: Any) -> DecisionJournalEntry:
    payload: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "symbol": MAIN,
        "kind": "watch",
        "bet_type": "direction",
        "position_size": "none",
        "thesis": "猪价见底，产能去化加速",
        "invalidation": {"kind": "price_below", "value": 96.5},
        "horizon_days": 7,
        "snapshot": _snapshot(),
        "created_at": datetime(2026, 10, 16, 15, 0),
        "due_on": DUE_ON,
    }
    payload.update(overrides)
    return DecisionJournalEntry(**payload)


async def _review(
    session: AsyncSession,
    entry: DecisionJournalEntry,
    *,
    excess: float,
    verdict: str = "luck",
) -> DecisionJournalReview:
    session.add(entry)
    await session.flush()
    review = DecisionJournalReview(
        entry_id=entry.id,
        outcome_pct=excess + 1.0,
        bench_pct=1.0,
        excess_pct=excess,
        invalidation_hit=False,
        verdict=verdict,
    )
    session.add(review)
    await session.flush()
    return review


# ---------------------------------------------------------------------------
# 纯函数常量：校准概率映射 / 仓位权重 / 导出列序（钉死，不许漂移）
# ---------------------------------------------------------------------------


def test_calibration_probability_mapping_is_fixed() -> None:
    """spec §S9.7.5：``{1:0.2, 2:0.35, 3:0.5, 4:0.65, 5:0.8}`` —— **不可优化**。"""
    assert svc.CALIBRATION_P == {1: 0.2, 2: 0.35, 3: 0.5, 4: 0.65, 5: 0.8}
    assert tuple(svc.CALIBRATION_P) == CONFIDENCE_VALUES
    assert svc.MIN_CALIBRATION_SAMPLE == 20
    assert svc.MIN_CALIBRATION_BIN_SAMPLE == 10
    # 说明"不可优化"的理由必须留在源码里（改表的人会先读到它）
    assert "不可优化" in inspect.getsource(svc)


def test_size_weights_are_pinned() -> None:
    """spec §S9.7.1：``none:0, light:0.5, normal:1, heavy:2``（``none`` = 只观察）。"""
    assert svc.SIZE_WEIGHTS == {"none": 0.0, "light": 0.5, "normal": 1.0, "heavy": 2.0}
    assert tuple(svc.SIZE_WEIGHTS) == POSITION_SIZES
    assert svc.EXPORT_HINT == "/api/v1/journal/export?format=csv"
    assert svc.EXPORT_CSV_COLUMNS == (
        "id",
        "symbol",
        "kind",
        "bet_type",
        "position_size",
        "confidence",
        "thesis",
        "invalidation",
        "horizon_days",
        "created_at",
        "due_on",
        "status",
        "outcome_pct",
        "bench_pct",
        "excess_pct",
        "net_excess_pct",
        "verdict",
    )


# ---------------------------------------------------------------------------
# §S9.7.5 校准（纯函数）：n<20 → null；单档 n<10 不 sufficient；brier 用净超额
# ---------------------------------------------------------------------------


def test_calibration_returns_none_with_note_below_20() -> None:
    """总样本 19 → ``calibration = None`` + 原因（不画半条曲线）。"""
    calibration, note = svc.calibration_summary({1: (4, 1), 3: (12, 7), 5: (3, 2)})
    assert calibration is None
    assert note is not None
    assert "样本不足" in note
    assert "19" in note, "原因里要带当前条数（用户才知道还差几条）"


def test_calibration_bins_and_brier_at_22_samples() -> None:
    """22 条：两档样本够、一档不够；``brier`` 逐值可核（用的是净超额命中）。"""
    calibration, note = svc.calibration_summary({1: (4, 1), 3: (12, 7), 5: (6, 4)})
    assert note is None
    assert calibration is not None
    assert calibration["n"] == 22
    bins = {item["confidence"]: item for item in calibration["bins"]}
    # 没有样本的档**不出现**（不是 n=0 / hit_rate=0 的假点）
    assert set(bins) == {1, 3, 5}
    assert bins[1] == {
        "confidence": 1,
        "p": 0.2,
        "n": 4,
        "hit_rate": 0.25,
        "sufficient": False,
    }
    assert bins[3]["p"] == 0.5
    assert bins[3]["n"] == 12
    assert bins[3]["hit_rate"] == pytest.approx(7 / 12, abs=1e-4)
    assert bins[3]["sufficient"] is True, "n=12 >= 10 → 该档才给命中率"
    assert bins[5]["sufficient"] is False, "n=6 < 10 → 该档样本不足"
    # brier = [(4-1)*0.04 + 1*0.64 + (12-7)*0.25 + 7*0.25 + (6-4)*0.64 + 4*0.04] / 22
    assert calibration["brier"] == pytest.approx(5.2 / 22, abs=1e-4)


# ---------------------------------------------------------------------------
# §S9.7.4 路径四指标（真 PG + 真行情序列）
# ---------------------------------------------------------------------------


@requires_pg
async def test_path_metrics_touch_then_reverse() -> None:
    """先跌破失效线（10-19 低 96 ≤ 96.5）、到期反而收 110 → ``touched_then_reversed``。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        entry = _entry(invalidation={"kind": "price_below", "value": 96.5})
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    # 区间最低 95（10-20）→ MAE = (95/100 - 1) * 100 = -5.0
    assert result["mae_pct"] == -5.0
    assert result["first_touch_invalidation"] == DAY_1.isoformat()
    assert result["days_to_invalidation"] == 1, "入场日之后第 1 个交易日就触达"
    assert result["invalidation_hit"] is True
    assert result["outcome_pct"] == 10.0
    assert result["net_outcome_pct"] == pytest.approx(9.88, abs=1e-4)
    assert result["touched_then_reversed"] is True, "先触失效、到期净收益 > 0 = 纪律问题"
    assert "mae_pct" not in result["missing_reason"]


@requires_pg
async def test_path_metrics_touch_but_not_reverse() -> None:
    """触达失效线后继续跌（到期收 95）：``touched_then_reversed`` 必须是 ``False``。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        entry = _entry(
            symbol=WEAK,
            snapshot=_snapshot(WEAK),
            invalidation={"kind": "price_below", "value": 95.0},
        )
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["first_touch_invalidation"] == DAY_1.isoformat(), "10-19 低 90 ≤ 95 → 触达"
    assert result["days_to_invalidation"] == 1
    assert result["net_outcome_pct"] == pytest.approx(-5.12, abs=1e-4)
    assert result["touched_then_reversed"] is False, "到期净收益 < 0 → 不是「先触后反」"


@requires_pg
async def test_path_metrics_price_above_and_no_touch() -> None:
    """``price_above`` 在到期日触达（第 3 个交易日）；未触达 → 日期/天数 ``None``。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        above = _entry(invalidation={"kind": "price_above", "value": 105.5})
        never = _entry(invalidation={"kind": "price_below", "value": 50.0})
        session.add_all([above, never])
        await session.flush()
        return (
            await svc.evaluate_entry(session, above, as_of=DUE_ON),
            await svc.evaluate_entry(session, never, as_of=DUE_ON),
        )

    above, never = await _with_rollback(_go)
    # 105.5 只在到期日（高 106）被触及 → 3 个交易日
    assert above["first_touch_invalidation"] == DUE_ON.isoformat()
    assert above["days_to_invalidation"] == 3
    assert above["touched_then_reversed"] is True  # 到期 110 > 100
    # 未触达：不是"缺数"，而是"没发生" —— 日期/天数 null，布尔 false，MAE 照算
    assert never["first_touch_invalidation"] is None
    assert never["days_to_invalidation"] is None
    assert never["touched_then_reversed"] is False
    assert never["mae_pct"] == -5.0
    assert never["invalidation_hit"] is False


@requires_pg
async def test_path_metrics_mae_is_clamped_at_zero_and_date_only_is_not_applicable() -> None:
    """最低价高于入场价 → ``mae_pct = 0``；``date_only`` → 触达三字段 null/false/null。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        # date_only：到期即命中，没有"先触达失效条件"这回事
        date_entry = _entry(invalidation={"kind": "date_only", "value": DUE_ON.isoformat()})
        # MAE 夹到 0：入场价 85 < 区间最低 90（没发生过不利偏移 → 0，不是 +5.88）
        zero_entry = _entry(
            symbol=WEAK,
            snapshot=_snapshot(WEAK, price=85.0),
            invalidation={"kind": "price_below", "value": 50.0},
        )
        session.add_all([date_entry, zero_entry])
        await session.flush()
        return (
            await svc.evaluate_entry(session, date_entry, as_of=DUE_ON),
            await svc.evaluate_entry(session, zero_entry, as_of=DUE_ON),
        )

    date_result, zero_result = await _with_rollback(_go)
    assert date_result["invalidation_hit"] is True, "date_only 到期即命中"
    assert date_result["first_touch_invalidation"] is None
    assert date_result["days_to_invalidation"] is None
    assert date_result["touched_then_reversed"] is False
    # MAE 是"最不利偏移"：区间最低 95 > 入场 100 → 没有不利偏移 → 0（不是正数）
    assert date_result["mae_pct"] == -5.0
    assert zero_result["mae_pct"] == 0.0, "区间最低 90 > 入场 85 → 没有不利偏移"
    assert zero_result["missing_reason"].get("mae_pct") is None, "0 是有意义的值，不是缺数"


@requires_pg
async def test_path_metrics_market_level_has_no_entry_price() -> None:
    """市场级判断：快照里没有 price → ``mae_pct = null`` + 原因（不补 0）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        entry = _entry(
            symbol=None,
            bet_type="market",
            snapshot=_snapshot(None, price=None),
            invalidation={"kind": "date_only", "value": DUE_ON.isoformat()},
        )
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["mae_pct"] is None
    assert result["missing_reason"]["mae_pct"] == "market_level_no_symbol"
    assert result["first_touch_invalidation"] is None
    assert result["touched_then_reversed"] is False


# ---------------------------------------------------------------------------
# §S9.7.1 仓位统计：by_size 是判断条数、加权超额排除 none
# ---------------------------------------------------------------------------


@requires_pg
async def test_by_size_counts_and_weighted_excess_excludes_none() -> None:
    """``by_size`` 合计 = ``n``；加权超额 = Σ(w·excess)/Σ(w)，``none`` 完全退出分母。

    构造：none(+100.0) / light(+4.0) / normal(+2.0) / heavy(+8.0) 四条已复盘
    + 一条未复盘的 none。加权值 = (0.5·4 + 1·2 + 2·8) / (0.5 + 1 + 2) = 5.7143；
    若把 none 的 +100 算进来（权重 0 只进分母）会变成 (2 + 2 + 16) / 3.5 = 5.7143
    —— 所以再断言"分母不含 none"：把 none 的权重当 1 时值会变成 5.0 附近。
    """

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        user = uuid.uuid4()
        for size, excess in (("none", 100.0), ("light", 4.0), ("normal", 2.0), ("heavy", 8.0)):
            entry = _entry(user_id=user, position_size=size)
            await _review(session, entry, excess=excess)
        unreviewed = _entry(user_id=user, position_size="none")
        session.add(unreviewed)
        await session.flush()
        return await svc.stats(session, user), await svc.stats(session, uuid.uuid4())

    stats, empty = await _with_rollback(_go)
    assert stats["n"] == 5
    assert stats["reviewed_n"] == 4
    assert stats["by_size"] == {"none": 2, "light": 1, "normal": 1, "heavy": 1}
    assert sum(stats["by_size"].values()) == stats["n"]
    assert set(stats["by_size"]) == set(POSITION_SIZES)
    assert stats["avg_weighted_excess_pct"] == pytest.approx(20 / 3.5, abs=1e-4)
    # 空用户：没有可加权样本 → null（不是 0）
    assert empty["by_size"] == {"none": 0, "light": 0, "normal": 0, "heavy": 0}
    assert empty["avg_weighted_excess_pct"] is None


@requires_pg
async def test_by_size_weighted_is_null_when_only_none_has_reviews() -> None:
    """只有 ``none`` 的复盘 → 加权超额 ``None``（分母 0，不是"加权后正好 0"）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        await _review(session, _entry(user_id=user, position_size="none"), excess=50.0)
        return await svc.stats(session, user)

    stats = await _with_rollback(_go)
    assert stats["by_size"]["none"] == 1
    assert stats["avg_weighted_excess_pct"] is None


# ---------------------------------------------------------------------------
# §S9.7.6 每类净超额中位（新增 by_kind_median，既有 by_kind 形状不变）
# ---------------------------------------------------------------------------


@requires_pg
async def test_by_kind_median_is_net_and_by_kind_stays_counts() -> None:
    """``by_kind`` 仍是纯数字（合计 = ``n``）；``by_kind_median`` 给 ``{count, 中位净超额}``。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        for kind, excess in (
            ("watch", 1.0),
            ("watch", 3.0),
            ("watch", 5.0),
            ("buy", 2.0),
            ("buy", 4.0),
            ("pass", 100.0),
        ):
            await _review(session, _entry(user_id=user, kind=kind), excess=excess)
        return await svc.stats(session, user)

    stats = await _with_rollback(_go)
    assert stats["by_kind"] == {"watch": 3, "buy": 2, "sell": 0, "pass": 1, "hold": 0}
    assert sum(stats["by_kind"].values()) == stats["n"] == 6
    median = stats["by_kind_median"]
    assert set(median) == set(svc.ENTRY_KINDS)
    # 中位毛超额 3.0 / 3.0 / 100 → 减 12bp 得净口径
    assert median["watch"] == {"count": 3, "median_net_excess_pct": 2.88}
    assert median["buy"] == {"count": 2, "median_net_excess_pct": 2.88}
    assert median["pass"] == {"count": 1, "median_net_excess_pct": 99.88}
    assert median["sell"] == {"count": 0, "median_net_excess_pct": None}
    # 既有收益均值仍然排除 pass：pass 的 +100 不能把均值拽上去
    assert stats["avg_excess_pct"] == pytest.approx(3.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 裁定 2：`share_ci.win` 的分母排除 pass
# ---------------------------------------------------------------------------


@requires_pg
async def test_win_denominator_excludes_pass() -> None:
    """分母 = 已复盘且 ``kind != pass``：3 条非 pass（2 赢）+ 2 条 pass 巨额盈利。

    若 pass 进了分母 → ``n = 5`` / ``successes = 3``；排除后必须是 ``n = 3`` / 2。
    """

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        for kind, excess in (
            ("watch", 4.0),
            ("watch", -2.0),
            ("buy", 0.5),
            ("pass", 100.0),
            ("pass", 50.0),
        ):
            await _review(session, _entry(user_id=user, kind=kind), excess=excess)
        return await svc.stats(session, user)

    stats = await _with_rollback(_go)
    assert stats["reviewed_n"] == 5
    win = stats["share_ci"]["win"]
    assert win is not None
    assert win["n"] == 3, "pass 不参与市场，收益无意义 → 不进分母"
    assert win["successes"] == 2
    assert win == svc.wilson_interval(2, 3)
    # 没按计划执行的占比仍以全部已复盘为分母（pass 也是判断）→ 不受裁定 2 影响
    assert stats["share_ci"]["discipline_break"]["n"] == 5


@requires_pg
async def test_win_is_null_when_all_reviewed_are_pass() -> None:
    """全是 pass → 分母 0 → ``win = None``（不是 100%）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        for excess in (1.0, 2.0):
            await _review(session, _entry(user_id=user, kind="pass"), excess=excess)
        return await svc.stats(session, user)

    stats = await _with_rollback(_go)
    assert stats["share_ci"]["win"] is None
    assert stats["discipline_break_share"] is None  # reviewed_n=2 < 5


# ---------------------------------------------------------------------------
# §S9.7.5 校准（真 PG）：n<20 → null；n>=20 分档 sufficient
# ---------------------------------------------------------------------------


async def _review_confidence(
    session: AsyncSession, user: uuid.UUID, confidence: int, *, count: int, hits: int
) -> None:
    """造 ``count`` 条已复盘样本：前 ``hits`` 条净超额 > 0，其余卡在成本线下方。"""
    for index in range(count):
        entry = _entry(user_id=user, confidence=confidence)
        await _review(session, entry, excess=1.0 if index < hits else 0.12)


@requires_pg
async def test_calibration_is_null_below_20_reviewed_samples() -> None:
    """19 条已复盘且填了信心 → ``calibration = null`` + ``calibration_note``。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        await _review_confidence(session, user, 3, count=19, hits=10)
        # 没填信心的复盘**不算**进校准样本（n 只数填了的）
        await _review(session, _entry(user_id=user, confidence=None), excess=5.0)
        return await svc.stats(session, user)

    stats = await _with_rollback(_go)
    assert stats["reviewed_n"] == 20
    assert stats["calibration"] is None
    assert stats["calibration_note"] is not None
    assert "样本不足" in stats["calibration_note"]
    assert "19" in stats["calibration_note"], "只数填了信心的 19 条（没填的不参与校准）"


@requires_pg
async def test_calibration_bins_and_sufficiency_at_20_samples() -> None:
    """20 条信心 3（净超额命中 10 条）+ 2 条信心 1：总 22 → 曲线可见、分档样本判定。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        user = uuid.uuid4()
        await _review_confidence(session, user, 3, count=20, hits=10)
        await _review_confidence(session, user, 1, count=2, hits=1)
        return await svc.stats(session, user)

    stats = await _with_rollback(_go)
    calibration = stats["calibration"]
    assert calibration is not None, "22 >= 20 → 必须给曲线（不是 null）"
    assert stats["calibration_note"] is None
    assert calibration["n"] == 22
    bins = {bin_["confidence"]: bin_ for bin_ in calibration["bins"]}
    assert set(bins) == {1, 3}, "没填/没样本的档不出现"
    assert bins[3]["n"] == 20
    assert bins[3]["sufficient"] is True
    assert bins[3]["p"] == 0.5
    assert bins[3]["hit_rate"] == 0.5, "excess=0.12 净超额为 0 → 不算命中"
    assert bins[1]["n"] == 2
    assert bins[1]["sufficient"] is False, "n=2 < 10 → 该档样本不足"
    # brier = [(20-10)*0.25 + 10*0.25 + (2-1)*0.04 + 1*0.64] / 22
    assert calibration["brier"] == pytest.approx((5.0 + 0.68) / 22, abs=1e-4)


# ---------------------------------------------------------------------------
# §S9.7.9 导出：只含本人 + CSV BOM / 固定列序（服务层，分页与 API 见 test_journal_api）
# ---------------------------------------------------------------------------


@requires_pg
async def test_export_rows_only_contain_the_owner() -> None:
    """两个用户的交叉：``export_rows`` 只返回本人的条目 + 快照 + 复盘。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed(session)
        owner = uuid.uuid4()
        other = uuid.uuid4()
        mine = _entry(user_id=owner, thesis="我的判断")
        reviewed = await _review(session, mine, excess=3.0, verdict="thesis_wrong")
        theirs = _entry(user_id=other, thesis="别人的判断")
        session.add(theirs)
        await session.flush()
        rows = await svc.export_rows(session, owner)
        return {
            "rows": rows,
            "owner": owner,
            "mine_id": mine.id,
            "theirs_id": theirs.id,
            "review_id": reviewed.id,
            "json": json.loads(svc.export_json(rows, now=datetime(2026, 10, 22, 9, 0))),
            "csv": svc.export_csv(rows),
        }

    data = await _with_rollback(_go)
    rows = data["rows"]
    assert [row.entry.id for row in rows] == [data["mine_id"]]
    assert rows[0].entry.user_id == data["owner"], "导出只能含本人（查询里就带 user_id）"
    assert rows[0].entry.thesis == "我的判断"
    assert rows[0].review is not None and rows[0].review.verdict == "thesis_wrong"
    assert data["theirs_id"] not in [row.entry.id for row in rows]
    # JSON：entries + snapshot + review
    payload = data["json"]
    assert payload["count"] == 1
    entry = payload["entries"][0]
    assert entry["id"] == data["mine_id"]
    assert entry["snapshot"]["price"] == PRICE
    assert entry["review"]["id"] == data["review_id"]
    assert entry["review"]["verdict"] == "thesis_wrong"
    assert entry["review"]["net_excess_pct"] == pytest.approx(2.88, abs=1e-4)
    assert "别人的判断" not in json.dumps(payload, ensure_ascii=False)


@requires_pg
async def test_export_csv_has_bom_and_fixed_column_order() -> None:
    """CSV：UTF-8 BOM 在字节层、列序 = 契约、缺失值是空串（不是 0），转义可回读。"""

    async def _go(session: AsyncSession) -> bytes:
        await _seed(session)
        owner = uuid.uuid4()
        reviewed = _entry(
            user_id=owner,
            kind="buy",
            position_size="heavy",
            confidence=4,
            thesis='含逗号, 引号" 与换行\n第二行',
            invalidation={"kind": "price_below", "value": 96.5},
        )
        await _review(session, reviewed, excess=3.0, verdict="discipline_break")
        session.add(_entry(user_id=owner, thesis="还没到期的一条"))
        await session.flush()
        return svc.export_csv(await svc.export_rows(session, owner))

    content = await _with_rollback(_go)
    assert content.startswith(b"\xef\xbb\xbf"), "Excel 打开中文不乱码：必须是 UTF-8 BOM"
    text = content.decode("utf-8-sig")
    reader = list(csv.reader(io.StringIO(text, newline="")))
    assert reader[0] == list(svc.EXPORT_CSV_COLUMNS)
    assert len(reader) == 3, "表头 + 两条（含一条未复盘）"
    header = dict(zip(reader[0], reader[1], strict=True))
    assert header["kind"] == "buy"
    assert header["position_size"] == "heavy"
    assert header["confidence"] == "4"
    assert header["thesis"] == '含逗号, 引号" 与换行\n第二行', "csv 转义必须能原样回读"
    assert json.loads(header["invalidation"]) == {"kind": "price_below", "value": 96.5}
    assert header["outcome_pct"] == "4.0000"
    assert header["excess_pct"] == "3.0000"
    assert header["net_excess_pct"] == "2.8800"
    assert header["verdict"] == "discipline_break"
    unreviewed = dict(zip(reader[0], reader[2], strict=True))
    # 未复盘：收益四列与 verdict 都是空串（不编 0，不写 "None"）
    assert unreviewed["outcome_pct"] == ""
    assert unreviewed["bench_pct"] == ""
    assert unreviewed["excess_pct"] == ""
    assert unreviewed["net_excess_pct"] == ""
    assert unreviewed["verdict"] == ""
    assert unreviewed["status"] == "open"
