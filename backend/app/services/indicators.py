"""Reusable **pure-function** factor library (spec §S2.1).

它只回答一件事：**把一条序列变成另一条序列/一个标量**。输入是普通
``list[float | None]``（旧→新），输出是等长 ``list[float | None]`` 或标量；
**没有任何 IO、没有 DB session、没有 cache client、不 import 任何 repository**。
物化（写库）由调用方在别处做——本文件只算。

## 为什么是纯函数

spec §S2.1 契约：``(序列, window, params) → 序列``，"有 IO / 副作用的实现违规"。
可复算性检验（同一输入必得同一输出）只有在纯函数上才可能成立：一旦函数内部
读时钟、读库或读缓存，"同一输入"就不再是可枚举的。

## 复用既有纯函数（不重写）

``count_consecutive_negative`` 直接来自 ``cycle_engine``（spec §S2.1 点名），
此处**re-export 而非复制**：两处实现会各自演进，是口径漂移的入口。
分位口径 `percentile_rank` 对齐两处既有实现：

* ``limit_up_service.percentile_from_series`` —— 只用有效样本、升序、无插值
* ``financial_service._percentile_rank`` —— "rank = 小于等于当前值的比例"

两者都是**nearest-rank / 无插值**口径，本文件的 ``percentile_rank`` 采用同一族口径
（见函数 docstring 的精确 tie 规则），因此与既有读面同源。

## 命名与登记

每个对外可用的因子都在 ``industry_registry.FACTORS`` 里登记一条 ``FactorDef``
（名称 / 单位 / 输入指标 / 窗口 / 展示分组），并在 ``docs/design/metric-dictionary.md``
§4.2 转录一行——``backend/scripts/check_metric_dictionary.py`` 机械比对两处，
**抄错单位或窗口会被抓**。因子键与 ``metric_key`` **共用同一命名空间且不得撞名**
（``test_indicators_registry.py`` 机械锁定）。

## None 语义（全文件统一，勿各自发明）

* 序列**旧→新**；``None`` = 该期缺失（停牌/未披露），**不是 0**
* 缺失样本**不进统计**（不当 0 参与均值/分位/回归）
* 输出序列与输入**等长**、逐位对齐：算不出来的位置回 ``None``
* 函数**不改动入参**（不 in-place ``sort``/``append``）；返回值是新 list
"""

from __future__ import annotations

import math
from typing import Any

# spec §S2.1 点名复用；re-export（不复制实现）
from app.services.cycle_engine import count_consecutive_negative as count_consecutive_negative

__all__ = [
    "annualized_volatility",
    "count_consecutive_negative",
    "downside_volatility",
    "drawdown",
    "latest",
    "latest_bool",
    "ma_bullish",
    "max_drawdown",
    "mom",
    "moving_average",
    "percentile_rank",
    "stock_feature_row",
    "trend_reversals",
    "trend_slope",
    "yoy",
    "zscore",
]

# 展示分组（与 FactorDef.group 取值域一致）
GROUP_MOMENTUM = "momentum"  # 动量/变化
GROUP_TREND = "trend"  # 趋势/均线
GROUP_RISK = "risk"  # 风险/波动
GROUP_VALUATION = "valuation"  # 分位/估值位

# 年化交易日数（annualized_volatility 的默认口径）
TRADING_DAYS_PER_YEAR = 252


def _valid(series: list[float | None]) -> list[float]:
    """取出有效（非 None、非 NaN）样本，保持原顺序。"""
    out: list[float] = []
    for v in series:
        if v is None:
            continue
        f = float(v)
        if math.isnan(f):
            continue
        out.append(f)
    return out


def latest(series: list[float | None]) -> float | None:
    """最后一个有效值；全缺失 → ``None``（**不是** 0）。"""
    vals = _valid(series)
    return vals[-1] if vals else None


# ── 变化率 ───────────────────────────────────────────────────────────


def _pct_change(series: list[float | None], lag: int) -> list[float | None]:
    """``(x[i] - x[i-lag]) / x[i-lag]``；任一端缺失或基期为 0 → ``None``。

    基期为 0 时比值无定义（**不**返回 inf）：inf 会污染后续均值/分位统计，
    且下游无法区分"真的无穷"与"算错了"。
    """
    if lag <= 0:
        raise ValueError("lag 必须为正整数")
    out: list[float | None] = [None] * len(series)
    for i in range(lag, len(series)):
        cur, base = series[i], series[i - lag]
        if cur is None or base is None:
            continue
        if float(base) == 0.0:
            continue
        out[i] = (float(cur) - float(base)) / abs(float(base))
    return out


