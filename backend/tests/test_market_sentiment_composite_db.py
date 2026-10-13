"""spec §S1.13 —— **真 PG** 用例（只用 `TEST_DATABASE_URL`；不联网）.

纯函数/配置不变量在 `tests/test_market_sentiment_composite.py`；本文件锁"只有真库才能证"
的五段：

**A. 写入路径**：`industry_key='market'` / `stock_id=0` / `metric_key` 精确 /
`freq='daily'`；`published_at` **非空、带时区、且 == 当天全部成分行的最大值**
（前视守卫）；`caliber_version` 非空且 == `s1.13-v1`；幂等（重跑新增行数 = 0）。

**B. 判据 2（可复算性，硬性）**：取库里某一天，**从成分行 + 公开方法**（本文件里
独立实现的 nearest-rank 分位 + 硬编码的 v1 权重表 + 归一化公式）重算 composite，
断言与库内值一致。独立实现**不 import** 服务的计算函数、**不读** `extra` 里的分位，
只用成分行（`SELECT` 原始值）——否则就是"重读库内值"。

**C. 缺失处置落到行上**：某天缺一条成分 ⇒ `extra.participants` 少一条、
`extra.effective_weights` 重新归一化（和为 1）、该成分 `reason='no_row_today'`；
只有 1 条腿参与的日 ⇒ `value IS NULL` + `extra.missing_reason`
（**缺数不静默为 0**）。

**D. 口径断路**：成分行带旧 `caliber_version` ⇒ 该行**不进**分位序列（口径不混算），
并计入 `rows_dropped_other_caliber`。

**E. 读面**（Ruling 61）：`GET /api/v1/metrics/*` 的服务层必须回传
`missing_reason` / `coverage` / `caliber_version` / `as_of_source='published_at'`
—— 前端不得只看到 `value=null`。

**不污染库**：所有写入都包在 `rollback` 的事务里（Ruling 17 的做法）。
**测试隔离**：库地址**只**取 `TEST_DATABASE_URL`，**不写死任何库名**；未设置时全文件 skip。
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import industry_metric_service as metric_service
from app.services import market_sentiment_composite_service as svc
from app.services.industry_registry import (
    COMPOSITE_CALIBER_VERSION,
    COMPOSITE_METRIC_KEY,
    COMPOSITE_PERCENTILE_MIN_SAMPLES,
    COMPOSITE_PERCENTILE_WINDOW,
)

#: **只**认 `TEST_DATABASE_URL`。没有就 skip —— **绝不**回落到写死的 dev 库名。
ASYNC_URL = os.environ.get("TEST_DATABASE_URL", "")
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")
CN = ZoneInfo("Asia/Shanghai")

#: 合成日的合成窗口（远离真实数据；测试库里 `industry_metrics` 为空，写入全部回滚）。
DAY0 = date(2019, 1, 1)
N_DAYS = 300
DAYS = [DAY0 + timedelta(days=i) for i in range(N_DAYS)]

#: 参与合成的 4 条腿（都是真实注册的 key；权重取自公开 v1 表）。
LIVE_KEYS = ("if_basis", "ic_basis", "im_basis", "qvix_50etf")
#: 公开 v1 权重表（本文件**独立**持有：既用于手算，也用来核对 registry 没被偷改）。
PUBLISHED_WEIGHTS: dict[str, float] = {
    "qvix_50etf": 0.12,
    "if_basis": 0.09,
    "ic_basis": 0.07,
    "im_basis": 0.07,
}
PUBLISHED_DIRECTION: dict[str, int] = {
    "qvix_50etf": -1,
    "if_basis": 1,
    "ic_basis": 1,
    "im_basis": 1,
}


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


# ── 合成成分行：4 条真实 key，300 个合成日 ────────────────────────────


def _constituent_rows() -> dict[str, list[svc.ConstituentObservation]]:
    """4 条腿的合成观测（值有确定性形状，便于独立重算；PIT 各自不同档）.

    `if_basis` 15:00 / `ic_basis` 16:00 / `im_basis` 16:00 / `qvix_50etf` 15:00；
    另有一条 19:00 的"最晚腿"只在最后一天以 NULL 出现（见 `_late_null_leg`），
    用来验证 `published_at` 取的是**当天全部成分**的最大值（含不参与的成分）。
    """
    at = {"if_basis": (15, 0), "ic_basis": (16, 0), "im_basis": (16, 0), "qvix_50etf": (15, 0)}
    out: dict[str, list[svc.ConstituentObservation]] = {}
    for key in LIVE_KEYS:
        hh, mm = at[key]
        stamp = time(hour=hh, minute=mm)
        obs: list[svc.ConstituentObservation] = []
        for i, day in enumerate(DAYS):
            if key == "im_basis" and i == N_DAYS - 1:
                # 最后一天：im_basis **没有行** ⇒ 缺一条腿（测试归一化）
                continue
            base = {"if_basis": 1.0, "ic_basis": 2.0, "im_basis": 3.0, "qvix_50etf": 20.0}[key]
            value = base + ((i * 37) % 101) / 10.0
            obs.append(
                svc.ConstituentObservation(
                    period=day, value=value, published_at=datetime.combine(day, stamp, tzinfo=CN)
                )
            )
        out[key] = obs
    # 19:00 才可得、且**值为 NULL** 的第 5 条腿：它不参与合成，但把当天的最晚可得时刻
    # 推到 19:00 —— 合成值在 19:00 之前不可得（前视守卫的直接证据）。
    out["margin_financing_balance"] = [
        svc.ConstituentObservation(
            period=DAYS[-1],
            value=None,
            published_at=datetime.combine(DAYS[-1], time(hour=19), tzinfo=CN),
        )
    ]
    return out


async def _seed_constituent_rows(db: AsyncSession) -> int:
    """把合成成分**真写进** `industry_metrics`（读面/重算都从库里的行来）。"""
    observations = _constituent_rows()
    rows: list[dict[str, Any]] = []
    for key, obs_list in observations.items():
        for obs in obs_list:
            rows.append(
                {
                    "industry_key": "market",
                    "stock_id": 0,
                    "metric_key": key,
                    "source": "test_seed",
                    "source_tier": "derived",
                    "freq": "daily",
                    "period": obs.period,
                    "value": obs.value,
                    "unit": "",
                    "extra": None,
                    "published_at": obs.published_at,
                    "caliber_version": svc.declared_caliber(key) or "test-v1",
                }
            )
    return await svc.upsert_metric_rows(db, rows)


async def _write_composite(db: AsyncSession) -> svc.BackfillStats:
    """写合成指数（**显式传观测**：于是测试与测试库里的其它行完全隔离）."""
    return await svc.upsert_composite_range(db, observations=_constituent_rows())


async def _stored(db: AsyncSession, day: date) -> Any:
    return (
        await db.execute(
            text(
                "SELECT period, value, published_at, caliber_version, extra "
                "FROM industry_metrics WHERE industry_key='market' AND stock_id=0 "
                "AND metric_key=:k AND period=:d"
            ),
            {"k": COMPOSITE_METRIC_KEY, "d": day},
        )
    ).one()


# ── 独立重算（判据 2 的核心：不复用服务的计算函数、不读 extra 的分位）──────


def _independent_percentile(values: list[float | None], idx: int) -> float | None:
    """**本文件自己的** nearest-rank 分位实现（窗口 252 行、最小有效样本 126）.

    刻意不调用 `indicators.percentile_rank`：判据 2 要的是"按公开方法重算"，
    复用被测实现的同一个函数等于把"实现有 bug"与"文档写错"一起放过。
    """
    cur = values[idx]
    if cur is None:
        return None
    lo = max(0, idx - COMPOSITE_PERCENTILE_WINDOW + 1)
    window = [v for v in values[lo : idx + 1] if v is not None]
    if len(window) < COMPOSITE_PERCENTILE_MIN_SAMPLES:
        return None
    return sum(1 for v in window if v <= cur) / len(window)


async def _recompute_from_db(db: AsyncSession, day: date) -> dict[str, Any]:
    """从**库里的成分行**重算某一日的 composite（原始 SQL，独立于服务实现）."""
    rows = (
        await db.execute(
            text(
                "SELECT metric_key, period, value, published_at FROM industry_metrics "
                "WHERE industry_key='market' AND stock_id=0 AND metric_key = ANY(:keys) "
                "ORDER BY metric_key, period"
            ),
            {"keys": list(PUBLISHED_WEIGHTS)},
        )
    ).all()
    series: dict[str, list[float | None]] = {k: [] for k in PUBLISHED_WEIGHTS}
    index_of: dict[str, dict[date, int]] = {k: {} for k in PUBLISHED_WEIGHTS}
    for row in rows:
        key = row.metric_key
        if key not in series:
            continue
        index_of[key][row.period] = len(series[key])
        series[key].append(
            None if row.value is None else float(row.value) * PUBLISHED_DIRECTION[key]
        )

    pct: dict[str, float] = {}
    for key, idx in ((k, index_of[k].get(day)) for k in PUBLISHED_WEIGHTS):
        if idx is None:
            continue
        value = _independent_percentile(series[key], idx)
        if value is not None:
            pct[key] = value

    participants = sorted(pct)
    weight_sum = sum(PUBLISHED_WEIGHTS[k] for k in participants)
    effective = {k: PUBLISHED_WEIGHTS[k] / weight_sum for k in participants}
    value = round(100 * sum(effective[k] * pct[k] for k in participants), 4)
    return {
        "participants": participants,
        "percentiles": pct,
        "effective_weights": effective,
        "effective_weight_sum": weight_sum,
        "value": value,
    }


# ── A/B：写入路径 + 可复算性 ─────────────────────────────────────────


@requires_pg
def test_stored_row_shape_pit_and_recomputability() -> None:
    """判据 1（数据契约）+ 判据 2（**可复算性**）在真库上一次跑完."""

    async def _go(db: AsyncSession) -> dict[str, Any]:
        await _seed_constituent_rows(db)
        stats = await _write_composite(db)
        last = DAYS[-1]
        row = await _stored(db, last)
        recomputed = await _recompute_from_db(db, last)
        # 见下：断言的容差写在测试里（Numeric(18,4) 的量化误差 ≤ 5e-5）
        return {"stats": stats, "row": row, "recomputed": recomputed, "day": last}

    out = asyncio.run(_with_rollback(_go))
    row, recomputed, day = out["row"], out["recomputed"], out["day"]
    stats: svc.BackfillStats = out["stats"]
    # 判据 2 的取证行（`pytest -rP` 会打印它）：挑的那一日、成分分位、重算值与库内值
    print(
        f"\n[可复算性] day={day} participants={recomputed['participants']} "
        f"sum_configured_w={round(recomputed['effective_weight_sum'], 6)} "
        f"recomputed={recomputed['value']} stored={float(row.value)} "
        f"equal={round(recomputed['value'], 4) == round(float(row.value), 4)}"
    )
    print(
        "[可复算性] 逐成分 "
        + ", ".join(
            f"{k}: p={recomputed['percentiles'][k]:.6f} w'={recomputed['effective_weights'][k]:.6f}"
            for k in recomputed["participants"]
        )
    )

    # ── A. 行形状 ──
    assert row.caliber_version == COMPOSITE_CALIBER_VERSION == "s1.13-v1"
    assert row.published_at is not None and row.published_at.tzinfo is not None
    assert row.published_at.date() == day, "合成值恒为当日可得（published_at::date == period）"
    assert row.published_at == datetime.combine(day, time(hour=19), tzinfo=CN), (
        "published_at 必须取当天**全部**成分行的最大值（含不参与者）"
    )
    assert stats.rows_upserted >= N_DAYS
    assert stats.value_days == N_DAYS - (COMPOSITE_PERCENTILE_MIN_SAMPLES - 1)
    assert stats.missing_reasons == {
        "insufficient_participants": COMPOSITE_PERCENTILE_MIN_SAMPLES - 1
    }

    # ── B. 可复算性（**从成分行重算**，不是重读库内值）──
    assert recomputed["participants"] == ["ic_basis", "if_basis", "qvix_50etf"], (
        "最后一天 im_basis 无行 ⇒ 只有 3 条腿参与"
    )
    assert float(row.value) == recomputed["value"]
    # 容差依据：value 列是 Numeric(18,4)，即"精确加权均值"被量化到 1e-4 网格，
    # 量化误差 ≤ 半个末位 = 5e-5；因此逐位一致 = 四舍五入到 4 位后**完全相等**。
    assert abs(float(row.value) - recomputed["value"]) <= 5e-5
    assert round(float(row.value), 4) == round(recomputed["value"], 4)
    extra = row.extra
    assert sorted(extra["participants"]) == recomputed["participants"]
    assert extra["effective_weights"] == pytest.approx(recomputed["effective_weights"])
    assert extra["effective_weight_sum"] == pytest.approx(recomputed["effective_weight_sum"])
    assert extra["constituents"]["im_basis"]["reason"] == "no_row_today"
    assert extra["constituents"]["im_basis"]["participated"] is False
    assert extra["constituents"]["margin_financing_balance"]["reason"] == "value_missing"
    assert extra["renormalization"] == "drop_and_renormalise"
    assert extra["percentile_window"] == COMPOSITE_PERCENTILE_WINDOW == 252
    assert extra["percentile_min_samples"] == COMPOSITE_PERCENTILE_MIN_SAMPLES == 126
    assert extra["coverage"] == pytest.approx(3 / 11)


@requires_pg
def test_hardcoded_published_table_matches_registry() -> None:
    """本文件独立持有的公开权重表 == registry（防止"改了 registry 却没人更新文档/测试"）."""
    registry_table = {
        c.metric_key: (c.weight, c.direction) for c in svc.CONSTITUENTS if c.metric_key in LIVE_KEYS
    }
    assert registry_table == {k: (PUBLISHED_WEIGHTS[k], PUBLISHED_DIRECTION[k]) for k in LIVE_KEYS}
    assert svc.CALIBER_VERSION == "s1.13-v1"


# ── C. 缺失处置 / 缺数不静默为 0 ─────────────────────────────────────


@requires_pg
def test_null_day_has_missing_reason_and_no_zero() -> None:
    """只有 1 条腿参与的日 ⇒ `value IS NULL` + `extra.missing_reason`（**不是 0**）."""

    async def _go(db: AsyncSession) -> Any:
        await _seed_constituent_rows(db)
        await _write_composite(db)
        first = DAYS[0]  # 第一条腿刚有 1 个样本 ⇒ 谁的分位都不够 126
        return await _stored(db, first)

    row = asyncio.run(_with_rollback(_go))
    assert row.value is None, "分位样本不足 ⇒ 缺数，**不得**落 0"
    assert row.extra["missing_reason"] == "insufficient_participants"
    assert row.extra["n_participants"] == 0
    assert row.extra["effective_weights"] == {}
    assert row.published_at is not None


@requires_pg
def test_recompute_mid_history_day_with_full_participation() -> None:
    """任取**历史中间**某一天也要逐位可复算（不是只有最后一天凑巧对）."""

    async def _go(db: AsyncSession) -> dict[str, Any]:
        await _seed_constituent_rows(db)
        await _write_composite(db)
        day = DAYS[200]
        return {
            "row": await _stored(db, day),
            "recomputed": await _recompute_from_db(db, day),
            "day": day,
        }

    out = asyncio.run(_with_rollback(_go))
    row, recomputed = out["row"], out["recomputed"]
    assert len(recomputed["participants"]) == 4
    assert float(row.value) == recomputed["value"]
    assert row.extra["effective_weights"] == pytest.approx(recomputed["effective_weights"])
    assert sum(row.extra["effective_weights"].values()) == pytest.approx(1.0)


@requires_pg
def test_rerun_is_idempotent_by_unique_key() -> None:
    """同一区间重跑新增行数 = 0（ADR 0014 的 B 类可回补）."""

    async def _go(db: AsyncSession) -> tuple[int, int, int]:
        await _seed_constituent_rows(db)
        await _write_composite(db)
        count_first = (
            await db.execute(
                text(
                    "SELECT count(*) FROM industry_metrics WHERE industry_key='market' "
                    "AND metric_key=:k"
                ),
                {"k": COMPOSITE_METRIC_KEY},
            )
        ).scalar_one()
        second = await _write_composite(db)
        count_second = (
            await db.execute(
                text(
                    "SELECT count(*) FROM industry_metrics WHERE industry_key='market' "
                    "AND metric_key=:k"
                ),
                {"k": COMPOSITE_METRIC_KEY},
            )
        ).scalar_one()
        return count_first, count_second, second.rows_upserted

    first, second, upserted = asyncio.run(_with_rollback(_go))
    assert first == second == N_DAYS
    assert upserted == N_DAYS, "ON CONFLICT DO UPDATE 的 rowcount 仍是 N，但**行数不变**"


# ── D. 口径断路 ──────────────────────────────────────────────────────


@requires_pg
def test_old_caliber_constituent_rows_are_dropped_not_mixed() -> None:
    """成分行带旧口径 ⇒ 不进口径序列（不混算两版口径），并计入 dropped."""

    async def _go(db: AsyncSession) -> dict[str, Any]:
        await _seed_constituent_rows(db)
        # 再插一行 if_basis，日期落在窗口外更早处，但 caliber_version 是旧版
        old_day = DAY0 - timedelta(days=5)
        await svc.upsert_metric_rows(
            db,
            [
                {
                    "industry_key": "market",
                    "stock_id": 0,
                    "metric_key": "if_basis",
                    "source": "test_seed",
                    "source_tier": "derived",
                    "freq": "daily",
                    "period": old_day,
                    "value": 1.0,
                    "unit": "",
                    "extra": None,
                    "published_at": datetime.combine(old_day, time(15, 0), tzinfo=CN),
                    "caliber_version": "s1.10-v0-old",
                }
            ],
        )
        obs, dropped = await svc.load_constituent_observations(db)
        return {"obs": obs, "dropped": dropped, "old_day": old_day}

    out = asyncio.run(_with_rollback(_go))
    periods = {o.period for o in out["obs"]["if_basis"]}
    assert out["old_day"] not in periods, "旧口径的行不得进入分位序列（口径不混算）"
    assert out["dropped"] == {"if_basis": 1}


# ── E. 读面（Ruling 61）─────────────────────────────────────────────


@requires_pg
def test_read_surface_exposes_missing_reason_coverage_and_pit() -> None:
    """`GET /api/v1/metrics/*` 的服务层：NULL 行必须带 `missing_reason`，
    且 `as_of_source` 必须来自 `published_at`（**不回退 `period`**）."""

    async def _go(db: AsyncSession) -> Any:
        await _seed_constituent_rows(db)
        await _write_composite(db)
        history = await metric_service.get_metric_history(
            db, "market", COMPOSITE_METRIC_KEY, limit=500
        )
        latest_rows = await metric_service.get_latest_metrics_across_scopes(
            db, scope_keys=["market"], metric_keys=[COMPOSITE_METRIC_KEY]
        )
        return history, latest_rows[0]

    history, latest = asyncio.run(_with_rollback(_go))
    assert history.metric_key == COMPOSITE_METRIC_KEY
    assert history.unit == "分"
    assert history.freq == "daily"
    by_period = {p.period: p for p in history.points}
    null_point = by_period[DAYS[0]]
    assert null_point.value is None
    assert null_point.missing_reason == "insufficient_participants"
    assert null_point.caliber_version == "s1.13-v1"
    assert null_point.as_of_source == "published_at"
    value_point = by_period[DAYS[-1]]
    assert value_point.value is not None
    assert value_point.coverage == pytest.approx(3 / 11)
    assert value_point.as_of_source == "published_at"
    assert latest.metric_key == COMPOSITE_METRIC_KEY
    assert latest.as_of_source == "published_at"
