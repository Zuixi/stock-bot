"""spec §S1.10 情绪 · 波动率与衍生品 —— 计算 + 落库.

## 这个模块是什么

把 §S1.10 的 **11 个指标**（5 条 QVIX 中国波指期权隐含波动率 + 4 个品种股指期货升贴水
+ IC/IM 两条贴水率）算成**日频序列**并写进 `industry_metrics`
（`industry_key='market'`，`stock_id=0`），与 §S1.8 / §S1.9 / §S1.11 同表同读面。
读面**不在这里** —— 走 S1.2 已建好的 `GET /api/v1/metrics/latest` / `/series`
（registry 驱动 + 四徽章 + Redis 缓存）。**不新建平行读路、不新建表。**

spec 原话：「**这是情绪模块里性价比最高的一组**——数据质量高、有真实前瞻性」。

## 两条腿分别是什么（详见 `docs/design/data-source.md` 附.8 的 Step-0 实机结论）

- **期权隐含波动率（5 条）**：产地 = **第三方 optbbs.com 编制的 QVIX 中国波指**；
  取数 = AKShare `index_option_*_qvix` 直读其静态 CSV 的 `close` 列。
  **直接取隐含波动率序列**，不自己实现 Black-Scholes 反解。
- **股指期货升贴水（4 品种）+ IC/IM 贴水率**：期货腿 = TuShare `fut_daily` 的
  `<品种>.CFX` **逐日主力连续**；现货腿 = TuShare `index_daily`。两条腿相减。

## 升贴水的符号约定（**唯一口径，前端不得自行翻转符号**）

**`基差 = 期货 − 现货`**（期货价 − 现货指数价）：**正值 = 升水（期货高于现货）、
负值 = 贴水（期货低于现货）**。依据 spec §S1.10 的验收判据「升贴水负值时 UI 明示
『贴水』语义，避免正负号误读」——`现货 − 期货` 会把贴水显示成**正数**，正好是判据
要防的那种误读。手算核对（2026-10-12，`fut_daily` 实测）：
`IF2612.CFX` 结算价 4233.60 − 沪深300 收盘 4287.98 = **−54.38** ⇒ 贴水。
约定同时写进 registry 的 `MetricDef.description`（:data:`BASIS_SIGN_CONVENTION`）
与 `docs/design/metric-dictionary.md`。

**主值取期货结算价 `settle`**（中金所收盘后计算发布的当日官方标价 = 保证金/对冲成本的
结算依据）；收盘价口径另落 `extra.basis_close`（`close` − 现货收盘），供与行情商口径
对拍。**两条腿都在 16:00 前可得**，故 `published_at` = 当日 16:00 是真实时点。

## 主力连续为什么可以直接用（无前视偏差的一手证据）

`fut_mapping` 是**逐交易日**映射（实测 `IF.CFX` 4003 行、映射到 183 个真实合约）；
把窗口内出现过的每一个真实合约整段取回，与 `.CFX` 连续序列**逐日**比对 `close` ——
四品种各 484 个交易日 / 23–24 个合约 **不一致 0 日**。故 `<品种>.CFX` 是**逐日**主力
连续（不是"用今天的主力拼历史"）且**未复权**（= 当日映射合约的原始价）⇒ 与现货相减
有经济含义。`extra.contract` 仍记当日真实合约代码供复核。

## 缺数不静默为 0（照 §S1.8 / §S1.9 的既有纪律）

- 缺数一律 `value = NULL` + `extra.missing_reason`（`None` = 无缺口）。
  **0 的波动率 / 0 点的基差**是"市场毫无波动 / 期货与现货完全平水"这一**事实断言**，
  与"这天没有数据"完全相反 —— 绝不拿 0 顶缺。
- 三条原因码分开：`source_not_updated`（**源侧**该日无值：既包括尾部发布滞后，
  也包括上游历史偶发缺行）/ `futures_leg_missing` / `spot_index_missing`；
  现货收盘为 0 → `basis_spot_zero`（不落无穷）。
- **落库天锚在各源自身的可得起点上**（`extra.expected_from`）；交易日全集来自
  TuShare `trade_cal`（SSE）—— 于是"上游漏了某个交易日"会作为 **NULL 行**出现在库里
  （可见），而不是被静默跳过。
- 上游 CSV 里落在**非交易日**的行（实测 3 行：2026-08-22 周六 / 09-12 周六 / 09-13 周日）
  **不落库**，但**计数并告警**（静默丢上游行正是要防的形态）。
- 逐指标覆盖率对账由 :func:`metric_coverage` 提供（`covered_days` / `null_days` /
  `missing_days` / `missing_reasons`）。

## A/B/C 归类（ADR 0014）

**全部 11 个指标都是 B 类**（只用当日点内事实，可回算、允许回补）：

- 5 条 QVIX：上游当日一行（该 ETF 期权组合的隐含波动率指数收盘值）。
- 4 个基差：当日期货结算价 − 当日现货指数收盘价，两条腿都是当日点内事实。
- 2 条贴水率：由当日基差与当日现货收盘算出（年化口径用的合约最后交易日
  `fut_basic.delist_date` 在**挂牌时即已知**，不是未来信息）。

**没有 A 类**（不消费"只给当下"的盘中明细）。**没有 C 类**（不用成分权重快照之类
事后归属回算）。本模块**不引用**任何"当前快照"作为历史归属。

## PIT 纪律（ADR 0011 / Ruling 34-35）

`published_at` 按**产地各自的可得出处**填（不是一律 15:00，也不是回退 `period`）：

| 指标 | `published_at` | 依据 |
|---|---|---|
| 5 条 QVIX | 当日 **15:00** | 隐含波动率由当日收盘的期权价格定型 |
| 4 个基差 + 2 条贴水率 | 当日 **16:00** | 期货**结算价**在中金所收盘后发布，是本行的较晚一条腿 |

`caliber_version` 恒填 :data:`VOLATILITY_DERIVATIVES_CALIBER_VERSION`。

**落库前的 PIT 守卫**：复用 :func:`market_activity_service.upsert_metric_rows`，
它拒绝任何缺 `published_at`（或该列非带时区 datetime）/ 缺 `caliber_version` 的行 ——
这两列可空，漏带会**静默**落 NULL（Ruling 59 的原始事故形态）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustryMetric
from app.services.industry_registry import (
    DISCOUNT_RATE_SPECS,
    FUTURES_BASIS_SPECS,
    QVIX_SPECS,
    SPOT_INDEX_SOURCE,
    VOLATILITY_DERIVATIVES_ALL_KEYS,
    VOLATILITY_DERIVATIVES_CALIBER_VERSION,
    VOLATILITY_DERIVATIVES_METRIC_KEYS,
)
from app.services.market_activity_service import (
    MetricValue,
    published_at_for,
    upsert_metric_rows,
)

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

#: QVIX 的可得时点（波动率由当日收盘的期权价格定型）。
QVIX_PUBLISH = dtime(15, 0)
#: 基差/贴水率的可得时点：**期货结算价**在中金所收盘后计算发布。取 16:00 作为
#: **保守**的"两腿都已可得"时点（精确到分钟的发布时刻属未验证余项，见附.8.4 #2）。
FUTURES_SETTLE_PUBLISH = dtime(16, 0)

# ── 缺数原因码（与 registry 的 `VOLATILITY_DERIVATIVES_MISSING_REASONS` 同源语义）───

MISSING_SOURCE_NOT_UPDATED = "source_not_updated"
MISSING_QVIX_BEFORE_LISTING = "qvix_before_option_listing"
MISSING_SPOT_INDEX = "spot_index_missing"
MISSING_FUTURES_LEG = "futures_leg_missing"
MISSING_BASIS_SPOT_ZERO = "basis_spot_zero"

# ── 取数分片与守卫常量 ─────────────────────────────────────────────────

#: `fut_daily` 单次返回上限（附.5 #2 已证、本任务探针**实测复现**：不带窗口 / 带
#: `start_date=20180101` 都只回 2000 行、最早都停在 20180711 ⇒ 起点写早没用、分片是唯一
#: 出路）。分片按 3 年（≈730 行）留足余量。
FUT_DAILY_ROW_CAP = 2000
FUT_DAILY_CHUNK_DAYS = 3 * 365
#: `index_daily` 实测单次最多 6006 行（**未撞 2000 行上限**），仍按 5 年分片留余量。
SPOT_INDEX_CHUNK_DAYS = 5 * 365

#: 基差相对现货的合理上界：超出即说明"品种↔现货指数配错 / 量纲错"，**响亮失败**而不是
#: 照落（实测 4 个品种的 |基差|/现货 ≤ 1.6%）。
MAX_ABS_BASIS_RATIO = 0.20

#: 年化贴水率的日基数（简单年化，非复利）。
DAYS_PER_YEAR = 365.0

#: `units` 随 metric_key 固定在 registry（字典 §4.2 规则 3）；本表是 registry 的投影，
#: 由 `tests/test_volatility_derivatives_s110.py` 机械比对，禁止在这里"顺手改单位"。
UNITS: dict[str, str | None] = {
    **{key: "%" for _series, key, _name, _listed in QVIX_SPECS},
    **{key: "点" for _product, _contract, key, _spot_code, _spot_name in FUTURES_BASIS_SPECS},
    **{key: "%" for _product, key, _name, _basis_key in DISCOUNT_RATE_SPECS},
}

#: **spec §S1.10 承诺**的 11 个指标 —— 覆盖率对账的分母。
INGEST_METRIC_KEYS: tuple[str, ...] = tuple(UNITS)

#: 本模块落库的**全部** key（本批次无"底座类"附加 key，故与 spec 的 11 个相等）。
ALL_METRIC_KEYS: tuple[str, ...] = tuple(UNITS)

#: 5 条 QVIX 的 key（顺序 = spec 顺序）。
QVIX_KEYS: tuple[str, ...] = tuple(key for _s, key, _n, _d in QVIX_SPECS)
#: 4 个品种基差的 key（顺序 = spec 顺序）。
BASIS_KEYS: tuple[str, ...] = tuple(key for _p, _c, key, _sc, _sn in FUTURES_BASIS_SPECS)

#: 贴水率 key → (品种, 基差 key)。spec §S1.10 的「额外指标：IC/IM 贴水率」。
DISCOUNT_RATE_KEYS: dict[str, tuple[str, str]] = {
    key: (product, basis_key) for product, key, _name, basis_key in DISCOUNT_RATE_SPECS
}
#: 品种 → 贴水率 key（反向查表；`basis_metrics_for_day` 逐品种产出时用）。
DISCOUNT_KEY_BY_PRODUCT: dict[str, str] = {
    product: key for key, (product, _b) in DISCOUNT_RATE_KEYS.items()
}

#: QVIX 的 series 键 → metric_key（落库 key 与取数 series 的**唯一**对应表）。
QVIX_SERIES_TO_KEY: dict[str, str] = {series: key for series, key, _n, _d in QVIX_SPECS}
#: QVIX 的 series 键 → 该 ETF 期权的上市日（结构起点；上游在上市前补 NaN 行）。
#: 实测 5 条的上游"首个非空日"**逐个等于**此表的值（探针里是断言），
#: 本模块在取数时再核一遍（上游改口径/换品种必须当场发现）。
QVIX_OPTION_LISTED: dict[str, date] = {
    series: date.fromisoformat(listed) for series, _k, _n, listed in QVIX_SPECS
}

#: 品种 → (连续合约代码, 现货指数代码, 现货指数名)。
FUTURES_PRODUCTS: dict[str, tuple[str, str, str]] = {
    product: (contract, spot_code, spot_name)
    for product, contract, _key, spot_code, spot_name in FUTURES_BASIS_SPECS
}

#: 品种 → 基差 metric_key。
BASIS_KEY_BY_PRODUCT: dict[str, str] = {
    product: key for product, _c, key, _sc, _sn in FUTURES_BASIS_SPECS
}


def _assert_registry_keys_match() -> None:
    """本模块落库的 key 集必须与 registry 的登记项**逐字相等**（导入时当场失败）。

    registry 是白名单：服务要落 a、registry 只登记 b 会让写路径失败或读面 404。
    """
    if set(ALL_METRIC_KEYS) != set(VOLATILITY_DERIVATIVES_ALL_KEYS):
        raise RuntimeError(
            "volatility_derivatives_service 与 registry 的 metric_key 集不一致："
            f"只在服务={sorted(set(ALL_METRIC_KEYS) - set(VOLATILITY_DERIVATIVES_ALL_KEYS))} "
            f"只在 registry={sorted(set(VOLATILITY_DERIVATIVES_ALL_KEYS) - set(ALL_METRIC_KEYS))}"
        )
    if set(INGEST_METRIC_KEYS) != set(VOLATILITY_DERIVATIVES_METRIC_KEYS):
        raise RuntimeError(
            "spec §S1.10 承诺的 11 个 key 在服务与 registry 之间不一致："
            f"只在服务={sorted(set(INGEST_METRIC_KEYS) - set(VOLATILITY_DERIVATIVES_METRIC_KEYS))} "
            f"只在 registry="
            f"{sorted(set(VOLATILITY_DERIVATIVES_METRIC_KEYS) - set(INGEST_METRIC_KEYS))}"
        )


_assert_registry_keys_match()


def _publish_time(metric_key: str) -> dtime:
    """该指标的 `published_at` 时点（两档：QVIX 收盘 15:00 / 期货结算 16:00）。"""
    if metric_key in BASIS_KEYS or metric_key in DISCOUNT_RATE_KEYS:
        return FUTURES_SETTLE_PUBLISH
    return QVIX_PUBLISH


#: `source_tier`：QVIX 是**第三方**编制（官方 iVIX 已自 2018-02-22 暂停发布）→ highfreq；
#: 基差/贴水率由两个当日上游事实相减得出 → derived。
_TIERS: dict[str, str] = {
    **{key: "highfreq" for key in QVIX_KEYS},
    **{key: "derived" for key in BASIS_KEYS},
    **{key: "derived" for key in DISCOUNT_RATE_KEYS},
}


def _source_for(metric_key: str) -> str:
    """该指标的产地标记（读面四徽章的 `source` 字段）。"""
    if metric_key in QVIX_KEYS:
        return "akshare_optbbs_qvix"
    return "tushare_fut_daily+index_daily"


def _parse_day(raw: Any) -> date:
    """上游日期（`YYYYMMDD` 字符串 / `date` / `Timestamp`）→ `date`。"""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text_value = str(raw).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text_value, fmt).date()
        except ValueError:
            continue
    parsed: date = pd.to_datetime(text_value).date()
    return parsed


# ── 交易日全集（落库天的**权威**分母）───────────────────────────────────


async def fetch_trade_days(client: Any, day_lo: date, day_hi: date) -> list[date]:
    """`[day_lo, day_hi]` 的 SSE 交易日（升序）—— 落库天的**唯一**分母。

    为什么不按日历推：节假日工作日不该被当成交易日，也不该被当成"缺数日"。
    为什么不用本地 `daily_quotes`（§S1.9 的做法）：本模块的腿**不依赖**它
    （`daily_quotes` 实测只从 2023-05-09 起 829 天，会把 QVIX 的 11.6 年截成 3.4 年
    —— 那正是 Ruling 59 要防的"被无关输入截断"）。`trade_cal` 是独立、完整、与两个源
    都无耦合的交易日权威。
    """
    df = await client.fetch_trade_cal(
        exchange="SSE", start_date=day_lo.strftime("%Y%m%d"), end_date=day_hi.strftime("%Y%m%d")
    )
    if df is None or not len(df):
        # **响亮失败**：交易日全集是分页/覆盖率的分母，空集会让"覆盖率 100%"变成空话。
        raise RuntimeError(
            f"trade_cal(SSE, {day_lo}..{day_hi}) 返回 0 行 —— 交易日分母不可得；"
            "不得用日历日顶替（会把节假日算成缺数日）"
        )
    days = sorted({_parse_day(v) for v in df["cal_date"].tolist()})
    return [d for d in days if day_lo <= d <= day_hi]


# ── QVIX 中国波指 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class QvixSeries:
    """一条 QVIX 序列：上市日（结构起点）+ 窗口内**全部**日期行（含 NaN 占位）。"""

    series: str
    metric_key: str
    listed: date
    #: 上游给的全部日期行（窗口内、**未**过滤非交易日）→ 只作"上游是否有这天"的判据。
    raw_days: frozenset[date]
    #: 该序列**非空**的日期 → 只有这些日子有值。
    value_days: dict[date, float]
    #: 上游落在非交易日的行数（本模块**不落库**，但必须计数并告警）。
    non_trading_rows: int


async def fetch_qvix_series(
    akshare: Any, day_lo: date, day_hi: date, *, trade_days: set[date]
) -> dict[str, QvixSeries]:
    """拉取 5 条 QVIX 序列（逐条一次调用；上游是静态 CSV，无分页）。

    **三条上游事实都必须当场核**（静默接受 = 把上游改版伪装成"缺数"）：

    1. 返回 0 行 → `RuntimeError`（接口没了 / 被改版）。
    2. 缺 `date` / `close` 列 → `RuntimeError`。
    3. 上游"首个非空日"必须等于 registry 记的**期权上市日** → 不等即 `RuntimeError`
       （品种换了 / 上游补了更长历史 → 必须人工复核，不得静默接受）。
    """
    out: dict[str, QvixSeries] = {}
    for series, metric_key, _name, _listed_raw in QVIX_SPECS:
        df = await akshare.fetch_qvix(series)
        if df is None or not len(df):
            raise RuntimeError(
                f"QVIX series={series}（{metric_key}）返回 0 行 —— 上游不可得；"
                "不得用相近品种顶替、不得自己反解 Black-Scholes"
            )
        for column in ("date", "close"):
            if column not in df.columns:
                raise RuntimeError(
                    f"QVIX series={series} 缺列 {column!r}（实测列={list(df.columns)}）"
                )
        frame = df.copy()
        frame["period"] = [_parse_day(v) for v in frame["date"].tolist()]
        frame["value"] = pd.to_numeric(frame["close"], errors="coerce")
        non_null = frame[frame["value"].notna()]
        if not len(non_null):
            raise RuntimeError(
                f"QVIX series={series} 有 {len(frame)} 行但 close **整列为空** —— "
                "有行 ≠ 有值，该品种须按附.8 标 blocked（不得落一列 NULL 当交付）"
            )
        first_non_null = min(non_null["period"].tolist())
        listed = QVIX_OPTION_LISTED[series]
        if first_non_null != listed:
            raise RuntimeError(
                f"QVIX series={series} 的上游首个非空日={first_non_null}，"
                f"而 registry 记的期权上市日={listed} —— 两者不一致时必须人工复核"
                "（品种换了 / 上游补齐了更长历史），**不得**静默接受"
            )
        # 窗口裁剪：日期轴（含 NaN 行）与有值行分别成形。
        window = frame[(frame["period"] >= day_lo) & (frame["period"] <= day_hi)]
        if not len(window):
            raise RuntimeError(
                f"QVIX series={series} 在窗口 {day_lo}..{day_hi} 内 0 行 —— "
                "窗口与源可得期不重叠（回补窗口写错了？）"
            )
        raw_days = frozenset(window["period"].tolist())
        value_days = {
            _parse_day(row["period"]): float(row["value"])
            for _, row in window[window["value"].notna()].iterrows()
        }
        non_trading = sorted(d for d in raw_days if d not in trade_days)
        if non_trading:
            # **响亮告警，不静默丢**：上游确实会写非交易日行（实测 3 行：2026-08-22 周六 /
            # 09-12 周六 / 09-13 周日，且带真实数值）。本模块只落交易日，故这些行被丢弃 ——
            # 丢上游数据必须留下痕迹，否则"上游给了 2823 行、库里只有 2820 行"无法解释。
            logger.warning(
                "QVIX %s 有 %d 行落在**非交易日**（不落库）：%s",
                series,
                len(non_trading),
                [d.isoformat() for d in non_trading],
            )
        out[series] = QvixSeries(
            series=series,
            metric_key=metric_key,
            listed=listed,
            raw_days=raw_days,
            value_days=value_days,
            non_trading_rows=len(non_trading),
        )
    return out


def qvix_metrics_for_day(
    day: date, *, series_frames: dict[str, QvixSeries]
) -> dict[str, MetricValue]:
    """某交易日的 5 条 QVIX（本模块对 QVIX 的**唯一**计算入口）。

    - 该日 ≥ 该序列的**期权上市日**才产出该 key 的行（期权不存在 ⇒ 无隐含波动率可算，
      故上市日之前**不落行**；序列起点由 `extra.expected_from` 显式承载）。
    - 上市日之后上游缺这一天（发布滞后 / 历史偶发缺行）→ `value = NULL` +
      `source_not_updated`（**可见**，不静默跳过、不取最近值）。
    """
    out: dict[str, MetricValue] = {}
    for series, frame in series_frames.items():
        if day < frame.listed:
            continue
        extra: dict[str, Any] = {
            "series": series,
            "caliber": "close",
            "upstream": "akshare.index_option_*_qvix ← optbbs.com 静态 CSV",
            "source_tier_note": "第三方编制（官方 iVIX 自 2018-02-22 暂停发布）",
            "option_listed_from": frame.listed.isoformat(),
            "expected_from": frame.listed.isoformat(),
        }
        if day in frame.value_days:
            out[frame.metric_key] = MetricValue(
                frame.metric_key, frame.value_days[day], None, extra
            )
        else:
            out[frame.metric_key] = MetricValue(
                frame.metric_key, None, MISSING_SOURCE_NOT_UPDATED, extra
            )
    return out


# ── 股指期货升贴水（期货腿 + 现货腿）───────────────────────────────────


async def fetch_futures_continuous(
    client: Any, contract: str, day_lo: date, day_hi: date
) -> pd.DataFrame:
    """按 ≤3 年分片拉取一个连续合约的 `fut_daily`（**分片是硬要求**）。

    不分片时**不管起点写多早**都只回 2000 行（实测 `start_date=20180101` 仍只到
    20180711）⇒ 头部被静默截断、"看起来 ≥3 年"实际缺头。分片后每片 ≤~730 行。

    分片内若仍撞上限 → **响亮失败**（说明分片粒度不够，继续跑就是静默丢行）。
    """
    frames: list[pd.DataFrame] = []
    cursor = day_lo
    while cursor <= day_hi:
        chunk_hi = min(cursor + timedelta(days=FUT_DAILY_CHUNK_DAYS), day_hi)
        df = await client.fetch_fut_daily(
            ts_code=contract,
            start_date=cursor.strftime("%Y%m%d"),
            end_date=chunk_hi.strftime("%Y%m%d"),
        )
        if df is not None and len(df):
            if len(df) >= FUT_DAILY_ROW_CAP:
                raise RuntimeError(
                    f"fut_daily({contract}, {cursor}..{chunk_hi}) 返回 {len(df)} 行 = "
                    f"单次上限 {FUT_DAILY_ROW_CAP} —— 分片粒度不够，继续跑会**静默丢行**；"
                    "请调小 FUT_DAILY_CHUNK_DAYS"
                )
            frames.append(df)
        cursor = chunk_hi + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


async def fetch_spot_index(client: Any, spot_code: str, day_lo: date, day_hi: date) -> pd.DataFrame:
    """按 ≤5 年分片拉取一个现货指数的 `index_daily`（实测单次最多 6006 行）。"""
    frames: list[pd.DataFrame] = []
    cursor = day_lo
    while cursor <= day_hi:
        chunk_hi = min(cursor + timedelta(days=SPOT_INDEX_CHUNK_DAYS), day_hi)
        df = await client.fetch_index_daily(
            ts_code=spot_code,
            start_date=cursor.strftime("%Y%m%d"),
            end_date=chunk_hi.strftime("%Y%m%d"),
        )
        if df is not None and len(df):
            frames.append(df)
        cursor = chunk_hi + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


@dataclass(frozen=True)
class FuturesLeg:
    """一个品种的**两条腿**在某交易日的原始事实（`None` = 该腿当日无值）。"""

    product: str
    contract: str | None
    fut_settle: float | None
    fut_close: float | None
    spot_close: float | None
    #: 该品种主力连续的**结构起点**（= 该品种期货**首个挂牌日**，来自 `fut_basic.list_date`
    #: 的最小值；**与回补窗口无关** ⇒ 同一 (key, period) 的 `extra` 不随窗口漂移），
    #: 落进 `extra.expected_from`。
    expected_from: date
    #: 当日映射合约的最后交易日（`fut_basic.delist_date`，挂牌即已知 ⇒ 无前视）。
    delist_date: date | None = None
    multiplier: float | None = None


def basis_metrics_for_day(day: date, *, legs: dict[str, FuturesLeg]) -> dict[str, MetricValue]:
    """某交易日的 4 个基差 + 2 条贴水率（本模块对衍生品部分的**唯一**计算入口）。

    - 该日 ≥ 该品种的期货首日才产出行（`legs` 里只应含这类品种）。
    - 任一腿缺 → `value = NULL` + `futures_leg_missing` / `spot_index_missing`。
    - 现货收盘为 0 → `basis_spot_zero`（不落无穷）。
    - `|基差| / 现货 > 0.20` → `RuntimeError`（品种↔现货指数配错 / 量纲错的形态，
      必须响亮失败而不是把错值落库）。
    """
    out: dict[str, MetricValue] = {}
    for product, leg in legs.items():
        spot_code, spot_name = FUTURES_PRODUCTS[product][1], FUTURES_PRODUCTS[product][2]
        basis_key = BASIS_KEY_BY_PRODUCT[product]
        base_extra: dict[str, Any] = {
            "product": product,
            "contract": leg.contract,
            "spot_index": spot_code,
            "spot_index_name": spot_name,
            "sign_convention": "futures_minus_spot",
            "sign_hint": "正=升水（期货高于现货）负=贴水（期货低于现货）",
            "expected_from": leg.expected_from.isoformat(),
            "futures_source": "tushare.fut_daily(主力连续)",
            "spot_source": SPOT_INDEX_SOURCE,
        }
        if leg.multiplier is not None:
            base_extra["multiplier"] = leg.multiplier
        missing = None
        if leg.fut_settle is None:
            missing = MISSING_FUTURES_LEG
        elif leg.spot_close is None:
            missing = MISSING_SPOT_INDEX
        elif leg.spot_close == 0:
            missing = MISSING_BASIS_SPOT_ZERO
        if missing is not None:
            out[basis_key] = MetricValue(
                basis_key,
                None,
                missing,
                {
                    **base_extra,
                    "fut_settle": leg.fut_settle,
                    "fut_close": leg.fut_close,
                    "spot_close": leg.spot_close,
                },
            )
            for disc_key, (disc_product, disc_basis) in DISCOUNT_RATE_KEYS.items():
                if disc_product != product:
                    continue
                out[disc_key] = MetricValue(
                    disc_key,
                    None,
                    missing,
                    {
                        **base_extra,
                        "basis_metric_key": disc_basis,
                        "fut_settle": leg.fut_settle,
                        "spot_close": leg.spot_close,
                    },
                )
            continue

        assert leg.spot_close is not None and leg.fut_settle is not None  # for mypy
        basis = leg.fut_settle - leg.spot_close
        ratio = abs(basis) / leg.spot_close
        if ratio > MAX_ABS_BASIS_RATIO:
            raise RuntimeError(
                f"{product}@{day} 基差/现货 = {basis / leg.spot_close:+.2%} 超出合理上界 "
                f"{MAX_ABS_BASIS_RATIO:.0%}（结算价={leg.fut_settle} 现货={leg.spot_close}）"
                " —— 品种↔现货指数疑似配错或存在量纲错误，**不得**静默落库"
            )
        extra = {
            **base_extra,
            "fut_settle": leg.fut_settle,
            "fut_close": leg.fut_close,
            "spot_close": leg.spot_close,
            "formula": "fut_settle - spot_close",
        }
        if leg.fut_close is not None:
            # 收盘口径（与行情商/媒体引用的"升贴水"口径对拍用）。它 15:00 就已知，
            # 放在 `published_at=16:00` 的行里**不构成前视**。
            extra["basis_close"] = leg.fut_close - leg.spot_close
        if leg.delist_date is not None:
            days_to_delivery = (leg.delist_date - day).days
            if days_to_delivery > 0:
                extra["days_to_delivery"] = days_to_delivery
        out[basis_key] = MetricValue(basis_key, basis, None, extra)

        discount_rate = basis / leg.spot_close * 100.0
        disc_extra = {
            "product": product,
            "contract": leg.contract,
            "spot_index": spot_code,
            "spot_index_name": spot_name,
            "basis_metric_key": BASIS_KEY_BY_PRODUCT[product],
            "basis": basis,
            "spot_close": leg.spot_close,
            "formula": "基差 / 现货指数收盘 × 100（符号与基差一致：负=贴水）",
            "annualized_note": "本值为**不年化**（非年化）口径；年化口径见 annualized_pct",
            "sign_convention": "futures_minus_spot",
            "sign_hint": "正=升水（期货高于现货）负=贴水（期货低于现货）",
            "expected_from": leg.expected_from.isoformat(),
        }
        if "days_to_delivery" in extra:
            disc_extra["days_to_delivery"] = extra["days_to_delivery"]
            disc_extra["annualized_pct"] = (
                discount_rate * DAYS_PER_YEAR / float(extra["days_to_delivery"])
            )
        for disc_key, (disc_product, _disc_basis) in DISCOUNT_RATE_KEYS.items():
            if disc_product == product:
                out[disc_key] = MetricValue(disc_key, discount_rate, None, disc_extra)
    return out


# ── 落库 ───────────────────────────────────────────────────────────────


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
        # （ADR 0014「产地决定可信度」：有行 ≠ 有值，必须能区分）。
        "extra": extra or None,
        "published_at": published_at_for(day, _publish_time(metric.metric_key)),
        "caliber_version": VOLATILITY_DERIVATIVES_CALIBER_VERSION,
    }


@dataclass(frozen=True)
class BackfillStats:
    """一次区间落库的可核对统计（回补脚本直接打印它）。"""

    day_lo: date
    day_hi: date
    trade_days: int
    rows_upserted: int
    per_metric_covered: dict[str, int]
    per_metric_null: dict[str, int]
    missing_reasons: dict[str, dict[str, int]]
    sources: dict[str, str]
    #: 5 条 QVIX 序列各自被丢弃的**非交易日**上游行数之和（同源 CSV ⇒ 同一行计 5 次）。
    qvix_non_trading_rows_dropped: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "day_lo": self.day_lo.isoformat(),
            "day_hi": self.day_hi.isoformat(),
            "trade_days": self.trade_days,
            "rows_upserted": self.rows_upserted,
            "per_metric_covered": self.per_metric_covered,
            "per_metric_null": self.per_metric_null,
            "missing_reasons": self.missing_reasons,
            "sources": self.sources,
            "qvix_non_trading_rows_dropped": self.qvix_non_trading_rows_dropped,
        }


def _frame_by_day(frame: pd.DataFrame, value_column: str) -> dict[date, float]:
    """上游帧 → `{交易日: 数值}`（只收该列非空的行）。"""
    if frame is None or not len(frame):
        return {}
    out: dict[date, float] = {}
    for _, row in frame.iterrows():
        value = row.get(value_column)
        if value is None or pd.isna(value):
            continue
        out[_parse_day(row["trade_date"])] = float(value)
    return out


async def upsert_metric_range(
    db: AsyncSession,
    day_lo: date,
    day_hi: date,
    *,
    tushare: Any | None = None,
    akshare: Any | None = None,
) -> BackfillStats:
    """把 `[day_lo, day_hi]` 的 11 个指标算出来并**幂等**写进 `industry_metrics`。

    **落库天锚在各源自身的可得起点上**（Ruling 59）：

    - 交易日全集 = TuShare `trade_cal`(SSE) ∩ `[day_lo, day_hi]`（与两个源都无耦合）。
    - 5 条 QVIX：从**该 ETF 期权上市日**起逐交易日落行（上市前不落行 —— 没有期权就
      没有隐含波动率）；上市后上游缺的那天落 `NULL + source_not_updated`。
    - 4 个基差 / 2 条贴水率：从**该品种期货首个交易日**起逐交易日落行；任一腿缺 →
      `NULL + 对应 missing_reason`。

    幂等：唯一键 + `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0（ADR 0014 的 B 类）。
    调用方负责 `await db.commit()`（本层不替上层决定事务边界）。
    """
    from app.core.providers.akshare_client import get_akshare_client
    from app.core.providers.tushare_client import get_tushare_client

    ts_client = tushare or get_tushare_client()
    ak_client = akshare or get_akshare_client()

    trade_days = await fetch_trade_days(ts_client, day_lo, day_hi)
    trade_day_set = set(trade_days)

    # ① QVIX：逐序列一次调用（静态 CSV，无分页）。
    qvix_frames = await fetch_qvix_series(ak_client, day_lo, day_hi, trade_days=trade_day_set)
    non_trading_dropped = sum(f.non_trading_rows for f in qvix_frames.values())

    # ② 期货腿 + 现货腿 + 主力映射 + 合约元数据（fut_basic 的 delist_date/multiplier）。
    basic = await ts_client.fetch_fut_basic(exchange="CFFEX")
    if basic is None or not len(basic):
        raise RuntimeError("fut_basic(exchange='CFFEX') 返回 0 行 —— 合约元数据不可得")
    delist_by_code = {
        str(row["ts_code"]): _parse_day(row["delist_date"])
        for _, row in basic.iterrows()
        if not pd.isna(row.get("delist_date"))
    }
    multiplier_by_code = {
        str(row["ts_code"]): float(row["multiplier"])
        for _, row in basic.iterrows()
        if not pd.isna(row.get("multiplier"))
    }
    # 每个品种的**首个挂牌日**（`fut_basic.list_date` 的最小值）= 该品种的结构起点。
    # **用挂牌日而不是"窗口内首个有行日"**：后者会让同一 (key, period) 的 `extra` 随回补
    # 窗口变化（同一行两次回补写出不同的 `expected_from`），口径就不稳定了。
    first_listed: dict[str, date] = {}
    for _, row in basic.iterrows():
        code = str(row.get("fut_code") or "")
        if pd.isna(row.get("list_date")):
            continue
        listed = _parse_day(row["list_date"])
        first_listed[code] = min(first_listed.get(code, listed), listed)

    legs_by_day: dict[date, dict[str, FuturesLeg]] = {d: {} for d in trade_days}
    for product, (contract, spot_code, _spot_name) in FUTURES_PRODUCTS.items():
        fut_frame = await fetch_futures_continuous(ts_client, contract, day_lo, day_hi)
        if not len(fut_frame):
            logger.warning(
                "fut_daily(%s) 在 %s..%s 返回 0 行 —— 该品种本窗口无数据（不落行）",
                contract,
                day_lo,
                day_hi,
            )
            continue
        spot_frame = await fetch_spot_index(ts_client, spot_code, day_lo, day_hi)
        if not len(spot_frame):
            raise RuntimeError(
                f"index_daily({spot_code}) 在 {day_lo}..{day_hi} 返回 0 行 —— "
                f"{product} 的现货腿不可得（不得落「只有期货的基差」）"
            )
        mapping = await ts_client.fetch_fut_mapping(
            contract, start_date=day_lo.strftime("%Y%m%d"), end_date=day_hi.strftime("%Y%m%d")
        )
        contract_by_day = (
            {
                _parse_day(row["trade_date"]): str(row["mapping_ts_code"])
                for _, row in mapping.iterrows()
            }
            if mapping is not None and len(mapping)
            else {}
        )
        settle_by_day = _frame_by_day(fut_frame, "settle")
        close_by_day = _frame_by_day(fut_frame, "close")
        spot_by_day = _frame_by_day(spot_frame, "close")
        expected_from = first_listed.get(
            product, min(_parse_day(v) for v in fut_frame["trade_date"].tolist())
        )
        # 逐交易日组装（**含"上游缺这一天"的日子**：那一天该腿为 None → 落 NULL 行 +
        # `missing_reason`。缺数必须**可见**，不静默跳过、不取最近值）。
        for day in trade_days:
            if day < expected_from:
                continue
            mapped = contract_by_day.get(day)
            legs_by_day[day][product] = FuturesLeg(
                product=product,
                contract=mapped,
                fut_settle=settle_by_day.get(day),
                fut_close=close_by_day.get(day),
                spot_close=spot_by_day.get(day),
                expected_from=expected_from,
                delist_date=delist_by_code.get(mapped) if mapped else None,
                multiplier=multiplier_by_code.get(mapped) if mapped else None,
            )

    rows: list[dict[str, Any]] = []
    covered: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    nulls: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    reasons: dict[str, dict[str, int]] = {k: {} for k in ALL_METRIC_KEYS}

    def _tally(metric: MetricValue) -> None:
        key = metric.metric_key
        if metric.value is None:
            nulls[key] += 1
            if metric.missing_reason:
                reasons[key][metric.missing_reason] = reasons[key].get(metric.missing_reason, 0) + 1
        else:
            covered[key] += 1

    produced: set[str] = set()
    for day in trade_days:
        values = {
            **qvix_metrics_for_day(day, series_frames=qvix_frames),
            **basis_metrics_for_day(day, legs=legs_by_day.get(day, {})),
        }
        for key, metric in values.items():
            produced.add(key)
            _tally(metric)
            rows.append(_row(day, metric))
    if produced != set(INGEST_METRIC_KEYS):
        raise RuntimeError(
            "窗口内没有产出全部 11 个指标："
            f"只产出={sorted(produced)} 缺={sorted(set(INGEST_METRIC_KEYS) - produced)} —— "
            "窗口太窄（没覆盖某个源的结构起点）或计算入口漏了 key；**不得**静默少落指标"
        )

    upserted = await upsert_metric_rows(db, rows)
    return BackfillStats(
        day_lo=day_lo,
        day_hi=day_hi,
        trade_days=len(trade_days),
        rows_upserted=upserted,
        per_metric_covered=covered,
        per_metric_null=nulls,
        missing_reasons={k: dict(sorted(v.items())) for k, v in reasons.items()},
        sources={k: _source_for(k) for k in ALL_METRIC_KEYS},
        qvix_non_trading_rows_dropped=non_trading_dropped,
    )


async def latest_source_day(*, tushare: Any | None = None) -> date | None:
    """本模块**最新可得**的交易日（= 4 个期货连续合约的最大 `trade_date`）。

    回补窗口的右端锚在**数据**上而不是 `date.today()`：今天是休市日时按今天回补只会
    得到一片空（§S1.9 的同款纪律）。实测期货腿比 QVIX 的静态 CSV **新 3 个交易日**，
    故以期货腿为右端会让 QVIX 的那 3 天以 `NULL + source_not_updated` **可见地**落库
    —— 那正是"源侧滞后必须看得见"的要求。
    """
    from app.core.providers.tushare_client import get_tushare_client

    ts_client = tushare or get_tushare_client()
    probe_lo = date.today() - timedelta(days=30)
    best: date | None = None
    for _product, (contract, _spot_code, _spot_name) in FUTURES_PRODUCTS.items():
        frame = await ts_client.fetch_fut_daily(
            ts_code=contract,
            start_date=probe_lo.strftime("%Y%m%d"),
            end_date=date.today().strftime("%Y%m%d"),
        )
        if frame is None or not len(frame):
            continue
        latest = max(_parse_day(v) for v in frame["trade_date"].tolist())
        best = latest if best is None else max(best, latest)
    return best


async def backfill_volatility_derivatives(
    db: AsyncSession,
    *,
    day_hi: date | None = None,
    lookback_days: int = 6100,
) -> BackfillStats:
    """回补 `[day_hi - lookback_days, day_hi]` 的 11 个指标（日频序列）并提交。

    默认 6100 个日历日（≈16.7 年）：`day_hi=2026-10-12` 时 `day_lo≈2010-01`，**早于**
    两个源的全部可得起点（QVIX CSV 自 2015-02-09、IF 期货自 2010-04-16、IC/IH 自
    2015-04-16、IM 自 2022-07-22）。窗口刻意宽于源起点是**故意的**：落库天一律锚在各源
    自身的可得起点（`extra.expected_from`）上，窗口宽不会多落一行；反之窗口太窄会像
    Ruling 59 那样把 QVIX 的 11.6 年截成窗口长度。

    `day_hi=None` → 取本模块**最新可得**的交易日（期货腿的最大 `trade_date`，
    **不是** `date.today()`）。
    """
    if day_hi is None:
        day_hi = await latest_source_day()
        if day_hi is None:
            raise RuntimeError(
                "期货腿在最近 30 天内没有任何交易日 —— 无法确定回补右端"
                "（不得回退 date.today()：休市日会得到一片空）"
            )
    day_lo = day_hi - timedelta(days=lookback_days)
    stats = await upsert_metric_range(db, day_lo, day_hi)
    await db.commit()
    return stats


# ── 覆盖率与审计 ───────────────────────────────────────────────────────


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

    **分母（"该交易日"）的定义**（Ruling 59 的同款纪律：分母必须是本模块自己的输入域，
    不能被无关输入截断）：本模块 11 个 key 的**日期并集** = 各自源可得期内的交易日并集。
    对每个 key 再按**它自己的结构起点**（`extra.expected_from`，= 该 ETF 期权上市日 /
    该品种期货首个挂牌日）截取 —— 于是：

    - `null_days` = 该 key **有行但值为 NULL** 的交易日（带 `missing_reason`，可见）；
    - `missing_days` = 并集里 ≥ 该 key 结构起点、却**连行都没有**的交易日（= 回补没跑到
      / 写失败，是本模块自己的缺陷信号，**不是**上游缺数）。

    为什么不用 `daily_quotes`（§S1.9 的做法）：本模块的腿不依赖它（实测它只有
    2023-05-09 起的 829 天，会把 QVIX 的 11.6 年判成"覆盖 29%"）。
    """
    stmt = (
        select(
            IndustryMetric.metric_key,
            IndustryMetric.period,
            IndustryMetric.value,
            IndustryMetric.extra["missing_reason"].astext.label("reason"),
            IndustryMetric.extra["expected_from"].astext.label("expected_from"),
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

    by_metric: dict[str, list[Any]] = {k: [] for k in INGEST_METRIC_KEYS}
    for row in rows:
        by_metric.setdefault(row.metric_key, []).append(row)
    universe: set[date] = {r.period for r in rows}

    out: list[MetricCoverage] = []
    for key in INGEST_METRIC_KEYS:
        metric_rows = by_metric.get(key, [])
        periods = {r.period for r in metric_rows}
        covered = sum(1 for r in metric_rows if r.value is not None)
        nulls = len(metric_rows) - covered
        reasons: dict[str, int] = {}
        expected: list[date] = []
        for r in metric_rows:
            if r.expected_from:
                expected.append(date.fromisoformat(str(r.expected_from)))
            if r.value is None and r.reason:
                reasons[r.reason] = reasons.get(r.reason, 0) + 1
        if not periods:
            out.append(MetricCoverage(key, None, None, 0, 0, 0, [], {}))
            continue
        lower = min(expected) if expected else min(periods)
        upper = max(universe) if universe else max(periods)
        missing = sorted(d for d in universe if lower <= d <= upper and d not in periods)
        out.append(
            MetricCoverage(
                metric_key=key,
                period_from=min(periods),
                period_to=max(periods),
                rows=len(metric_rows),
                covered_days=covered,
                null_days=nulls,
                missing_days=missing,
                missing_reasons=dict(sorted(reasons.items())),
            )
        )
    return out


async def count_rows(db: AsyncSession) -> int:
    """本模块 11 个 key 在 `industry_key='market'` 下的行数（回补脚本的落库判据）。"""
    stmt = (
        select(func.count())
        .select_from(IndustryMetric)
        .where(
            IndustryMetric.industry_key == "market",
            IndustryMetric.stock_id == 0,
            IndustryMetric.metric_key.in_(INGEST_METRIC_KEYS),
        )
    )
    return int((await db.execute(stmt)).scalar_one())
