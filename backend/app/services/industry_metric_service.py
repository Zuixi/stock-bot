"""Industry research service: ingest, derived metrics, cycle evaluation, dashboard.

读取面统一：所有消费方（看板/图表/规则引擎）都从 industry_metrics 单表取数，
不感知指标来源；派生指标（猪粮比/能繁环比）ingest 后计算并统一落表。
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import asdict
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.redis import CacheClient
from app.models.industry_research import IndustryReferencePoint
from app.repositories import industry_metric_repo as repo
from app.schemas.industry import (
    CycleOut,
    DashboardOut,
    IndustryBriefOut,
    IndustrySummaryOut,
    MetricDelta,
    MetricHistoryOut,
    MetricHistoryPointOut,
    MetricLatestOut,
    PhaseOut,
    PositionSliceOut,
    ReferenceOut,
    SignalOut,
    TrendSeriesOut,
)
from app.services import cycle_engine
from app.services.industry_mock_data import build_pig_mock_points
from app.services.industry_registry import (
    TIER_DERIVED,
    IndustryConfig,
    MetricDef,
    get_industry,
)

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_TTL = 60
SPARK_POINTS = 24


class UnknownIndustryError(ValueError):
    pass


class UnknownMetricError(ValueError):
    pass


def _require_industry(industry_key: str) -> IndustryConfig:
    cfg = get_industry(industry_key)
    if cfg is None:
        raise UnknownIndustryError(f"Industry '{industry_key}' is not configured in registry")
    return cfg


# ── Fetchers ──────────────────────────────────────────────────────────

async def _fetch_akshare_rows(cfg: IndustryConfig, months: int = 37) -> list[dict]:
    """Best-effort real fetch via AKShare（接口名未实机验证，失败即跳过该指标）."""
    from app.core.providers.akshare_client import get_akshare_client

    client = get_akshare_client()
    rows: list[dict] = []
    today = date.today()

    def _row(m: MetricDef, period: date, value: float) -> dict:
        return {
            "industry_key": cfg.key, "stock_id": 0, "metric_key": m.key,
            "source": "akshare_100ppi" if m.key != "lh_future_main" else "akshare_sina",
            "source_tier": m.tier, "freq": "daily", "period": period,
            "value": value, "unit": m.unit or None, "extra": None,
        }

    # 回补窗口：按月数换算行数下限（月均 ~31 天），保底 45 天近端窗口
    tail_rows = max(45, months * 31)

    # TODO(api-verify): 生意社返回列名未确认，做启发式解析，失败跳过。
    for metric_key, symbol_cn in [
        ("hog_price", "生猪"),
        ("corn_price", "玉米"),
        ("soybean_meal_price", "豆粕"),
    ]:
        m = cfg.metric(metric_key)
        if m is None:
            continue
        try:
            df = await client.fetch_spot_price_history(symbol_cn)
            date_col = next(c for c in df.columns if "日期" in str(c) or "date" in str(c).lower())
            val_col = next(c for c in df.columns if c != date_col)
            for _, r in df.tail(tail_rows).iterrows():
                period = date.fromisoformat(str(r[date_col])[:10])
                if period > today:
                    continue
                rows.append(_row(m, period, float(r[val_col])))
        except Exception as exc:
            logger.warning("AKShare %s fetch failed (skipped): %s", metric_key, exc)

    try:
        m = cfg.metric("lh_future_main")
        df = await client.fetch_lh_future_daily()
        if m is not None and not df.empty:
            date_col = next(c for c in df.columns if "date" in str(c).lower() or "日期" in str(c))
            val_col = "close"
            for _, r in df.tail(tail_rows).iterrows():
                period = date.fromisoformat(str(r[date_col])[:10])
                rows.append(_row(m, period, float(r[val_col])))
    except Exception as exc:
        logger.warning("AKShare LH future fetch failed (skipped): %s", exc)

    return rows


# ── Ingest ────────────────────────────────────────────────────────────

async def ingest_industry_metrics(
    db: AsyncSession,
    industry_key: str = "pig",
    source: str | None = None,
    months: int = 37,
) -> dict:
    """Fetch → upsert → derive → signal，一次 ingest 完成整条链（幂等）."""
    cfg = _require_industry(industry_key)
    source = source or settings.industry_data_source

    if source == "mock":
        rows = build_pig_mock_points(cfg.key, months=months)
    elif source == "akshare":
        rows = await _fetch_akshare_rows(cfg, months=months)
    else:
        raise ValueError(f"Unknown industry data source: {source}")

    await _ensure_reference_points(db, cfg)
    upserted = await repo.upsert_metrics(db, rows)
    purged = 0
    # 真实源首次落库后清除演示数据：宁可空缺也不让 mock 冒充真实值
    if source != "mock" and upserted > 0:
        purged = await repo.delete_mock_rows(db, cfg.key)
    derived_count = await _compute_derived_metrics(db, cfg)
    signal = await evaluate_and_store_signal(db, cfg)

    return {
        "source": source,
        "upserted": upserted,
        "derived_upserted": derived_count,
        "purged_mock": purged,
        "signal": signal.signal_type if signal else None,
    }


async def _ensure_reference_points(db: AsyncSession, cfg: IndustryConfig) -> None:
    rows = [
        {
            "industry_key": cfg.key,
            "metric_key": rp.metric_key,
            "label": rp.label,
            "value": rp.value,
            "effective_from": rp.effective_from,
            "note": rp.note,
        }
        for rp in cfg.reference_points
    ]
    await repo.upsert_reference_points(db, rows)


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _rollup_monthly_rows(cfg: IndustryConfig, m: MetricDef, rows: list) -> list[dict]:
    """每日度序列 → 月度行：每月最后一个非空日度值，period=月末，source 原样保留。"""
    by_key: dict[tuple[str, date], object] = {}
    for r in rows:  # rows 为升序
        if r.value is None:
            continue
        by_key[(r.source, _month_end(r.period))] = r
    return [
        {
            "industry_key": cfg.key, "stock_id": 0, "metric_key": m.key,
            "source": source, "source_tier": m.tier, "freq": "monthly",
            "period": period, "value": float(r.value), "unit": m.unit or None,
            "extra": {"rollup": "last_daily"},
        }
        for (source, period), r in sorted(by_key.items())
    ]


async def _compute_derived_metrics(db: AsyncSession, cfg: IndustryConfig) -> int:
    """rollup（日→月）+ 派生（猪粮比/能繁环比）— 统一幂等落表."""
    total = 0

    # 1) 日度→月度 rollup：先落库，后续月度派生当次可见
    rollup_rows: list[dict] = []
    for m in cfg.metrics:
        if not m.rollup_monthly:
            continue
        daily = await repo.get_metric_history(db, cfg.key, m.key, limit=4000, freq="daily")
        rollup_rows.extend(_rollup_monthly_rows(cfg, m, daily))
    if rollup_rows:
        total += await repo.upsert_metrics(db, rollup_rows)

    derived: list[dict] = []

    def _row(m: MetricDef, freq: str, period: date, value: float) -> dict:
        return {
            "industry_key": cfg.key, "stock_id": 0, "metric_key": m.key,
            "source": "derived", "source_tier": TIER_DERIVED, "freq": freq,
            "period": period, "value": round(value, 4), "unit": m.unit or None,
            "extra": None,
        }

    # 猪粮比 = hog_price / corn_price（按 period 对齐，日/月各算一条序列）
    ratio_def = cfg.metric("hog_corn_ratio")
    if ratio_def is not None:
        for freq in ("daily", "monthly"):
            hogs = {
                r.period: float(r.value)
                for r in await repo.get_metric_history(db, cfg.key, "hog_price", limit=400, freq=freq)
                if r.value is not None
            }
            corns = {
                r.period: float(r.value)
                for r in await repo.get_metric_history(db, cfg.key, "corn_price", limit=400, freq=freq)
                if r.value is not None
            }
            for period in sorted(set(hogs) & set(corns)):
                if corns[period]:
                    derived.append(_row(ratio_def, freq, period, hogs[period] / corns[period]))

    # 能繁环比 = (本月 - 上月) / 上月
    mom_def = cfg.metric("sow_inventory_mom")
    if mom_def is not None:
        sow = [
            r for r in await repo.get_metric_history(db, cfg.key, "sow_inventory", limit=240, freq="monthly")
            if r.value is not None
        ]
        for prev, cur in zip(sow, sow[1:], strict=False):
            if prev.value:
                derived.append(
                    _row(mom_def, "monthly", cur.period, (float(cur.value) - float(prev.value)) / float(prev.value) * 100)
                )

    total += await repo.upsert_metrics(db, derived)
    return total


async def evaluate_and_store_signal(db: AsyncSession, cfg: IndustryConfig):
    """Build snapshot from latest rows → rules engine → persist (idempotent per day)."""
    inp = await _build_cycle_input(db, cfg)
    out = cycle_engine.evaluate_pig_cycle(inp)

    return await repo.upsert_signal(db, {
        "industry_key": cfg.key,
        "phase": out.phase,
        "signal_type": out.signal,
        "positions": [asdict(s) for s in out.positions],
        "reason": "；".join(out.reasons),
        "basis": out.basis,
        "effective_date": date.today(),
    })


async def _build_cycle_input(db: AsyncSession, cfg: IndustryConfig) -> cycle_engine.CycleInput:
    """指标快照：latest 行按 registry 源优先级裁决 + 环比/猪粮比序列。"""
    grouped = await repo.latest_rows_by_metric(db, cfg.key)
    ratio_row = _pick_latest(cfg, grouped, "hog_corn_ratio")
    price_row = _pick_latest(cfg, grouped, "hog_price")
    cost_row = _pick_latest(cfg, grouped, "industry_cost_avg")
    sow_mom = [
        float(r.value)
        for r in await repo.get_metric_history(db, cfg.key, "sow_inventory_mom", limit=12, freq="monthly")
        if r.value is not None
    ]
    ratio_series = [
        float(r.value)
        for r in await repo.get_metric_history(db, cfg.key, "hog_corn_ratio", limit=30, freq="daily")
        if r.value is not None
    ]
    return cycle_engine.CycleInput(
        ratio=float(ratio_row.value) if ratio_row and ratio_row.value is not None else None,
        price=float(price_row.value) if price_row and price_row.value is not None else None,
        cost=float(cost_row.value) if cost_row and cost_row.value is not None else None,
        sow_mom_series=sow_mom,
        ratio_series=ratio_series,
    )


# ── Query side ────────────────────────────────────────────────────────

def _pick_latest(cfg: IndustryConfig, grouped: dict, metric_key: str):
    rows = grouped.get(metric_key, [])
    m = cfg.metric(metric_key)
    if m is None or not rows:
        return None
    # 注册频率优先：月度行不得借月末日期压过日度行
    rows = [r for r in rows if r.freq == m.freq] or rows
    by_source = {r.source: r for r in rows}
    for source_key in m.sources:
        if source_key in by_source:
            return by_source[source_key]
    return max(rows, key=lambda r: r.period)


async def _build_metric_latest(
    db: AsyncSession, cfg: IndustryConfig, m: MetricDef, grouped: dict, with_spark: bool
) -> MetricLatestOut:
    row = _pick_latest(cfg, grouped, m.key)
    out = MetricLatestOut(
        metric_key=m.key, name=m.name, unit=m.unit, tier=m.tier,
        description=m.description,
    )
    if row is None:
        return out

    out.value = float(row.value) if row.value is not None else None
    out.source = row.source
    out.freq = row.freq
    out.period = row.period

    if out.value is not None:
        label = {"daily": "日环比", "weekly": "周环比", "monthly": "月环比",
                 "quarterly": "季环比", "yearly": "年同比"}.get(row.freq, "环比")
        out.delta = await _delta_of(db, cfg.key, m, row, label)
        if m.warn_bands:
            band = next((b for b in sorted(m.warn_bands, key=lambda b: (b.upper is None, b.upper or 0))
                         if b.upper is None or out.value <= b.upper), None)
            if band is not None:
                out.warn = band.label
                out.warn_severity = band.severity

    if with_spark and m.spark:
        series = await repo.get_metric_history(
            db, cfg.key, m.key, limit=SPARK_POINTS, freq=row.freq, source=row.source
        )
        out.spark = [float(r.value) for r in series if r.value is not None]
    return out


async def _delta_of(
    db: AsyncSession, industry_key: str, m: MetricDef, row, label: str
) -> MetricDelta | None:
    if row.value is None:
        return None
    series = await repo.get_metric_history(
        db, industry_key, m.key, limit=2, freq=row.freq, source=row.source
    )
    if (
        len(series) < 2
        or series[-1].period != row.period
        or not series[0].value
    ):
        return MetricDelta(pct=None, direction="flat", label=label)
    pct = (float(row.value) - float(series[0].value)) / float(series[0].value) * 100
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    return MetricDelta(pct=round(pct, 2), direction=direction, label=label)


async def get_latest_metrics(
    db: AsyncSession, cache: CacheClient, industry_key: str, group: str | None = None
) -> list[MetricLatestOut]:
    cfg = _require_industry(industry_key)
    grouped = await repo.latest_rows_by_metric(db, cfg.key)
    metrics = cfg.metrics if group is None else [m for m in cfg.metrics if m.group == group]
    return [
        await _build_metric_latest(db, cfg, m, grouped, with_spark=group is None)
        for m in metrics
    ]


async def get_metric_history(
    db: AsyncSession,
    cache: CacheClient,
    industry_key: str,
    metric_key: str,
    months: int = 36,
    freq: str | None = None,
    source: str | None = None,
) -> MetricHistoryOut:
    cfg = _require_industry(industry_key)
    m = cfg.metric(metric_key)
    if m is None:
        raise UnknownMetricError(f"Metric '{metric_key}' is not defined for '{industry_key}'")
    rows = await repo.get_metric_history(
        db, industry_key, metric_key, limit=months, freq=freq or m.freq, source=source
    )
    return MetricHistoryOut(
        metric_key=m.key, name=m.name, unit=m.unit, freq=freq or m.freq, tier=m.tier,
        points=[
            MetricHistoryPointOut(period=r.period, value=float(r.value) if r.value is not None else None,
                                  source=r.source, freq=r.freq)
            for r in rows
        ],
    )


async def list_industries(
    db: AsyncSession, cache: CacheClient
) -> list[IndustrySummaryOut]:
    summaries: list[IndustrySummaryOut] = []
    for cfg in get_all_industries():
        grouped = await repo.latest_rows_by_metric(db, cfg.key)
        coverage = {m.key: bool(grouped.get(m.key)) for m in cfg.metrics}
        periods = [r.period for rows in grouped.values() for r in rows]
        summaries.append(IndustrySummaryOut(
            key=cfg.key, name=cfg.name, description=cfg.description,
            sw_l3_codes=cfg.sw_l3_codes,
            metric_total=len(cfg.metrics),
            metric_with_data=sum(coverage.values()),
            coverage=coverage,
            last_period=max(periods) if periods else None,
        ))
    return summaries


def get_all_industries() -> list[IndustryConfig]:
    from app.services.industry_registry import INDUSTRIES  # noqa: PLC0415

    return list(INDUSTRIES.values())


# ── Dashboard aggregate ───────────────────────────────────────────────

def _signal_out(row) -> SignalOut:
    positions = [
        PositionSliceOut(**p) for p in (row.positions or [])
        if isinstance(p, dict) and "name" in p
    ]
    return SignalOut(
        signal_type=row.signal_type,
        phase=row.phase,
        effective_date=row.effective_date,
        reason=row.reason,
        positions=positions,
    )


async def get_dashboard(
    db: AsyncSession, cache: CacheClient, industry_key: str
) -> DashboardOut:
    cfg = _require_industry(industry_key)
    cache_key = f"industry:{industry_key}:dashboard"
    cached = await cache.get(cache_key)
    if cached is not None:
        return DashboardOut(**cached)

    grouped = await repo.latest_rows_by_metric(db, cfg.key)
    strip = [await _build_metric_latest(db, cfg, m, grouped, with_spark=True) for m in cfg.strip_metrics]
    quick = [await _build_metric_latest(db, cfg, m, grouped, with_spark=False) for m in cfg.quick_metrics]

    trends: dict[str, TrendSeriesOut] = {}
    trends["price_vs_cost"] = await _trend_two_series(
        db, cfg, "hog_price", "生猪均价", "industry_cost_avg", "行业平均完全成本", limit=36
    )
    sow_trend = await _trend_one_series(db, cfg, "sow_inventory", limit=36)
    ref = await _applicable_reference(db, cfg, "sow_inventory")
    sow_trend.reference = ref
    trends["sow_inventory"] = sow_trend

    # 规则引擎结果：evaluate_and_store_signal 已用最新快照评估并落表（幂等），
    # 直接复用存储行构造输出，避免重复查询。
    signal_row = await evaluate_and_store_signal(db, cfg)
    basis = signal_row.basis or {}
    cycle = CycleOut(
        phase=signal_row.phase,
        phase_index=cycle_engine.phase_index(cfg, signal_row.phase),
        phases=[
            PhaseOut(key=p.key, label=p.label, desc=p.desc, active=p.key == signal_row.phase)
            for p in cfg.phases
        ],
        reasons=[s for s in (signal_row.reason or "").split("；") if s],
        basis=basis,
    )
    history_rows = await repo.list_signals(db, cfg.key, limit=10)
    signal_history = [_signal_out(r) for r in history_rows]

    dashboard = DashboardOut(
        industry=IndustryBriefOut(key=cfg.key, name=cfg.name, description=cfg.description,
                                  sw_l3_codes=cfg.sw_l3_codes),
        as_of=date.today(),
        strip=strip,
        quick_view=quick,
        trends=trends,
        cycle=cycle,
        signal=_signal_out(signal_row),
        signal_history=signal_history,
    )
    await cache.set(cache_key, dashboard.model_dump(mode="json"), ttl=DASHBOARD_CACHE_TTL)
    return dashboard


async def _trend_two_series(
    db: AsyncSession, cfg: IndustryConfig,
    key_a: str, label_a: str, key_b: str, label_b: str, limit: int,
) -> TrendSeriesOut:
    rows_a = await repo.get_metric_history(db, cfg.key, key_a, limit=limit, freq="monthly")
    rows_b = await repo.get_metric_history(db, cfg.key, key_b, limit=limit, freq="monthly")
    b_by_period = {r.period: (float(r.value) if r.value is not None else None) for r in rows_b}
    periods = [r.period for r in rows_a]
    return TrendSeriesOut(
        periods=periods,
        series={
            label_a: [float(r.value) if r.value is not None else None for r in rows_a],
            label_b: [b_by_period.get(p) for p in periods],
        },
    )


async def _trend_one_series(
    db: AsyncSession, cfg: IndustryConfig, metric_key: str, limit: int
) -> TrendSeriesOut:
    rows = await repo.get_metric_history(db, cfg.key, metric_key, limit=limit, freq="monthly")
    return TrendSeriesOut(
        periods=[r.period for r in rows],
        series={cfg.metric(metric_key).name if cfg.metric(metric_key) else metric_key:
                [float(r.value) if r.value is not None else None for r in rows]},
    )


async def _applicable_reference(
    db: AsyncSession, cfg: IndustryConfig, metric_key: str
) -> ReferenceOut | None:
    points: list[IndustryReferencePoint] = await repo.list_reference_points(db, cfg.key, metric_key)
    point = repo.applicable_reference(points)
    if point is None:
        return None
    return ReferenceOut(
        label=point.label, value=float(point.value), note=point.note,
        effective_from=point.effective_from,
    )


async def batch_upsert_metrics(
    db: AsyncSession, industry_key: str, items: list[dict], recompute_derived: bool = False
) -> dict:
    """人工/CSV 导入通道：仅接受 registry 内已定义的 metric_key，幂等 upsert。"""
    cfg = _require_industry(industry_key)
    rows: list[dict] = []
    skipped: list[str] = []
    for item in items:
        m = cfg.metric(item["metric_key"])
        if m is None:
            skipped.append(item["metric_key"])
            continue
        rows.append({
            "industry_key": cfg.key,
            "stock_id": item.get("stock_id") or 0,
            "metric_key": m.key,
            "source": item.get("source") or "manual",
            "source_tier": m.tier if item.get("source") in (None, "manual") else m.tier,
            "freq": item.get("freq") or m.freq,
            "period": item["period"],
            "value": item["value"],
            "unit": item.get("unit") or m.unit or None,
            "extra": None,
        })
    upserted = await repo.upsert_metrics(db, rows)
    derived = 0
    if recompute_derived:
        derived = await _compute_derived_metrics(db, cfg)
        await evaluate_and_store_signal(db, cfg)
    return {"upserted": upserted, "derived_upserted": derived,
            "skipped_unknown_metric": sorted(set(skipped))}
