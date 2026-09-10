"""Watchlist service: manages user watchlists, caching, and business logic."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import watchlist_repo
from app.schemas.watchlist import (
    WatchlistItemIn,
    WatchlistItemOut,
    WatchlistOut,
    WatchlistReorderIn,
)

logger = logging.getLogger(__name__)


def _to_uuid(uid: uuid.UUID | str) -> uuid.UUID:
    if isinstance(uid, uuid.UUID):
        return uid
    return uuid.UUID(str(uid))


async def list_user_watchlists(
    db: AsyncSession,
    cache: CacheClient,
    user_id: uuid.UUID | str,
) -> list[WatchlistOut]:
    """Return all watchlists with items for the user, with user-scoped caching."""
    uid = _to_uuid(user_id)
    cache_key = f"user:{uid}:watchlists"
    cached = await cache.get(cache_key)
    if cached is not None:
        return [WatchlistOut(**w) for w in cached]

    watchlists = await watchlist_repo.list_user_watchlists(db, uid)
    result = [WatchlistOut.model_validate(w) for w in watchlists]
    await cache.set(cache_key, [w.model_dump(mode="json") for w in result], ttl=300)
    return result


async def add_watchlist_item(
    db: AsyncSession,
    cache: CacheClient,
    user_id: uuid.UUID | str,
    item_in: WatchlistItemIn,
    watchlist_id: uuid.UUID | None = None,
) -> WatchlistItemOut:
    """Add a stock item to a user's watchlist and invalidate cache."""
    uid = _to_uuid(user_id)
    item = await watchlist_repo.add_item_to_watchlist(
        db,
        user_id=uid,
        symbol=item_in.symbol,
        exchange=item_in.exchange,
        notes=item_in.notes,
        sort_order=item_in.sort_order,
        watchlist_id=watchlist_id,
    )
    await cache.delete(f"user:{uid}:watchlists")
    return WatchlistItemOut.model_validate(item)


async def remove_watchlist_item(
    db: AsyncSession,
    cache: CacheClient,
    user_id: uuid.UUID | str,
    symbol: str,
    watchlist_id: uuid.UUID | None = None,
) -> bool:
    """Remove a stock item from user's watchlist(s) and invalidate cache."""
    uid = _to_uuid(user_id)
    deleted = await watchlist_repo.remove_item_from_watchlist(
        db,
        user_id=uid,
        symbol=symbol,
        watchlist_id=watchlist_id,
    )
    if deleted:
        await cache.delete(f"user:{uid}:watchlists")
    return deleted


async def reorder_watchlist_items(
    db: AsyncSession,
    cache: CacheClient,
    user_id: uuid.UUID | str,
    reorder_in: WatchlistReorderIn,
    watchlist_id: uuid.UUID | None = None,
) -> None:
    """Reorder items in a watchlist and invalidate cache."""
    uid = _to_uuid(user_id)
    await watchlist_repo.reorder_items(
        db,
        user_id=uid,
        symbols=reorder_in.symbols,
        watchlist_id=watchlist_id,
    )
    await cache.delete(f"user:{uid}:watchlists")
