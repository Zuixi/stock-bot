"""Service for managing user-defined SW industry tags on stocks."""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sw_industry import StockCustomSwTag, SwIndustryClass

logger = logging.getLogger(__name__)


async def get_custom_sw_tags(db: AsyncSession, symbol: str) -> list[dict]:
    """Return all custom SW tags for *symbol* with resolved names."""
    rows = (
        await db.execute(
            select(
                StockCustomSwTag.industry_code,
                SwIndustryClass.industry_name,
                SwIndustryClass.level,
            )
            .join(
                SwIndustryClass,
                SwIndustryClass.industry_code == StockCustomSwTag.industry_code,
            )
            .where(StockCustomSwTag.symbol == symbol)
            .order_by(SwIndustryClass.level, SwIndustryClass.industry_code)
        )
    ).all()
    return [
        {"industryCode": r.industry_code, "industryName": r.industry_name, "level": r.level}
        for r in rows
    ]


async def set_custom_sw_tags(
    db: AsyncSession, symbol: str, industry_codes: list[str]
) -> list[dict]:
    """Replace all custom SW tags for *symbol*. Returns the new tag list."""
    if industry_codes:
        valid = (
            await db.execute(
                select(SwIndustryClass.industry_code).where(
                    SwIndustryClass.industry_code.in_(industry_codes),
                    SwIndustryClass.level.in_([2, 3]),
                )
            )
        ).scalars().all()
        valid_set = set(valid)
        invalid = [c for c in industry_codes if c not in valid_set]
        if invalid:
            raise ValueError(f"Invalid or non-L2/L3 industry codes: {invalid}")

    await db.execute(
        delete(StockCustomSwTag).where(StockCustomSwTag.symbol == symbol)
    )

    for code in dict.fromkeys(industry_codes):
        db.add(StockCustomSwTag(symbol=symbol, industry_code=code))
    await db.flush()

    return await get_custom_sw_tags(db, symbol)


async def list_sw_options(db: AsyncSession, level: int) -> list[dict]:
    """Return SW industry nodes at the given *level* (2 or 3) for dropdown."""
    rows = (
        await db.execute(
            select(
                SwIndustryClass.industry_code,
                SwIndustryClass.industry_name,
                SwIndustryClass.parent_code,
            )
            .where(SwIndustryClass.level == level)
            .order_by(SwIndustryClass.industry_code)
        )
    ).all()
    return [
        {"code": r.industry_code, "name": r.industry_name, "parentCode": r.parent_code}
        for r in rows
    ]
