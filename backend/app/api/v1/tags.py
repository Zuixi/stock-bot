"""User-defined tags global endpoints with user isolation.

Routes:
    GET  /api/v1/tags              - List all tags with stock counts for current user
    GET  /api/v1/tags/{tag_name}/stocks - List stocks for a given tag for current user
"""

from fastapi import APIRouter

from app.api.deps import CacheDep, CurrentUserDep, DbDep
from app.schemas.stock import StockOut, TagSummary
from app.services import user_tag_service

router = APIRouter()


@router.get("", response_model=list[TagSummary])
async def list_all_tags(
    db: DbDep,
    cache: CacheDep,
    user: CurrentUserDep,
) -> list[TagSummary]:
    """List all user-defined tags with stock counts for current user."""
    assert user.user_id is not None
    cache_key = f"user:{user.user_id}:tags:all"
    cached = await cache.get(cache_key)
    if cached:
        return [TagSummary(**t) for t in cached]

    tags = await user_tag_service.list_all_tags(db, user.user_id)
    await cache.set(cache_key, [t.model_dump() for t in tags], ttl=300)
    return tags


@router.get("/{tag_name}/stocks", response_model=list[StockOut])
async def get_stocks_by_tag(
    tag_name: str,
    db: DbDep,
    cache: CacheDep,
    user: CurrentUserDep,
) -> list[StockOut]:
    """List all stocks that have the given tag for current user."""
    assert user.user_id is not None
    cache_key = f"user:{user.user_id}:tags:stocks:{tag_name}"
    cached = await cache.get(cache_key)
    if cached:
        return [StockOut(**s) for s in cached]

    stocks = await user_tag_service.get_stocks_by_tag(db, user.user_id, tag_name)
    await cache.set(cache_key, [s.model_dump(mode="json") for s in stocks], ttl=300)
    return stocks
