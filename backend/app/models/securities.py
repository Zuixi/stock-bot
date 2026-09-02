"""Industry securities ORM models — ETF/可转债日线（P5 行情面）.

数据源 TuShare ``fund_daily`` / ``cb_daily``，由 registry 的 etf_codes/cb_codes
驱动逐代码回补；``volume`` 单位为手（ETF）/张（转债），``amount`` 单位千元，
均按上游原样落库（与 daily_quotes 的 amount 口径一致，不做换算）。
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FundEtfDaily(Base):
    """ETF 场内基金日线（TuShare fund_daily，按 registry etf_codes 逐代码回补）。"""

    __tablename__ = "fund_etf_daily"
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_fund_etf_daily_code_date"),
        Index("idx_fund_etf_daily_code_date", "ts_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(12), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    volume: Mapped[float | None] = mapped_column(Numeric(20, 2))
    amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CbDaily(Base):
    """可转债日线（TuShare cb_daily，按 registry cb_codes 逐代码回补）。"""

    __tablename__ = "cb_daily"
    __table_args__ = (
        UniqueConstraint("ts_code", "trade_date", name="uq_cb_daily_code_date"),
        Index("idx_cb_daily_code_date", "ts_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(12), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float | None] = mapped_column(Numeric(12, 4))
    high: Mapped[float | None] = mapped_column(Numeric(12, 4))
    low: Mapped[float | None] = mapped_column(Numeric(12, 4))
    close: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    volume: Mapped[float | None] = mapped_column(Numeric(20, 2))
    amount: Mapped[float | None] = mapped_column(Numeric(20, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
