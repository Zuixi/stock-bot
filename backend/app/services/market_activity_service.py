"""spec §S1.9 交易活跃度与杠杆资金 + §S1.11 估值与风险溢价 —— 计算 + 落库.

## 这个模块是什么

把 S1.9 的 11 个指标与 S1.11 的 2 个指标算成**日频序列**并写进 `industry_metrics`
（`industry_key='market'`，`stock_id=0`），与 S1.8 的 `market_sentiment_service.py`
同表同读面。读面**不在这里** —— 走 S1.2 已建好的 `GET /api/v1/metrics/latest` /
`/series`（registry 驱动 + 四徽章 + Redis 缓存）。**不新建平行读路、不新建表。**

## Step 0 结论（实机验证，2026-10-12；探针 `scripts/spike_s19_s111_sources.py`）

- **TuShare `margin`**（两融市场汇总，SSE/SZSE/BSE 各一行/交易日）：`可得`；
  金额单位元；单次上限 4000 行 → 按 ≤2 年分片拉取；三所**求和**成全市场值。
- **TuShare `index_dailybasic`**（指数日度指标）：`受限` —— 全接口只 15 个代码，
  spec 的 7 个指数里 **5 个直取、2 个 0 行** → 5 个直取；
  北证50/科创综指走 `index_weight` + `daily_basic` **自建**。
- **10Y 国债收益率**（TuShare `yc_cb`）：`blocked-on-verification` —— **token 权限不足**
  （`抱歉，您没有接口(yc_cb)访问权限`）→ 改走**中债官方** `bond_china_yield`（逐年翻窗）；
  备源东财 `bond_zh_us_rate`（两源同日逐值相同）。

三态结论与逐条实机输出见 `docs/design/data-source.md` 附.7。

## 换手率口径（spec §S1.9 硬性验收判据）

落库值一律取 **自由流通股本口径**：

- 直取的 5 个指数 = `index_dailybasic.turnover_rate_f`（源自身定义，**不做二次换算**）；
  `turnover_rate`（总股本口径）**不落库**，只在 `extra.turnover_rate_total` 留痕供对账。
- 自建的 2 个指数 = 成分股 `daily_basic.turnover_rate_f` 的**权重加权平均**
  （权重 = `index_weight.weight`，缺失权重按实际覆盖权重重新归一），`extra.caliber`
  恒为 `free_float_weighted`，`extra.weight_coverage` 记录权重覆盖率。
- 口径本身写在 registry 的 `description` 与 `docs/design/metric-dictionary.md`。

## 缺数不静默为 0（照 S1.8 的既有纪律）

- 缺数一律 `value = NULL` + `extra.missing_reason`（`None` = 无缺口）。
- 覆盖不足也算缺数：自建换手率的权重覆盖率 < `MIN_WEIGHT_COVERAGE` → NULL +
  `partial_weight_coverage`；成分行覆盖率 < `MIN_MEMBER_COVERAGE` → NULL +
  `partial_member_coverage`。**不拿缺失数据拼数字**。
- 完全不写 0 行：某交易日全市场无行情行时**不落任何行**（不拿 0 伪造一天
  "全市场 0 元成交"）。
- 逐指标覆盖率对账由 :func:`metric_coverage` 提供（`covered_days` / `null_days` /
  `missing_days` / `missing_reasons`），可直接被巡检接口消费。

## A/B/C 归类（ADR 0014）

**全部 13 个指标都是 B 类**（只用当日点内事实，可回算、允许回补）：

- `market_turnover_amount`：当日 `daily_quotes.amount` 求和 —— 纯当日聚合。
- 7 个指数换手率：直取的 5 个是上游当日一行；自建的 2 个用**当日**成分行情 +
  **截至当日的最近一期**月度权重快照（`index_weight.trade_date <= 当日` 取最大者，
  **不看未来**）→ 无前视偏差、可回补。
- `margin_*` 3 个：上游当日一行的三所求和（市场汇总口径，非个股明细）。
- `cn_treasury_yield_10y` / `erp_10y`：中债当日发布的曲线值 / 由当日 PE 与当日
  收益率相减。

**没有 A 类**（不消费"只给当下"的盘中明细）。**自建指数的成分权重是 C 类事实**
（月度快照），但它只作**归属**用、且**字段级标注**：`extra.weight_source` =
`tu_share_index_weight_monthly_snapshot`、`extra.attribution` =
`current_index_weight_snapshot`（ADR 0014 第②③条：禁止用当前快照回算"成分本身"，
允许用行情事实按成分聚合，并显式标注归属）。

## PIT 纪律（ADR 0011 / Ruling 34-35）

`published_at` 按**产地各自的可得出处**填（不是一律 15:00，也不是回退 `period`）：

| 指标 | `published_at` | 依据 |
|---|---|---|
| `market_turnover_amount` / 7 个换手率 / `erp_10y` | 当日 **15:00** | 收盘行情在收盘那刻定型 |
| 3 个 `margin_*` | 当日 **18:00** | 交易所在收盘后清算并发布两融汇总（15:00 尚不可得）|
| `cn_treasury_yield_10y` | 当日 **19:00** | 中债/东财当日曲线在银行间收市（16:30）后发布 |

`caliber_version` 恒填 :data:`MARKET_ACTIVITY_CALIBER_VERSION`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustryMetric
from app.models.quote import DailyQuote
from app.repositories import industry_metric_repo as metric_repo
from app.services.industry_registry import (
    MARGIN_SOURCE,
    MARKET_ACTIVITY_ALL_KEYS,
    MARKET_ACTIVITY_CALIBER_VERSION,
    MARKET_ACTIVITY_METRIC_KEYS,
    MARKET_TURNOVER_AMOUNT_SOURCE,
    TREASURY_YIELD_SOURCE,
)

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

#: A 股收盘（行情类指标的可得出处）。
MARKET_CLOSE = dtime(15, 0)
#: 两融汇总发布时点（交易所收盘后清算）。
MARGIN_PUBLISH = dtime(18, 0)
#: 10Y 国债收益率发布时点（银行间收市后；中债与东财两源都已发布的最早保守时点）。
TREASURY_PUBLISH = dtime(19, 0)


# ── 指数换手率配置 ───────────────────────────────────────────────────────

#: 直取来源标记（`index_dailybasic.turnover_rate_f`）。
SRC_INDEX_DAILYBASIC = "index_dailybasic"
#: 自建来源标记（`index_weight` 权重 × 成分股 `daily_basic.turnover_rate_f`）。
SRC_SELF_BUILT = "self_built_weighted"


@dataclass(frozen=True)
class IndexTurnoverSpec:
    """一个指数的换手率取数规格（spec §S1.9 逐字列的 7 个）。"""

    label: str
    ts_code: str
    metric_key: str
    #: `index_dailybasic` 直取，或用成分权重自建。
    source: str
    #: 直取路径的落地列（自建路径不用）。
    column: str = "turnover_rate_f"


#: spec §S1.9 逐字的 7 个指数。**顺序 = spec 顺序**，缺一个必须当场失败
#: （`_assert_seven_indices`），不允许静默少一行。
INDEX_TURNOVER_SPECS: tuple[IndexTurnoverSpec, ...] = (
    IndexTurnoverSpec("上证指数", "000001.SH", "idx_turnover_ratio_sse", SRC_INDEX_DAILYBASIC),
    IndexTurnoverSpec("深证成指", "399001.SZ", "idx_turnover_ratio_szse", SRC_INDEX_DAILYBASIC),
    IndexTurnoverSpec("北证50", "899050.BJ", "idx_turnover_ratio_bse50", SRC_SELF_BUILT),
    IndexTurnoverSpec("科创综指", "000680.SH", "idx_turnover_ratio_star", SRC_SELF_BUILT),
    IndexTurnoverSpec("创业板指", "399006.SZ", "idx_turnover_ratio_chinext", SRC_INDEX_DAILYBASIC),
    IndexTurnoverSpec("上证50", "000016.SH", "idx_turnover_ratio_sse50", SRC_INDEX_DAILYBASIC),
    IndexTurnoverSpec("沪深300", "000300.SH", "idx_turnover_ratio_csi300", SRC_INDEX_DAILYBASIC),
)

#: S1.11 的 PE 口径底座：**沪深300 整体法**（`index_dailybasic.pe`，即
#: 指数总市值 / 指数口径净利润，不是成分中位数）。spec 允许"全A或沪深300"，
#: 本轮实测 `index_dailybasic` **没有全A（中证全指 000985）**，故取沪深300。
ERP_PE_INDEX_CODE = "000300.SH"
ERP_PE_INDEX_NAME = "沪深300"
ERP_PE_CALIBER = "aggregate_total_mv_over_net_profit"

#: `extra.missing_reason` 取值（与 registry 的 `MARKET_ACTIVITY_MISSING_REASONS` 同源语义）。
MISSING_NO_QUOTE_ROWS = "no_quote_rows"
MISSING_SOURCE_EMPTY = "source_empty"
MISSING_INDEX_NOT_IN_SOURCE = "index_not_in_source"
MISSING_PARTIAL_WEIGHT_COVERAGE = "partial_weight_coverage"
MISSING_PARTIAL_MEMBER_COVERAGE = "partial_member_coverage"
MISSING_NO_WEIGHT_SNAPSHOT = "no_weight_snapshot"
MISSING_TREASURY_MISSING = "treasury_yield_missing"
MISSING_PE_MISSING = "pe_missing"
MISSING_OUT_OF_INDEX_HISTORY = "out_of_index_history"
MISSING_ZERO_TURNOVER_DENOMINATOR = "zero_turnover_denominator"

#: 自建换手率的最小**权重覆盖率**（参与计算的成分权重和 / 该期全部成分权重和）。
MIN_WEIGHT_COVERAGE = 0.95
#: 自建换手率的最小**成分行覆盖率**（有当日 daily_basic 行的权重和 / 全部权重和）。
MIN_MEMBER_COVERAGE = 0.95

#: `units` 随 metric_key 固定在 registry（字典 §4.2 规则 3）；本表是 registry 的投影，
#: 由 `tests/test_market_activity_leverage.py` 机械比对，禁止在这里"顺手改单位"。
UNITS: dict[str, str | None] = {
    "market_turnover_amount": "亿元",
    "margin_financing_balance": "亿元",
    "margin_financing_buy_ratio": "%",
    "margin_short_balance": "亿元",
    "cn_treasury_yield_10y": "%",
    "erp_10y": "%",
    "pe_csi300": "倍",
    "idx_turnover_ratio_sse": "%",
    "idx_turnover_ratio_szse": "%",
    "idx_turnover_ratio_bse50": "%",
    "idx_turnover_ratio_star": "%",
    "idx_turnover_ratio_chinext": "%",
    "idx_turnover_ratio_sse50": "%",
    "idx_turnover_ratio_csi300": "%",
}

#: **spec §S1.9 + §S1.11 承诺**的 13 个指标（11 + 2）——覆盖率对账的分母。
INGEST_METRIC_KEYS: tuple[str, ...] = tuple(k for k in UNITS if k != "pe_csi300")

#: 本模块落库的**全部** key（= 13 个 spec 指标 + ERP 的 PE 底座）。
ALL_METRIC_KEYS: tuple[str, ...] = tuple(UNITS)

#: 两融三个指标的 key（顺序 = spec 表格顺序）。
MARGIN_KEYS: tuple[str, ...] = (
    "margin_financing_balance",
    "margin_financing_buy_ratio",
    "margin_short_balance",
)

#: 换手率指标的 key（顺序 = spec 顺序）。
TURNOVER_RATIO_KEYS: tuple[str, ...] = tuple(s.metric_key for s in INDEX_TURNOVER_SPECS)

#: 金额单位换算：TuShare 两融金额是**元**，落库单位是**亿元**。
YUAN_PER_YI = 1e8

#: `margin` 单次返回上限 4000 行 = 约 8 年 × 3 交易所；分片按 2 年（≈1500 行）留足余量。
MARGIN_CHUNK_DAYS = 730

#: 一次 `INSERT ... ON CONFLICT` 最多带多少行。
#:
#: **硬性上限**：asyncpg 拒绝参数个数 > 32767 的语句（实测 5.2 年 × 14 指标 ≈ 11,600 行
#: = 13.9 万参数，直接 `InterfaceError: the number of query arguments cannot exceed 32767`）。
#: 本表一行 12 列 → 500 行 = 6,000 参数，留足余量。分批**不影响幂等**（同一唯一键
#: 无论落在哪一批都是 DO UPDATE）。
UPSERT_BATCH_ROWS = 500


def _assert_registry_keys_match() -> None:
    """本模块落库的 key 集必须与 registry 的登记项**逐字相等**。

    registry 是白名单（未登记 key 会被读/写路径拒绝），故"服务要落 a、registry 只登记 b"
    会让落库在写入路径上失败或让读面 404。这里在**导入时**当场失败，而不是等运行到一半。
    """
    if set(ALL_METRIC_KEYS) != set(MARKET_ACTIVITY_ALL_KEYS):
        raise RuntimeError(
            "market_activity_service 与 registry 的 metric_key 集不一致："
            f"只在服务={sorted(set(ALL_METRIC_KEYS) - set(MARKET_ACTIVITY_ALL_KEYS))} "
            f"只在 registry={sorted(set(MARKET_ACTIVITY_ALL_KEYS) - set(ALL_METRIC_KEYS))}"
        )
    spec = set(INGEST_METRIC_KEYS)
    if spec != set(MARKET_ACTIVITY_METRIC_KEYS):
        raise RuntimeError(
            "spec 承诺的 13 个 key 在服务与 registry 之间不一致："
            f"只在服务={sorted(spec - set(MARKET_ACTIVITY_METRIC_KEYS))} "
            f"只在 registry={sorted(set(MARKET_ACTIVITY_METRIC_KEYS) - spec)}"
        )


_assert_registry_keys_match()


def published_at_for(day: date, at: dtime = MARKET_CLOSE) -> datetime:
    """交易日 + 时点 → 带时区的 `published_at`（non-naive；Ruling 35 的反面）。"""
    return datetime.combine(day, at, tzinfo=_CN_TZ)


def _publish_time(metric_key: str) -> dtime:
    if metric_key in MARGIN_KEYS:
        return MARGIN_PUBLISH
    if metric_key == "cn_treasury_yield_10y":
        return TREASURY_PUBLISH
    return MARKET_CLOSE


@dataclass(frozen=True)
class MetricValue:
    """一个指标在一天的结果：值 + 缺口原因 + 可复核的分母/原始量。"""

    metric_key: str
    value: float | None
    missing_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _assert_seven_indices() -> None:
    """spec §S1.9 要求恰好 7 个指数换手率；少一个必须当场 `RuntimeError`。"""
    if len(INDEX_TURNOVER_SPECS) != 7:
        raise RuntimeError(
            f"spec §S1.9 要求 7 个指数换手率，登记了 {len(INDEX_TURNOVER_SPECS)} 个："
            f"{[s.label for s in INDEX_TURNOVER_SPECS]}"
        )


# ── 交易日与全市场成交额 ────────────────────────────────────────────────


async def _quote_days(db: AsyncSession, day_lo: date, day_hi: date) -> list[date]:
    """区间内 `daily_quotes` 实际持有的交易日（升序）—— 落库天的**唯一**定义。

    不按日历推：节假日工作日的脏日期不该被当成交易日，也不该被当成"缺数日"。
    """
    stmt = (
        select(DailyQuote.trade_date)
        .where(DailyQuote.trade_date >= day_lo, DailyQuote.trade_date <= day_hi)
        .group_by(DailyQuote.trade_date)
        .order_by(DailyQuote.trade_date)
    )
    return [r[0] for r in (await db.execute(stmt)).all()]


async def turnover_amount_by_day(
    db: AsyncSession, day_lo: date, day_hi: date
) -> dict[date, tuple[float, int]]:
    """`[day_lo, day_hi]` 内逐日全市场成交额（**亿元**）+ 有成交额的标的家数。

    源 = `daily_quotes.amount`（TuShare `daily.amount`，单位**千元**）全市场求和。
    **不另接上游**：S1.7 的资金流四件套已经证明这张表就是全市场成交的底座
    （实测 20261009 合计 1,916,691,559 千元 = 19,166.9 亿元）。

    只统计 `amount IS NOT NULL` 的行；家数一并返回，让"这天统计了几只票"可复核
    （当日一行都没有时该日**不出现在返回字典里** → 调用方落 `no_quote_rows`，
    而不是落一个 0 元的假成交额）。
    """
    sql = text(
        """
        SELECT trade_date,
               sum(amount) / 100000.0 AS amount_yi,
               count(*)               AS n_rows
        FROM daily_quotes
        WHERE trade_date BETWEEN CAST(:day_lo AS date) AND CAST(:day_hi AS date)
          AND amount IS NOT NULL
        GROUP BY trade_date
        ORDER BY trade_date
        """
    )
    rows = (await db.execute(sql, {"day_lo": day_lo, "day_hi": day_hi})).all()
    return {r.trade_date: (float(r.amount_yi), int(r.n_rows)) for r in rows}


# ── 两融（TuShare `margin`，市场汇总）────────────────────────────────────


async def fetch_margin_frame(client: Any, day_lo: date, day_hi: date) -> pd.DataFrame:
    """按 ≤2 年分片拉取 `margin`（单次上限 4000 行）并拼接。

    分片是**硬要求**：不分片时 2000→2026 的请求只回 4000 行（最早 20200508），
    更早的年份被静默截断——那会让"≥3 年序列"看起来成立、实际缺头。
    """
    frames: list[pd.DataFrame] = []
    cursor = day_lo
    while cursor <= day_hi:
        chunk_hi = min(cursor + timedelta(days=MARGIN_CHUNK_DAYS), day_hi)
        df = await client.fetch_margin(
            start_date=cursor.strftime("%Y%m%d"), end_date=chunk_hi.strftime("%Y%m%d")
        )
        if df is not None and len(df):
            frames.append(df)
        cursor = chunk_hi + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def margin_by_day(frame: pd.DataFrame) -> dict[date, dict[str, Any]]:
    """`margin` 原始帧 → `{交易日: 全市场汇总}`（**三所求和**）。

    为什么求和：`margin` 一行 = 一个交易所，而 spec §S1.9 要的是**市场级**
    杠杆水平。逐所拆分落进 `extra.by_exchange`（产地留痕，ADR 0014「产地决定可信度」）。
    """
    if frame is None or not len(frame):
        return {}
    out: dict[date, dict[str, Any]] = {}
    for trade_date, group in frame.groupby("trade_date"):
        day = datetime.strptime(str(trade_date), "%Y%m%d").date()
        by_exchange: dict[str, dict[str, float | None]] = {}
        totals: dict[str, float] = {"rzye": 0.0, "rzmre": 0.0, "rqye": 0.0}
        for _, row in group.iterrows():
            exch = str(row.get("exchange_id") or "?")
            vals: dict[str, float | None] = {
                k: (None if pd.isna(row.get(k)) else float(row[k]))
                for k in ("rzye", "rzmre", "rqye")
            }
            by_exchange[exch] = vals
            for k in totals:
                v = vals[k]
                if v is not None:
                    totals[k] += v
        out[day] = {"totals": totals, "by_exchange": by_exchange, "n_exchanges": int(len(group))}
    return out


def _margin_metrics(day: date, agg: dict[str, Any] | None) -> list[MetricValue]:
    """两融三个指标：融资余额 / 融资买入额占比 / 融券余额。

    `融资买入额占比` = 融资买入额 / 全市场成交额 × 100（分母=当日 `daily_quotes.amount` 合计）。
    分母缺失或为 0 → NULL（**不落 0、不落无穷**）。
    """
    if agg is None:
        return [MetricValue(k, None, MISSING_SOURCE_EMPTY) for k in MARGIN_KEYS]
    totals = agg["totals"]
    # `by_exchange` 只在 :func:`margin_by_day` 产出的聚合里存在；调用方只给 `totals`
    # 时按空处理（逐所拆分是留痕字段，不是计算输入）。
    by_exchange = agg.get("by_exchange", {})
    rzye_yi = totals["rzye"] / YUAN_PER_YI
    rqye_yi = totals["rqye"] / YUAN_PER_YI
    rzmre = totals["rzmre"]
    share_extra = {
        "basis": "sum_of_exchanges",
        "n_exchanges": agg.get("n_exchanges", len(by_exchange)),
        "by_exchange_yuan": by_exchange,
    }
    return [
        MetricValue("margin_financing_balance", rzye_yi, None, dict(share_extra)),
        MetricValue("margin_short_balance", rqye_yi, None, dict(share_extra)),
        # 融资买入额占比在 :func:`compute_day_metrics` 里补分母（需要当日成交额）。
        MetricValue("margin_financing_buy_ratio", rzmre, None, dict(share_extra)),
    ]


# ── 指数换手率（直取 5 个 + 自建 2 个）─────────────────────────────────


async def fetch_direct_turnover(
    client: Any,
    specs: tuple[IndexTurnoverSpec, ...],
    day_lo: date,
    day_hi: date,
) -> dict[str, dict[date, tuple[float, float | None]]]:
    """直取路径：`index_dailybasic` 逐指数拉取（单次上限 3000 行，按 5 年分片）。

    返回 `{ts_code: {交易日: (turnover_rate_f, turnover_rate)}}`。
    上游对**不覆盖的指数**返回 0 行且不报错（北证50 / 科创综指）——这里**不抛异常**，
    由调用方按 spec 的 `source` 字段决定该走自建还是落 `index_not_in_source`。
    """
    out: dict[str, dict[date, tuple[float, float | None]]] = {}
    for spec in specs:
        if spec.source != SRC_INDEX_DAILYBASIC:
            continue
        per_day: dict[date, tuple[float, float | None]] = {}
        cursor = day_lo
        while cursor <= day_hi:
            chunk_hi = min(cursor + timedelta(days=5 * 365), day_hi)
            df = await client.fetch_index_dailybasic(
                ts_code=spec.ts_code,
                start_date=cursor.strftime("%Y%m%d"),
                end_date=chunk_hi.strftime("%Y%m%d"),
            )
            if df is not None and len(df):
                for _, row in df.iterrows():
                    if pd.isna(row.get(spec.column)):
                        continue
                    day = datetime.strptime(str(row["trade_date"]), "%Y%m%d").date()
                    total = (
                        None if pd.isna(row.get("turnover_rate")) else float(row["turnover_rate"])
                    )
                    per_day[day] = (float(row[spec.column]), total)
            cursor = chunk_hi + timedelta(days=1)
        out[spec.ts_code] = per_day
    return out


async def fetch_index_weight_frame(client: Any, index_code: str) -> pd.DataFrame:
    """拉取一个指数的**全部**月度成分权重快照（不传窗口会被截到最新一期）。"""
    df = await client.fetch_index_weight(index_code=index_code, start_date="19900101")
    if df is None or not len(df):
        return pd.DataFrame()
    df = df.copy()
    df["period_date"] = [
        datetime.strptime(str(v), "%Y%m%d").date() for v in df["trade_date"].tolist()
    ]
    return df


async def self_built_turnover(
    db: AsyncSession,
    frames: dict[str, pd.DataFrame],
    day_lo: date,
    day_hi: date,
    *,
    tushare: Any | None = None,
    skip_days: dict[str, set[date]] | None = None,
) -> dict[str, dict[date, MetricValue]]:
    """自建路径：成分股 `daily_basic.turnover_rate_f` 的**权重加权平均**。

    - 权重 = `index_weight.weight`（%），取**该交易日或之前最近一期**快照
      （`period_date <= day` 的最大者）→ 无前视偏差。
    - 分子分母都只用**当日有 `turnover_rate_f` 行**的成分：缺失权重按实际覆盖权重
      重新归一（`extra.weight_coverage` = 可用权重 / 全部权重）。
    - 覆盖率不足 → `value = NULL` + `partial_weight_coverage`（不拿缺失数据拼数字）。
    - `skip_days`：增量缓存 —— 该指数这些交易日**已经有落库值**，不必重算
      （回补中断后重跑只补缺口，不重复打上游）。

    返回 `{ts_code: {交易日: MetricValue}}`。
    """
    out: dict[str, dict[date, MetricValue]] = {}
    for spec in INDEX_TURNOVER_SPECS:
        if spec.source != SRC_SELF_BUILT:
            continue
        frame = frames.get(spec.ts_code)
        if frame is None or not len(frame):
            out[spec.ts_code] = {}
            continue
        cached = (skip_days or {}).get(spec.metric_key, set())
        out[spec.ts_code] = await _self_built_rows(
            db, frame, day_lo, day_hi, tushare=tushare, cached_days=cached
        )
    return out


async def self_built_cached_days(
    db: AsyncSession, day_lo: date, day_hi: date
) -> dict[str, set[date]]:
    """已落库**有值**的自建换手率交易日（增量回补的缓存键 = metric_key）。"""
    keys = [s.metric_key for s in INDEX_TURNOVER_SPECS if s.source == SRC_SELF_BUILT]
    stmt = select(IndustryMetric.metric_key, IndustryMetric.period).where(
        IndustryMetric.industry_key == "market",
        IndustryMetric.stock_id == 0,
        IndustryMetric.metric_key.in_(keys),
        IndustryMetric.period >= day_lo,
        IndustryMetric.period <= day_hi,
        IndustryMetric.value.is_not(None),
    )
    out: dict[str, set[date]] = {k: set() for k in keys}
    for key, period in (await db.execute(stmt)).all():
        out.setdefault(key, set()).add(period)
    return out


async def _turnover_from_daily_basic(client: Any, day: date) -> dict[str, float]:
    """按交易日拉全市场 `turnover_rate_f`（**为什么不是本地表**：见下）。

    ⚠️ 本地 `daily_basic_indicators.turnover_rate_f` 实测**整列为 NULL**
    （5559/5559 行为空，只有总股本口径 `turnover_rate` 有值），故自建换手率**不能**
    从本地表取自由流通口径 —— 必须按交易日直取上游 `daily_basic`
    （一次调用即覆盖全市场 5559 行，含北交所 349 行；见
    `scripts/spike_s19_s111_sources.py` 的 `[SELF]` 行）。
    """
    df = await client.fetch_daily_basic_by_trade_date(day.strftime("%Y%m%d"), page_size=6000)
    if df is None or not len(df):
        return {}
    out: dict[str, float] = {}
    for _, r in df.iterrows():
        v = r.get("turnover_rate_f")
        if v is None or pd.isna(v):
            continue
        out[str(r["ts_code"])] = float(v)
    return out


async def _self_built_rows(
    db: AsyncSession,
    weight_frame: pd.DataFrame,
    day_lo: date,
    day_hi: date,
    *,
    tushare: Any | None = None,
    cached_days: set[date] | None = None,
) -> dict[date, MetricValue]:
    """自建换手率的逐日加权平均（逐交易日取上游 `daily_basic.turnover_rate_f`）。"""
    if tushare is None:  # pragma: no cover — 调用方必须注入（避免隐式联网）
        raise ValueError("self-built turnover 需要注入 tushare client")
    cached = cached_days or set()
    days = [d for d in await _quote_days(db, day_lo, day_hi) if d not in cached]
    if not days:
        return {}
    by_day: dict[date, dict[str, float]] = {}
    empty_days: list[date] = []
    for day in days:
        quotes = await _turnover_from_daily_basic(tushare, day)
        if quotes:
            by_day[day] = quotes
        else:
            empty_days.append(day)
    if empty_days:
        # **响亮失败**：交易日（来自 `daily_quotes`）在上游 `daily_basic` 取不到任何
        # `turnover_rate_f` 只可能是上游异常/限频/超时。静默跳过会让这一整段被
        # 记成 `source_empty`（"这天没有数据"）—— 那是把**采集失败**伪装成**缺数**，
        # 实测在 800+ 天的长回补里会把 828 天一次性写成空。
        raise RuntimeError(
            f"daily_basic.turnover_rate_f 在 {len(empty_days)} 个交易日的取数结果为空"
            f"（前几个：{[d.isoformat() for d in empty_days[:5]]}）—— 上游限频/超时，"
            "请重跑回补（自建指标有增量缓存，重跑只补缺口）。**不得**静默记成缺数。"
        )
    if not by_day:
        return {}
    # `daily_basic` 与 `index_weight` 两侧都带交易所后缀（920001.BJ / 688001.SH），
    # 直接按 ts_code 匹配。**匹配前显式规范化**（去空白 + 大写），避免大小写/空白造成的
    # 假不匹配（那会把成分覆盖率打成 0 → 整列 `partial_member_coverage`）。
    weight_frame = weight_frame.copy()
    weight_frame["symbol"] = [str(c).strip().upper() for c in weight_frame["con_code"].tolist()]
    by_day = {d: {str(k).strip().upper(): v for k, v in q.items()} for d, q in by_day.items()}

    # **匹配率断言**（防"取数成功但一只都没匹配上"这一形态静默落 `source_empty`）：
    # 逐日按**当期成分**（而不是全历史成分并集）算有当日 `turnover_rate_f` 的比例，
    # 中位数低于门槛即抛错。⚠️ 分母必须是当期成分：北证50 的全历史并集有 110 只而每期
    # 只有 50 只 —— 按并集算中位数 = 0.936，会被误判成"键对不上"。
    _periods_sorted = sorted(set(weight_frame["period_date"]))
    _cov: list[float] = []
    for _day, _q in by_day.items():
        _eligible = [pp for pp in _periods_sorted if pp <= _day]
        if not _eligible:
            continue
        _members = set(weight_frame.loc[weight_frame["period_date"] == _eligible[-1], "symbol"])
        if _members:
            _cov.append(len(_members & set(_q)) / len(_members))
    _cov.sort()
    _median_cov = _cov[len(_cov) // 2] if _cov else 0.0
    if _median_cov < MIN_MEMBER_COVERAGE:
        raise RuntimeError(
            f"自建换手率的成分匹配率中位数={_median_cov:.3f} < {MIN_MEMBER_COVERAGE} —— "
            "上游取到了行但成分 ts_code 对不上（键空间/代码迁移），"
            "**不得**静默落 source_empty；请重核 `index_weight.con_code` 与 "
            "`daily_basic.ts_code` 的命名空间。"
        )
    periods = sorted(set(weight_frame["period_date"]))
    if not periods:
        return {}
    by_period = {p: g for p, g in weight_frame.groupby("period_date")}

    out: dict[date, MetricValue] = {}
    for day in sorted(by_day):
        # 该交易日或之前最近一期快照（无前视偏差）
        eligible = [p for p in periods if p <= day]
        if not eligible:
            out[day] = MetricValue(
                "", None, MISSING_NO_WEIGHT_SNAPSHOT, {"weight_period_from": periods[0].isoformat()}
            )
            continue
        period = eligible[-1]
        group = by_period[period]
        quotes = by_day[day]
        total_w = 0.0
        avail_w = 0.0
        weighted = 0.0
        n_members = 0
        n_with_quote = 0
        for _, wrow in group.iterrows():
            w = float(wrow["weight"])
            total_w += w
            n_members += 1
            v = quotes.get(wrow["symbol"])
            if v is None:
                continue
            n_with_quote += 1
            avail_w += w
            weighted += w * v
        weight_cov = (avail_w / total_w) if total_w else 0.0
        member_cov = (n_with_quote / n_members) if n_members else 0.0
        extra = {
            "caliber": "free_float_weighted",
            "weight_source": "tu_share_index_weight_monthly_snapshot",
            "attribution": "current_index_weight_snapshot",
            "weight_period": period.isoformat(),
            "n_members": n_members,
            "n_members_with_quote": n_with_quote,
            "member_coverage": round(member_cov, 6),
            "weight_coverage": round(weight_cov, 6),
            "min_coverage": MIN_WEIGHT_COVERAGE,
        }
        if member_cov < MIN_MEMBER_COVERAGE:
            out[day] = MetricValue("", None, MISSING_PARTIAL_MEMBER_COVERAGE, extra)
            continue
        if total_w == 0 or weight_cov < MIN_WEIGHT_COVERAGE:
            out[day] = MetricValue("", None, MISSING_PARTIAL_WEIGHT_COVERAGE, extra)
            continue
        out[day] = MetricValue("", weighted / avail_w, None, extra)
    return out


# ── 10Y 国债收益率与 ERP ───────────────────────────────────────────────


async def fetch_treasury_10y(client: Any, day_lo: date, day_hi: date) -> dict[date, float]:
    """中债国债收益率曲线 10Y 列（逐年翻窗；窗口必须 < 1 年）。"""
    out: dict[date, float] = {}
    for year in range(day_lo.year, day_hi.year + 1):
        df = await client.fetch_china_bond_yield(start_date=f"{year}0101", end_date=f"{year}1231")
        if df is None or not len(df):
            logger.warning("bond_china_yield(%d) 返回空 —— 该年 10Y 国债收益率缺数", year)
            continue
        sub = df[df["曲线名称"].astype(str).str.contains("中债国债收益率曲线")]
        for _, row in sub.iterrows():
            raw = row.get("10年")
            if raw is None or pd.isna(raw):
                continue
            d = row["日期"]
            day = d if isinstance(d, date) else pd.to_datetime(d).date()
            out[day] = float(raw)
    return out


async def pe_by_day_index(
    db: AsyncSession, index_code: str, day_lo: date, day_hi: date
) -> dict[date, float]:
    """读本模块已落的 PE 底座行（`metric_key='pe_csi300'`），供 ERP 相减。

    **为什么 PE 也落库**：spec §S1.11 要求"明示用哪个 PE 口径与哪个指数"，且判据是
    "≥5 年日频序列"。把 PE 单独落成一行，让 `erp_10y` 的**分子可被独立查询与手算核对**
    （只落 ERP 的话，"1/PE"这一半无法从库里复原）。`index_code` 仅用于断言，
    实际读取的 key 由 :data:`ERP_PE_METRIC_KEY` 固定。
    """
    if index_code != ERP_PE_INDEX_CODE:  # pragma: no cover — 防调用方传错指数
        raise ValueError(f"ERP 的 PE 底座只登记 {ERP_PE_INDEX_CODE}，收到 {index_code}")
    stmt = (
        select(IndustryMetric.period, IndustryMetric.value)
        .where(
            IndustryMetric.industry_key == "market",
            IndustryMetric.stock_id == 0,
            IndustryMetric.metric_key == ERP_PE_METRIC_KEY,
            IndustryMetric.period >= day_lo,
            IndustryMetric.period <= day_hi,
            IndustryMetric.value.is_not(None),
        )
        .order_by(IndustryMetric.period)
    )
    return {r.period: float(r.value) for r in (await db.execute(stmt)).all()}


# ── 落库 ───────────────────────────────────────────────────────────────


def _source_for(metric_key: str) -> str:
    if metric_key in MARGIN_KEYS:
        return MARGIN_SOURCE
    if metric_key == "cn_treasury_yield_10y":
        return TREASURY_YIELD_SOURCE
    if metric_key == "market_turnover_amount":
        return MARKET_TURNOVER_AMOUNT_SOURCE
    return METRIC_SOURCE_DERIVED


def _row(day: date, metric: MetricValue) -> dict[str, Any]:
    """`MetricValue` → `industry_metrics` 行（scope='market'，`stock_id=0`）。"""
    extra = dict(metric.extra)
    if metric.missing_reason is not None:
        extra["missing_reason"] = metric.missing_reason
    return {
        "industry_key": "market",
        "stock_id": 0,
        "metric_key": metric.metric_key,
        "source": _source_for(metric.metric_key),
        "source_tier": _TIERS[metric.metric_key],
        "freq": "daily",
        "period": day,
        "value": metric.value,
        "unit": UNITS[metric.metric_key],
        # NULL 值的行**也要留 extra**：`missing_reason` 是"为什么没有值"的唯一证据
        # （ADR 0014「产地决定可信度」的同款要求：有行 ≠ 有值，必须能区分）。
        "extra": extra or None,
        "published_at": published_at_for(day, _publish_time(metric.metric_key)),
        "caliber_version": MARKET_ACTIVITY_CALIBER_VERSION,
    }


#: 派生类指标的 `source`（由本地/上游当日事实算出）。
METRIC_SOURCE_DERIVED = "local_calc"

#: ERP 的 PE 底座 `metric_key`（沪深300 整体法 PE，单位倍数）。
ERP_PE_METRIC_KEY = "pe_csi300"

#: `source_tier`：两融/中债/5 个直取指数的换手率是交易所与中债的官方口径；
#: 全市场成交额与 ERP 是本地派生；自建的 2 个换手率含 C 类成分快照 → 归 `derived`。
_TIERS: dict[str, str] = {
    "market_turnover_amount": "derived",
    "margin_financing_balance": "official",
    "margin_financing_buy_ratio": "derived",
    "margin_short_balance": "official",
    "cn_treasury_yield_10y": "official",
    "erp_10y": "derived",
    "idx_turnover_ratio_sse": "official",
    "idx_turnover_ratio_szse": "official",
    "idx_turnover_ratio_bse50": "derived",
    "idx_turnover_ratio_star": "derived",
    "idx_turnover_ratio_chinext": "official",
    "idx_turnover_ratio_sse50": "official",
    "idx_turnover_ratio_csi300": "official",
    ERP_PE_METRIC_KEY: "official",
}


# ── 一天 13 个指标 ─────────────────────────────────────────────────────


def compute_day_metrics(
    day: date,
    *,
    turnover: tuple[float, int] | None,
    margin: dict[str, Any] | None,
    direct: dict[str, tuple[float, float | None]],
    self_built: dict[str, MetricValue],
    treasury: float | None,
    pe: float | None,
) -> dict[str, MetricValue]:
    """一天 13 个指标的结果（本模块的**唯一**计算入口，回补与增量共用）。

    所有入参都是"该日的原始事实"：`None` = 该源在当日**没有**值（→ `missing_reason`），
    `0` 与 `None` 语义严格区分（0 元融券余额是事实断言，缺数是 NULL）。

    - `direct`：`{ts_code: (turnover_rate_f, turnover_rate)}`（**只含该日**，由调用方按日切片）。
    - `self_built`：`{ts_code: MetricValue}`（**只含该日**）。
    """
    _assert_seven_indices()
    out: dict[str, MetricValue] = {}

    # ① 全市场成交额
    if turnover is None:
        out["market_turnover_amount"] = MetricValue(
            "market_turnover_amount", None, MISSING_NO_QUOTE_ROWS, {"source": "daily_quotes.amount"}
        )
    else:
        amount_yi, n_rows = turnover
        out["market_turnover_amount"] = MetricValue(
            "market_turnover_amount",
            amount_yi,
            None,
            {"source": "daily_quotes.amount", "n_rows": n_rows, "unit_native": "千元"},
        )

    # ② 7 个指数换手率（自由流通口径）
    for spec in INDEX_TURNOVER_SPECS:
        if spec.source == SRC_INDEX_DAILYBASIC:
            pair = direct.get(spec.ts_code)
            if pair is None:
                out[spec.metric_key] = MetricValue(
                    spec.metric_key,
                    None,
                    MISSING_INDEX_NOT_IN_SOURCE,
                    {
                        "ts_code": spec.ts_code,
                        "caliber": "free_float",
                        "source_column": spec.column,
                        "upstream": "tushare.index_dailybasic",
                    },
                )
            else:
                free, total = pair
                out[spec.metric_key] = MetricValue(
                    spec.metric_key,
                    free,
                    None,
                    {
                        "ts_code": spec.ts_code,
                        "index_name": spec.label,
                        "caliber": "free_float",
                        "source_column": spec.column,
                        "upstream": "tushare.index_dailybasic",
                        "turnover_rate_total": total,
                    },
                )
        else:
            mv = self_built.get(spec.ts_code)
            if mv is None:
                out[spec.metric_key] = MetricValue(
                    spec.metric_key,
                    None,
                    MISSING_SOURCE_EMPTY,
                    {
                        "ts_code": spec.ts_code,
                        "index_name": spec.label,
                        "caliber": "free_float_weighted",
                        "upstream": "self_built(index_weight × daily_basic)",
                    },
                )
            else:
                extra = dict(mv.extra)
                extra.setdefault("ts_code", spec.ts_code)
                extra.setdefault("index_name", spec.label)
                extra.setdefault("upstream", "self_built(index_weight × daily_basic)")
                out[spec.metric_key] = MetricValue(
                    spec.metric_key, mv.value, mv.missing_reason, extra
                )

    # ③ 两融三个指标（融资买入额占比需要全市场成交额作分母）
    margin_vals = {m.metric_key: m for m in _margin_metrics(day, margin)}
    buy = margin_vals["margin_financing_buy_ratio"]
    if buy.value is None or turnover is None or turnover[0] == 0:
        # 三种缺数原因**分开**：分子缺（底座没行）/ 分母缺（无行情行）/ 分母为 0
        # （成交额 0 元 → 占比无定义）。混成一个原因码会让对账分不清该补哪一侧。
        if buy.value is None:
            reason = buy.missing_reason or MISSING_SOURCE_EMPTY
        elif turnover is None:
            reason = MISSING_NO_QUOTE_ROWS
        else:
            reason = MISSING_ZERO_TURNOVER_DENOMINATOR
        margin_vals["margin_financing_buy_ratio"] = MetricValue(
            "margin_financing_buy_ratio",
            None,
            reason,
            {
                **buy.extra,
                "numerator_yuan": buy.value,
                "denominator_yi": None if turnover is None else turnover[0],
            },
        )
    else:
        margin_vals["margin_financing_buy_ratio"] = MetricValue(
            "margin_financing_buy_ratio",
            buy.value / YUAN_PER_YI / turnover[0] * 100.0,
            None,
            {
                **buy.extra,
                "numerator_yuan": buy.value,
                "denominator_yi": turnover[0],
                "formula": "融资买入额 / 全市场成交额 × 100",
            },
        )
    out.update(margin_vals)

    # ④ 10Y 国债收益率
    if treasury is None:
        out["cn_treasury_yield_10y"] = MetricValue(
            "cn_treasury_yield_10y",
            None,
            MISSING_SOURCE_EMPTY,
            {"upstream": "bond_china_yield(中债国债收益率曲线, 10年)"},
        )
    else:
        out["cn_treasury_yield_10y"] = MetricValue(
            "cn_treasury_yield_10y",
            treasury,
            None,
            {
                "upstream": "yield.chinabond.com.cn (中债国债收益率曲线)",
                "curve": "中债国债收益率曲线",
                "term": "10年",
            },
        )

    # ⑤ ERP = 1/PE × 100 − 10Y 国债收益率
    if pe is None or pe == 0:
        out["erp_10y"] = MetricValue(
            "erp_10y",
            None,
            MISSING_PE_MISSING,
            {
                "pe_index": ERP_PE_INDEX_NAME,
                "pe_index_code": ERP_PE_INDEX_CODE,
                "pe_caliber": ERP_PE_CALIBER,
                "pe": pe,
                "treasury_yield": treasury,
            },
        )
    elif treasury is None:
        out["erp_10y"] = MetricValue(
            "erp_10y",
            None,
            MISSING_TREASURY_MISSING,
            {
                "pe_index": ERP_PE_INDEX_NAME,
                "pe_index_code": ERP_PE_INDEX_CODE,
                "pe_caliber": ERP_PE_CALIBER,
                "pe": pe,
                "treasury_yield": None,
            },
        )
    else:
        earnings_yield = 100.0 / pe
        out["erp_10y"] = MetricValue(
            "erp_10y",
            earnings_yield - treasury,
            None,
            {
                "formula": "1/PE × 100 − 10Y 国债收益率",
                "pe_index": ERP_PE_INDEX_NAME,
                "pe_index_code": ERP_PE_INDEX_CODE,
                "pe_caliber": ERP_PE_CALIBER,
                "pe": pe,
                "earnings_yield_pct": earnings_yield,
                "treasury_yield": treasury,
            },
        )
    return out


# ── 区间落库与回补 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackfillStats:
    """一次区间落库的可核对统计（回补脚本直接打印它）。"""

    day_lo: date
    day_hi: date
    quote_days: int
    rows_upserted: int
    days_written: int
    per_metric_covered: dict[str, int]
    per_metric_null: dict[str, int]
    missing_reasons: dict[str, dict[str, int]]
    sources: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "day_lo": self.day_lo.isoformat(),
            "day_hi": self.day_hi.isoformat(),
            "quote_days": self.quote_days,
            "rows_upserted": self.rows_upserted,
            "days_written": self.days_written,
            "per_metric_covered": self.per_metric_covered,
            "per_metric_null": self.per_metric_null,
            "missing_reasons": self.missing_reasons,
            "sources": self.sources,
        }


async def upsert_metric_range(
    db: AsyncSession,
    day_lo: date,
    day_hi: date,
    *,
    tushare: Any | None = None,
    akshare: Any | None = None,
) -> BackfillStats:
    """把 `[day_lo, day_hi]` 的 13 个指标算出来并**幂等**写进 `industry_metrics`。

    落库天 = `daily_quotes` 实际持有的交易日（与 S1.8 同一定义）。
    幂等：唯一键 + `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0。
    调用方负责 `await db.commit()`（本层不替上层决定事务边界）。
    """
    _assert_seven_indices()
    from app.core.providers.akshare_client import get_akshare_client
    from app.core.providers.tushare_client import get_tushare_client

    ts_client = tushare or get_tushare_client()
    ak_client = akshare or get_akshare_client()

    days = await _quote_days(db, day_lo, day_hi)
    turnover = await turnover_amount_by_day(db, day_lo, day_hi)
    margin = margin_by_day(await fetch_margin_frame(ts_client, day_lo, day_hi))
    direct = await fetch_direct_turnover(ts_client, INDEX_TURNOVER_SPECS, day_lo, day_hi)
    weight_frames = {
        spec.ts_code: await fetch_index_weight_frame(ts_client, spec.ts_code)
        for spec in INDEX_TURNOVER_SPECS
        if spec.source == SRC_SELF_BUILT
    }
    self_built = await self_built_turnover(
        db,
        weight_frames,
        day_lo,
        day_hi,
        tushare=ts_client,
        skip_days=await self_built_cached_days(db, day_lo, day_hi),
    )
    treasury = await fetch_treasury_10y(ak_client, day_lo, day_hi)
    pe = await pe_by_day_index(db, ERP_PE_INDEX_CODE, day_lo, day_hi)

    rows: list[dict[str, Any]] = []
    covered: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    nulls: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    reasons: dict[str, dict[str, int]] = {k: {} for k in ALL_METRIC_KEYS}
    for day in days:
        values = compute_day_metrics(
            day,
            turnover=turnover.get(day),
            margin=margin.get(day),
            direct={code: per_day[day] for code, per_day in direct.items() if day in per_day},
            self_built={
                code: per_day[day] for code, per_day in self_built.items() if day in per_day
            },
            treasury=treasury.get(day),
            pe=pe.get(day),
        )
        for key in INGEST_METRIC_KEYS:
            metric = values[key]
            if metric.value is None:
                nulls[key] += 1
                if metric.missing_reason:
                    reasons[key][metric.missing_reason] = (
                        reasons[key].get(metric.missing_reason, 0) + 1
                    )
            else:
                covered[key] += 1
            rows.append(_row(day, metric))
        # PE 底座**不由本循环落库**（它已在 :func:`ingest_erp_pe` 里写过，且产地在
        # 上游直取而非本地派生），但覆盖率仍要看得见 —— 故只记数、不重复写行。
        if pe.get(day) is None:
            nulls[ERP_PE_METRIC_KEY] += 1
        else:
            covered[ERP_PE_METRIC_KEY] += 1

    upserted = await _upsert_batched(db, rows)
    return BackfillStats(
        day_lo=day_lo,
        day_hi=day_hi,
        quote_days=len(days),
        rows_upserted=upserted,
        days_written=len(days),
        per_metric_covered=covered,
        per_metric_null=nulls,
        missing_reasons={k: dict(sorted(v.items())) for k, v in reasons.items()},
        sources={k: _source_for(k) for k in ALL_METRIC_KEYS},
    )


async def ingest_erp_pe(
    db: AsyncSession, day_lo: date, day_hi: date, *, tushare: Any | None = None
) -> int:
    """落 ERP 的 PE 底座（`pe_csi300`，沪深300 整体法 PE）—— **必须先于 ERP**。

    分片按 5 年（`index_dailybasic` 单次上限实测 3000 行 ≈ 12 年，5 年留足余量）。
    返回落库行数。
    """
    from app.core.providers.tushare_client import get_tushare_client

    client = tushare or get_tushare_client()
    rows: list[dict[str, Any]] = []
    cursor = day_lo
    while cursor <= day_hi:
        chunk_hi = min(cursor + timedelta(days=5 * 365), day_hi)
        df = await client.fetch_index_dailybasic(
            ts_code=ERP_PE_INDEX_CODE,
            start_date=cursor.strftime("%Y%m%d"),
            end_date=chunk_hi.strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,pe,pe_ttm,pb,total_mv",
        )
        if df is not None and len(df):
            for _, r in df.iterrows():
                pe = r.get("pe")
                day = datetime.strptime(str(r["trade_date"]), "%Y%m%d").date()
                extra: dict[str, Any] = {
                    "index_code": ERP_PE_INDEX_CODE,
                    "index_name": ERP_PE_INDEX_NAME,
                    "caliber": ERP_PE_CALIBER,
                    "upstream": "tushare.index_dailybasic",
                }
                pe_ttm = r.get("pe_ttm")
                if not pd.isna(pe_ttm):
                    extra["pe_ttm"] = float(pe_ttm)
                if not pd.isna(r.get("pb")):
                    extra["pb"] = float(r["pb"])
                mv = r.get("total_mv")
                if not pd.isna(mv):
                    extra["total_mv_wan"] = float(mv)
                rows.append(
                    {
                        "industry_key": "market",
                        "stock_id": 0,
                        "metric_key": ERP_PE_METRIC_KEY,
                        # PE 的产地是上游直取（不是本地算的）→ source 用上游名。
                        "source": "tushare_index_dailybasic",
                        "source_tier": "official",
                        "freq": "daily",
                        "period": day,
                        "value": None if pd.isna(pe) else float(pe),
                        "unit": "倍",
                        "extra": extra,
                        "published_at": published_at_for(day, MARKET_CLOSE),
                        "caliber_version": MARKET_ACTIVITY_CALIBER_VERSION,
                    }
                )
        cursor = chunk_hi + timedelta(days=1)
    return await _upsert_batched(db, rows)


async def _upsert_batched(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """分批 upsert（asyncpg 参数上限 32767，见 :data:`UPSERT_BATCH_ROWS`）。

    返回各批 `rowcount` 之和（与一次性 upsert 的语义一致：`DO UPDATE` 计入 rowcount）。
    """
    if not rows:
        return 0
    total = 0
    for start in range(0, len(rows), UPSERT_BATCH_ROWS):
        total += await metric_repo.upsert_metrics(db, rows[start : start + UPSERT_BATCH_ROWS])
    return total


async def backfill_market_activity(
    db: AsyncSession,
    *,
    day_hi: date | None = None,
    lookback_days: int = 1900,
) -> BackfillStats:
    """回补 `[day_hi - lookback_days, day_hi]` 的 13 个指标（日频序列）并提交。

    默认 1900 个日历日 ≈ 5.2 年 ≥ spec §S1.11 的「≥5 年」判据（同时也满足 §S1.9 的
    「≥3 年」）。先落 PE 底座再算 ERP，保证 `erp_10y` 的分子可从库里独立复核。

    `day_hi=None` → 取 `daily_quotes` 的 `max(trade_date)`（**不是** `date.today()`：
    今天是休市日时按今天回补只会得到一片空）。
    """
    if day_hi is None:
        day_hi = (await db.execute(text("SELECT max(trade_date) FROM daily_quotes"))).scalar_one()
    day_lo = day_hi - timedelta(days=lookback_days)
    await ingest_erp_pe(db, day_lo, day_hi)
    stats = await upsert_metric_range(db, day_lo, day_hi)
    await db.commit()
    return stats


@dataclass(frozen=True)
class MetricCoverage:
    """一个指标的覆盖率对账（缺数可见性的对外形状）。"""

    metric_key: str
    period_from: date | None
    period_to: date | None
    rows: int
    covered_days: int
    null_days: int
    missing_days: list[date]
    missing_reasons: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "period_from": self.period_from.isoformat() if self.period_from else None,
            "period_to": self.period_to.isoformat() if self.period_to else None,
            "rows": self.rows,
            "covered_days": self.covered_days,
            "null_days": self.null_days,
            "missing_days": [d.isoformat() for d in self.missing_days],
            "missing_reasons": self.missing_reasons,
        }


async def metric_coverage(
    db: AsyncSession,
    *,
    day_lo: date | None = None,
    day_hi: date | None = None,
) -> list[MetricCoverage]:
    """逐指标的覆盖率：`covered_days` / `null_days` / `missing_days` / `missing_reasons`。

    "该交易日"的分母 = `daily_quotes` 实际持有的交易日（与落库天同一定义），
    故 `missing_days` 是"行情有、指标没有"的真缺口，**不含**尚未采集行情的日子。
    """
    stmt = (
        select(
            IndustryMetric.metric_key,
            IndustryMetric.period,
            IndustryMetric.value,
            IndustryMetric.extra["missing_reason"].astext.label("reason"),
        )
        .where(
            IndustryMetric.industry_key == "market",
            IndustryMetric.stock_id == 0,
            IndustryMetric.metric_key.in_(INGEST_METRIC_KEYS),
        )
        .order_by(IndustryMetric.metric_key, IndustryMetric.period)
    )
    if day_lo is not None:
        stmt = stmt.where(IndustryMetric.period >= day_lo)
    if day_hi is not None:
        stmt = stmt.where(IndustryMetric.period <= day_hi)
    rows = (await db.execute(stmt)).all()
    quote_days = set(
        await _quote_days(db, day_lo or date(1900, 1, 1), day_hi or date(2999, 12, 31))
    )

    by_metric: dict[str, list[Any]] = {k: [] for k in INGEST_METRIC_KEYS}
    for row in rows:
        by_metric.setdefault(row.metric_key, []).append(row)

    out: list[MetricCoverage] = []
    for key in INGEST_METRIC_KEYS:
        metric_rows = by_metric.get(key, [])
        periods = {r.period for r in metric_rows}
        covered = sum(1 for r in metric_rows if r.value is not None)
        nulls = len(metric_rows) - covered
        reasons: dict[str, int] = {}
        for r in metric_rows:
            if r.value is None and r.reason:
                reasons[r.reason] = reasons.get(r.reason, 0) + 1
        out.append(
            MetricCoverage(
                metric_key=key,
                period_from=min(periods) if periods else None,
                period_to=max(periods) if periods else None,
                rows=len(metric_rows),
                covered_days=covered,
                null_days=nulls,
                missing_days=sorted(quote_days - periods),
                missing_reasons=dict(sorted(reasons.items())),
            )
        )
    return out
