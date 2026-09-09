"""Watchlist repository: DB operations for UserWatchlist and UserWatchlistItem."""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.watchlist import UserWatchlist, UserWatchlistItem


async def get_or_create_default_watchlist(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> UserWatchlist:
    """Get the user's default watchlist, or create one if it doesn't exist."""
    stmt = (
        select(UserWatchlist)
        .options(selectinload(UserWatchlist.items))
        .where(UserWatchlist.user_id == user_id, UserWatchlist.is_default.is_(True))
    )
    wl = (await db.execute(stmt)).scalar_one_or_none()
    if wl is not None:
        return wl

    # If no default watchlist exists, create one
    new_wl = UserWatchlist(
        user_id=user_id,
        name="默认自选",
        is_default=True,
    )
    db.add(new_wl)
    await db.flush()
    await db.refresh(new_wl, attribute_names=["items"])
    return new_wl


async def list_user_watchlists(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[UserWatchlist]:
    """List all watchlists belonging to a user with their items loaded."""
    stmt = (
        select(UserWatchlist)
        .options(selectinload(UserWatchlist.items))
        .where(UserWatchlist.user_id == user_id)
        .order_by(UserWatchlist.is_default.desc(), UserWatchlist.created_at.asc())
    )
    result = (await db.execute(stmt)).scalars().all()
    watchlists = list(result)
    if not watchlists:
        default_wl = await get_or_create_default_watchlist(db, user_id)
        return [default_wl]
    return watchlists


async def get_watchlist(
    db: AsyncSession,
    user_id: uuid.UUID,
    watchlist_id: uuid.UUID,
) -> UserWatchlist | None:
    """Get a single watchlist owned by user_id."""
    stmt = (
        select(UserWatchlist)
        .options(selectinload(UserWatchlist.items))
        .where(UserWatchlist.user_id == user_id, UserWatchlist.id == watchlist_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def add_item_to_watchlist(
    db: AsyncSession,
    user_id: uuid.UUID,
    symbol: str,
    exchange: str | None = None,
    notes: str | None = None,
    sort_order: int | None = None,
    watchlist_id: uuid.UUID | None = None,
) -> UserWatchlistItem:
    """Add or update a stock symbol in a user's watchlist."""
    symbol = symbol.strip().upper()
    if watchlist_id is not None:
        wl = await get_watchlist(db, user_id, watchlist_id)
        if wl is None:
            raise ValueError(f"Watchlist {watchlist_id} not found for user")
    else:
        wl = await get_or_create_default_watchlist(db, user_id)

    # Check if item already exists in this watchlist
    stmt = select(UserWatchlistItem).where(
        UserWatchlistItem.watchlist_id == wl.id,
        UserWatchlistItem.symbol == symbol,
    )
    existing_item = (await db.execute(stmt)).scalar_one_or_none()
    if existing_item is not None:
        if exchange is not None:
            existing_item.exchange = exchange
        if notes is not None:
            existing_item.notes = notes
        if sort_order is not None:
            existing_item.sort_order = sort_order
        await db.flush()
        await db.refresh(existing_item)
        return existing_item

    # Determine sort order if not specified
    if sort_order is None:
        count_stmt = select(func.coalesce(func.max(UserWatchlistItem.sort_order), -1)).where(
            UserWatchlistItem.watchlist_id == wl.id
        )
        max_order = (await db.execute(count_stmt)).scalar_one()
        sort_order = max_order + 1

    item = UserWatchlistItem(
        watchlist_id=wl.id,
        symbol=symbol,
        exchange=exchange,
        sort_order=sort_order,
        notes=notes,
    )
    db.add(item)
    await db.flush()
    await db.refresh(item)
    return item


async def remove_item_from_watchlist(
    db: AsyncSession,
    user_id: uuid.UUID,
    symbol: str,
    watchlist_id: uuid.UUID | None = None,
) -> bool:
    """Remove a symbol from user's watchlist(s)."""
    symbol = symbol.strip().upper()
    if watchlist_id is not None:
        wl = await get_watchlist(db, user_id, watchlist_id)
        if wl is None:
            return False
        stmt = delete(UserWatchlistItem).where(
            UserWatchlistItem.watchlist_id == wl.id,
            UserWatchlistItem.symbol == symbol,
        )
    else:
        # Delete from all watchlists owned by user_id
        user_wl_ids_stmt = select(UserWatchlist.id).where(UserWatchlist.user_id == user_id)
        stmt = delete(UserWatchlistItem).where(
            UserWatchlistItem.watchlist_id.in_(user_wl_ids_stmt),
            UserWatchlistItem.symbol == symbol,
        )

    result = await db.execute(stmt)
    await db.flush()
    return cast(CursorResult, result).rowcount > 0


async def reorder_items(
    db: AsyncSession,
    user_id: uuid.UUID,
    symbols: list[str],
    watchlist_id: uuid.UUID | None = None,
) -> None:
    """Update sort_order of items in a watchlist to match the order in symbols."""
    if watchlist_id is not None:
        wl = await get_watchlist(db, user_id, watchlist_id)
    else:
        wl = await get_or_create_default_watchlist(db, user_id)

    if wl is None:
        return

    items_stmt = select(UserWatchlistItem).where(UserWatchlistItem.watchlist_id == wl.id)
    items = list((await db.execute(items_stmt)).scalars().all())
    item_map = {item.symbol: item for item in items}

    for order, sym in enumerate(symbols):
        norm_sym = sym.strip().upper()
        if norm_sym in item_map:
            item_map[norm_sym].sort_order = order

    await db.flush()
