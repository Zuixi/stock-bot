"""spec §S1.12 情绪 · 投资者行为 —— 计算 + 落库（`industry_key='market'`）.

## 这个模块是什么

把 §S1.12 的**两条可得腿**落成序列写进 `industry_metrics`
（`industry_key='market'`，`stock_id=0`），与 §S1.7/§S1.8/§S1.9/§S1.10/§S1.11 同表同读面。
读面**不在这里** —— 走 S1.2 已建好的 `GET /api/v1/metrics/{latest,series}`
（registry 驱动 + 四徽章 + Redis 缓存）。**不新建表、不新建平行读路、不新增迁移。**

spec 的契约表是**四条腿 + 一条不做**，处置逐条不同（这不是偷懒，是四条腿的源现状不同）：

| 原图口径 | `metric_key` | 本模块做什么 |
|---|---|---|
| 基金申购赎回 → ETF 份额变化 | `etf_share_change` | **不重做**：§S1.7 已落，只**引用** |
| 新增散户开户数 | —— | **判为不可得 ⇒ 整项不存在**（不注册 key、UI 无此项、不填估算值）|
| 新基金发行规模 → 新成立基金份额 | `new_fund_share` | **落库**（月频序列）|
| 公募基金仓位 | `public_fund_equity_position` | **落库但仅背景展示**（低频；**不进信号链路**）|
| 主动基金申赎 | —— | spec 明写**不做**（不公开）|

## Step 0 结论（实机验证，2026-10-13；探针 `scripts/spike_s112_investor_behavior_sources.py`）

- **新增散户开户数 = 不可得**。候选源东财 `stock_account_statistics_em`
  （`RPT_STOCK_OPEN_DATA`）实机可得，但**最新月停在 2023-08**（101 行 = 该接口单页上限，
  响应自带的 `count` 字段亦为 101），距观测日已停滞约 3 年；中国结算（chinaclear.cn）
  月报是唯一权威产地而 AKShare **未封装**（无对应 `fetch_*` 方法）⇒ **无权威第二源**。
  ⇒ 按 spec 的「不可得则删除，不用估算值填充」**不注册 `metric_key`**：
  本模块**不产出**开户数行、registry 里**不存在**该键 ⇒ 读面对它 404、UI 无占位空图。
  文字记录见 `industry_registry.INVESTOR_BEHAVIOR_DROPPED_ITEMS` 与字典 §4.2。
- **新成立基金份额 = 可得**：东财 `FundNewIssue.aspx`（`fund_new_found_em`）实机
  **7012 行**，`成立日期` 2023-01-01…2026-09-30 **完整**（更早只剩 2013/2018 两行孤例，
  2019–2022 产地无数据）。逐页取（`page=1..3,50000`）合计同为 7012 行 ⇒ **无分页丢行**。
  取值列 = **`募集份额`**（单位**亿元**）——该表**没有**独立的「成立份额/发行份额」列，
  而表内全是**新成立**基金，故"发行份额 == 成立份额 == 募集份额"。
- **公募基金仓位 = 可得但口径与 spec 的假设不符**：乐咕乐股 `fund_stock_position_lg`
  （`type=pos_stock`、`category=总仓位`）实机 **455 行 / 2017-12-04…2026-09-30**，
  相邻观测间隔 **6–8 天占 90.3% ⇒ 实测是周度**（**不是** spec 写的季度）；
  上游 JSON 只有 `date/close/position` 三个字段（**无**报告期/发布日期/编制方法）
  ⇒「股票投资占基金总资产比」这一口径**无法从产地复算**。故本模块把它标成
  `source_tier='calc'`（第三方编制，**不是**官方口径）、文案显式写「低频参考值 / 季报口径、
  滞后 ~15 工作日」，并且**禁止任何消费方把它接进信号链路**（判据 2 的 grep）。

三态结论、逐条实机输出与未验证余项见 `docs/design/data-source.md` **附.10**。

## 频率与发布滞后（spec 判据 1：必须在 UI 明示）

落在**两处、都是可机读/可渲染字段**（不是活在代码里的常量）：

- `MetricDef.freq`（`monthly` / `quarterly`）→ 前端 `FREQ_LABELS` 徽章直接渲染；
- `MetricDef.description` 里同时写明**频率**与**滞后**（含具体天数/口径），
  由 `industry_registry._assert_investor_behavior_keys()` 在**导入期**强制
  （缺任一词即 `RuntimeError`）。

## 缺数不静默为 0（照 §S1.7/§S1.8/§S1.9 的既有纪律）

- 缺数一律 `value = NULL` + `extra.missing_reason`（`None` = 无缺口），**绝不落 0**。
  **0 亿元的新成立基金份额**是"这个月真的没有基金成立"这一**事实断言**，
  与"这个月的份额还没披露"完全相反 —— 拿 0 顶缺会把两者混为一谈。
- `new_fund_share`：**该月一位有值的基金都没有** → `value = NULL` +
  `missing_reason='source_empty'`。**部分缺值不当缺数、更不填 0**：上游的
  `募集份额` 有约 **4.8%（340/7012）** 的**字段级**空缺，实测**均匀散布**在各类基金与
  各月（逐年缺值率 2023 4.9% / 2024 6.6% / 2025 5.6% / 2026 1.4%，无按类型聚集）
  —— 这是"个别基金的该字段没收录"，不是"整月没披露"。故该月**照落有值成员之和**
  （= 真实全月规模的**下界**），并在 `extra` 里显式登记 `n_funds` / `n_missing_share`
  / `is_lower_bound=True`（值不完整时），让读的人自行判断；
  **绝不**把缺的份额当 0（那会把下界冒充成精确值）。
  ⚠️ 若某个**整月**的 `募集份额` 全空 → 那才是"缺数"，落 NULL（见上）。
  该月上游**一行都没有** → 不落行（结构性不存在，不是缺数）。
- `public_fund_equity_position`：上游该观测日无值 → NULL + `source_empty`。
- 落库天锚在**各指标自身输入的可得周期**上（Ruling 59）：月频锚在"有成立基金的那些月"，
  周度锚在"乐咕乐股给出观测的那些日"。**不**拿 `daily_quotes` 当日历底座 ——
  月/周频指标的分母不是交易日，拿行情日历当分母会把每一个非交易日算成"缺数"。

## A/B/C 归类（ADR 0014）

两个落库指标都是 **B 类**（只用当期点内事实，可回算、允许回补）：
`new_fund_share` 只用"该月已成立的基金及其成立时募集份额"（成立日即事实发生日）；
`public_fund_equity_position` 只用"该观测日乐咕乐股给的仓位值"。
**没有 A 类**（不消费"只给当下"的盘中明细）、**没有 C 类**（不用成分快照回算）。

## PIT 纪律（ADR 0011 / Ruling 34-35）

`published_at` 按**产地各自的可得出处**填（不是一律 15:00，也不是回退 `period`）：

- `new_fund_share`：该月**最后一个成立日** + **4 个日历日**
  （东财「新成立基金」页在基金成立后数日收录，保守取值）；
- `public_fund_equity_position`：观测日 + **15 个工作日（跳过周末的近似）**
  （spec 原话「季报、滞后 ~15 工作日」的**推算** —— 产地无发布字段，故只能推算）。

⚠️ 两个偏移量**都是保守取值、未经实测**：上游都没有给出发布时刻字段，
故它们属于附.10 的未验证余项，**不得**被当成"已证实的发布时刻"。
`caliber_version` 恒填 :data:`INVESTOR_BEHAVIOR_CALIBER_VERSION`。
**落库前的 PIT 守卫**复用 `market_activity_service.upsert_metric_rows` ——
它拒绝任何缺 `published_at`（或该列非带时区 datetime）/ 缺 `caliber_version` 的行
（这两列在表里可空，漏带会**静默**落 NULL；Ruling 59 的原始事故形态）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustryMetric
from app.services.industry_registry import (
    INVESTOR_BEHAVIOR_ALL_KEYS,
    INVESTOR_BEHAVIOR_CALIBER_VERSION,
    INVESTOR_BEHAVIOR_METRIC_KEYS,
    INVESTOR_BEHAVIOR_REUSED_KEYS,
    NEW_FUND_SHARE_SOURCE,
    PUBLIC_FUND_POSITION_SOURCE,
)
from app.services.market_activity_service import (
    MetricValue,
    published_at_for,
    upsert_metric_rows,
)

logger = logging.getLogger(__name__)

#: 本模块**实际落库**的两个 key（= spec 的四条腿里可落库的那两条）。
INGEST_METRIC_KEYS: tuple[str, ...] = INVESTOR_BEHAVIOR_METRIC_KEYS
#: 本模块的**全部** key（无"底座类"附加 key，故与落库集相等）。
ALL_METRIC_KEYS: tuple[str, ...] = INVESTOR_BEHAVIOR_ALL_KEYS

NEW_FUND_SHARE = "new_fund_share"
PUBLIC_FUND_EQUITY_POSITION = "public_fund_equity_position"

#: 各 key 的落库 `source`（必须与 registry 的 `MetricDef.sources` **逐字相等** ——
#: 序列读面有源白名单过滤，见 §S1.10 的教训）。
_SOURCES: dict[str, str] = {
    NEW_FUND_SHARE: NEW_FUND_SHARE_SOURCE,
    PUBLIC_FUND_EQUITY_POSITION: PUBLIC_FUND_POSITION_SOURCE,
}
#: 各 key 的 `source_tier`（**必须与 registry 的 `MetricDef.tier` 一致**，由单测锁定）。
_TIERS: dict[str, str] = {
    NEW_FUND_SHARE: "derived",
    PUBLIC_FUND_EQUITY_POSITION: "calc",
}
#: 各 key 的 `unit`（字典 §4.2 规则 3：单位随 `metric_key` 固定在 registry）。
UNITS: dict[str, str] = {
    NEW_FUND_SHARE: "亿元",
    PUBLIC_FUND_EQUITY_POSITION: "%",
}
#: 各 key 的落库 `freq`（= registry 的 `MetricDef.freq`，逐字）。
FREQS: dict[str, str] = {
    NEW_FUND_SHARE: "monthly",
    PUBLIC_FUND_EQUITY_POSITION: "quarterly",
}
#: 各 key 的 `published_at` 时刻（当日几点可得）。两者都是**日粒度**披露（无盘中时点），
#: 取 21:00 与 §S1.7 的"盘后/次日收录"同档，偏保守。
_PUBLISH_AT = dtime(21, 0)

#: `new_fund_share` 的发布滞后：上游"新成立基金"页在基金成立后数日收录（**保守取值**）。
NEW_FUND_ESTABLISH_LAG_DAYS = 4
#: `public_fund_equity_position` 的发布滞后：spec 原话「季报、滞后 ~15 工作日」。
#: 按**工作日**计（跳过周末，不查节假日日历）—— **推算值**，产地无发布字段。
POSITION_PUBLISH_LAG_DAYS = 15

#: `new_fund_share` 认为"该产地有完整数据"的起点（实测：2023-01 起连续，之前只剩孤例）。
NEW_FUND_COMPLETE_FROM = date(2023, 1, 1)
#: 该产地实测的**最小可信行数**：低于此值几乎必然是"接口改版返回半张表"。
#: （实测 7012 行；取 5000 留出上游正常增长/收缩的余量，但挡住"只回 2000 行"这类截断。）
NEW_FUND_MIN_ROWS = 5000
#: 单只基金的 `募集份额` 合理区间（亿元）。实测 [0.10, 80.00]；越界即口径/单位变了。
FUND_SHARE_MIN = 0.01
FUND_SHARE_MAX = 500.0
#: 仓位的合理区间（%）。实测 [77.81, 97.13]；越界即口径变了（不是"新极值"）。
POSITION_MIN, POSITION_MAX = 50.0, 100.0

#: `extra.missing_reason` 取值（与 registry 的 `INVESTOR_BEHAVIOR_MISSING_REASONS` 同源语义）。
MISSING_SOURCE_EMPTY = "source_empty"
MISSING_UPSTREAM_NO_ROWS = "upstream_no_rows"

#: 探针 `--json-out` 的产物形状：逐月的 `{period, sum, n, n_null, last_day}` 记录。
OfflineMonths = list[dict[str, Any]]


class InvestorBehaviorSourceError(RuntimeError):
    """上游形状不符（接口改版 / 静默截断 / 越界值）—— **响亮失败**，不得静默降级。

    Ruling 51 的同族纪律：把"接口没了/被截断"伪装成"这个月没数据"，会让整条序列
    静默变 NULL 而无人报错。
    """


def _assert_registry_contract() -> None:
    """落库行的四元组必须与 registry 的 `MetricDef` **逐字相等**（导入期当场失败）.

    三处真相（服务里的 `_TIERS`/`UNITS`/`FREQS`/`_SOURCES`、registry 的 `MetricDef`、
    字典 §4.2 的转录表）里任何一处漂移，都会让"UI 明示频率/单位"变成谎言 ——
    例如服务写 `daily` 而 registry 写 `monthly` 时，前端徽章与落库 `freq` 就不一致。
    """
    from app.services.industry_registry import INDUSTRIES  # noqa: PLC0415

    defs = {m.key: m for m in INDUSTRIES["market"].metrics}
    drift: list[str] = []
    for key in ALL_METRIC_KEYS:
        metric = defs.get(key)
        if metric is None:
            drift.append(f"{key}: registry 未登记（落库会被白名单拒绝）")
            continue
        for label, service_value, registry_value in (
            ("freq", FREQS[key], metric.freq),
            ("unit", UNITS[key], metric.unit),
            ("source_tier", _TIERS[key], metric.tier),
        ):
            if service_value != registry_value:
                drift.append(f"{key}.{label}: 服务={service_value!r} registry={registry_value!r}")
        if _SOURCES[key] not in metric.sources:
            drift.append(f"{key}.source: {_SOURCES[key]!r} 不在 registry.sources={metric.sources}")
    if drift:
        raise RuntimeError("investor_behavior_service 与 registry 的口径漂移：" + "; ".join(drift))


_assert_registry_contract()


# ── 取数与规范化 ───────────────────────────────────────────────────────


def _parse_day(value: Any) -> date:
    """把上游的日期（`datetime.date` / `YYYY-MM-DD` 字符串）规范成 `date`。"""
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:  # pragma: no cover - 上游形状变了才会走到
        raise InvestorBehaviorSourceError(f"上游日期无法解析：{value!r}") from exc


def normalize_new_fund_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """东财「新成立基金」→ 规范化行 `[(代码, 成立日, 募集份额|None), …]`（纯函数）.

    **三条上游事实当场核**（静默接受 = 把接口改版伪装成"缺数"）：

    1. 返回行数 < :data:`NEW_FUND_MIN_ROWS` → 响亮失败（实测 7012；"只回 2000 行"
       这类**静默截断**正是本会话连续踩到的陷阱族）。
    2. 缺 `基金代码` / `成立日期` / `募集份额` 列 → 响亮失败（接口改版）。
    3. 非空的 `募集份额` 落在 `[FUND_SHARE_MIN, FUND_SHARE_MAX]` 之外 → 响亮失败
       （单位换成了万份/份？口径变了？**不得**照落）。

    `募集份额` 为空的行**保留**（只记 `None`）：服务侧据此把该月整月落 NULL，
    而不是悄悄把该行丢掉、把一个偏低的"全月规模"当交付。
    """
    if df is None or not len(df):
        raise InvestorBehaviorSourceError(
            "fund_new_found_em 返回 0 行 —— 接口不可得；不得用估算值/相近接口顶替"
        )
    missing_columns = [c for c in ("基金代码", "成立日期", "募集份额") if c not in df.columns]
    if missing_columns:
        raise InvestorBehaviorSourceError(
            f"fund_new_found_em 缺列 {missing_columns}（实测列={list(df.columns)}）—— 上游改版"
        )
    if len(df) < NEW_FUND_MIN_ROWS:
        raise InvestorBehaviorSourceError(
            f"fund_new_found_em 只回 {len(df)} 行（< {NEW_FUND_MIN_ROWS}）—— "
            "疑似静默截断（该产地自 2023-01 起完整，实测 7012 行）；"
            "**不得**照落一个缺头的月度序列"
        )

    out: list[dict[str, Any]] = []
    bad: list[tuple[str, float]] = []
    for _, row in df.iterrows():
        raw = row["募集份额"]
        value = None if raw is None or raw != raw else float(raw)  # NaN != NaN
        if value is not None and not FUND_SHARE_MIN <= value <= FUND_SHARE_MAX:
            bad.append((str(row["基金代码"]), value))
            continue
        out.append(
            {
                "fund_code": str(row["基金代码"]),
                "established": _parse_day(row["成立日期"]),
                "share_yi": value,
            }
        )
    if bad:
        raise InvestorBehaviorSourceError(
            f"fund_new_found_em 有 {len(bad)} 行 募集份额 越界"
            f"（∉[{FUND_SHARE_MIN}, {FUND_SHARE_MAX}] 亿元）：{bad[:5]} —— "
            "单位/口径变了，**不得**照落"
        )
    return out


def normalize_position_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """乐咕乐股「基金仓位」→ 规范化行 `[(观测日, 仓位%)]`（纯函数）.

    形状守卫：列集合必须**恰好**是 `['date', 'close', 'position']`。列一变就说明
    上游改了返回结构（可能顺手补了报告期/发布日期）——那时 PIT 结论必须**重新裁决**，
    而不是继续用"滞后 ~15 工作日"这个推算值。故这里响亮失败。
    """
    if df is None or not len(df):
        raise InvestorBehaviorSourceError(
            "fund_stock_position_lg 返回 0 行 —— 接口不可得；不得取最近值/插值顶替"
        )
    if list(df.columns) != ["date", "close", "position"]:
        raise InvestorBehaviorSourceError(
            f"fund_stock_position_lg 列集合变了（实测={list(df.columns)}）—— "
            "若上游补了 report_period/publish_date，published_at 的推算结论须重新裁决"
        )

    out: list[dict[str, Any]] = []
    bad: list[float] = []
    for _, row in df.iterrows():
        value = row["position"]
        if value is None or value != value:  # noqa: PLR0124 - NaN 判定
            out.append({"day": _parse_day(row["date"]), "position_pct": None})
            continue
        numeric = float(value)
        if not POSITION_MIN <= numeric <= POSITION_MAX:
            bad.append(numeric)
            continue
        out.append({"day": _parse_day(row["date"]), "position_pct": numeric})
    if bad:
        raise InvestorBehaviorSourceError(
            f"fund_stock_position_lg 有 {len(bad)} 行 position 越界"
            f"（∉[{POSITION_MIN}, {POSITION_MAX}]%）：{bad[:5]} —— 口径/单位变了，**不得**照落"
        )
    return out


def month_start(day: date) -> date:
    """该日所属月的**月首**（月频行的 `period` 锚点）。

    `period` 对月频指标是"哪个周期"的标识，不是"数据在哪一天"。第 1 天是唯一无歧义的
    选择（月内任何一天都可能被误读成"那天发生了事"）。
    """
    return day.replace(day=1)


def add_working_days(day: date, working_days: int) -> date:
    """观测日 + N 个**工作日**（跳过周末，**不查节假日日历**）.

    只跳周末 ⇒ 真实节假日会让实际可得日**更晚**，故本函数给的是**早于等于**真实可得日的
    下界（偏乐观——**不是**保守）。选它的理由：spec 给的就是「~15 个工作日」这个量级，
    而精确到交易日的版本需要交易日日历 + 对"哪一天发布"的实测证据；两者都没有，
    故该滞后整体登记为附.10 的未验证余项，**不得**被当成"已证实的发布时刻"。
    """
    cursor = day
    remaining = working_days
    while remaining > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


# ── 聚合（纯函数：离线可测，不触网）─────────────────────────────────────


def new_fund_share_by_month(
    rows: list[dict[str, Any]], *, month_lo: date | None = None, month_hi: date | None = None
) -> dict[date, MetricValue]:
    """规范化行 → `{月首: MetricValue}`（`new_fund_share` 的**唯一**计算入口）.

    - **只产出"上游有行"的月份**：某月一行都没有 ⇒ **不落行**（该月结构性没有新基金，
      是"不存在"而不是"缺数"）。Ruling 59：落库天锚在自身输入的可得周期上。
    - **部分缺值 ≠ 缺数，更 ≠ 0**：`募集份额` 的空缺是**字段级**的（实测 ~4.8%、均匀
      散布），故照落"有值成员之和"作为该月规模的**下界**，并在 `extra` 里登记
      `n_funds` / `n_missing_share` / `is_lower_bound=True`（**不得**把它当精确值，
      也**不得**把缺的当 0）。只有**整月全空**才落 NULL + `missing_reason='source_empty'`。
    - `published_at` 的锚 = 该月**最后一个成立日**（不是月首、不是月尾）：上游按成立日
      收录，最后一只成立的那天才是"这个月的数字定型"的时点。
    """
    buckets: dict[date, dict[str, Any]] = {}
    for row in rows:
        day: date = row["established"]
        if month_lo is not None and day < month_lo:
            continue
        if month_hi is not None and day > month_hi:
            continue
        slot = buckets.setdefault(
            month_start(day),
            {"sum": 0.0, "n_funds": 0, "n_null": 0, "last_established": day},
        )
        if row["share_yi"] is None:
            slot["n_null"] += 1
        else:
            slot["sum"] += float(row["share_yi"])
            slot["n_funds"] += 1
        if day > slot["last_established"]:
            slot["last_established"] = day

    out: dict[date, MetricValue] = {}
    for month, slot in buckets.items():
        anchor: date = slot["last_established"]
        extra: dict[str, Any] = {
            # 有值成员数 / **字段缺值**成员数（两者之和 = 该月成立基金数）。
            "n_funds": slot["n_funds"],
            "n_missing_share": slot["n_null"],
            # 该月最后一只基金的成立日 = 上游"当月数字定型"的时点（published_at 的锚）。
            "anchor_established": anchor.isoformat(),
            "published_anchor": anchor.isoformat(),
            "unit_note": "亿元；募集份额 = 成立时的募集份额（含 A/C 双计）",
            "source": NEW_FUND_SHARE_SOURCE,
        }
        if not slot["n_funds"]:
            # **整月全空**才是"缺数"：一位有值的基金都没有 ⇒ NULL（绝不落 0）。
            extra["missing_note"] = "该月全部新成立基金的 募集份额 均为空 —— 是缺数，不是 0 亿元"
            out[month] = MetricValue(NEW_FUND_SHARE, None, MISSING_SOURCE_EMPTY, extra)
            continue
        if slot["n_null"]:
            # 部分缺值：落有值成员之和（**下界**），并把不完整性显式登记在 extra 里。
            # 值本身仍是数字（可以用），但任何消费方都必须能看到 `is_lower_bound`。
            extra["is_lower_bound"] = True
            extra["missing_note"] = (
                f"该月有 {slot['n_null']} 只新成立基金的 募集份额 为空（字段级缺值，"
                "实测 ~4.8%、均匀散布）⇒ 本值是**下界**，缺的份额未被当成 0"
            )
        out[month] = MetricValue(NEW_FUND_SHARE, round(slot["sum"], 4), None, extra)
    return out


def public_fund_position_by_day(
    rows: list[dict[str, Any]], *, day_lo: date | None = None, day_hi: date | None = None
) -> dict[date, MetricValue]:
    """规范化行 → `{观测日: MetricValue}`（`public_fund_equity_position` 的入口）.

    ⚠️ **本函数产出的 key 不得被任何信号链路消费**（spec §S1.12 硬约束：
    「仅作背景展示，不进信号链路（不得当情绪指标）」）。
    """
    out: dict[date, MetricValue] = {}
    for row in rows:
        day: date = row["day"]
        if day_lo is not None and day < day_lo:
            continue
        if day_hi is not None and day > day_hi:
            continue
        extra: dict[str, Any] = {
            "upstream": "akshare.fund_stock_position_lg ← legulegu.com fund-position",
            "source_tier_note": "第三方编制（乐咕乐股），**不是**交易所官方口径；口径不可复算",
            "frequency_note": "产地实测为周度观测；本 key 按 spec 标为季报口径（仅背景展示）",
            "publish_lag_note": (
                f"published_at = 观测日 + {POSITION_PUBLISH_LAG_DAYS} 个**工作日**"
                "（跳过周末，不查节假日日历；产地无发布字段 ⇒ 按 spec 的"
                "「滞后 ~15 工作日」推算，属未验证余项）"
            ),
            "signal_chain": "forbidden",
        }
        if row["position_pct"] is None:
            out[day] = MetricValue(PUBLIC_FUND_EQUITY_POSITION, None, MISSING_SOURCE_EMPTY, extra)
        else:
            out[day] = MetricValue(
                PUBLIC_FUND_EQUITY_POSITION, float(row["position_pct"]), None, extra
            )
    return out


# ── 落库 ───────────────────────────────────────────────────────────────


def _row(period: date, metric: MetricValue) -> dict[str, Any]:
    """一个 `MetricValue` → `industry_metrics` 一行（PIT 两列恒填满）."""
    extra: dict[str, Any] = dict(metric.extra or {})
    if metric.missing_reason:
        extra["missing_reason"] = metric.missing_reason
    key = metric.metric_key
    published_on = period
    if key == NEW_FUND_SHARE:
        anchor_raw = extra.get("anchor_established")
        anchor = _parse_day(anchor_raw) if anchor_raw else period
        published_on = anchor + timedelta(days=NEW_FUND_ESTABLISH_LAG_DAYS)
    elif key == PUBLIC_FUND_EQUITY_POSITION:
        published_on = add_working_days(period, POSITION_PUBLISH_LAG_DAYS)
    return {
        "industry_key": "market",
        "stock_id": 0,
        "metric_key": key,
        "source": _SOURCES[key],
        "source_tier": _TIERS[key],
        "freq": FREQS[key],
        "period": period,
        "value": metric.value,
        "unit": UNITS[key],
        # NULL 值的行**也要留 extra**：`missing_reason` 是"为什么没有值"的唯一证据
        # （ADR 0014「有行 ≠ 有值」，必须能区分）。
        "extra": extra or None,
        "published_at": published_at_for(published_on, _PUBLISH_AT),
        "caliber_version": INVESTOR_BEHAVIOR_CALIBER_VERSION,
    }


@dataclass(frozen=True)
class BackfillStats:
    """一次区间落库的可核对统计（回补脚本直接打印它）。"""

    period_lo: date | None
    period_hi: date | None
    rows_upserted: int
    per_metric_covered: dict[str, int]
    per_metric_null: dict[str, int]
    missing_reasons: dict[str, dict[str, int]]
    #: **引用不重做**的 key（spec：「ETF 份额变化（复用 S1.7）」）。
    reused: dict[str, str]
    sources: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "period_lo": self.period_lo.isoformat() if self.period_lo else None,
            "period_hi": self.period_hi.isoformat() if self.period_hi else None,
            "rows_upserted": self.rows_upserted,
            "per_metric_covered": self.per_metric_covered,
            "per_metric_null": self.per_metric_null,
            "missing_reasons": self.missing_reasons,
            "reused": self.reused,
            "sources": self.sources,
        }


async def fetch_investor_behavior_rows(
    *,
    client: Any | None = None,
    offline_months: OfflineMonths | None = None,
) -> tuple[dict[date, MetricValue], dict[date, MetricValue]]:
    """取数 + 规范化 + 聚合 → `(月频桶, 周度桶)`.

    `offline_months` 非空时**不触网**：用探针 `--json-out` 落盘的月度记录（每条含
    `period` / `sum` / `n` / `n_null`）直接还原月频桶 —— 让回补与测试都能离线复跑
    （与 §S1.10 探针的 `--json-out` 同思路，但这里让**服务侧**也吃这个形状）。
    """
    if client is None:
        from app.core.providers.akshare_client import get_akshare_client

        client = get_akshare_client()

    if offline_months:
        months: dict[date, MetricValue] = {}
        for rec in offline_months:
            month = _parse_day(rec["period"])
            n_null = int(rec.get("n_null") or 0)
            extra = {
                "n_funds": int(rec.get("n") or 0),
                "n_missing_share": n_null,
                "anchor_established": str(rec.get("last_day") or month.isoformat())[:10],
                "unit_note": "亿元；募集份额 = 成立时的募集份额（含 A/C 双计）",
                "source": NEW_FUND_SHARE_SOURCE,
                "offline_replay": True,
            }
            n_funds = int(rec.get("n") or 0)
            if n_null:
                extra["is_lower_bound"] = True
                extra["missing_note"] = (
                    f"该月有 {n_null} 只新成立基金的 募集份额 为空（字段级缺值）⇒ "
                    "本值是**下界**，缺的份额未被当成 0"
                )
            if not n_funds:
                extra["missing_note"] = "该月全部新成立基金的 募集份额 均为空 —— 是缺数，不是 0"
                months[month] = MetricValue(NEW_FUND_SHARE, None, MISSING_SOURCE_EMPTY, extra)
            else:
                months[month] = MetricValue(
                    NEW_FUND_SHARE, round(float(rec.get("sum") or 0.0), 4), None, extra
                )
        positions = await fetch_position_series(client)
        return months, positions

    new_funds = await client.fetch_fund_new_found()
    months = new_fund_share_by_month(normalize_new_fund_rows(new_funds))
    positions = await fetch_position_series(client)
    return months, positions


async def fetch_position_series(client: Any) -> dict[date, MetricValue]:
    """公募基金仓位：取数 → 规范化 → 逐观测日成行."""
    frame = await client.fetch_fund_stock_position()
    return public_fund_position_by_day(normalize_position_rows(frame))


async def upsert_investor_behavior_range(
    db: AsyncSession,
    *,
    month_lo: date | None = None,
    month_hi: date | None = None,
    client: Any | None = None,
    offline_months: OfflineMonths | None = None,
) -> BackfillStats:
    """把 §S1.12 的两个可得指标算出来并**幂等**写进 `industry_metrics`。

    - `new_fund_share`：只落"有成立基金的那些月"（`month_lo..month_hi` 内）。
    - `public_fund_equity_position`：只落"乐咕乐股给出观测的那些日"。
    - ETF 份额变化：**不重做**（§S1.7 已落；这里只在返回值里显式声明"引用"）。

    幂等：唯一键 + `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0。
    调用方负责 `await db.commit()`（本层不替上层决定事务边界）。
    """
    monthly, positions = await fetch_investor_behavior_rows(
        client=client, offline_months=offline_months
    )
    if month_lo is not None:
        monthly = {d: v for d, v in monthly.items() if d >= month_start(month_lo)}
    if month_hi is not None:
        monthly = {d: v for d, v in monthly.items() if d <= month_hi}

    rows: list[dict[str, Any]] = []
    covered: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    nulls: dict[str, int] = dict.fromkeys(ALL_METRIC_KEYS, 0)
    reasons: dict[str, dict[str, int]] = {k: {} for k in ALL_METRIC_KEYS}

    def _tally(metric: MetricValue) -> None:
        if metric.value is None:
            nulls[metric.metric_key] += 1
            if metric.missing_reason:
                reasons[metric.metric_key][metric.missing_reason] = (
                    reasons[metric.metric_key].get(metric.missing_reason, 0) + 1
                )
        else:
            covered[metric.metric_key] += 1

    for month, metric in sorted(monthly.items()):
        _tally(metric)
        rows.append(_row(month, metric))
    for day, metric in sorted(positions.items()):
        _tally(metric)
        rows.append(_row(day, metric))

    if not rows:
        # **响亮失败**：可落库的两条一条都没产出，只可能是上游取数全空或窗口参数错。
        # 静默返回 0 行会让"回补跑过了"与"回补没跑"长得一样。
        raise InvestorBehaviorSourceError(
            "§S1.12 两个可落库指标一条行都没产出 —— 上游取数异常或窗口错，"
            "**不得**静默成功（那会把采集失败伪装成缺数）。请重跑或收窄窗口。"
        )

    upserted = await upsert_metric_rows(db, rows)
    periods = sorted(r["period"] for r in rows)
    return BackfillStats(
        period_lo=periods[0],
        period_hi=periods[-1],
        rows_upserted=upserted,
        per_metric_covered=covered,
        per_metric_null=nulls,
        missing_reasons={k: dict(sorted(v.items())) for k, v in reasons.items()},
        reused={
            key: "§S1.7 已落（spec 原话「ETF 份额变化（复用 S1.7）」）—— 本模块不重做、不重登记"
            for key in INVESTOR_BEHAVIOR_REUSED_KEYS
        },
        sources={k: _SOURCES[k] for k in ALL_METRIC_KEYS},
    )


async def backfill_investor_behavior(
    db: AsyncSession,
    *,
    month_hi: date | None = None,
    lookback_months: int = 60,
    offline_months: OfflineMonths | None = None,
) -> BackfillStats:
    """回补 `[month_hi - lookback_months, month_hi]` 的两个指标并提交。

    默认 60 个月：上游「新成立基金」实测只从 **2023-01** 起完整（附.10），
    故实际落库起点会被数据自身抬到 2023-01（Ruling 34：收窄必须显式，见
    `BackfillStats.period_lo`）。

    `month_hi=None` → 取 `date.today()` 的月首（月频/周度指标的"最近可得周期"由
    上游数据自己决定，窗口只用来裁剪）。
    """
    today = month_hi or date.today()
    month_lo = month_start(today) - timedelta(days=31 * lookback_months)
    stats = await upsert_investor_behavior_range(
        db, month_lo=month_lo, month_hi=month_start(today), offline_months=offline_months
    )
    await db.commit()
    return stats


async def latest_new_fund_establish_day(*, client: Any | None = None) -> date:
    """上游「新成立基金」的最近成立日（回补窗口可锚在**数据**上而不是 `date.today()`）。"""
    if client is None:
        from app.core.providers.akshare_client import get_akshare_client

        client = get_akshare_client()
    frame = await client.fetch_fund_new_found()
    rows = normalize_new_fund_rows(frame)
    if not rows:
        raise InvestorBehaviorSourceError("fund_new_found_em 规范化后 0 行 —— 无法确定回补上界")
    latest: date = max(r["established"] for r in rows)
    return latest


# ── 覆盖率对账与 PIT 审计 ───────────────────────────────────────────────


@dataclass(frozen=True)
class MetricCoverage:
    """一个指标的覆盖率对账（缺数可见性的对外形状）。"""

    metric_key: str
    period_from: date | None
    period_to: date | None
    rows: int
    covered_periods: int
    null_periods: int
    missing_reasons: dict[str, int]
    #: 整张表**一行都没有**时的原因（否则 None）—— 「没有行」与「有行但 NULL」是两种缺口，
    #: 修复动作不同，所以分开暴露（照 §S1.7 的 `pending_reason` 做法）。
    pending_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "period_from": self.period_from.isoformat() if self.period_from else None,
            "period_to": self.period_to.isoformat() if self.period_to else None,
            "rows": self.rows,
            "covered_periods": self.covered_periods,
            "null_periods": self.null_periods,
            "missing_reasons": self.missing_reasons,
            "pending_reason": self.pending_reason,
        }


async def investor_behavior_coverage(
    db: AsyncSession, *, period_lo: date | None = None, period_hi: date | None = None
) -> list[MetricCoverage]:
    """逐指标的覆盖率：`covered_periods` / `null_periods` / `missing_reasons`。

    **分母不用 `daily_quotes`**（与 §S1.9 的 `metric_coverage` 不同，这是有意的）：
    月频与周度指标的"周期"不是交易日，拿行情日历当分母会把每个非交易日算成缺数。
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
    if period_lo is not None:
        stmt = stmt.where(IndustryMetric.period >= period_lo)
    if period_hi is not None:
        stmt = stmt.where(IndustryMetric.period <= period_hi)
    rows = (await db.execute(stmt)).all()

    by_metric: dict[str, list[Any]] = {k: [] for k in ALL_METRIC_KEYS}
    for row in rows:
        by_metric.setdefault(row.metric_key, []).append(row)

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
                covered_periods=covered,
                null_periods=len(metric_rows) - covered,
                missing_reasons=dict(sorted(reasons.items())),
                # 一行都没有 ⇒ 说明"上游这个周期一行都没有"（`upstream_no_rows`），
                # 而不是"有行但值缺失" —— 前者不落行是设计，后者才是缺口。
                pending_reason=None if metric_rows else MISSING_UPSTREAM_NO_ROWS,
            )
        )
    return out


def load_offline_months(path: str | Path) -> OfflineMonths:
    """读探针 `--json-out` 的产物（离线复跑用）。"""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise InvestorBehaviorSourceError(f"{path} 不是非空的月度记录数组")
    return payload
