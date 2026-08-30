"""Industry research workbench ORM models.

行业投研工作台（猪智投为首个实例）的三张核心表：
- industry_metrics          指标单表（行业级 + 公司级共用，见 stock_id 说明）
- industry_reference_points 政策锚点（随政策修订带生效日期，禁止硬编码）
- industry_signals          信号历史（周期判定结果，可回测可审计）

metric_key 命名与 docs/design/data-source.md 对齐。
"""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IndustryMetric(Base):
    """One observation of one metric for an industry (or a company within it).

    ``stock_id = 0`` 表示行业级指标；公司级指标（标的分析，P5）携带 stocks.id（>0）。
    ``period`` 为该值所属周期的截止日（月度即月末、日度即当日）。
    ``source`` + 唯一约束保证重跑幂等；派生指标（猪粮比等）以 source='derived' 统一落表。
    """

    __tablename__ = "industry_metrics"
    __table_args__ = (
        UniqueConstraint(
            "industry_key", "stock_id", "metric_key", "source", "period",
            name="uq_industry_metrics_key",
        ),
        Index("idx_industry_metrics_lookup", "industry_key", "metric_key", "period"),
        Index("idx_industry_metrics_industry", "industry_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_key: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_id: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    # official / highfreq / calc / manual / derived
    source_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    freq: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    period: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(18, 4))
    unit: Mapped[str | None] = mapped_column(String(16))
    extra: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IndustryReferencePoint(Base):
    """Policy anchor value with an effective date.

    例：能繁母猪正常保有量 4100（2021 方案）→ 3900（2024 方案）→ 3750（2026 修订）。
    查询侧按 effective_from <= 今日取最新一条，参考线随日期自动切换。
    """

    __tablename__ = "industry_reference_points"
    __table_args__ = (
        UniqueConstraint(
            "industry_key", "metric_key", "label", "effective_from",
            name="uq_industry_reference_points",
        ),
        Index(
            "idx_industry_reference_lookup",
            "industry_key", "metric_key", "effective_from",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_key: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IndustrySignal(Base):
    """Cycle evaluation result for one day (rules engine output history)."""

    __tablename__ = "industry_signals"
    __table_args__ = (
        UniqueConstraint("industry_key", "effective_date", name="uq_industry_signals_date"),
        Index("idx_industry_signals_lookup", "industry_key", "effective_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_key: Mapped[str] = mapped_column(String(32), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    positions: Mapped[list | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    basis: Mapped[dict | None] = mapped_column(JSONB)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
