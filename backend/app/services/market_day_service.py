"""日级完整性判据 —— "最新交易日"决策的唯一来源。

行数够 ≠ 数据可用：实测 2026-09-14/15 的 daily_quotes 行数满 5485，但
pct_chg 只有 1 行非空（历史 NULL），下游所有按日聚合的端点都在用 LATERAL
逐股回看前收重算——判据看不出列坏了，于是永远不会被重拉。
同理，一条当日脏行（data_init 覆盖任务写出的未收盘数据）会把 max(trade_date)
顶到今天，让 8 个端点集体降级成 1 行，所以"最新日"必须带完整性门禁并允许回落。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.repositories import limit_up_repo

logger = logging.getLogger(__name__)

MarketQuality = Literal["complete", "partial", "fallback"]

MIN_ROW_RATIO = 0.9
MIN_PCT_CHG_RATIO = 0.99
FALLBACK_LOOKBACK_DAYS = 5
CACHE_KEY = "market:day:latest_complete"
CACHE_TTL = 60
_INCOMPLETE_REASON = "latest_day_incomplete"


@dataclass(frozen=True)
class MarketDay:
    day: date
    quality: MarketQuality
    reason: str | None
    rows: int
    universe: int
    pct_chg_ratio: float
    limits_present: bool


def quotes_day_complete(rows: int, universe: int, pct_chg_ratio: float) -> bool:
    """**行情自身**的两项判据：行数达阈值 + `pct_chg` 非空率达标（不含"限价存在"）.

    为什么单列一支：`is_day_complete` 的第三项（涨跌停价存在）是 `price_limits`
    **另一个域**的职责。对账在决定"要不要重拉 `daily_quotes`"时若带上它，就会让
    "限价缺一天"变成"行情也要天天重拉"（跨域误伤）。两支共用同两个阈值常量，
    所以不存在第二套标准。
    """
    if universe <= 0:
        return False
    if rows < MIN_ROW_RATIO * universe:
        return False
    return pct_chg_ratio >= MIN_PCT_CHG_RATIO


def is_day_complete(rows: int, universe: int, pct_chg_ratio: float, limits_present: bool) -> bool:
    """三元组判据：行数达阈值 + pct_chg 非空率达标 + 该日涨跌停价存在。

    `universe <= 0`（stocks 表为空）时一律判不完整——避免把 0/0 的真空判成完整。
    """
    return quotes_day_complete(rows, universe, pct_chg_ratio) and bool(limits_present)


async def _universe_count(db: AsyncSession) -> int:
    """在市标的总数——行数阈值与 pct_chg 覆盖率的分母。"""
    return int((await db.execute(select(func.count()).select_from(Stock))).scalar_one())


async def quote_day_stats(db: AsyncSession, day: date) -> tuple[int, float]:
    """单日行情两项：(行数, `pct_chg` 非空率)。

    非空率用 ``count(pct_chg) / count(*)`` 一条 SQL 取回（``count(col)`` 只数 NULL
    之外的行），空日返回 0.0 而不是 ZeroDivisionError。

    与 :func:`day_is_complete` 分开是因为**限价查询不便宜**（多一次 SQL）：
    只关心行情本身的对账路径不该为它付钱。
    """
    row = (
        await db.execute(
            select(
                func.count().label("rows"),
                func.count(DailyQuote.pct_chg).label("pct_chg_rows"),
            ).where(DailyQuote.trade_date == day)
        )
    ).one()
    rows = int(row.rows)
    ratio = float(row.pct_chg_rows) / rows if rows else 0.0
    return rows, ratio


async def _day_stats(db: AsyncSession, day: date) -> tuple[int, float, bool]:
    """单日三元组：(行数, pct_chg 非空率, 该日是否有涨跌停价)。"""
    rows, ratio = await quote_day_stats(db, day)
    return rows, ratio, await limit_up_repo.has_price_limits(db, day)


async def day_is_complete(db: AsyncSession, day: date, *, universe: int | None = None) -> bool:
    """该日**是否过三元组门禁**（与 :func:`resolve_latest_complete_day` 同一判据）。

    `universe` 可由调用方传入以避免逐日重复计数（对账要连查十几天）。

    存在的理由（2026-10-17）：对账的"齐备守门"需要一个**与读面完全同源**的判据。
    此前对账用的是 `COMPLETENESS_RATIO = 0.8` 的行数门槛，而读面用 0.9 + 列级 + 限价
    —— 两个数之间的日子会被判"已 OK 无需重拉"却又永远过不了读面门禁，于是**当天卡在
    T-1 且永不自愈**。统一到本函数（更严的那个）才能同时关掉这两个洞。
    """
    if universe is None:
        universe = await _universe_count(db)
    rows, ratio, limits_present = await _day_stats(db, day)
    return is_day_complete(rows, universe, ratio, limits_present)


def _from_cache(payload: Any) -> MarketDay | None:
    """Rebuild ``MarketDay`` from a cached JSON payload, or None when unusable.

    The cache JSON-serializes, so ``day`` comes back as an ISO string and the
    whole payload may be a stale/foreign shape. A malformed payload is a cache
    miss (never an exception) — ``MarketDay(**payload)`` would otherwise happily
    build a dataclass holding a ``str`` day for callers to trip over later.
    """
    if not isinstance(payload, dict):
        return None
    try:
        return MarketDay(**{**payload, "day": date.fromisoformat(str(payload["day"]))})
    except (KeyError, TypeError, ValueError):
        logger.warning("resolve_latest_complete_day: unusable cache payload %r", payload)
        return None


def _to_cache(day: MarketDay) -> dict[str, Any]:
    return {
        "day": day.day.isoformat(),
        "quality": day.quality,
        "reason": day.reason,
        "rows": day.rows,
        "universe": day.universe,
        "pct_chg_ratio": day.pct_chg_ratio,
        "limits_present": day.limits_present,
    }


async def resolve_latest_complete_day(db: AsyncSession, *, cache: Any = None) -> MarketDay | None:
    """最新可用交易日 = 候选日（max trade_date）或窗口内最近的完整交易日。

    - 候选日完整 → ``quality="complete"``；
    - 窗口内（含候选日，最多 ``FALLBACK_LOOKBACK_DAYS`` 个交易日）有完整日但非候选日
      → ``quality="fallback"``（脏候选日被跳过，端点不至于集体降级成 1 行）；
    - 窗口内全不完整 → 仍返回候选日，``quality="partial"``，让下游能提示数据不新鲜；
    - ``daily_quotes`` 为空 → ``None``（不抛异常，与已删除的 legacy
      ``get_latest_trade_date`` 的 ``ValueError`` 契约相反）。

    ``cache`` 只要求 duck-typed ``async get`` / ``async set(key, value, ttl=...)``。
    """
    if cache is not None:
        hit = _from_cache(await cache.get(CACHE_KEY))
        if hit is not None:
            return hit

    candidate = await limit_up_repo.latest_quote_date(db)
    if candidate is None:
        return None

    universe = await _universe_count(db)
    days = await limit_up_repo.list_recent_trade_dates(
        db, as_of=candidate, limit=FALLBACK_LOOKBACK_DAYS
    )

    resolved: MarketDay | None = None
    stats_seen: dict[date, tuple[int, float, bool]] = {}
    for day in reversed(days):  # 新→旧：窗口内第一个完整日即答案
        rows, pct_chg_ratio, limits_present = await _day_stats(db, day)
        stats_seen[day] = (rows, pct_chg_ratio, limits_present)
        if not is_day_complete(rows, universe, pct_chg_ratio, limits_present):
            continue
        resolved = MarketDay(
            day=day,
            quality="complete" if day == candidate else "fallback",
            reason=None if day == candidate else _INCOMPLETE_REASON,
            rows=rows,
            universe=universe,
            pct_chg_ratio=pct_chg_ratio,
            limits_present=limits_present,
        )
        break

    if resolved is None:
        # 没有任何完整日用 → 退回候选日本身（partial），而不是让调用方 500。
        # 候选日不在 days 里时（窗口被 limit 截断，理论不可达）用 max(days) 兜底。
        fallback_day = candidate if candidate in days else max(days, default=candidate)
        cached_stats = stats_seen.get(fallback_day)
        rows, pct_chg_ratio, limits_present = (
            cached_stats if cached_stats is not None else await _day_stats(db, fallback_day)
        )
        resolved = MarketDay(
            day=fallback_day,
            quality="partial",
            reason=_INCOMPLETE_REASON,
            rows=rows,
            universe=universe,
            pct_chg_ratio=pct_chg_ratio,
            limits_present=limits_present,
        )

    if cache is not None:
        await cache.set(CACHE_KEY, _to_cache(resolved), CACHE_TTL)
    return resolved


# ── 当日盘后（T+0）期望日 ────────────────────────────────────────────────────
#
# 见 plans/2026-10-16-t0-after-close-expected-latest.md。
#
# **期望日必须与"数据齐备"解耦**：本节只回答"按日历与时钟，现在应该出到哪天"，
# 不探库、不看行数。混在一起会形成两处死锁：
#   ① 对账拉取**之前**当日必然没有数据 ⇒ 若判据要看齐备，当日永远进不了期望集（鸡生蛋），
#      当日行情于是永远拉不到（2026-10-16 的实况正是"库里连一行当日行情都没有"）；
#   ② 读面新鲜度若也看齐备，则"今天整天没采"时会退回昨天并报 `stale=false` —— 与
#      ADR 0016 时代同一个自指 bug（判据的锚来自被判据的对象）。
# 真实的新鲜度由调用方用「实际持有日 vs 期望日」的差表达；删掉自指的正是那一处。

#: 当日纳入期望集的时间闸门（Asia/Shanghai）。取值理由见计划 §3.4 / §四.1：
#: 晚于 16:50 的限价入库（`is_day_complete` 要求 `limits_present`）、早于 17:45 的兜底对账
#: —— 于是既有的 17:45 全量对账自动成为 T+0 收敛点，不必新开 cron。
T0_CUTOFF = time(17, 30)

#: 交易日历缓存（读面用它做本地推算，**不外呼**）。
#: `covered_through` 是**必需**字段：只存日期列表无法区分"今天休市"与"缓存是昨天写的、
#: 还没覆盖今天"；后者若被当成"今天不是交易日"，期望日会静默退回昨天 —— 又是一次
#: "自称新鲜"。所以覆盖不到今天就返回 `None`（无法判定），不许猜。
CALENDAR_CACHE_KEY = "market:day:trade_calendar"
CALENDAR_CACHE_TTL = 7 * 24 * 60 * 60

SH_TZ = ZoneInfo("Asia/Shanghai")


def now_sh(now: datetime | None = None) -> datetime:
    """统一到上海时区（`now=None` 取真实时钟）：naive 输入视为**上海本地时间**.

    与 `market_data_service._today_sh()` 同一约定。

    容器默认 UTC：naive `datetime.now()` 与带时区的 cutoff 直接比较会抛 `TypeError`；
    若按 UTC 解释，则北京时间 17:30 时 UTC 才 09:30，T+0 永远不会触发。
    """
    if now is None:
        return datetime.now(SH_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=SH_TZ)
    return now.astimezone(SH_TZ)


def calendar_expected_latest_day(
    calendar: Sequence[date],
    *,
    covered_through: date | None,
    now: datetime,
) -> date | None:
    """**按日历与时钟**判定"现在应该有的最新交易日"；`None` = 判不出（**不猜**）。

    判据（纯函数：无 IO、无隐含时钟，`now` 由调用方注入，便于逐位复核）：

    - `covered_through` 缺 / 早于今天 ⇒ `None`（缓存没覆盖今天，无法区分"休市"与"没覆盖"）；
    - 今天是交易日且已到 `T0_CUTOFF` ⇒ **今天**；
    - 今天是交易日但未到 cutoff ⇒ 今天之前最近一个交易日（当日数据尚未出全）；
    - 今天不是交易日 ⇒ 今天之前最近一个交易日；
    - 日历里没有任何 ≤ 今天的交易日 ⇒ `None`。

    **它不回答"数据到没到"**：那是 :func:`is_day_complete` 与
    :func:`resolve_latest_complete_day` 的职责。两者刻意分开，理由见本节开头。
    """
    now_sh_dt = now_sh(now)
    today = now_sh_dt.date()
    if covered_through is None or covered_through < today:
        return None
    days_le_today = [day for day in calendar if day <= today]
    if not days_le_today:
        return None
    latest = max(days_le_today)
    if latest != today:  # 今天不是交易日（或日历里没有今天）
        return latest
    if now_sh_dt.time() >= T0_CUTOFF:
        return today
    prior = [day for day in days_le_today if day < today]
    return max(prior) if prior else None


def calendar_cache_payload(days: Sequence[date], covered_through: date) -> dict[str, Any]:
    """交易日历 → 可进 JSON 缓存的载荷（**显式 ISO 字符串**）.

    `date` 不能直接进缓存：`CacheClient` 走 orjson，`date` 不是原生类型（stdlib `json`
    会靠 `default=str` 悄悄转成字符串，orjson 直接抛）。而"写进去是 `date`、读回来是
    `str`"会让后续 `day <= today` 的比较抛 `TypeError` —— 故两侧都显式转换。
    """
    return {
        "days": [day.isoformat() for day in sorted(set(days))],
        "covered_through": covered_through.isoformat(),
    }


def parse_calendar_payload(payload: Any) -> tuple[list[date], date | None] | None:
    """缓存载荷 → `(days, covered_through)`；形状不对一律当**缓存未命中**（不抛）."""
    if not isinstance(payload, dict):
        return None
    raw_days = payload.get("days")
    if not isinstance(raw_days, list):
        return None
    try:
        days = [date.fromisoformat(str(item)) for item in raw_days]
        raw_covered = payload.get("covered_through")
        covered = None if raw_covered is None else date.fromisoformat(str(raw_covered))
    except (TypeError, ValueError):
        logger.warning("trade calendar cache: unusable payload %r", payload)
        return None
    return days, covered


async def write_calendar_cache(cache: Any, *, days: Sequence[date], covered_through: date) -> None:
    """写交易日历缓存（**只在日历非降级时调用**）.

    上游 `trade_cal` 不可用时 `expected_trade_dates` 会降级成工作日启发式（节假日会多出
    几个空日）—— 那是可接受的临时降级，但**不能进缓存**，否则把节假日误判固化 7 天。

    `cache=None`（调用方不关心缓存）时静默跳过；`CacheClient` 自身已把 Redis 故障与
    序列化失败降级成"缓存未命中"，故这里不再包一层 try。
    """
    if cache is None or not days:
        return
    await cache.set(
        CALENDAR_CACHE_KEY,
        calendar_cache_payload(days, covered_through),
        ttl=CALENDAR_CACHE_TTL,
    )


def count_trading_days_between(calendar: Sequence[date], *, after: date, upto: date) -> int:
    """日历上落在 `(after, upto]` 的交易日数（纯函数）.

    **读面的落后天数必须用它、不能用"行情表里实际持有几天"**：在"今天整天没采"这个
    正要修的场景里，行情表里根本没有今天，数出来是 0 ⇒ 读面会报 `stale=false`
    —— 自指 bug 原地复活。日历是外部事实，不随"我们采到了什么"变化。
    """
    return sum(1 for day in calendar if after < day <= upto)


async def resolve_expected_day(
    cache: Any, *, now: datetime | None = None
) -> tuple[date | None, str | None]:
    """读面编排：`(期望日, 判不出的原因)` —— **不外呼 TuShare**、不退回 `daily_quotes`.

    读面（GET）不得外呼上游是本仓的既有纪律（见
    `market_sentiment_composite_service.get_composite_hero`）：日历由对账在
    `degraded=False` 时写缓存，这里只做本地推算。

    `now` 可注入（默认取真实时钟）：纯函数已强制注入时钟，编排层同样留这个口子，
    单测才不必去 patch 模块级 `datetime`。

    缓存未命中 / 覆盖不到今天 ⇒ `(None, reason)`，由调用方**如实报"无法判定"**；
    **不得**退回 `max(daily_quotes)` 那个自指锚（那正是本任务要拆掉的东西）。

    只关心"该到哪天"的调用方用它；同时要**落后天数**（hero）用
    :func:`resolve_expected_day_details`（一次读缓存算齐，省一次 IO）。
    """
    expected, _lag, reason = await resolve_expected_day_details(cache, as_of=None, now=now)
    return expected, reason


async def resolve_expected_day_details(
    cache: Any, *, as_of: date | None, now: datetime | None = None
) -> tuple[date | None, int | None, str | None]:
    """读面一次算齐：`(期望日, as_of 落后的交易日数, 判不出的原因)`.

    - `lag_trading_days` = 日历上 `(as_of, 期望日]` 的交易日数（见
      :func:`count_trading_days_between`）；`as_of=None`（没有选中行）⇒ `None`；
    - 三个返回值**同生共死**：判不出就是 `(None, None, reason)`，绝不半真半假
      （读面三态纪律：`None` 不得被读成"新鲜"）。
    """
    if not settings.t0_after_close_enabled:
        return None, None, "t0_after_close_disabled"
    if cache is None:
        return None, None, "no_cache"
    parsed = parse_calendar_payload(await cache.get(CALENDAR_CACHE_KEY))
    if parsed is None:
        return None, None, "calendar_cache_miss"
    days, covered_through = parsed
    expected = calendar_expected_latest_day(days, covered_through=covered_through, now=now_sh(now))
    if expected is None:
        return None, None, "calendar_not_covering_today"
    lag = None if as_of is None else count_trading_days_between(days, after=as_of, upto=expected)
    return expected, lag, None
