"""spec §S1.7 —— **真 PG** 用例（只用 `TEST_DATABASE_URL`；不联网）。

纯函数不变量在 `tests/test_capital_flows_s17.py`；本文件锁"只有真库才能证"的三段：

**A. 写入路径**（离线自造行，走同一套 `_row` + 同一个 PIT 守卫）
1. 落库形状：`industry_key='market'` / `stock_id=0` / `freq='daily'` / 单位与
   `source_tier` 正确；`published_at` **非空、带时区**（当日北京时间 21:00）；
   `caliber_version` 非空。**绝不回退 `period`**。
2. **幂等**：同一批行 upsert 两次，表内行数不变（ADR 0014 的 B 类可回补）。
3. `extra` 的 JSONB 往返：`missing_reason` / `by_member` / 累计值写进去能读回来。

**B. Ruling 61 —— 缺数在**读面**可见**（本任务新加的接口面）
4. `GET /api/v1/metrics/latest` 的服务层（`get_latest_metrics_across_scopes`）对
   `value=NULL` 的行必须回传 `missing_reason` 与 `coverage` —— 否则前端只看到
   `value=null`，**分不清"源侧滞后"与"真缺数"**。
5. `GET /api/v1/metrics/series` 的服务层（`get_series`）里每个点同样带上两者。

**C. 覆盖率对账**：`capital_flows_coverage` 的 `null_days` 与 `missing_reasons`
必须能解释每一个 NULL 行（"有行无值"必须带原因）。

**不污染库**：所有写入都包在 `rollback` 的事务里（Ruling 17 的做法）。
**测试隔离**：库地址**只**取 `TEST_DATABASE_URL`，**不写死任何库名**；未设置时全文件 skip
（而不是猜一个默认库 —— 这正是本会话踩过的坑：`tests/test_intraday_snapshot_table.py`
曾硬编码 dev 库并对它跑破坏性 downgrade）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from typing import Any

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.industry_research import IndustryMetric
from app.services import capital_flows_service as svc
from app.services import industry_metric_service as metric_service

#: **只**认 `TEST_DATABASE_URL`。没有就 skip —— **绝不**回落到写死的 dev 库名。
ASYNC_URL = os.environ.get("TEST_DATABASE_URL", "")
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")


def _pg_reachable() -> bool:
    if not ASYNC_URL:
        return False

    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:  # noqa: BLE001 — 连不上就 skip，不猜库
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


_PG_OK = _pg_reachable()

requires_pg = pytest.mark.skipif(
    not _PG_OK, reason="需要 TEST_DATABASE_URL 指向可达的 PostgreSQL（本文件不写死库名）"
)


async def _with_rollback(coro_factory: Any) -> Any:
    """在一个**回滚**的事务里跑：测试不向库里留任何行。"""
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                result = await coro_factory(session)
                await session.flush()
                return result
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


#: 测试用的两个合成日（远离真实数据，避免与 dev 回补的行混淆）。
_NULL_DAY = date(2019, 1, 8)
_OK_DAY = date(2019, 1, 9)


def _synthetic_rows() -> list[dict[str, Any]]:
    """一批**离线可造**的行（走同一套 `_row` 构造器 + 同一个 PIT 守卫）。

    为什么不用 `upsert_capital_flows_range`（真联网）：本文件要证的是"算出来的东西
    **进得了这两列、出得来**"。联网取证在 Step-0 探针与回补脚本里做。
    """
    null_metric = svc.MetricValue(
        "etf_share_change",
        None,
        svc.MISSING_PARTIAL_BASKET_COVERAGE,
        {
            "caliber": "sum_of_daily_net_share_creation",
            "n_members": 6,
            "n_with_share": 1,
            "member_coverage": 0.166667,
            "missing_members": ["510050.SH"],
            "by_member": [{"ts_code": "510300.SH", "delta_wan": None}],
        },
    )
    ok_metric = svc.MetricValue(
        "southbound_net_buy",
        26.03,
        None,
        {
            "caliber": "first_difference_of_cumulative_net_buy",
            "cumulative_yi": 55470.72,
            "prev_cumulative_yi": 55444.69,
            "prev_trade_date": "2026-10-09",
        },
    )
    null_south = svc.MetricValue(
        "southbound_net_buy",
        None,
        svc.MISSING_PREV_CONNECT_DAY,
        {"caliber": "first_difference_of_cumulative_net_buy", "cumulative_yi": 28838.31},
    )
    return [
        svc._row(_NULL_DAY, null_metric),
        svc._row(_OK_DAY, ok_metric),
        svc._row(date(2019, 1, 10), null_south),
    ]


async def _count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(IndustryMetric)
                .where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                )
            )
        ).scalar_one()
    )


# ── A. 写入路径 ─────────────────────────────────────────────────────────


@requires_pg
def test_write_path_shape_pit_and_caliber() -> None:
    async def _go(session: AsyncSession) -> list[IndustryMetric]:
        rows = _synthetic_rows()
        await svc.upsert_metric_rows(session, rows)
        stmt = (
            select(IndustryMetric)
            .where(
                IndustryMetric.industry_key == "market",
                IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                IndustryMetric.period.in_([_NULL_DAY, _OK_DAY, date(2019, 1, 10)]),
            )
            .order_by(IndustryMetric.metric_key, IndustryMetric.period)
        )
        return list((await session.execute(stmt)).scalars())

    stored = asyncio.run(_with_rollback(_go))
    assert len(stored) == 3
    for row in stored:
        assert row.industry_key == "market" and row.stock_id == 0
        assert row.freq == "daily"
        assert row.caliber_version == svc.CALIBER_VERSIONS[row.metric_key]
        assert row.unit == svc.UNITS[row.metric_key]
        # PIT 是可空列：漏带会**静默**落 NULL（Ruling 59 的原始事故形态）
        assert row.published_at is not None
        assert isinstance(row.published_at, datetime)
        assert row.published_at.tzinfo is not None
        assert row.published_at.astimezone(svc._CN_TZ).hour == 21
    by_key = {(r.metric_key, r.period): r for r in stored}
    null_row = by_key[("etf_share_change", _NULL_DAY)]
    assert null_row.value is None
    assert null_row.extra is not None and null_row.extra["missing_reason"]
    assert null_row.extra["member_coverage"] == pytest.approx(0.166667)
    assert null_row.extra["by_member"][0]["ts_code"] == "510300.SH"


@requires_pg
def test_write_path_is_idempotent() -> None:
    async def _go(session: AsyncSession) -> tuple[int, int, int]:
        rows = _synthetic_rows()
        before = await _count(session)
        await svc.upsert_metric_rows(session, rows)
        after_first = await _count(session)
        await svc.upsert_metric_rows(session, rows)
        after_second = await _count(session)
        return before, after_first, after_second

    before, after_first, after_second = asyncio.run(_with_rollback(_go))
    assert after_first == before + 3
    assert after_second == after_first, "重跑同一批必须一行都不新增（唯一键 + DO UPDATE）"


# ── B. Ruling 61：缺数在**读面**可见 ────────────────────────────────────


@requires_pg
def test_read_surface_exposes_missing_reason_and_coverage() -> None:
    """`/api/v1/metrics/latest` 的服务层必须回传 `missing_reason` + `coverage`。

    这是 Ruling 61 的核心：S1.8/S1.9/S1.10/S1.11 都落了 `extra.missing_reason`，
    读面不回传的话前端只看到 `value=null`，**分不清"源侧滞后"与"真缺数"**。
    """
    day = date(2019, 2, 11)

    async def _go(session: AsyncSession) -> tuple[Any, Any]:
        null_metric = svc.MetricValue(
            "etf_share_change",
            None,
            svc.MISSING_PARTIAL_BASKET_COVERAGE,
            {"n_members": 6, "n_with_share": 5, "member_coverage": 0.833333},
        )
        ok_metric = svc.MetricValue("southbound_net_buy", 26.03, None, {"cumulative_yi": 55470.72})
        await svc.upsert_metric_rows(
            session,
            [svc._row(day, null_metric), svc._row(day, ok_metric)],
        )
        latest = await metric_service.get_latest_metrics_across_scopes(
            session,
            scope_keys=["market"],
            metric_keys=["etf_share_change", "southbound_net_buy"],
        )
        series = await metric_service.get_series(session, "market", "etf_share_change", "all")
        return latest, series

    latest, series = asyncio.run(_with_rollback(_go))
    by_key = {m.metric_key: m for m in latest}
    etf = by_key["etf_share_change"]
    assert etf.value is None
    assert etf.missing_reason == svc.MISSING_PARTIAL_BASKET_COVERAGE
    assert etf.coverage == pytest.approx(0.833333)
    # 有值的行不该被塞一个原因码（`None` = 无缺口）
    south = by_key["southbound_net_buy"]
    assert south.value == pytest.approx(26.03)
    assert south.missing_reason is None

    # 序列读面同理：每个点都要带得上原因与覆盖率
    points = [p for p in series.points if p.period == day and p.value is None]
    assert points, "序列里应能找到那个 NULL 点"
    assert points[0].missing_reason == svc.MISSING_PARTIAL_BASKET_COVERAGE
    assert points[0].coverage == pytest.approx(0.833333)


# ── C. 覆盖率对账 ───────────────────────────────────────────────────────


@requires_pg
def test_coverage_explains_every_null_row() -> None:
    async def _go(session: AsyncSession) -> Any:
        await svc.upsert_metric_rows(session, _synthetic_rows())
        return await svc.capital_flows_coverage(
            session, day_lo=date(2019, 1, 1), day_hi=date(2019, 1, 31)
        )

    coverage = asyncio.run(_with_rollback(_go))
    by_key = {c.metric_key: c for c in coverage}
    assert set(by_key) == set(svc.ALL_METRIC_KEYS)
    etf = by_key["etf_share_change"]
    assert etf.rows == 1 and etf.covered_days == 0 and etf.null_days == 1
    assert etf.missing_reasons == {svc.MISSING_PARTIAL_BASKET_COVERAGE: 1}
    south = by_key["southbound_net_buy"]
    assert south.rows == 2 and south.covered_days == 1 and south.null_days == 1
    assert south.missing_reasons == {svc.MISSING_PREV_CONNECT_DAY: 1}
    # 待接/只登记口径的两条：0 行，但必须**写明为什么没有**
    assert by_key["industrial_capital_net_change"].rows == 0
    assert by_key["industrial_capital_net_change"].pending_reason == svc.MISSING_UPSTREAM_PENDING
    assert by_key["northbound_holding_quarterly"].pending_reason == svc.MISSING_NOT_INGESTED
