"""spec §S1.7 资金流四件套 —— 计算 + 落库（`industry_key='market'`）.

## 这个模块是什么

把 §S1.7 的**四件套**落成**日频序列**写进 `industry_metrics`
（`industry_key='market'`，`stock_id=0`），与 §S1.8/§S1.9/§S1.10/§S1.11 同表同读面。
读面**不在这里** —— 走 S1.2 已建好的 `GET /api/v1/metrics/latest` / `/series`
（registry 驱动 + 四徽章 + Redis 缓存）。**不新建表、不新建平行读路。**

四件套的落地方式**逐条不同**（这不是偷懒，是四条腿的源现状不同）：

| 腿 | `metric_key` | 本模块做什么 |
|---|---|---|
| 两融 | `margin_financing_balance` / `margin_short_balance` | **不重做**：S1.9 已落，只**引用** |
| 南向 | `southbound_net_buy` | **落库**（`south_money` 的**一阶差分**，见下） |
| ETF 份额变化 | `etf_share_change` | **落库**（六只宽基 ETF 的份额日变化之和） |
| 产业资本增减持 | `industrial_capital_net_change` | **只登记、不落库**（S3.2 未完成 → 待接）|

另有一条**同批次的登记**（不是四件套之一）：`northbound_holding_quarterly`
（陆股通持仓（季度））—— spec 要的是「频率标识为季度 + UI 明示滞后」，本轮**只落口径
与滞后说明**，ingest 与页面待后续批次（页面侧从未建过，见 §「不是重构」）。

## Step 0 结论（实机验证，2026-10-13；探针 `scripts/spike_s17_capital_flows_sources.py`）

- **北向每日净买入：已死**（本模块**不**碰它，也**不**删除既有能力）。
  断点前（2024-01-01…2024-08-16）`moneyflow_hsgt.north_money` 148 日有 **83 个负值**
  （净买入语义，可为负）；断点后（2024-08-19…2026-10-12）**502 日全非空、零负值、零零值**
  —— 该列**换了语义**（仍在日更的成交量口径），而独立产地（东财 `stock_hsgt_hist_em`）
  同期的北向**当日净买额 502 行全空** ⇒ 「北向每日净买入 2024-08-19 取消」**成立**。
  既有的 `northbound_daily` / `get_northbound_series` **已按 Ruling 62/63 改成成交额口径**
  （`amount` + 行级 `amount_kind`，见 `app/services/northbound_caliber.py`）：
  能力保留、数值一个不改，只是不再把断点后的成交额标成"净流入"。
- **南向：日频净买入**仍披露，但 `south_money` **不是**日频值 —— 它是
  **历史累计净买额（亿元）**。逐值对拍（东财 `stock_hsgt_hist_em`）：
  `south_money` == 东财「历史累计净买额」×1e4（**650/650 日**），
  一阶差分 == 东财「当日成交净买额」（**649/649 日**，单位同为亿元）。
  ⇒ `southbound_net_buy` = **差分**（不是直取），`extra` 带首尾累计值供手算复核。
- **ETF 份额**：TuShare `fund_share` 可得、日频；**单次 2000 行上限且截"最近"**
  ⇒ 必须**逐 ts_code** 取（宽窗会静默缺头）；六只宽基 ETF 各自 `20230103..20261012`
  **915–916 行、非空 100%、零重复日、零 0 值**（spec 的「≥1 年」被超额满足）。
- **产业资本增减持**：S3.2（公告采集：四类 + `content_entities` + `raw_hash`）**未落地**
  （无 `app/services/content_ingest/`、无 `content_items`；本 token 的 `anns_d` 也无权限）。
  ⇒ 本项**待接**，**一行都不落**。上游 `stk_holdertrade`（结构化增减持）实测可得
  （单日 3000 行已撞上限），但 spec 明写原料来自公告 ⇒ **换原料是控制器裁定权**，本轮不采用。
- **陆股通持仓（季度）**：`hk_hold` 季度末 **10/12** 可得（20240329 返回 0 行、
  20260930 只回港股通行），单次上限 **4200 行**，覆盖不规则 ⇒ 只落口径与滞后说明。

三态结论与逐条实机输出见 `docs/design/data-source.md` 附.9。

## 口径（写进 registry 与 `docs/design/metric-dictionary.md`）

- `etf_share_change`（亿份）= 六只宽基 ETF 的 `fd_share` **日变化之和**
  （`fd_share` 原生单位**万份**，÷1e4 → 亿份）。篮子 = 每个宽基指数族里份额最大的一只
  （沪深300 510300 / 上证50 510050 / 中证500 510500 / 中证1000 512100 /
  科创50 588000 / 创业板指 159915）。**为什么是"份额变化"而非份额**：份额变化 =
  净申购赎回，是「"国家队"护盘唯一可见痕迹」；份额水平本身的漂移不表达当日动作。
- `southbound_net_buy`（亿元）= `south_money(T) − south_money(前一个港股通交易日)`。
  **必须差分**：`south_money` 是**历史累计**净买额，直接落库会把累计值冒充日频净买入。
- `industrial_capital_net_change` / `northbound_holding_quarterly`：**无值**（见上）。

## 方法论禁令（Ruling 19）

**不得**把「香港中央結算（HKSCC）持仓」称作"外资" —— 它是**名义持有人汇总账户**，
持仓主体不可拆分。故本模块与本批次的全部命名/文案一律写「陆股通持仓」
（`northbound_holding_quarterly`），**不出现"外资"**。

## 缺数不静默为 0（照 §S1.8/§S1.9 的既有纪律）

- 缺数一律 `value = NULL` + `extra.missing_reason`（`None` = 无缺口），**绝不落 0**。
  0 份净申购是事实断言（"今天真的没有净申购"），与"今天没有数据"完全相反。
- 篮子**不完整即不落数字**：某只 ETF 当日或前一日无份额行 → NULL +
  `partial_basket_coverage`（`extra.n_members` / `n_with_share` / `missing_members`）。
  份额变化是**可加量**，缺一只成员时**不能**按覆盖率归一（那会得到一个假的"全市场净申购"）。
- 南向缺前一个港股通交易日 → NULL + `prev_connect_day_missing`（**不**回退到更早的行，
  那会把跨多日的累计变化记成"一天"）。
- 落库天锚在**各指标自身输入的可得日**上（Ruling 59）：ETF 锚在 `fund_share` 有值的日，
  南向锚在 `moneyflow_hsgt` 有行的日。**不**拿 `daily_quotes` 当日历底座 ——
  港股通交易日是 A 股交易日的**子集**（港股休市日无沪深港通数据），
  拿行情日历当分母会把结构性不可得的日子算成"缺数"。

## A/B/C 归类（ADR 0014）

两个落库指标都是 **B 类**（只用当日点内事实，可回算、允许回补）：
ETF 份额日变化只用 T 与 T−1 两行份额；南向净买入只用 T 与前一港股通交易日的两行累计值。
**没有 A 类**（不消费"只给当下"的盘中明细）、**没有 C 类**（不用成分快照回算）。

## PIT 纪律（ADR 0011 / Ruling 34-35）

`published_at` 按**产地各自的可得出处**填（不是一律 15:00，也不是回退 `period`）：

| 指标 | `published_at` | 依据 |
|---|---|---|
| `etf_share_change` | 当日 **21:00** | 交易所/基金管理人盘后清算时披露当日 ETF 份额（偏保守取值）|
| `southbound_net_buy` | 当日 **21:00** | 沪深港通当日成交/累计数据在收市后披露（偏保守取值）|

`caliber_version` 恒填 :data:`CAPITAL_FLOWS_CALIBER_VERSION`。

**落库前的 PIT 守卫**复用 `market_activity_service.upsert_metric_rows` ——
它拒绝任何缺 `published_at`（或该列非带时区 datetime）/ 缺 `caliber_version` 的行
（这两列在表里可空，漏带会**静默**落 NULL；Ruling 59 的原始事故形态）。
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
from app.services.industry_registry import (
    CAPITAL_FLOWS_ALL_KEYS,
    CAPITAL_FLOWS_CALIBER_VERSION,
    CAPITAL_FLOWS_METRIC_KEYS,
    CAPITAL_FLOWS_PENDING_KEYS,
    ETF_SHARE_SOURCE,
    INDUSTRIAL_CAPITAL_SOURCE,
    NORTHBOUND_HOLDING_SOURCE,
    SOUTHBOUND_CUMULATIVE_FROM,
    SOUTHBOUND_SOURCE,
)
from app.services.market_activity_service import upsert_metric_rows

logger = logging.getLogger(__name__)

_CN_TZ = ZoneInfo("Asia/Shanghai")

#: ETF 份额的可得时点（交易所/基金管理人盘后披露当日份额；偏保守）。
ETF_SHARE_PUBLISH = dtime(21, 0)
#: 沪深港通当日数据的可得时点（收市后披露；偏保守）。
CONNECT_FLOW_PUBLISH = dtime(21, 0)

#: 万份 → 亿份（`fund_share.fd_share` 原生单位是**万份**）。
WAN_PER_YI = 1e4

#: `fund_share` 单次 2000 行上限（Step-0 实测）：逐代码取，一次取满 2000 行即可覆盖
#: 约 8 年，故不需要分片；但**必须**逐代码（宽窗只回最新 2000 行）。
FUND_SHARE_ROW_CAP = 2000

#: `moneyflow_hsgt` 单次 300 行上限（Step-0 实测：宽窗只回最近 300 行，**截最近**）。
#: 分片按 200 个日历日（≈135 行）留足余量。
HSGT_ROW_CAP = 300
HSGT_CHUNK_DAYS = 200

#: 计算 T 日变化时需要 T−1 日的值：向前多取这么多**日历日**（长假也够）。
PREV_LOOKBACK_DAYS = 20

#: 「口径回退守卫」的累计值下界（亿元）。断点后的 `south_money` 是**历史累计净买额**，
#: 实测最低 28811.25（2023-11-28）且此后一路上行；而旧口径（日频净买入/百万元）的
#: 日值绝对值最大 13833.55、且 114 日里有 37 日为负。故：
#: - `south_money <= 0` → 只可能是口径被改回日频（累计净买额不可能为负/为零）；
#: - `south_money < 10000` → 高度可疑（累计值掉到 1 万亿需要约 1.9 万亿净卖出）。
#: **这是启发式探测器，不是证明**：它能挡住"改回日频"与"改回小额口径"两种已知形态，
#: 未验证的第三种形态见 `docs/design/data-source.md` 附.9 的未验证余项。
SOUTHBOUND_CUMULATIVE_FLOOR = 10000.0

#: `extra.missing_reason` 取值（与 registry 的 `CAPITAL_FLOWS_MISSING_REASONS` 同源语义）。
MISSING_SOURCE_EMPTY = "source_empty"
MISSING_PARTIAL_BASKET_COVERAGE = "partial_basket_coverage"
MISSING_PREV_CONNECT_DAY = "prev_connect_day_missing"
#: 上一个交易日的值缺失（差分跨了缺口）—— 与"缺前一个交易日"分开：修复动作不同。
MISSING_PREV_VALUE = "prev_value_missing"
#: 基金在窗口内没有前一个份额日（序列起点）—— 与"篮子今天不全"分开。
MISSING_NO_PREV_FUND_DAY = "no_prev_fund_day"
#: 待接（S3.2 未完成）—— 只用于登记与覆盖率对账，**不落任何行**。
MISSING_UPSTREAM_PENDING = "upstream_dependency_pending"
#: 陆股通持仓（季度）：本轮只落口径，未接 ingest。
MISSING_NOT_INGESTED = "not_ingested_page_pending"


@dataclass(frozen=True)
class EtfBasketMember:
    """宽基 ETF 篮子的一名成员（族名 / ts_code / 基金全名）。"""

    family: str
    ts_code: str
    name: str


#: 宽基 ETF 篮子：**一族一只**（每个宽基指数族里**规模最大**者）。
#:
#: 依据 = Step-0 的实机排名（`spike_s17_capital_flows_sources.py` 的 `[RANK]` 行，
#: 快照日 2026-10-12，规模 = `fd_share` × `fund_daily.close`）：华泰柏瑞沪深300ETF
#: **1069.5 亿元** / 华夏上证50ETF **208.7 亿** / 南方中证500ETF **404.5 亿** /
#: 南方中证1000ETF **230.3 亿** / 华夏上证科创板50成份ETF **952.3 亿** /
#: 易方达创业板ETF **656.9 亿**。
#: ⚠️ **不能只比份额**：低净值的行业主题 ETF 份额更大而规模更小（实测
#: `512010.SH` 沪深300医药卫生ETF 有 387 万份却只有 147 亿元）。
#: ⚠️ 选篮**不能**只看 `fund_basic.benchmark` 的文本：`510050.SH` 的 benchmark 是
#: 「上海证券交易所50成份指数×100%」、`510500.SH` 是「中证小盘500指数×100%」——
#: 按指数名做文本匹配会把这两只族内规模最大者漏掉（探针的 `[MISS]` 行实测漏掉，
#: 并打印原文；探针同时断言"选定者规模不低于文本命中集最大者"）。
#:
#: **已知口径收窄（登记为限制，不掩盖）**：沪深300 族里还有 `510310.SH` 1,211,374.4 与
#: `159919.SZ` 685,641.7 两只大 ETF（2024 年汇金增持的品种之一）**未纳入**篮子；
#: 本指标因此表达的是"六只代表性宽基 ETF 的合计净申购"，**不是**"全部宽基 ETF"。
ETF_BASKET: tuple[EtfBasketMember, ...] = (
    EtfBasketMember("沪深300", "510300.SH", "华泰柏瑞沪深300ETF"),
    EtfBasketMember("上证50", "510050.SH", "华夏上证50ETF"),
    EtfBasketMember("中证500", "510500.SH", "南方中证500ETF"),
    EtfBasketMember("中证1000", "512100.SH", "南方中证1000ETF"),
    EtfBasketMember("科创50", "588000.SH", "华夏上证科创板50成份ETF"),
    EtfBasketMember("创业板指", "159915.SZ", "易方达创业板ETF"),
)

#: `units` 随 metric_key 固定在 registry（字典 §4.2 规则 3）；本表是 registry 的投影，
#: 由 `tests/test_capital_flows_s17.py` 机械比对，禁止在这里"顺手改单位"。
UNITS: dict[str, str | None] = {
    "etf_share_change": "亿份",
    "southbound_net_buy": "亿元",
}

#: 本模块**实际落库**的 key（= registry 的 ingest 面）。
INGEST_METRIC_KEYS: tuple[str, ...] = tuple(UNITS)
#: 本模块涉及的全部 key（含待接与只登记口径的两条）。
ALL_METRIC_KEYS: tuple[str, ...] = (
    tuple(UNITS) + CAPITAL_FLOWS_PENDING_KEYS + ("northbound_holding_quarterly",)
)

#: `source` / `source_tier`（产地：ETF 份额与沪深港通数据都产自交易所披露，
#: 但本模块落的是**本地差分**值 → `source_tier='derived'`，与 S1.9 的
#: `margin_financing_buy_ratio` / `erp_10y` 同规矩）。
_SOURCES: dict[str, str] = {
    "etf_share_change": ETF_SHARE_SOURCE,
    "southbound_net_buy": SOUTHBOUND_SOURCE,
    "industrial_capital_net_change": INDUSTRIAL_CAPITAL_SOURCE,
    "northbound_holding_quarterly": NORTHBOUND_HOLDING_SOURCE,
}


def _assert_registry_keys_match() -> None:
    """本模块的 key 集必须与 registry 的登记项**逐字相等**（导入时当场失败）。

    registry 是白名单（未登记 key 会被读/写路径拒绝），故"服务要落 a、registry 只登记 b"
    会让落库在写入路径上失败或让读面 404。
    """
    if set(ALL_METRIC_KEYS) != set(CAPITAL_FLOWS_ALL_KEYS):
        raise RuntimeError(
            "capital_flows_service 与 registry 的 metric_key 集不一致："
            f"只在服务={sorted(set(ALL_METRIC_KEYS) - set(CAPITAL_FLOWS_ALL_KEYS))} "
            f"只在 registry={sorted(set(CAPITAL_FLOWS_ALL_KEYS) - set(ALL_METRIC_KEYS))}"
        )
    if set(INGEST_METRIC_KEYS) != set(CAPITAL_FLOWS_METRIC_KEYS):
        raise RuntimeError(
            "本模块**实际落库**的 key 与 registry 声明的 ingest 面不一致："
            f"只在服务={sorted(set(INGEST_METRIC_KEYS) - set(CAPITAL_FLOWS_METRIC_KEYS))} "
            f"只在 registry={sorted(set(CAPITAL_FLOWS_METRIC_KEYS) - set(INGEST_METRIC_KEYS))}"
        )
    if len(ETF_BASKET) < 3:
        raise RuntimeError(
            f"宽基 ETF 篮子至少要 3 只（spec 要「宽基」而非单只），登记了 {len(ETF_BASKET)} 只"
        )
    dup = {m.ts_code for m in ETF_BASKET}
    if len(dup) != len(ETF_BASKET):
        raise RuntimeError("宽基 ETF 篮子里有重复的 ts_code —— 会把同一只基金数两遍")
    fams = {m.family for m in ETF_BASKET}
    if len(fams) != len(ETF_BASKET):
        raise RuntimeError("宽基 ETF 篮子里有重复的指数族 —— 一个族只应取一只")


_assert_registry_keys_match()


def published_at_for(day: date, at: dtime) -> datetime:
    """交易日 + 时点 → 带时区的 `published_at`（non-naive；Ruling 35 的反面）。"""
    return datetime.combine(day, at, tzinfo=_CN_TZ)


@dataclass(frozen=True)
class MetricValue:
    """一个指标在一天的结果：值 + 缺口原因 + 可复核的分母/原始量。"""

    metric_key: str
    value: float | None
    missing_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _parse_day(raw: Any) -> date:
    """TuShare 的 `YYYYMMDD` 字符串 / `date` / `datetime` → `date`。"""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text_raw = str(raw).strip()
    if len(text_raw) == 8 and text_raw.isdigit():
        return datetime.strptime(text_raw, "%Y%m%d").date()
    return datetime.fromisoformat(text_raw).date()


# ── ETF 份额变化 ─────────────────────────────────────────────────────────


async def fetch_basket_shares(
    client: Any, day_lo: date, day_hi: date
) -> dict[str, dict[date, float]]:
    """逐篮成员取 `fund_share`，返回 `{ts_code: {交易日: fd_share(万份)}}`。

    **逐代码**是硬要求：`fund_share` 单次 2000 行且超限**截最近**，宽窗会静默缺头。
    """
    out: dict[str, dict[date, float]] = {}
    for member in ETF_BASKET:
        df = await client.fetch_fund_share(
            ts_code=member.ts_code,
            start_date=day_lo.strftime("%Y%m%d"),
            end_date=day_hi.strftime("%Y%m%d"),
        )
        per_day: dict[date, float] = {}
        if df is not None and len(df):
            for _, row in df.iterrows():
                raw = row.get("fd_share")
                if raw is None or pd.isna(raw):
                    continue
                per_day[_parse_day(row["trade_date"])] = float(raw)
        if len(per_day) > FUND_SHARE_ROW_CAP:
            raise RuntimeError(
                f"fund_share({member.ts_code}) 单次回了 {len(per_day)} 行 > 上限 "
                f"{FUND_SHARE_ROW_CAP} —— 说明上游截断了窗口，回补区间必须收窄/分片，"
                "**不得**把截断后的序列当完整序列落库。"
            )
        out[member.ts_code] = per_day
    if sum(len(v) for v in out.values()) == 0:
        raise RuntimeError(
            "fund_share 对篮子的**全部**成员都返回了 0 行 —— 上游异常/限频/窗口错。"
            "**不得**静默落一片 NULL（那是把采集失败伪装成缺数）；请重跑。"
        )
    return out


def etf_share_change_by_day(
    shares: dict[str, dict[date, float]], day_lo: date, day_hi: date
) -> dict[date, MetricValue]:
    """篮子份额的**逐日净变化**（亿份）。

    - 落库天 = 篮子各成员可得日的**并集** ∩ `[day_lo, day_hi]`（锚在自身输入上）。
    - 逐成员取**该成员自己的**前一个可得日做差（份额序列在自家交易日上连续，
      用别人的日历会在停牌/无数据日算出跨多日的假变化）。
    - **任一成员缺当日或前一日 → NULL + `partial_basket_coverage`**：
      份额变化是可加量，缺成员时按覆盖率归一会得到一个假的"全市场净申购"。
    """
    per_member_prev: dict[str, dict[date, date]] = {}
    all_days: set[date] = set()
    for code, per_day in shares.items():
        days = sorted(per_day)
        prev_of: dict[date, date] = {}
        for i in range(1, len(days)):
            prev_of[days[i]] = days[i - 1]
        per_member_prev[code] = prev_of
        all_days.update(days)

    out: dict[date, MetricValue] = {}
    for day in sorted(d for d in all_days if day_lo <= d <= day_hi):
        det: list[dict[str, Any]] = []
        total_wan = 0.0
        missing: list[str] = []
        no_prev: list[str] = []
        for member in ETF_BASKET:
            per_day = shares[member.ts_code]
            cur = per_day.get(day)
            prev_day = per_member_prev[member.ts_code].get(day)
            prev = per_day[prev_day] if prev_day is not None else None
            if cur is not None and prev_day is None:
                # 序列起点（窗口内这只基金还没有前一个份额日）：变化量结构性不可得，
                # 与"篮子今天不全"是两回事 —— 混成一个原因码会稀释守卫的语义。
                no_prev.append(member.ts_code)
            if cur is None or prev is None:
                missing.append(member.ts_code)
                det.append(
                    {
                        "ts_code": member.ts_code,
                        "family": member.family,
                        "name": member.name,
                        "fd_share_wan": cur,
                        "prev_fd_share_wan": prev,
                        "prev_trade_date": prev_day.isoformat() if prev_day else None,
                        "delta_wan": None,
                    }
                )
                continue
            delta = cur - prev
            total_wan += delta
            det.append(
                {
                    "ts_code": member.ts_code,
                    "family": member.family,
                    "name": member.name,
                    "fd_share_wan": cur,
                    "prev_fd_share_wan": prev,
                    "prev_trade_date": prev_day.isoformat() if prev_day else None,
                    "delta_wan": delta,
                }
            )
        n_members = len(ETF_BASKET)
        n_with = n_members - len(missing)
        extra: dict[str, Any] = {
            "caliber": "sum_of_daily_net_share_creation",
            "unit_native": "万份",
            "basket": [m.ts_code for m in ETF_BASKET],
            "n_members": n_members,
            "n_with_share": n_with,
            "member_coverage": round(n_with / n_members, 6),
            "total_share_wan": round(sum(d["fd_share_wan"] or 0.0 for d in det), 4),
            "delta_wan": round(total_wan, 4),
            "by_member": det,
            "upstream": "tushare.fund_share",
        }
        if no_prev:
            extra["missing_members"] = missing
            extra["members_without_prev_day"] = no_prev
            out[day] = MetricValue("", None, MISSING_NO_PREV_FUND_DAY, extra)
            continue
        if missing:
            extra["missing_members"] = missing
            out[day] = MetricValue("", None, MISSING_PARTIAL_BASKET_COVERAGE, extra)
            continue
        out[day] = MetricValue("", total_wan / WAN_PER_YI, None, extra)
    return out


# ── 南向资金（moneyflow_hsgt.south_money 的一阶差分）─────────────────────


async def fetch_hsgt_frame(client: Any, day_lo: date, day_hi: date) -> pd.DataFrame:
    """按 ≤200 个日历日分片拉 `moneyflow_hsgt` 并拼接。

    分片是**硬要求**：该接口单次只回 300 行且超限**截最近**，宽窗会把早期整段
    静默丢掉（Step-0 实测：`20240101→20261016` 只回 300 行、最早 `20250704`）。
    """
    frames: list[pd.DataFrame] = []
    cursor = day_lo
    while cursor <= day_hi:
        chunk_hi = min(cursor + timedelta(days=HSGT_CHUNK_DAYS), day_hi)
        df = await client.fetch_moneyflow_hsgt(
            start_date=cursor.strftime("%Y%m%d"), end_date=chunk_hi.strftime("%Y%m%d")
        )
        if df is not None and len(df):
            if len(df) >= HSGT_ROW_CAP:
                raise RuntimeError(
                    f"moneyflow_hsgt({cursor}..{chunk_hi}) 回了 {len(df)} 行（撞上限 "
                    f"{HSGT_ROW_CAP}）—— 分片太宽，必须收窄 HSGT_CHUNK_DAYS，"
                    "**不得**把截断后的序列当完整序列落库。"
                )
            frames.append(df)
        cursor = chunk_hi + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def southbound_net_buy_by_day(
    frame: pd.DataFrame, day_lo: date, day_hi: date
) -> dict[date, MetricValue]:
    """`south_money`（**历史累计净买额**，亿元）的**一阶差分** → 日频净买入（亿元）。

    **为什么必须差分**（Step-0 实证）：`south_money` 与东财「历史累计净买额」逐值相等
    （650/650 日），其差分与东财「当日成交净买额」逐值相等（649/649 日）。
    直取该列当"当日净买入"就是把累计值冒充日频值 —— 数值形态上完全不像
    （累计序列几乎只增不减），但字段名会骗过读的人。

    - 落库天 = 上游**有行**的日 ∩ `[day_lo, day_hi]`（锚在自身输入上；港股通交易日是
      A 股交易日的子集，拿行情日历当分母会把港股休市日算成缺数）。
    - 前一个交易日取**上游序列里的前一行**（即使它在 `day_lo` 之前 —— 调用方为此多取了
      `PREV_LOOKBACK_DAYS`）。若确实没有前一行 → NULL + `prev_connect_day_missing`，
      **不**回退到更早的行（那会把跨多日的累计变化记成"一天"）。
    - **口径断点守卫**（v2 的关键修正）：早于
      :data:`industry_registry.SOUTHBOUND_CUMULATIVE_FROM` 的行**整段丢弃** ——
      该列在那天之前是**日频净买入（百万元）**，混进来差分得到的是二阶差分（纯噪声）。
      断点后若出现 `<= 0` 或不合理小值 ⇒ **响亮失败**（上游把口径改回去了的信号）。
    """
    if frame is None or not len(frame):
        return {}
    rows: list[tuple[date, float | None]] = []
    dropped = 0
    for _, row in frame.iterrows():
        day = _parse_day(row["trade_date"])
        raw = row.get("south_money")
        value = None if raw is None or pd.isna(raw) else float(raw)
        if day < SOUTHBOUND_CUMULATIVE_FROM:
            # 旧口径（日频净买入/百万元）：**丢弃**，绝不参与差分
            dropped += 1
            continue
        rows.append((day, value))
    if dropped:
        logger.warning(
            "south_money 丢弃 %d 行旧口径（< %s）：该列在断点前是日频净买入，混算会得到二阶差分",
            dropped,
            SOUTHBOUND_CUMULATIVE_FROM,
        )
    bad = [(d, v) for d, v in rows if v is not None and v <= SOUTHBOUND_CUMULATIVE_FLOOR]
    if bad:
        raise RuntimeError(
            f"南向口径回退守卫触发：{len(bad)} 行在 {SOUTHBOUND_CUMULATIVE_FROM} 之后的 "
            f"`south_money` <= {SOUTHBOUND_CUMULATIVE_FLOOR}（前几个：{bad[:3]}）—— "
            "该列应是**历史累计净买额（亿元）**且实测最低 28811.25。出现 ≤ 阈值 的值只可能是"
            "上游把口径改回了日频/小额口径。**禁止**继续差分落库（那会产出噪声序列），"
            "请重核 data-source.md 附.9 的口径结论。"
        )
    rows.sort(key=lambda r: r[0])

    out: dict[date, MetricValue] = {}
    prev_day: date | None = None
    prev_value: float | None = None
    for day, value in rows:
        if day_lo <= day <= day_hi:
            base: dict[str, Any] = {
                "caliber": "first_difference_of_cumulative_net_buy",
                "cumulative_yi": value,
                "prev_trade_date": prev_day.isoformat() if prev_day else None,
                "prev_cumulative_yi": prev_value,
                "upstream": "tushare.moneyflow_hsgt(south_money, 历史累计净买额/亿元)",
            }
            if value is None:
                out[day] = MetricValue("", None, MISSING_SOURCE_EMPTY, base)
            elif prev_day is None:
                out[day] = MetricValue("", None, MISSING_PREV_CONNECT_DAY, base)
            elif prev_value is None:
                # 上一个交易日的值缺失 ⇒ 差分跨了缺口：落 NULL，**不**回退到更早的行
                # （把跨多日的累计变化记成"一天"是静默错标）。
                out[day] = MetricValue("", None, MISSING_PREV_VALUE, base)
            else:
                out[day] = MetricValue("", value - prev_value, None, base)
        # 无论值是否缺失都推进基准：缺失日**要**成为下一天的基准（值为 None），
        # 于是下一天落 `prev_value_missing` 而不是拿更早的行算出一个跨多日的差分。
        prev_day, prev_value = day, value
    return out


# ── 落库 ───────────────────────────────────────────────────────────────


def _publish_time(metric_key: str) -> dtime:
    if metric_key == "etf_share_change":
        return ETF_SHARE_PUBLISH
    return CONNECT_FLOW_PUBLISH


_TIERS: dict[str, str] = {
    "etf_share_change": "derived",
    "southbound_net_buy": "derived",
    "industrial_capital_net_change": "manual",
    "northbound_holding_quarterly": "official",
}


def _row(day: date, metric: MetricValue) -> dict[str, Any]:
    """`MetricValue` → `industry_metrics` 行（scope='market'，`stock_id=0`）。"""
    extra = dict(metric.extra)
    if metric.missing_reason is not None:
        extra["missing_reason"] = metric.missing_reason
    return {
        "industry_key": "market",
        "stock_id": 0,
        "metric_key": metric.metric_key,
        "source": _SOURCES[metric.metric_key],
        "source_tier": _TIERS[metric.metric_key],
        "freq": "daily",
        "period": day,
        "value": metric.value,
        "unit": UNITS[metric.metric_key],
        # NULL 值的行**也要留 extra**：`missing_reason` 是"为什么没有值"的唯一证据
        # （ADR 0014「有行 ≠ 有值」，必须能区分）。
        "extra": extra or None,
        "published_at": published_at_for(day, _publish_time(metric.metric_key)),
        "caliber_version": CAPITAL_FLOWS_CALIBER_VERSION,
    }


async def upsert_capital_flows_range(
    db: AsyncSession, day_lo: date, day_hi: date, *, tushare: Any | None = None
) -> BackfillStats:
    """把 `[day_lo, day_hi]` 的两个可落库指标算出来并**幂等**写进 `industry_metrics`。

    - `etf_share_change` / `southbound_net_buy`：见各自的 `*_by_day`。
    - `industrial_capital_net_change`：**不落任何行**（S3.2 未完成，spec 要求待接）。
    - `northbound_holding_quarterly`：**不落任何行**（本轮只登记口径与滞后说明）。

    `published_at` / `caliber_version` 由 :func:`_row` 填满，落库前经
    `market_activity_service.upsert_metric_rows` 的 **PIT 守卫**（缺任一列即响亮失败）。

    幂等：唯一键 + `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0。
    调用方负责 `await db.commit()`（本层不替上层决定事务边界）。
    """
    from app.core.providers.tushare_client import get_tushare_client

    client = tushare or get_tushare_client()

    # 计算 T 日变化需要 T−1 日：两路取数都向前多取一点。
    fetch_lo = day_lo - timedelta(days=PREV_LOOKBACK_DAYS)
    shares = await fetch_basket_shares(client, fetch_lo, day_hi)
    etf = etf_share_change_by_day(shares, day_lo, day_hi)
    # 南向的取数下界还要受**口径断点**约束：更早的整段是另一种口径，取了也会被丢弃。
    south_lo = max(day_lo, SOUTHBOUND_CUMULATIVE_FROM) - timedelta(days=PREV_LOOKBACK_DAYS)
    hsgt = await fetch_hsgt_frame(client, south_lo, day_hi)
    south = southbound_net_buy_by_day(hsgt, day_lo, day_hi)
    clamped: dict[str, str] = {}
    if day_lo < SOUTHBOUND_CUMULATIVE_FROM:
        clamped["southbound_net_buy"] = (
            f"落库窗口下界被口径断点抬到 {SOUTHBOUND_CUMULATIVE_FROM.isoformat()}"
            "（该列此前是日频净买入/百万元，差分无意义）"
        )

    rows: list[dict[str, Any]] = []
    covered: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    nulls: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    reasons: dict[str, dict[str, int]] = {k: {} for k in ALL_METRIC_KEYS}

    def _tally(key: str, metric: MetricValue) -> None:
        if metric.value is None:
            nulls[key] += 1
            if metric.missing_reason:
                reasons[key][metric.missing_reason] = reasons[key].get(metric.missing_reason, 0) + 1
        else:
            covered[key] += 1

    for day, metric in sorted(etf.items()):
        key = "etf_share_change"
        _tally(key, metric)
        rows.append(_row(day, MetricValue(key, metric.value, metric.missing_reason, metric.extra)))
    for day, metric in sorted(south.items()):
        key = "southbound_net_buy"
        _tally(key, metric)
        rows.append(_row(day, MetricValue(key, metric.value, metric.missing_reason, metric.extra)))

    # 待接 / 只登记口径的两条：**不落行**，但把"为什么没有"按 key 记进统计里，
    # 免得读的人以为"漏了跑"（**不**往 `missing_reasons` 里塞假的天数计数）。
    pending = {k: MISSING_UPSTREAM_PENDING for k in CAPITAL_FLOWS_PENDING_KEYS}
    pending["northbound_holding_quarterly"] = MISSING_NOT_INGESTED

    if not rows:
        # **响亮失败**：可落库的两条一条都没产出，只可能是上游异常（取数全空）
        # 或窗口参数错。静默返回 0 行会让"回补跑过了"与"回补没跑"长得一样。
        raise RuntimeError(
            f"{day_lo}..{day_hi} 两个可落库指标一条行都没产出 —— 上游取数异常或窗口错，"
            "**不得**静默成功（那会把采集失败伪装成缺数）。请重跑或收窄窗口。"
        )

    upserted = await upsert_metric_rows(db, rows)

    day_set = {r["period"] for r in rows}
    return BackfillStats(
        day_lo=day_lo,
        day_hi=day_hi,
        input_days=len(day_set),
        rows_upserted=upserted,
        per_metric_covered=covered,
        per_metric_null=nulls,
        missing_reasons={k: dict(sorted(v.items())) for k, v in reasons.items()},
        pending=pending,
        clamped=clamped,
        sources={k: _SOURCES[k] for k in ALL_METRIC_KEYS},
    )


@dataclass(frozen=True)
class BackfillStats:
    """一次区间落库的可核对统计（回补脚本直接打印它）。"""

    day_lo: date
    day_hi: date
    input_days: int
    rows_upserted: int
    per_metric_covered: dict[str, int]
    per_metric_null: dict[str, int]
    missing_reasons: dict[str, dict[str, int]]
    #: **没有落库**的 key → 原因（待接 S3.2 / 只登记口径）。有值不代表有行，反之亦然。
    pending: dict[str, str]
    #: 落库窗口被**口径断点/上游限制**抬高的 key → 说明（Ruling 34：收窄必须显式）。
    clamped: dict[str, str]
    sources: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "day_lo": self.day_lo.isoformat(),
            "day_hi": self.day_hi.isoformat(),
            "input_days": self.input_days,
            "rows_upserted": self.rows_upserted,
            "per_metric_covered": self.per_metric_covered,
            "per_metric_null": self.per_metric_null,
            "missing_reasons": self.missing_reasons,
            "pending": self.pending,
            "clamped": self.clamped,
            "sources": self.sources,
        }


async def backfill_capital_flows(
    db: AsyncSession,
    *,
    day_hi: date | None = None,
    lookback_days: int = 1400,
) -> BackfillStats:
    """回补 `[day_hi - lookback_days, day_hi]` 的两个指标（日频序列）并提交。

    默认 1400 个日历日：`day_hi=2026-10-12` 时 `day_lo≈2022-12-12`，
    篮子份额的可得起点是 **2023-01-03**（实测 915 行 ≈ 3.8 年，≥ spec §S1.7 的「≥1 年」）。

    `day_hi=None` → 取上游 `fund_share` 的 `max(trade_date)`（**不是** `date.today()`：
    休市日/尚未披露时会得到一片空）。
    """
    if day_hi is None:
        day_hi = await latest_etf_share_day()
    day_lo = day_hi - timedelta(days=lookback_days)
    stats = await upsert_capital_flows_range(db, day_lo, day_hi)
    await db.commit()
    return stats


async def latest_etf_share_day(*, tushare: Any | None = None) -> date:
    """上游 `fund_share` 的最近可得日（第一个篮成员的 `max(trade_date)`）。

    回补窗口锚在**数据**上而不是 `date.today()`：今天休市 / 上游尚未披露时，
    按今天回补只会得到一片空。
    """
    from app.core.providers.tushare_client import get_tushare_client

    client = tushare or get_tushare_client()
    member = ETF_BASKET[0]
    df = await client.fetch_fund_share(ts_code=member.ts_code, start_date="20200101")
    if df is None or not len(df):
        raise RuntimeError(
            f"fund_share({member.ts_code}) 返回 0 行 —— 无法确定回补上界（上游异常/限频）"
        )
    return max(_parse_day(d) for d in df["trade_date"].tolist())


# ── 覆盖率对账与 PIT 审计 ───────────────────────────────────────────────


@dataclass(frozen=True)
class MetricCoverage:
    """一个指标的覆盖率对账（缺数可见性的对外形状）。"""

    metric_key: str
    period_from: date | None
    period_to: date | None
    rows: int
    covered_days: int
    null_days: int
    missing_reasons: dict[str, int]
    pending_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "period_from": self.period_from.isoformat() if self.period_from else None,
            "period_to": self.period_to.isoformat() if self.period_to else None,
            "rows": self.rows,
            "covered_days": self.covered_days,
            "null_days": self.null_days,
            "missing_reasons": self.missing_reasons,
            "pending_reason": self.pending_reason,
        }


async def capital_flows_coverage(
    db: AsyncSession, *, day_lo: date | None = None, day_hi: date | None = None
) -> list[MetricCoverage]:
    """逐指标的覆盖率：`covered_days` / `null_days` / `missing_reasons`。

    **分母不用 `daily_quotes`**（与 S1.9 的 `metric_coverage` 不同，这是有意的）：
    这两个指标的落库天锚在**自身输入**上，且港股通交易日是 A 股交易日的**子集** ——
    拿行情日历当分母会把"港股休市（结构性不可得）"算成"缺数"。
    真缺口的证据是 `rows`（有行但 `value IS NULL`）+ `missing_reasons`。
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
            IndustryMetric.metric_key.in_(ALL_METRIC_KEYS),
        )
        .order_by(IndustryMetric.metric_key, IndustryMetric.period)
    )
    if day_lo is not None:
        stmt = stmt.where(IndustryMetric.period >= day_lo)
    if day_hi is not None:
        stmt = stmt.where(IndustryMetric.period <= day_hi)
    rows = (await db.execute(stmt)).all()

    by_metric: dict[str, list[Any]] = {k: [] for k in ALL_METRIC_KEYS}
    for row in rows:
        by_metric.setdefault(row.metric_key, []).append(row)

    pending = {k: MISSING_UPSTREAM_PENDING for k in CAPITAL_FLOWS_PENDING_KEYS}
    pending["northbound_holding_quarterly"] = MISSING_NOT_INGESTED

    out: list[MetricCoverage] = []
    for key in ALL_METRIC_KEYS:
        metric_rows = by_metric.get(key, [])
        periods = {r.period for r in metric_rows}
        covered = sum(1 for r in metric_rows if r.value is not None)
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
                null_days=len(metric_rows) - covered,
                missing_reasons=dict(sorted(reasons.items())),
                pending_reason=None if metric_rows else pending.get(key),
            )
        )
    return out


async def count_rows(db: AsyncSession) -> int:
    """本模块两个可落库指标在 `industry_key='market'` 的行数（回补脚本的收尾断言）。"""
    value = (
        await db.execute(
            text(
                "SELECT count(*) FROM industry_metrics "
                "WHERE industry_key='market' AND stock_id=0 "
                "AND metric_key = ANY(:keys)"
            ),
            {"keys": list(INGEST_METRIC_KEYS)},
        )
    ).scalar_one()
    return int(value)
