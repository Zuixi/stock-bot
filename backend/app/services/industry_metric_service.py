"""Industry research service: ingest, derived metrics, cycle evaluation, dashboard.

读取面统一：所有消费方（看板/图表/规则引擎）都从 industry_metrics 单表取数，
不感知指标来源；派生指标（猪粮比/能繁环比）ingest 后计算并统一落表。
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.redis import CacheClient
from app.models.industry_research import IndustryMetric, IndustryReferencePoint, IndustrySignal
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
    MetricSeriesOut,
    PhaseOut,
    PositionSliceOut,
    ReferenceOut,
    SignalOut,
    TrendSeriesOut,
)
from app.services import cycle_engine
from app.services.industry_ingest_diff import (
    canonical_content_hash,
    compute_ingest_actions,
    is_due,
    next_due,
)
from app.services.industry_mock_data import build_industry_mock_points
from app.services.industry_registry import (
    TIER_DERIVED,
    IndustryConfig,
    MetricDef,
    find_metric,
    get_industry,
)

logger = logging.getLogger(__name__)

DASHBOARD_CACHE_TTL = 60
SERIES_CACHE_TTL = 60
SPARK_POINTS = 24

# 通用读面 GET /metrics/series 的 range 语法：<n><d|w|m|y> 或 "all"（spec §S1.2）。
# range 是**真实时间窗**（period >= 今天 - n 单位），不是行数；`limit` 只是同窗内的
# 防御性行数上界（防病态数据拉爆）。频率过滤由 registry 的 m.freq 负责。
SERIES_RANGE_UNITS: dict[str, int] = {"d": 1, "w": 7, "m": 31, "y": 366}
SERIES_DEFAULT_RANGE = "1y"
SERIES_MAX_LIMIT = 5000

# 窗口天数硬上限（Ruling 36）。两个理由：
# 1) `date.today() - timedelta(days=days)` 在超大 days 上抛 OverflowError
#    （`100000y` / `999999999999y` 实测可复现），异常穿透路由 → HTTP 500。
# 2) 再大的窗口在数据上等价于"全历史"，没有区分价值 —— 故超限**判非法（422）**，
#    而不是悄悄钳成一个与请求不符的窗口（那会让 `range` 回显说谎，见 F4 教训）。
# 999y ≈ 365,634 天 ≈ 1001 年前，仍在 Python date 范围内（不会下溢）。
SERIES_MAX_DAYS = 999 * SERIES_RANGE_UNITS["y"]

# PIT 实际可得日按中国市场日历判定（timestamptz 的 .date() 是 UTC 日，会早一天 = 偷看未来）
_CN_TZ = ZoneInfo("Asia/Shanghai")

AS_OF_FROM_PUBLISHED = "published_at"
AS_OF_FROM_PERIOD = "period"


class UnknownIndustryError(ValueError):
    pass


class UnknownMetricError(ValueError):
    pass


@dataclass(frozen=True)
class SeriesRange:
    """一个 range 字面量解析结果：真实时间窗下界 + 防御性行数上界。

    ``limit = days + 1``：窗口下界是**含**的（``period >= today - days``），日频指标
    在窗内最多有 ``days + 1`` 个点，上界少 1 会让完整序列被误判 ``truncated``。
    """

    text: str
    days: int | None  # None = "all"（无窗）
    limit: int

    def period_from(self, today: date | None = None) -> date | None:
        if self.days is None:
            return None
        try:
            return (today or date.today()) - timedelta(days=self.days)
        except OverflowError:
            # 双保险：解析层已按 SERIES_MAX_DAYS 拒绝超大天数，这里绝不把
            # OverflowError 漏成 HTTP 500（宁可返回"无下界"也不 500）。
            logger.warning(
                "series range %s underflows date range; treating as unbounded", self.text
            )
            return None


def parse_series_range(range_key: str) -> SeriesRange | None:
    """range 语法 → SeriesRange；非法（含超大天数）返回 None（路由层转 422）。

    ``30d`` → 窗 30 天（canonical ``"30d"``）、``6w`` → 42（``"42d"``）、``3m`` → 93
    （``"93d"``）、``1y`` → 366（``"366d"``）、``all`` → 无窗。单位换算法只在
    `SERIES_RANGE_UNITS` 一处。

    两道钳制：天数超过 ``SERIES_MAX_DAYS`` → **None**（422，绝不 500）；行数上界按
    ``SERIES_MAX_LIMIT`` 钳制。
    """
    key = range_key.strip().lower()
    if key == "all":
        return SeriesRange(text=key, days=None, limit=SERIES_MAX_LIMIT)
    unit = key[-1:]
    digits = key[:-1]
    if unit not in SERIES_RANGE_UNITS or not digits.isdigit():
        return None
    n = int(digits)
    if n <= 0:
        return None
    days = n * SERIES_RANGE_UNITS[unit]
    if days > SERIES_MAX_DAYS:
        # 超大窗口 = 无区分价值的"全历史"，且会让 date - timedelta 溢出（500）。
        # 判非法（422）而不是悄悄钳小：钳小会让回显的 range 与实际窗口不符。
        return None
    # 规范化文本按**换算后的天数**取：`1y` 与 `366d` 是同一个时间窗，回共同一个 canonical
    # 文本（缓存键与响应 range 都据此），不因字面量不同而各存一份 / 各报一个窗口。
    # 上界 = days + 1（窗口下界含当天，日频最多 days+1 个点），再按 SERIES_MAX_LIMIT 封顶。
    return SeriesRange(text=f"{days}d", days=days, limit=min(days + 1, SERIES_MAX_LIMIT))


def _pit_as_of(row: IndustryMetric | None) -> tuple[date | None, str | None]:
    """PIT 徽章 (as_of, as_of_source) —— 全部投影点的**唯一**取数口（Ruling 34）。

    ``published_at`` 存在 → 上海日历日 + ``"published_at"``（真 PIT）；
    否则 → ``period`` + ``"period"``（显式标注回退，ADR 0011「绝不静默回退到 period」）。
    ``row`` 为 None（无数据）→ (None, None)，两个字段都空，不假装有值。
    """
    if row is None:
        return None, None
    if row.published_at is not None:
        return row.published_at.astimezone(_CN_TZ).date(), AS_OF_FROM_PUBLISHED
    return row.period, AS_OF_FROM_PERIOD


def _stamp_caliber(cfg: IndustryConfig, rows: list[dict]) -> list[dict]:
    """给缺 ``caliber_version`` 的行盖上 registry 声明的当前口径（就地，返回同一列表）。

    行自带值优先（人工导入指定口径断点）；registry 未声明（None）即不写 —— 于是
    "登记位存在但尚未回填具体口径"在库里的表现就是 NULL，不会被假值污染。
    """
    for row in rows:
        if row.get("caliber_version") is None:
            m = cfg.metric(row["metric_key"])
            row["caliber_version"] = m.caliber_version if m is not None else None
    return rows


def _require_industry(industry_key: str) -> IndustryConfig:
    cfg = get_industry(industry_key)
    if cfg is None:
        raise UnknownIndustryError(f"Industry '{industry_key}' is not configured in registry")
    return cfg


# ── Fetchers ──────────────────────────────────────────────────────────

if TYPE_CHECKING:
    from app.core.providers.akshare_client import AkShareClient
    from app.core.providers.caaa_client import CaaaClient
    from app.core.providers.xuantian_client import XuantianClient

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
                        metric_key,
                        value,
                        period,
                    )
                    continue
                rows.append(
                    {
                        "industry_key": cfg.key,
                        "stock_id": 0,
                        "metric_key": m.key,
                        "source": source,
                        "source_tier": m.tier,
                        "freq": "daily",
                        "period": period,
                        "value": value,
                        "unit": m.unit or None,
                        "extra": None,
                    }
                )
        except Exception as exc:
            logger.warning("AKShare %s fetch failed (skipped): %s", metric_key, exc)

    return rows


async def _fetch_caaa_sow_row(cfg: IndustryConfig, client: CaaaClient | None = None) -> list[dict]:
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

    return [
        {
            "industry_key": cfg.key,
            "stock_id": 0,
            "metric_key": "sow_inventory",
            "source": "caaa",
            "source_tier": m.tier,
            "freq": "monthly",
            "period": data["period"],
            "value": data["inventory_wan_tou"],
            "unit": m.unit or None,
            "extra": {"article_url": data["article_url"], "mom_pct": data.get("mom_pct")},
        }
    ]


async def _fetch_xuantian_capacity_rows(
    cfg: IndustryConfig, client: XuantianClient | None = None
) -> list[dict]:
    """玄田产能四指标 → 标准 metric 行（每指标展开一行）.

    客户端已做周期解析与哨兵剔除；本层只做 registry 对照展开。任何失败
    返回空列表（不抛穿），未命中 registry 的指标键静默跳过。
    """
    if client is None:
        from app.core.providers.xuantian_client import get_xuantian_client  # noqa: PLC0415

        client = get_xuantian_client()

    try:
        parsed = await client.fetch_capacity()
    except Exception as exc:  # 双保险：client 自身已兜底，此处防注入实现抛穿
        logger.warning("Xuantian capacity fetch raised (skipped): %s", exc)
        return []
    if not parsed:
        return []

    rows: list[dict] = []
    for item in parsed:
        for metric_key, value in item["values"].items():
            m = cfg.metric(metric_key)
            if m is None:
                continue
            rows.append(
                {
                    "industry_key": cfg.key,
                    "stock_id": 0,
                    "metric_key": m.key,
                    "source": "xuantian",
                    "source_tier": m.tier,
                    "freq": item["freq"],
                    "period": item["period"],
                    "value": value,
                    "unit": m.unit or None,
                    "extra": {"channel": "xuantian", "raw_period": item["raw_period"]},
                }
            )
    return rows


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


# ── 增量门控（三层）──────────────────────────────────────────────────
#
# L1 调度门：next_due_at 未到 → 不发请求（scheduled 轨道；worker force=True 绕过）
# L2 内容门：响应哈希与库内一致 → 零写入零重算，只 touch 检查时间
# L3 行级 diff：只写 new/changed 行；修订 old→new 留痕（官方回修是常态，非异常）
#
# 依据：官方统计会修订历史（2023 畜禽监测样本轮换即实例），因此任何层都
# 不以 last_period 水位跳过历史——水位仅审计用。
#
# 节奏表与 next_due() 定义在 industry_ingest_diff.py（纯函数模块），
# service 只 import 使用（单测 import 链不拉起 settings）。


def _as_row_dicts(rows: list[IndustryMetric]) -> list[dict]:
    """ORM 行 → diff 输入 dict（键与 fetcher 产出的标准行对齐）."""
    return [
        {
            "industry_key": r.industry_key,
            "stock_id": r.stock_id,
            "metric_key": r.metric_key,
            "source": r.source,
            "source_tier": r.source_tier,
            "freq": r.freq,
            "period": r.period,
            "value": float(r.value) if r.value is not None else None,
            "unit": r.unit,
            "extra": r.extra,
        }
        for r in rows
    ]


async def _gated_xuantian_fetch(
    db: AsyncSession, cfg: IndustryConfig, *, force: bool
) -> tuple[list[dict], str, dict, list[dict]]:
    """玄田通道三层门控：返回 (待写行, gating 状态, stats, revisions) 并维护状态表.

    gating: "not_due"（L1 短路，返回空行）| "unchanged"（L2 短路）|
            "full"（首抓全量）| "incremental"（有 new/changed）
    短路时 stats/revisions 返回空 dict/list，主流程直接透传。
    """
    now = datetime.now(UTC)
    state = await repo.get_ingest_state(db, cfg.key, "xuantian")

    # L1：调度门（force 绕过；无状态/无到期时间/到期已过均放行，判定住纯模块）
    if not is_due(state.next_due_at if state else None, now, force=force):
        return [], "not_due", {}, []

    parsed = await _fetch_xuantian_capacity_rows(cfg)
    if not parsed:
        return [], "not_due", {}, []  # 抓取失败视为本轮跳过（client 已 log）

    new_hash = canonical_content_hash(parsed)

    # L2：内容门（哈希一致 → 零下游）
    if state and state.content_hash == new_hash:
        await repo.touch_ingest_state(db, cfg.key, "xuantian", last_checked_at=now)
        return [], "unchanged", {}, []

    # L3：行级 diff（首抓 existing 为空 → 全部 new）
    existing = await repo.list_metric_rows(db, cfg.key, source="xuantian")
    actions = compute_ingest_actions(parsed, _as_row_dicts(existing))
    to_write = actions["new"] + actions["changed"]
    xt_stats = {
        "new": len(actions["new"]),
        "changed": len(actions["changed"]),
        "unchanged": actions["unchanged_count"],
        "orphaned": actions["orphaned_count"],
    }

    if to_write:
        max_period = max(r["period"] for r in parsed)
        await repo.upsert_ingest_state(
            db,
            {
                "industry_key": cfg.key,
                "source": "xuantian",
                "last_success_at": now,
                "last_period": max_period,
                "content_hash": new_hash,
                "next_due_at": next_due("xuantian", now),
                "stats": xt_stats,
            },
        )
    else:
        # 空写但哈希已变（DB restore / 哈希被清 / 仅 orphaned 响应）：仍须武装状态，
        # 否则 next_due_at 永不推进，调度任务每天重抓重 diff（MF-1）。
        # last_success_at 语义 = "最近一次有写入的成功抓取"，空写轮保留原值。
        await repo.upsert_ingest_state(
            db,
            {
                "industry_key": cfg.key,
                "source": "xuantian",
                "last_success_at": state.last_success_at if state else None,
                "last_period": max(r["period"] for r in parsed),
                "content_hash": new_hash,
                "next_due_at": next_due("xuantian", now),
                "stats": xt_stats,
            },
        )
    return to_write, ("full" if state is None else "incremental"), xt_stats, actions["revisions"]


async def ingest_industry_metrics(
    db: AsyncSession,
    industry_key: str = "pig",
    source: str | None = None,
    months: int = 37,
    *,
    force: bool = False,
) -> dict:
    """Fetch → [L1/L2/L3 门控] → upsert → purge → derive → signal（幂等）.

    force=True（worker 手动触发）绕过 L1 调度门——修订场景的人工兜底；
    scheduled 轨道 force=False 吃满三层短路。
    """
    cfg = _require_industry(industry_key)
    source = source or settings.industry_data_source

    xt_stats: dict = {}
    xt_revisions: list[dict] = []
    if source == "mock":
        rows = build_industry_mock_points(cfg, months=months)
        gating = "full"
    elif source == "akshare":
        # 玄田通道独立过门（唯一带全量历史响应的源）；日价源照常滚动抓取：
        xt_rows, gating, xt_stats, xt_revisions = await _gated_xuantian_fetch(db, cfg, force=force)
        rows = await _fetch_akshare_rows(cfg, months=months)
        rows += await _fetch_caaa_sow_row(cfg)
        rows += xt_rows
    else:
        raise ValueError(f"Unknown industry data source: {source}")

    await _ensure_reference_points(db, cfg)
    # 口径版本登记位在写路径统一盖章（fetcher 不必各自记得带它）
    upserted = await repo.upsert_metrics(db, _stamp_caliber(cfg, rows))

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

    # 短路条件看"本轮是否有任何写入"，不只看玄田 gating——玄田 unchanged 但
    # soozhu 日价有新行时（常态），派生（猪粮比/sow_mom）必须照常重算
    # （ponytail review 修正：原"gating != unchanged"会停掉日价源的派生链）
    wrote_anything = upserted > 0
    derived_count = 0
    signal = None
    if wrote_anything:
        derived_count = await _compute_derived_metrics(db, cfg)
        signal = await evaluate_and_store_signal(db, cfg)
    else:
        signal = await repo.latest_signal(db, cfg.key)

    result = {
        "source": source,
        "gating": gating,
        "upserted": upserted,
        "derived_upserted": derived_count,
        "purged": purged,  # 已覆盖指标下清除的 mock/derived 行数
        "covered_metrics": sorted(covered),
        "signal": signal.signal_type if signal else None,
    }
    if source == "akshare":
        # period 转 ISO 字符串：worker 把 result 落 JSONB（默认 json 序列化器不认 date）
        result["stats"] = xt_stats
        result["revisions"] = [{**rev, "period": rev["period"].isoformat()} for rev in xt_revisions]
    return result


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


def _rollup_monthly_rows(
    cfg: IndustryConfig, m: MetricDef, rows: list[IndustryMetric]
) -> list[dict]:
    """每日度序列 → 月度行：每月最后一个非空日度值，period=月末，source 原样保留。"""
    by_key: dict[tuple[str, date], IndustryMetric] = {}
    for r in rows:  # rows 为升序
        if r.value is None:
            continue
        by_key[(r.source, _month_end(r.period))] = r
    return [
        {
            "industry_key": cfg.key,
            "stock_id": 0,
            "metric_key": m.key,
            "source": source,
            "source_tier": m.tier,
            "freq": "monthly",
            "period": period,
            "value": float(cast(float, r.value)),
            "unit": m.unit or None,
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
        total += await repo.upsert_metrics(db, _stamp_caliber(cfg, rollup_rows))

    derived: list[dict] = []

    def _row(
        m: MetricDef,
        freq: str,
        period: date,
        value: float,
        stock_id: int = 0,
        extra: dict | None = None,
    ) -> dict:
        return {
            "industry_key": cfg.key,
            "stock_id": stock_id,
            "metric_key": m.key,
            "source": "derived",
            "source_tier": TIER_DERIVED,
            "freq": freq,
            "period": period,
            "value": round(value, 4),
            "unit": m.unit or None,
            "extra": extra,
        }

    # 猪粮比 = hog_price / corn_price（按 period 对齐，日/月各算一条序列）
    ratio_def = cfg.metric("hog_corn_ratio")
    if ratio_def is not None:
        for freq in ("daily", "monthly"):
            hogs = {
                r.period: float(r.value)
                for r in await repo.get_metric_history(
                    db, cfg.key, "hog_price", limit=400, freq=freq
                )
                if r.value is not None
            }
            corns = {
                r.period: float(r.value)
                for r in await repo.get_metric_history(
                    db, cfg.key, "corn_price", limit=400, freq=freq
                )
                if r.value is not None
            }
            for period in sorted(set(hogs) & set(corns)):
                if corns[period]:
                    derived.append(_row(ratio_def, freq, period, hogs[period] / corns[period]))

    # 能繁环比 = (本月 - 上月) / 上月
    mom_def = cfg.metric("sow_inventory_mom")
    if mom_def is not None:
        sow_rows = [
            r
            for r in await repo.get_metric_history(
                db, cfg.key, "sow_inventory", limit=240, freq="monthly"
            )
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
            p for p in (_pick_row(sow_def, rows) for rows in by_period.values()) if p is not None
        ]
        for prev, cur in zip(sow, sow[1:], strict=False):
            if prev.value:
                derived.append(
                    _row(
                        mom_def,
                        "monthly",
                        cur.period,
                        (float(cast(float, cur.value)) - float(prev.value))
                        / float(prev.value)
                        * 100,
                    )
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
            derived.append(
                {
                    "industry_key": cfg.key,
                    "stock_id": stock_id,
                    "metric_key": mcap_def.key,
                    "source": "derived",
                    "source_tier": mcap_def.tier,
                    "freq": mcap_def.freq,
                    "period": date.today(),
                    "value": round(float(basic.total_mv) / annual, 2),
                    "unit": mcap_def.unit or None,
                    "extra": {
                        "annualized": not _hogs_ttm_ready(points),
                        "annual_hogs_wan": round(annual, 2),
                    },
                }
            )

    total += await repo.upsert_metrics(db, _stamp_caliber(cfg, derived))
    return total


async def evaluate_and_store_signal(db: AsyncSession, cfg: IndustryConfig) -> IndustrySignal:
    """Build snapshot from latest rows → rules engine → persist (idempotent per day)."""
    inp = await _build_cycle_input(db, cfg)
    out = cycle_engine.evaluate_pig_cycle(inp, cfg)

    return await repo.upsert_signal(
        db,
        {
            "industry_key": cfg.key,
            "phase": out.phase,
            "signal_type": out.signal,
            "positions": [asdict(s) for s in out.positions],
            "reason": "；".join(out.reasons),
            "basis": out.basis,
            "effective_date": date.today(),
        },
    )


async def _build_cycle_input(db: AsyncSession, cfg: IndustryConfig) -> cycle_engine.CycleInput:
    """指标快照：latest 行按 registry 源优先级裁决 + 环比/猪粮比序列。"""
    grouped = await repo.latest_rows_by_metric(db, [cfg.key])
    ratio_row = _pick_latest(cfg, grouped, "hog_corn_ratio")
    price_row = _pick_latest(cfg, grouped, "hog_price")
    cost_row = _pick_latest(cfg, grouped, "industry_cost_avg")
    sow_mom = [
        float(r.value)
        for r in await repo.get_metric_history(
            db, cfg.key, "sow_inventory_mom", limit=12, freq="monthly"
        )
        if r.value is not None
    ]
    ratio_series = [
        float(r.value)
        for r in await repo.get_metric_history(
            db, cfg.key, "hog_corn_ratio", limit=30, freq="daily"
        )
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


def _pick_row(m: MetricDef | None, rows: list[IndustryMetric]) -> IndustryMetric | None:
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


def _pick_latest(cfg: IndustryConfig, grouped: dict, metric_key: str) -> IndustryMetric | None:
    return _pick_row(cfg.metric(metric_key), grouped.get(metric_key, []))


def _caliber_of(m: MetricDef, row: IndustryMetric | None) -> tuple[str | None, str | None]:
    """(行上实际口径, registry 当前口径)。两者不一致即上游口径断点，由消费方提示。"""
    stored = row.caliber_version if row is not None else None
    return stored, m.caliber_version


async def _build_metric_latest(
    db: AsyncSession, cfg: IndustryConfig, m: MetricDef, grouped: dict, with_spark: bool
) -> MetricLatestOut:
    row = _pick_latest(cfg, grouped, m.key)
    out = MetricLatestOut(
        metric_key=m.key,
        name=m.name,
        unit=m.unit,
        tier=m.tier,
        caliber_version_registered=m.caliber_version,
        scope_key=cfg.key,
        description=m.description,
    )
    if row is None:
        return out

    out.value = float(row.value) if row.value is not None else None
    out.source = row.source
    out.freq = row.freq
    out.period = row.period
    out.published_at = row.published_at
    # PIT 徽章：as_of + as_of_source 同出一口（_pit_as_of），回退显式标注不静默
    out.as_of, out.as_of_source = _pit_as_of(row)
    out.caliber_version, _ = _caliber_of(m, row)
    out.scope_key = row.industry_key

    if out.value is not None:
        label = {
            "daily": "日环比",
            "weekly": "周环比",
            "monthly": "月环比",
            "quarterly": "季环比",
            "yearly": "年同比",
        }.get(row.freq, "环比")
        out.delta = await _delta_of(db, cfg.key, m, row, label)
        if m.warn_bands:
            band = next(
                (
                    b
                    for b in sorted(m.warn_bands, key=lambda b: (b.upper is None, b.upper or 0))
                    if b.upper is None or out.value <= b.upper
                ),
                None,
            )
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
    db: AsyncSession, industry_key: str, m: MetricDef, row: IndustryMetric, label: str
) -> MetricDelta | None:
    if row.value is None:
        return None
    series = await repo.get_metric_history(
        db, industry_key, m.key, limit=2, freq=row.freq, source=row.source
    )
    if len(series) < 2 or series[-1].period != row.period or not series[0].value:
        return MetricDelta(pct=None, direction="flat", label=label)
    pct = (float(row.value) - float(series[0].value)) / float(series[0].value) * 100
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    return MetricDelta(pct=round(pct, 2), direction=direction, label=label)


async def get_latest_metrics(
    db: AsyncSession, industry_key: str, group: str | None = None
) -> list[MetricLatestOut]:
    cfg = _require_industry(industry_key)
    grouped = await repo.latest_rows_by_metric(db, [cfg.key])
    metrics = cfg.metrics if group is None else [m for m in cfg.metrics if m.group == group]
    return [
        await _build_metric_latest(db, cfg, m, grouped, with_spark=group is None) for m in metrics
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
        metric_key=m.key,
        name=m.name,
        unit=m.unit,
        freq=freq or m.freq,
        tier=m.tier,
        points=[
            MetricHistoryPointOut(
                period=r.period,
                value=float(r.value) if r.value is not None else None,
                source=r.source,
                freq=r.freq,
                as_of=_pit_as_of(r)[0],
                as_of_source=_pit_as_of(r)[1],
                published_at=r.published_at,
                caliber_version=r.caliber_version,
            )
            for r in rows
        ],
    )


# ── 通用读面（spec §S1.2）：跨 scope latest + 升序 series ───────────────
#
# 复用审计结论：本读面**不是平行栈** —— 它复用与行业看板同一套 registry 元数据、
# 同一套源优先级裁决（`_pick_row`）与同一套徽章投影（`_latest_out` / `_caliber_of`），
# 只是把 scope 从"单个行业"放宽到"任意 scope 列表 / 全部 scope"。
# 行业端点 `/industries/{key}/metrics/latest` 继续服务行业 scope（含 spark/预警带，
# 工作台要用）；通用端点服务"一次问多个 scope 多个 metric"，供情绪/宏观/策略消费。


def _latest_out(
    m: MetricDef, row: IndustryMetric | None, row_scope: str | None = None
) -> MetricLatestOut:
    """registry 元数据 + 行上四徽章 → MetricLatestOut（无行时也给全 None 的骨架）。"""
    stored, registered = _caliber_of(m, row)
    out = MetricLatestOut(
        metric_key=m.key,
        name=m.name,
        unit=m.unit,
        tier=m.tier,
        caliber_version=stored,
        caliber_version_registered=registered,
        scope_key=row_scope,
        description=m.description,
    )
    if row is None:
        return out
    out.value = float(row.value) if row.value is not None else None
    out.source = row.source
    out.freq = row.freq
    out.period = row.period
    out.published_at = row.published_at
    out.as_of, out.as_of_source = _pit_as_of(row)
    return out


def _metric_def_or_raise(cfg: IndustryConfig, metric_key: str) -> MetricDef:
    m = cfg.metric(metric_key)
    if m is None:
        raise UnknownMetricError(f"Metric '{metric_key}' is not defined for '{cfg.key}'")
    return m


async def get_latest_metrics_across_scopes(
    db: AsyncSession,
    scope_keys: list[str] | None = None,
    metric_keys: list[str] | None = None,
) -> list[MetricLatestOut]:
    """跨 scope 的每指标最新值 + 四徽章（未登记 metric_key 一律拒绝）。

    - ``metric_keys`` 省略 → 返回**已登记 scope** 的全部指标（未登记 scope 无指标定义，
      故不出现在结果里，也不报错——这正是"先登记再读"的白名单语义）。
    - ``scope_keys`` 省略 → 全部 scope（行业 + 后续 'market' 等）。
    - ``metric_key`` 未在任何 scope 登记 → UnknownMetricError（调用方转 404）。
    """
    known_scopes = {cfg.key: cfg for cfg in get_all_industries()}
    if scope_keys is not None:
        if not scope_keys:
            raise ValueError("scope_keys 不得为空列表：省略该参数才表示'全部 scope'")
        unknown = [k for k in scope_keys if k not in known_scopes]
        if unknown:
            raise UnknownIndustryError(f"Scope(s) {unknown} are not configured in registry")
    if metric_keys is not None and not metric_keys:
        # 空列表 ≠ 省略：省略=全部指标，空列表=问了个空问题 → 调用方 bug，显式拒绝
        raise ValueError("metric_keys 不得为空列表：省略该参数才表示'全部已登记指标'")

    defs: list[tuple[str, MetricDef]] = []
    if metric_keys:
        for key in metric_keys:
            found = find_metric(key)
            if found is None:
                raise UnknownMetricError(f"Metric '{key}' is not registered in registry")
            defs.append(found)
    else:
        scopes = (
            known_scopes.values() if scope_keys is None else [known_scopes[k] for k in scope_keys]
        )
        defs = [(cfg.key, m) for cfg in scopes for m in cfg.metrics]

    grouped = await repo.latest_rows_by_metric(db, scope_keys)
    outs: list[MetricLatestOut] = []
    for _owner_scope, m in defs:
        rows = grouped.get(m.key, [])
        if scope_keys is not None:
            rows = [r for r in rows if r.industry_key in scope_keys]
        row = _pick_row(m, rows)
        outs.append(_latest_out(m, row, row.industry_key if row is not None else None))
    return outs


async def get_series(
    db: AsyncSession,
    scope_key: str,
    metric_key: str,
    range_key: str = SERIES_DEFAULT_RANGE,
) -> MetricSeriesOut:
    """一个 scope 一个指标的升序序列（**真实时间窗**内 + 单一源）。

    - `range` 是时间窗：只取 ``period >= today - window`` 的行。原来的实现把它当行数
      传给 `get_metric_history`，于是 `range=30d` 对月度指标会给 30 个月 —— 参数名与
      行为不符（F4）。窗口下界由 ``SeriesRange.period_from()`` 决定并回传为
      ``period_from``。
    - 回传的 ``range`` 是**规范化文本**（``1y`` → ``"366d"``），不是请求原样：同一窗口
      的不同字面量（``1y`` / ``366d``）必须回同一个值，否则缓存与前端会各持一套口径。
    - ``truncated`` 以**源过滤前**的取数为准（真正打满 DB 上界才算截断）；先过滤再判会
      把"多源占用上界、过滤后剩得很少"误报成完整（F3）。
    - 序列**不跨源拼接**：同 period 多源共存时按 registry 优先级取一个源。若库里源的
      都不在 registry 优先级表里（登记与数据脱节），**返回空序列 + source_mismatch=True**
      而不是悄悄丢掉全部点（保证"单一源"这条不变量是显式兑现的）。
    """
    parsed = parse_series_range(range_key)
    if parsed is None:
        raise ValueError(f"Invalid series range: {range_key!r}")
    cfg = _require_industry(scope_key)
    m = _metric_def_or_raise(cfg, metric_key)
    period_from = parsed.period_from()
    fetched = await repo.get_metric_window(
        db,
        scope_key,
        m.key,
        period_from=period_from,
        limit=parsed.limit,
        freq=m.freq,
    )
    # 截断判定必须在源过滤之前：命中 DB 上界 = 窗内还有更早的点没取回来
    truncated = len(fetched) >= parsed.limit
    available = {r.source for r in fetched}
    source = next((s for s in m.sources if s in available), None)
    source_mismatch = source is None and bool(fetched)
    rows = [r for r in fetched if r.source == source] if source is not None else []
    # source is None：单一源保证下不给图（另有 source_mismatch 让调用方报警）
    stored = rows[-1].caliber_version if rows else None
    return MetricSeriesOut(
        scope_key=scope_key,
        metric_key=m.key,
        name=m.name,
        unit=m.unit or None,
        tier=m.tier,
        freq=m.freq,
        source=source,
        source_mismatch=source_mismatch,
        caliber_version=stored,
        caliber_version_registered=m.caliber_version,
        range=parsed.text,
        period_from=period_from,
        limit=parsed.limit,
        truncated=truncated,
        points=[
            MetricHistoryPointOut(
                period=r.period,
                value=float(r.value) if r.value is not None else None,
                source=r.source,
                freq=r.freq,
                as_of=_pit_as_of(r)[0],
                as_of_source=_pit_as_of(r)[1],
                published_at=r.published_at,
                caliber_version=r.caliber_version,
            )
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
        for m in cfg.metrics
        if m.group == "company"
    ]


async def get_industry_companies(db: AsyncSession, industry_key: str) -> IndustryCompaniesOut:
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
        rows.append(
            CompanyRowOut(
                symbol=e.symbol,
                name=e.name,
                latest_price=e.latest_price,
                total_mv_yi=round(e.total_mv / 1e4, 2) if e.total_mv is not None else None,
                pe_ttm=e.pe_ttm,
                pb=e.pb,
                has_company_data=any(v is not None for v in metrics.values()),
                metrics=metrics,
            )
        )

    return IndustryCompaniesOut(
        industry=IndustryBriefOut(
            key=cfg.key,
            name=cfg.name,
            description=cfg.description,
            sw_l3_codes=cfg.sw_l3_codes,
        ),
        columns=_company_columns(cfg),
        rows=rows,
    )


async def list_industries(db: AsyncSession) -> list[IndustrySummaryOut]:
    """`GET /api/v1/industries`（"已产品化行业列表"）。

    **只列行业 scope**（:func:`get_industry_scopes`，判据 = `IndustryConfig.is_industry`）：
    `INDUSTRIES` 现在同时登记非行业 scope（`market` = 全市场情绪，spec §S1.8），
    把它列成一个"产品化行业"会让列表与徽标的「行业分类（申万）」分母双双虚高
    （Ruling 54 第 4 条）。全市场情绪的读面走通用 `GET /api/v1/metrics/*`，不在本列表。
    """
    summaries: list[IndustrySummaryOut] = []
    for cfg in get_industry_scopes():
        grouped = await repo.latest_rows_by_metric(db, [cfg.key])
        coverage = {m.key: bool(grouped.get(m.key)) for m in cfg.metrics}
        periods = [r.period for rows in grouped.values() for r in rows]
        # 列表卡片状态行（P6）：最新信号行带出周期阶段/信号/生效日期。
        # 逐行业一次查询即可（N=行业数，个位数）；从未 ingest 的行业 signal 为 None。
        signal = await repo.latest_signal(db, cfg.key)
        summaries.append(
            IndustrySummaryOut(
                key=cfg.key,
                name=cfg.name,
                description=cfg.description,
                sw_l3_codes=cfg.sw_l3_codes,
                metric_total=len(cfg.metrics),
                metric_with_data=sum(coverage.values()),
                coverage=coverage,
                last_period=max(periods) if periods else None,
                phase=signal.phase if signal else None,
                signal_type=signal.signal_type if signal else None,
                signal_date=signal.effective_date if signal else None,
            )
        )
    return summaries


def get_all_industries() -> list[IndustryConfig]:
    """**全部 scope**（行业 + 非行业）。"行业数"请用 `get_industry_scopes()`。"""
    from app.services.industry_registry import INDUSTRIES  # noqa: PLC0415

    return list(INDUSTRIES.values())


def get_industry_scopes() -> list[IndustryConfig]:
    """行业 scope（判据见 `IndustryConfig.is_industry`）；列表/徽标分母的唯一来源。"""
    from app.services.industry_registry import get_industry_scopes as _scopes  # noqa: PLC0415

    return _scopes()


# ── Dashboard aggregate ───────────────────────────────────────────────


def _signal_out(row: IndustrySignal) -> SignalOut:
    positions = [
        PositionSliceOut(**p) for p in (row.positions or []) if isinstance(p, dict) and "name" in p
    ]
    return SignalOut(
        signal_type=row.signal_type,
        phase=row.phase,
        effective_date=row.effective_date,
        reason=row.reason,
        positions=positions,
    )


async def get_dashboard(db: AsyncSession, cache: CacheClient, industry_key: str) -> DashboardOut:
    cfg = _require_industry(industry_key)
    cache_key = f"industry:{industry_key}:dashboard"
    cached = await cache.get(cache_key)
    if cached is not None:
        return DashboardOut(**cached)

    grouped = await repo.latest_rows_by_metric(db, [cfg.key])
    strip = [
        await _build_metric_latest(db, cfg, m, grouped, with_spark=True) for m in cfg.strip_metrics
    ]
    quick = [
        await _build_metric_latest(db, cfg, m, grouped, with_spark=False) for m in cfg.quick_metrics
    ]

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
        industry=IndustryBriefOut(
            key=cfg.key, name=cfg.name, description=cfg.description, sw_l3_codes=cfg.sw_l3_codes
        ),
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
    db: AsyncSession,
    cfg: IndustryConfig,
    key_a: str,
    label_a: str,
    key_b: str,
    label_b: str,
    limit: int,
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
    md = cfg.metric(metric_key)
    return TrendSeriesOut(
        periods=[r.period for r in rows],
        series={
            md.name if md else metric_key: [
                float(r.value) if r.value is not None else None for r in rows
            ]
        },
    )


async def _applicable_reference(
    db: AsyncSession, cfg: IndustryConfig, metric_key: str
) -> ReferenceOut | None:
    points: list[IndustryReferencePoint] = await repo.list_reference_points(db, cfg.key, metric_key)
    point = repo.applicable_reference(points)
    if point is None:
        return None
    return ReferenceOut(
        label=point.label,
        value=float(point.value),
        note=point.note,
        effective_from=point.effective_from,
    )


# 人工/CSV 导入通道允许写库的 source：人工录入 + 统计局 CSV（data-source.md L2 通道）。
# 采集适配器专属 source（akshare_* 等）与 mock/derived 不得经人工通道伪造。
IMPORT_ALLOWED_SOURCES = {"manual", "stats_gov"}


def _prepare_batch_rows(
    cfg: IndustryConfig, items: list[dict]
) -> tuple[list[dict], list[str], list[str]]:
    """校验并规整导入行：返回 (rows, skipped_unknown_metric, skipped_invalid_source).

    每行**无条件**补齐 `published_at` / `caliber_version` 两个键（缺则 None）：
    `pg_insert.values(rows)` 的列集取自**第一行**，混合批次里"首行没带、后面带了"的列
    会被静默丢弃…… 本函数是人工通道的规整口，故在此把列集钉死（repo 侧另有并集兜底）。
    """
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
        rows.append(
            {
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
                # 人工/CSV 通道是公告类数据的入口：PIT 与口径版本可显式落库
                # （published_at 不填即 NULL —— 不许拿 period 冒充实际可得时点）
                "published_at": item.get("published_at"),
                "caliber_version": item.get("caliber_version") or m.caliber_version,
            }
        )
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
        "upserted": upserted,
        "derived_upserted": derived,
        "skipped_unknown_metric": skipped,
        "skipped_invalid_source": rejected,
    }
