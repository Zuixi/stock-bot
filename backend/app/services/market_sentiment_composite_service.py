"""spec §S1.13 情绪 · **市场情绪综合指数** —— 计算 + 落库（`industry_key='market'`）.

## 这个模块是什么

把 S1.8–S1.12 已登记的情绪指标里**选定的 11 条日频腿**做**滚动分位标准化**，再按
**公开权重表**加权合成一条 0–100 的序列，写进 `industry_metrics`
（`industry_key='market'`、`stock_id=0`、`metric_key='market_sentiment_composite'`）。
**通用**读面不在这里 —— 走 S1.2 已建好的 `GET /api/v1/metrics/{latest,series}`
（registry 驱动 + 四徽章 + Redis 缓存）。本模块另有一条**专用读路**（文件末尾）：
`GET /api/v1/market/sentiment/composite`，回答"今天的值在自身历史里的分位"——
分位必须由后端算（Ruling 4），前端 hero 不做统计。**不新建表、不新增迁移。**

**权重 / 分位窗口 / 最小样本量 / 缺失处置 / 版本号的唯一事实源是
`industry_registry` 的 `COMPOSITE_*` 常量**（本模块只读取、不另存一份），公开转录在
`docs/design/metric-dictionary.md` §4.2「市场情绪综合指数（spec §S1.13）」。本文件里
**没有第二个权重表**。

## 方法（v1，逐字见字典；这里是可执行版本）

1. **成分分位标准化**：对每个成分 `i`，取它**自身的观测行**序列 `v_i(1..n)`（升序），
   反向腿先取相反数（`direction = -1` ⇒ `-v`，把"越高越恐慌"翻成"越高越乐观"），
   再用 `indicators.percentile_rank(window=252, min_samples=126)` 算滚动分位
   `p_i ∈ [0,1]`（nearest-rank / 无插值 / 只用有效样本；与 registry `pct_rank` 因子
   同一函数、同一默认窗口 —— **不另写一套分位**）。窗口内有效样本 < 126 的行
   **不产出分位** ⇒ 该成分当日**不参与**（不估算、不按 0 算）。
2. **加权合成**：`value = 100 × Σ_{i∈参与集} w'_i × p_i`，`w'_i = w_i / Σ_{j∈参与集} w_j`
   （**重新归一化**）。权重 `w_i` 与方向 `direction_i` 都在 registry 的
   :data:`~app.services.industry_registry.COMPOSITE_CONSTITUENTS` 里写死。
3. **成分缺失的处置 = `drop_and_renormalise`**：当日无行 / 值为 NULL / 分位样本不足的
   成分**被剔除**，剩余成分权重按上式归一化。**绝不**静默用 0、**绝不**插值、
   **绝不**前向填充。当天的实际参与集与生效权重落 `extra`（可复算性的前提）。
4. **合成日的日历** = "当天至少有一个成分有值"的那些日（union，不是求交）——
   于是合成指数的历史**尽可能长**（实测 2010-04 起有行、2015-08 起有值），
   而"当天谁参与了"逐日可查。
5. **NULL 不落数字**：参与成分数 < `COMPOSITE_MIN_PARTICIPANTS`(2) 或生效权重之和
   < `COMPOSITE_MIN_EFFECTIVE_WEIGHT`(0.20) ⇒ `value = NULL` + `extra.missing_reason`
   （`insufficient_participants` / `insufficient_effective_weight`）。

## PIT 纪律（ADR 0011 / Ruling 34-35）

`published_at` = **当日全部成分行 `published_at` 的最大值**（精确到时刻，带时区）——
合成的值不可能在"当天最后一个成分定型"之前可得；用**全部**成分（含当日缺值的成分）
而不是只用参与成分，是因为"谁参与"这件事本身也要等各成分的可得性落定才知道。
`extra.pit_basis = 'max_constituent_published_at'` 记录该基准，`extra.latest_constituent`
给出决定该时刻的那条腿。成分行缺 `published_at` ⇒ **响亮失败**（`RuntimeError`），
**绝不**静默回退 `period`。

## A/B/C 归类（ADR 0014）

**B 类可回算**：只用"截至该日已发生的成分观测"（分位窗口是回看窗口，不含未来行），
可由成分序列 + 公开权重逐日重算。回算必须**指名版本**
（`caliber_version = COMPOSITE_CALIBER_VERSION = 's1.13-v1'`）—— 换权重/换窗口 = bump
版本 + 留断点，两版的行不可混用。
**无 A 类**（不消费只给当下的盘中明细）、**无 C 类**（不用成分归属快照）。

## 允许进信号链路（spec Ruling 65）

本指标是**自建、算法公开、可逐日复算**的合成指数 ⇒ **允许**被信号链路消费
（S2.2 的示例规则 `ctx.percentile("market_sentiment_composite", window=1260)` 正是它）。
被禁入信号链路的只有**第三方机构编制**的情绪指数（算法不公开、无法复算；
本 scope 内即 `public_fund_equity_position` —— 它被 :data:`COMPOSITE_FORBIDDEN_CONSTITUENT_KEYS`
机械挡在成分表外）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.industry_research import IndustryMetric
from app.repositories import industry_metric_repo
from app.schemas.limit_up import MIN_PERCENTILE_SAMPLES
from app.services import industry_registry as registry
from app.services.indicators import percentile_rank
from app.services.market_activity_service import upsert_metric_rows

logger = logging.getLogger(__name__)

METRIC_KEY = registry.COMPOSITE_METRIC_KEY
SOURCE = registry.COMPOSITE_SOURCE
CALIBER_VERSION = registry.COMPOSITE_CALIBER_VERSION
UNIT = "分"
FREQ = "daily"
TIER = registry.TIER_DERIVED
CONSTITUENTS: tuple[registry.CompositeConstituent, ...] = registry.COMPOSITE_CONSTITUENTS
CONSTITUENT_KEYS: tuple[str, ...] = tuple(c.metric_key for c in CONSTITUENTS)
WEIGHT_BY_KEY: dict[str, float] = {c.metric_key: c.weight for c in CONSTITUENTS}
DIRECTION_BY_KEY: dict[str, int] = {c.metric_key: c.direction for c in CONSTITUENTS}

#: **发布闸门**：生效权重之和低于它 ⇒ 该日不发布值（`value=NULL` +
#: `below_publish_weight`）。唯一事实源是 registry，本模块只读取、不另存一份数。
PUBLISH_MIN_WEIGHT: float = registry.COMPOSITE_PUBLISH_MIN_WEIGHT

#: 分位不足 / 当日缺值的**逐成分**原因码（落 `extra.constituents[k].reason`）。
CONSTITUENT_REASONS: dict[str, str] = {
    "ok": "当日有值且窗口有效样本 >= min_samples ⇒ 参与合成",
    "no_row_today": "当日该成分在 industry_metrics 无行（源侧未更新或未回补）",
    "value_missing": "当日该成分有行但 value 为 NULL（缺数，**不是 0**）",
    "window_below_min_samples": "窗口内有效样本 < min_samples ⇒ 不产出分位（不估算）",
}


@dataclass(frozen=True)
class ConstituentObservation:
    """一个成分的一天：`period` + 值（可 NULL）+ 该行的 `published_at`。"""

    period: date
    value: float | None
    published_at: Any  # datetime | None（None 会在落库前触发响亮失败）


@dataclass(frozen=True)
class CompositeDay:
    """合成指数的一天（= 库内将落的一行）。"""

    period: date
    value: float | None
    missing_reason: str | None
    published_at: Any
    extra: dict[str, Any]


@dataclass(frozen=True)
class BackfillStats:
    """一次区间落库的可核对统计（回补脚本直接打印它）。"""

    period_lo: date | None
    period_hi: date | None
    rows_upserted: int
    value_days: int
    null_days: int
    missing_reasons: dict[str, int]
    #: 逐成分的"参与天数"（`{'qvix_50etf': 2680, ...}`）—— 直接回答"哪些成分在多少天生效"。
    participating_days: dict[str, int]
    #: 参与集**连续相同**的日期区间（"哪些成分在哪些日期区间生效"的机读答案）。
    eras: list[dict[str, Any]]
    #: 因 `caliber_version` 与 registry 不符而被丢弃的成分行数（口径断点，不混算）。
    rows_dropped_other_caliber: dict[str, int]
    sources: dict[str, str]
    #: 本次**过不了发布闸门**（`effective_weight_sum < COMPOSITE_PUBLISH_MIN_WEIGHT`）
    #: 而被改判为 NULL 的天数。这些天仍有行、仍可复核，只是不作为值发布。
    days_below_publish_weight: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": METRIC_KEY,
            "caliber_version": CALIBER_VERSION,
            "period_lo": self.period_lo.isoformat() if self.period_lo else None,
            "period_hi": self.period_hi.isoformat() if self.period_hi else None,
            "rows_upserted": self.rows_upserted,
            "value_days": self.value_days,
            "null_days": self.null_days,
            "missing_reasons": self.missing_reasons,
            "participating_days": self.participating_days,
            "eras": self.eras,
            "rows_dropped_other_caliber": self.rows_dropped_other_caliber,
            "sources": self.sources,
            "publish_min_weight": PUBLISH_MIN_WEIGHT,
            "days_below_publish_weight": self.days_below_publish_weight,
        }


@dataclass
class CompositeCoverage:
    """合成指数的覆盖率对账（按**自身落库行**为分母，与成分各自的日历无关）。"""

    period_from: date | None = None
    period_to: date | None = None
    rows: int = 0
    value_days: int = 0
    null_days: int = 0
    missing_reasons: dict[str, int] = field(default_factory=dict)
    first_value_day: date | None = None
    last_value_day: date | None = None
    participating_days: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_key": METRIC_KEY,
            "period_from": self.period_from.isoformat() if self.period_from else None,
            "period_to": self.period_to.isoformat() if self.period_to else None,
            "rows": self.rows,
            "value_days": self.value_days,
            "null_days": self.null_days,
            "first_value_day": self.first_value_day.isoformat() if self.first_value_day else None,
            "last_value_day": self.last_value_day.isoformat() if self.last_value_day else None,
            "missing_reasons": self.missing_reasons,
            "participating_days": self.participating_days,
        }


# ── 纯函数（无 IO：可复算性的核心）────────────────────────────────────


def constituent_percentiles(
    observations: Sequence[ConstituentObservation],
    *,
    direction: int,
    window: int = registry.COMPOSITE_PERCENTILE_WINDOW,
    min_samples: int = registry.COMPOSITE_PERCENTILE_MIN_SAMPLES,
) -> dict[date, float]:
    """**一个**成分的滚动分位 → `{period: p}`（`p ∈ [0,1]`，样本不足的日**不在**返回里）.

    - 窗口单位 = 该成分**自身的观测行**（日频成分即 ≈252 个交易日），与
      `FACTORS['pct_rank']` 的默认窗口 252 同值；
    - `direction = -1` ⇒ 先对序列取相反数再算分位（"越高越乐观"这条轴统一）；
    - `min_samples` 未达 ⇒ 该日**不返回**（调用方按"该成分当日不参与"处理，
      **不是** 0 分位、**不是** 0.5）；
    - 重复 `period` 不允许（同一成分同一天一行由唯一键保证；这里显式守卫）。
    """
    ordered = sorted(observations, key=lambda o: o.period)
    periods = [o.period for o in ordered]
    if len(set(periods)) != len(periods):
        raise ValueError("同一成分的观测出现重复 period —— 唯一键被绕过")
    series = [None if o.value is None else float(o.value) * direction for o in ordered]
    ranks = percentile_rank(series, window, min_samples=min_samples)
    return {p: r for p, r in zip(periods, ranks, strict=True) if r is not None}


def _latest_published_at(
    published: Sequence[tuple[Any, str]],
) -> tuple[Any, str]:
    """当日**全部**成分行的 `published_at` 最大值 + 决定它的 key.

    `published` 必须是 (datetime, metric_key) 对；空列表 ⇒ 调用方有 bug（日历由"至少一个
    成分有值"构造，不会出现空集）。缺 `published_at` 的行在构造 `published` 之前就已
    响亮失败（Ruling 34/35：**不得静默回退 `period`**）。
    """
    if not published:
        raise RuntimeError("当日没有任何成分行 —— 合成日历构造有 bug")
    return max(published, key=lambda item: item[0])


def compose_series(
    observations: Mapping[str, Sequence[ConstituentObservation]],
    *,
    day_lo: date | None = None,
    day_hi: date | None = None,
    constituents: Iterable[registry.CompositeConstituent] = CONSTITUENTS,
    window: int = registry.COMPOSITE_PERCENTILE_WINDOW,
    min_samples: int = registry.COMPOSITE_PERCENTILE_MIN_SAMPLES,
    min_participants: int = registry.COMPOSITE_MIN_PARTICIPANTS,
    min_effective_weight: float = registry.COMPOSITE_MIN_EFFECTIVE_WEIGHT,
) -> list[CompositeDay]:
    """成分观测 → 合成指数序列（**纯函数**，无 DB、无时钟）.

    日历 = "当天至少一个成分有值"的日期的并集（可被 `day_lo` / `day_hi` 裁剪）。
    每一天的 `extra` 都带：实际参与集、生效权重、逐成分明细（值/分位/权重/不参与原因）、
    覆盖率、PIT 基准 —— 于是"从成分重算"所需的一切都在库里。
    """
    specs = list(constituents)
    weight_of: dict[str, float] = {s.metric_key: s.weight for s in specs}
    pct_by_key: dict[str, dict[date, float]] = {}
    for spec in specs:
        pct_by_key[spec.metric_key] = constituent_percentiles(
            observations.get(spec.metric_key, ()),
            direction=spec.direction,
            window=window,
            min_samples=min_samples,
        )
    by_key: dict[str, dict[date, ConstituentObservation]] = {
        spec.metric_key: {o.period: o for o in observations.get(spec.metric_key, ())}
        for spec in specs
    }

    calendar: set[date] = set()
    for spec in specs:
        for obs in observations.get(spec.metric_key, ()):
            if obs.value is not None and (day_lo is None or obs.period >= day_lo):
                if day_hi is None or obs.period <= day_hi:
                    calendar.add(obs.period)

    out: list[CompositeDay] = []
    for day in sorted(calendar):
        detail: dict[str, Any] = {}
        participants: list[str] = []
        published_all: list[tuple[Any, str]] = []
        for spec in specs:
            key = spec.metric_key
            obs_row = by_key[key].get(day)
            pct = pct_by_key[key].get(day)
            if obs_row is None:
                reason = "no_row_today"
            elif obs_row.value is None:
                reason = "value_missing"
            elif pct is None:
                reason = "window_below_min_samples"
            else:
                reason = "ok"
            participated = reason == "ok"
            if participated:
                participants.append(key)
            if obs_row is not None:
                if obs_row.published_at is None:
                    raise RuntimeError(
                        f"成分 {key} 在 {day} 的行缺 published_at —— 合成的 PIT 无从确定；"
                        "**不得**回退 period（ADR 0011 / Ruling 34-35）。请先修复该成分的落库。"
                    )
                published_all.append((obs_row.published_at, key))
            detail[key] = {
                "value": None if obs_row is None or obs_row.value is None else float(obs_row.value),
                "direction": spec.direction,
                "percentile": None if pct is None else pct,
                "weight_configured": spec.weight,
                "weight_effective": None,
                "participated": participated,
                "reason": reason,
            }
        if not published_all:  # pragma: no cover — 日历由"至少一个有值"构造，不会发生
            raise RuntimeError(f"{day} 落在合成日历里却没有任何成分行 —— 日历构造有 bug")
        latest_at, latest_key = _latest_published_at(published_all)
        # 权重取**本次传入的**成分表（不只是模块级 registry 表）：定制成分表（测试/未来版本）
        # 也必须走同一条归一化路径，否则"公开方法"与实际计算会分叉。
        weight_sum = sum(weight_of[k] for k in participants)
        effective: dict[str, float] = {}
        if weight_sum > 0:
            # **不四舍五入**：生效权重与分位都以 double 全精度落 extra —— 从 extra 重算
            # 必须与 value 逐位一致（6 位小数的权重会把 62.78846… 重算成 62.7884）。
            effective = {k: weight_of[k] / weight_sum for k in participants}
        for key, w in effective.items():
            detail[key]["weight_effective"] = w
        missing_reason: str | None = None
        value: float | None = None
        if len(participants) < min_participants:
            missing_reason = "insufficient_participants"
        elif weight_sum < min_effective_weight:
            missing_reason = "insufficient_effective_weight"
        else:
            total = sum(effective[k] * pct_by_key[k][day] for k in participants)
            value = round(100.0 * total, 4)
        extra: dict[str, Any] = {
            "method": "rolling_percentile_weighted_sum",
            "method_version": CALIBER_VERSION,
            "renormalization": registry.COMPOSITE_RENORMALIZATION,
            "percentile_window": window,
            "percentile_min_samples": min_samples,
            "percentile_scale": "0-1（nearest-rank / 无插值 / 只用有效样本）",
            "value_definition": (
                "value = 100 × Σ_(i∈参与集) w'_i × p_i，w'_i = w_i / Σ_(j∈参与集) w_j"
            ),
            "value_scale": "0-100（50 ≈ 历史中位）",
            "min_participants": min_participants,
            "min_effective_weight": min_effective_weight,
            "n_constituents": len(specs),
            "n_participants": len(participants),
            "participants": participants,
            "effective_weights": effective,
            "effective_weight_sum": round(weight_sum, 6),
            "coverage": round(len(participants) / len(specs), 6),
            "constituents": detail,
            "pit_basis": "max_constituent_published_at",
            "latest_constituent": {
                "metric_key": latest_key,
                "published_at": latest_at.isoformat(),
            },
        }
        if missing_reason:
            extra["missing_reason"] = missing_reason
        out.append(
            CompositeDay(
                period=day,
                value=value,
                missing_reason=missing_reason,
                published_at=latest_at,
                extra=extra,
            )
        )
    return out


def participant_eras(days: Sequence[CompositeDay]) -> list[dict[str, Any]]:
    """把**有值**的日子按"参与集连续相同"切成区间 —— "哪些成分在哪些日期生效"的机读答案.

    每个区间：起止日、天数、参与集（升序）、生效权重（区间内逐日归一化后的稳定值）、
    生效权重之和。NULL 日不属于任何区间（它没有生效权重）。
    """
    eras: list[dict[str, Any]] = []
    for day in days:
        if day.value is None:
            continue
        keys = tuple(sorted(day.extra["participants"]))
        if eras and eras[-1]["signature"] == keys:
            eras[-1]["to"] = day.period.isoformat()
            eras[-1]["days"] += 1
            continue
        eras.append(
            {
                "signature": keys,
                "from": day.period.isoformat(),
                "to": day.period.isoformat(),
                "days": 1,
                "participants": list(keys),
                "effective_weights": dict(day.extra["effective_weights"]),
                "effective_weight_sum": day.extra["effective_weight_sum"],
            }
        )
    return eras


def composite_stats(days: Sequence[CompositeDay], rows_upserted: int) -> BackfillStats:
    """一组合成日 + 落库行数 → :class:`BackfillStats`（纯统计，无 IO）。"""
    value_days = [d for d in days if d.value is not None]
    reasons: dict[str, int] = {}
    participating: dict[str, int] = dict.fromkeys(CONSTITUENT_KEYS, 0)
    for day in days:
        if day.missing_reason:
            reasons[day.missing_reason] = reasons.get(day.missing_reason, 0) + 1
        for key in day.extra["participants"]:
            participating[key] = participating.get(key, 0) + 1
    periods = [d.period for d in days]
    return BackfillStats(
        period_lo=periods[0] if periods else None,
        period_hi=periods[-1] if periods else None,
        rows_upserted=rows_upserted,
        value_days=len(value_days),
        null_days=len(days) - len(value_days),
        missing_reasons=dict(sorted(reasons.items())),
        participating_days={k: v for k, v in participating.items() if v},
        eras=participant_eras(days),
        rows_dropped_other_caliber={},
        sources={METRIC_KEY: SOURCE},
    )


# ── 发布闸门（纯函数：与"怎么算"无关，只决定"算出来的数能不能当值发布"）──────
#
# 背景（2026-10-16 产品裁决）：`compose_series` 的方法下限是 0.20，于是 2026-10-12
# 那种"各腿可得日不齐"的日子（3/11 条腿、0.23）会带着一个**数字**发布出去，
# 而它与齐备日（10 条腿、0.88）不是同一个东西。闸门把这类日子改判为"无值 + 原因码"，
# 值仍在 `extra.withheld_value` 里可复核 —— **不删、不插值、不前向填充**。


def effective_weight(extra: Mapping[str, Any]) -> float | None:
    """`extra.effective_weight_sum` → float；缺键 / 非数字（含 bool）→ `None`。

    `None` 的含义是"**说不清权重**"（老行、异常行），调用方按**不可发布**处理：
    宁可退回上一个说得清的齐备日，也不拿一个权重不明的数字当头条。
    """
    value = extra.get("effective_weight_sum")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def is_publishable(day: CompositeDay, *, min_weight: float = PUBLISH_MIN_WEIGHT) -> bool:
    """该日是否过**发布闸门**（`effective_weight_sum >= min_weight`）。

    值本身为 NULL 的日子不算"未过闸门"（它另有原因码，如 `insufficient_participants`），
    故调用方判"被闸门改判"时必须同时看 `value is not None`。
    """
    weight = effective_weight(day.extra)
    return weight is not None and weight >= min_weight


def apply_publish_gate(
    days: Sequence[CompositeDay], *, min_weight: float = PUBLISH_MIN_WEIGHT
) -> list[CompositeDay]:
    """未过闸门的**有值**日子 → `value=NULL` + `missing_reason='below_publish_weight'`.

    **为什么落 NULL 而不是不落行**（缺数可见原则）：行不存在时"各腿没齐"与"任务没跑"
    在库里长得一样；落 NULL + 原因码，则该日仍在、参与集/生效权重/逐腿分位仍可复核，
    只是不作为值发布。`extra.withheld_value` 保留当时算出的数（调试与对账用），
    `extra.publish_min_weight` 记下当时生效的闸门，可复算"为什么这天没值"。

    已经是 NULL 的日子**原样返回**（原因码由上游方法决定，闸门不覆盖它）。
    纯函数：同一输入恒得同一输出（无时钟、无 IO），故收敛器可反复重跑。
    """
    out: list[CompositeDay] = []
    for day in days:
        if day.value is None or is_publishable(day, min_weight=min_weight):
            out.append(day)
            continue
        extra = dict(day.extra)
        extra["missing_reason"] = "below_publish_weight"
        extra["publish_min_weight"] = min_weight
        extra["withheld_value"] = day.value
        out.append(
            CompositeDay(
                period=day.period,
                value=None,
                missing_reason="below_publish_weight",
                published_at=day.published_at,
                extra=extra,
            )
        )
    return out


def below_publish_weight_days(
    days: Sequence[CompositeDay], *, min_weight: float = PUBLISH_MIN_WEIGHT
) -> int:
    """`days` 里被闸门改判的天数（`composite_stats` 的统计口径，与落库口径同一判据）."""
    return sum(
        1
        for day in days
        if day.value is not None and not is_publishable(day, min_weight=min_weight)
    )


# ── 落库 ───────────────────────────────────────────────────────────────


def _row(day: CompositeDay) -> dict[str, Any]:
    """一个 :class:`CompositeDay` → `industry_metrics` 一行（PIT 两列恒填满）."""
    extra = dict(day.extra)
    if day.missing_reason:
        extra["missing_reason"] = day.missing_reason
    return {
        "industry_key": "market",
        "stock_id": 0,
        "metric_key": METRIC_KEY,
        "source": SOURCE,
        "source_tier": TIER,
        "freq": FREQ,
        "period": day.period,
        "value": day.value,
        "unit": UNIT,
        "extra": extra,
        "published_at": day.published_at,
        "caliber_version": CALIBER_VERSION,
    }


def declared_caliber(metric_key: str) -> str | None:
    """成分 `metric_key` 在 registry 里声明的口径版本（None = 尚未登记具体口径）.

    只认**当前口径**的行：同一成分混着两版口径会算出一条不存在的分位序列
    （`market_activity_service` 的增量缓存同款纪律）。
    """
    _, metric = registry.find_metric(metric_key) or (None, None)
    return metric.caliber_version if metric is not None else None


async def load_constituent_observations(
    db: AsyncSession,
    *,
    metric_keys: Sequence[str] = CONSTITUENT_KEYS,
) -> tuple[dict[str, list[ConstituentObservation]], dict[str, int]]:
    """从 `industry_metrics` 读全部成分历史 → `({key: [obs...]}, 被丢弃的口径断点行数)`.

    **读全量**（不按回补窗口裁剪）：分位是滚动窗口，裁掉窗口前的历史会算错——
    回补区间只决定**写哪些行**，不决定**用哪些历史**。
    """
    stmt = (
        select(
            IndustryMetric.metric_key,
            IndustryMetric.period,
            IndustryMetric.value,
            IndustryMetric.published_at,
            IndustryMetric.caliber_version,
        )
        .where(
            IndustryMetric.industry_key == "market",
            IndustryMetric.stock_id == 0,
            IndustryMetric.metric_key.in_(list(metric_keys)),
        )
        .order_by(IndustryMetric.metric_key, IndustryMetric.period)
    )
    dropped: dict[str, int] = {}
    out: dict[str, list[ConstituentObservation]] = {k: [] for k in metric_keys}
    for row in (await db.execute(stmt)).all():
        want = declared_caliber(row.metric_key)
        if want is not None and row.caliber_version != want:
            dropped[row.metric_key] = dropped.get(row.metric_key, 0) + 1
            continue
        out.setdefault(row.metric_key, []).append(
            ConstituentObservation(
                period=row.period,
                value=None if row.value is None else float(row.value),
                published_at=row.published_at,
            )
        )
    return out, dropped


async def upsert_composite_range(
    db: AsyncSession,
    *,
    day_lo: date | None = None,
    day_hi: date | None = None,
    observations: Mapping[str, Sequence[ConstituentObservation]] | None = None,
    dropped_other_caliber: dict[str, int] | None = None,
) -> BackfillStats:
    """算 `[day_lo, day_hi]` 的合成指数并**幂等**写进 `industry_metrics`。

    幂等：唯一键 + `ON CONFLICT DO UPDATE`，重跑同一区间新增行数 = 0（ADR 0014 的 B 类）。
    调用方负责 `await db.commit()`（本层不替上层决定事务边界）。
    """
    if observations is None:
        observations, dropped = await load_constituent_observations(db)
    else:
        dropped = dict(dropped_other_caliber or {})
    days = compose_series(observations, day_lo=day_lo, day_hi=day_hi)
    if not days:
        # **响亮失败**：区间内一个合成日都没有，只可能是成分没回补或窗口参数错。
        # 静默返回 0 行会让"回补跑过了"与"回补没跑"长得一样。
        raise CompositeSourceError(
            f"§S1.13 在 [{day_lo}, {day_hi}] 一个合成日都没产出 —— 成分行缺失或窗口错，"
            "**不得**静默成功（那会把「没跑」伪装成「跑过」）。请先回补成分。"
        )
    # 发布闸门在**落库前**生效：算得出的薄日改判为 NULL + 原因码（有行、无值），
    # 于是读面（hero）与通用读面都不会把一个 3/11 条腿的数字当成当天的合成值。
    published = apply_publish_gate(days)
    rows = [_row(day) for day in published]
    stats = composite_stats(days, 0)
    upserted = await upsert_metric_rows(db, rows)
    return BackfillStats(
        period_lo=stats.period_lo,
        period_hi=stats.period_hi,
        rows_upserted=upserted,
        value_days=stats.value_days,
        null_days=stats.null_days,
        missing_reasons=stats.missing_reasons,
        participating_days=stats.participating_days,
        eras=stats.eras,
        rows_dropped_other_caliber=dict(sorted(dropped.items())),
        sources=stats.sources,
        days_below_publish_weight=below_publish_weight_days(days),
    )


class CompositeSourceError(RuntimeError):
    """成分或窗口异常导致"一行都算不出来" —— 必须响亮失败，不得静默成功。"""


async def backfill_composite(
    db: AsyncSession,
    *,
    day_hi: date | None = None,
    lookback_days: int | None = None,
) -> BackfillStats:
    """回补 `[day_hi - lookback_days, day_hi]` 的合成指数并提交。

    `day_hi=None` → 取**成分本身的**最近日（`max(period)` over 成分行），
    **不是** `date.today()`、也不是 `daily_quotes`：合成的日历由成分决定（Ruling 59 的
    "锚在自身输入的可得性上"）。`lookback_days=None` → 从成分的最早日开始（全量）。
    """
    if day_hi is None:
        day_hi = (
            await db.execute(
                select(func.max(IndustryMetric.period)).where(
                    IndustryMetric.industry_key == "market",
                    IndustryMetric.stock_id == 0,
                    IndustryMetric.metric_key.in_(list(CONSTITUENT_KEYS)),
                )
            )
        ).scalar_one()
    if day_hi is None:
        raise CompositeSourceError("成分表里一行都没有 —— 没有可合成的底座")
    day_lo = None if lookback_days is None else day_hi - timedelta(days=lookback_days)
    stats = await upsert_composite_range(db, day_lo=day_lo, day_hi=day_hi)
    await db.commit()
    return stats


async def composite_coverage(
    db: AsyncSession, *, day_lo: date | None = None, day_hi: date | None = None
) -> CompositeCoverage:
    """合成指数自身的覆盖率对账（分母 = **它自己的落库行**，不是别的日历）."""
    stmt = select(
        IndustryMetric.period,
        IndustryMetric.value,
        IndustryMetric.extra["missing_reason"].astext.label("reason"),
        IndustryMetric.extra["participants"].label("participants"),
    ).where(
        IndustryMetric.industry_key == "market",
        IndustryMetric.stock_id == 0,
        IndustryMetric.metric_key == METRIC_KEY,
    )
    if day_lo is not None:
        stmt = stmt.where(IndustryMetric.period >= day_lo)
    if day_hi is not None:
        stmt = stmt.where(IndustryMetric.period <= day_hi)
    rows = (await db.execute(stmt.order_by(IndustryMetric.period))).all()
    out = CompositeCoverage()
    participating: dict[str, int] = dict.fromkeys(CONSTITUENT_KEYS, 0)
    for row in rows:
        out.rows += 1
        out.period_from = out.period_from or row.period
        out.period_to = row.period
        if row.value is None:
            out.null_days += 1
            reason = row.reason or "unlabelled"
            out.missing_reasons[reason] = out.missing_reasons.get(reason, 0) + 1
        else:
            out.value_days += 1
            out.first_value_day = out.first_value_day or row.period
            out.last_value_day = row.period
        for key in row.participants or []:
            participating[key] = participating.get(key, 0) + 1
    out.participating_days = {k: v for k, v in participating.items() if v}
    out.missing_reasons = dict(sorted(out.missing_reasons.items()))
    return out


# ── 读面：hero 端点（`GET /api/v1/market/sentiment/composite`）──────────────────
#
# 本模块唯一的**读**路径。它不重算合成（`value` 取库里既有行），只回答
# "今天的值在它自己的历史里坐在哪"。默认读面（`/api/v1/metrics/*`）不受影响。

#: hero 分位窗口（交易日 ≈ 5 年）：与 spec §S2.2 示例规则
#: `ctx.percentile("market_sentiment_composite", window=1260)` 同值。**契约冻结**：
#: 前端 hero 不传参，改这个数 = 改前端契约（不是内部旋钮）。
COMPOSITE_HISTORY_WINDOW = 1260

#: 序列读的行数上界（**含 NULL 行**）。当前全量 4002 行（2708 有值），留足余量。
#: 命中上界 = 历史长到该换 SQL 侧窗口读了，而不是继续调大这个数。
COMPOSITE_READ_LIMIT = 8000

#: `provisional` 的"仍可能被改写"窗口（交易日）：与收敛器的重算窗口同值。
#:
#: **刻意不 import `derived_metrics_service.CONVERGE_WINDOW_TRADING_DAYS`**：读面模块不该
#: 反向依赖收敛器（derived_metrics 已在函数内 import 本模块，模块级互相 import 会成环）。
#: 两者的一致性由 `test_provisional_window_matches_converge_window` 钉住。
COMPOSITE_PROVISIONAL_WINDOW_TRADING_DAYS = 10


def composite_is_provisional(
    participants: Sequence[str] | None,
    *,
    lag_trading_days: int | None,
    window: int = COMPOSITE_PROVISIONAL_WINDOW_TRADING_DAYS,
) -> bool | None:
    """当期值是否**临时值**（`True`/`False`/`None` = 判不出）.

    **三态，不是布尔**：`None` 不得被归并成 `False`（那会把"不知道"说成"是终值"）。

    - 该行没登记参与集（老行 `extra` 缺 `participants`）⇒ `None`：连"缺没缺腿"都不知道；
    - 落后交易日数判不出（缺交易日历）⇒ `None`：无法判断它是否还在改写窗口内；
    - 该行缺了**已声明的 T+1 成分**（`registry.COMPOSITE_LATE_ARRIVAL_KEYS`）**且**仍在
      收敛窗口内 ⇒ `True`（值会被上游披露后的重算改写）；
    - 其余 ⇒ `False`。

    为什么必须看窗口：历史行一旦落到窗口外就**再也不会被重算**（ADR 0016 的窗口语义），
    所以"缺 T+1 腿"对它们不是"临时"，而是终值。只按"缺腿"判会让六个月前的行也自称临时。
    """
    if participants is None:
        return None
    if lag_trading_days is None:
        return None
    missing_late = bool(registry.COMPOSITE_LATE_ARRIVAL_KEYS - set(participants))
    return missing_late and lag_trading_days < window


def _extra_float(extra: Mapping[str, Any], key: str) -> float | None:
    """`extra` 里的数字键 → float；缺失 / 非数字（含 bool）→ `None`（**不猜**）。"""
    value = extra.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def composite_percentile(
    values: Sequence[float],
    window: int = COMPOSITE_HISTORY_WINDOW,
    *,
    min_samples: int = MIN_PERCENTILE_SAMPLES,
) -> tuple[int, float | None]:
    """序列**末值**在自身尾部窗口里的分位（0–100，nearest-rank，**无插值**）。

    - 样本 = `values` 末尾 `window` 个（调用方只传非 `None` 值 —— "有效样本"口径，
      NULL 既**不进**样本也**不计 0**）；
    - 定义 = `100 × count(v <= 末值) / n`（末值自身计入 tie，故全相等 → 100，
      最小值 → `100/n` > 0）；四舍五入 2 位。全程**不插值**；
    - `n < min_samples`（默认 `MIN_PERCENTILE_SAMPLES` = 60，Ruling 4）→ 分位 `None`
      （**不给假分位**）；样本为 0 同样 `None`；
    - 返回 `(sample_count, percentile|None)`：样本数**恒**如实返回（读面据此判
      `insufficient_history`）。

    ponytail: 这是 `indicators.percentile_rank`（同一 `count(v<=cur)/n` 口径，逐位多值版）
    与 `limit_up_service.percentile_from_series`（分位**表**版 p10..p90）之外的**第三份**
    同族实现。两处都读得动，但各自绑着历史形状（前者的 0–1 缩放 + 逐位对齐、后者的
    `_PERCENTILE_VALIDITY` 三字段 + `row["trade_date"]`）。这里只要"末值一个标量"，
    故自持 4 行。S2.2 引入规则引擎的 `ctx.percentile(..., window=)` 时**必须**统一成一个
    共享 helper（届时以 `indicators` 为家），本函数与 `percentile_from_series` 一起改。
    """
    samples = values[-window:] if window > 0 else []
    n = len(samples)
    if n < min_samples:
        return n, None
    current = samples[-1]
    return n, round(100.0 * sum(1 for v in samples if v <= current) / n, 2)


def select_latest_publishable_index(
    rows: Sequence[Any], *, min_weight: float = PUBLISH_MIN_WEIGHT
) -> int | None:
    """**最新一个过发布闸门**的行的下标；一行都不过 ⇒ `None`（升序序列里从尾往前找）.

    判据只有一条：该行 `extra.effective_weight_sum >= min_weight`。缺键的行（老行、
    异常行）视为**不可发布** —— 见 :func:`effective_weight` 的说明。

    为什么读面也要过滤（写面已经有闸门）：闸门是本轮才加的，**库里已经躺着**那些
    按旧口径发布的薄行（实测 2026-10-12 = 3/11 条腿、0.23，全库有 2592 个这样的日子），
    而收敛器只重算最近一个窗口、**不会**回头改写全部历史。读面不复核一遍，
    这些历史薄行仍会被当成头条。
    """
    for idx in range(len(rows) - 1, -1, -1):
        extra = getattr(rows[idx], "extra", None) or {}
        weight = effective_weight(extra)
        if weight is not None and weight >= min_weight:
            return idx
    return None


def composite_hero_payload(
    rows: Sequence[Any],
    *,
    window: int = COMPOSITE_HISTORY_WINDOW,
    min_weight: float = PUBLISH_MIN_WEIGHT,
    expected_as_of: date | None = None,
    lag_trading_days: int | None = None,
) -> dict[str, Any]:
    """合成序列（**升序** ORM 行 / 行替身）→ hero 响应（**纯函数**，无 IO、无时钟）。

    只回**库里已有**的事实：`as_of`/`value`/`caliber_version` 取**最新一个过发布闸门
    的行**（`select_latest_publishable_index`）；`legs`/`effective_weight`/
    `min_effective_weight`/`total_legs` 直读**同一行** `extra` 的既有键
    （`participants` / `effective_weight_sum` / `min_effective_weight` /
    `n_constituents`）—— 缺键就 `None`/0，**绝不**回退去别的行或按配置权重现算。

    为什么"最新一个过闸门的行"而不是盲取 `rows[-1]`（2026-10-16 裁决）：盲取会把
    2026-10-12 那种 3/11 条腿的薄值当成"当天的市场情绪综合指数"，与前一日 10 条腿的
    值直接可比地展示 —— 那正是用户看到的"数字对不上"。

    **分位窗口截到选中行为止**：`values` 只取该行**及其之前**的非 NULL 值。若不过滤，
    选中行之后那些"有值但未过闸门"的历史薄行会成为窗口末值，分位就算在错误的对象上。

    `expected_as_of` / `lag_trading_days` 由调用方（`get_composite_hero`）注入 ——
    本函数不认识交易日历，也不查库：`stale` 三态（`True`/`False`/`None`）中 `None`
    表示"无法判定"（缺 `expected_as_of`），**不得**把它当"新鲜"。
    今天的值为 NULL（合成日缺数）时 `percentile=None`：没有"今天的位置"可分位
    （`sample_count` 仍如实返回尾部有效样本数）。
    """
    idx = select_latest_publishable_index(rows, min_weight=min_weight)
    latest = None if idx is None else rows[idx]
    # 序列在选中行处截断：分位定义在"截至当天的历史"上，不能吃到更晚的薄行
    series = list(rows[: idx + 1]) if idx is not None else []
    values = [float(r.value) for r in series if r.value is not None]
    sample_count, percentile = composite_percentile(values, window)
    if latest is None or latest.value is None:
        percentile = None

    extra: Mapping[str, Any] = dict(getattr(latest, "extra", None) or {})
    legs_raw = extra.get("participants")
    legs = [str(k) for k in legs_raw] if isinstance(legs_raw, list) else None
    total_legs = extra.get("n_constituents")
    as_of = None if latest is None else latest.period
    # `provisional`：该行缺了 T+1 披露的成分、且仍在收敛器的改写窗口内 ⇒ 值会变。
    # 判不出（缺参与集 / 缺日历）⇒ None，**不假装是终值**（与 `stale` 同一条三态纪律）。
    provisional = composite_is_provisional(legs, lag_trading_days=lag_trading_days)

    return {
        "as_of": as_of,
        "value": None if latest is None or latest.value is None else float(latest.value),
        "window": window,
        "sample_count": sample_count,
        "percentile": percentile,
        "insufficient_history": sample_count < MIN_PERCENTILE_SAMPLES,
        "legs": legs,
        "leg_count": 0 if legs is None else len(legs),
        "total_legs": (
            int(total_legs)
            if isinstance(total_legs, int) and not isinstance(total_legs, bool)
            else 0
        ),
        "effective_weight": _extra_float(extra, "effective_weight_sum"),
        "min_effective_weight": _extra_float(extra, "min_effective_weight"),
        "caliber_version": None if latest is None else latest.caliber_version,
        "expected_as_of": expected_as_of,
        "lag_trading_days": lag_trading_days,
        # `None` = 无法判定（缺 expected_as_of），**不是**"新鲜"：三态必须区分，
        # 否则读面在信息缺失时会静默宣称"数据是新的"。
        "stale": None if lag_trading_days is None else lag_trading_days > 0,
        "provisional": provisional,
    }


async def get_composite_hero(*, cache: Any = None) -> dict[str, Any]:
    """`GET /api/v1/market/sentiment/composite` 的编排：索引读 + 纯函数组装 + 新鲜度判定。

    **不新增表、不加新缓存**：复用 `industry_metric_repo.get_metric_history`
    （`idx_industry_metrics_lookup` 覆盖 `(industry_key, metric_key, period)`）。
    窗口是回看窗口（分位定义在它上面），读全量行是为了**从尾部取足** `window` 个有效样本 ——
    裁掉窗口前的历史不会改变结果，但少读 NULL 行会（故 `limit` 留足余量）。

    新鲜度（`expected_as_of` / `lag_trading_days` / `stale`）—— **2026-10-17 换锚**：

    - `expected_as_of` = **按日历与时钟**判定的"现在应该出到哪天"
      （`market_day_service.resolve_expected_day_details`，交易日 17:30 后即当日）。
      此前它取自 `resolve_latest_complete_day`，而那个候选日就是
      `max(daily_quotes.trade_date)` —— **与被判据的对象同源**，于是"今天整天没采"时
      期望日跟着退回昨天、`stale` 恒为 `false`：判据永远自洽、永远不报警（本次要修的
      正是它）。日历是外部事实，不随"我们采到了什么"变化；
    - `lag_trading_days` = 日历上 `(as_of, expected_as_of]` 的交易日数 —— **不再**用
      `daily_quotes` 的实际持有日计数（那会让"整天没采"数出 0 天 ⇒ `stale=false`）；
    - 三项都只读 Redis 日历，**不在 GET 里外呼 TuShare**（读面纪律）；
    - 任一项算不出 ⇒ 三个字段留 `None`（**不假装新鲜**）。
    """
    from app.services import market_day_service  # noqa: PLC0415 — 读面按需引入，避免环

    async with async_session_factory() as db:
        rows = await industry_metric_repo.get_metric_history(
            db, "market", METRIC_KEY, limit=COMPOSITE_READ_LIMIT
        )
    expected_as_of: date | None = None
    lag_trading_days: int | None = None
    try:
        idx = select_latest_publishable_index(rows)
        as_of = None if idx is None else rows[idx].period
        (
            expected_as_of,
            lag_trading_days,
            _reason,
        ) = await market_day_service.resolve_expected_day_details(cache, as_of=as_of)
    except Exception:  # noqa: BLE001 — 新鲜度是**加法**：算不出不得反噬 hero 主数据
        logger.warning("composite hero: freshness unavailable", exc_info=True)
        expected_as_of, lag_trading_days = None, None
    return composite_hero_payload(
        rows, expected_as_of=expected_as_of, lag_trading_days=lag_trading_days
    )


async def get_metric_row_at(db: AsyncSession, day: date) -> IndustryMetric | None:
    """`market_sentiment_composite` 在 `day` 的那一行（收敛器据此判"发布了什么"）.

    走 `idx_industry_metrics_lookup`，单行等值查询。

    `populate_existing=True` 是**必需**的，不是优化：落库走 Core 的
    `INSERT ... ON CONFLICT DO UPDATE`（`upsert_metric_rows`），它**不刷新** ORM
    identity map —— 同一个 session 里先读过这一行、再 upsert、再读，会拿到**upsert
    之前的旧对象**（2026-10-16 实测：薄行改成齐备值后再读仍是 `value=NULL`，
    于是"自愈成功"被误判成"没变化"）。本函数的语义是"库里现在到底存了什么"，
    故每次都必须从库里取，而不是复用会话里可能过期的那一份。
    """
    stmt = (
        select(IndustryMetric)
        .where(
            IndustryMetric.industry_key == "market",
            IndustryMetric.stock_id == 0,
            IndustryMetric.metric_key == METRIC_KEY,
            IndustryMetric.period == day,
        )
        .execution_options(populate_existing=True)
    )
    return (await db.execute(stmt)).scalars().first()
