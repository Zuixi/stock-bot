"""**派生指标收敛器** —— 让"市场情绪"这一族派生指标每天自己追上最新可用交易日.

## 为什么需要它（2026-10-16 的事故与裁决）

`industry_metrics` 里 scope=`market` 的那一族指标（spec §S1.7–§S1.13：市场宽度与动量、
活跃度与杠杆、波动率与衍生品、资金流，以及由前者合成的**市场情绪综合指数**）
**只有手工回补脚本**会写它们（`backend/scripts/backfill_*.py`），而**没有任何定时任务**
调用过 —— 于是 2026-10-12 那次人工回补之后，页面上的"数据截至 10月12日"就一直不动，
而底层行情（`daily_quotes`）其实天天在更新。这不是数据源的问题，是**编排缺失**：
一族 B 类可回算的派生指标没有接进日更链。

本模块补的就是那一环，并且按仓库既有的**对账 / 电平触发**范式来补
（`plans/2026-09-17-data-sync-self-healing.md`：「数据面完整性由对账保证，
不依赖任务准点跑」）：

* **挂在对账之后**（`app/scheduler/jobs.py::reconcile_market_data_job`），不新开 cron：
  三个既有触发点（启动 +2min / 交易日 17:45 / 每日 10:00）都成为收敛点；
* **目标由数据决定（T-1）**：`target` 默认取 `reconcile` 已算好的 `expected_latest`
  （`trade_cal` 截止昨日），**不是** `date.today()`；
* **幂等 + 自愈**：每轮按**交易日窗口**重算（默认 10 个交易日），上一轮漏掉的、
  写薄的、上游迟到补数的日子，在这一轮被 `ON CONFLICT DO UPDATE` 覆盖；
* **逐腿独立**：一条腿失败不回滚别的腿，也**不阻断**不依赖它的腿
  （composite 只依赖喂它的那三条腿）。

## "齐备"的口径（用户 2026-10-16 裁决）

`target` 那天，各腿必须**自己**推进到该日（判据：该腿**每个** key 的
`max(period) >= target`）。某条喂 composite 的腿落后时 composite **不重算**，
页面继续显示上一个齐备日并如实报出落后交易日数
（`GET /market/sentiment/composite` 的 `lag_trading_days`）——
即「只展示各腿齐备到 T-1 的值」。

## 窗口为什么锚在 `daily_quotes` 的交易日上

库内没有持久化交易日历，而"哪些日子真有行情"本身就是最准的可用性判据
（`limit_up_repo.list_recent_trade_dates` 的同一条理由）。窗口下界 = `target` 往前数
`CONVERGE_WINDOW_TRADING_DAYS` 个**交易日**，不是日历日 —— 长假后日历日窗口会虚胖。

## 不做的事

* **不做全量回补**：落后超过 `CONVERGE_MAX_LAG_TRADING_DAYS` 个交易日时**不自动跑**，
  只告警并提示走手工入口（`fetch-market-data` 的 `market_metrics`）—— 日更任务不该
  在某个深夜悄悄向 TuShare 要五年历史（额度事故的教训见 `docker-compose.override.yml`）；
* **不新增表、不改任何口径**：全部写进既有的 `industry_metrics`；composite 的
  权重/窗口/版本号仍只由 `industry_registry.COMPOSITE_*` 决定，本模块不碰。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory, engine
from app.models.industry_research import IndustryMetric
from app.models.market_data import MarketSentimentDaily
from app.repositories import limit_up_repo
from app.services import industry_registry as registry
from app.services.market_activity_service import upsert_metric_rows

logger = logging.getLogger(__name__)

_SH_TZ = ZoneInfo("Asia/Shanghai")

#: 每轮重算的**交易日**窗口。为什么不是 1 天：收敛器靠"重算一个区间"自愈 ——
#: 上一轮漏跑、写薄、上游迟到补数的日子都在这个窗口里被覆盖（幂等 upsert）。
CONVERGE_WINDOW_TRADING_DAYS = 10
#: 落后超过这个交易日数就**不自动收敛**（只告警 + 提示手工入口）。
CONVERGE_MAX_LAG_TRADING_DAYS = 60
#: 告警门槛：目标日生效权重之和低于它 ⇒ 告警（**与发布闸门同源**，不另立一个数）。
ALERT_MIN_EFFECTIVE_WEIGHT = registry.COMPOSITE_PUBLISH_MIN_WEIGHT
#: `above_ma20_ratio` 的 MA20 预热（日历日，60 > 19 个交易日；与回补脚本同值）。
BREADTH_WARMUP_DAYS = 60
#: 同一时刻只允许一个收敛器在跑（三个触发点 + 手工入口可能重叠）→ PG advisory lock。
#: 取值本身无意义，只是要一个全局稳定的整数（改了等于换锁）。
CONVERGE_LOCK_KEY = 20261016

#: 前置闸门未放行时，CST 几点之后才告警。
#: 为什么分时段：每日 10:00 那次触发时，T-1 的行情**常常还没拉回来**（实测 10/14 的
#: 行情是 10/15 10:02 才落库的）——那是**正常**的"还没到"，不是故障。
#: 到了盘后（17:45 那次）仍不达标，才说明收敛链真的断了，必须响。
_GATE_ALERT_HOUR = 17


# ── 纯函数：窗口与目标日规划 ─────────────────────────────────────────────


def plan_leg_window(
    *,
    target: date,
    window_lo: date,
    latest: date | None,
    always_rescan: bool = False,
) -> tuple[date, date] | None:
    """该腿本轮要重算的 `(day_lo, day_hi)`；`None` = 已追平、本轮零外呼.

    判据（纯函数，可逐位复核）：

    - `latest is None`（该腿一行都没有）⇒ 用**窗口** `[window_lo, target]`；
    - `latest >= target`：`always_rescan=True` ⇒ 仍重算窗口（源侧会**迟到补数**：
      QVIX 的静态 CSV 落后若干交易日，迟到时先以 NULL 落行，等它追上后必须能用
      同一窗口把它覆盖成有值行）；否则 ⇒ `None`（追平了，零外呼）；
    - `latest < target` ⇒ `[min(latest, window_lo), target]`（**含** `latest`：
      它可能是一行写薄的日，重算会覆盖它）。
    """
    if latest is None:
        return (window_lo, target)
    if latest >= target:
        return (window_lo, target) if always_rescan else None
    return (min(latest, window_lo), target)


def lag_trading_days(
    trade_days: Sequence[date], *, latest: date | None, target: date
) -> int | None:
    """`latest` 落后 `target` 几个**交易日**（`latest` 之后、到 `target` 为止的交易日数）.

    `latest is None` ⇒ `None`（"一行都没有"不是"落后 0 天"）。
    调用方必须传**足够长**的交易日序列（≥ 可能的最大落后天数），否则会低估落后。
    """
    if latest is None:
        return None
    return sum(1 for day in trade_days if latest < day <= target)


# ── 腿的定义与执行 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class LegSpec:
    """一条可独立收敛的指标腿（= 一个 service 的区间落库方法）."""

    key: str
    label: str
    metric_keys: tuple[str, ...]
    #: True ⇒ 每轮都重扫窗口（源侧延迟补数），见 :func:`plan_leg_window`。
    always_rescan: bool = False
    #: 该腿是否喂**综合指数**（§S1.13 的成分只来自 §S1.8 / §S1.9 / §S1.10）；
    #: `False` 的腿失败**不阻断** composite。
    feeds_composite: bool = True


@dataclass
class LegOutcome:
    """一条腿本轮的结果（可核对：窗口、前后落库点、行数、错误）."""

    key: str
    label: str
    status: str  # ok | caught_up | failed
    window: tuple[date, date] | None = None
    latest_before: date | None = None
    latest_after: date | None = None
    rows_upserted: int = 0
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "window": (
                None
                if self.window is None
                else [self.window[0].isoformat(), self.window[1].isoformat()]
            ),
            "latest_before": (
                None if self.latest_before is None else self.latest_before.isoformat()
            ),
            "latest_after": (None if self.latest_after is None else self.latest_after.isoformat()),
            "rows_upserted": self.rows_upserted,
            "error": self.error,
            **({"detail": self.detail} if self.detail else {}),
        }


def leg_specs() -> tuple[LegSpec, ...]:
    """四条腿的定义（key 集**取自各 service 自己的常量**，不在这里另抄一份）.

    顺序 = 依赖序：宽度吃 `daily_quotes` 与 `market_sentiment_daily`，
    活跃度要先落 PE 底座，波动率/资金流吃外部源；composite 由主流程最后算。
    """
    from app.services import (  # noqa: PLC0415 — 按需引入，避免调度器启动期拉起重依赖
        capital_flows_service,
        market_activity_service,
        market_sentiment_service,
        volatility_derivatives_service,
    )

    return (
        LegSpec(
            key="breadth",
            label="S1.8 市场宽度与动量",
            metric_keys=market_sentiment_service.ALL_METRIC_KEYS,
        ),
        LegSpec(
            key="activity",
            label="S1.9/S1.11 活跃度·杠杆·估值",
            metric_keys=market_activity_service.INGEST_METRIC_KEYS,
        ),
        LegSpec(
            key="volatility",
            label="S1.10 波动率与衍生品",
            metric_keys=volatility_derivatives_service.INGEST_METRIC_KEYS,
            # QVIX 的静态 CSV 落后期货腿若干交易日：迟到补数必须能覆盖已落的 NULL 行
            always_rescan=True,
        ),
        LegSpec(
            key="capital_flows",
            label="S1.7 资金流",
            metric_keys=capital_flows_service.INGEST_METRIC_KEYS,
            # 不是 §S1.13 的成分（资金流四件套未纳入合成）⇒ 失败不阻断 composite
            feeds_composite=False,
        ),
    )


LEG_KEYS: tuple[str, ...] = ("breadth", "activity", "volatility", "capital_flows")


async def _latest_period(db: AsyncSession, metric_keys: Sequence[str]) -> date | None:
    """该腿**每个** key 的 `max(period)` 里**最小**的那个 —— "这条腿推到哪天".

    为什么取 min 而不是 max：一条腿内多个 key 由同一次运行写入，取 min 才能发现
    "同一次运行只写了一部分 key"的半截状态（取 max 会把它当成追平而永不自愈）。
    任一 key 一行都没有 ⇒ `None`（视为"没追上"，不是"追上到某天"）。
    """
    stmt = (
        select(IndustryMetric.metric_key, func.max(IndustryMetric.period))
        .where(
            IndustryMetric.industry_key == "market",
            IndustryMetric.stock_id == 0,
            IndustryMetric.metric_key.in_(list(metric_keys)),
        )
        .group_by(IndustryMetric.metric_key)
    )
    seen: dict[str, date] = {
        str(row[0]): cast("date", row[1]) for row in (await db.execute(stmt)).all()
    }
    if not seen or any(key not in seen for key in metric_keys):
        return None
    return min(seen.values())


# ── 降级保护：重算历史时，已有的好值不得被"本轮源侧缺数"改写成 NULL ──────────
#
# 为什么需要它（2026-10-16 复核实测）：共享写路径 `industry_metric_repo.upsert_metrics`
# 对 `value` / `extra` 是 `excluded.*` **直接覆盖**（只有 `published_at` /
# `caliber_version` 用 `coalesce`，那是 Ruling 36 明确限定的两列）。而本收敛器**每天 3 次
# 重写 10 个交易日的窗口** —— 于是某一轮 TuShare/AKShare 返回空时，窗口内**早就正确观测到**
# 的历史值会被改写成 NULL（实测：`above_ma20_ratio` 2026-10-14 的 23.2655 被一行
# `value=NULL` + `source_empty` 覆盖，且旧值无处可回退）。
#
# 为什么不在共享写路径上加 `coalesce(value, 既有)`：那会改掉**所有**写入方（行业指标、
# 手工回补、其它 scope）的语义，而"源侧缺数 ⇒ 落 NULL + 原因码"正是本仓库刻意的数据纪律
# （缺数不许落 0、不许前向填充）。要挡的不是"记下缺数"，而是"**重算把历史改坏**" ——
# 故边界画在收敛器这一层：本轮的 NULL 照写，但快照里非 NULL 而重算后变 NULL 的行**恢复回去**。
#
# 方向性是关键：**值 → NULL 拒绝**（降级）；**NULL → 值 放行**（源侧迟到补数，如 QVIX
# 本轮回填）；**值 → 值 放行**（修订）。故这条保护不会妨碍自愈，只妨碍"越算越差"。


def downgraded_rows(
    snapshot: Sequence[Mapping[str, Any]],
    current: Mapping[tuple[str, date], float | None],
) -> list[dict[str, Any]]:
    """快照里**非 NULL**、但重算后变成 NULL（或整行消失）的行 —— 需要恢复的那些.

    纯函数（无 IO、无时钟），故"什么算降级"可以逐位复核。`snapshot` 的元素必须是
    **可直接 re-upsert 的行字典**（:func:`load_window_rows` 的产物）。
    """
    out: list[dict[str, Any]] = []
    for row in snapshot:
        key = (str(row["metric_key"]), cast("date", row["period"]))
        if current.get(key) is None:
            out.append(dict(row))
    return out


async def load_window_rows(
    db: AsyncSession, metric_keys: Sequence[str], day_lo: date, day_hi: date
) -> list[dict[str, Any]]:
    """窗口内这些 key 的**全部**行 → 可直接 re-upsert 的行字典（含 NULL 行）.

    列集与唯一键 `(industry_key, stock_id, metric_key, source, freq, period)` **逐列取回**：
    少取 `source`/`freq` 会让"恢复"插成**另一条键**（插入而非覆盖），那比不恢复更糟。
    """
    stmt = select(
        IndustryMetric.metric_key,
        IndustryMetric.period,
        IndustryMetric.value,
        IndustryMetric.unit,
        IndustryMetric.extra,
        IndustryMetric.source,
        IndustryMetric.source_tier,
        IndustryMetric.freq,
        IndustryMetric.published_at,
        IndustryMetric.caliber_version,
    ).where(
        IndustryMetric.industry_key == "market",
        IndustryMetric.stock_id == 0,
        IndustryMetric.metric_key.in_(list(metric_keys)),
        IndustryMetric.period >= day_lo,
        IndustryMetric.period <= day_hi,
    )
    return [
        {
            "industry_key": "market",
            "stock_id": 0,
            "metric_key": row.metric_key,
            "source": row.source,
            "source_tier": row.source_tier,
            "freq": row.freq,
            "period": row.period,
            "value": None if row.value is None else float(row.value),
            "unit": row.unit,
            "extra": row.extra,
            "published_at": row.published_at,
            "caliber_version": row.caliber_version,
        }
        for row in (await db.execute(stmt)).all()
    ]


async def restore_downgraded(
    db: AsyncSession,
    *,
    metric_keys: Sequence[str],
    day_lo: date,
    day_hi: date,
    snapshot: Sequence[Mapping[str, Any]],
) -> list[tuple[str, date]]:
    """把"重算后被改成 NULL"的行恢复回去并提交；返回被恢复的 `(metric_key, period)`.

    `snapshot` 必须是**重算前**的 :func:`load_window_rows` 结果里 `value is not None`
    的那些行。恢复走同一条 upsert（唯一键相同 ⇒ 覆盖回旧值，不新增行），
    `published_at` / `caliber_version` 因 `coalesce` 语义保持不变。
    """
    if not snapshot:
        return []
    after = await load_window_rows(db, metric_keys, day_lo, day_hi)
    current = {(str(row["metric_key"]), cast("date", row["period"])): row["value"] for row in after}
    restore = downgraded_rows(snapshot, current)
    if not restore:
        return []
    await upsert_metric_rows(db, restore)
    await db.commit()
    restored = sorted((str(r["metric_key"]), cast("date", r["period"])) for r in restore)
    logger.warning(
        "derived-metrics: 本轮源侧缺数本会把 %d 个已有值改写成 NULL，已恢复：%s",
        len(restored),
        [f"{k}@{d.isoformat()}" for k, d in restored[:10]],
    )
    return restored


async def _run_breadth(db: AsyncSession, day_lo: date, day_hi: date) -> dict[str, Any]:
    from app.services import market_sentiment_service as svc  # noqa: PLC0415

    stats = await svc.upsert_metric_range(db, day_lo, day_hi, warmup_days=BREADTH_WARMUP_DAYS)
    await db.commit()
    return {"rows_upserted": stats.rows_upserted, "days_written": stats.days_written}


async def _run_activity(db: AsyncSession, day_lo: date, day_hi: date) -> dict[str, Any]:
    from app.services import market_activity_service as svc  # noqa: PLC0415

    # **顺序有硬依赖**：`erp_10y` 的分子（沪深300 PE 底座）必须先落库
    # （回补脚本 `_upsert` 的同一条纪律，这里逐字复用而不是重写）
    await svc.ingest_erp_pe(db, day_lo, day_hi)
    stats = await svc.upsert_metric_range(db, day_lo, day_hi)
    await db.commit()
    return {"rows_upserted": stats.rows_upserted, "quote_days": stats.quote_days}


async def _run_volatility(db: AsyncSession, day_lo: date, day_hi: date) -> dict[str, Any]:
    from app.services import volatility_derivatives_service as svc  # noqa: PLC0415

    stats = await svc.upsert_metric_range(db, day_lo, day_hi)
    await db.commit()
    return {"rows_upserted": stats.rows_upserted, "trade_days": stats.trade_days}


async def _run_capital_flows(db: AsyncSession, day_lo: date, day_hi: date) -> dict[str, Any]:
    from app.services import capital_flows_service as svc  # noqa: PLC0415

    stats = await svc.upsert_capital_flows_range(db, day_lo, day_hi)
    await db.commit()
    return {"rows_upserted": stats.rows_upserted, "input_days": stats.input_days}


_LEG_RUNNERS: dict[str, Callable[[AsyncSession, date, date], Awaitable[dict[str, Any]]]] = {
    "breadth": _run_breadth,
    "activity": _run_activity,
    "volatility": _run_volatility,
    "capital_flows": _run_capital_flows,
}

#: **故意不由收敛器写**的 market 指标 → 理由（"不收敛"也必须可查，与 registry 的
#: `COMPOSITE_EXCLUDED_KEYS` 同一条纪律）。由 :func:`assert_market_keys_covered` 机械核对：
#: 新登记一条 market 指标却**既不在某条腿上、也不在这里** ⇒ 测试变红。
CONVERGE_EXCLUDED_KEYS: dict[str, str] = {
    "pe_csi300": (
        "ERP 的 **PE 底座**：由活跃度腿的 `ingest_erp_pe` 顺带写入（顺序有硬依赖），"
        "不是独立输出指标 —— 故刻意不进 `market_activity_service.INGEST_METRIC_KEYS`"
    ),
    "new_fund_share": "月频（§S1.12）：日更收敛器不碰（纳入日更必然要前向填充）",
    "public_fund_equity_position": (
        "实测**周度**且口径不可复算（第三方编制）⇒ 仅背景展示、禁入信号链路"
    ),
    "industrial_capital_net_change": (
        "金额抽取未落地（公告原料已采）—— 服务明确**不落任何行**（待接）"
    ),
    # ── dormant（2026-10-17 裁定）：数据可采、但**无消费方** ⇒ 不进收敛器 ──────────
    "insider_net_change_daily": (
        "**dormant（2026-10-17）**：官方一手可采（`scripts/backfill_insider_change.py`），"
        "但**暂无任何消费方**（前端 0 引用；市场读面按 `stock_id=0` 取数而本指标是 per-stock）"
        "⇒ 不进收敛器，避免每天两次无效外呼。消费方出现后再挂腿"
    ),
    "northbound_holding_quarterly": (
        "**dormant（2026-10-17）**：主源可采（`scripts/backfill_northbound_holding.py`，"
        "实测 3 个季末共 11,634 行），但 per-stock 行**不在**市场读面（`stock_id=0`）里、"
        "前端也无消费位 ⇒ 不进收敛器；消费方出现后再挂腿（届时同时决定聚合口径）"
    ),
}


def assert_market_keys_covered() -> None:
    """每个已登记的 market 指标都必须有"日更写入方"或一条**写明理由**的豁免.

    为什么需要这条守卫（2026-10-16 的根因）：那一族指标**登记齐全、读面齐全、手工回补脚本
    齐全**，唯独**没有任何定时任务**写它们 —— 于是行情天天更新、页面数据停在人工回补的最后
    一天，三天后才被用户发现。手工脚本**不算写入方**（没人会记得天天跑）。

    这条断言把"漏接调度"从"三天后用户报"变成"加 key 那次测试就红"。它同时核对两件反向的事：
    豁免项必须**真实存在**（改名后不得留死条目）且**不与腿重叠**（两处声明必然漂移）。

    **只在测试里调用**（不在 `converge()` 里抛）：登记漂移是配置错误，但把它变成生产
    收敛链的运行时异常，会让"多登记一条月频指标"直接停掉整条日更。测试期响亮失败足够。
    """
    registered = {m.key for m in registry.MARKET_SENTIMENT_METRICS_ALL}
    leg_keys: set[str] = set()
    for spec in leg_specs():
        leg_keys |= set(spec.metric_keys)
    covered = {registry.COMPOSITE_METRIC_KEY} | leg_keys | set(CONVERGE_EXCLUDED_KEYS)
    missing = sorted(registered - covered)
    if missing:
        raise RuntimeError(
            f"这些 market 指标既不在任何一条腿上，也没有豁免理由：{missing} —— "
            "登记了却没人日更 = 2026-10-16 的事故形态（手工回补脚本不算写入方）。"
            "请把它挂到某条腿，或在 CONVERGE_EXCLUDED_KEYS 写明理由"
        )
    stale = sorted(set(CONVERGE_EXCLUDED_KEYS) - registered)
    if stale:
        raise RuntimeError(
            f"CONVERGE_EXCLUDED_KEYS 里有已不存在的 key：{stale} —— 改名/下线后留下死条目"
            "会让豁免清单越来越不可信"
        )
    both = sorted(set(CONVERGE_EXCLUDED_KEYS) & leg_keys)
    if both:
        raise RuntimeError(
            f"这些 key 既在豁免清单里、又被某条腿覆盖：{both} —— 两处声明必然漂移，请只留一处"
        )


# ── 前置闸门 ────────────────────────────────────────────────────────────


async def prerequisites_ready(db: AsyncSession, target: date) -> tuple[bool, dict[str, Any]]:
    """`target` 那天的**底座**是否齐备（行情 + 涨停温度计）.

    复用 `market_day_service.resolve_latest_complete_day` 的完整性判据
    （行数达阈 + `pct_chg` 非空率 + 涨跌停价存在），**不另立一套阈值**：
    它给出的 `day >= target` 且 `quality != 'partial'` ⇒ 行情底座齐备。

    第二条是 `market_sentiment_daily` 在 `target` 有行：宽度腿的
    `limit_up_break_ratio` / `max_consecutive_limit_up` 直接读它，
    少了它那两条腿会以 `window_limits_incomplete` 之类的原因静默留空。
    """
    from app.services import market_day_service  # noqa: PLC0415

    md = await market_day_service.resolve_latest_complete_day(db)
    quotes_ok = md is not None and md.day >= target and md.quality != "partial"
    sent_rows = int(
        (
            await db.execute(
                select(func.count())
                .select_from(MarketSentimentDaily)
                .where(MarketSentimentDaily.trade_date == target)
            )
        ).scalar()
        or 0
    )
    detail: dict[str, Any] = {
        "quotes_latest_complete": None if md is None else md.day.isoformat(),
        "quotes_quality": None if md is None else md.quality,
        "quotes_ok": quotes_ok,
        "sentiment_daily_rows_at_target": sent_rows,
    }
    return (quotes_ok and sent_rows > 0), detail


# ── 收敛主流程 ──────────────────────────────────────────────────────────


@dataclass
class ConvergeResult:
    """一轮收敛的结果（job / 手工入口的返回值，也是日志与告警的输入）."""

    status: str  # ok | converged_with_gaps | gated | no_target | locked
    target: date | None = None
    window: tuple[date, date] | None = None
    legs: list[LegOutcome] = field(default_factory=list)
    composite: dict[str, Any] | None = None
    alerts: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": None if self.target is None else self.target.isoformat(),
            "window": (
                None
                if self.window is None
                else [self.window[0].isoformat(), self.window[1].isoformat()]
            ),
            "legs": [leg.as_dict() for leg in self.legs],
            "composite": self.composite,
            "alerts": self.alerts,
            **({"detail": self.detail} if self.detail else {}),
        }


def _now_sh() -> datetime:
    return datetime.now(_SH_TZ)


def _after_gate_alert_hour() -> bool:
    """现在是否已过"应该齐了"的时刻（CST 17 点）——决定"没齐"要不要告警."""
    return _now_sh().hour >= _GATE_ALERT_HOUR


async def _recent_trade_days(db: AsyncSession, *, as_of: date, limit: int) -> list[date]:
    return await limit_up_repo.list_recent_trade_dates(db, as_of=as_of, limit=limit)


async def _converge_inner(
    db: AsyncSession,
    *,
    target: date | None,
    legs_filter: Sequence[str] | None,
    window: int,
) -> ConvergeResult:
    """收敛主体（调用方已持锁、已开好 session）."""
    latest_probe = date(2999, 12, 31)

    if legs_filter is not None:
        unknown = sorted(set(legs_filter) - set(LEG_KEYS))
        if unknown:
            raise ValueError(f"未知的腿：{unknown}（可选 {list(LEG_KEYS)}）")

    if target is None:
        window_days = await _recent_trade_days(db, as_of=latest_probe, limit=window)
        if not window_days:
            return ConvergeResult(status="no_target", detail={"reason": "daily_quotes 为空"})
        target = window_days[-1]
        window_lo = window_days[0]
    else:
        window_days = await _recent_trade_days(db, as_of=target, limit=window)
        window_lo = window_days[0] if window_days else target
    # 落后判定要用**足够长**的序列（窗口只有 10 天，会把"落后 40 天"低估成 10 天）
    lag_days = await _recent_trade_days(db, as_of=target, limit=CONVERGE_MAX_LAG_TRADING_DAYS + 1)

    result = ConvergeResult(status="ok", target=target, window=(window_lo, target))

    ready, gate_detail = await prerequisites_ready(db, target)
    result.detail["prerequisites"] = gate_detail
    if not ready:
        result.status = "gated"
        # 10:00 那次常常只是"上游还没到"（正常）；盘后仍不达标才是故障
        if _after_gate_alert_hour():
            result.alerts.append(f"prerequisites_not_ready_at_{target.isoformat()}")
        logger.info("derived-metrics gated: target=%s detail=%s", target, gate_detail)
        return result

    specs = [s for s in leg_specs() if legs_filter is None or s.key in set(legs_filter)]
    outcomes: dict[str, LegOutcome] = {}
    for spec in specs:
        outcome = await _converge_leg(
            db, spec, target=target, window_days=window_days, lag_days=lag_days
        )
        outcomes[spec.key] = outcome
        result.legs.append(outcome)
        # 降级被挡 = 本轮源侧缺数（值已保住）。沉默会让"历史被改坏又悄悄改回来"
        # 变成新的静默失败点 —— 与本次事故同一形态，必须响。
        blocked = int(outcome.detail.get("value_downgrade_blocked") or 0)
        if blocked:
            result.alerts.append(f"value_downgrade_blocked:{spec.key}={blocked}")
        if outcome.detail.get("downgrade_restore_error"):
            result.alerts.append(f"value_downgrade_restore_failed:{spec.key}")

    composite_outcome, composite_alert = await _converge_composite(
        db,
        target=target,
        window_lo=window_lo,
        specs=specs,
        outcomes=outcomes,
    )
    result.composite = composite_outcome
    if composite_alert:
        result.alerts.append(composite_alert)

    failed = sorted(o.key for o in result.legs if o.status == "failed")
    if failed:
        result.status = "converged_with_gaps"
        result.alerts.append(f"legs_failed:{','.join(failed)}")
    return result


async def _converge_leg(
    db: AsyncSession,
    spec: LegSpec,
    *,
    target: date,
    window_days: Sequence[date],
    lag_days: Sequence[date],
) -> LegOutcome:
    """一条腿：定窗口 → 已追平则跳过 → 否则跑 runner（**单腿失败不外溢**）."""
    latest_before = await _latest_period(db, spec.metric_keys)
    lag = lag_trading_days(lag_days, latest=latest_before, target=target)
    plan = plan_leg_window(
        target=target,
        window_lo=window_days[0] if window_days else target,
        latest=latest_before,
        always_rescan=spec.always_rescan,
    )
    outcome = LegOutcome(
        key=spec.key,
        label=spec.label,
        status="caught_up",
        latest_before=latest_before,
        detail={"lag_trading_days": lag},
    )
    if plan is None:
        outcome.latest_after = latest_before
        logger.info("derived-metrics leg %s: caught up at %s", spec.key, latest_before)
        return outcome
    if lag is not None and lag > CONVERGE_MAX_LAG_TRADING_DAYS:
        # 落后太多 ⇒ 不自动跑（日更任务不该深夜要五年历史）：告警 + 手工入口
        outcome.status = "failed"
        outcome.error = (
            f"落后 {lag} 个交易日 > 上限 {CONVERGE_MAX_LAG_TRADING_DAYS}：不自动回补，"
            "请用手工入口 POST /tasks/fetch-market-data(type=market_metrics) 或回补脚本"
        )
        return outcome

    day_lo, day_hi = plan
    outcome.window = (day_lo, day_hi)
    # 重算**前**的非 NULL 快照：本轮若因源侧缺数把这些值改写成 NULL，下面会恢复回去
    before = await load_window_rows(db, spec.metric_keys, day_lo, day_hi)
    non_null_before = [row for row in before if row["value"] is not None]
    runner = _LEG_RUNNERS[spec.key]
    try:
        detail = await runner(db, day_lo, day_hi)
    except Exception as exc:  # noqa: BLE001 — 单腿失败必须被隔离
        await db.rollback()
        outcome.status = "failed"
        outcome.error = f"{type(exc).__name__}: {exc}"
        logger.exception("derived-metrics leg %s failed window=[%s, %s]", spec.key, day_lo, day_hi)
        return outcome
    outcome.status = "ok"
    outcome.rows_upserted = int(detail.get("rows_upserted", 0))
    outcome.detail.update(detail)
    try:
        restored = await restore_downgraded(
            db,
            metric_keys=spec.metric_keys,
            day_lo=day_lo,
            day_hi=day_hi,
            snapshot=non_null_before,
        )
    except Exception as exc:  # noqa: BLE001 — 保护失败不得把"腿跑成功"变成"腿失败"
        await db.rollback()
        logger.exception("derived-metrics leg %s: downgrade restore failed", spec.key)
        restored = []
        outcome.detail["downgrade_restore_error"] = f"{type(exc).__name__}: {exc}"
    if restored:
        outcome.detail["value_downgrade_blocked"] = len(restored)
    outcome.latest_after = await _latest_period(db, spec.metric_keys)
    outcome.detail["lag_trading_days_after"] = lag_trading_days(
        lag_days, latest=outcome.latest_after, target=target
    )
    logger.info(
        "derived-metrics leg %s: window=[%s, %s] rows=%s latest=%s",
        spec.key,
        day_lo,
        day_hi,
        outcome.rows_upserted,
        outcome.latest_after,
    )
    return outcome


async def _converge_composite(
    db: AsyncSession,
    *,
    target: date,
    window_lo: date,
    specs: Sequence[LegSpec],
    outcomes: dict[str, LegOutcome],
) -> tuple[dict[str, Any], str | None]:
    """综合指数：**只在喂它的那几条腿都追平到 `target`** 之后按 `target` 重算.

    为什么必须等：composite 的日历是"各成分可得日的并集"，任一喂它的腿没到 `target`
    就会长出**薄行**（2026-10-12 的 3/11 条腿就是这么来的）。等齐之后，
    发布闸门再挡一道（生效权重 < 0.80 不发布值）。
    """
    from app.services import market_sentiment_composite_service as composite_svc  # noqa: PLC0415

    composite_outcome: dict[str, Any] = {"status": "skipped"}
    feeders = [s for s in specs if s.feeds_composite]
    if not feeders:
        composite_outcome["reason"] = "本轮未选中任何喂 composite 的腿"
        return composite_outcome, None
    lagging = sorted(
        s.key
        for s in feeders
        if outcomes.get(s.key) is None
        or outcomes[s.key].status == "failed"
        or (outcomes[s.key].latest_after or date.min) < target
    )
    if lagging:
        composite_outcome["reason"] = f"喂 composite 的腿尚未追平 {target}: {lagging}"
        logger.info("derived-metrics composite skipped: lagging=%s", lagging)
        # 盘后仍不齐 ⇒ 用户看到的就是"停住的"综合指数，必须响
        if _after_gate_alert_hour():
            return composite_outcome, f"composite_lagging_legs:{','.join(lagging)}"
        return composite_outcome, None

    # 与逐腿同一条降级保护：composite 的值由**当时的**齐备成分算出，一旦算过就是稳定的
    # （分位窗口是回看的；成分修订才变）。本轮若因某条腿临时缺数而把历史值改成 NULL，
    # 那是"用今天的缺数抹掉昨天算对的值"，恢复回去并告警（headline 不该来回跳）。
    composite_before = await load_window_rows(db, (composite_svc.METRIC_KEY,), window_lo, target)
    composite_non_null = [row for row in composite_before if row["value"] is not None]

    try:
        stats = await composite_svc.upsert_composite_range(db, day_lo=window_lo, day_hi=target)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("derived-metrics composite failed window=[%s, %s]", window_lo, target)
        return {
            "status": "failed",
            "window": [window_lo.isoformat(), target.isoformat()],
            "error": f"{type(exc).__name__}: {exc}",
        }, "composite_failed"

    composite_restored: list[tuple[str, date]] = []
    try:
        composite_restored = await restore_downgraded(
            db,
            metric_keys=(composite_svc.METRIC_KEY,),
            day_lo=window_lo,
            day_hi=target,
            snapshot=composite_non_null,
        )
    except Exception as exc:  # noqa: BLE001 — 保护失败不得把"写成功"变成"写失败"
        await db.rollback()
        logger.exception("derived-metrics composite: downgrade restore failed")
        composite_outcome["downgrade_restore_error"] = f"{type(exc).__name__}: {exc}"

    row = await composite_svc.get_metric_row_at(db, target)
    # **立刻拷成纯 Python 值**：`get_metric_row_at` 用 `populate_existing` 读库，
    # 它刷新的是 identity map 里**同一个对象**；任何后续 upsert 都会就地改动这个对象
    # （2026-10-16 实测：测试里跨着下一次写入去读旧引用，拿到的是被刷新后的新值）。
    row_value = None if row is None or row.value is None else float(row.value)
    extra = dict(getattr(row, "extra", None) or {})
    weight = composite_svc.effective_weight(extra)
    participants = extra.get("participants")
    composite_outcome.update(
        {
            "status": "ok",
            "window": [window_lo.isoformat(), target.isoformat()],
            "rows_upserted": stats.rows_upserted,
            "value_days": stats.value_days,
            "null_days": stats.null_days,
            "days_below_publish_weight": stats.days_below_publish_weight,
            "value_at_target": row_value,
            "effective_weight_at_target": weight,
            "legs_at_target": len(participants) if isinstance(participants, list) else None,
            "missing_reason_at_target": extra.get("missing_reason"),
            "value_downgrade_blocked": len(composite_restored),
        }
    )
    logger.info(
        "derived-metrics composite: window=[%s, %s] rows=%s withheld_thin=%s weight=%s legs=%s",
        window_lo,
        target,
        stats.rows_upserted,
        stats.days_below_publish_weight,
        weight,
        composite_outcome["legs_at_target"],
    )
    if composite_restored:
        # 值已保住，但这是**源侧本轮缺数**的信号：不响的话，"历史值被改坏又被我们悄悄改回来"
        # 就成了第二个静默失败点（与本次事故同一形态）。
        return (
            composite_outcome,
            f"composite_value_downgrade_blocked:{len(composite_restored)}",
        )
    if weight is not None and weight < ALERT_MIN_EFFECTIVE_WEIGHT:
        return composite_outcome, f"composite_below_publish_weight:{weight}"
    return composite_outcome, None


async def converge(
    *,
    target: date | None = None,
    legs: Sequence[str] | None = None,
    window: int = CONVERGE_WINDOW_TRADING_DAYS,
    alert_job_id: str | None = None,
) -> dict[str, Any]:
    """**收敛入口**：把 scope=`market` 的派生指标推到 `target`（默认 T-1）并返回结果.

    - `target=None` ⇒ 取 `daily_quotes` 的最近交易日（本地口径，"数据说了算"）。
      job 轨道会显式传 `reconcile` 的 `expected_latest`（`trade_cal` 截止昨日）；
    - `legs=None` ⇒ 四条腿全跑；给了子集就只跑子集（手工入口用）；
    - `alert_job_id` 非空 ⇒ 把告警登记到 `job:failures`（`/market/data-freshness` 可见）；
    - 并发保护：PG advisory lock（同一时刻只有一个收敛器在跑，拿不到就返回 `locked`）。

    返回 :meth:`ConvergeResult.as_dict`（`status` / `target` / `legs` / `composite` / `alerts`）。
    """
    got_lock = False
    lock_conn = None
    try:
        # 锁必须挂在**专用连接**上（与工作用的 session 分开）：工作 session 每腿 commit，
        # 连接会回池；会话级 advisory lock 会跟着那条连接漂到别的请求上。
        lock_conn = await engine.connect()
        got_lock = bool(
            (
                await lock_conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": CONVERGE_LOCK_KEY}
                )
            ).scalar()
        )
        if not got_lock:
            logger.info("derived-metrics converge skipped: another run holds the lock")
            return ConvergeResult(status="locked").as_dict()
        async with async_session_factory() as session:
            result = await _converge_inner(session, target=target, legs_filter=legs, window=window)
    finally:
        if lock_conn is not None:
            if got_lock:
                try:
                    await lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": CONVERGE_LOCK_KEY}
                    )
                except Exception:  # noqa: BLE001 — 解锁失败不得吞掉真正的结果
                    logger.warning("derived-metrics: advisory unlock failed", exc_info=True)
            await lock_conn.close()

    payload = result.as_dict()
    if alert_job_id and result.alerts:
        from app.services import job_alert_service  # noqa: PLC0415

        await job_alert_service.record_job_failure(alert_job_id, "; ".join(result.alerts)[:500])
    return payload


# ── 只读排障面 ──────────────────────────────────────────────────────────


async def leg_lag_report(db: AsyncSession) -> dict[str, Any]:
    """四条腿各自的"推到哪天 / 落后几天"（只读；新鲜度巡检与排障用）.

    `target` 用**本地**最近交易日（`daily_quotes`），故报告不依赖任何外呼。
    """
    probe = await _recent_trade_days(
        db, as_of=date(2999, 12, 31), limit=CONVERGE_MAX_LAG_TRADING_DAYS + 1
    )
    if not probe:
        return {"target": None, "legs": []}
    target = probe[-1]
    legs: list[dict[str, Any]] = []
    for spec in leg_specs():
        latest = await _latest_period(db, spec.metric_keys)
        legs.append(
            {
                "key": spec.key,
                "label": spec.label,
                "latest": None if latest is None else latest.isoformat(),
                "lag_trading_days": lag_trading_days(probe, latest=latest, target=target),
                "feeds_composite": spec.feeds_composite,
            }
        )
    return {"target": target.isoformat(), "checked_at": _now_sh().isoformat(), "legs": legs}
