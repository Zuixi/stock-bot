"""Market-data face endpoints: global indices / sector moneyflow / northbound（北向）/
dragon-tiger（龙虎榜）/ block-trades（大宗交易）/ share-floats（解禁）/ repurchases（回购）/
announcements（公告快讯）/ limit-up ladder（连板梯队与市场情绪）."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CacheDep
from app.schemas.limit_up import (
    LimitUpLadderOut,
    SectorLimitUpOut,
    SentimentCalendarPointOut,
    YesterdayLimitUpOut,
)
from app.schemas.market_data import (
    AnnouncementOut,
    BlockTradeOut,
    DragonTigerOut,
    GlobalIndexCardOut,
    MarketMoneyflowOut,
    NorthboundPointOut,
    RepurchaseOut,
    SectorMoneyflowOut,
    ShareFloatOut,
)
from app.schemas.reconciliation import DataFreshnessOut
from app.services import limit_up_service, market_data_service

router = APIRouter(tags=["market-data"])


def _iso(v: str | None) -> date | None:
    """ISO 日期 → date；解析失败抛 ValueError（由各端点转 400，与 /dragon-tiger 同写法）。"""
    return datetime.fromisoformat(v).date() if v else None


@router.get("/global-indices", response_model=list[GlobalIndexCardOut])
async def get_global_indices(cache: CacheDep) -> list[GlobalIndexCardOut]:
    cards = await market_data_service.get_global_index_cards(cache)
    return [GlobalIndexCardOut(**c) for c in cards]


@router.get("/sector-moneyflow", response_model=list[SectorMoneyflowOut])
async def get_sector_moneyflow_endpoint(
    cache: CacheDep,
    dimension: Literal["industry", "concept", "region"] = "industry",
    limit: int = Query(default=15, ge=1, le=100),
) -> list[SectorMoneyflowOut]:
    rows = await market_data_service.get_sector_moneyflow(cache, dimension, limit)
    return [SectorMoneyflowOut(**r) for r in rows]


@router.get("/northbound", response_model=list[NorthboundPointOut])
async def get_northbound(
    cache: CacheDep, days: int = Query(default=30, ge=1, le=180)
) -> list[NorthboundPointOut]:
    rows = await market_data_service.get_northbound_series(cache, days)
    return [NorthboundPointOut(**r) for r in rows]


@router.get("/dragon-tiger", response_model=list[DragonTigerOut])
async def get_dragon_tiger_endpoint(
    cache: CacheDep,
    date: str | None = Query(default=None, description="ISO 日期，缺省=表内最新交易日"),
    limit: int = Query(default=15, ge=1, le=100),
) -> list[DragonTigerOut]:
    try:
        rows = await market_data_service.get_dragon_tiger(cache, date, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-09-02"
        ) from None
    return [DragonTigerOut(**r) for r in rows]


@router.get("/block-trades", response_model=list[BlockTradeOut])
async def get_block_trades_endpoint(
    cache: CacheDep,
    date: str | None = Query(default=None, description="ISO 日期，缺省=表内最新交易日"),
    symbol: str | None = Query(default=None, description="6 位股票代码，如 000488"),
    limit: int = Query(default=15, ge=1, le=100),
) -> list[BlockTradeOut]:
    try:
        rows = await market_data_service.get_block_trades(cache, date, symbol, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-09-02"
        ) from None
    return [BlockTradeOut(**r) for r in rows]


@router.get("/share-floats", response_model=list[ShareFloatOut])
async def get_share_floats_endpoint(
    cache: CacheDep,
    start: str | None = Query(default=None, description="ISO 起始日期，缺省=近 30 天"),
    end: str | None = Query(default=None, description="ISO 结束日期，缺省=未来 90 天"),
    symbol: str | None = Query(default=None, description="6 位股票代码，如 002747"),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[ShareFloatOut]:
    try:
        rows = await market_data_service.get_share_floats(cache, start, end, symbol, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="start/end must be ISO format, e.g. 2026-09-02"
        ) from None
    return [ShareFloatOut(**r) for r in rows]


@router.get("/repurchases", response_model=list[RepurchaseOut])
async def get_repurchases_endpoint(
    cache: CacheDep,
    start: str | None = Query(default=None, description="ISO 起始日期，缺省=近 30 天"),
    end: str | None = Query(default=None, description="ISO 结束日期，缺省=今天"),
    symbol: str | None = Query(default=None, description="6 位股票代码，如 002120"),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[RepurchaseOut]:
    try:
        rows = await market_data_service.get_repurchases(cache, start, end, symbol, limit)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="start/end must be ISO format, e.g. 2026-09-02"
        ) from None
    return [RepurchaseOut(**r) for r in rows]


@router.get("/announcements", response_model=list[AnnouncementOut])
async def get_announcements_endpoint(
    cache: CacheDep,
    symbol: str | None = Query(default=None, description="6 位股票代码，如 002762"),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[AnnouncementOut]:
    from app.services import announcement_service  # noqa: PLC0415

    rows = await announcement_service.get_announcements(cache, symbol, limit)
    return [AnnouncementOut(**r) for r in rows]


@router.get("/market-moneyflow", response_model=MarketMoneyflowOut)
async def get_market_moneyflow(cache: CacheDep) -> MarketMoneyflowOut:
    """大盘资金流：今日四档实时 + 近 30 日历史（沪深两市合成口径）。"""
    payload = await market_data_service.get_market_moneyflow(cache)
    return MarketMoneyflowOut(**payload)


@router.get("/limit-up-ladder", response_model=LimitUpLadderOut)
async def get_limit_up_ladder(
    cache: CacheDep,
    date_: str | None = Query(
        default=None, alias="date", description="ISO 日期，缺省=库内最新交易日"
    ),
    lookback: int = Query(default=10, ge=5, le=30),
) -> LimitUpLadderOut:
    """连板梯队 + 情绪温度计（本地 K 线自算为主，见 docs/design/limit-up-sentiment.md）。"""
    try:
        snap = await limit_up_service.get_snapshot(cache, _iso(date_), lookback)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-09-08"
        ) from None
    return LimitUpLadderOut(
        as_of=snap["as_of"],
        as_of_prev=snap["as_of_prev"],
        as_of_quality=snap.get("as_of_quality", "partial"),
        source=snap["source"],
        limits_present=snap["limits_present"],
        is_partial=snap["is_partial"],
        sw_coverage=snap["sw_coverage"],
        lookback=lookback,
        degraded_reason=snap["degraded_reason"],
        kpis=snap["kpis"] or None,
        echelons=snap["echelons"],
    )


@router.get("/sector-limit-up", response_model=SectorLimitUpOut)
async def get_sector_limit_up(
    cache: CacheDep,
    date_: str | None = Query(default=None, alias="date"),
    sw_l1: str | None = Query(default=None, description="按申万一级代码过滤，如 110000"),
) -> SectorLimitUpOut:
    try:
        snap = await limit_up_service.get_snapshot(cache, _iso(date_))
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-09-08"
        ) from None
    payload = limit_up_service.sector_payload(snap, sw_l1)
    return SectorLimitUpOut(**payload)


@router.get("/yesterday-limit-up", response_model=YesterdayLimitUpOut)
async def get_yesterday_limit_up(
    cache: CacheDep, date_: str | None = Query(default=None, alias="date")
) -> YesterdayLimitUpOut:
    try:
        snap = await limit_up_service.get_snapshot(cache, _iso(date_))
    except ValueError:
        raise HTTPException(
            status_code=400, detail="date must be ISO format, e.g. 2026-09-08"
        ) from None
    return YesterdayLimitUpOut(**limit_up_service.yesterday_payload(snap))


@router.get("/sentiment/calendar", response_model=list[SentimentCalendarPointOut])
async def get_sentiment_calendar(
    cache: CacheDep, days: int = Query(default=30, ge=5, le=120)
) -> list[SentimentCalendarPointOut]:
    rows = await limit_up_service.get_calendar(cache, days)
    return [SentimentCalendarPointOut(**r) for r in rows]


@router.get("/data-freshness", response_model=DataFreshnessOut)
async def get_data_freshness() -> DataFreshnessOut:
    """数据新鲜度巡检（只读，绝不触发回补）：把"静默缺数据"变成端点可见。

    复用对账器的期望集/行数判据（apply=False 路径），开销为几次聚合计数查询。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import reconciliation_service  # noqa: PLC0415

    async with async_session_factory() as db:
        result = await reconciliation_service.reconcile_market_data(db, apply=False)
    return DataFreshnessOut(**result)
