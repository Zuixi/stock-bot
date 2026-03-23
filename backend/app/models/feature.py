"""StockFeature ORM model — partitioned by asof_date."""

from datetime import date, datetime

from sqlalchemy import DateTime, Index, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockFeature(Base):
    """Feature-engineering results for a stock over a sliding window."""

    __tablename__ = "stock_features"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "asof_date", "window_days", name="uq_stock_features_key"
        ),
        Index("idx_stock_features_stock_date", "stock_id", "asof_date"),
        Index("idx_stock_features_window", "asof_date", "window_days"),
        # Partitioning is managed externally. Do NOT put postgresql_partition_by here.
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(nullable=False)
    asof_date: Mapped[date] = mapped_column(nullable=False)
    window_days: Mapped[int] = mapped_column(nullable=False)

    # Return metrics
    total_return: Mapped[float | None] = mapped_column(Numeric(12, 6))
    return_percentile: Mapped[float | None] = mapped_column(Numeric(8, 4))

    # Risk metrics
    annual_volatility: Mapped[float | None] = mapped_column(Numeric(12, 6))
    max_drawdown: Mapped[float | None] = mapped_column(Numeric(12, 6))
    downside_vol: Mapped[float | None] = mapped_column(Numeric(12, 6))

    # Trend metrics
    trend_slope: Mapped[float | None] = mapped_column(Numeric(12, 6))
    trend_r2: Mapped[float | None] = mapped_column(Numeric(8, 4))
    ma_bullish: Mapped[bool | None]
    trend_reversals: Mapped[int | None]

    # Liquidity metrics
    avg_volume: Mapped[float | None] = mapped_column(Numeric(20, 2))
    volume_volatility: Mapped[float | None] = mapped_column(Numeric(12, 6))

    # Extensible additional features
    extra: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
