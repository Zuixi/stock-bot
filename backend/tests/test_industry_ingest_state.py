# backend/tests/test_industry_ingest_state.py
"""industry_ingest_state 仓库层语义（mock AsyncSession，不触任何 DB）.

repo 三函数是薄 SQL 封装；真库行为由 Task 7 的 alembic + 真实 API 链路验证
（JSONB/PG 方言不进 sqlite，见 test_concept_agg_sql.py 先例的同类取舍）。
本文件锁接口形状与 touch 的最小语义：miss → None、upsert 走 get-then-set。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.models.industry_research import IndustryIngestState
from app.repositories import industry_metric_repo as repo


def _db_returning(result):
    db = MagicMock()
    execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=result)))
    db.execute = execute
    return db


async def test_get_missing_returns_none():
    db = _db_returning(None)
    assert await repo.get_ingest_state(db, "pig", "xuantian") is None


async def test_get_returns_state_row():
    row = IndustryIngestState(industry_key="pig", source="xuantian", content_hash="abc")
    db = _db_returning(row)
    assert (await repo.get_ingest_state(db, "pig", "xuantian")) is row


async def test_upsert_existing_sets_fields_and_flushes():
    row = IndustryIngestState(industry_key="pig", source="xuantian", content_hash="old")
    db = _db_returning(row)
    db.flush = AsyncMock()
    out = await repo.upsert_ingest_state(
        db, {"industry_key": "pig", "source": "xuantian", "content_hash": "new"}
    )
    assert out is row and row.content_hash == "new"
    db.flush.assert_awaited_once()


async def test_touch_only_sets_checked_at():
    row = IndustryIngestState(industry_key="pig", source="xuantian", content_hash="keep")
    db = _db_returning(row)
    db.flush = AsyncMock()
    from datetime import UTC, datetime

    await repo.touch_ingest_state(
        db, "pig", "xuantian", last_checked_at=datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
    )
    assert row.content_hash == "keep"  # 哈希水位不动
    assert row.last_checked_at is not None
