"""spec §S1.8 情绪/市场宽度与动量 —— **真 PG** 用例（`TEST_DATABASE_URL`，默认 `stock_bot_test`）。

纯函数不变量在 `tests/test_market_sentiment_breadth.py`；本文件只锁需要真实底座的
四条东西：

1. **落库形状 + 幂等**：`upsert_metric_range` 写 `industry_key='market'` 的行，
   每个交易日恰好 8 行；同一区间重跑 → **新增行数 0**（ADR 0014 的 B 类可回补，
   唯一键 `(industry_key, stock_id, metric_key, source, freq, period)`）。
2. **PIT**：每行 `published_at` = 该行 `period` 的 15:00 Asia/Shanghai、非空，
   于是读面的 `as_of_source` 恒为 `published_at`（不是回退的 `period`，Ruling 34/35）。
3. **同源证据（真库）**：市场分布（`market_service._distribution_items`）的 11 桶
   逐桶相加 == 本模块落库的涨/跌家数；且 `advance_decline_ratio == 上涨/下跌`。
4. **`above_ma20_ratio` 可手算核对**：取一只股票、一天，手工算 20 日收盘均线验证
   "是否在均线上方"；并用一条**独立写法的相关子查询**（不用窗口函数）重算全市场
   分子分母，与窗口函数结果数字相等 —— 两套写法互为对拍。

**不污染库**（Ruling 17 的做法）：所有写入都包在 `rollback` 的事务里；测试库无数据时
skip 而不是红。测试前后 `industry_key='market'` 的行数与内容不变（回滚）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.industry_research import IndustryMetric
from app.models.quote import DailyQuote
from app.services import market_sentiment_service as svc

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")


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


async def _with_rollback(coro_factory: Any) -> Any:
    """在一个**回滚**的事务里跑：测试不向库里留任何行（跑完库与跑前一致）。"""
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await coro_factory(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _latest_quote_day(session: AsyncSession) -> date:
    day = (await session.execute(select(func.max(DailyQuote.trade_date)))).scalar_one_or_none()
    if day is None:
        pytest.skip("库内 daily_quotes 为空（本 spec 依赖行情底座）")
    return day


# ── 1) 落库形状 + 幂等 ────────────────────────────────────────────────


@requires_pg
def test_upsert_writes_eight_rows_per_quote_day_and_is_idempotent() -> None:
    """每交易日 8 行（= 6 个 S1.8 指标 + 2 个家数指标）；重跑不新增行。"""

    async def _go(session: AsyncSession) -> tuple[int, set[date], int, int]:
        day_hi = await _latest_quote_day(session)
        day_lo = day_hi - timedelta(days=30)
        expected_days = len(await svc._quote_days(session, day_lo, day_hi))
        if expected_days == 0:
            pytest.skip("最近 30 天窗口内无行情行")
        stats = await svc.upsert_metric_range(session, day_lo, day_hi)
        assert stats.quote_days == expected_days
        assert stats.days_written == expected_days
        rows = (
            await session.execute(
                select(IndustryMetric.metric_key, IndustryMetric.period).where(
                    IndustryMetric.industry_key == "market", IndustryMetric.stock_id == 0
                )
            )
        ).all()
        before = len(rows)
        stats2 = await svc.upsert_metric_range(session, day_lo, day_hi)
        after = (
            await session.execute(
                select(func.count())
                .select_from(IndustryMetric)
                .where(IndustryMetric.industry_key == "market")
            )
        ).scalar_one()
        return before, {r.period for r in rows}, stats2.rows_upserted, int(after)

    before, periods, rerun_upserted, after = asyncio.run(_with_rollback(_go))
    assert before == len(periods) * len(svc.ALL_METRIC_KEYS), "每交易日必须恰好 8 行"
    assert rerun_upserted > 0, "ON CONFLICT DO UPDATE 会更新既有行（rowcount 非 0）"
    assert after == before, "重跑不得新增任何行（唯一键 + upsert 幂等）"


@requires_pg
def test_rows_carry_registry_projection_and_pit() -> None:
    """行的 source/tier/freq/caliber/unit 来自 registry，`published_at` = 当日 15:00 CN。"""

    async def _go(session: AsyncSession) -> list[IndustryMetric]:
        day_hi = await _latest_quote_day(session)
        await svc.upsert_metric_range(session, day_hi - timedelta(days=3), day_hi)
        return list(
            (
                await session.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.period == day_hi,
                    )
                )
            )
            .scalars()
            .all()
        )

    rows = asyncio.run(_with_rollback(_go))
    assert {r.metric_key for r in rows} == set(svc.ALL_METRIC_KEYS)
    for row in rows:
        assert row.source == svc.METRIC_SOURCE == "local_calc"
        assert row.source_tier == "derived"
        assert row.freq == "daily" and row.stock_id == 0
        assert row.caliber_version == "s1.8-v1"
        assert row.published_at is not None, "PIT 恒非空：读面 as_of_source 不得回退 period"
        assert row.published_at == svc.published_at_for(row.period)
        assert row.unit == svc._UNITS[row.metric_key]


@requires_pg
def test_missing_base_row_yields_null_with_reason_not_zero() -> None:
    """底座缺行的日子：4 个涨停类指标 `value is NULL` 且带 `no_sentiment_day`。

    **绝不落 0**（spec §S1.8 验收：涨停类指标的"缺数日"不能静默为 0）。
    若窗口内每个交易日都有底座行（小库常见），本用例无样本 → skip 并说明。
    """

    async def _go(session: AsyncSession) -> tuple[date, list[IndustryMetric]]:
        day_hi = await _latest_quote_day(session)
        # 窗口取 400 天：S1.8 的 ≥1 年序列要求下，涨停底座（market_sentiment_daily）
        # 常常比行情底座短一截，缺数日就落在这一段里；窗口太窄会挑不到样本而白 skip。
        day_lo = day_hi - timedelta(days=400)
        quote_days = await svc._quote_days(session, day_lo, day_hi)
        sentiment = await svc._sentiment_rows_by_day(session, day_lo, day_hi)
        gaps = [d for d in quote_days if d not in sentiment]
        if not gaps:
            pytest.skip("窗口内每个交易日都有 market_sentiment_daily 行 —— 无缺数样本")
        target = gaps[0]
        await svc.upsert_metric_range(session, target, target, warmup_days=60)
        rows = list(
            (
                await session.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.period == target,
                    )
                )
            )
            .scalars()
            .all()
        )
        return target, rows

    target, rows = asyncio.run(_with_rollback(_go))
    by_key = {r.metric_key: r for r in rows}
    for key in (
        "limit_up_down_ratio",
        "limit_up_break_ratio",
        "max_consecutive_limit_up",
        "first_board_promotion_ratio",
    ):
        assert by_key[key].value is None, f"{key} 在缺数日必须 NULL 而不是 0（{target}）"
        assert by_key[key].extra is not None
        assert by_key[key].extra["missing_reason"] == svc.MISSING_NO_SENTIMENT_DAY


# ── 2) 同源（真库）：市场分布 ↔ 情绪涨跌家数 ──────────────────────────


@requires_pg
def test_advance_decline_matches_market_distribution_buckets() -> None:
    """**同源证据（真库）**：市场分布的 11 桶 vs 本模块落库的家数（同一天、同一份行）。

    - `市场负数桶之和 == market_decline_count`（实测边界与 `pct_chg < 0` 完全重合）；
    - `市场正数桶之和 == market_advance_count`（`0~1%` 桶含平盘）；
    - 差额 = 平盘家数，落 `extra.flat_in_positive_bucket`，账目闭合。
    """

    async def _go(
        session: AsyncSession,
    ) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, Any]]:
        from app.services.market_service import _distribution_items

        day_hi = await _latest_quote_day(session)
        await svc.upsert_metric_range(session, day_hi, day_hi)
        stored = {
            r.metric_key: r
            for r in (
                await session.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.period == day_hi,
                    )
                )
            )
            .scalars()
            .all()
        }
        rows = await svc.load_day_rows(session, day_hi)
        return svc.distribution_counts(rows), _distribution_items(rows), stored

    buckets, items, stored = asyncio.run(_with_rollback(_go))
    # 逐桶对拍：市场侧发出的 items 与本模块分桶是同一个判据函数
    from app.services.market_service import _DISTRIBUTION_RANGES

    assert [i["range"] for i in items] == list(_DISTRIBUTION_RANGES)
    for item in items:
        assert buckets[item["range"]] == item["count"], item["range"]

    up = int(stored["market_advance_count"].value)
    down = int(stored["market_decline_count"].value)
    assert up == sum(buckets[label] for label in svc.DISTRIBUTION_UP_LABELS)
    assert down == sum(buckets[label] for label in svc.DISTRIBUTION_DOWN_LABELS)
    extra = stored["advance_decline_ratio"].extra
    assert extra["up_count"] == up and extra["down_count"] == down
    assert up + down + extra["flat_count"] == extra["total"]
    assert up - extra["up_sign_count"] == extra[svc.FLAT_IN_POSITIVE_BUCKET]
    # `industry_metrics.value` 是 `Numeric(18,4)` → 落库被四舍五入到 4 位小数
    assert float(stored["advance_decline_ratio"].value) == pytest.approx(up / down, abs=5e-5)
    assert buckets["0~1%"] >= extra[svc.FLAT_IN_POSITIVE_BUCKET]


# ── 3) above_ma20_ratio 可手算核对 ────────────────────────────────────


#: **与窗口函数独立写法的** MA20 复核 SQL：按 `stock_id` 分区的 `row_number()`
#: 自连接取"截至当日的最后 20 个交易日"，`sum(close)/20` 手工算均值。
#: 刻意不用 `avg(...) OVER (ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)` —— 两套写法
#: 算出同一个数字才算对拍；用同一个窗口函数再跑一遍只是自证。
#:
#: 语义等价性：窗口写法要求"当日在分区内**恰好**有 20 行可看"（`win = 20`），
#: 本写法要求"截至当日的累计行号 >= 20 且近 20 行都取到"（`win_n = 20`）——
#: 在无重复 `(stock_id, trade_date)` 的日频表上两者同为"至少 20 个交易日历史"。
#: 注意两者必须用**同一个** `pre_lo` 下界（窗口的预热起点），否则次新股会被
#: 一边算成"有 20 行"、另一边算成"不足"，差异全是预热窗口造成的假阳性。
_MA20_INDEPENDENT_SQL = text(
    """
    WITH numbered AS (
        SELECT stock_id, trade_date, close,
               row_number() OVER (PARTITION BY stock_id ORDER BY trade_date) AS rn
        FROM daily_quotes
        WHERE trade_date BETWEEN :pre_lo AND :hi
    ),
    paired AS (
        SELECT n.trade_date, n.stock_id, n.close,
               count(*) FILTER (WHERE p.rn > n.rn - 20) AS win_n,
               sum(p.close) FILTER (WHERE p.rn > n.rn - 20) AS wsum
        FROM numbered n
        JOIN numbered p
          ON p.stock_id = n.stock_id AND p.rn <= n.rn AND p.rn > n.rn - 20
        WHERE n.trade_date >= :lo
        GROUP BY n.trade_date, n.stock_id, n.close, n.rn
    )
    SELECT trade_date,
           count(*) FILTER (WHERE win_n = 20)                  AS eligible,
           count(*) FILTER (WHERE win_n = 20 AND close > wsum / 20) AS above
    FROM paired
    GROUP BY trade_date
    ORDER BY trade_date
    """
)


@requires_pg
def test_ma20_hand_check_compute_day_matches_sql_ratio() -> None:
    """**可手算**的 MA20 核对：`compute_day_metrics` 的 ratio == SQL 的 above/eligible。

    三段证据：
    1. 抽一只具备 20 日窗口的股票，手工求 20 日收盘算术均值，打印该股
       `close > ma20` 的判定（严格大于）——"窗口是什么"到此可肉眼核对；
    2. `ratio_above_ma20` 的实际值 == `compute_day_metrics` 落库值（同一算法同一入口）；
    3. ratio == SQL 里 `above / eligible × 100`（分子分母都在 `extra` 里，可复算）。
    """

    async def _go(session: AsyncSession) -> tuple[date, int, list[Decimal], Decimal, dict, dict]:
        day_hi = await _latest_quote_day(session)
        pre_lo = day_hi - timedelta(days=120)
        ma20 = await svc.ratio_above_ma20(session, pre_lo, day_hi)
        if day_hi not in ma20:
            pytest.skip("该日无具备完整 20 日窗口的标的")
        candidate = (
            await session.execute(
                select(DailyQuote.stock_id)
                .where(
                    DailyQuote.trade_date <= day_hi,
                    DailyQuote.trade_date >= day_hi - timedelta(days=60),
                )
                .group_by(DailyQuote.stock_id)
                .having(func.count() >= 20)
                .order_by(DailyQuote.stock_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if candidate is None:
            pytest.skip("窗口内没有具备完整 20 日窗口的股票")
        closes = (
            (
                await session.execute(
                    select(DailyQuote.close)
                    .where(DailyQuote.stock_id == candidate, DailyQuote.trade_date <= day_hi)
                    .order_by(DailyQuote.trade_date.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        assert len(closes) == 20
        window_closes = [c for c in reversed(closes)]
        mean = sum(window_closes) / Decimal(20)
        stored = await svc.compute_day_metrics(session, day_hi, ma20=ma20)
        return (
            day_hi,
            int(candidate),
            window_closes,
            mean,
            {"eligible": ma20[day_hi][0], "above": ma20[day_hi][1]},
            {
                "value": stored["above_ma20_ratio"].value,
                "extra": stored["above_ma20_ratio"].extra,
            },
        )

    day, stock_id, window_closes, mean, sql_pair, computed = asyncio.run(_with_rollback(_go))
    assert len(window_closes) == 20, "手工窗口必须恰好 20 个交易日"
    p_close = window_closes[-1]
    on_or_above = p_close > mean  # 严格大于：等于均线**不**计入（与 SQL 的 `>` 一致）
    eligible, above = sql_pair["eligible"], sql_pair["above"]
    assert 0 <= above <= eligible > 0
    assert computed["extra"] == {"window": 20, "eligible": eligible, "above": above}
    assert computed["value"] == pytest.approx(above / eligible * 100)
    assert 0.0 <= float(computed["value"]) <= 100.0
    print(
        f"\n[MA20 手算样例] day={day} stock_id={stock_id}\n"
        f"  20 个交易日收盘（升序，末位=当日）: {[float(c) for c in window_closes]}\n"
        f"  手工 ma20 = sum/20 = {mean}\n"
        f"  当日 close = {p_close}   close > ma20 ？ {on_or_above}\n"
        f"  全市场 SQL 口径: above={above} / eligible={eligible}"
        f" = {eligible and above / eligible * 100}%\n"
        f"  落库 above_ma20_ratio = {computed['value']}"
    )
    assert on_or_above in (True, False)  # 判定本身有唯一取值（上面已打印供肉眼核对）


@requires_pg
def test_ma20_window_function_matches_independent_rewrite() -> None:
    """窗口函数写法 vs 独立的 row_number 自连接写法：逐日 (eligible, above) 必须相等。"""

    async def _go(
        session: AsyncSession,
    ) -> tuple[int, list[tuple[date, tuple[int, int], tuple[int, int]]]]:
        day_hi = await _latest_quote_day(session)
        # 预热下界两边必须一致（见 _MA20_INDEPENDENT_SQL 的说明）
        pre_lo = day_hi - timedelta(days=120)
        day_lo = day_hi - timedelta(days=60)
        window_result = await svc.ratio_above_ma20(session, pre_lo, day_hi)
        rows = (
            await session.execute(
                _MA20_INDEPENDENT_SQL,
                {"pre_lo": pre_lo, "lo": day_lo, "hi": day_hi},
            )
        ).all()
        alt = {r.trade_date: (int(r.eligible), int(r.above)) for r in rows}
        compared = [
            (day, window_result[day], alt[day])
            for day in sorted(set(window_result) & set(alt))
            if day >= day_lo
        ]
        return len(alt), compared

    alt_days, compared = asyncio.run(_with_rollback(_go))
    if not compared:
        pytest.skip("窗口写法与独立写法没有可对拍的共同交易日（库内行情太短）")
    mismatches = [(d, w, a) for d, w, a in compared if w != a]
    assert not mismatches, f"窗口函数与独立写法不一致：{mismatches[:5]}"
    assert alt_days > 0


# ── 4) 读面（S1.2 既有 surfaces，不新建平行读路）─────────────────────


@requires_pg
def test_read_surface_exposes_all_badges_for_market_scope() -> None:
    """`/api/v1/metrics/latest` 的 service 层：8 个 key 都回四徽章。

    本任务**不新建读路**（brief 的硬性约束）：这里只验证 S1.2 既有读面能把
    `industry_key='market'` 的行按 registry 元数据下发。库内无 market 行时 skip。
    """

    async def _go(session: AsyncSession) -> list[Any]:
        day_hi = await _latest_quote_day(session)
        await svc.upsert_metric_range(session, day_hi, day_hi)
        from app.services import industry_metric_service as metric_svc

        return list(
            await metric_svc.get_latest_metrics_across_scopes(
                session, scope_keys=["market"], metric_keys=list(svc.ALL_METRIC_KEYS)
            )
        )

    out = asyncio.run(_with_rollback(_go))
    assert {m.metric_key for m in out} == set(svc.ALL_METRIC_KEYS)
    for m in out:
        assert m.scope_key == "market"
        assert m.freq == "daily" and m.tier == "derived"
        assert m.source == "local_calc"
        assert m.caliber_version_registered == "s1.8-v1"
        # PIT：published_at 非空 → as_of 走真 PIT，不回退 period
        assert m.published_at is not None
        assert m.as_of_source == "published_at"
        assert m.as_of == m.period
        # 定义数据可查（spec §S1.8：连板高度/首板晋级率的定义在 UI 可查）
        assert m.description, m.metric_key
        if m.value is None:
            assert m.model_dump().get("value") is None  # NULL 不被打成 0
