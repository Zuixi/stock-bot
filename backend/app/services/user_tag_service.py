"""Service for managing user-defined custom tags on stocks."""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock, StockUserTag
from app.schemas.stock import StockOut, TagSummary, UserTagOut

logger = logging.getLogger(__name__)


async def get_stock_tags(db: AsyncSession, symbol: str) -> list[UserTagOut]:
    """Return all user-defined tags for a stock."""
    rows = (
        await db.execute(
            select(StockUserTag)
            .where(StockUserTag.symbol == symbol)
            .order_by(StockUserTag.tag_name)
        )
    ).scalars().all()
    return [UserTagOut.model_validate(r) for r in rows]


async def add_stock_tag(db: AsyncSession, symbol: str, tag_name: str) -> UserTagOut:
    """Add a tag to a stock. Returns the created tag."""
    tag_name = tag_name.strip()
    if not tag_name:
        raise ValueError("Tag name cannot be empty")
    if len(tag_name) > 64:
        raise ValueError("Tag name too long (max 64 chars)")

    existing = (
        await db.execute(
            select(StockUserTag).where(
                StockUserTag.symbol == symbol,
                StockUserTag.tag_name == tag_name,
            )
        )
    ).scalar_one_or_none()

    if existing:
        return UserTagOut.model_validate(existing)

    tag = StockUserTag(symbol=symbol, tag_name=tag_name)
    db.add(tag)
    await db.flush()
    await db.refresh(tag)
    return UserTagOut.model_validate(tag)


async def remove_stock_tag(db: AsyncSession, symbol: str, tag_name: str) -> bool:
    """Remove a tag from a stock. Returns True if deleted."""
    result = await db.execute(
        delete(StockUserTag).where(
            StockUserTag.symbol == symbol,
            StockUserTag.tag_name == tag_name,
        )
    )
    await db.flush()
    return result.rowcount > 0


async def list_all_tags(db: AsyncSession) -> list[TagSummary]:
    """Return all tags with their stock counts, sorted by count desc."""
    rows = (
        await db.execute(
            select(
                StockUserTag.tag_name,
                func.count(StockUserTag.id).label("stock_count"),
            )
            .group_by(StockUserTag.tag_name)
            .order_by(func.count(StockUserTag.id).desc(), StockUserTag.tag_name)
        )
    ).all()
    return [TagSummary(tag_name=r.tag_name, stock_count=r.stock_count) for r in rows]


async def get_stocks_by_tag(db: AsyncSession, tag_name: str) -> list[StockOut]:
    """Return all stocks that have the given tag."""
    rows = (
        await db.execute(
            select(Stock)
            .join(StockUserTag, StockUserTag.symbol == Stock.symbol)
            .where(StockUserTag.tag_name == tag_name)
            .order_by(Stock.symbol)
        )
    ).scalars().all()
    return [StockOut.model_validate(r) for r in rows]
