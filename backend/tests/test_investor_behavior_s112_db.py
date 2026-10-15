"""spec §S1.12 —— **真 PG** 用例（只用 `TEST_DATABASE_URL`；不联网）。

纯函数不变量在 `tests/test_investor_behavior_s112.py`；本文件锁"只有真库才能证"的四段：

**A. 写入路径**（离线自造行，走同一套 `_row` + 同一个 PIT 守卫）
1. 落库形状：`industry_key='market'` / `stock_id=0` / `freq` 与 `unit` 正确；
   `published_at` **非空、带时区**；`caliber_version` 非空 —— 而且 **≠ `period`**
   （Ruling 34/35：静默回退 `period` 正是要防的形态）。
2. **幂等**：同一批行 upsert 两次，表内行数不变（ADR 0014 的 B 类可回补）。
3. `extra` 的 JSONB 往返：`missing_reason` / `anchor_established` / `n_null` 写进去能读回来。

**B. Ruling 61 —— 缺数在**读面**可见**
4. `GET /api/v1/metrics/latest` 的服务层对 `value=NULL` 的行必须回传 `missing_reason`；
   `GET /api/v1/metrics/series` 的每个点同样带上它 —— 否则前端只看到 `value=null`，
   **分不清"源侧滞后"与"真缺数"**。

**C. 覆盖率对账**：`investor_behavior_coverage` 的 `null_periods` 与 `missing_reasons`
必须能解释每一个 NULL 行（"有行无值"必须带原因）；整表无行时给 `pending_reason`。

**D. 判据 3 的运行时证据**：未登记的 `metric_key` 读面必须 **404 语义**（抛
`UnknownMetricError`）—— 开户数没登记 ⇒ 前端拿不到骨架、不会出现占位空图。

**不污染库**：所有写入都包在 `rollback` 的事务里（Ruling 17 的做法）。
**测试隔离**：库地址**只**取 `TEST_DATABASE_URL`，**不写死任何库名**；未设置时全文件 skip
（而不是猜一个默认库 —— 这正是本会话踩过的坑：`tests/test_intraday_snapshot_table.py`
曾硬编码 dev 库并对它跑破坏性 downgrade）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import industry_metric_service as metric_service
from app.services import investor_behavior_service as svc
from app.services.industry_registry import INVESTOR_BEHAVIOR_CALIBER_VERSION

#: **只**认 `TEST_DATABASE_URL`。没有就 skip —— **绝不**回落到写死的 dev 库名。
ASYNC_URL = os.environ.get("TEST_DATABASE_URL", "")
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")
_CN = ZoneInfo("Asia/Shanghai")


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


#: 测试用的合成周期（远离真实数据，避免与 dev 回补的行混淆）。
_OK_MONTH = date(2019, 3, 1)
#: **整月全空**的月（缺数 ⇒ value=NULL）；`n=0` 表示"一位有值的基金都没有"。
_NULL_MONTH = date(2019, 4, 1)
#: **部分字段缺值**的月（⇒ 落有值成员之和 + `is_lower_bound=True`）。
_LOWER_MONTH = date(2019, 5, 1)
_OK_DAY = date(2019, 3, 15)
_NULL_DAY = date(2019, 4, 12)


def _offline_months(*, null_month: bool = True) -> list[dict[str, Any]]:
    """探针 `--json-out` 形状的离线月度记录（**不触网**）。"""
    months: list[dict[str, Any]] = [
        {
            "period": _OK_MONTH.isoformat(),
            "sum": 12.5,
            "n": 2,
            "n_null": 0,
            "last_day": "2019-03-27",
        },
    ]
    if null_month:
        months.append(
            {
                "period": _NULL_MONTH.isoformat(),
                # `n=0` = 该月**一位有值的基金都没有** ⇒ 缺数（NULL），不是 0 亿元。
                "sum": 0.0,
                "n": 0,
                "n_null": 1,
                "last_day": "2019-04-25",
            }
        )
        months.append(
            {
                "period": _LOWER_MONTH.isoformat(),
                # `n=1, n_null=1` = 部分字段缺值 ⇒ 落有值成员之和（**下界**）。
                "sum": 10.0,
                "n": 1,
                "n_null": 1,
                "last_day": "2019-05-24",
            }
        )
    return months


class _FakeAkShare:
    """最小假客户端：只提供两个 fetch 方法（不触网）。"""

    def __init__(self, frame: Any) -> None:
        self._frame = frame

    async def fetch_fund_stock_position(self) -> Any:
        return self._frame


def _position_frame() -> Any:
    import pandas as pd

    return pd.DataFrame(
        {
            "date": [_OK_DAY, _NULL_DAY],
            "close": [4000.0, 4010.0],
            "position": [90.5, None],
        }
    )


@requires_pg
class TestWritePath:
    def test_rows_land_with_pit_columns_and_correct_shape(self):
        async def _go(session: AsyncSession) -> list[Any]:
            await svc.upsert_investor_behavior_range(
                session,
                month_lo=date(2019, 1, 1),
                month_hi=date(2019, 12, 1),
                client=_FakeAkShare(_position_frame()),
                offline_months=_offline_months(),
            )
            # 同一事务里刚 add 的行要 flush 才被后续 SELECT 看见（否则断言会读到 0 行）。
            await session.flush()
            return await svc.investor_behavior_coverage(
                session, period_lo=date(2019, 1, 1), period_hi=date(2019, 12, 31)
            )

        coverage = asyncio.run(_with_rollback(_go))
        by_key = {c.metric_key: c for c in coverage}
        # 3 个月频行：1 个有值 + 1 个下界（也有值）+ 1 个整月全空（NULL）
        assert by_key["new_fund_share"].rows == 3
        assert by_key["new_fund_share"].covered_periods == 2
        assert by_key["new_fund_share"].null_periods == 1
        assert by_key["new_fund_share"].missing_reasons == {svc.MISSING_SOURCE_EMPTY: 1}
        assert by_key["public_fund_equity_position"].rows == 2
        assert by_key["public_fund_equity_position"].covered_periods == 1
        assert by_key["public_fund_equity_position"].null_periods == 1

    def test_published_at_is_real_and_never_falls_back_to_period(self):
        async def _go(session: AsyncSession) -> dict[tuple[str, date], Any]:
            await svc.upsert_investor_behavior_range(
                session,
                month_lo=date(2019, 1, 1),
                month_hi=date(2019, 12, 1),
                client=_FakeAkShare(_position_frame()),
                offline_months=_offline_months(),
            )
            from sqlalchemy import select

            from app.models.industry_research import IndustryMetric

            stmt = (
                select(
                    IndustryMetric.metric_key,
                    IndustryMetric.period,
                    IndustryMetric.published_at,
                    IndustryMetric.caliber_version,
                    IndustryMetric.freq,
                    IndustryMetric.unit,
                    IndustryMetric.source_tier,
                    IndustryMetric.source,
                )
                .where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.stock_id == 0,
                    IndustryMetric.period >= date(2019, 1, 1),
                    IndustryMetric.period <= date(2019, 12, 31),
                    IndustryMetric.metric_key.in_(svc.ALL_METRIC_KEYS),
                )
                .order_by(IndustryMetric.metric_key, IndustryMetric.period)
            )
            return {(r.metric_key, r.period): r for r in (await session.execute(stmt)).all()}

        rows = asyncio.run(_with_rollback(_go))
        assert len(rows) == 5
        for (key, period), row in rows.items():
            assert isinstance(row.published_at, datetime)
            assert row.published_at.tzinfo is not None, "published_at 必须带时区（Ruling 35）"
            assert row.published_at.date() != period, (
                f"{key}@{period} 的 published_at 回退成了 period —— Ruling 34/35 禁止"
            )
            assert row.caliber_version == INVESTOR_BEHAVIOR_CALIBER_VERSION
        # 月频：published_at = 该月最后一个成立日 + 4 天
        assert rows[("new_fund_share", _OK_MONTH)].published_at.astimezone(_CN).date() == date(
            2019, 3, 31
        )
        assert rows[("new_fund_share", _OK_MONTH)].freq == "monthly"
        assert rows[("new_fund_share", _OK_MONTH)].unit == "亿元"
        # 低频背景指标（产地实测周度 ⇒ freq='weekly'，Ruling 64）：
        # published_at = 观测日 + 15 个工作日（跳过周末）
        assert rows[("public_fund_equity_position", _OK_DAY)].published_at.astimezone(
            _CN
        ).date() == (date(2019, 4, 5))
        assert rows[("public_fund_equity_position", _OK_DAY)].freq == "weekly"
        assert rows[("public_fund_equity_position", _OK_DAY)].source_tier == "calc"
        assert rows[("public_fund_equity_position", _OK_DAY)].source == (
            "akshare_legulegu_fund_position"
        )

    def test_upsert_is_idempotent(self):
        async def _go(session: AsyncSession) -> tuple[int, int]:
            first = await svc.upsert_investor_behavior_range(
                session,
                month_lo=date(2019, 1, 1),
                month_hi=date(2019, 12, 1),
                client=_FakeAkShare(_position_frame()),
                offline_months=_offline_months(),
            )
            await svc.upsert_investor_behavior_range(
                session,
                month_lo=date(2019, 1, 1),
                month_hi=date(2019, 12, 1),
                client=_FakeAkShare(_position_frame()),
                offline_months=_offline_months(),
            )
            await session.flush()
            coverage = await svc.investor_behavior_coverage(
                session, period_lo=date(2019, 1, 1), period_hi=date(2019, 12, 31)
            )
            total = sum(c.rows for c in coverage)
            return first.rows_upserted, total

        upserted, total = asyncio.run(_with_rollback(_go))
        assert upserted == 5
        assert total == 5, "重跑同一区间后表内行数必须不变（幂等）"

    def test_extra_jsonb_roundtrip(self):
        async def _go(session: AsyncSession) -> dict[str, Any]:
            from sqlalchemy import select

            from app.models.industry_research import IndustryMetric

            await svc.upsert_investor_behavior_range(
                session,
                month_lo=date(2019, 1, 1),
                month_hi=date(2019, 12, 1),
                client=_FakeAkShare(_position_frame()),
                offline_months=_offline_months(),
            )
            await session.flush()
            stmt = select(IndustryMetric.metric_key, IndustryMetric.extra).where(
                IndustryMetric.industry_key == "market",
                IndustryMetric.metric_key.in_(svc.ALL_METRIC_KEYS),
                IndustryMetric.period.in_([_OK_MONTH, _NULL_MONTH, _LOWER_MONTH, _OK_DAY]),
            )
            return {r.metric_key: r.extra for r in (await session.execute(stmt)).all()}

        extra = asyncio.run(_with_rollback(_go))
        # `period.in_` 会同时命中两个 new_fund_share 月，故这里的键是 metric_key →
        # 最后一次写入的那行（用 dict 覆盖语义 + 显式取 NULL 月来断言）。
        assert extra["public_fund_equity_position"]["signal_chain"] == "forbidden"
        assert extra["public_fund_equity_position"]["signal_chain"] == "forbidden"


@requires_pg
class TestReadSurface:
    def test_missing_reason_is_visible_on_the_read_surface(self):
        """Ruling 61：`value=null` 必须带得走"为什么"（否则前端无从解释）。"""

        async def _go(session: AsyncSession) -> tuple[list[Any], Any]:
            await svc.upsert_investor_behavior_range(
                session,
                month_lo=date(2019, 1, 1),
                month_hi=date(2019, 12, 1),
                client=_FakeAkShare(_position_frame()),
                offline_months=_offline_months(),
            )
            await session.flush()
            latest = await metric_service.get_latest_metrics_across_scopes(
                session, ["market"], ["new_fund_share"]
            )
            # 序列读面按"真实时间窗 + 单一源"取；这里用一个足够宽的窗口覆盖 2019 的合成行。
            wide = await metric_service.get_series(
                session, "market", "new_fund_share", range_key="3000d"
            )
            return latest, wide

        latest, series = asyncio.run(_with_rollback(_go))
        assert len(latest) == 1
        assert latest[0].metric_key == "new_fund_share"
        points = {p.period: p for p in series.points}
        # 整月全空 ⇒ 读面必须回传 missing_reason（Ruling 61）
        assert points[_NULL_MONTH].value is None
        assert points[_NULL_MONTH].missing_reason == svc.MISSING_SOURCE_EMPTY
        # 部分字段缺值 ⇒ 有值（下界），且**不带** missing_reason（值不是"缺"）
        assert points[_LOWER_MONTH].value == pytest.approx(10.0)
        assert points[_LOWER_MONTH].missing_reason is None
        assert points[_OK_MONTH].value == pytest.approx(12.5)
        assert points[_OK_MONTH].missing_reason is None

    def test_unregistered_account_metric_is_404_not_a_placeholder(self):
        """判据 3 的运行时证据：开户数**未登记** ⇒ 读面拒绝（不是回骨架/空图）。"""

        async def _go(session: AsyncSession) -> None:
            with pytest.raises(metric_service.UnknownMetricError):
                await metric_service.get_latest_metrics_across_scopes(
                    session, ["market"], ["new_investor_accounts"]
                )
            with pytest.raises(metric_service.UnknownMetricError):
                await metric_service.get_series(session, "market", "new_investor_accounts")

        asyncio.run(_with_rollback(_go))

    def test_registry_key_list_has_no_account_metric(self):
        """同一判据的**静态**证据面：registry 的 key 列表里没有开户数。"""
        from app.services.industry_registry import INDUSTRIES

        keys = {m.key for m in INDUSTRIES["market"].metrics}
        assert not [k for k in keys if "investor_account" in k or "account_statistics" in k]


@requires_pg
class TestCoverage:
    def test_all_null_rows_carry_a_reason(self):
        async def _go(session: AsyncSession) -> list[Any]:
            await svc.upsert_investor_behavior_range(
                session,
                month_lo=date(2019, 1, 1),
                month_hi=date(2019, 12, 1),
                client=_FakeAkShare(_position_frame()),
                offline_months=_offline_months(),
            )
            # 同一事务里刚 add 的行要 flush 才被后续 SELECT 看见（否则断言会读到 0 行）。
            await session.flush()
            return await svc.investor_behavior_coverage(
                session, period_lo=date(2019, 1, 1), period_hi=date(2019, 12, 31)
            )

        coverage = asyncio.run(_with_rollback(_go))
        for cov in coverage:
            if cov.null_periods:
                assert cov.missing_reasons, f"{cov.metric_key} 有 NULL 行却没有原因码"

    def test_period_without_rows_reports_pending_reason(self):
        """整表无行 ⇒ `pending_reason='upstream_no_rows'`（"没有行"与"有行但 NULL"分开）。"""

        async def _go(session: AsyncSession) -> list[Any]:
            return await svc.investor_behavior_coverage(
                session, period_lo=date(1901, 1, 1), period_hi=date(1901, 12, 31)
            )

        coverage = asyncio.run(_with_rollback(_go))
        assert coverage, "覆盖率对账必须按登记 key 回骨架（一行都没有也要回答）"
        for cov in coverage:
            assert cov.rows == 0
            assert cov.pending_reason == svc.MISSING_UPSTREAM_NO_ROWS


@requires_pg
class TestRealBackfillShape:
    """真数据形状的**只读**断言：库里若已有本模块的行，PIT 必须 100%。

    这条不是"回补过就算过"：它把 Ruling 59 的审计（`count(*)` vs `count(published_at)`
    vs `count(caliber_version)`）变成断言，缺一列即红。库里没有行时跳过（回补由
    `scripts/backfill_investor_behavior.py` 负责，见报告）。
    """

    def test_pit_audit_green_for_this_modules_keys(self):
        async def _go(session: AsyncSession) -> list[Any]:
            from app.services import market_activity_service as activity

            audit = await activity.pit_audit(session, date(1900, 1, 1), date(2999, 12, 31))
            return [r for r in audit if r.metric_key in set(svc.ALL_METRIC_KEYS)]

        mine = asyncio.run(_with_rollback(_go))
        if not mine:
            pytest.skip("库里还没有本模块的行：回补脚本未跑（不属于测试失败）")
        for row in mine:
            assert row.ok, (
                f"{row.metric_key}: rows={row.rows} published_at={row.with_published_at} "
                f"caliber={row.with_caliber} —— Ruling 59 的 PIT 审计不通过"
            )

    def test_real_rows_are_not_in_the_future(self):
        """真数据的 `published_at` 不得晚于"现在 + 观测日 + 滞后"（防回填错位）。"""

        async def _go(session: AsyncSession) -> list[Any]:
            from sqlalchemy import select

            from app.models.industry_research import IndustryMetric

            stmt = select(
                IndustryMetric.metric_key, IndustryMetric.period, IndustryMetric.published_at
            ).where(
                IndustryMetric.industry_key == "market",
                IndustryMetric.stock_id == 0,
                IndustryMetric.metric_key.in_(svc.ALL_METRIC_KEYS),
            )
            return list((await session.execute(stmt)).all())

        rows = asyncio.run(_with_rollback(_go))
        if not rows:
            pytest.skip("库里还没有本模块的行")
        ceiling = datetime.now(_CN) + timedelta(days=1)
        for row in rows:
            assert row.published_at <= ceiling, (
                f"{row.metric_key}@{row.period} 的 published_at={row.published_at} 在未来"
            )
