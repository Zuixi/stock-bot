"""First-boot data initialisation.

Detects an empty ``stocks`` table and asynchronously seeds the database
with stock universe + recent daily quotes from TuShare Pro.

This runs as a background ``asyncio.Task`` so it never blocks the API.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.stock import Stock

logger = logging.getLogger(__name__)

EXCHANGES = ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]
SEED_TRADE_DAYS = 30

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
    except Exception:
        logger.warning("data_init: SW industry check/import failed", exc_info=True)

    if count > 0:
        logger.info("data_init: %d stocks already in DB — skipping seed", count)
        return

    logger.info("data_init: empty database detected — starting background seed")
    _init_task = asyncio.create_task(_seed_database())


async def _seed_database() -> None:
    """Pull stock universe + recent daily quotes from TuShare."""
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

    # Step 1: Ingest stock universe for all exchanges
    for exchange in EXCHANGES:
        try:
            async with async_session_factory() as db:
                result = await service.ingest_stock_universe(db, exchange)
                logger.info("data_init: universe %s -> %s", exchange, result)
        except Exception:
            logger.error("data_init: universe ingest failed for %s", exchange, exc_info=True)

    # Step 2: Get recent trade dates from trade calendar
    trade_dates: list[str] = []
    try:
        today = datetime.now().strftime("%Y%m%d")
        df = await client.fetch_trade_cal(
            exchange="SSE",
            end_date=today,
            is_open="1",
        )
        if not df.empty:
            all_dates = sorted(df["cal_date"].astype(str).tolist(), reverse=True)
            trade_dates = list(reversed(all_dates[:SEED_TRADE_DAYS]))
    except Exception:
        logger.warning("data_init: trade_cal failed", exc_info=True)

    if not trade_dates:
        logger.warning("data_init: no trade dates found — skipping daily quotes seed")
        return

    # Step 3: Ingest daily quotes for each trade date
    logger.info("data_init: seeding %d trade dates of daily data", len(trade_dates))
    for td in trade_dates:
        try:
            async with async_session_factory() as db:
                result = await service.ingest_daily_quotes(db, td)
                logger.info("data_init: daily %s -> upserted=%s", td, result.get("upserted", 0))
        except Exception:
            logger.error("data_init: daily ingest failed for %s", td, exc_info=True)

    # Step 4: Ingest index daily data (past year for dashboard indices)
    from app.services.market_service import _TARGET_INDICES  # noqa: PLC0415

    logger.info("data_init: seeding index daily data for %d indices", len(_TARGET_INDICES))
    for idx in _TARGET_INDICES:
        try:
            async with async_session_factory() as db:
                result = await service.ingest_index_daily(
                    db,
                    ts_code=idx["ts_code"],
                    start_date=trade_dates[0] if trade_dates else "",
                    end_date=trade_dates[-1] if trade_dates else "",
                )
                logger.info("data_init: index %s -> upserted=%s", idx["ts_code"], result.get("upserted", 0))
        except Exception:
            logger.error("data_init: index ingest failed for %s", idx["ts_code"], exc_info=True)

    logger.info("data_init: seed complete")
