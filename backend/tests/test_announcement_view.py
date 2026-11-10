"""公告兼容视图（``c1d2e3f4a5b6``）的真库不变量。

迁移把旧表 ``announcements`` 的存量搬进 ``content_items`` 后，用**同名只读视图**
顶替原表——``market_data_repo.list_announcements`` 与 ``journal_service`` 因此零改动。
本文件钉的是这条兼容契约的四条判据：

1. ``announcements`` 在真库里是视图（``pg_class.relkind = 'v'``），不是表；
2. 列名/列序仍是旧的 9 列（列序变了 ORM 的 ``SELECT`` 也不在乎，但位次漂移通常
   意味着有人按"表"重建过，是设计事故的早期信号）；
3. **写入必须被拒**：视图带 ``LEFT JOIN LATERAL`` 与表达式列，PG 不会自动更新它，
   于是 ``INSERT`` 抛 ``cannot insert into view``（SQLSTATE 55000）——这是"物理上堵住
   再往旧表写公告"的那道闸；
4. 视图能把 ``content_items`` + ``content_entities`` 投影回旧字段，且
   ``ORDER BY announce_time DESC LIMIT 1`` 这条读路径可用。

写入一律在**回滚**事务里（跑完库与跑前一致）；库不可达时 skip 而非红。
测试数据用 ``T`` 前缀的合成公告号与代码，避免与真实 cninfo 存量互相污染。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import engine

LEGACY_COLUMNS = [
    "id",
    "announcement_id",
    "sec_code",
    "sec_name",
    "title",
    "announce_time",
    "category",
    "pdf_url",
    "created_at",
]

# PG 对"不可自动更新的视图"报 SQLSTATE 55000（object_not_in_prerequisite_state），
# asyncpg 归到 ``ObjectNotInPrerequisiteStateError``，SQLAlchemy 落到共同基类
# ``DBAPIError``（``ProgrammingError`` / ``DataError`` 都是它的子类）。三个都收，
# 断言的是"任何 ORM/DBAPI 写路径都拿不到成功"，而不是某个具体异常类名。
WRITE_REJECTED = (ProgrammingError, DataError, DBAPIError)


def _database_url() -> str:
    """同一个库、同步可用的 URL（全局 engine 的连接池可能绑在别的事件循环上）。"""
    return engine.url.render_as_string(hide_password=False)


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
    reason="PostgreSQL unavailable (announcements view needs alembic-upgraded DB)",
)


async def _relkind_and_columns() -> tuple[str, list[str], str]:
    eng = create_async_engine(_database_url())
    try:
        async with eng.connect() as conn:
            relkind_result = await conn.execute(
                text(
                    "SELECT relkind::text FROM pg_class "
                    "WHERE oid = 'public.announcements'::regclass"
                )
            )
            relkind = relkind_result.scalar_one()
            columns_result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'announcements' "
                    "ORDER BY ordinal_position"
                )
            )
            columns = columns_result.scalars().all()
            table_type_result = await conn.execute(
                text(
                    "SELECT table_type FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'announcements'"
                )
            )
            table_type = table_type_result.scalar_one()
            return relkind, list(columns), table_type
    finally:
        await eng.dispose()


async def _try_insert() -> None:
    eng = create_async_engine(_database_url())
    try:
        async with eng.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO announcements "
                    "(announcement_id, sec_code, title, announce_time, category) "
                    "VALUES ('T-fake', 'T90000', 'write must be rejected', "
                    "CAST('2026-11-11 09:00:00+08' AS timestamptz), 'event')"
                )
            )
    finally:
        await eng.dispose()


async def _seed_and_read() -> dict[str, Any]:
    """插一条公告事实行 → 读视图 → 回滚。返回视图投影与倒序读面。"""
    ann_id = f"T{uuid.uuid4().hex[:16]}"
    raw_hash = hashlib.sha256(f"test:{ann_id}".encode()).hexdigest()
    extracted = {
        "source_item_id": ann_id,
        "subtype": "report",
        "sec_code": "T90001",
        "sec_name": "测试公司",
    }
    eng = create_async_engine(_database_url())
    try:
        async with eng.connect() as conn:
            trans = await conn.begin()
            try:
                content_id = (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO content_items (
                                content_type, source_name, publisher_tier, publisher,
                                title, published_at, url, raw_hash, extracted
                            ) VALUES (
                                'announcement', 'cninfo', 'legal', '测试公司',
                                '测试公告：只读视图回读',
                                CAST('2026-11-10 09:00:00+08' AS timestamptz),
                                'https://example.test/announcement.pdf', :raw_hash,
                                CAST(:extracted AS jsonb)
                            )
                            RETURNING id
                            """
                        ),
                        {
                            "raw_hash": raw_hash,
                            "extracted": json.dumps(extracted, ensure_ascii=False),
                        },
                    )
                ).scalar_one()
                await conn.execute(
                    text(
                        "INSERT INTO content_entities (content_id, entity_type, entity_id) "
                        "VALUES (:content_id, 'stock', :entity_id)"
                    ),
                    {"content_id": content_id, "entity_id": extracted["sec_code"]},
                )
                row_result = await conn.execute(
                    text(
                        "SELECT announcement_id, sec_code, sec_name, title, category, "
                        "pdf_url FROM announcements WHERE id = :content_id"
                    ),
                    {"content_id": content_id},
                )
                row = row_result.mappings().one()
                latest_result = await conn.execute(
                    text(
                        "SELECT announcement_id, announce_time FROM announcements "
                        "ORDER BY announce_time DESC LIMIT 1"
                    )
                )
                latest = latest_result.mappings().all()
                return {
                    "row": dict(row),
                    "latest": [dict(r) for r in latest],
                    "extracted": extracted,
                }
            finally:
                # Ruling 17：跑完库与跑前一致。
                await trans.rollback()
    finally:
        await eng.dispose()


@requires_pg
def test_announcements_is_a_view_not_a_table() -> None:
    relkind, _columns, table_type = asyncio.run(_relkind_and_columns())
    assert relkind == "v", f"announcements should be a view, relkind={relkind!r}"
    assert table_type == "VIEW"


@requires_pg
def test_view_keeps_legacy_column_names() -> None:
    _relkind, columns, _table_type = asyncio.run(_relkind_and_columns())
    assert columns == LEGACY_COLUMNS


@requires_pg
def test_insert_into_view_is_rejected() -> None:
    with pytest.raises(WRITE_REJECTED) as excinfo:
        asyncio.run(_try_insert())

    err = excinfo.value
    message = str(err).lower()
    assert "cannot insert into view" in message
    # 55000 = object_not_in_prerequisite_state（"视图不可自动更新"的 PG 口径）。
    assert getattr(getattr(err, "orig", None), "sqlstate", None) == "55000"


@requires_pg
def test_view_projects_content_items_announcement() -> None:
    result = asyncio.run(_seed_and_read())
    row = result["row"]
    extracted = result["extracted"]

    assert row["announcement_id"] == extracted["source_item_id"]
    assert row["sec_code"] == extracted["sec_code"]
    assert row["sec_name"] == extracted["sec_name"]
    assert row["title"] == "测试公告：只读视图回读"
    assert row["category"] == "report"
    assert row["pdf_url"] == "https://example.test/announcement.pdf"


@requires_pg
def test_view_supports_order_by_announce_time_desc_limit_1() -> None:
    result = asyncio.run(_seed_and_read())
    latest = result["latest"]
    assert len(latest) == 1
    assert latest[0]["announce_time"] is not None
