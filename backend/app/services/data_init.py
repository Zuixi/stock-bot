"""First-boot data initialisation.

Detects an empty ``stocks`` table and asynchronously seeds the database
with stock universe + trailing 3-year daily quote coverage from TuShare Pro.

This runs as a background ``asyncio.Task`` so it never blocks the API.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.stock import Stock

logger = logging.getLogger(__name__)

EXCHANGES = ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]
DAILY_BACKFILL_YEARS = 3
DAILY_BACKFILL_CONCURRENCY = 3
DAILY_BACKFILL_BATCH_SIZE = 100

_init_task: asyncio.Task | None = None


async def maybe_seed_on_startup() -> None:
    """Check DB; if empty, kick off a background seed task.

    Also ensures SW industry data is imported from local XLS files
    even if stock data already exists.
    """
    global _init_task

    try:
        async with async_session_factory() as db:
            count = (await db.execute(select(func.count()).select_from(Stock))).scalar_one()
    except Exception:
        logger.warning("data_init: cannot query stocks table — skipping seed check", exc_info=True)
        return

    # Always check if SW industry data needs importing
    try:
        from app.services.sw_industry_service import import_all as import_sw_all  # noqa: PLC0415
        from app.services.sw_industry_service import is_sw_data_loaded  # noqa: PLC0415

        if not await is_sw_data_loaded():
            logger.info("data_init: SW industry data missing — importing")
            result = await import_sw_all()
            logger.info("data_init: SW industry import -> %s", result)
        else:
            logger.info("data_init: SW industry data already loaded")

        # Custom-tag overlay is additive & idempotent — must run even when SW
        # tables are already loaded (pre-overlay deployments upgrading would
        # otherwise never receive the OTHER→SW merge).
        from app.services.sw_industry_service import import_custom_tags_from_sql  # noqa: PLC0415

        await import_custom_tags_from_sql()
    except Exception:
        logger.warning("data_init: SW industry check/import failed", exc_info=True)

    if count > 0:
        logger.info("data_init: %d stocks already in DB — starting coverage check", count)
        _init_task = asyncio.create_task(_seed_database(skip_universe=True))
        return

    logger.info("data_init: empty database detected — starting background seed")
    _init_task = asyncio.create_task(_seed_database(skip_universe=False))


async def _seed_database(skip_universe: bool) -> None:
    """Pull stock universe (if needed) + ensure trailing 3-year daily coverage."""
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    try:
        client = get_tushare_client()
    except Exception:
        logger.error(
            "data_init: TuShare client init failed — is TUSHARE_TOKEN set in .env? "
            "stock_basic requires 2000+ credits. See https://tushare.pro/document/1?doc_id=108",
            exc_info=True,
        )
        return

    service = TuShareIngestService(client=client)

    # Step 1: Ingest stock universe for all exchanges if DB is empty.
    if not skip_universe:
        for exchange in EXCHANGES:
            try:
                async with async_session_factory() as db:
                    result = await service.ingest_stock_universe(db, exchange)
                    logger.info("data_init: universe %s -> %s", exchange, result)
            except Exception:
                logger.error("data_init: universe ingest failed for %s", exchange, exc_info=True)

    # Step 2: Ensure daily quotes cover the trailing 3 years.
    # Step 2.5: Ensure daily basic indicators cover trailing 1 year.
    # These two use independent APIs/DB tables — run them in parallel.
    quote_task = asyncio.create_task(
        _ensure_trailing_three_year_daily_quotes(service),
        name="daily-quotes-backfill",
    )
    basic_task = asyncio.create_task(
        _ensure_trailing_one_year_daily_basic(service),
        name="daily-basic-backfill",
    )
    await asyncio.gather(quote_task, basic_task)

    # Step 3: Ingest index daily data (past year for dashboard indices)
    from app.services.market_service import _TARGET_INDICES  # noqa: PLC0415

    logger.info("data_init: seeding index daily data for %d indices", len(_TARGET_INDICES))
    start_date = (date.today() - timedelta(days=365)).strftime("%Y%m%d")
    end_date = date.today().strftime("%Y%m%d")
    for idx in _TARGET_INDICES:
        try:
            async with async_session_factory() as db:
                result = await service.ingest_index_daily(
                    db,
                    ts_code=idx["ts_code"],
                    start_date=start_date,
                    end_date=end_date,
                )
                logger.info(
                    "data_init: index %s -> upserted=%s",
                    idx["ts_code"],
                    result.get("upserted", 0),
                )
        except Exception:
            logger.error("data_init: index ingest failed for %s", idx["ts_code"], exc_info=True)

    logger.info("data_init: seed complete")


def _chunked(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _ensure_trailing_three_year_daily_quotes(service) -> None:
    today = date.today()
    async with async_session_factory() as db:
        missing = await service.list_stocks_missing_daily_coverage(
            db,
            years=DAILY_BACKFILL_YEARS,
            asof_date=today,
        )

    if not missing:
        logger.info("data_init: all stocks already cover trailing %d years", DAILY_BACKFILL_YEARS)
        return

    logger.info(
        "data_init: %d stocks need daily backfill for trailing %d years",
        len(missing),
        DAILY_BACKFILL_YEARS,
    )
    sem = asyncio.Semaphore(DAILY_BACKFILL_CONCURRENCY)
    total_upserted = 0
    failed = 0

    async def _run_one(item: dict) -> int:
        async with sem:
            async with async_session_factory() as db:
                result = await service.ingest_daily_quotes_for_stock(
                    db,
                    stock_id=item["stock_id"],
                    exchange=item["exchange"],
                    symbol=item["symbol"],
                    start_date=item["expected_start"],
                    end_date=item["asof_date"],
                    save_raw=False,
                )
                await db.commit()
                return int(result.get("upserted", 0))

    batches = _chunked(missing, DAILY_BACKFILL_BATCH_SIZE)
    for batch_index, batch in enumerate(batches, start=1):
        tasks = [_run_one(item) for item in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        batch_upserted = 0
        for result in results:
            if isinstance(result, Exception):
                failed += 1
                logger.warning("data_init: stock backfill task failed: %s", result)
                continue
            batch_upserted += int(result)
        total_upserted += batch_upserted
        logger.info(
            "data_init: backfill batch %d/%d done (stocks=%d, upserted=%d, failed=%d)",
            batch_index,
            len(batches),
            len(batch),
            batch_upserted,
            failed,
        )

    logger.info(
        "data_init: trailing %d-year backfill complete (stocks=%d, upserted=%d, failed=%d)",
        DAILY_BACKFILL_YEARS,
        len(missing),
        total_upserted,
        failed,
    )


async def _ensure_trailing_one_year_daily_basic(service) -> None:
    """Backfill daily_basic_indicators for trailing 1 year by trade_date.

    TuShare daily_basic supports trade_date-based batch fetch (entire market
    per call) which is far more efficient than per-stock iteration.
    """
    from datetime import datetime as _dt

    today = date.today()
    # Fetch up to 1 year back (250 trading days ≈ ~370 calendar days)
    start = today - timedelta(days=370)

    # Get the list of trading days in this range
    try:
        df_cal = await service.client.fetch_trade_cal(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=today.strftime("%Y%m%d"),
            is_open="1",
        )
        if df_cal.empty:
            logger.warning("data_init: trade_cal empty, skipping daily_basic backfill")
            return
        trade_dates = sorted(df_cal["cal_date"].astype(str).tolist())
    except Exception as exc:
        logger.warning("data_init: trade_cal failed for daily_basic backfill: %s", exc)
        return

    # Check which dates are already covered (sample a few to detect gap)
    async with async_session_factory() as db:
        from sqlalchemy import select, func
        from app.models.daily_basic import DailyBasicIndicator

        result = await db.execute(
            select(DailyBasicIndicator.trade_date).distinct()
        )
        existing_dates = {d for (d,) in result.all()}

    missing_dates = [td for td in trade_dates if _dt.strptime(td, "%Y%m%d").date() not in existing_dates]

    if not missing_dates:
        logger.info("data_init: daily_basic already covers trailing 1 year")
        return

    logger.info(
        "data_init: %d trade dates need daily_basic backfill (out of %d total)",
        len(missing_dates), len(trade_dates),
    )

    # Process dates concurrently, respecting TuShare rate limits.
    total_upserted = 0
    total_saved = 0
    failed = 0
    sem = asyncio.Semaphore(4)  # 4 concurrent API calls — safe, client lock serializes

    async def _run_one_date(td: str) -> tuple[int, int]:
        async with sem:
            async with async_session_factory() as db:
                result = await service.ingest_daily_basic(db, td)
                await db.commit()
            return result.get("upserted", 0), result.get("saved", 0)

    batch_size = 20
    for i in range(0, len(missing_dates), batch_size):
        batch = missing_dates[i : i + batch_size]
        tasks = [_run_one_date(td) for td in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        batch_upserted = 0
        batch_failed = 0
        for r in results:
            if isinstance(r, Exception):
                batch_failed += 1
                logger.warning(
                    "data_init: daily_basic backfill task failed: %s", r,
                )
            else:
                batch_upserted += r[0]
                total_saved += r[1]
        total_upserted += batch_upserted
        failed += batch_failed

        logger.info(
            "data_init: daily_basic backfill batch %d/%d done (upserted=%d, failed=%d)",
            i // batch_size + 1,
            (len(missing_dates) + batch_size - 1) // batch_size,
            batch_upserted,
            batch_failed,
        )

    logger.info(
        "data_init: daily_basic trailing 1-year backfill complete "
        "(dates=%d, upserted=%d, saved=%d, failed=%d)",
        len(missing_dates), total_upserted, total_saved, failed,
    )
