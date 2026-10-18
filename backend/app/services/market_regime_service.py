"""市场状态机（spec §S10）：特征 → 五态（迟滞 3 日）→ 转移矩阵 / 同态后续分布。

**为什么是离线产物 + 读时只投影**：把 834 个交易日 × 全市场（实测 445 万行
``daily_quotes``）跑一遍是**分钟级扫描**，HTTP 端点不能这么干。产物随仓库入库
（``market_regime_history.json``，由 ``scripts/build_market_regime.py`` 生成），
与 ``triage_rule_stats.json`` 同一范式：可评审、可 diff、可复跑（两次运行逐字节一致）。

**口径**（spec §S10.1）：五维特征只用 ``daily_quotes`` 覆盖全历史的列
（``amount`` / ``close`` / ``pre_close``）。**不用**涨停/连板族特征——
``stock_price_limits`` 只有 259 天，前两年算不出来（spec §S10.1 口径禁令）。

涨跌幅一律 ``close / 前收 - 1``：前收取 ``pre_close``，**该值缺失时退化为该股上一行收盘**
（与 ``triage_service.load_context`` 的同一退化、以及 ``market_day_service`` 记录的
"``pct_chg`` 历史 NULL、下游按日聚合一律逐股回看前收重算"一致——实测 308 万行
``pct_chg`` 为 NULL，把它当唯一来源等于把 2023–2024 整段算不出来）。

**纯函数边界**（可单测）：``compute_features`` 的输入是**已按日聚合**的行
（``DailyRaw``），不查库、不写 SQL——日级原语由 ``scripts/build_market_regime.py``
一条只读 SQL 产出。于是单测能在内存里造任意日历验证分位/滚动波动/新高的边界。

**无前视**：``forward_stats`` 的收益窗口从**入场日的下一个交易日**开始；
入场日当天的收益不参与（收盘才判出的状态，不能吃当天涨幅）。

**不许说的话**：本模块只陈述"当前处于哪个阶段 / 历史上同阶段的后续分布"，
不产出任何预测词（spec §S10.5：状态机天然滞后，文案必须写"当前处于"）。

**阈值是标定出来的**（spec §S10.5 "先标定再上线"）：spec §S10.2 的原始数字（新高 >0.30、
退潮回落 >0.20、修复带 [0.30,0.70]）在真实分布下让 亢奋/退潮/修复 分别只剩
0.4%/1.2%/2.7% 的交易日，故 ``s10.2`` 起按实测分布调整了其中 4 个数字（冰点与迟滞未动）。
新旧对照见 ``.superpowers/sdd/regime-unlock/regime-report.md``；改阈值必须继续 bump
``REGIME_VERSION``——产物自带阈值快照，读面会拿版本对拍并提示过期。
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args

#: 五态唯一真相。**元组顺序 = 判定优先级**（spec §S10.2：亢奋 > 冰点 > 退潮 > 修复 > 中性），
#: 同时也是 JSON 里 ``transitions`` 的列序与 ``forward`` 的键序（确定性产物依赖它）。
RegimeState = Literal["亢奋", "冰点", "退潮", "修复", "中性"]
STATES: tuple[str, ...] = get_args(RegimeState)

#: 阈值版本。**改任何阈值/窗口都必须 bump**：产物自带版本，读面对不上会在 ``notes`` 里报
#: "产物版本过期需复跑"（把 spec §S10.2 的"改阈值必须 bump"变成机械可核对的事实）。
REGIME_VERSION = "s10.2"

#: 阈值 registry（唯一真相）：判定式、特征窗口、迟滞天数、统计口径全部从这里读，
#: 本模块与脚本里**不许再出现第二份数字**。
REGIME_THRESHOLDS: dict[str, int | float] = {
    # ── 特征窗口（spec §S10.1）──────────────────────────────────────────────
    "amount_pctile_window": 250,
    #: 分位最少要有这么多天（含当日）。半窗分位会把"数据只有 10 天"说成"成交额极冷"，
    #: 于是序列开头要么不判（本选择），要么给一个不可复核的假状态。
    "amount_pctile_min_days": 60,
    "vol_window": 20,
    #: "60 日新高"的窗口：前 60 个交易日（本股自己的最近 60 行）内的最高收盘。
    #: 由脚本 SQL 的帧窗口消费；有 ≥60 日历史的股票才进新高比例的分母。
    "new_high_window": 60,
    #: 新高分母（有 ≥60 日历史的股票数）少于这个数 → 不出比例（小分母的 0.5 没有意义）。
    "new_high_min_universe": 100,
    # ── 冰点（spec §S10.2 逐字：三个同时满足）────────────────────────────────
    "ice_amount_pctile_below": 0.30,
    "ice_median_pct_chg_below": 0.0,
    "ice_new_high_below": 0.15,
    # ── 修复 ────────────────────────────────────────────────────────────────
    #: 标定（s10.2）：spec 的 [0.30, 0.70] 在真实分布下只剩 2.7% 天数（中位 pctile=0.689，
    #: 而修复还要叠加 median>0 与新高 5 日环比上升），故对称放宽到 [0.20, 0.80]。
    "repair_amount_pctile_min": 0.20,
    "repair_amount_pctile_max": 0.80,
    "repair_median_pct_chg_above": 0.0,
    "repair_new_high_lookback_days": 5,
    # ── 亢奋 ────────────────────────────────────────────────────────────────
    "frenzy_amount_pctile_above": 0.70,
    #: 标定（s10.2）：``new_high_ratio`` 实测中位 3.3%、p90 9.1%、p95 12.1%，spec 的 0.30
    #: 只在 0.4% 的交易日出现（该态等于永不触发）；取 p90 之上的 0.08。
    "frenzy_new_high_above": 0.08,
    "frenzy_median_pct_chg_above": 0.0,
    # ── 退潮 ────────────────────────────────────────────────────────────────
    "retreat_pctile_lookback_days": 20,
    #: 标定（s10.2）：20 日回落实测 p50=0.032、p75=0.167、p90=0.348；spec 的 0.20 只剩
    #: "前 15% 的回落"，再被冰点优先级吃掉，最终只有 1.2% 天数。取 0.05（中位之上的回落）。
    "retreat_amount_pctile_drop": 0.05,
    #: spec 只写"``new_high_ratio`` 下降"，没给窗口 → 取 1（环比上一交易日）。
    #: 真实分布见交付报告：若 1 日抖动让该态占比不足，改窗口或阈值都必须 bump 版本。
    "retreat_new_high_lookback_days": 1,
    # ── 迟滞与统计口径（spec §S10.2/§S10.3）──────────────────────────────────
    "hysteresis_days": 3,
    "forward_horizon_short": 5,
    "forward_horizon_long": 20,
    #: 每态样本 <30 → 不给分布（只给 n）。spec §S10.3 的硬约束。
    "forward_min_samples": 30,
    #: winsorize ±50%（收益口径，不是百分数）。
    "winsorize": 0.5,
}

#: 判定式依赖的**全部**阈值键。import 期断言与 registry 完全一致：漏登记一个键，
#: 判定式就在运行期才 KeyError（最坏的一种"阈值没生效"）；多登记一个键则是死配置。
#: 增删阈值必须同时改这里 —— 这就是 spec 要求的"import 期断言阈值齐全"。
REQUIRED_THRESHOLDS: tuple[str, ...] = (
    "amount_pctile_window",
    "amount_pctile_min_days",
    "vol_window",
    "new_high_window",
    "new_high_min_universe",
    "ice_amount_pctile_below",
    "ice_median_pct_chg_below",
    "ice_new_high_below",
    "repair_amount_pctile_min",
    "repair_amount_pctile_max",
    "repair_median_pct_chg_above",
    "repair_new_high_lookback_days",
    "frenzy_amount_pctile_above",
    "frenzy_new_high_above",
    "frenzy_median_pct_chg_above",
    "retreat_pctile_lookback_days",
    "retreat_amount_pctile_drop",
    "retreat_new_high_lookback_days",
    "hysteresis_days",
    "forward_horizon_short",
    "forward_horizon_long",
    "forward_min_samples",
    "winsorize",
)

assert set(REQUIRED_THRESHOLDS) == set(REGIME_THRESHOLDS), (
    f"REGIME_THRESHOLDS 与 REQUIRED_THRESHOLDS 不一致："
    f"多 {sorted(set(REGIME_THRESHOLDS) - set(REQUIRED_THRESHOLDS))}，"
    f"缺 {sorted(set(REQUIRED_THRESHOLDS) - set(REGIME_THRESHOLDS))}"
)
# ``DayFeatures`` 的滞后字段是**按窗口命名的**（``amount_pctile_20d_ago`` 等）：
# 窗口一旦改数字，字段语义就假了。故把耦合钉死在 import 期，改窗口必须同时改 dataclass
# 与 ``compute_features``（这是"阈值改了但特征没跟上"的机械闸）。
assert int(REGIME_THRESHOLDS["retreat_pctile_lookback_days"]) == 20, (
    "改 retreat_pctile_lookback_days 必须同步 DayFeatures.amount_pctile_20d_ago"
)
assert int(REGIME_THRESHOLDS["repair_new_high_lookback_days"]) == 5, (
    "改 repair_new_high_lookback_days 必须同步 DayFeatures.new_high_ratio_5d_ago"
)
assert int(REGIME_THRESHOLDS["retreat_new_high_lookback_days"]) == 1, (
    "改 retreat_new_high_lookback_days 必须同步 DayFeatures.new_high_ratio_prev"
)
assert int(REGIME_THRESHOLDS["hysteresis_days"]) >= 1
assert int(REGIME_THRESHOLDS["forward_horizon_short"]) < int(
    REGIME_THRESHOLDS["forward_horizon_long"]
)
assert 0.0 < float(REGIME_THRESHOLDS["winsorize"]) < 1.0


# ── 产物路径与读面兜底文案 ─────────────────────────────────────────────────

#: 产物路径（``scripts/build_market_regime.py`` 的默认输出；随仓库入库）
HISTORY_PATH = Path(__file__).with_name("market_regime_history.json")

#: 产物不存在：说明"没算"，绝不是"中性"（把缺数据画成中性是最坏的一种误导）
MISSING_NOTE = "状态产物尚未生成（运行 backend/scripts/build_market_regime.py）"

#: 产物存在但不可解析：把链路断点说出来，不假装有状态
BROKEN_NOTE = "状态产物无法解析（重新运行 backend/scripts/build_market_regime.py）"

#: 固定口径（spec §S10.3 逐字，前端原样展示）
BASIS_NOTE = "状态由固定规则判定，不预测未来"
SAMPLES_NOTE = "每态样本 <30 不给分布（只给 n）"
FORWARD_NOTE = "同态后续 5/20 个交易日为全市场中位收益（按交易日连乘），winsorize ±50%"


def _t(key: str) -> float:
    """读一个阈值（float 投影）；键名写错在运行期才会炸，故 import 期已断言齐全。"""
    return float(REGIME_THRESHOLDS[key])


def _ti(key: str) -> int:
    return int(REGIME_THRESHOLDS[key])


# ── 特征 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DailyRaw:
    """**一天**的行情原语（脚本 SQL 按日聚合；纯函数只做跨日派生）。

    ``median_pct_chg`` 是**百分数**（``-1.23`` 表示 -1.23%），与 ``daily_quotes.pct_chg``
    同单位；``new_high_universe`` 是"有 ≥60 日历史"的股票数（新高比例的分母）。
    """

    trade_date: date
    total_amount: float
    advancers: int
    decliners: int
    median_pct_chg: float
    new_highs: int
    new_high_universe: int


@dataclass(frozen=True)
class DayFeatures:
    """一天的 5 维特征 + 判定式需要的**滞后值**。

    把滞后值也算进特征（而不是让 ``classify`` 回头翻序列）是为了让 ``classify``
    保持"一天进、一个状态出"的纯函数形状（spec §S10.4 的表驱动单测要求）。
    代价是字段名绑定了窗口：窗口常量改动由 import 期断言拦截。
    """

    trade_date: date
    amount_pctile: float | None
    adv_dec_ratio: float | None
    median_pct_chg: float | None
    new_high_ratio: float | None
    realized_vol_20d: float | None = None
    amount_pctile_20d_ago: float | None = None
    new_high_ratio_prev: float | None = None
    new_high_ratio_5d_ago: float | None = None


@dataclass(frozen=True)
class RegimePoint:
    """迟滞之后的序列点：``since`` = 当前状态的**起点**（切换日，不是确认期首日）。"""

    trade_date: date
    state: str
    since: date


def _percentile(window: Sequence[float], value: float) -> float:
    """``value`` 在窗口里的分位（含当日、闭区间 ``<=``）：1.0 = 窗口内最高。

    含当日是有意的：分位回答"今天在最近 250 天里有多热"，今天当然属于最近 250 天。
    """
    return sum(1 for item in window if item <= value) / len(window)


def _lagged(values: Sequence[float | None], index: int, lag: int) -> float | None:
    """``index - lag`` 处的值；越界 → ``None``（"没有历史"≠"值是 0"）。"""
    return values[index - lag] if index >= lag else None


def compute_features(rows: Sequence[DailyRaw]) -> list[DayFeatures]:
    """按日聚合法 → 日级特征（纯函数，不查库；输入顺序无关，内部按日期排序）。

    - ``amount_pctile``：当日全市场成交额在最近 ``amount_pctile_window``（250）个交易日
      （含当日）里的分位；不足 ``amount_pctile_min_days``（60）天 → ``None``；
    - ``adv_dec_ratio``：上涨家数 / 下跌家数；当日无下跌 → ``None``（0 作分母不是"无穷"）；
    - ``median_pct_chg``：当日全市场 ``pct_chg`` 的截面中位数（百分数）；
    - ``new_high_ratio``：收盘创 60 日新高的股票 / 有 ≥60 日历史的股票；分母过小 → ``None``；
    - ``realized_vol_20d``：``median_pct_chg`` 的 20 日滚动**总体**标准差（同日为当日已知）。
    """
    ordered = sorted(rows, key=lambda row: row.trade_date)
    window = _ti("amount_pctile_window")
    min_days = _ti("amount_pctile_min_days")
    vol_window = _ti("vol_window")
    min_universe = _ti("new_high_min_universe")
    retreat_pctile_lag = _ti("retreat_pctile_lookback_days")
    retreat_nh_lag = _ti("retreat_new_high_lookback_days")
    repair_nh_lag = _ti("repair_new_high_lookback_days")

    amounts = [row.total_amount for row in ordered]
    medians = [row.median_pct_chg for row in ordered]

    pctiles: list[float | None] = []
    ratios: list[float | None] = []
    vols: list[float | None] = []
    for index, row in enumerate(ordered):
        pctile: float | None = None
        if index + 1 >= min_days:
            pctile = _percentile(amounts[max(0, index - window + 1) : index + 1], row.total_amount)
        pctiles.append(pctile)
        ratio: float | None = None
        if row.new_high_universe >= min_universe and row.new_high_universe > 0:
            ratio = row.new_highs / row.new_high_universe
        ratios.append(ratio)
        vol: float | None = None
        if index + 1 >= vol_window:
            vol = statistics.pstdev(medians[index - vol_window + 1 : index + 1])
        vols.append(vol)

    # 滞后值直接按窗口取（窗口常量与字段名的耦合由 import 期断言钉死）
    return [
        DayFeatures(
            trade_date=row.trade_date,
            amount_pctile=pctiles[index],
            adv_dec_ratio=(row.advancers / row.decliners if row.decliners > 0 else None),
            median_pct_chg=row.median_pct_chg,
            new_high_ratio=ratios[index],
            realized_vol_20d=vols[index],
            amount_pctile_20d_ago=_lagged(pctiles, index, retreat_pctile_lag),
            new_high_ratio_prev=_lagged(ratios, index, retreat_nh_lag),
            new_high_ratio_5d_ago=_lagged(ratios, index, repair_nh_lag),
        )
        for index, row in enumerate(ordered)
    ]


# ── 五态判定（优先级 + 迟滞）────────────────────────────────────────────────


def _is_retreat(features: DayFeatures) -> bool:
    """退潮：20 日内 ``amount_pctile`` 回落 > 0.20 **且** ``new_high_ratio`` 下降。

    "回落"用严格大于（``prior - now > 0.20``）；任一滞后值缺失 → 不判退潮
    （没有 20 天历史就不能说"20 日内回落"）。
    """
    pctile = features.amount_pctile
    prior = features.amount_pctile_20d_ago
    ratio = features.new_high_ratio
    previous_ratio = features.new_high_ratio_prev
    if pctile is None or prior is None or ratio is None or previous_ratio is None:
        return False
    return prior - pctile > _t("retreat_amount_pctile_drop") and ratio < previous_ratio


def classify(features: DayFeatures) -> RegimeState | None:
    """单日规则态（**不含**迟滞；迟滞是序列属性，见 ``classify_series``）。

    判定顺序 = ``STATES`` 顺序（亢奋 > 冰点 > 退潮 > 修复 > 中性），先命中先返回，
    于是"同时满足"时有确定答案（spec §S10.4 的优先级验收）。
    必要条件（成交额分位 / 中位涨跌幅 / 新高比例）任一缺失 → ``None``：
    **不判**不是"中性"（把没数据画成中性会让人以为市场平淡）。
    """
    pctile = features.amount_pctile
    median = features.median_pct_chg
    ratio = features.new_high_ratio
    if pctile is None or median is None or ratio is None:
        return None

    if (
        pctile > _t("frenzy_amount_pctile_above")
        and ratio > _t("frenzy_new_high_above")
        and median > _t("frenzy_median_pct_chg_above")
    ):
        return "亢奋"
    if (
        pctile < _t("ice_amount_pctile_below")
        and median < _t("ice_median_pct_chg_below")
        and ratio < _t("ice_new_high_below")
    ):
        return "冰点"
    if _is_retreat(features):
        return "退潮"
    if (
        _t("repair_amount_pctile_min") <= pctile <= _t("repair_amount_pctile_max")
        and median > _t("repair_median_pct_chg_above")
        and features.new_high_ratio_5d_ago is not None
        and ratio > features.new_high_ratio_5d_ago
    ):
        return "修复"
    return "中性"


def classify_series(features: Sequence[DayFeatures]) -> list[RegimePoint]:
    """逐日判定 + **迟滞**：新状态需连续 ``hysteresis_days``（3）个交易日满足才切换。

    三条纪律：

    1. **切换日 = 连续满足的第 N 天**（``since`` 从那天重新计）——若从第 1 天起算，
       ``duration_days`` 会把 3 天的确认期也算进新状态，把"刚确认"说成"已持续 3 天"；
    2. 抖动序列（``冰点→修复→冰点`` 各 1 天）里 ``pending`` 每天被覆盖，``current`` 不动，
       于是状态不来回跳（spec §S10.4 的验收）；
    3. 特征未就绪的日子（序列前缀）**不进序列**，也不打断计数——它是"没判过"，
       不是"中性"。
    """
    need = _ti("hysteresis_days")
    points: list[RegimePoint] = []
    current: str | None = None
    since: date | None = None
    pending: str | None = None
    streak = 0
    for feature in features:
        raw = classify(feature)
        if raw is None:
            continue
        if current is None:
            # 序列第一天的态不是"切换"出来的：没有前序可比，直接以当日规则态起算。
            current, since, pending, streak = raw, feature.trade_date, None, 0
        elif raw == current:
            pending, streak = None, 0
        else:
            if raw == pending:
                streak += 1
            else:
                pending, streak = raw, 1
            if streak >= need:
                current, since, pending, streak = raw, feature.trade_date, None, 0
        assert since is not None
        points.append(RegimePoint(trade_date=feature.trade_date, state=current, since=since))
    return points


def build_series(rows: Sequence[DailyRaw]) -> list[dict[str, Any]]:
    """``[{date, state}]``（spec §S10.3 的产物形状；``date`` 是 ``date`` 对象，落盘时转 ISO）。

    只含**特征就绪**的交易日：序列前缀（成交额分位不足 60 天）不入内，故起点晚于
    ``daily_quotes`` 的首日，长度可能少于总交易日数——这是覆盖度事实，由脚本打印。
    """
    return [
        {"date": point.trade_date, "state": point.state}
        for point in classify_series(compute_features(rows))
    ]


# ── 序列 → 转移矩阵 / 同态后续分布 ──────────────────────────────────────────


def _as_date(value: Any) -> date:
    """``date`` 或 ISO 字符串 → ``date``（序列既能从 JSON 读，也能内存里造）。"""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _series_points(series: Sequence[Mapping[str, Any]]) -> list[tuple[date, str]]:
    """校验并展开序列；未知状态直接抛（静默丢行会让矩阵行和悄悄不等于 1）。"""
    points: list[tuple[date, str]] = []
    for item in series:
        state = item.get("state")
        if state not in STATES:
            raise ValueError(f"序列里有未知状态 {state!r}：只允许 {STATES}")
        points.append((_as_date(item.get("date")), str(state)))
    return points


def transition_matrix(series: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """同态转移概率矩阵 ``{"冰点": {"修复": 0.42, ...}}``：行和 = 1（±1e-6）。

    **只在真的出现过出边的态上出行**：给一个从未作为起点的态补全 0 行，行和就不是 1；
    补均匀分布则是编造先验。缺键 = 该态从未作为起点（读面据 ``.get`` 给空 dict）。
    列固定按 ``STATES`` 序（含 0.0 列），这样前端画表不缺格。
    """
    points = _series_points(series)
    counts: dict[str, dict[str, int]] = {}
    for (_, previous), (_, current) in zip(points, points[1:]):
        row = counts.setdefault(previous, {})
        row[current] = row.get(current, 0) + 1
    matrix: dict[str, dict[str, float]] = {}
    for state in STATES:
        row_counts = counts.get(state)
        if not row_counts:
            continue
        total = sum(row_counts.values())
        matrix[state] = {target: row_counts.get(target, 0) / total for target in STATES}
    return matrix


def _quantile(values: Sequence[float], q: float) -> float:
    """线性插值分位（与 ``statistics.quantiles(method="inclusive")`` 同一定义）。"""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def forward_stats(
    series: Sequence[Mapping[str, Any]],
    daily_market_returns: Mapping[date, float],
) -> dict[str, dict[str, float | int | None]]:
    """同态后续 5/20 个交易日的**全市场中位收益**分布（百分数）。

    - 入场日 = 该状态的**第一个交易日**（同态连续段里每一天都算一个入场日，样本才够）；
    - 收益 = 入场日**之后** h 个交易日的日收益按交易日连乘（``prod(1+r) - 1``）——
      入场日当天不参与，**无前视**；
    - ``daily_market_returns`` 是 ``{交易日: 当日全市场收益（小数）}``，其键集就是交易日历：
      未来窗口不足 h 天 → 该入场日**不计入**该 horizon（不给半窗统计）；
    - winsorize 到 ±50%（天量未复权极值会把最差 5% 放大数倍）；
    - 每 horizon 独立判断 ``n < 30`` → 中位/最差 5% 一律 ``None``，只给 n（spec §S10.4）。

    产物里 ``n`` 按 horizon 分开（``n5``/``n20``）：末尾几天有 5 日窗口但没有 20 日窗口，
    两个 n 天然不同（spec §S10.3 的 API 示例本身就是 37 vs 36）。
    """
    horizons = (_ti("forward_horizon_short"), _ti("forward_horizon_long"))
    winsor_pct = _t("winsorize") * 100.0
    min_samples = _ti("forward_min_samples")
    calendar = sorted(daily_market_returns)
    index_of = {day: i for i, day in enumerate(calendar)}
    buckets: dict[str, dict[int, list[float]]] = {}
    for entry_day, state in _series_points(series):
        start = index_of.get(entry_day)
        if start is None:
            continue
        for horizon in horizons:
            window = calendar[start + 1 : start + 1 + horizon]
            if len(window) < horizon:
                continue
            growth = 1.0
            for day in window:
                growth *= 1.0 + float(daily_market_returns[day])
            pct = (growth - 1.0) * 100.0
            pct = max(-winsor_pct, min(winsor_pct, pct))
            buckets.setdefault(state, {}).setdefault(horizon, []).append(pct)

    stats: dict[str, dict[str, float | int | None]] = {}
    for state in STATES:
        by_horizon = buckets.get(state)
        if not by_horizon:
            continue
        row: dict[str, float | int | None] = {}
        for horizon in horizons:
            values = by_horizon.get(horizon, [])
            row[f"n{horizon}"] = len(values)
            enough = len(values) >= min_samples
            row[f"t{horizon}_median"] = round(statistics.median(values), 2) if enough else None
            row[f"t{horizon}_p05"] = round(_quantile(values, 0.05), 2) if enough else None
        stats[state] = row
    return stats


# ── 产物装配（脚本与单测共用；同一输入逐字节一致）──────────────────────────


def build_document(
    rows: Sequence[DailyRaw],
    daily_market_returns: Mapping[date, float],
    *,
    version: str = REGIME_VERSION,
) -> dict[str, Any]:
    """把日级原语装配成入库产物（spec §S10.3 的形状 + 阈值快照）。

    自带 ``thresholds`` 是有意的：产物必须能**独立评审**"这批序列是哪套阈值算出来的"，
    否则半年后没人知道 diff 是阈值变了还是数据变了。读面还会拿 ``version`` 对拍当前
    版本并在 ``notes`` 里报过期（spec：改阈值必须 bump 版本）。
    """
    series = build_series(rows)
    if not series:
        raise ValueError("没有任何可判定的交易日：特征窗口不足或 daily_quotes 为空")
    return {
        "version": version,
        "as_of": _as_date(series[-1]["date"]).isoformat(),
        "basis": FORWARD_NOTE,
        "thresholds": dict(REGIME_THRESHOLDS),
        "series": [
            {"date": _as_date(point["date"]).isoformat(), "state": point["state"]}
            for point in series
        ],
        "transitions": transition_matrix(series),
        "forward": forward_stats(series, daily_market_returns),
    }


def dump_document(document: Mapping[str, Any]) -> str:
    """产物文本（固定 ``ensure_ascii=False`` + ``indent=2`` + 尾部换行）：两跑逐字节一致。"""
    return json.dumps(dict(document), ensure_ascii=False, indent=2) + "\n"


# ── 读面：产物 → 契约（不查库、不重算行情；缺产物/坏产物一律降级不 500）──────


@dataclass(frozen=True)
class RegimeRead:
    """``GET /api/v1/market/regime`` 的领域结果（路由只做 pydantic 投影）。"""

    state: str | None
    since: date | None
    duration_days: int
    transition: dict[str, float]
    forward: dict[str, dict[str, float | int | None] | None]
    notes: list[str]
    as_of: date | None
    version: str | None


def _read_artifact(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """读产物；返回 ``(文档, 兜底说明)`` —— 不存在/损坏都不抛。"""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, MISSING_NOTE
    except (OSError, json.JSONDecodeError):
        return None, BROKEN_NOTE
    if not isinstance(loaded, dict):
        return None, BROKEN_NOTE
    return loaded, None


def _empty_read(note: str) -> RegimeRead:
    """缺产物时的**显式空态**：``state=null`` + 原因，绝不是"中性"。"""
    return RegimeRead(
        state=None,
        since=None,
        duration_days=0,
        transition={},
        forward={"5d": None, "20d": None},
        notes=[note, BASIS_NOTE],
        as_of=None,
        version=None,
    )


def _count(value: float | int | None) -> int:
    """产物里的 ``n`` 可能是手改过的 ``37.0``：能读就按 int 读，读不出当 0（不给数字）。"""
    return int(value) if isinstance(value, int | float) else 0


def _transition_row(value: Any) -> dict[str, float]:
    """转移矩阵的当前态一行；形状不对 → 空 dict（不猜、不补均匀分布）。"""
    if not isinstance(value, dict):
        return {}
    row: dict[str, float] = {}
    for state in STATES:
        raw = value.get(state)
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            row[state] = float(raw)
    return row


def _forward_bucket(entry: Any, horizon: int) -> dict[str, float | int | None] | None:
    """``forward`` 的一格：``{n, median_pct, p05_pct}``；无该格 → ``None``。

    ``n < 30`` 时产物已把中位/最差 5% 写成 ``null``（口径只有一处），本函数不再判一次。
    """
    if not isinstance(entry, dict):
        return None
    raw_n = entry.get(f"n{horizon}")
    if not isinstance(raw_n, int | float) or isinstance(raw_n, bool):
        return None
    median = entry.get(f"t{horizon}_median")
    p05 = entry.get(f"t{horizon}_p05")
    return {
        "n": int(raw_n),
        "median_pct": float(median) if isinstance(median, int | float) else None,
        "p05_pct": float(p05) if isinstance(p05, int | float) else None,
    }


def read_regime(
    path: Path | None = None,
    *,
    latest_trade_date: date | None = None,
) -> RegimeRead:
    """读产物并投影成契约（零 DB 查询；``latest_trade_date`` 只用于**新鲜度说明**）。

    降级规则：产物缺失/损坏/序列为空/形状不对 → ``state=null`` + 原因 note，
    **不抛异常也不 500**（spec §S10.3：产物缺失不是客户端错误）。
    """
    doc, note = _read_artifact(HISTORY_PATH if path is None else path)
    if doc is None:
        return _empty_read(note or MISSING_NOTE)

    raw_series = doc.get("series")
    forward_doc = doc.get("forward")
    if not isinstance(raw_series, list) or not raw_series:
        return _empty_read(BROKEN_NOTE)
    try:
        points = _series_points([item for item in raw_series if isinstance(item, dict)])
    except (TypeError, ValueError):
        return _empty_read(BROKEN_NOTE)
    if len(points) != len(raw_series):
        # 序列里有非对象元素：宁可整体降级，也不静默少算几天转移
        return _empty_read(BROKEN_NOTE)

    last_day, state = points[-1]
    duration = 1
    for _, previous in reversed(points[:-1]):
        if previous != state:
            break
        duration += 1
    since = points[len(points) - duration][0]

    transitions = doc.get("transitions")
    transition = _transition_row(transitions.get(state) if isinstance(transitions, dict) else None)
    forward_entry = forward_doc.get(state) if isinstance(forward_doc, dict) else None
    short = _ti("forward_horizon_short")
    long = _ti("forward_horizon_long")
    forward = {
        f"{short}d": _forward_bucket(forward_entry, short),
        f"{long}d": _forward_bucket(forward_entry, long),
    }

    notes = [BASIS_NOTE, SAMPLES_NOTE, FORWARD_NOTE]
    version = doc.get("version")
    if version != REGIME_VERSION:
        notes.append(
            f"产物版本 {version!r} 与当前阈值版本 {REGIME_VERSION!r} 不一致，"
            "结论按旧阈值算出（需复跑 build_market_regime.py）"
        )
    if latest_trade_date is not None and latest_trade_date > last_day:
        notes.append(
            f"产物截至 {last_day.isoformat()}，最新交易日已是 "
            f"{latest_trade_date.isoformat()}（需复跑 build_market_regime.py 更新）"
        )
    for horizon in (short, long):
        bucket = forward[f"{horizon}d"]
        count = _count(bucket["n"]) if bucket is not None else 0
        if count == 0:
            notes.append(f"当前态「{state}」没有完整的 {horizon} 个交易日后续样本")
        elif count < _ti("forward_min_samples"):
            notes.append(f"当前态「{state}」{horizon} 日样本 n={count}（<30），不给中位/最差 5%")

    return RegimeRead(
        state=state,
        since=since,
        duration_days=duration,
        transition=transition,
        forward=forward,
        notes=notes,
        as_of=last_day,
        version=str(version) if version is not None else None,
    )