def yoy(series: list[float | None], periods_per_year: int = 12) -> list[float | None]:
    """同比：与 ``periods_per_year`` 期前比（月度序列 default 12 = 去年同期）。

    返回**比值**（0.05 = +5%），不是百分数。
    """
    return _pct_change(series, periods_per_year)


def mom(series: list[float | None], lag: int = 1) -> list[float | None]:
    """环比：与 ``lag`` 期前比（default 1 = 上期）。返回比值（0.05 = +5%）。"""
    return _pct_change(series, lag)


# ── 分位 / 标准化 ────────────────────────────────────────────────────


def percentile_rank(
    series: list[float | None], window: int, *, min_samples: int | None = None
) -> list[float | None]:
    """滚动分位（0–1，**越高越极端**），逐位对齐输入序列。

    口径（nearest-rank / 无插值，与 ``limit_up_service.percentile_from_series``
    同族）：对位置 ``i`` 取窗口 ``series[i-window+1 : i+1]`` 的有效样本，
    ``rank = count(v <= series[i]) / n``。``count`` 用 ``<=`` 故当前值**自身计入**，
    tie（几个相等的极值）一起计入 → 全相等时恒为 1.0。

    有效样本数 < ``min_samples``（默认 = window，即要求窗口内无缺失）→ ``None``。
    样本为 0 → ``None``（不是 0.5，也不是 0）。
    """
    if window <= 0:
        raise ValueError("window 必须为正整数")
    need = window if min_samples is None else min_samples
    out: list[float | None] = [None] * len(series)
    for i in range(len(series)):
        cur = series[i]
        if cur is None:
            continue
        lo = max(0, i - window + 1)
        vals = _valid(series[lo : i + 1])
        if len(vals) < need:
            continue
        cur_f = float(cur)
        out[i] = sum(1 for v in vals if v <= cur_f) / len(vals)
    return out


def zscore(
    series: list[float | None], window: int, *, min_samples: int | None = None
) -> list[float | None]:
    """滚动 z-score：``(x[i] - mean) / pstdev``（**总体标准差**，不是样本标准差）。

    窗口内有效样本 < ``min_samples``（默认 = window）→ ``None``；
    标准差为 0（窗口内全相等）→ ``None``（除零无定义，**不**返回 0.0，
    否则"无波动"与"无数据"在读面上不可分）。
    """
    if window <= 0:
        raise ValueError("window 必须为正整数")
    need = window if min_samples is None else min_samples
    out: list[float | None] = [None] * len(series)
    for i in range(len(series)):
        cur = series[i]
        if cur is None:
            continue
        lo = max(0, i - window + 1)
        vals = _valid(series[lo : i + 1])
        if len(vals) < need:
            continue
        sd = _pstdev(vals)
        if sd == 0.0:
            continue
        out[i] = (float(cur) - sum(vals) / len(vals)) / sd
    return out


# ── 均线 / 趋势 ──────────────────────────────────────────────────────


def moving_average(
    series: list[float | None], window: int, *, min_samples: int | None = None
) -> list[float | None]:
    """简单移动平均（旧→新）；窗口内有效样本 < ``min_samples``（默认 = window）→ ``None``。

    ``min_samples < window`` 可用于"预热期也出值"（与 ``market_sentiment_service``
    的 20 日窗口完备守卫同义：默认要求窗口无缺失，避免用半个窗口冒充 20 日均线）。
    """
    if window <= 0:
        raise ValueError("window 必须为正整数")
    need = window if min_samples is None else min_samples
    out: list[float | None] = [None] * len(series)
    for i in range(len(series)):
        lo = max(0, i - window + 1)
        vals = _valid(series[lo : i + 1])
        if len(vals) < need:
            continue
        out[i] = sum(vals) / len(vals)
    return out


def ma_bullish(
    series: list[float | None],
    short_window: int,
    long_window: int,
    *,
    min_samples: int | None = None,
) -> list[bool | None]:
    """多头排列：``MA(short) > MA(long)``（**严格大于**；等于不算多头）。

    对齐 ``stock_features.ma_bullish`` 的布尔语义。任一侧为 ``None`` → ``None``。
    """
    if short_window >= long_window:
        raise ValueError("short_window 必须小于 long_window")
    short = moving_average(series, short_window, min_samples=min_samples)
    long = moving_average(series, long_window, min_samples=min_samples)
    return [None if s is None or lo is None else s > lo for s, lo in zip(short, long, strict=True)]


