"""Industry research service: ingest, derived metrics, cycle evaluation, dashboard.

读取面统一：所有消费方（看板/图表/规则引擎）都从 industry_metrics 单表取数，
不感知指标来源；派生指标（猪粮比/能繁环比）ingest 后计算并统一落表。
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import asdict
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.redis import CacheClient
from app.models.industry_research import IndustryReferencePoint
from app.repositories import daily_basic_repo
from app.repositories import industry_metric_repo as repo
from app.schemas.industry import (
    CompanyColumnOut,
    CompanyRowOut,
    CycleOut,
    DashboardOut,
    IndustryBriefOut,
    IndustryCompaniesOut,
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

if TYPE_CHECKING:
    from app.core.providers.akshare_client import AkShareClient
    from app.core.providers.caaa_client import CaaaClient

# AKShare 真实源表驱动规格：(metric_key, source, client 方法, 日期列, 数值列, 数值上限护栏)。
# 四个接口均于 2026-09-03 实机验证（akshare 1.18.94）：搜猪网 soozhu 现货（元/kg）+
# 新浪 LH0 期货（元/吨）；value_max 用于剔除上游脏数据（现货 0<v<100、期货 0<v<100000）。
_AKSHARE_SPECS: list[tuple[str, str, str, str, str, float]] = [
    ("hog_price", "akshare_soozhu", "fetch_hog_price_trend", "日期", "价格", 100.0),
    ("corn_price", "akshare_soozhu", "fetch_corn_price", "日期", "价格", 100.0),
    ("soybean_meal_price", "akshare_soozhu", "fetch_soybean_meal_price", "日期", "价格", 100.0),
    ("lh_future_main", "akshare_sina", "fetch_lh_future_daily", "date", "close", 100000.0),
]


async def _fetch_akshare_rows(
    cfg: IndustryConfig, months: int = 37, client: AkShareClient | None = None
) -> list[dict]:
    """AKShare 真实拉取（已验证接口）：单指标失败/脏行只跳过该指标，互不牵连.

    ``client`` 可注入假对象供纯单测（不触网）；默认取模块级单例。
    """
    if client is None:
        from app.core.providers.akshare_client import get_akshare_client

        client = get_akshare_client()

    rows: list[dict] = []
    today = date.today()

    # 回补窗口：按月数换算行数下限（月均 ~31 天），保底 45 天近端窗口
    tail_rows = max(45, months * 31)

    for metric_key, source, attr, date_col, val_col, value_max in _AKSHARE_SPECS:
        m = cfg.metric(metric_key)
        if m is None:
            continue
        try:
            df = await getattr(client, attr)()
            for _, r in df.tail(tail_rows).iterrows():
                # soozhu 日期与 LH0 date 均为 ISO 字符串（已验证），截前 10 位防御性解析
                period = date.fromisoformat(str(r[date_col])[:10])
                if period > today:
                    continue
                value = float(r[val_col])
                if not 0 < value < value_max:
                    logger.warning(
                        "AKShare %s out-of-range value %s @ %s (row skipped)",
                        metric_key, value, period,
                    )
                    continue
                rows.append({
                    "industry_key": cfg.key, "stock_id": 0, "metric_key": m.key,
                    "source": source, "source_tier": m.tier, "freq": "daily",
                    "period": period, "value": value, "unit": m.unit or None,
                    "extra": None,
                })
        except Exception as exc:
            logger.warning("AKShare %s fetch failed (skipped): %s", metric_key, exc)

    return rows


async def _fetch_caaa_sow_row(
    cfg: IndustryConfig, client: CaaaClient | None = None
) -> list[dict]:
    """中国畜牧业协会（pig.caaa.cn）能繁母猪存栏 → 单行 metric row.

    协会行业动态栏目月度转载五部委数据，正文正则解析（见 ``caaa_client``）；
    stats_gov CSV 通道优先级仍最高，本源作为自动月度补充。任何失败返回
    空列表（不抛穿），未命中 registry 定义时静默跳过。
    """
    m = cfg.metric("sow_inventory")
    if m is None:
        return []
    if client is None:
        from app.core.providers.caaa_client import get_caaa_client

        client = get_caaa_client()

    try:
        data = await client.fetch_latest_sow_inventory()
    except Exception as exc:  # 双保险：client 自身已兜底，此处防注入实现抛穿
        logger.warning("CAAA sow fetch raised (skipped): %s", exc)
        return []
    if data is None:
        return []

    return [{
        "industry_key": cfg.key, "stock_id": 0, "metric_key": "sow_inventory",
        "source": "caaa", "source_tier": m.tier, "freq": "monthly",
        "period": data["period"], "value": data["inventory_wan_tou"],
        "unit": m.unit or None,
        "extra": {"article_url": data["article_url"], "mom_pct": data.get("mom_pct")},
    }]


# ── Ingest ────────────────────────────────────────────────────────────

# 派生指标 → 其全部基础输入；输入全部被真实源覆盖时，旧 mock 派生行一并清除
_DERIVED_INPUTS: dict[str, set[str]] = {
    "hog_corn_ratio": {"hog_price", "corn_price"},
    "sow_inventory_mom": {"sow_inventory"},
}


def _covered_purge_keys(covered: set[str]) -> set[str]:
    """已覆盖指标 → 需清除 mock/derived 行的指标集合（纯函数，单测锁定）.

    真实源 ingest 只清除**本次已覆盖指标**的演示行（修订 C2 裁定：无法补齐的指标
    继续用 mock，宁可标注演示也不空缺）。当某派生指标的全部输入都被覆盖时（如
    hog_price 与 corn_price 同时真实化 → 猪粮比），连同清除其 derived 旧序列
    （当次重算即真实值）。
    """
    derived = {d for d, inputs in _DERIVED_INPUTS.items() if inputs <= covered}
    return covered | derived


async def ingest_industry_metrics(
    db: AsyncSession,
    industry_key: str = "pig",
    source: str | None = None,
    months: int = 37,
) -> dict:
    """Fetch → upsert → purge（按覆盖指标）→ derive → signal，一次 ingest 完成整条链（幂等）."""
    cfg = _require_industry(industry_key)
    source = source or settings.industry_data_source

    if source == "mock":
        rows = build_pig_mock_points(cfg.key, months=months)
    elif source == "akshare":
        # caaa 行在 upsert 前并入：sow_inventory 进入 covered_metrics → mock purge 覆盖
        rows = await _fetch_akshare_rows(cfg, months=months)
        rows += await _fetch_caaa_sow_row(cfg)
    else:
        raise ValueError(f"Unknown industry data source: {source}")

    await _ensure_reference_points(db, cfg)
    upserted = await repo.upsert_metrics(db, rows)

    # 按覆盖清除：真实源落库成功后，仅清除本次已覆盖指标的 mock 行 + 由 mock 派生的
    # derived 行（派生计算只 upsert 不删除，不清除会让 mock 算出的旧序列继续喂给
    # 周期引擎）；未覆盖指标（能繁/成本/仔猪等）保留 mock 演示数据。
    covered = {r["metric_key"] for r in rows}
    purge_keys = _covered_purge_keys(covered)
    purged = 0
    if source != "mock" and upserted > 0 and purge_keys:
        purged = await repo.delete_rows_by_source(
            db, cfg.key, ["mock", "derived"], metric_keys=sorted(purge_keys)
        )
    derived_count = await _compute_derived_metrics(db, cfg)
    signal = await evaluate_and_store_signal(db, cfg)

    return {
        "source": source,
        "upserted": upserted,
        "derived_upserted": derived_count,
        "purged": purged,  # 已覆盖指标下清除的 mock/derived 行数
        "covered_metrics": sorted(covered),
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


# ── 头均市值派生（纯函数，离线单测锁定） ─────────────────────────────

def _month_key(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def _hogs_by_month(monthly_points: list[tuple[date, float]]) -> dict[tuple[int, int], float]:
    """同月多点取最新一条（人工修正语义），非正值剔除。"""
    picked: dict[tuple[int, int], tuple[date, float]] = {}
    for d, v in monthly_points:
        if v and v > 0:
            cur = picked.get(_month_key(d))
            if cur is None or d > cur[0]:
                picked[_month_key(d)] = (d, v)
    return {k: v for k, (_, v) in picked.items()}


def _annualize_hogs(monthly_points: list[tuple[date, float]]) -> float | None:
    """月度出栏序列 → 年化出栏量（万头）。

    - 不同月份数 ≥12 → 最近 12 个不同月的滚动求和（trailing-12M SUM）；
    - 1-11 个月 → 最新月 × 12 粗年化（落表 extra.annualized=True 标记）；
    - 空序列 / 全为非正值 → None。
    """
    by_month = _hogs_by_month(monthly_points)
    months = sorted(by_month)
    if not months:
        return None
    if len(months) >= 12:
        return float(sum(by_month[k] for k in months[-12:]))
    return float(by_month[months[-1]]) * 12


def _hogs_ttm_ready(monthly_points: list[tuple[date, float]]) -> bool:
    """是否满足 trailing-12M 求和条件（False = 最新月 ×12 粗年化）。"""
    return len(_hogs_by_month(monthly_points)) >= 12


async def _compute_derived_metrics(db: AsyncSession, cfg: IndustryConfig) -> int:
    """rollup（日→月）+ 派生（猪粮比/能繁环比/头均市值）— 统一幂等落表."""
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

    def _row(
        m: MetricDef, freq: str, period: date, value: float,
        stock_id: int = 0, extra: dict | None = None,
    ) -> dict:
        return {
            "industry_key": cfg.key, "stock_id": stock_id, "metric_key": m.key,
            "source": "derived", "source_tier": TIER_DERIVED, "freq": freq,
            "period": period, "value": round(value, 4), "unit": m.unit or None,
            "extra": extra,
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
        sow_rows = [
            r for r in await repo.get_metric_history(db, cfg.key, "sow_inventory", limit=240, freq="monthly")
            if r.value is not None
        ]
        # 同一 period 可能多源共存（真实源 ingest 后又重跑 mock 演示）：按 registry 源
        # 优先级逐期去重，否则环比派生会对同一 period 产出重复行，单批 ON CONFLICT
        # 二次命中同一行直接 CardinalityViolation（2026-09-03 实跑复现）。
        sow_def = cfg.metric("sow_inventory")
        by_period: dict[date, list] = {}
        for r in sow_rows:
            by_period.setdefault(r.period, []).append(r)
        sow = [
            p for p in (_pick_row(sow_def, rows) for rows in by_period.values())
            if p is not None
        ]
        for prev, cur in zip(sow, sow[1:], strict=False):
            if prev.value:
                derived.append(
                    _row(mom_def, "monthly", cur.period, (float(cur.value) - float(prev.value)) / float(prev.value) * 100)
                )

    # 头均市值 = 最新总市值 / 年化出栏（公司级派生，stock_id>0；source_tier 取 registry 的 calc）
    # 单位相消：daily_basic_indicators.total_mv（万元）÷ 年化出栏（万头）= 元/头。
    # 历史分位（percentile over history）暂缓：需 ≥1 年派生行积累后再开放，此处只落当期值。
    mcap_def = cfg.metric("mcap_per_head")
    hogs_def = cfg.metric("company.hogs_sold_monthly")
    if mcap_def is not None and hogs_def is not None:
        hogs_rows = await repo.get_company_metric_history(
            db, cfg.key, hogs_def.key, limit=4000, freq="monthly"
        )
        by_stock: dict[int, list[tuple[date, float]]] = {}
        for r in hogs_rows:
            if r.value is not None:
                by_stock.setdefault(r.stock_id, []).append((r.period, float(r.value)))
        for stock_id, points in by_stock.items():
            annual = _annualize_hogs(points)
            if annual is None or annual <= 0:
                continue
            basic = await daily_basic_repo.get_latest_daily_basic(db, stock_id)
            if basic is None or basic.total_mv is None:
                continue  # 无估值行（新股/未回补）只跳过该公司，不牵连其他
            derived.append({
                "industry_key": cfg.key, "stock_id": stock_id,
                "metric_key": mcap_def.key, "source": "derived",
                "source_tier": mcap_def.tier, "freq": mcap_def.freq,
                "period": date.today(), "value": round(float(basic.total_mv) / annual, 2),
                "unit": mcap_def.unit or None,
                "extra": {
                    "annualized": not _hogs_ttm_ready(points),
                    "annual_hogs_wan": round(annual, 2),
                },
            })

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

def _pick_row(m: MetricDef | None, rows: list):
    """latest 行集合（按 source/freq 分组去重后）→ registry 源优先级裁决，兜底最新 period。"""
    if m is None or not rows:
        return None
    # 注册频率优先：月度行不得借月末日期压过日度行
    rows = [r for r in rows if r.freq == m.freq] or rows
    by_source = {r.source: r for r in rows}
    for source_key in m.sources:
        if source_key in by_source:
            return by_source[source_key]
    return max(rows, key=lambda r: r.period)


def _pick_latest(cfg: IndustryConfig, grouped: dict, metric_key: str):
    return _pick_row(cfg.metric(metric_key), grouped.get(metric_key, []))


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
    db: AsyncSession, industry_key: str, group: str | None = None
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
    industry_key: str,
    metric_key: str,
    limit: int = 500,
    freq: str | None = None,
    source: str | None = None,
) -> MetricHistoryOut:
    cfg = _require_industry(industry_key)
    m = cfg.metric(metric_key)
    if m is None:
        raise UnknownMetricError(f"Metric '{metric_key}' is not defined for '{industry_key}'")
    rows = await repo.get_metric_history(
        db, industry_key, metric_key, limit=limit, freq=freq or m.freq, source=source
    )
    return MetricHistoryOut(
        metric_key=m.key, name=m.name, unit=m.unit, freq=freq or m.freq, tier=m.tier,
        points=[
            MetricHistoryPointOut(period=r.period, value=float(r.value) if r.value is not None else None,
                                  source=r.source, freq=r.freq)
            for r in rows
        ],
    )


# ── 标的分析（P5）：成分股对比 ────────────────────────────────────────

def _company_columns(cfg: IndustryConfig) -> list[CompanyColumnOut]:
    """对比表列定义：固定行情/估值列 + registry company 分组指标列（纯函数，单测锁定）。

    新增公司指标 = registry 加一条 MetricDef，列自动下发，前端零改动。
    """
    fixed = [
        CompanyColumnOut(key="symbol", label="代码", numeric=False),
        CompanyColumnOut(key="name", label="名称", numeric=False),
        CompanyColumnOut(key="latest_price", label="最新价"),
        CompanyColumnOut(key="total_mv_yi", label="总市值(亿)"),
        CompanyColumnOut(key="pe_ttm", label="PE(TTM)"),
        CompanyColumnOut(key="pb", label="PB"),
    ]
    return fixed + [
        CompanyColumnOut(key=m.key, label=m.name, unit=m.unit or None, tier=m.tier)
        for m in cfg.metrics if m.group == "company"
    ]


async def get_industry_companies(
    db: AsyncSession, industry_key: str
) -> IndustryCompaniesOut:
    """成分股对比表：sw_l3_codes 成员 + enriched 行情/估值 + 公司指标 latest。"""
    from app.services import market_service  # noqa: PLC0415 （函数级导入，避免模块环）

    cfg = _require_industry(industry_key)
    symbols = await market_service.list_symbols_by_industry_codes(cfg.sw_l3_codes)
    enriched = await market_service.get_stocks_enriched_by_symbols(db, symbols)

    grouped = await repo.latest_company_rows(db, cfg.key)
    company_defs = [m for m in cfg.metrics if m.group == "company"]

    rows: list[CompanyRowOut] = []
    for e in enriched:
        metrics: dict[str, float | None] = {}
        for m in company_defs:
            row = _pick_row(m, grouped.get((e.id, m.key), []))
            metrics[m.key] = float(row.value) if row is not None and row.value is not None else None
        rows.append(CompanyRowOut(
            symbol=e.symbol, name=e.name,
            latest_price=e.latest_price,
            total_mv_yi=round(e.total_mv / 1e4, 2) if e.total_mv is not None else None,
            pe_ttm=e.pe_ttm, pb=e.pb,
            has_company_data=any(v is not None for v in metrics.values()),
            metrics=metrics,
        ))

    return IndustryCompaniesOut(
        industry=IndustryBriefOut(
            key=cfg.key, name=cfg.name, description=cfg.description,
            sw_l3_codes=cfg.sw_l3_codes,
        ),
        columns=_company_columns(cfg),
        rows=rows,
    )


async def list_industries(db: AsyncSession) -> list[IndustrySummaryOut]:
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

    # 信号在 ingest 时评估落表；GET 只读。空库引导：从未评估过才补算一次。
    signal_row = await repo.latest_signal(db, cfg.key)
    if signal_row is None:
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
        data_source=settings.industry_data_source,
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


# 人工/CSV 导入通道允许写库的 source：人工录入 + 统计局 CSV（data-source.md L2 通道）。
# 采集适配器专属 source（akshare_* 等）与 mock/derived 不得经人工通道伪造。
IMPORT_ALLOWED_SOURCES = {"manual", "stats_gov"}


def _prepare_batch_rows(
    cfg: IndustryConfig, items: list[dict]
) -> tuple[list[dict], list[str], list[str]]:
    """校验并规整导入行：返回 (rows, skipped_unknown_metric, skipped_invalid_source)."""
    rows: list[dict] = []
    skipped: list[str] = []
    rejected: list[str] = []
    for item in items:
        m = cfg.metric(item["metric_key"])
        source = item.get("source") or "manual"
        if m is None:
            skipped.append(item["metric_key"])
            continue
        if source not in IMPORT_ALLOWED_SOURCES:
            rejected.append(f"{m.key}:{source}")
            continue
        rows.append({
            "industry_key": cfg.key,
            "stock_id": item.get("stock_id") or 0,
            "metric_key": m.key,
            "source": source,
            "source_tier": m.tier,
            "freq": item.get("freq") or m.freq,
            "period": item["period"],
            "value": item["value"],
            "unit": item.get("unit") or m.unit or None,
            "extra": None,
        })
    return rows, sorted(set(skipped)), sorted(set(rejected))


async def batch_upsert_metrics(
    db: AsyncSession, industry_key: str, items: list[dict], recompute_derived: bool = False
) -> dict:
    """人工/CSV 导入通道：白名单校验 + 幂等 upsert."""
    cfg = _require_industry(industry_key)
    rows, skipped, rejected = _prepare_batch_rows(cfg, items)
    upserted = await repo.upsert_metrics(db, rows)
    derived = 0
    if recompute_derived:
        derived = await _compute_derived_metrics(db, cfg)
        await evaluate_and_store_signal(db, cfg)
    return {
        "upserted": upserted, "derived_upserted": derived,
        "skipped_unknown_metric": skipped, "skipped_invalid_source": rejected,
    }
