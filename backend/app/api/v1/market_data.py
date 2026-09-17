"""Market-data face endpoints: global indices / sector moneyflow / northbound（北向）/
dragon-tiger（龙虎榜）/ block-trades（大宗交易）/ share-floats（解禁）/ repurchases（回购）/
announcements（公告快讯）/ limit-up ladder（连板梯队与市场情绪）."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CacheDep
from app.repositories import market_data_repo
from app.schemas.limit_up import (
    LimitUpLadderOut,
    SectorLimitUpOut,
    SentimentCalendarPointOut,
    SentimentIntradayPointOut,
    YesterdayLimitUpOut,
)
from app.schemas.market_data import (
    AnnouncementOut,
    BlockTradeOut,
    DragonTigerOut,
    GlobalIndexCardOut,
    MarketMoneyflowOut,
    NorthboundSeriesOut,
    RepurchaseOut,
    SectorMoneyflowListOut,
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


@router.get("/sector-moneyflow", response_model=SectorMoneyflowListOut)
async def get_sector_moneyflow_endpoint(
    cache: CacheDep,
    dimension: Literal["industry", "concept", "region"] = "industry",
    limit: int = Query(default=15, ge=1, le=100),
) -> SectorMoneyflowListOut:
    """板块资金流榜；`as_of` 描述**返回的 items**：取该 dimension 自己持有的最近日
    （各 dimension 可能不同）；无 items 时 `as_of`/`stale_days` 均为 None。
    """
    payload = await market_data_service.get_sector_moneyflow(cache, dimension, limit)
    return SectorMoneyflowListOut.model_validate(payload)


@router.get("/northbound", response_model=NorthboundSeriesOut)
async def get_northbound(
    cache: CacheDep, days: int = Query(default=30, ge=1, le=180)
) -> NorthboundSeriesOut:
    """北向净流入序列；`as_of` 是**返回的 items 末项**的日期，窗口内无项时 None
    （此时 `source_status="discontinued"`，上游停更也从该状态体现）。
    """
    payload = await market_data_service.get_northbound_series(cache, days)
    return NorthboundSeriesOut.model_validate(payload)


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
    mode: Literal["close", "intraday"] = Query(
        default="close",
        description="close=本地口径（默认，权威、可回放）；intraday=盘中口径（仅今日，由东财涨停池兜底）",
    ),
) -> LimitUpLadderOut:
    """连板梯队 + 情绪温度计（本地 K 线自算为主，见 docs/design/limit-up-sentiment.md）。

    `mode=intraday` 仅接受今日（无 as_of 或 as_of=今天）；历史日期会 400。
    盘中口径**不写** market_sentiment_daily；东财池失败时回落收盘口径并把
    `as_of_label` 标为 "盘中不可用，已回落收盘"。
    """
    try:
        snap = await limit_up_service.get_snapshot(cache, _iso(date_), lookback, mode=mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc) or "date must be ISO format, e.g. 2026-09-08",
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
        as_of_label=snap.get("as_of_label"),
    )


@router.get("/sector-limit-up", response_model=SectorLimitUpOut)
async def get_sector_limit_up(
    cache: CacheDep,
    date_: str | None = Query(default=None, alias="date"),
    sw_l1: str | None = Query(default=None, description="按申万一级代码过滤，如 110000"),
    mode: Literal["close", "intraday"] = Query(default="close"),
) -> SectorLimitUpOut:
    try:
        snap = await limit_up_service.get_snapshot(cache, _iso(date_), mode=mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc) or "date must be ISO format, e.g. 2026-09-08",
        ) from None
    payload = limit_up_service.sector_payload(snap, sw_l1)
    return SectorLimitUpOut(**payload)


@router.get("/yesterday-limit-up", response_model=YesterdayLimitUpOut)
async def get_yesterday_limit_up(
    cache: CacheDep,
    date_: str | None = Query(default=None, alias="date"),
    mode: Literal["close", "intraday"] = Query(default="close"),
) -> YesterdayLimitUpOut:
    try:
        snap = await limit_up_service.get_snapshot(cache, _iso(date_), mode=mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc) or "date must be ISO format, e.g. 2026-09-08",
        ) from None
    return YesterdayLimitUpOut(**limit_up_service.yesterday_payload(snap))


@router.get("/sentiment/calendar", response_model=list[SentimentCalendarPointOut])
async def get_sentiment_calendar(
    cache: CacheDep, days: int = Query(default=30, ge=5, le=120)
) -> list[SentimentCalendarPointOut]:
    rows = await limit_up_service.get_calendar(cache, days)
    return [SentimentCalendarPointOut(**r) for r in rows]


@router.get("/sentiment/intraday", response_model=list[SentimentIntradayPointOut])
async def get_sentiment_intraday(
    cache: CacheDep,
    date: str | None = Query(default=None, description="ISO 日期，缺省=今天（上海时区）"),
) -> list[SentimentIntradayPointOut]:
    """盘中分时点序列（升序），按 ``captured_at`` 升序返回 ``market_sentiment_intraday`` 表点。

    缓存 key ``market:limit-up:intra-points:{date}`` TTL=60s：5min scheduler 轮询
    + 60s 端点 cache 共同兜住前端 30s 轮询节拍。**未来日期** 400（避免上游被请求
    时刻尚未落库导致的不一致）。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services.market_data_service import _today_sh  # noqa: PLC0415

    today_sh = _today_sh()
    if date is None:
        target = today_sh
    else:
        try:
            target = datetime.fromisoformat(date).date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="date must be ISO format, e.g. 2026-09-17"
            ) from None
    if target > today_sh:
        raise HTTPException(
            status_code=400,
            detail=f"date must not be in the future (today_sh={today_sh.isoformat()})",
        )

    cache_key = f"market:limit-up:intra-points:{target.isoformat()}"
    if cache is not None:
        cached: list[dict] | None = await cache.get(cache_key)
        if cached:
            return [SentimentIntradayPointOut(**p) for p in cached]

    async with async_session_factory() as db:
        rows = await market_data_repo.list_intraday_snapshot(db, target)
    out = [SentimentIntradayPointOut.model_validate(r, from_attributes=True) for r in rows]
    if cache is not None and out:
        await cache.set(cache_key, [p.model_dump(mode="json") for p in out], ttl=60)
    return out


@router.get("/data-freshness", response_model=DataFreshnessOut)
async def get_data_freshness() -> DataFreshnessOut:
    """数据新鲜度巡检（只读，绝不触发回补）：把"静默缺数据"变成端点可见。

    复用对账器的期望集/行数判据（apply=False 路径），开销为几次聚合计数查询。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import job_alert_service, reconciliation_service  # noqa: PLC0415

    async with async_session_factory() as db:
        result = await reconciliation_service.reconcile_market_data(db, apply=False)
    # Redis 不可用 → 空列表（登记表是运行时告警，不影响"只读巡检"契约）
    result["failed_jobs"] = await job_alert_service.list_job_failures()
    return DataFreshnessOut(**result)
