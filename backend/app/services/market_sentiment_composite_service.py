"""spec §S1.13 情绪 · **市场情绪综合指数** —— 计算 + 落库（`industry_key='market'`）.

## 这个模块是什么

把 S1.8–S1.12 已登记的情绪指标里**选定的 11 条日频腿**做**滚动分位标准化**，再按
**公开权重表**加权合成一条 0–100 的序列，写进 `industry_metrics`
（`industry_key='market'`、`stock_id=0`、`metric_key='market_sentiment_composite'`）。
读面**不在这里** —— 走 S1.2 已建好的 `GET /api/v1/metrics/{latest,series}`
（registry 驱动 + 四徽章 + Redis 缓存）。**不新建表、不新建平行读路、不新增迁移。**

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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustryMetric
from app.services import industry_registry as registry
from app.services.indicators import percentile_rank
from app.services.market_activity_service import upsert_metric_rows

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
    rows = [_row(day) for day in days]
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
