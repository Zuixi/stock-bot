"""SseIndexSnapshot ORM model — stores intraday SSE index snapshots scraped every 10 min."""

from datetime import date, datetime

from sqlalchemy import DateTime, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SseIndexSnapshot(Base):
    """Intraday snapshot for SSE indices, keyed by (code, collect_time)."""

    __tablename__ = "sse_index_snapshots"
    __table_args__ = (
        UniqueConstraint("code", "collect_time", name="uq_sse_snapshots_code_time"),
        Index("idx_sse_snapshots_code_date", "code", "trade_date"),
        Index("idx_sse_snapshots_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(nullable=False)
    collect_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prev_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    last: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    chg_rate: Mapped[float | None] = mapped_column(Numeric(8, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
