"""spec §S1.10 —— **真 PG** 用例（只用 `TEST_DATABASE_URL`；不联网）。

纯函数不变量在 `tests/test_volatility_derivatives_s110.py`；本文件锁的是"只有真库才能证"
的那几条，分三段：

**A. 写入路径**（离线自造行，走同一套 `_row` + 同一个 PIT 守卫）
1. 落库形状：`industry_key='market'` / `stock_id=0` / `freq='daily'` / `unit` 与
   `source_tier` 正确；`published_at` **非空、带时区**、按产地分两档
   （QVIX 15:00 / 期货结算 16:00）；`caliber_version='s1.10-v1'`。**绝不回退 `period`**。
2. **幂等**：同一批行 upsert 两次新增行数 = 0（ADR 0014 的 B 类可回补）。
3. `extra` 的 JSONB 往返：`missing_reason` / `sign_convention` / `basis_close` /
   `expected_from` 写进去能读回来（"有行 ≠ 有值，必须能区分"靠的就是这几个字段）。

**B. 一条完整的合成序列**（≥3 年、6 个衍生品 key，全部经真实计算入口产出）
4. **spec 判据「≥3 年日频升贴水」**、**`value == extra.fut_settle − extra.spot_close`**、
   **贴水率 == 基差 / 现货 × 100 且与基差同号**、**覆盖率无"连行都没有"的洞**、
   **PIT 三计数相等**（Ruling 59）。

**C. 真实回补数据**（需库里已有行；没有则 skip —— 回填与覆盖率演示用 dev 库 `stock_bot`）
5. 真实行的 PIT 完整性 + 三态覆盖率对照 + 四品种 ≥3 年 + 值↔`extra` 对账。

**不污染库**：所有写入都包在 `rollback` 的事务里（Ruling 17 的做法）。
**测试隔离**：库地址**只**取 `TEST_DATABASE_URL`，**不写死任何库名**；未设置时全文件 skip
（而不是猜一个默认库 —— 那正是本会话踩过的坑：`tests/test_intraday_snapshot_table.py`
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.industry_research import IndustryMetric
from app.services import market_activity_service as market_svc
from app.services import volatility_derivatives_service as svc

_CN = ZoneInfo("Asia/Shanghai")

#: `industry_metrics.value` 是 `Numeric(18, 4)` ⇒ 读回来只有 **4 位小数**。
#: 对账容差必须容纳这一次舍入（它仍是强判据：符号翻转、公式漂移、腿配错都远超 1e-3）。
_VALUE_ABS = 1e-3

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


async def _existing_rows(session: AsyncSession) -> list[IndustryMetric]:
    """库里**已有**的本模块行（只读、不联网）—— C 段用例的输入。

    约定：**回填与覆盖率演示用 dev 库 `stock_bot`**，故 `stock_bot_test` 没有这些行时
    对应用例 **skip 而不是红**（与 `test_market_activity_leverage_db.py` 同规矩）。
    """
    return list(
        (
            await session.execute(
                select(IndustryMetric).where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.stock_id == 0,
                    IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                )
            )
        ).scalars()
    )


def _synthetic_rows() -> list[dict[str, Any]]:
    """一批**离线可造**的行（走同一套 `_row` 构造器 + 同一个 PIT 守卫）。

    为什么不用 `upsert_metric_range`（真联网）：本文件的目的不是再验一遍上游，而是验证
    "算出来的东西**进得了这两列、出得来**"。联网取证在 Step-0 探针与回补脚本里做。
    """
    qvix = svc.MetricValue(
        "qvix_50etf",
        13.84,
        None,
        {"series": "50etf", "caliber": "close", "expected_from": "2015-02-09"},
    )
    qvix_null = svc.MetricValue(
        "qvix_300etf",
        None,
        svc.MISSING_SOURCE_NOT_UPDATED,
        {"series": "300etf", "expected_from": "2019-12-23"},
    )
    basis = svc.MetricValue(
        "if_basis",
        -62.1798,
        None,
        {
            "product": "IF",
            "contract": "IF2612.CFX",
            "fut_settle": 4225.8,
            "fut_close": 4233.6,
            "spot_close": 4287.9798,
            "basis_close": -54.3798,
            "sign_convention": "futures_minus_spot",
            "expected_from": "2010-04-16",
        },
    )
    discount = svc.MetricValue(
        "ic_discount_rate",
        -1.6977,
        None,
        {
            "product": "IC",
            "basis": -120.4205,
            "spot_close": 7093.0205,
            "basis_metric_key": "ic_basis",
            "sign_convention": "futures_minus_spot",
            "expected_from": "2015-04-16",
        },
    )
    day = date(2026, 10, 12)
    return [svc._row(day, m) for m in (qvix, qvix_null, basis, discount)]


#: 合成序列的现货基线（元/点）与"期货贴水 60 点"——用来造一条 6 个 key 的完整序列。
_SEED_SPOT = {"IF": 4287.98, "IC": 7093.02, "IH": 2819.41, "IM": 7021.47}
_SEED_BASIS = -60.0


def _seed_series_rows() -> tuple[list[dict[str, Any]], list[date]]:
    """经**真实计算入口**造一条 ≥3 年的 6-key 序列（每工作日一行，全部为贴水）。

    用 `basis_metrics_for_day` 造行（而不是手搓 `extra`）的理由：本段要证的是
    "计算入口 → 落库 → 读回 → 对账"这条链自洽，手搓 `extra` 会把被测对象换成测试自己。
    """
    day = date(2022, 7, 22)  # IM 首个挂牌日（四品种里最晚 ⇒ 共同起点）
    end = date(2026, 10, 12)
    days: list[date] = []
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    rows: list[dict[str, Any]] = []
    for d in days:
        legs = {
            product: svc.FuturesLeg(
                product=product,
                contract=f"{product}2612.CFX",
                fut_settle=spot + _SEED_BASIS,
                fut_close=spot + _SEED_BASIS + 1.0,
                spot_close=spot,
                expected_from=date(2022, 7, 22),
                delist_date=d + timedelta(days=60),
                multiplier=300.0,
            )
            for product, spot in _SEED_SPOT.items()
        }
        for metric in svc.basis_metrics_for_day(d, legs=legs).values():
            rows.append(svc._row(d, metric))
    return rows, days


# ── A. 写入路径 / 幂等 / PIT 真落列 ─────────────────────────────────────


@requires_pg
async def test_upsert_lands_rows_with_pit_and_caliber() -> None:
    async def _go(session: AsyncSession) -> dict[str, IndustryMetric]:
        rows = _synthetic_rows()
        inserted = await svc.upsert_metric_rows(session, rows)
        assert inserted >= len(rows)
        stored = {
            r.metric_key: r
            for r in (
                await session.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.stock_id == 0,
                        IndustryMetric.metric_key.in_([r["metric_key"] for r in rows]),
                        IndustryMetric.period == date(2026, 10, 12),
                    )
                )
            ).scalars()
        }
        assert len(stored) == len(rows)
        for row in rows:
            got = stored[row["metric_key"]]
            assert got.unit == row["unit"]
            assert got.freq == "daily"
            assert got.source_tier == row["source_tier"]
            assert got.caliber_version == "s1.10-v1"
            assert got.published_at is not None
            assert got.published_at.tzinfo is not None  # 带时区（Ruling 35 的反面）
            assert got.published_at == row["published_at"]
        return stored

    stored = await _with_rollback(_go)
    assert set(stored) == {"ic_discount_rate", "if_basis", "qvix_300etf", "qvix_50etf"}
    # 两档时点：QVIX 15:00 / 期货结算 16:00（Asia/Shanghai）
    assert stored["qvix_50etf"].published_at == datetime(2026, 10, 12, 15, 0, tzinfo=_CN)
    assert stored["if_basis"].published_at == datetime(2026, 10, 12, 16, 0, tzinfo=_CN)
    assert stored["qvix_50etf"].published_at != stored["if_basis"].published_at


@requires_pg
async def test_upsert_is_idempotent() -> None:
    """同一区间重跑新增行数 = 0（ADR 0014 的 B 类可回补判据）。"""

    async def _go(session: AsyncSession) -> tuple[int, int]:
        rows = _synthetic_rows()
        before = await _count(session)
        await svc.upsert_metric_rows(session, rows)
        await session.flush()
        mid = await _count(session)
        await svc.upsert_metric_rows(session, [dict(r) for r in rows])
        await session.flush()
        after = await _count(session)
        return mid - before, after - mid

    first, second = await _with_rollback(_go)
    assert first == 4  # 4 条新行
    assert second == 0  # 重跑不新增（幂等）


@requires_pg
async def test_extra_jsonb_round_trips_missing_reason_and_sign_convention() -> None:
    async def _go(session: AsyncSession) -> dict[str, dict[str, Any]]:
        await svc.upsert_metric_rows(session, _synthetic_rows())
        got = {
            r.metric_key: r
            for r in (
                await session.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.metric_key.in_(
                            ["qvix_300etf", "if_basis", "ic_discount_rate"]
                        ),
                        IndustryMetric.period == date(2026, 10, 12),
                    )
                )
            ).scalars()
        }
        return {k: dict(v.extra or {}) for k, v in got.items()}

    extra = await _with_rollback(_go)
    # 有行 ≠ 有值：NULL 值必须带 missing_reason 才解释得清"为什么没有值"
    assert extra["qvix_300etf"]["missing_reason"] == svc.MISSING_SOURCE_NOT_UPDATED
    assert extra["if_basis"]["sign_convention"] == "futures_minus_spot"
    assert extra["if_basis"]["basis_close"] == pytest.approx(-54.3798)
    assert extra["if_basis"]["expected_from"] == "2010-04-16"
    assert extra["ic_discount_rate"]["basis_metric_key"] == "ic_basis"


# ── B. 一条 ≥3 年的合成序列：spec 判据 + 对账 + 覆盖率 + PIT 三计数 ──────


@requires_pg
async def test_seeded_series_satisfies_the_spec_three_year_rule_and_reconciles() -> None:
    """`value` 与 `extra` 里的两条腿**逐行**对得上；贴水率与基差同号；跨度 ≥3 年。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        rows, days = _seed_series_rows()
        await svc.upsert_metric_rows(session, rows)
        await session.flush()
        stored = list(
            (
                await session.execute(
                    select(IndustryMetric).where(
                        IndustryMetric.industry_key == "market",
                        IndustryMetric.stock_id == 0,
                        IndustryMetric.metric_key.in_(svc.INGEST_METRIC_KEYS),
                    )
                )
            ).scalars()
        )
        return {"rows": days, "stored": stored}

    got = await _with_rollback(_go)
    stored = got["stored"]
    assert len(stored) == len(got["rows"]) * 6  # 4 基差 + 2 贴水率
    by_key: dict[str, list[IndustryMetric]] = {}
    for row in stored:
        by_key.setdefault(row.metric_key, []).append(row)
    assert set(by_key) == set(svc.BASIS_KEYS) | set(svc.DISCOUNT_RATE_KEYS)

    for key, rows in by_key.items():
        periods = sorted(r.period for r in rows)
        years = (periods[-1] - periods[0]).days / 365.25
        assert years >= 3.0, f"{key} 只有 {years:.2f} 年 < spec §S1.10 的 3 年"
        assert len(rows) == len(got["rows"]), key
        for r in rows:
            assert r.value is not None  # 本合成序列每天两腿齐全 ⇒ 不得有 NULL
            assert r.caliber_version == "s1.10-v1"
            assert r.published_at is not None and r.published_at.tzinfo is not None
            extra = dict(r.extra or {})
            value = float(r.value)  # Numeric 列读回来是 Decimal
            if key in svc.BASIS_KEYS:
                assert value == pytest.approx(
                    float(extra["fut_settle"]) - float(extra["spot_close"]), abs=_VALUE_ABS
                )
                assert float(extra["basis_close"]) == pytest.approx(
                    float(extra["fut_close"]) - float(extra["spot_close"]), abs=_VALUE_ABS
                )
                assert value < 0  # 合成序列全部贴水 ⇒ 负值
            else:
                expected = float(extra["basis"]) / float(extra["spot_close"]) * 100.0
                assert value == pytest.approx(expected, abs=_VALUE_ABS)
                # 同号（负 = 贴水）—— 翻转符号正是 spec §S1.10 要防的误读
                assert (value < 0) == (float(extra["basis"]) < 0)


