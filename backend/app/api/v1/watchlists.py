"""Watchlist endpoints: list watchlists, add/remove/reorder watchlist items."""

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CacheDep, CurrentUserDep, DbDep
from app.schemas.watchlist import (
    WatchlistItemIn,
    WatchlistItemOut,
    WatchlistOut,
    WatchlistReorderIn,
)
from app.services import watchlist_service

router = APIRouter()


@router.get("", response_model=list[WatchlistOut])
async def list_watchlists(
    db: DbDep,
    cache: CacheDep,
    user: CurrentUserDep,
) -> list[WatchlistOut]:
    """List all watchlists and their items for the current authenticated user."""
    assert user.user_id is not None
    return await watchlist_service.list_user_watchlists(db, cache, user.user_id)


@router.post(
    "/items",
    response_model=WatchlistItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_watchlist_item(
    item_in: WatchlistItemIn,
    db: DbDep,
    cache: CacheDep,
    user: CurrentUserDep,
    watchlist_id: uuid.UUID | None = Query(
        None, description="Target watchlist ID (default if omitted)"
    ),
) -> WatchlistItemOut:
    """Add a stock symbol to the user's watchlist."""
    assert user.user_id is not None
    try:
        item = await watchlist_service.add_watchlist_item(
            db, cache, user.user_id, item_in, watchlist_id=watchlist_id
        )
        await db.commit()
        return item
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete(
    "/items/{symbol}",
    response_model=dict,
)
async def remove_watchlist_item(
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    user: CurrentUserDep,
    watchlist_id: uuid.UUID | None = Query(
        None, description="Target watchlist ID (all if omitted)"
    ),
) -> dict:
    """Remove a stock symbol from the user's watchlist(s)."""
    assert user.user_id is not None
    deleted = await watchlist_service.remove_watchlist_item(
        db, cache, user.user_id, symbol, watchlist_id=watchlist_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock {symbol} not found in watchlist",
        )
    await db.commit()
    return {"deleted": True, "symbol": symbol}


@router.put(
    "/items/reorder",
    response_model=dict,
)
async def reorder_watchlist_items(
    reorder_in: WatchlistReorderIn,
    db: DbDep,
    cache: CacheDep,
    user: CurrentUserDep,
    watchlist_id: uuid.UUID | None = Query(
        None, description="Target watchlist ID (default if omitted)"
    ),
) -> dict:
    """Update sort order for symbols in the user's watchlist."""
    assert user.user_id is not None
    await watchlist_service.reorder_watchlist_items(
        db, cache, user.user_id, reorder_in, watchlist_id=watchlist_id
    )
    await db.commit()
    return {"success": True}
