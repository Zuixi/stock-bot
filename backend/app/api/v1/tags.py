"""User-defined tags global endpoints.

Routes:
    GET  /api/v1/tags              - List all tags with stock counts
    GET  /api/v1/tags/{tag_name}/stocks - List stocks for a given tag
"""

from fastapi import APIRouter

from app.api.deps import CacheDep, DbDep
from app.schemas.stock import StockOut, TagSummary
from app.services import user_tag_service

router = APIRouter()


@router.get("", response_model=list[TagSummary])
async def list_all_tags(db: DbDep, cache: CacheDep) -> list[TagSummary]:
    """List all user-defined tags with stock counts."""
    cache_key = "tags:all"
    cached = await cache.get(cache_key)
    if cached:
        return [TagSummary(**t) for t in cached]

    tags = await user_tag_service.list_all_tags(db)
    await cache.set(cache_key, [t.model_dump() for t in tags], ttl=300)
    return tags


@router.get("/{tag_name}/stocks", response_model=list[StockOut])
async def get_stocks_by_tag(tag_name: str, db: DbDep, cache: CacheDep) -> list[StockOut]:
    """List all stocks that have the given tag."""
    cache_key = f"tags:stocks:{tag_name}"
    cached = await cache.get(cache_key)
    if cached:
        return [StockOut(**s) for s in cached]

    stocks = await user_tag_service.get_stocks_by_tag(db, tag_name)
    await cache.set(cache_key, [s.model_dump(mode="json") for s in stocks], ttl=300)
    return stocks