@requires_pg
async def test_seeded_series_coverage_and_pit_audit_are_clean() -> None:
    """覆盖率无"连行都没有"的洞；PIT 三计数逐 key 相等（Ruling 59 的机械版）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        rows, days = _seed_series_rows()
        await svc.upsert_metric_rows(session, rows)
        await session.flush()
        coverage = [c.as_dict() for c in await svc.metric_coverage(session)]
        audit = [
            r.as_dict()
            for r in await market_svc.pit_audit(session)
            if r.metric_key in set(svc.INGEST_METRIC_KEYS)
        ]
        return {"days": days, "coverage": coverage, "audit": audit}

    got = await _with_rollback(_go)
    assert got["coverage"], "覆盖率对账没有任何输出"
    # `metric_coverage` 恒返回本模块的 11 个 key（即使库里一行都没有，也给出零行形状）
    assert {c["metric_key"] for c in got["coverage"]} == set(svc.INGEST_METRIC_KEYS)
    seeded = set(svc.BASIS_KEYS) | set(svc.DISCOUNT_RATE_KEYS)
    by_key = {c["metric_key"]: c for c in got["coverage"]}
    for key in svc.QVIX_KEYS:  # 本段没种 QVIX ⇒ 必须是干净的"零行"，而不是半个形状
        assert by_key[key]["rows"] == 0 and by_key[key]["missing_days"] == [], by_key[key]
    for key in seeded:
        cov = by_key[key]
        assert cov["missing_days"] == [], cov  # 上游缺数会以 NULL 行出现，不该是"无行"
        assert cov["covered_days"] == cov["rows"] == len(got["days"]), cov
        assert cov["null_days"] == 0, cov
        assert cov["missing_reasons"] == {}, cov
    assert {a["metric_key"] for a in got["audit"]} == set(svc.BASIS_KEYS) | set(
        svc.DISCOUNT_RATE_KEYS
    )
    for row in got["audit"]:
        assert row["rows"] == row["with_published_at"] == row["with_caliber"], row
        assert row["calibers"] == ["s1.10-v1"], row
        assert row["ok"] is True


# ── C. 真实回补数据（stock_bot_test 没有行时 skip）──────────────────────


@requires_pg
async def test_existing_rows_are_pit_complete_and_span_three_years() -> None:
    """真实数据的验收：PIT 完整性 + 四品种（含贴水率）≥3 年 + 值↔`extra` 对账。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        rows = await _existing_rows(session)
        if not rows:
            pytest.skip("测试库无 §S1.10 行（回填与覆盖率演示用 dev 库 stock_bot）")
        audit = [r.as_dict() for r in await market_svc.pit_audit(session)]
        return {"rows": rows, "audit": audit}

    got = await _with_rollback(_go)
    rows = got["rows"]

    # ① PIT / 口径：逐 key 三计数相等
    audit = [r for r in got["audit"] if r["metric_key"] in set(svc.INGEST_METRIC_KEYS)]
    assert audit
    for row in audit:
        assert row["rows"] == row["with_published_at"] == row["with_caliber"], row
        assert row["calibers"] == ["s1.10-v1"], row

    # ② spec 的「≥3 年日频升贴水」：四品种 + 两条贴水率
    for key in (*svc.BASIS_KEYS, *svc.DISCOUNT_RATE_KEYS):
        periods = sorted(r.period for r in rows if r.metric_key == key)
        assert periods, f"{key} 一行都没有"
        years = (periods[-1] - periods[0]).days / 365.25
        assert years >= 3.0, f"{key} 只有 {years:.2f} 年（{periods[0]}..{periods[-1]}）"

    # ③ 期权波动率覆盖 ≥3 个品种（spec 判据），且每条都带 expected_from
    qvix_keys = {r.metric_key for r in rows if r.metric_key in svc.QVIX_KEYS}
    assert len(qvix_keys) >= 3, qvix_keys
    for r in rows:
        assert (r.extra or {}).get("expected_from"), r.metric_key

    # ④ 值 ↔ extra 对账（符号约定在真实数据上成立）
    checked = 0
    for r in rows:
        if r.value is None:
            continue
        extra = dict(r.extra or {})
        if r.metric_key in svc.BASIS_KEYS:
            assert float(r.value) == pytest.approx(
                float(extra["fut_settle"]) - float(extra["spot_close"]), abs=_VALUE_ABS
            )
        elif r.metric_key in svc.DISCOUNT_RATE_KEYS:
            assert float(r.value) == pytest.approx(
                float(extra["basis"]) / float(extra["spot_close"]) * 100.0, abs=_VALUE_ABS
            )
            assert (float(r.value) < 0) == (float(extra["basis"]) < 0)
        checked += 1
    assert checked > 0


