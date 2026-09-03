"""Market-data face endpoints: global indices / sector moneyflow / northbound（北向）/
dragon-tiger（龙虎榜）/ block-trades（大宗交易）/ share-floats（解禁）/ repurchases（回购）."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CacheDep
from app.schemas.market_data import (
    BlockTradeOut,
    DragonTigerOut,
    GlobalIndexCardOut,
    NorthboundPointOut,
    RepurchaseOut,
    SectorMoneyflowOut,
    ShareFloatOut,
)
from app.services import market_data_service

router = APIRouter(tags=["market-data"])


@router.get("/global-indices", response_model=list[GlobalIndexCardOut])
async def get_global_indices(cache: CacheDep) -> list[GlobalIndexCardOut]:
    cards = await market_data_service.get_global_index_cards(cache)
    return [GlobalIndexCardOut(**c) for c in cards]


@router.get("/sector-moneyflow", response_model=list[SectorMoneyflowOut])
async def get_sector_moneyflow_endpoint(
    cache: CacheDep,
    dimension: Literal["industry", "concept"] = "industry",
    limit: int = Query(default=15, ge=1, le=100),
) -> list[SectorMoneyflowOut]:
    rows = await market_data_service.get_sector_moneyflow(cache, dimension, limit)
    return [SectorMoneyflowOut(**r) for r in rows]


@router.get("/northbound", response_model=list[NorthboundPointOut])
async def get_northbound(
    cache: CacheDep, days: int = Query(default=30, ge=1, le=180)
) -> list[NorthboundPointOut]:
    rows = await market_data_service.get_northbound_series(cache, days)
    return [NorthboundPointOut(**r) for r in rows]


@router.get("/dragon-tiger", response_model=list[DragonTigerOut])
async def get_dragon_tiger_endpoint(
    cache: CacheDep,
    date: str | None = Query(default=None, description="ISO 日期，缺省=表内最新交易日"),
    limit: int = Query(default=15, ge=1, le=100),
) -> list[DragonTigerOut]:
    try:
        rows = await market_data_service.get_dragon_tiger(cache, date, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-09-02"
        ) from None
    return [DragonTigerOut(**r) for r in rows]


@router.get("/block-trades", response_model=list[BlockTradeOut])
async def get_block_trades_endpoint(
    cache: CacheDep,
    date: str | None = Query(default=None, description="ISO 日期，缺省=表内最新交易日"),
    symbol: str | None = Query(default=None, description="6 位股票代码，如 000488"),
    limit: int = Query(default=15, ge=1, le=100),
) -> list[BlockTradeOut]:
    try:
        rows = await market_data_service.get_block_trades(cache, date, symbol, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-09-02"
        ) from None
    return [BlockTradeOut(**r) for r in rows]


@router.get("/share-floats", response_model=list[ShareFloatOut])
async def get_share_floats_endpoint(
    cache: CacheDep,
    start: str | None = Query(default=None, description="ISO 起始日期，缺省=近 30 天"),
    end: str | None = Query(default=None, description="ISO 结束日期，缺省=未来 90 天"),
    symbol: str | None = Query(default=None, description="6 位股票代码，如 002747"),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[ShareFloatOut]:
    try:
        rows = await market_data_service.get_share_floats(cache, start, end, symbol, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="start/end must be ISO format, e.g. 2026-09-02"
        ) from None
    return [ShareFloatOut(**r) for r in rows]


@router.get("/repurchases", response_model=list[RepurchaseOut])
async def get_repurchases_endpoint(
    cache: CacheDep,
    start: str | None = Query(default=None, description="ISO 起始日期，缺省=近 30 天"),
    end: str | None = Query(default=None, description="ISO 结束日期，缺省=今天"),
    symbol: str | None = Query(default=None, description="6 位股票代码，如 002120"),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[RepurchaseOut]:
    try:
        rows = await market_data_service.get_repurchases(cache, start, end, symbol, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="start/end must be ISO format, e.g. 2026-09-02"
        ) from None
    return [RepurchaseOut(**r) for r in rows]
