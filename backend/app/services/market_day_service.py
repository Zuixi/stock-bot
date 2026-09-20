"""日级完整性判据 —— "最新交易日"决策的唯一来源。

行数够 ≠ 数据可用：实测 2026-09-14/15 的 daily_quotes 行数满 5485，但
pct_chg 只有 1 行非空（历史 NULL），下游所有按日聚合的端点都在用 LATERAL
逐股回看前收重算——判据看不出列坏了，于是永远不会被重拉。
同理，一条当日脏行（data_init 覆盖任务写出的未收盘数据）会把 max(trade_date)
顶到今天，让 8 个端点集体降级成 1 行，所以"最新日"必须带完整性门禁并允许回落。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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


def is_day_complete(rows: int, universe: int, pct_chg_ratio: float, limits_present: bool) -> bool:
    """三元组判据：行数达阈值 + pct_chg 非空率达标 + 该日涨跌停价存在。

    `universe <= 0`（stocks 表为空）时一律判不完整——避免把 0/0 的真空判成完整。
    """
    if universe <= 0:
        return False
    if rows < MIN_ROW_RATIO * universe:
        return False
    if pct_chg_ratio < MIN_PCT_CHG_RATIO:
        return False
    return bool(limits_present)


async def _universe_count(db: AsyncSession) -> int:
    """在市标的总数——行数阈值与 pct_chg 覆盖率的分母。"""
    return int((await db.execute(select(func.count()).select_from(Stock))).scalar_one())


async def _day_stats(db: AsyncSession, day: date) -> tuple[int, float, bool]:
    """单日三元组：(行数, pct_chg 非空率, 该日是否有涨跌停价)。

    非空率用 ``count(pct_chg) / count(*)`` 一条 SQL 取回（``count(col)`` 只数
    NULL 之外的行），空日返回 0.0 而不是 ZeroDivisionError。
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
    limits_present = await limit_up_repo.has_price_limits(db, day)
    return rows, ratio, limits_present


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
