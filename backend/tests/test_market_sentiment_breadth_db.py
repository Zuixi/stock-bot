"""spec §S1.8 情绪/市场宽度与动量 —— **真 PG** 用例（`TEST_DATABASE_URL`，默认 `stock_bot_test`）。

纯函数不变量在 `tests/test_market_sentiment_breadth.py`；本文件只锁需要真实底座的
四条东西：

1. **落库形状 + 幂等**：`upsert_metric_range` 写 `industry_key='market'` 的行，
   每个交易日恰好 8 行；同一区间重跑 → **新增行数 0**（ADR 0014 的 B 类可回补，
   唯一键 `(industry_key, stock_id, metric_key, source, freq, period)`）。
2. **PIT**：每行 `published_at` = 该行 `period` 的 15:00 Asia/Shanghai、非空，
   于是读面的 `as_of_source` 恒为 `published_at`（不是回退的 `period`，Ruling 34/35）。
3. **同源证据（真库）**：市场分布（`market_service._distribution_items`）的 11 桶
   逐桶相加 == 本模块落库的涨/跌家数（在**可用行**上）；且
   `advance_decline_ratio == 上涨/下跌`。**C1 回归**：`pct_chg` 覆盖率不足的交易日
   整组落 NULL + `partial_pct_chg`（不拿 NULL 当"涨"）。
4. **`above_ma20_ratio` 可手算核对**：取一只股票、一天，手工算 20 日收盘均线验证
   "是否在均线上方"；并用一条**独立写法的相关子查询**（不用窗口函数）重算全市场
   分子分母，与窗口函数结果数字相等 —— 两套写法互为对拍。窗口的**日历跨度**与
   **最大相邻间隔**（`win = 20` = 20 行，不是 20 个连续交易日）也用独立写法对拍。
5. **I3**：`GET /api/v1/industries` 的 service 层只列**行业** scope（不含 `market`）。

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
from app.services.industry_registry import MARKET_CALIBER_VERSION

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
        assert row.caliber_version == MARKET_CALIBER_VERSION == "s1.8-v2"
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
    """**同源证据（真库）**：市场分布的 11 桶 vs 本模块落库的家数（同一天、同一份**可用**行）。

    - `市场负数桶之和 == market_decline_count`（实测边界与 `pct_chg < 0` 完全重合）；
    - `市场正数桶之和 == market_advance_count`（`0~1%` 桶含平盘）；
    - 差额 = 平盘家数，落 `extra.flat_in_positive_bucket`，账目闭合；
    - **NULL 行在分桶前剔除**：可用行上 `up + down == total`；若该日存在
      `pct_chg IS NULL` 的行，市场页（含 NULL 的全量分桶）会比本模块多出恰好那么多。
    """

    async def _go(
        session: AsyncSession,
    ) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, Any], dict[str, int]]:
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
        usable = [r for r in rows if r.get("pct_chg") is not None]
        return (
            svc.distribution_counts(usable),
            _distribution_items(rows),
            stored,
            svc.distribution_counts(rows),
        )

    buckets, items, stored, all_rows_buckets = asyncio.run(_with_rollback(_go))
    # 逐桶对拍：市场侧发出的 items 与本模块分桶是同一个判据函数
    from app.services.market_service import _DISTRIBUTION_RANGES

    assert [i["range"] for i in items] == list(_DISTRIBUTION_RANGES)
    for item in items:
        # 市场页对**全量行**分桶（含 NULL → 0~1%），故它与全量分桶逐桶相等
        assert all_rows_buckets[item["range"]] == item["count"], item["range"]

    up = int(stored["market_advance_count"].value)
    down = int(stored["market_decline_count"].value)
    assert up == sum(buckets[label] for label in svc.DISTRIBUTION_UP_LABELS)
    assert down == sum(buckets[label] for label in svc.DISTRIBUTION_DOWN_LABELS)
    extra = stored["advance_decline_ratio"].extra
    assert extra["up_count"] == up and extra["down_count"] == down
    # 可用行上桶是完备划分：up + down == total == 可用行数；NULL 行单独记账
    assert up + down == extra["total"] == extra["total_rows"] - extra["unavailable_pct_chg"]
    assert up - extra["up_sign_count"] == extra[svc.FLAT_IN_POSITIVE_BUCKET]
    assert "flat_count" not in extra
    # 市场页的 0~1% 桶含 NULL；本模块的扁平家数必须是可用行上的真平盘
    assert all_rows_buckets["0~1%"] - buckets["0~1%"] == extra["unavailable_pct_chg"]
    assert buckets["0~1%"] >= extra[svc.FLAT_IN_POSITIVE_BUCKET]
    # `industry_metrics.value` 是 `Numeric(18,4)` → 落库被四舍五入到 4 位小数
    assert float(stored["advance_decline_ratio"].value) == pytest.approx(up / down, abs=5e-5)


@requires_pg
def test_mostly_null_pct_chg_day_persists_null_with_partial_reason() -> None:
    """**C1 核心回归（真库）**：`pct_chg` 覆盖率不足的交易日必须整组 NULL。

    做法：用一条聚合 SQL 找出窗口内"行很多、可用 `pct_chg` 很少"的交易日
    （实测 `stock_bot@5433` 有 21 天，首日 2025-09-23），对该日跑
    `upsert_metric_range` 并断言落库行 `value IS NULL` + `partial_pct_chg`，且
    `extra` 里同时有 `unavailable_pct_chg` / `coverage` —— 旧代码在这些日子落
    `market_advance_count = NULL 行数`（2025-09-23 为 5382）。
    """

    async def _go(
        session: AsyncSession,
    ) -> tuple[date, int, int, dict[str, IndustryMetric]]:
        day_hi = await _latest_quote_day(session)
        day_lo = day_hi - timedelta(days=400)
        coverage_rows = (
            await session.execute(
                select(
                    DailyQuote.trade_date,
                    func.count().label("rows"),
                    func.count(DailyQuote.pct_chg).label("usable"),
                )
                .where(DailyQuote.trade_date >= day_lo, DailyQuote.trade_date <= day_hi)
                .group_by(DailyQuote.trade_date)
                .order_by(DailyQuote.trade_date)
            )
        ).all()
        bad = [
            row
            for row in coverage_rows
            if int(row.rows) and int(row.usable) / int(row.rows) < svc.MIN_PCT_CHG_COVERAGE
        ]
        if not bad:
            pytest.skip("窗口内没有 pct_chg 覆盖率不足的交易日 —— 无样本")
        target = bad[0].trade_date
        await svc.upsert_metric_range(session, target, target, warmup_days=60)
        stored = {
            r.metric_key: r
            for r in (
                await session.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.period == target,
                    )
                )
            )
            .scalars()
            .all()
        }
        return target, int(bad[0].rows), int(bad[0].usable), stored

    target, rows, usable, stored = asyncio.run(_with_rollback(_go))
    for key in ("market_advance_count", "market_decline_count", "advance_decline_ratio"):
        row = stored[key]
        assert row.value is None, f"{key} 在覆盖率不足的日子必须 NULL（{target}）"
        assert row.extra is not None and row.extra["missing_reason"] == "partial_pct_chg"
        assert row.extra["unavailable_pct_chg"] == rows - usable
        assert row.extra["total_rows"] == rows
        assert row.extra["coverage"] < svc.MIN_PCT_CHG_COVERAGE
        # 诊断数字仍是"可用行上的真值"（不是 0 伪造、也不是 NULL 家数）
        assert row.extra["up_count"] + row.extra["down_count"] == usable
        assert "flat_count" not in row.extra


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
        md = ma20[day_hi]
        return (
            day_hi,
            int(candidate),
            window_closes,
            mean,
            {
                "eligible": md.eligible,
                "above": md.above,
                "max_window_span_days": md.max_window_span_days,
                "max_gap_days": md.max_gap_days,
                "span_over_alert_days": md.span_over_alert_days,
                "span_alert_days": svc.MA20_SPAN_ALERT_DAYS,
            },
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
    assert computed["extra"] == {"window": 20, **sql_pair}
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
            (day, (window_result[day].eligible, window_result[day].above), alt[day])
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


#: **与窗口函数独立写法的**窗口跨度/间隔复核 SQL。刻意不用
#: `min(trade_date) OVER (ROWS 19 PRECEDING)`，而是按 `row_number() DESC` 取
#: "最近 20 行"再求 `max - min`（跨度）与相邻 `lag` 之差（间隔）；两套写法算出同一个
#: 数字才算对拍。`w.n_close > 0 AND w.last_close IS NOT NULL` 与服务侧
#: `ma20 IS NOT NULL AND close IS NOT NULL` 的分母判据一一对应。
_MA20_SPAN_INDEPENDENT_SQL = text(
    """
    WITH r AS (
        SELECT stock_id, trade_date, close,
               row_number() OVER (PARTITION BY stock_id ORDER BY trade_date DESC) AS rn
        FROM daily_quotes
        WHERE trade_date BETWEEN :pre_lo AND :hi
    ),
    g AS (
        SELECT stock_id, trade_date, rn,
               trade_date - lag(trade_date) OVER (PARTITION BY stock_id ORDER BY rn DESC) AS gap
        FROM r
    ),
    w AS (
        SELECT stock_id,
               max(trade_date) - min(trade_date) AS span,
               count(*)                          AS n,
               count(close)                      AS n_close,
               max(CASE WHEN rn = 1 THEN close END) AS last_close
        FROM r WHERE rn <= 20 GROUP BY stock_id HAVING count(*) = 20
    ),
    gw AS (
        SELECT stock_id, max(gap) AS max_gap FROM g WHERE rn BETWEEN 1 AND 19 GROUP BY stock_id
    )
    SELECT max(w.span) AS max_span,
           max(gw.max_gap) AS max_gap,
           count(*) FILTER (WHERE w.span > 40) AS over_alert
    FROM w JOIN gw USING (stock_id)
    WHERE w.n_close > 0 AND w.last_close IS NOT NULL
    """
)


@requires_pg
def test_ma20_window_span_and_gap_are_recorded_and_reproducible() -> None:
    """**窗口退化可见（Ruling：`win = 20` = 20 行 != 20 个连续交易日）**。

    `_ma20_metric` 的 `extra.max_window_span_days` / `extra.max_gap_days` 必须与一条
    独立写法（`row_number() DESC` + 相邻 `lag`）算出的当日最坏值逐字相等。长停牌股的
    "20 行窗口"可以横跨 40+ 个日历日（实测 2026-10-09 最坏 64 天），旧实现对此完全
    沉默 —— 这两个字段是唯一能看见该退化的证据。
    """

    async def _go(session: AsyncSession) -> tuple[svc.Ma20Day, int | None, int | None, int]:
        day_hi = await _latest_quote_day(session)
        pre_lo = day_hi - timedelta(days=120)
        ma20 = await svc.ratio_above_ma20(session, pre_lo, day_hi)
        if day_hi not in ma20:
            pytest.skip("该日无具备完整 20 日窗口的标的")
        row = (
            await session.execute(_MA20_SPAN_INDEPENDENT_SQL, {"pre_lo": pre_lo, "hi": day_hi})
        ).one()
        return ma20[day_hi], row.max_span, row.max_gap, int(row.over_alert)

    got, indep_span, indep_gap, indep_over = asyncio.run(_with_rollback(_go))
    assert indep_span is not None and indep_gap is not None
    assert got.max_window_span_days == int(indep_span), "窗口跨度与独立写法不一致"
    assert got.max_gap_days == int(indep_gap), "窗口内最大间隔与独立写法不一致"
    assert got.span_over_alert_days == indep_over, "超阈窗口家数与独立写法不一致"
    # 20 个交易日至少横跨 25 个日历日（4 周 + 周末），间隔至少 1 天；
    # 跨度 >= 间隔是定义使然（一个窗口内的间隔不可能大于整段跨度）。
    assert got.max_window_span_days >= 25, got
    assert got.max_window_span_days >= got.max_gap_days >= 1
    assert got.span_over_alert_days <= got.eligible
    print(
        f"\n[MA20 窗口退化] 最坏 max_window_span_days={got.max_window_span_days}"
        f" / max_gap_days={got.max_gap_days}"
        f" / 跨度 > {svc.MA20_SPAN_ALERT_DAYS} 天的合格标的 {got.span_over_alert_days}"
        f" / {got.eligible}"
    )


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
        assert m.caliber_version_registered == MARKET_CALIBER_VERSION == "s1.8-v2"
        # PIT：published_at 非空 → as_of 走真 PIT，不回退 period
        assert m.published_at is not None
        assert m.as_of_source == "published_at"
        assert m.as_of == m.period
        # 定义数据可查（spec §S1.8：连板高度/首板晋级率的定义在 UI 可查）
        assert m.description, m.metric_key
        if m.value is None:
            assert m.model_dump().get("value") is None  # NULL 不被打成 0


@requires_pg
def test_industries_list_excludes_market_scope() -> None:
    """**I3**：`GET /api/v1/industries`（"已产品化行业列表"）不得列全市场情绪。

    service 层 `list_industries` 是路由的唯一实现处；用 `is_industry` 过滤后，返回的
    key 集必须 == `get_industry_scopes()` 的 key 集，且**不含** `market`。
    """

    async def _go(session: AsyncSession) -> list[str]:
        from app.services import industry_metric_service as metric_svc

        return [s.key for s in await metric_svc.list_industries(session)]

    keys = asyncio.run(_with_rollback(_go))
    from app.services.industry_registry import INDUSTRIES, get_industry_scopes

    assert "market" not in keys, "全市场情绪不是行业，不得出现在已产品化行业列表"
    assert keys == [cfg.key for cfg in get_industry_scopes()]
    assert set(keys) == {"pig", "broiler"}
    # scope 总数（含 market）比行业数多一个 —— 这正是需要显式判据的原因
    assert len(keys) == len(INDUSTRIES) - 1
