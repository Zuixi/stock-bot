"""Market endpoints for dashboard data."""

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.quote import IndexDailyOut, IndexKlineResponse
from app.schemas.sse_index import (
    BackfillRequest,
    BackfillResponse,
    SseIntradayPoint,
    SseIntradayResponse,
    SseSnapshotOut,
)
from app.schemas.stock import StockOut
from app.services import market_service

router = APIRouter()


@router.get("/indices", response_model=list[dict])
async def list_market_indices(cache: CacheDep) -> list[dict]:
    return await market_service.list_market_indices(cache=cache)


@router.get("/distribution", response_model=list[dict])
async def get_distribution(cache: CacheDep) -> list[dict]:
    return await market_service.get_distribution(cache=cache)


@router.get("/sectors", response_model=list[dict])
async def get_sectors(cache: CacheDep) -> list[dict]:
    return await market_service.get_sectors(cache=cache)


@router.get("/capital-flow", response_model=list[dict])
async def get_capital_flow(cache: CacheDep) -> list[dict]:
    return await market_service.get_capital_flow(cache=cache)


@router.get("/hot-boards", response_model=list[dict])
async def get_hot_boards(
    cache: CacheDep,
    category: Literal["industry", "concept", "region"] = Query(default="industry"),
) -> list[dict]:
    return await market_service.get_hot_boards(category, cache=cache)


@router.get("/indices/{ts_code}/kline", response_model=IndexKlineResponse)
async def get_index_kline(
    ts_code: str,
    cache: CacheDep,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
) -> IndexKlineResponse:
    """Return index daily K-line data for charting."""
    if start is None:
        start = date.today() - timedelta(days=365)
    data = await market_service.get_index_kline(
        ts_code, start_date=start, end_date=end, cache=cache,
    )
    name = market_service.INDEX_NAME_MAP.get(ts_code, ts_code)
    return IndexKlineResponse(
        ts_code=ts_code,
        name=name,
        data=[IndexDailyOut.model_validate(d) for d in data],
    )


@router.get("/sw-industry/tree", response_model=list[dict])
async def get_sw_industry_tree(cache: CacheDep) -> list[dict]:
    return await market_service.get_sw_industry_tree(cache=cache)


@router.get("/sw-industry/{level1_code}/stocks", response_model=list[StockOut])
async def get_sw_level1_stocks(level1_code: str, db: DbDep) -> list[StockOut]:
    if await market_service.get_sw_level1(level1_code) is None:
        raise not_found_response("SW level1", level1_code)
    symbols = await market_service.list_symbols_by_level1(level1_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get("/sw-industry/{level1_code}/{level2_code}/stocks", response_model=list[StockOut])
async def get_sw_level2_stocks(level1_code: str, level2_code: str, db: DbDep) -> list[StockOut]:
    if await market_service.get_sw_level2(level1_code, level2_code) is None:
        raise not_found_response("SW level2", f"{level1_code}/{level2_code}")
    symbols = await market_service.list_symbols_by_level2(level1_code, level2_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get(
    "/sw-industry/{level1_code}/{level2_code}/{level3_code}/stocks",
    response_model=list[StockOut],
)
async def get_sw_level3_stocks(
    level1_code: str, level2_code: str, level3_code: str, db: DbDep
) -> list[StockOut]:
    if await market_service.get_sw_level3(level1_code, level2_code, level3_code) is None:
        raise not_found_response("SW level3", f"{level1_code}/{level2_code}/{level3_code}")
    symbols = await market_service.list_symbols_by_level3(level1_code, level2_code, level3_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


# ---------------------------------------------------------------------------
# SSE index snapshots
# ---------------------------------------------------------------------------

_SSE_CACHE_TTL = 60  # 1 minute — snapshots update every 10 min


@router.get("/sse-snapshots/latest", response_model=list[SseSnapshotOut])
async def get_sse_latest_snapshots(db: DbDep, cache: CacheDep) -> list[SseSnapshotOut]:
    """Return the most recent snapshot for every tracked SSE index."""
    cache_key = "market:sse-snapshots:latest"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    from app.repositories import sse_index_repo  # noqa: PLC0415

    rows = await sse_index_repo.get_latest_snapshots(db)
    result = [SseSnapshotOut.model_validate(r) for r in rows]
    if result:
        await cache.set(cache_key, [r.model_dump(mode="json") for r in result], _SSE_CACHE_TTL)
    return result


@router.get("/sse-snapshots/{code}/intraday", response_model=SseIntradayResponse)
async def get_sse_intraday(
    code: str,
    db: DbDep,
    cache: CacheDep,
    trade_date: date | None = Query(default=None, alias="date"),
) -> SseIntradayResponse:
    """Return intraday snapshots for a single index on the given date."""
    if trade_date is None:
        trade_date = date.today()

    cache_key = f"market:sse-intraday:{code}:{trade_date}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return SseIntradayResponse(**cached)

    from app.repositories import sse_index_repo  # noqa: PLC0415

    rows = await sse_index_repo.get_intraday_by_date(db, code, trade_date)
    if not rows:
        return SseIntradayResponse(code=code, name=code, trade_date=trade_date, data=[])

    name = rows[0].name
    data = [
        SseIntradayPoint(
            time=r.collect_time.strftime("%H:%M"),
            last=float(r.last),
            chg_rate=float(r.chg_rate) if r.chg_rate is not None else None,
        )
        for r in rows
    ]
    resp = SseIntradayResponse(code=code, name=name, trade_date=trade_date, data=data)
    await cache.set(cache_key, resp.model_dump(mode="json"), _SSE_CACHE_TTL)
    return resp


@router.post("/sse-snapshots/backfill", response_model=BackfillResponse, status_code=202)
async def trigger_sse_backfill(
    req: BackfillRequest,
    background_tasks: BackgroundTasks,
) -> BackfillResponse:
    """Trigger a background backfill of historical SSE snapshots."""
    from app.services import sse_scraper_service  # noqa: PLC0415

    background_tasks.add_task(
        sse_scraper_service.batch_backfill, req.start_date, req.end_date,
    )
    return BackfillResponse(
        message="Backfill task started",
        start_date=req.start_date,
        end_date=req.end_date,
    )
