"""A股全景日表读面（纯读，一次范围查询；不做 N+1）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import (
    MarketBreadthBucketDaily,
    MarketBreadthDaily,
    MarketIndustryDaily,
    SectorMoneyflowSnapshot,
)
from app.repositories.market_data_repo import EOD_FULL_CAPTURE


async def list_breadth_days(db: AsyncSession, days: int) -> list[MarketBreadthDaily]:
    """最近 `days` 个有行的交易日（升序）。"""
    stmt = (
        select(MarketBreadthDaily)
        .order_by(MarketBreadthDaily.trade_date.desc())
        .limit(max(days, 0))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return list(reversed(rows))


async def latest_breadth_day(db: AsyncSession) -> date | None:
    return (await db.execute(select(func.max(MarketBreadthDaily.trade_date)))).scalar_one_or_none()


async def list_buckets(db: AsyncSession, trade_date: date) -> list[MarketBreadthBucketDaily]:
    stmt = (
        select(MarketBreadthBucketDaily)
        .where(MarketBreadthBucketDaily.trade_date == trade_date)
        .order_by(MarketBreadthBucketDaily.id)
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_structure_days(db: AsyncSession, days: int) -> list[Any]:
    """最近 `days` 个交易日的**结构聚合**（一天一行，不是 31 行/日）。

    在 SQL 里算 Top3 成交额占比 / HHI / 行业收益离散度：250 日窗口只回 250 行，
    避免把 7,750 行 ORM 对象读进 Python（这是 <100ms 的关键）。
    """
    stmt = text(
        """
        WITH recent AS (
            SELECT DISTINCT trade_date
            FROM market_industry_daily
            ORDER BY trade_date DESC
            LIMIT :days
        ), base AS (
            SELECT d.trade_date, d.industry_code, d.amount, d.pct_chg,
                   sum(d.amount) OVER (PARTITION BY d.trade_date) AS total_amount,
                   row_number() OVER (
                       PARTITION BY d.trade_date
                       ORDER BY d.amount DESC NULLS LAST, d.industry_code
                   ) AS rn
            FROM market_industry_daily d
            JOIN recent r ON r.trade_date = d.trade_date
        )
        SELECT trade_date,
               round(
                   (sum(amount) FILTER (WHERE rn <= 3)
                    / nullif(sum(amount), 0))::numeric, 6
               ) AS top3_share,
               round(sum(power(amount / nullif(total_amount, 0), 2))::numeric, 6) AS hhi,
               round(stddev_pop(pct_chg)::numeric, 4) AS dispersion
        FROM base
        GROUP BY trade_date
        ORDER BY trade_date
        """
    )
    return list((await db.execute(stmt, {"days": max(days, 0)})).mappings().all())


async def list_recent_industries(db: AsyncSession, days: int = 2) -> list[MarketIndustryDaily]:
    """最近 `days` 个交易日的行业行（默认 2 天，用于最新榜 + 昨日排名）。"""
    dates = (
        select(MarketIndustryDaily.trade_date)
        .order_by(MarketIndustryDaily.trade_date.desc())
        .distinct()
        .limit(max(days, 0))
        .subquery()
    )
    stmt = (
        select(MarketIndustryDaily)
        .where(MarketIndustryDaily.trade_date.in_(select(dates.c.trade_date)))
        .order_by(MarketIndustryDaily.trade_date, MarketIndustryDaily.industry_code)
    )
    return list((await db.execute(stmt)).scalars().all())


async def latest_industry_day(db: AsyncSession) -> date | None:
    return (await db.execute(select(func.max(MarketIndustryDaily.trade_date)))).scalar_one_or_none()


async def latest_eod_flow_day(db: AsyncSession, dimension: str) -> date | None:
    return (
        await db.execute(
            select(func.max(SectorMoneyflowSnapshot.trade_date)).where(
                SectorMoneyflowSnapshot.dimension == dimension,
                SectorMoneyflowSnapshot.capture_kind == EOD_FULL_CAPTURE,
            )
        )
    ).scalar_one_or_none()


async def earliest_eod_flow_day(db: AsyncSession, dimension: str) -> date | None:
    return (
        await db.execute(
            select(func.min(SectorMoneyflowSnapshot.trade_date)).where(
                SectorMoneyflowSnapshot.dimension == dimension,
                SectorMoneyflowSnapshot.capture_kind == EOD_FULL_CAPTURE,
            )
        )
    ).scalar_one_or_none()


async def list_eod_flow_rows(
    db: AsyncSession, trade_date: date, dimension: str
) -> list[SectorMoneyflowSnapshot]:
    """某完整日的全部板块行（约 86 行），供 Top in/out 同批计算。"""
    stmt = (
        select(SectorMoneyflowSnapshot)
        .where(
            SectorMoneyflowSnapshot.trade_date == trade_date,
            SectorMoneyflowSnapshot.dimension == dimension,
            SectorMoneyflowSnapshot.capture_kind == EOD_FULL_CAPTURE,
        )
        .order_by(desc(SectorMoneyflowSnapshot.main_net_inflow))
    )
    return list((await db.execute(stmt)).scalars().all())


def _num(value: Any) -> float | None:
    return None if value is None else float(value)
