"""Ingestion ledger + data-quality models (P5).

三张表的分工（见 ``plans/2026-11-11-data-platform-hardening-p5-p7.md``）：

- ``ingest_batches``：一次采集的**终态**。它同时充当"分区封存"记录：
  ``status='accepted'`` 且 DB 行数对得上，才允许下一轮跳过外呼。
- ``ingest_quarantine``：被校验拒绝的行**原样留档** + 原因码。绝不静默丢弃。
- ``data_quality_runs``：声明式期望（SQL 断言）的执行历史。

刻意不做的事：不引入 dbt / Great Expectations；三张表都只是账本，判据仍在
``services/data_expectations.py`` 与 ``services/ingest_pipeline.py`` 里（代码即配置）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IngestBatch(Base):
    """一次 (domain, business_date, source, capture_kind) 采集的终态账本."""

    __tablename__ = "ingest_batches"
    __table_args__ = (
        UniqueConstraint(
            "domain",
            "business_date",
            "source",
            "capture_kind",
            "params_hash",
            name="uq_ingest_batches_partition",
        ),
        Index("ix_ingest_batches_domain_date", "domain", "business_date"),
        Index("ix_ingest_batches_status", "status", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(48), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    capture_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="eod_full")
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    rows_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_quarantined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pending | accepted | partial | quarantined | replayed | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class IngestQuarantine(Base):
    """被校验拒绝的单行（原样 JSON），带机器可聚合的 ``reason_code``."""

    __tablename__ = "ingest_quarantine"
    __table_args__ = (
        Index("ix_ingest_quarantine_domain_date", "domain", "business_date"),
        Index("ix_ingest_quarantine_reason", "reason_code", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingest_batches.id", ondelete="CASCADE")
    )
    domain: Mapped[str] = mapped_column(String(48), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    row_key: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DataQualityRun(Base):
    """一条期望断言在某次运行中的结果（``pass|warn|fail|error``）."""

    __tablename__ = "data_quality_runs"
    __table_args__ = (
        Index("ix_data_quality_runs_key_checked", "expectation_key", "checked_at"),
        Index("ix_data_quality_runs_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    expectation_key: Mapped[str] = mapped_column(String(96), nullable=False)
    domain: Mapped[str] = mapped_column(String(48), nullable=False)
    business_date: Mapped[date | None] = mapped_column(Date)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    observed: Mapped[float | None] = mapped_column(Numeric(20, 4))
    threshold: Mapped[float | None] = mapped_column(Numeric(20, 4))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
