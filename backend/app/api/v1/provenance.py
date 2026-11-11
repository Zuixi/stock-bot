"""指标溯源端点（P7）.

``GET /api/v1/provenance``              列出可溯源指标
``GET /api/v1/provenance/{metric_key}`` 单指标完整链路（值 / 口径 / 来源 / 批次 / 质量 / 新鲜度）

权限：行情与情绪指标随公开路由；研究指标沿用 ``research:read`` 时由调用方在 router
层加依赖（当前 6 个指标均为公开行情/情绪口径）。缓存 60s，key 含 ``metric_key`` +
``date`` + ``scope_key``——三者任一变化都必须换 key。
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CacheDep, DbDep
from app.services import lineage_registry, market_day_service, provenance_service
from app.services.industry_registry import find_metric

logger = logging.getLogger(__name__)

router = APIRouter()

_CACHE_TTL = 60


@router.get("")
async def list_metrics() -> dict[str, list[dict[str, str]]]:
    return {
        "items": [
            {
                "key": item.key,
                "label": item.label,
                "unit": item.unit,
                "caliber_version": item.caliber_version,
            }
            for item in lineage_registry.METRICS.values()
        ]
    }


@router.get("/{metric_key}")
async def get_provenance(
    metric_key: str,
    db: DbDep,
    cache: CacheDep,
    date_: date | None = Query(default=None, alias="date"),
    scope_key: str | None = Query(default=None),
) -> dict:
    lineage = lineage_registry.get(metric_key)
    if lineage is None and find_metric(metric_key) is None:
        raise HTTPException(status_code=404, detail=f"unknown metric_key: {metric_key}")
    if lineage is not None and lineage.scope_required and not scope_key:
        raise HTTPException(status_code=404, detail=f"metric {metric_key} requires scope_key")

    if date_ is None:
        latest = await market_day_service.resolve_latest_complete_day(db)
        if latest is None:
            raise HTTPException(status_code=503, detail="NO_COMPLETE_TRADING_DAY")
        day = latest.day
    else:
        day = date_

    cache_key = f"market:provenance:{metric_key}:{day.isoformat()}:{scope_key or '-'}"
    cached = await cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    payload = await provenance_service.build(
        db, metric_key=metric_key, day=day, scope_key=scope_key
    )
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unsupported scope for {metric_key}")
    await cache.set(cache_key, payload, ttl=_CACHE_TTL)
    return payload
