"""User watchlist request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WatchlistItemIn(BaseModel):
    """Schema for adding/updating a stock in a watchlist."""

    symbol: str = Field(..., min_length=1, max_length=10, description="Stock code / symbol")
    exchange: str | None = Field(default=None, max_length=32, description="Exchange code if known")
    notes: str | None = Field(default=None, description="Optional user notes")
    sort_order: int | None = Field(default=None, ge=0, description="Sort position")


class WatchlistItemOut(BaseModel):
    """Schema for returning a single watchlist stock item."""

    id: uuid.UUID
    watchlist_id: uuid.UUID
    symbol: str
    exchange: str | None = None
    sort_order: int = 0
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WatchlistCreateIn(BaseModel):
    """Schema for creating a new named watchlist."""

    name: str = Field(..., min_length=1, max_length=64)
    is_default: bool = False


class WatchlistOut(BaseModel):
    """Schema for returning a watchlist container with items."""

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    is_default: bool
    items: list[WatchlistItemOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WatchlistReorderIn(BaseModel):
    """Schema for reordering symbols within a watchlist."""

    symbols: list[str] = Field(..., description="Ordered list of stock symbols")
