"""Market-data face endpoints: global indices（moneyflow/龙虎榜等面片由后续任务追加）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CacheDep
from app.schemas.market_data import GlobalIndexCardOut
from app.services import market_data_service

router = APIRouter(tags=["market-data"])


@router.get("/global-indices", response_model=list[GlobalIndexCardOut])
async def get_global_indices(cache: CacheDep) -> list[GlobalIndexCardOut]:
    cards = await market_data_service.get_global_index_cards(cache)
    return [GlobalIndexCardOut(**c) for c in cards]
