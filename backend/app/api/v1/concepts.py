"""概念板块读接口：`/api/v1/concepts`（T6 列表；T8 详情 + 板内梯队）。

只读 + `CacheDep`（service 内 Redis 300s，仅非空结果写入），无请求参数落 SQL：`sort` 只有
`pct` / `inflow` 两个白名单值，排序在 service 层完成。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.concept import ConceptDetailOut, ConceptListOut
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


@router.get("/{board_code}", response_model=ConceptDetailOut)
async def get_board_detail(board_code: str, db: DbDep, cache: CacheDep) -> ConceptDetailOut:
    """板块详情：单板 BoardItem + KPI + 板内连板梯队（与 `/market/limit-up` 快照同源）。"""
    body = await concept_service.get_board_detail(db, board_code, cache)
    if body is None:
        raise not_found_response("concept board", board_code)
    return ConceptDetailOut(**body)
