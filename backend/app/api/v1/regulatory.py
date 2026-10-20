"""监管异动雷达读面：`GET /api/v1/market/regulatory-anomalies`（公开，只读）。

口径与已知失效场景见 `docs/design/regulatory-anomaly.md`。只算 A 股价格口径；
换手率口径与交易所公告原文不在此接口内。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CacheDep
from app.schemas.regulatory import RegulatoryRadarOut
from app.services import regulatory_anomaly_service

router = APIRouter(tags=["market"])


@router.get("/regulatory-anomalies", response_model=RegulatoryRadarOut)
async def get_regulatory_anomalies(
    cache: CacheDep,
    date_: str | None = Query(
        default=None, alias="date", description="ISO 日期，缺省=库内最新完整交易日"
    ),
    history_days: int = Query(default=10, ge=1, le=30, description="异动历史回看交易日数"),
) -> RegulatoryRadarOut:
    """监管异动雷达：今日涨停/跌停与临近异动线的个股 + 明日触发价 + 近 N 日异动历史。"""
    try:
        target = datetime.fromisoformat(date_).date() if date_ else None
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-10-19"
        ) from None
    try:
        await regulatory_anomaly_service.ensure_within_window(cache, target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    payload = await regulatory_anomaly_service.get_radar(cache, target, history_days)
    return RegulatoryRadarOut(**payload)