def trend_slope(
    series: list[float | None], window: int, *, normalize: bool = True
) -> list[float | None]:
    """窗口内 OLS 斜率（对 ``t = 0..n-1`` 回归 ``y``）。

    ``normalize=True``（默认）**除以窗口内 y 的均值** → 无量纲、跨量级可比
    （对齐 ``stock_features.trend_slope`` 的"归一化斜率"读法）；
    均值为 0 时无法归一 → ``None``。有效样本 < 2 或 ``x`` 无方差 → ``None``。
    """
    if window <= 0:
        raise ValueError("window 必须为正整数")
    out: list[float | None] = [None] * len(series)
    for i in range(len(series)):
        lo = max(0, i - window + 1)
        pts = [(t, float(v)) for t, v in enumerate(series[lo : i + 1]) if v is not None]
        n = len(pts)
        if n < 2:
            continue
        mean_t = sum(t for t, _ in pts) / n
        mean_y = sum(y for _, y in pts) / n
        var_t = sum((t - mean_t) ** 2 for t, _ in pts)
        if var_t == 0.0:
            continue
        slope = sum((t - mean_t) * (y - mean_y) for t, y in pts) / var_t
        if not normalize:
            out[i] = slope
            continue
        if mean_y == 0.0:
            continue
        out[i] = slope / abs(mean_y)
    return out


def trend_reversals(series: list[float | None]) -> int:
    """方向反转次数（相邻两段有效差分异号即计一次）——``stock_features.trend_reversals`` 口径。

    只统计**相邻**有效值之间的一阶差分符号变化；缺失值不断开也不制造反转
    （跳过缺失后比较前后两个有效值，与"补齐后比较"结果一致）。
    """
    vals = _valid(series)
    signs: list[int] = []
    for a, b in zip(vals, vals[1:], strict=False):
        d = b - a
        if d != 0:
            signs.append(1 if d > 0 else -1)
    return sum(1 for a, b in zip(signs, signs[1:], strict=False) if a != b)


# ── 风险 ─────────────────────────────────────────────────────────────


def drawdown(series: list[float | None]) -> list[float | None]:
    """逐点回撤：``(x[i] - running_max) / running_max``，范围 ``[-1, 0]``。

    ``running_max`` 是**截至 i 的历史最高**（含 i 自己），缺失值不更新峰值。
    首个有效值回撤为 0.0；``running_max == 0`` → ``None``（除零无定义）。
    """
    out: list[float | None] = [None] * len(series)
    peak: float | None = None
    for i, v in enumerate(series):
        if v is None:
            continue
        f = float(v)
        peak = f if peak is None else max(peak, f)
        if peak == 0.0:
            continue
        out[i] = (f - peak) / abs(peak)
    return out


def max_drawdown(series: list[float | None], window: int | None = None) -> float:
    """窗口内最大回撤（**负数**，如 -0.32 = 最大跌 32%）；无有效值 → ``0.0``。

    ``window=None`` → 全序列。返回负数而非正数：与 ``drawdown`` 逐点序列同号，
    消费方不必记两套符号约定（"回撤是正的跌幅"这类隐性约定是错读之源）。
    """
    vals = _valid(series)
    if not vals:
        return 0.0
    # list 不协变：list[float] 不能赋给 list[float | None]（虽然元素类型更窄）。
    # 这里语义上完全安全（更窄的元素类型），用 ignore 而非 cast 以免掩盖真问题。
    tail: list[float | None] = vals[-window:] if window else vals  # type: ignore[assignment]
    dd = [d for d in drawdown(tail) if d is not None]
    return min(dd) if dd else 0.0


def _pstdev(vals: list[float]) -> float:
    """总体标准差（``statistics.pstdev`` 口径：除数 n，不是 n-1）。"""
    n = len(vals)
    if n == 0:
        return 0.0
    mean = sum(vals) / n
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / n)


def annualized_volatility(
    series: list[float | None],
    window: int,
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
    min_samples: int | None = None,
) -> list[float | None]:
    """滚动年化波动率：窗口内**一阶收益率**的总体标准差 × ``sqrt(periods_per_year)``。

    返回比值（0.28 = 年化 28%）。窗口内有效样本 < ``min_samples``（默认 = window）
    → ``None``；收益率少于 2 个 → ``None``（单个收益率算不出离散度）。
    基期价格为 0 的收益率无定义 → 该收益率跳过（不进样本）。
    """
    if window <= 1:
        raise ValueError("window 必须大于 1（波动率需要至少两个价格点）")
    need = window if min_samples is None else min_samples
    out: list[float | None] = [None] * len(series)
    for i in range(len(series)):
        lo = max(0, i - window + 1)
        vals = _valid(series[lo : i + 1])
        if len(vals) < need:
            continue
        rets: list[float] = []
        for a, b in zip(vals, vals[1:], strict=False):
            if a == 0.0:
                continue
            rets.append((b - a) / abs(a))
        if len(rets) < 2:
            continue
        out[i] = _pstdev(rets) * math.sqrt(periods_per_year)
    return out


