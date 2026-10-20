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


def watchlists_cache_key(user_id: uuid.UUID | str) -> str:
    """该用户 watchlists 读面缓存键（读面与写面共用，避免两处拼字符串漂移）。"""
    return f"user:{_to_uuid(user_id)}:watchlists"


async def list_user_watchlists(
    db: AsyncSession,
    cache: CacheClient,
    user_id: uuid.UUID | str,
) -> list[WatchlistOut]:
    """Return all watchlists with items for the user, with user-scoped caching."""
    uid = _to_uuid(user_id)
    cache_key = watchlists_cache_key(uid)
    cached = await cache.get(cache_key)
    if cached is not None:
        return [WatchlistOut(**w) for w in cached]

    watchlists = await watchlist_repo.list_user_watchlists(db, uid)
    result = [WatchlistOut.model_validate(w) for w in watchlists]
    await cache.set(cache_key, [w.model_dump(mode="json") for w in result], ttl=300)
    return result


async def add_watchlist_item(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    item_in: WatchlistItemIn,
    watchlist_id: uuid.UUID | None = None,
) -> WatchlistItemOut:
    """Add a stock item to a user's watchlist.

    ⚠️ **不在这里失效缓存**（2026-10-19 修）：本函数跑在调用方的事务里，此时 INSERT 尚未
    commit。若在这里 `cache.delete`，任何并发读（另一个标签页 / 另一台设备 / 前端 invalidate
    触发的 GET）都会在 commit 之前把**旧列表**重新写回缓存（TTL 300s）——症状就是
    "刚加的自选时有时无，刷新也不出现；刚删的还在，再点删除变成 404"。
    正确顺序是 **commit → 失效**，由路由层在 `db.commit()` 之后调用
    :func:`watchlists_cache_key` 删除（写面唯一职责是落库）。
    """
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
    return WatchlistItemOut.model_validate(item)


async def remove_watchlist_item(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    symbol: str,
    watchlist_id: uuid.UUID | None = None,
) -> bool:
    """Remove a stock item from user's watchlist(s).

    缓存失效同样交给路由层在 commit 之后做（理由见 :func:`add_watchlist_item`）。
    """
    uid = _to_uuid(user_id)
    return await watchlist_repo.remove_item_from_watchlist(
        db,
        user_id=uid,
        symbol=symbol,
        watchlist_id=watchlist_id,
    )


async def reorder_watchlist_items(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    reorder_in: WatchlistReorderIn,
    watchlist_id: uuid.UUID | None = None,
) -> None:
    """Reorder items in a watchlist（缓存失效同样交给路由层在 commit 之后做）。"""
    uid = _to_uuid(user_id)
    await watchlist_repo.reorder_items(
        db,
        user_id=uid,
        symbols=reorder_in.symbols,
        watchlist_id=watchlist_id,
    )
