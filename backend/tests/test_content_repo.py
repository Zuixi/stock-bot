"""内容事实层 repo 的真库不变量（S3.1，``app/repositories/content_repo.py``）。

钉的是**幂等语义**——这批函数唯一的失败模式就是"重放变成重复入库"：

- ``content_items``：同 ``(source_name, raw_hash)`` 重放 → 0 行新增。
- ``content_entities``：同实体重复抽取 → 0 行新增。
- ``content_metrics``：同键重抽只更新值，不插重复行（``period IS NULL`` 的
  非期间型指标走部分唯一索引那条路径，单独钉一次）。
- ``user_content_state``：复合主键下两个用户互不干扰。
- ``list_content_items``：``extracted->>'subtype'`` 过滤生效。

库不可达时整文件跳过（``requires_pg``），CI 无 PG 不该红。用例自建自清：
``source_name`` 用固定测试前缀，``finally`` 里先删用户状态再删内容行（子表靠
``ON DELETE CASCADE`` 清掉），失败路径也不留脏行。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from sqlalchemy import delete, func, select

from app.core.database import async_session_factory, engine
from app.models.content import ContentEntity, ContentItem, ContentMetric, UserContentState
from app.repositories import content_repo

#: 测试数据前缀：清理与断言都只认它，绝不碰真实内容行。
TEST_SOURCE = "tcrepo"


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
    reason="PostgreSQL unavailable (content repo invariants need alembic-upgraded DB)",
)

pytestmark = requires_pg

PUBLISHED = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> Any:
    """每个用例一个事件循环：不 dispose 会把上个循环的连接留给下个用例。"""
    yield
    await engine.dispose()


def _item_row(raw_hash: str, **overrides: Any) -> dict[str, Any]:
    """一条最小合法内容行（必填列齐 + 测试前缀）。"""
    row: dict[str, Any] = {
        "content_type": "report",
        "source_name": TEST_SOURCE,
        "publisher_tier": "sellside",
        "title": f"test content {raw_hash}",
        "published_at": PUBLISHED,
        "url": f"https://example.invalid/{raw_hash}",
        "raw_hash": raw_hash,
        "extracted": {},
    }
    row.update(overrides)
    return row


async def _purge(db: Any, user_ids: tuple[UUID, ...] = ()) -> None:
    """先删用户状态（若有），再删测试内容行（子表 CASCADE）。"""
    if user_ids:
        await db.execute(delete(UserContentState).where(UserContentState.user_id.in_(user_ids)))
    await db.execute(delete(ContentItem).where(ContentItem.source_name == TEST_SOURCE))
    await db.commit()


async def test_upsert_content_items_is_idempotent_on_source_and_hash() -> None:
    """同 ``(source_name, raw_hash)`` 重放 → 第二次 0 行，表里只有 1 行。"""
    raw_hash = uuid4().hex
    async with async_session_factory() as db:
        try:
            await _purge(db)
            assert await content_repo.upsert_content_items(db, [_item_row(raw_hash)]) == 1
            assert await content_repo.upsert_content_items(db, [_item_row(raw_hash)]) == 0
            await db.commit()

            count = (
                await db.execute(
                    select(func.count())
                    .select_from(ContentItem)
                    .where(ContentItem.source_name == TEST_SOURCE)
                )
            ).scalar_one()
            assert count == 1
        finally:
            await _purge(db)


async def test_get_content_ids_by_hashes_resolves_id() -> None:
    """哈希 → id 一次解析；缺失的哈希不出现在结果里。"""
    raw_hash = uuid4().hex
    async with async_session_factory() as db:
        try:
            await _purge(db)
            await content_repo.upsert_content_items(db, [_item_row(raw_hash)])
            ids = await content_repo.get_content_ids_by_hashes(
                db, [raw_hash, "missing-" + uuid4().hex]
            )
            await db.commit()

            assert set(ids) == {raw_hash}
            assert ids[raw_hash] > 0
            stored = (
                await db.execute(select(ContentItem.id).where(ContentItem.raw_hash == raw_hash))
            ).scalar_one()
            assert ids[raw_hash] == stored
        finally:
            await _purge(db)


async def test_get_content_ids_by_hashes_scopes_source() -> None:
    """同一 raw_hash 存在于两个来源时，传 source_name 必须返回该来源的 id。

    唯一键是 ``(source_name, raw_hash)``；只按哈希查会把两个来源压成一条，
    调用方（例如 cninfo 公告回填）必须能锁定自己的来源。
    """
    raw_hash = uuid4().hex
    other_source = TEST_SOURCE + "-other"
    async with async_session_factory() as db:
        try:
            await db.execute(
                delete(ContentItem).where(ContentItem.source_name.in_([TEST_SOURCE, other_source]))
            )
            await content_repo.upsert_content_items(
                db,
                [
                    _item_row(raw_hash),
                    _item_row(raw_hash, source_name=other_source),
                ],
            )
            db.commit()

            first = await content_repo.get_content_ids_by_hashes(
                db, [raw_hash], source_name=TEST_SOURCE
            )
            second = await content_repo.get_content_ids_by_hashes(
                db, [raw_hash], source_name=other_source
            )
            assert first[raw_hash] != second[raw_hash]
        finally:
            await db.execute(
                delete(ContentItem).where(ContentItem.source_name.in_([TEST_SOURCE, other_source]))
            )
            await db.commit()


async def test_insert_content_entities_is_idempotent() -> None:
    """同一 (content_id, entity_type, entity_id) 插两次 → 第二次 0 行。"""
    raw_hash = uuid4().hex
    async with async_session_factory() as db:
        try:
            await _purge(db)
            await content_repo.upsert_content_items(db, [_item_row(raw_hash)])
            content_id = (await content_repo.get_content_ids_by_hashes(db, [raw_hash]))[raw_hash]
            entities = [("industry", "sw:生猪养殖"), ("stock", "002714")]

            assert await content_repo.insert_content_entities(db, content_id, entities) == 2
            assert await content_repo.insert_content_entities(db, content_id, entities) == 0
            await db.commit()

            count = (
                await db.execute(
                    select(func.count())
                    .select_from(ContentEntity)
                    .where(ContentEntity.content_id == content_id)
                )
            ).scalar_one()
            assert count == 2
        finally:
            await _purge(db)


async def test_upsert_content_metrics_updates_in_place() -> None:
    """同键重抽只刷新值，不插重复行（期间型走唯一约束）。"""
    raw_hash = uuid4().hex
    async with async_session_factory() as db:
        try:
            await _purge(db)
            await content_repo.upsert_content_items(db, [_item_row(raw_hash)])
            content_id = (await content_repo.get_content_ids_by_hashes(db, [raw_hash]))[raw_hash]

            def metric(value: float) -> dict[str, Any]:
                return {
                    "content_id": content_id,
                    "entity_type": "industry",
                    "entity_id": "sw:生猪养殖",
                    "metric_key": "net_profit",
                    "period": "2026H1",
                    "value": value,
                    "unit": "亿元",
                    "source_span": {"kind": "pdf", "page": 3},
                }

            assert await content_repo.upsert_content_metrics(db, [metric(1.5)]) == 1
            assert await content_repo.upsert_content_metrics(db, [metric(2.5)]) == 1
            await db.commit()

            rows = (
                await db.execute(
                    select(ContentMetric.value, ContentMetric.unit).where(
                        ContentMetric.content_id == content_id
                    )
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].value == Decimal("2.5000")
            assert rows[0].unit == "亿元"
        finally:
            await _purge(db)


async def test_upsert_content_metrics_null_period_updates_in_place() -> None:
    """``period IS NULL`` 的非期间型指标（如 core_view）重抽也不得插重复行。"""
    raw_hash = uuid4().hex
    async with async_session_factory() as db:
        try:
            await _purge(db)
            await content_repo.upsert_content_items(db, [_item_row(raw_hash)])
            content_id = (await content_repo.get_content_ids_by_hashes(db, [raw_hash]))[raw_hash]

            def metric(text: str) -> dict[str, Any]:
                return {
                    "content_id": content_id,
                    "entity_type": "industry",
                    "entity_id": "sw:生猪养殖",
                    "metric_key": "core_view",
                    "period": None,
                    "value_text": text,
                    "source_span": {"kind": "structured"},
                }

            await content_repo.upsert_content_metrics(db, [metric("旧观点")])
            await content_repo.upsert_content_metrics(db, [metric("新观点")])
            await db.commit()

            rows = (
                (
                    await db.execute(
                        select(ContentMetric).where(ContentMetric.content_id == content_id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].value_text == "新观点"
        finally:
            await _purge(db)


async def test_user_state_upsert_is_per_user() -> None:
    """两个 user_id 的状态互相独立；同一用户重复 upsert 覆盖。"""
    raw_hash = uuid4().hex
    alice, bob = uuid4(), uuid4()
    async with async_session_factory() as db:
        try:
            await _purge(db, (alice, bob))
            await content_repo.upsert_content_items(db, [_item_row(raw_hash)])
            content_id = (await content_repo.get_content_ids_by_hashes(db, [raw_hash]))[raw_hash]

            await content_repo.upsert_user_state(db, alice, content_id, "read", "已读")
            await content_repo.upsert_user_state(db, bob, content_id, "archived")
            await content_repo.upsert_user_state(db, alice, content_id, "processed", "已入池")
            await db.commit()

            alice_state = await content_repo.get_user_state(db, alice, content_id)
            bob_state = await content_repo.get_user_state(db, bob, content_id)
            assert alice_state is not None and bob_state is not None
            assert (alice_state.status, alice_state.note) == ("processed", "已入池")
            assert (bob_state.status, bob_state.note) == ("archived", None)

            count = (
                await db.execute(
                    select(func.count())
                    .select_from(UserContentState)
                    .where(UserContentState.content_id == content_id)
                )
            ).scalar_one()
            assert count == 2
        finally:
            await _purge(db, (alice, bob))


async def test_list_content_items_filters_by_subtype() -> None:
    """``extracted->>'subtype'`` 过滤生效，且按 published_at/id 倒序。"""
    hog, feed = uuid4().hex, uuid4().hex
    async with async_session_factory() as db:
        try:
            await _purge(db)
            await content_repo.upsert_content_items(
                db,
                [
                    _item_row(
                        hog,
                        extracted={"subtype": "hog_farming"},
                        published_at=PUBLISHED,
                    ),
                    _item_row(
                        feed,
                        extracted={"subtype": "feed"},
                        published_at=PUBLISHED,
                        title="feed content",
                    ),
                ],
            )
            await db.commit()

            all_items = await content_repo.list_content_items(db, content_type="report")
            assert {item.raw_hash for item in all_items} == {hog, feed}

            only_hog = await content_repo.list_content_items(
                db, content_type="report", subtype="hog_farming"
            )
            assert [item.raw_hash for item in only_hog] == [hog]
        finally:
            await _purge(db)
