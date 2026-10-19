"""情绪周期阶段（**启发式规则**，不是预测模型）。

为什么需要它：单指标只能回答"今天多少"，回答不了"处在周期的哪一段"。短线用户真正
用来做判断的语境是"冰点 / 修复 / 发酵 / 高潮 / 退潮"。

三条纪律：
1. **规则透明、可复核**：只用涨停家数、炸板率、最高板三个维度（都已在
   `market_sentiment_daily` 冻结），阈值写死在本文件，阶段随时可被读者用同一套数据复算；
2. **PIT**：每一天的分位只用**当天及之前**最多 250 个交易日计算，绝不看未来；
3. **不硬凑**：分位样本 < 20 天 → `unknown`（数据不足），不拿 0 或中位数冒充阶段。

阈值（0–100 分位）：
| 条件 | 阶段 |
|---|---|
| 涨停 ≤ 20 且 炸板 ≥ 40 | 冰点 |
| 涨停 < 50 且 炸板 ≥ 60 | 退潮 |
| 涨停 < 50 | 修复 |
| 涨停 ≥ 85 且 最高板 ≥ 80 | 高潮 |
| 炸板 < 60 | 发酵 |
| 其余（涨停 ≥ 50 且 炸板 ≥ 60） | 分歧 |

这是**描述性标签**，不是交易信号；阈值变化必须 bump `REGIME_VERSION` 并同步
`docs/design/limit-up-sentiment.md` §3.7。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

#: 口径版本：阈值/维度一改就 bump（前端/文档按它对齐）。
REGIME_VERSION = "s1.r2"
#: 分位窗口（交易日）。与 `/sentiment/trends` 的默认窗口一致。
REGIME_WINDOW = 250
#: 分位的最小样本：低于它判"数据不足"，不出阶段。
MIN_REGIME_SAMPLES = 20
#: 阶段持续性：新阶段必须连续出现 N 天才切换（否则沿用上一阶段）。
#: 否则 `冰点/修复/退潮` 会按天翻转，背景带变成噪声——阶段是"周期"不是"日频信号"。
REGIME_PERSIST_DAYS = 2

Stage = Literal["ice", "ebb", "repair", "ferment", "climax", "mixed", "unknown"]
Tone = Literal["cold", "neutral", "hot", "extreme", "muted"]

STAGE_LABEL: dict[Stage, str] = {
    "ice": "冰点",
    "ebb": "退潮",
    "repair": "修复",
    "ferment": "发酵",
    "climax": "高潮",
    "mixed": "分歧",
    "unknown": "数据不足",
}
STAGE_TONE: dict[Stage, Tone] = {
    "ice": "cold",
    "ebb": "cold",
    "repair": "neutral",
    "ferment": "hot",
    "climax": "extreme",
    "mixed": "neutral",
    "unknown": "muted",
}


@dataclass(frozen=True)
class StageInputs:
    zt_count: float | None
    zt_pct: float | None
    broken_rate: float | None
    broken_pct: float | None
    max_streak: float | None
    streak_pct: float | None


def trailing_percentiles(
    values: list[float | None], *, window: int = REGIME_WINDOW
) -> list[float | None]:
    """PIT 分位：第 i 天只用 `[i-window+1, i]` 的有效样本（含当天），`<=` 口径。"""
    out: list[float | None] = []
    for i, value in enumerate(values):
        if value is None:
            out.append(None)
            continue
        prior = [v for v in values[max(0, i - window + 1) : i + 1] if v is not None]
        if len(prior) < MIN_REGIME_SAMPLES:
            out.append(None)
            continue
        out.append(round(100 * sum(1 for v in prior if v <= value) / len(prior), 2))
    return out


def classify(
    *,
    zt_count: float | None,
    zt_pct: float | None,
    broken_pct: float | None,
    max_streak: float | None,
    streak_pct: float | None,
) -> Stage:
    """三维**值 + 分位** → 阶段（规则见模块 docstring）。

    不能只看分位：当窗口内数值几乎不变（例如 20 天全是 20 家），分位会饱和到 100，
    把"低位横盘"误判成高潮。故 climax/ice 另加**绝对水位**守卫；这也是阶段规则必须
    写死、可复核的原因。
    """
    if zt_pct is None or broken_pct is None or zt_count is None:
        return "unknown"
    if zt_count <= 40 and zt_pct <= 25 and broken_pct <= 60:
        return "ice"
    if zt_pct < 50 and broken_pct >= 60:
        return "ebb"
    if zt_pct < 50:
        return "repair"
    if zt_count >= 80 and zt_pct >= 85 and (max_streak or 0) >= 5 and (streak_pct or 0) >= 80:
        return "climax"
    if broken_pct < 60:
        return "ferment"
    return "mixed"


def _num(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return None if value is None else float(value)


def build_regime(series: list[dict[str, Any]]) -> dict[str, Any]:
    """整段序列 → 逐日阶段 + 合并后的区段 + 当前状态。

    `series` 是 `sentiment_series_row` 的投影（升序）。返回值可 JSON 序列化，
    直接进 `/sentiment/pulse` 与指标详情响应。
    """
    if not series:
        return {
            "version": REGIME_VERSION,
            "current": None,
            "segments": [],
        }

    zt = [_num(r, "zt_count") for r in series]
    broken = [_num(r, "broken_rate") for r in series]
    streak = [_num(r, "max_streak") for r in series]
    zt_pct = trailing_percentiles(zt)
    broken_pct = trailing_percentiles(broken)
    streak_pct = trailing_percentiles(streak)

    raw_stages: list[Stage] = [
        classify(
            zt_count=zt[i],
            zt_pct=zt_pct[i],
            broken_pct=broken_pct[i],
            max_streak=streak[i],
            streak_pct=streak_pct[i],
        )
        for i in range(len(series))
    ]
    stages: list[Stage] = []
    for i, stage in enumerate(raw_stages):
        if i == 0 or stage == (stages[-1] if stages else None):
            stages.append(stage)
            continue
        # 只有新阶段连续出现 REGIME_PERSIST_DAYS 天才切换，否则沿用上一阶段
        window = raw_stages[i : i + REGIME_PERSIST_DAYS]
        if len(window) == REGIME_PERSIST_DAYS and all(x == stage for x in window):
            stages.append(stage)
        else:
            stages.append(stages[-1])
    # 最后一天以**当日原始判定**为准：平滑是为了历史段不抖，但"当前阶段"必须反映今天。
    # 这最多产生一个 1 天的末段（并在 UI 的 since/days 上如实显示），好过把今天标成昨天的阶段。
    if stages and raw_stages and stages[-1] != raw_stages[-1]:
        stages[-1] = raw_stages[-1]

    segments: list[dict[str, Any]] = []
    start = 0
    for i in range(1, len(series) + 1):
        if i == len(series) or stages[i] != stages[start]:
            stage = stages[start]
            segments.append(
                {
                    "stage": stage,
                    "label": STAGE_LABEL[stage],
                    "tone": STAGE_TONE[stage],
                    "from": series[start]["trade_date"],
                    "to": series[i - 1]["trade_date"],
                    "days": i - start,
                }
            )
            start = i

    last = len(series) - 1
    last_stage = stages[last]
    last_segment = segments[-1]
    current = {
        "stage": last_stage,
        "label": STAGE_LABEL[last_stage],
        "tone": STAGE_TONE[last_stage],
        "since": last_segment["from"],
        "days": last_segment["days"],
        "inputs": [
            {
                "key": "zt_count",
                "label": "涨停家数",
                "value": zt[last],
                "percentile": zt_pct[last],
                "unit": "count",
            },
            {
                "key": "broken_rate",
                "label": "炸板率",
                "value": broken[last],
                "percentile": broken_pct[last],
                "unit": "ratio",
            },
            {
                "key": "max_streak",
                "label": "最高板",
                "value": streak[last],
                "percentile": streak_pct[last],
                "unit": "board",
            },
        ],
    }
    return {
        "version": REGIME_VERSION,
        "current": current,
        "segments": segments,
    }


def zt_percentile_series(series: list[dict[str, Any]]) -> list[float | None]:
    """供 pulse 的"情绪 vs 指数"对比复用：涨停家数的 PIT 分位。"""
    return trailing_percentiles([_num(r, "zt_count") for r in series])


def dates_of(series: list[dict[str, Any]]) -> list[date]:
    return [r["trade_date"] for r in series]
