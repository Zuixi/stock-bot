"""cninfo 公告采集**只**写内容事实层（``content_items`` / ``content_entities``）。

钉两件事：

1. **单写路径**：``ingest_announcements_window`` 不再碰旧 ``announcements`` 表，
   幂等由 ``(source_name, raw_hash)`` 兜住——同一窗口跑两遍，第二次 0 行新增。
2. **只存链接**（owner 裁定）：``url`` 是唯一原文入口，不落正文/PDF，
   表里也**没有** ``body`` / ``body_ref`` 列。

库不可达时整文件跳过（与 ``test_content_repo.py`` 同口径），CI 无 PG 不该红。
用例自建自清：``finally`` 里按测试公告的 ``raw_hash`` 删内容行（子表靠
``ON DELETE CASCADE`` 清掉），失败路径也不留脏行。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest
from sqlalchemy import delete, select

from app.core.database import async_session_factory, engine
from app.models.content import ContentEntity, ContentItem
from app.services import announcement_service as anns

#: 测试公告：cninfo 真实 id 形态（幂等键来自它，清理也按它精确定位）。
ANNOUNCEMENT_ID = "1225434550"
SEC_CODE = "002956"
SEC_NAME = "西麦食品"
PDF_URL = f"http://static.cninfo.com.cn/finalpage/2026-10-30/{ANNOUNCEMENT_ID}.PDF"


def _pg_self_check() -> bool:
    """库是否可达（asyncpg 直连，不进 SQLAlchemy 事件循环）。"""

    async def _go() -> bool:
        url = engine.url.set(drivername="postgresql")
        conn = await asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database=url.database,
        )
        try:
            return True
        finally:
            await conn.close()

    try:
        return asyncio.run(_go())
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_self_check(),
    reason="PostgreSQL unavailable (content layer ingest needs alembic-upgraded DB)",
)

pytestmark = requires_pg

PUBLISHED = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> Any:
    """每个用例一个事件循环：不 dispose 会把上个循环的连接留给下个用例。"""
    yield
    await engine.dispose()


def _raw(announcement_id: str = ANNOUNCEMENT_ID, **overrides: Any) -> dict[str, Any]:
    """一条 ``CninfoClient.fetch_announcements`` 真实形状的行。"""
    row: dict[str, Any] = {
        "announcement_id": announcement_id,
        "sec_code": SEC_CODE,
        "sec_name": SEC_NAME,
        "title": f"{SEC_NAME}({SEC_CODE})2026年第三季度报告",
        "announce_time": PUBLISHED,
        "category": "report",
        "pdf_url": PDF_URL,
    }
    row.update(overrides)
    return row


class _TinyClient:
    """最小 cninfo 客户端：只实现 service 真正调用的那一个方法。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str]] = []

    async def fetch_announcements(self, se_date: str, category_key: str) -> list[dict[str, Any]]:
        self.calls.append((se_date, category_key))
        return list(self.rows)


async def _purge(db: Any, raw_hashes: tuple[str, ...]) -> None:
    """只删本用例写的内容行（子表 CASCADE 带走实体行）。"""
    await db.execute(
        delete(ContentItem).where(
            ContentItem.source_name == anns.SOURCE_NAME,
            ContentItem.raw_hash.in_(list(raw_hashes)),
        )
    )
    await db.commit()


async def test_ingest_writes_content_layer_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一窗口连跑两遍：第一次 1 行、第二次 0 行；表里只有内容事实层那一条。"""
    raw_hash = anns._raw_hash(ANNOUNCEMENT_ID)
    client = _TinyClient([_raw()])
    monkeypatch.setattr(anns, "get_announcement_client", lambda: client)
    # 本用例只验内容/实体入库；定期报告财务 join 由 test_content_financial_join 覆盖。
    monkeypatch.setattr(anns, "upsert_financial_metrics_for_content", AsyncMock(return_value=0))

    async with async_session_factory() as db:
        try:
            await _purge(db, (raw_hash,))
            window, cats = "2026-10-30~2026-10-30", ("report",)
            first = await anns.ingest_announcements_window(db, window, cats)
            await db.commit()
            second = await anns.ingest_announcements_window(db, window, cats)
            await db.commit()

            assert first == {"report": 1}
            assert second == {"report": 0}
            # 客户端按 (窗口, 类目) 被调用，两遍同参
            assert client.calls == [("2026-10-30~2026-10-30", "report")] * 2

            stored = (
                (
                    await db.execute(
                        select(ContentItem).where(
                            ContentItem.source_name == anns.SOURCE_NAME,
                            ContentItem.raw_hash == raw_hash,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(stored) == 1
            item = stored[0]
            assert item.content_type == "announcement"
            assert item.publisher_tier == "legal"
            assert item.publisher == SEC_NAME
            assert item.url == PDF_URL
            assert item.url.endswith(".PDF")
            assert item.published_at == PUBLISHED
            assert item.extracted == {
                "source_item_id": ANNOUNCEMENT_ID,
                "subtype": "report",
                "sec_code": SEC_CODE,
                "sec_name": SEC_NAME,
            }
            # 只存链接：表结构里根本没有正文入口
            columns = set(ContentItem.__table__.columns.keys())
            assert "body" not in columns
            assert "body_ref" not in columns

            entities = (
                await db.execute(
                    select(ContentEntity.entity_type, ContentEntity.entity_id).where(
                        ContentEntity.content_id == item.id
                    )
                )
            ).all()
            assert entities == [("stock", SEC_CODE)]
        finally:
            await _purge(db, (raw_hash,))


async def test_ingest_skips_rows_without_pdf_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 ``pdf_url`` = 没有原文入口 → 不入库（只存链接，无链接即不可溯源）。"""
    skipped_id = "1225434551"
    raw_hashes = (anns._raw_hash(ANNOUNCEMENT_ID), anns._raw_hash(skipped_id))
    client = _TinyClient([_raw(pdf_url=None, announcement_id=skipped_id)])
    monkeypatch.setattr(anns, "get_announcement_client", lambda: client)
    # 本用例只验内容/实体入库；定期报告财务 join 由 test_content_financial_join 覆盖。
    monkeypatch.setattr(anns, "upsert_financial_metrics_for_content", AsyncMock(return_value=0))

    async with async_session_factory() as db:
        try:
            await _purge(db, raw_hashes)
            result = await anns.ingest_announcements_window(
                db, "2026-10-30~2026-10-30", ("report",)
            )
            await db.commit()

            assert result == {"report": 0}
            count = (
                await db.execute(
                    select(ContentItem.id).where(ContentItem.raw_hash.in_(list(raw_hashes)))
                )
            ).all()
            assert count == []
        finally:
            await _purge(db, raw_hashes)
