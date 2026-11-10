"""内容事实层四表的读写 repo（S3.1）。

口径见 ``app/models/content.py`` 与 ``docs/specs/S3-content.md`` §S3.1：

- 入库一律 **幂等**：``content_items`` 靠 ``uq_content_items_source_hash``，
  ``content_entities`` 靠复合主键，``content_metrics`` 靠 ``uq_content_metrics_key``
  （``period IS NULL`` 的非期间型指标另由部分唯一索引兜住），用户状态靠复合主键。
- 事实层（``content_items``）**不写任何用户状态**：个人状态只落
  ``user_content_state``。
- 删除/更新一律走冲突键，不做"先查再插"（并发下必错）。

空入参一律 no-op 返回 0/{}：``pg_insert(...).values([])`` 会直接抛错，而
"本轮没有新内容"是完全正常的稳态。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import (
    ContentEntity,
    ContentItem,
    ContentMetric,
    UserContentState,
)


async def upsert_content_items(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """内容事实层幂等写入，返回**实际新增**行数（重放同一原文 → 0）。

    冲突键是 ``(source_name, raw_hash)``：同一来源的同一正文哈希只入库一次。冲突时
    DO NOTHING 而非 DO UPDATE——已入库内容的 ``ingested_at``/抽取结果属于历史事实，
    重放不得覆盖（抽取版本升级要走显式重抽流程，不是 upsert 的副作用）。
    """
    if not rows:
        return 0
    stmt = (
        pg_insert(ContentItem)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_content_items_source_hash")
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def get_content_ids_by_hashes(
    db: AsyncSession, hashes: Sequence[str], source_name: str | None = None
) -> dict[str, int]:
    """``raw_hash`` → ``content_items.id`` 一次查询解析（用于回填子表 FK）。

    唯一键是 ``(source_name, raw_hash)``，同一哈希可存在于不同来源；调用方知道来源时
    必须传 ``source_name``，否则 dict 会按哈希把两个来源的行压成一条。不传则保持旧的
    "全来源"行为（仅限哈希全局唯一的调用方）。
    哈希不存在就**不在返回的 dict 里**（调用方据此跳过该条），不抛错。
    """
    if not hashes:
        return {}
    stmt = select(ContentItem.raw_hash, ContentItem.id).where(
        ContentItem.raw_hash.in_(list(hashes))
    )
    if source_name is not None:
        stmt = stmt.where(ContentItem.source_name == source_name)
    rows = (await db.execute(stmt)).all()
    return {raw_hash: int(content_id) for raw_hash, content_id in rows}


async def insert_content_entities(
    db: AsyncSession, content_id: int, entities: Sequence[tuple[str, str]]
) -> int:
    """内容 ↔ 实体关联幂等写入（``(entity_type, entity_id)`` 元组），返回新增行数。

    复合主键 ``(content_id, entity_type, entity_id)`` 即去重键：重复抽取同一实体
    → 0 行新增，不报错。
    """
    if not entities:
        return 0
    values = [
        {"content_id": content_id, "entity_type": entity_type, "entity_id": entity_id}
        for entity_type, entity_id in entities
    ]
    stmt = pg_insert(ContentEntity).values(values).on_conflict_do_nothing()
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def upsert_content_metrics(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """抽取指标幂等写入/覆盖，返回受影响行数。

    冲突时刷新 ``value`` / ``value_text`` / ``unit`` / ``source_span``——重复抽取是
    "同一内容的新一轮解析"，以最新解析为准（``created_at`` 保留首次入库时刻）。

    ``period IS NULL``（非期间型指标，如 ``core_view``）在 Postgres 里与唯一约束
    ``uq_content_metrics_key`` **不冲突**（NULL 互不相等），只由部分唯一索引
    ``uq_content_metrics_key_nullperiod`` 兜住；因此按 period 是否为空分两条语句，
    各用各的冲突目标，否则重抽会插重复行或被未处理的唯一冲突打挂。
    """
    if not rows:
        return 0
    set_ = {
        "value": pg_insert(ContentMetric).excluded.value,
        "value_text": pg_insert(ContentMetric).excluded.value_text,
        "unit": pg_insert(ContentMetric).excluded.unit,
        "source_span": pg_insert(ContentMetric).excluded.source_span,
    }
    null_period = [r for r in rows if r.get("period") is None]
    dated = [r for r in rows if r.get("period") is not None]

    affected = 0
    if dated:
        stmt = (
            pg_insert(ContentMetric)
            .values(dated)
            .on_conflict_do_update(constraint="uq_content_metrics_key", set_=set_)
        )
        result = cast("CursorResult[Any]", await db.execute(stmt))
        affected += int(result.rowcount)
    if null_period:
        stmt = (
            pg_insert(ContentMetric)
            .values(null_period)
            .on_conflict_do_update(
                index_elements=["content_id", "entity_id", "metric_key"],
                index_where=ContentMetric.period.is_(None),
                set_=set_,
            )
        )
        result = cast("CursorResult[Any]", await db.execute(stmt))
        affected += int(result.rowcount)
    await db.flush()
    return affected


async def get_user_state(
    db: AsyncSession, user_id: UUID, content_id: int
) -> UserContentState | None:
    """单个用户 × 单条内容的状态；没有记录返回 ``None``（调用方自行决定默认值）。"""
    return (
        await db.execute(
            select(UserContentState).where(
                UserContentState.user_id == user_id,
                UserContentState.content_id == content_id,
            )
        )
    ).scalar_one_or_none()


async def upsert_user_state(
    db: AsyncSession,
    user_id: UUID,
    content_id: int,
    status: str,
    note: str | None = None,
) -> None:
    """用户状态幂等写入（复合主键 ``(user_id, content_id)``）。"""
    stmt = (
        pg_insert(UserContentState)
        .values(
            user_id=user_id,
            content_id=content_id,
            status=status,
            note=note,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "content_id"],
            set_={"status": status, "note": note, "updated_at": func.now()},
        )
    )
    await db.execute(stmt)
    await db.flush()


async def list_content_items(
    db: AsyncSession,
    *,
    content_type: str | None = None,
    subtype: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ContentItem]:
    """按类型/子类型分页列出内容，最新发布优先（``published_at desc, id desc``）。

    ``subtype`` 存在 ``extracted`` JSONB 里（``extracted->>'subtype'``），走 astext
    比较；该字段没有独立索引，因此只在已按类型过滤的读面上使用。
    """
    stmt = select(ContentItem)
    if content_type is not None:
        stmt = stmt.where(ContentItem.content_type == content_type)
    if subtype is not None:
        stmt = stmt.where(ContentItem.extracted["subtype"].astext == subtype)
    stmt = stmt.order_by(ContentItem.published_at.desc(), ContentItem.id.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all())
