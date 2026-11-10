"""内容读面（S3.x）：``/api/v1/content`` 列表 / 详情 / 用户状态。

三条鉴权口径：

- **``GET /content`` 公开**（与 ``/news`` 一致）：内容事实层是公共信息，匿名也能读；
- **``status`` 过滤要登录**：它过滤的是"**我**的状态"，匿名带上它只能是问错人 →
  **401**（不是静默忽略、也不是空列表）；
- **``PATCH /content/{id}/state`` 要登录**：写的是用户自己的状态。

两条路由纪律：

1. 静态路径声明在 `/{content_id}` 之前（这里没有静态路径，但保持习惯）；
2. ``entity_type`` / ``entity_id`` **必须成对**：只给一个 → **422**。单独按
   ``entity_type`` 查（"所有股票相关内容"）会扫全表，单独按 ``entity_id`` 查（"所
   有 600000 相关内容"）会跨实体类型误配（``stock:600000`` vs ``concept:600000``），
   两种都不是读面需要的查询。

``metrics`` 在列表与详情都下发：S3.4 要求列表展示定期报告的核心信息（收入/利润）。
故列表按整页批量查询，不做 N+1；无指标的内容给空列表，前端渲染 `—`。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, OptionalUserDep
from app.models.content import ContentEntity, ContentItem, ContentMetric, UserContentState
from app.repositories import content_repo
from app.schemas.content import (
    ContentEntityOut,
    ContentItemOut,
    ContentMetricOut,
    ContentStateIn,
)

router = APIRouter()

#: ``status`` 过滤的取值 = 库 CHECK 约束全集（含``new``：用户可能显式筛"未读"）。
StatusFilter = Literal["new", "read", "processed", "ignored", "archived"]


def _item_out(
    item: ContentItem,
    *,
    user_state: str | None,
    entities: list[ContentEntityOut],
    metrics: list[ContentMetricOut] | None = None,
) -> ContentItemOut:
    return ContentItemOut(
        id=int(item.id),
        content_type=item.content_type,
        source_name=item.source_name,
        publisher_tier=item.publisher_tier,
        publisher=item.publisher,
        title=item.title,
        published_at=item.published_at,
        ingested_at=item.ingested_at,
        url=item.url,
        extracted=item.extracted or {},
        review_status=item.review_status,
        user_state=user_state,
        entities=entities,
        metrics=metrics or [],
    )


async def _entity_map(
    db: AsyncSession, content_ids: list[int]
) -> dict[int, list[ContentEntityOut]]:
    """一次查询取回整页的实体关联（避免 N+1）。"""
    if not content_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(ContentEntity)
                .where(ContentEntity.content_id.in_(content_ids))
                .order_by(ContentEntity.entity_type, ContentEntity.entity_id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[int, list[ContentEntityOut]] = defaultdict(list)
    for row in rows:
        out[int(row.content_id)].append(
            ContentEntityOut(entity_type=row.entity_type, entity_id=row.entity_id)
        )
    return out


def _metric_out(row: ContentMetric) -> ContentMetricOut:
    return ContentMetricOut(
        metric_key=row.metric_key,
        period=row.period,
        value=None if row.value is None else float(row.value),
        value_text=row.value_text,
        unit=row.unit,
        source_span=row.source_span or {},
    )


async def _metric_map(
    db: AsyncSession, content_ids: list[int]
) -> dict[int, list[ContentMetricOut]]:
    """一次查询取回整页的抽取指标（避免 N+1）。

    列表必须带指标：S3.4 的列表契约就是「定期报告仅显示核心信息（收入/利润）」。
    对没有指标的内容返回空列表，前端渲染 `—`，**不补 0**。
    """
    if not content_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(ContentMetric)
                .where(ContentMetric.content_id.in_(content_ids))
                .order_by(
                    ContentMetric.content_id,
                    ContentMetric.metric_key,
                    ContentMetric.period,
                    ContentMetric.id,
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[int, list[ContentMetricOut]] = defaultdict(list)
    for row in rows:
        out[int(row.content_id)].append(_metric_out(row))
    return out


async def _metric_list(db: AsyncSession, content_id: int) -> list[ContentMetricOut]:
    return (await _metric_map(db, [content_id])).get(content_id, [])


def _user_id(user: Any) -> UUID | None:
    """``Principal.user_id``（str）→ UUID；匿名 → ``None``。"""
    raw = getattr(user, "user_id", None)
    if not raw or not getattr(user, "is_authenticated", False):
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


@router.get("", response_model=list[ContentItemOut])
async def list_content(
    db: DbDep,
    user: OptionalUserDep,
    content_type: Annotated[str | None, Query(max_length=24)] = None,
    subtype: Annotated[str | None, Query(max_length=64)] = None,
    entity_type: Annotated[str | None, Query(max_length=24)] = None,
    entity_id: Annotated[str | None, Query(max_length=64)] = None,
    content_status: Annotated[StatusFilter | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ContentItemOut]:
    """按类型/子类型/实体/我的状态分页列内容（``published_at desc, id desc``）。

    返回**裸数组**（不套 ``{items: …}``）：前端直接把它当列表用，加了信封会多一层
    ``.items`` 的隐式约定，而这里没有分页元数据要下发。
    """
    if (entity_type is None) != (entity_id is None):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="entity_type 与 entity_id 必须成对提供（只给一个无法定位实体）",
        )

    user_uuid = _user_id(user)
    if content_status is not None and user_uuid is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="按 status 过滤需要登录（status 是当前用户自己的状态）",
        )

    # 用户状态左侧外连接：一条语句取回"内容 + 我的状态"，避免 N+1。
    stmt = select(ContentItem, UserContentState.status).outerjoin(
        UserContentState,
        (UserContentState.content_id == ContentItem.id) & (UserContentState.user_id == user_uuid),
    )
    if content_type is not None:
        stmt = stmt.where(ContentItem.content_type == content_type)
    if subtype is not None:
        stmt = stmt.where(ContentItem.extracted["subtype"].astext == subtype)
    if entity_type is not None and entity_id is not None:
        stmt = stmt.where(
            ContentItem.id.in_(
                select(ContentEntity.content_id).where(
                    ContentEntity.entity_type == entity_type,
                    ContentEntity.entity_id == entity_id,
                )
            )
        )
    if content_status is not None:
        stmt = stmt.where(UserContentState.status == content_status)

    stmt = (
        stmt.order_by(ContentItem.published_at.desc(), ContentItem.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    items = [item for item, _ in rows]
    ids = [int(item.id) for item in items]
    entities = await _entity_map(db, ids)
    metrics = await _metric_map(db, ids)
    return [
        _item_out(
            item,
            user_state=state,
            entities=entities.get(int(item.id), []),
            metrics=metrics.get(int(item.id), []),
        )
        for item, state in rows
    ]


@router.get("/{content_id}", response_model=ContentItemOut)
async def get_content(content_id: int, db: DbDep, user: OptionalUserDep) -> ContentItemOut:
    """单条内容（含实体与抽取指标）。不存在 → 404。"""
    item = (
        await db.execute(select(ContentItem).where(ContentItem.id == content_id))
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=f"内容不存在：{content_id}"
        )

    user_uuid = _user_id(user)
    state: str | None = None
    if user_uuid is not None:
        row = await content_repo.get_user_state(db, user_uuid, content_id)
        state = None if row is None else row.status

    entities = (await _entity_map(db, [content_id])).get(content_id, [])
    return _item_out(
        item,
        user_state=state,
        entities=entities,
        metrics=await _metric_list(db, content_id),
    )


@router.patch("/{content_id}/state")
async def patch_content_state(
    content_id: int, payload: ContentStateIn, db: DbDep, user: OptionalUserDep
) -> dict[str, str]:
    """写当前用户的内容状态（幂等 upsert）。

    用 ``OptionalUserDep`` + 显式判空而不是 ``CurrentUserDep``：两者的 401 都是 401，
    但这样 `status` 走的是 ``Principal.user_id`` 的 **UUID 解析**（脏 ``sub`` → 401 而
    不是 500 的 asyncpg 类型错误）。
    """
    user_uuid = _user_id(user)
    if user_uuid is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED, detail="标记内容状态需要登录"
        )

    exists = (
        await db.execute(select(ContentItem.id).where(ContentItem.id == content_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail=f"内容不存在：{content_id}"
        )

    # 不在这里 commit：事务边界归 ``get_db``（写只 flush，异常整体回滚）。
    await content_repo.upsert_user_state(db, user_uuid, content_id, payload.status, payload.note)
    return {"status": payload.status}
