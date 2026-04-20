"""IndexDaily ORM model — stores index OHLCV data (e.g. 上证指数, 深证成指)."""

from datetime import date, datetime

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IndexDaily(Base):
    """Daily OHLCV for market indices, keyed by (ts_code, trade_date)."""

    __tablename__ = "index_dailies"
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_index_dailies_code_date"),
        Index("idx_index_dailies_ts_date", "ts_code", "trade_date"),
        Index("idx_index_dailies_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    volume: Mapped[float | None] = mapped_column(Numeric(20, 2))
    amount: Mapped[float | None] = mapped_column(Numeric(20, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
