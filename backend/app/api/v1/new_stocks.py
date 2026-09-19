"""次新股板块读接口：`GET /api/v1/new-stocks`（T10）。

单板只读（东财 `BK0501` 成分 × 本地行情 × 涨停梯队，口径见 plans §2.2/§2.3）+ `CacheDep`
（service 内 Redis 300s，仅非空结果写入）。无请求参数，故无常量化的 SQL 面。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CacheDep, DbDep
from app.schemas.concept import NewStocksOut
from app.services import new_stock_service

router = APIRouter(tags=["new-stocks"])


@router.get("", response_model=NewStocksOut)
async def get_new_stocks(db: DbDep, cache: CacheDep) -> NewStocksOut:
    """次新股情绪卡数据：BK0501 成分 + 上市统计 KPI + 梯队 streak/is_lu。"""
    return NewStocksOut(**await new_stock_service.get_new_stock_board(db, cache))
