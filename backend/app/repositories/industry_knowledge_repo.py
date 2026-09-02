"""Industry knowledge repository: content rows (org map / principles / mindmap)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustryKnowledge


async def list_knowledge(db: AsyncSession, industry_key: str) -> list[IndustryKnowledge]:
    """All knowledge rows of one industry, ordered (kind, sort, id) — content-managed."""
    stmt = (
        select(IndustryKnowledge)
        .where(IndustryKnowledge.industry_key == industry_key)
        .order_by(
            IndustryKnowledge.kind, IndustryKnowledge.sort, IndustryKnowledge.id
        )
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
