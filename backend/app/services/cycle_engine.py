"""Cycle phase / signal / position rules engine — pure functions, no I/O.

输入为指标快照（最新值 + 能繁环比序列），输出周期阶段、交易信号、仓位建议及判定依据。
规则可读性优先；阈值调整应改 industry_registry 的配置而非本文件逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services import industry_registry as reg

# 周期阶段 key
PHASE_PROSPERITY = "prosperity"
PHASE_RECESSION = "recession"
PHASE_DEPRESSION = "depression"
PHASE_RECOVERY = "recovery"

# 猪粮比预警档（与 registry 中 hog_corn_ratio 的 warn_bands 对齐）
RATIO_LEVEL1 = 5.0   # 一级预警（深度亏损）
RATIO_LEVEL2 = 6.0   # 二级预警
RATIO_OVERHEAT = 9.0  # 过度上涨


@dataclass
class CycleInput:
    ratio: float | None = None                 # 猪粮比
    price: float | None = None                 # 生猪均价
    cost: float | None = None                  # 行业平均完全成本
    sow_mom_series: list[float] = field(default_factory=list)  # 能繁环比（旧→新）
    ratio_series: list[float] = field(default_factory=list)    # 猪粮比序列（旧→新）


@dataclass
class CycleOutput:
    phase: str
    signal: str
    reasons: list[str]
    basis: dict[str, Any]
    positions: list[reg.PositionSlice]


def count_consecutive_negative(series: list[float]) -> int:
    """Trailing count of strictly negative values."""
    n = 0
    for v in reversed(series):
        if v is not None and v < 0:
            n += 1
        else:
            break
    return n


def evaluate_pig_cycle(inp: CycleInput) -> CycleOutput:
    cfg = reg.PIG_INDUSTRY
    reasons: list[str] = []

    ratio_band = None
    ratio_def = cfg.metric("hog_corn_ratio")
    if inp.ratio is not None and ratio_def is not None:
        ratio_band = ratio_def.band_label(inp.ratio)

    loss = None
    if inp.price is not None and inp.cost is not None:
        loss = inp.price < inp.cost

    sow_decline_months = count_consecutive_negative(inp.sow_mom_series)
    sow_declining = sow_decline_months >= 3
    ratio_low = inp.ratio is not None and inp.ratio < RATIO_LEVEL2
    ratio_deep_loss = inp.ratio is not None and inp.ratio < RATIO_LEVEL1
    ratio_overheat = inp.ratio is not None and inp.ratio > RATIO_OVERHEAT

    # ── 周期阶段判定（按优先级） ──────────────────────────────────
    # 猪粮比是行业盈亏的代理指标：ratio < 6 即视为仍在磨底（即使价格短暂站上成本线），
    # 复苏必须同时满足「盈亏平衡之上 + 产能去化确认」。
    if ratio_overheat:
        phase = PHASE_PROSPERITY
        reasons.append(f"猪粮比 {inp.ratio:.2f} 处于过度上涨区间（>{RATIO_OVERHEAT}）")
    elif loss is True or (inp.ratio is not None and ratio_low):
        phase = PHASE_DEPRESSION
        if loss is True:
            reasons.append(
                f"生猪均价 {inp.price:.2f} 元/kg 低于行业平均成本 {inp.cost:.2f} 元/kg，全行业亏损"
            )
        if ratio_low:
            reasons.append(f"猪粮比 {inp.ratio:.2f} 处于{ratio_band}区间，行业处于亏损预警区间")
    elif sow_declining:
        phase = PHASE_RECOVERY
        reasons.append("猪价站上盈亏平衡线，且能繁产能持续去化")
    elif loss is False:
        phase = PHASE_PROSPERITY if not _ratio_falling(inp) else PHASE_RECESSION
        reasons.append(
            "猪价高于行业成本、盈利为正，产能仍在扩张"
            if phase == PHASE_PROSPERITY
            else "猪价高于行业成本但猪粮比自高位回落，进入衰退"
        )
    else:
        phase = PHASE_DEPRESSION
        reasons.append("关键指标缺失，按保守口径判定为萧条磨底")

    if sow_declining:
        reasons.append(f"能繁存栏环比连续 {sow_decline_months} 个月回落，产能去化进行中")

    # ── 交易信号判定 ─────────────────────────────────────────────
    if phase == PHASE_PROSPERITY and ratio_overheat:
        signal = reg.SIGNAL_SELL
        reasons.append("周期繁荣+猪粮比过热，兑现收益")
    elif phase == PHASE_DEPRESSION and sow_declining:
        signal = reg.SIGNAL_WATCH
        reasons.append("产能去化提速，左侧布局窗口临近")
        if ratio_deep_loss:
            reasons.append("猪粮比触发一级预警，国储收储预期强化底部判断")
    elif phase == PHASE_DEPRESSION:
        signal = reg.SIGNAL_EMPTY
        reasons.append("行业亏损且去化未确认，信号转防守")
    elif phase == PHASE_RECOVERY:
        signal = reg.SIGNAL_BUY
        reasons.append("供给收缩驱动盈利修复，右侧趋势确认")
    else:
        signal = reg.SIGNAL_WATCH
        reasons.append("周期中段，跟踪等待更明确信号")

    basis = {
        "ratio": inp.ratio,
        "ratio_band": ratio_band,
        "price": inp.price,
        "cost": inp.cost,
        "loss": loss,
        "sow_mom_series": inp.sow_mom_series[-6:],
        "sow_consecutive_decline": sow_decline_months,
    }
    positions = cfg.position_template(signal)
    return CycleOutput(
        phase=phase,
        signal=signal,
        reasons=reasons,
        basis=basis,
        positions=list(positions),
    )


def _ratio_falling(inp: CycleInput) -> bool:
    """猪粮比是否自近期高位回落（繁荣/衰退分界辅助）。"""
    series = [v for v in inp.ratio_series if v is not None][-6:]
    if len(series) < 3 or inp.ratio is None:
        return False
    recent_avg = sum(series[:-1]) / len(series[:-1])
    return inp.ratio < recent_avg


def phase_index(cfg: reg.IndustryConfig, phase_key: str) -> int:
    for i, p in enumerate(cfg.phases):
        if p.key == phase_key:
            return i
    return -1
