"""DailyQuote ORM model — partitioned by trade_date."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyQuote(Base):
    """Daily OHLCV data, partitioned by trade_date (managed externally)."""

    __tablename__ = "daily_quotes"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_daily_quotes_stock_date"),
        CheckConstraint("high >= low AND open >= 0 AND close >= 0", name="chk_ohlc"),
        Index("idx_daily_quotes_stock_date", "stock_id", "trade_date"),
        Index("idx_daily_quotes_date", "trade_date"),
        {"postgresql_partition_by": "RANGE (trade_date)"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    volume: Mapped[int | None]
    amount: Mapped[float | None] = mapped_column(Numeric(20, 2))
    adj_factor: Mapped[float | None] = mapped_column(Numeric(12, 6), default=1.0)
    source: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
