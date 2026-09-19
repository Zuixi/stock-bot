"""概念板块读接口：`/api/v1/concepts`（T6 列表；T8 详情 + 板内梯队；T9 成分/个股反查）。

只读 + `CacheDep`（service 内 Redis 300s，仅非空结果写入），无请求参数落 SQL：`sort` 只有
`pct` / `inflow` 两个白名单值，排序在 service 层完成。

**路由顺序**：`/by-symbol/{symbol}` 是字面量前缀，必须先于 `/{board_code}` 声明。当前两条
路由段数不同（`by-symbol/x` 2 段 vs `/{board_code}` 1 段），FastAPI 尚不会误匹配；但注册顺序
是唯一护栏，把字面量路由固定在最前，将来加 `/{board_code}/xxx` 时也不会被通配吞掉。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.concept import ConceptBySymbolOut, ConceptDetailOut, ConceptListOut
from app.schemas.stock import StockEnrichedOut
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


@router.get("/by-symbol/{symbol}", response_model=ConceptBySymbolOut)
async def list_concepts_by_symbol(symbol: str, db: DbDep, cache: CacheDep) -> ConceptBySymbolOut:
    """个股所属概念（触点 C）：未知 symbol 返回空 items 让前端整块不渲染，**不是 404**。"""
    return ConceptBySymbolOut(**await concept_service.get_concepts_by_symbol(db, symbol, cache))


@router.get("/{board_code}/stocks", response_model=list[StockEnrichedOut])
async def list_board_stocks(board_code: str, db: DbDep) -> list[StockEnrichedOut]:
    """板块成分股，**纯数组**（无 envelope）：前端复用 `mapBackendStockEnriched` 零改映射。

    `change_percent DESC NULLS LAST, symbol ASC`；未知板块码返回 `[]`（空成分表）。
    成分列表的分页外元信息在 `GET /concepts/{board_code}` 里。
    """
    rows = await concept_service.get_board_stocks(db, board_code)
    return [StockEnrichedOut(**row) for row in rows]


@router.get("/{board_code}", response_model=ConceptDetailOut)
async def get_board_detail(board_code: str, db: DbDep, cache: CacheDep) -> ConceptDetailOut:
    """板块详情：单板 BoardItem + KPI + 板内连板梯队（与 `/market/limit-up` 快照同源）。"""
    body = await concept_service.get_board_detail(db, board_code, cache)
    if body is None:
        raise not_found_response("concept board", board_code)
    return ConceptDetailOut(**body)
