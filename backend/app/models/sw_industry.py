"""Shenwan (申万) industry classification ORM models."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SwIndustryClass(Base):
    """Three-level Shenwan industry classification tree node."""

    __tablename__ = "sw_industry_classes"
    __table_args__ = (
        UniqueConstraint("industry_code", name="uq_sw_class_code"),
        Index("idx_sw_class_level", "level"),
        Index("idx_sw_class_parent", "parent_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    level: Mapped[int] = mapped_column(nullable=False)
    industry_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SwIndustryMember(Base):
    """Mapping of A-share stocks to L3 Shenwan industry codes."""

    __tablename__ = "sw_industry_members"
    __table_args__ = (
        UniqueConstraint("industry_code", "stock_code", name="uq_sw_member_code_stock"),
        Index("idx_sw_member_industry", "industry_code"),
        Index("idx_sw_member_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
