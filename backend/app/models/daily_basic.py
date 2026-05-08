"""DailyBasicIndicator ORM model.

Stores daily fundamental indicators from TuShare ``daily_basic`` API:
PE, PB, PS, market cap, turnover rate, share counts, dividend yield, etc.
"""

from datetime import date, datetime

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DailyBasicIndicator(Base):
    """Daily fundamental indicators per (stock_id, trade_date)."""

    __tablename__ = "daily_basic_indicators"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "trade_date",
            name="uq_daily_basic_stock_date",
        ),
        Index("idx_daily_basic_stock_date", "stock_id", "trade_date"),
        Index("idx_daily_basic_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(nullable=False)

    close: Mapped[float | None] = mapped_column(Numeric(12, 4))

    # Market activity
    turnover_rate: Mapped[float | None] = mapped_column(Numeric(12, 4))
    turnover_rate_f: Mapped[float | None] = mapped_column(Numeric(12, 4))
    volume_ratio: Mapped[float | None] = mapped_column(Numeric(12, 4))

    # Valuation
    pe: Mapped[float | None] = mapped_column(Numeric(16, 4))
    pe_ttm: Mapped[float | None] = mapped_column(Numeric(16, 4))
    pb: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ps: Mapped[float | None] = mapped_column(Numeric(12, 4))
    ps_ttm: Mapped[float | None] = mapped_column(Numeric(12, 4))

    # Dividend
    dv_ratio: Mapped[float | None] = mapped_column(Numeric(12, 4))
    dv_ttm: Mapped[float | None] = mapped_column(Numeric(12, 4))

    # Shares (in 10,000 shares)
    total_share: Mapped[float | None] = mapped_column(Numeric(20, 4))
    float_share: Mapped[float | None] = mapped_column(Numeric(20, 4))
    free_share: Mapped[float | None] = mapped_column(Numeric(20, 4))

    # Market cap (in 10,000 CNY)
    total_mv: Mapped[float | None] = mapped_column(Numeric(20, 2))
    circ_mv: Mapped[float | None] = mapped_column(Numeric(20, 2))

    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
