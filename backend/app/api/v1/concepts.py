"""概念板块读接口：`/api/v1/concepts`（T6 列表；详情/成分/次新股见 plans §2.2 后续任务）。

只读 + `CacheDep`（service 内 Redis 300s，仅非空结果写入），无请求参数落 SQL：`sort` 只有
`pct` / `inflow` 两个白名单值，排序在 service 层完成。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbDep
from app.schemas.concept import ConceptListOut
from app.services import concept_service

router = APIRouter(tags=["concepts"])


@router.get("", response_model=ConceptListOut)
async def list_concepts(
    db: DbDep,
    cache: CacheDep,
    sort: Literal["pct", "inflow"] = Query(
        default="pct",
        description=(
            "pct = 全库 avg_pct 降序（NULLS LAST）；inflow = 只在**当前页切片内**按主力净流入"
            "重排（缺快照行排最后），不是全库流入 Top-N。"
        ),
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ConceptListOut:
    """概念板块列表：本地成分聚合 + 东财资金流快照内存 join（口径/降级见 plans §2.2/§2.3）。"""
    body = await concept_service.list_boards(db, cache, sort, limit, offset)
    return ConceptListOut(**body)
