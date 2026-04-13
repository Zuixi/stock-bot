"""Stock and StockHistory ORM models."""

from datetime import date, datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

ExchangeName = Literal["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]

EXCHANGE_CHECK = CheckConstraint(
    "exchange IN ('Shanghai_Stocks', 'Shenzen_Stocks', 'Beijing_Stocks')",
    name="chk_exchange",
)


class Stock(Base):
    """Current stock snapshot — one row per (exchange, symbol)."""

    __tablename__ = "stocks"
    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_stocks_exchange_symbol"),
        EXCHANGE_CHECK,
        Index("idx_stocks_exchange", "exchange"),
        Index("idx_stocks_category", "exchange", "category"),
        Index("idx_stocks_symbol", "symbol"),
        Index("idx_stocks_asof", "asof"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(nullable=False)
    symbol: Mapped[str] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(nullable=False)
    list_date: Mapped[date | None]
    csrc_code: Mapped[str | None]
    csrc_desc: Mapped[str | None]
    province: Mapped[str | None]
    status: Mapped[str | None]
    detail: Mapped[dict | None] = mapped_column(JSONB)
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StockHistory(Base):
    """Historical stock snapshots for audit and backfill."""

    __tablename__ = "stocks_history"
    __table_args__ = (
        Index("idx_stocks_history_key", "exchange", "symbol", "asof"),
        Index("idx_stocks_history_asof", "asof"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(nullable=False)
    symbol: Mapped[str] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(nullable=False)
    list_date: Mapped[date | None]
    csrc_code: Mapped[str | None]
    csrc_desc: Mapped[str | None]
    province: Mapped[str | None]
    status: Mapped[str | None]
    detail: Mapped[dict | None] = mapped_column(JSONB)
    source_url: Mapped[str | None] = mapped_column(Text)
    asof: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
