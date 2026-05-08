"""Daily basic service: fetch fundamental indicators with caching."""

import logging
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import daily_basic_repo, stock_repo
from app.schemas.daily_basic import (
    DailyBasicLatestOut,
    DailyBasicListResponse,
    DailyBasicOut,
)

logger = logging.getLogger(__name__)


async def get_daily_basic_history(
    db: AsyncSession,
    cache: CacheClient,
    exchange: str,
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DailyBasicListResponse | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    start_str = start_date.isoformat() if start_date else "all"
    end_str = end_date.isoformat() if end_date else "all"
    cache_key = f"daily_basic:history:{exchange}:{symbol}:{start_str}:{end_str}"
    cached = await cache.get(cache_key)
    if cached:
        return DailyBasicListResponse(**cached)

    rows = await daily_basic_repo.get_daily_basic(
        db, stock.id, start_date, end_date
    )
    data = [DailyBasicOut.model_validate(r) for r in rows]
    response = DailyBasicListResponse(
        symbol=symbol, name=stock.name, exchange=exchange, data=data
    )
    await cache.set(cache_key, response.model_dump(mode="json"), ttl=600)
    return response


async def get_latest_daily_basic(
    db: AsyncSession,
    cache: CacheClient,
    exchange: str,
    symbol: str,
) -> DailyBasicLatestOut | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    cache_key = f"daily_basic:latest:{exchange}:{symbol}"
    cached = await cache.get(cache_key)
    if cached:
        return DailyBasicLatestOut(**cached)

    row = await daily_basic_repo.get_latest_daily_basic(db, stock.id)
    if row is None:
        return None

    out = DailyBasicLatestOut(
        symbol=symbol,
        name=stock.name,
        exchange=exchange,
        trade_date=row.trade_date,
        pe=float(row.pe) if row.pe else None,
        pe_ttm=float(row.pe_ttm) if row.pe_ttm else None,
        pb=float(row.pb) if row.pb else None,
        ps=float(row.ps) if row.ps else None,
        ps_ttm=float(row.ps_ttm) if row.ps_ttm else None,
        total_mv=float(row.total_mv) if row.total_mv else None,
        circ_mv=float(row.circ_mv) if row.circ_mv else None,
        turnover_rate=float(row.turnover_rate) if row.turnover_rate else None,
        volume_ratio=float(row.volume_ratio) if row.volume_ratio else None,
        dv_ratio=float(row.dv_ratio) if row.dv_ratio else None,
        dv_ttm=float(row.dv_ttm) if row.dv_ttm else None,
        total_share=float(row.total_share) if row.total_share else None,
        float_share=float(row.float_share) if row.float_share else None,
        updated_at=row.created_at,
    )
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=600)
    return out