@requires_pg
async def test_existing_coverage_has_no_row_level_holes() -> None:
    """覆盖率对账：`missing_days`（连行都没有的交易日）必须为空；NULL 行必须带原因。"""

    async def _go(session: AsyncSession) -> list[dict[str, Any]]:
        rows = await _existing_rows(session)
        if not rows:
            pytest.skip("测试库无 §S1.10 行（回填与覆盖率演示用 dev 库 stock_bot）")
        return [c.as_dict() for c in await svc.metric_coverage(session)]

    coverage = await _with_rollback(_go)
    assert {c["metric_key"] for c in coverage} == set(svc.INGEST_METRIC_KEYS)
    for cov in coverage:
        assert cov["rows"] > 0, cov
        assert cov["missing_days"] == [], cov
        assert cov["covered_days"] + cov["null_days"] == cov["rows"], cov
        for reason in cov["missing_reasons"]:
            assert reason in {
                svc.MISSING_SOURCE_NOT_UPDATED,
                svc.MISSING_QVIX_BEFORE_LISTING,
                svc.MISSING_SPOT_INDEX,
                svc.MISSING_FUTURES_LEG,
                svc.MISSING_BASIS_SPOT_ZERO,
            }, (cov["metric_key"], reason)
    # 期货腿比 QVIX 的静态 CSV 新（实测 3 个交易日）⇒ QVIX 必有"源侧滞后"的 NULL 行
    qvix_reasons = {
        c["metric_key"]: c["missing_reasons"] for c in coverage if c["metric_key"] in svc.QVIX_KEYS
    }
    assert any("source_not_updated" in r for r in qvix_reasons.values()), qvix_reasons