def downside_volatility(
    series: list[float | None],
    window: int,
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
    min_samples: int | None = None,
) -> list[float | None]:
    """下行波动率：只取**负收益**的总体标准差 × ``sqrt(periods_per_year)``。

    无负收益（窗口内全涨）→ ``None``（不是 0.0：0 会被读成"下行风险为零"，
    而"样本里没有下跌"并不等于"没有下行风险"）。返回比值。
    """
    if window <= 1:
        raise ValueError("window 必须大于 1（波动率需要至少两个价格点）")
    need = window if min_samples is None else min_samples
    out: list[float | None] = [None] * len(series)
    for i in range(len(series)):
        lo = max(0, i - window + 1)
        vals = _valid(series[lo : i + 1])
        if len(vals) < need:
            continue
        negs: list[float] = []
        for a, b in zip(vals, vals[1:], strict=False):
            if a == 0.0:
                continue
            r = (b - a) / abs(a)
            if r < 0:
                negs.append(r)
        if len(negs) < 2:
            continue
        out[i] = _pstdev(negs) * math.sqrt(periods_per_year)
    return out


# ── 物化装配（纯计算，不落库） ────────────────────────────────────────


def stock_feature_row(
    closes: list[float | None],
    volumes: list[float | None],
    *,
    window: int,
    include_extra: list[tuple[str, list[float | None]]] | None = None,
) -> dict[str, Any]:
    """算出一行 ``stock_features`` 的**列值**（纯函数；写库由调用方用既有 upsert 做）。

    spec §S2.1 的物化判据是「复用 ``stock_features`` 的物化 + 按 ``asof_date`` 分区
    模式（**新增列**，不新增表）」——所以物化路径 = **本函数的返回值 + 既有
    ``feature_repo.upsert_features``**（``uq_stock_features_key`` 冲突即更新，重跑幂等）。
    列名逐字对齐 ``app/models/feature.py``，**不新增列、不新增表**：

    ``return_percentile`` / ``max_drawdown`` / ``trend_slope`` / ``annual_volatility``
    / ``downside_vol`` / ``ma_bullish`` / ``trend_reversals`` / ``total_return`` /
    ``avg_volume`` / ``volume_volatility``。

    ``extra`` 是既有 JSONB 列：``include_extra`` 里登记的长窗口因子（如 1260 日
    情绪分位）落这里，**无需迁移**。

    ``window`` 即 ``stock_features.window_days`` 的窗口；本函数不读它、不写它，
    只按它算列值（分区键 ``asof_date`` 由调用方给）。**所有列都落在同一个
    ``window`` 上**——包括无窗口参数的 ``trend_reversals`` / ``max_drawdown``
    （先裁剪再算），否则同一行里 `total_return` 是 60 日而 `trend_reversals`
    是全历史，两列不可比。
    """
    closes_valid = _valid(closes)
    total_return: float | None = None
    if len(closes_valid) >= 2 and closes_valid[0] != 0.0:
        total_return = (closes_valid[-1] - closes_valid[0]) / abs(closes_valid[0])

    vols_tail = _valid(volumes)[-window:]
    avg_volume: float | None = sum(vols_tail) / len(vols_tail) if vols_tail else None

    # 无窗口参数的因子（trend_reversals / max_drawdown）也要落在**同一个 window** 上：
    # 一行 stock_features 的语义是"截至 asof_date、回看 window_days"，混入全历史量
    # 会让同一行的两列量纲不同（total_return 是 60 日、trend_reversals 却是全历史）。
    closes_tail = closes[-window:]
    volumes_tail = volumes[-window:]

    # 短窗口（window < 2）时无法构成"短均线 < 长均线"，落 None 而不是抛异常
    short_window = max(1, window // 4)
    bullish: bool | None = None
    if short_window < window:
        bullish = latest_bool(ma_bullish(closes, short_window, window))

    extra: dict[str, Any] = {}
    for key, series in include_extra or []:
        extra[key] = latest(series)

    return {
        "total_return": total_return,
        "return_percentile": latest(percentile_rank(closes, window)),
        "annual_volatility": latest(annualized_volatility(closes, window)),
        "max_drawdown": max_drawdown(closes_tail),
        "downside_vol": latest(downside_volatility(closes, window)),
        "trend_slope": latest(trend_slope(closes, window)),
        "ma_bullish": bullish,
        "trend_reversals": trend_reversals(closes_tail),
        "avg_volume": avg_volume,
        "volume_volatility": latest(annualized_volatility(volumes_tail, window)),
        "extra": extra or None,
    }


def latest_bool(series: list[bool | None]) -> bool | None:
    """``bool`` 序列的最后一个非 None 值（``latest`` 的布尔版）。"""
    for v in reversed(series):
        if v is not None:
            return v
    return None
