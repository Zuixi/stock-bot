"""Task repository: create and update async task state."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task


async def create_task(db: AsyncSession, task_type: str, payload: dict | None) -> Task:
    task = Task(type=task_type, payload=payload, status="pending")
    db.add(task)
    await db.flush()
    return task


async def get_task(db: AsyncSession, task_id: uuid.UUID) -> Task | None:
    return await db.get(Task, task_id)


async def update_task_status(
    db: AsyncSession,
    task_id: uuid.UUID,
    status: str,
    progress: int | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> Task | None:
    task = await db.get(Task, task_id)
    if task is None:
        return None

    task.status = status
    if progress is not None:
        task.progress = progress
    if result is not None:
        task.result = result
    if error is not None:
        task.error = error
    if status == "running" and task.started_at is None:
        task.started_at = datetime.now(UTC)
    if status in ("completed", "failed", "cancelled"):
        task.finished_at = datetime.now(UTC)

    await db.flush()
    return task


async def list_tasks(
    db: AsyncSession,
    task_type: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[Task]:
    stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
    if task_type:
        stmt = stmt.where(Task.type == task_type)
    if status:
        stmt = stmt.where(Task.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())
