"""Security audit event service."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuthAuditEvent


async def record_audit_event(
    db: AsyncSession,
    event_type: str,
    status: str,
    user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    trace_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuthAuditEvent:
    """Record an immutable security audit event to the database."""
    audit_entry = AuthAuditEvent(
        event_id=uuid.uuid4(),
        user_id=user_id,
        event_type=event_type,
        status=status,
        ip_address=ip_address,
        user_agent=user_agent,
        trace_id=trace_id,
        payload=payload,
    )
    db.add(audit_entry)
    await db.commit()
    return audit_entry
