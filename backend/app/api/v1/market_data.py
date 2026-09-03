"""Market-data face endpoints: global indices / sector moneyflow / northbound（北向）。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import CacheDep
from app.schemas.market_data import GlobalIndexCardOut, NorthboundPointOut, SectorMoneyflowOut
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
