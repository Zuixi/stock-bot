"""Stock and StockHistory ORM models."""

from datetime import date, datetime
from typing import Literal

from sqlalchemy import CheckConstraint, DateTime, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

ExchangeName = Literal["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]


class StockUserTag(Base):
    """User-defined custom tags for stocks (many-to-many, free-form text)."""

    __tablename__ = "stock_user_tags"
    __table_args__ = (
        UniqueConstraint("symbol", "tag_name", name="uq_stock_user_tag"),
        Index("idx_user_tag_symbol", "symbol"),
        Index("idx_user_tag_name", "tag_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(nullable=False)
    tag_name: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

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
    name: Mapped[str] = mapped_column(nullable=False)          # 股票名称
    area: Mapped[str | None]                                   # 地域
    industry: Mapped[str | None]                               # 所属行业
    full_name: Mapped[str | None] = mapped_column(Text)        # 股票全称
    enname: Mapped[str | None]                                 # 英文全称
    cnspell: Mapped[str | None]                                # 拼音缩写
    market: Mapped[str | None]                                 # 市场类型（主板/创业板/科创板/CDR）
    curr_type: Mapped[str | None]                              # 交易货币
    list_status: Mapped[str | None]                            # 上市状态原始值 L/D/P/G
    list_date: Mapped[date | None]                             # 上市日期
    delist_date: Mapped[date | None]                           # 退市日期
    is_hs: Mapped[str | None]                                  # 是否沪深港通标的 N/H/S
    act_name: Mapped[str | None]                               # 实控人名称
    act_ent_type: Mapped[str | None]                           # 实控人企业性质
    # Legacy fields kept for backward compatibility
    category: Mapped[str] = mapped_column(nullable=False)
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
    area: Mapped[str | None]
    industry: Mapped[str | None]
    full_name: Mapped[str | None] = mapped_column(Text)
    enname: Mapped[str | None]
    cnspell: Mapped[str | None]
    market: Mapped[str | None]
    curr_type: Mapped[str | None]
    list_status: Mapped[str | None]
    list_date: Mapped[date | None]
    delist_date: Mapped[date | None]
    is_hs: Mapped[str | None]
    act_name: Mapped[str | None]
    act_ent_type: Mapped[str | None]
    # Legacy fields kept for backward compatibility
    category: Mapped[str] = mapped_column(nullable=False)
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
