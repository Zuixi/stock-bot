"""Clustering-related ORM models."""

import uuid
from datetime import date, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ClusteringRun(Base):
    """Metadata for one clustering execution (supports multi-version)."""

    __tablename__ = "clustering_runs"
    __table_args__ = (
        Index("idx_clustering_runs_asof", "asof_date"),
        Index("idx_clustering_runs_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str | None]
    algorithm: Mapped[str] = mapped_column(nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    asof_date: Mapped[date] = mapped_column(nullable=False)
    window_days: Mapped[int] = mapped_column(nullable=False)
    n_clusters: Mapped[int | None]
    silhouette: Mapped[float | None] = mapped_column(Numeric(8, 4))
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="completed")
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    members: Mapped[list["ClusteringMember"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    explanations: Mapped[list["ClusterExplanation"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ClusteringMember(Base):
    """Maps each stock to its cluster label for a given run."""

    __tablename__ = "clustering_members"
    __table_args__ = (
        UniqueConstraint("run_id", "stock_id", name="uq_clustering_members"),
        Index("idx_clustering_members_run", "run_id"),
        Index("idx_clustering_members_stock", "stock_id"),
        Index("idx_clustering_members_label", "run_id", "cluster_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clustering_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    cluster_label: Mapped[int] = mapped_column(nullable=False)
    distance: Mapped[float | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped["ClusteringRun"] = relationship(back_populates="members")


class ClusterExplanation(Base):
    """LLM-generated natural-language explanation for one cluster."""

    __tablename__ = "cluster_explanations"
    __table_args__ = (
        UniqueConstraint("run_id", "cluster_label", name="uq_cluster_explanation"),
        Index("idx_cluster_explanations_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clustering_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    cluster_label: Mapped[int] = mapped_column(nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    input_summary: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None]
    disclaimer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped["ClusteringRun"] = relationship(back_populates="explanations")
