"""Async task state ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

TASK_STATUS_CHECK = CheckConstraint(
    "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
    name="chk_task_status",
)


class Task(Base):
    """Tracks the state of an async background job."""

    __tablename__ = "tasks"
    __table_args__ = (
        TASK_STATUS_CHECK,
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_type", "type"),
        Index("idx_tasks_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    type: Mapped[str] = mapped_column(nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(default=0)
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
