"""Scheduled job definitions for SSE index snapshot + daily data backfill."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

JITTER_SEC = 30


def _is_workday() -> bool:
    return datetime.now().weekday() < 5


def _in_trading_hours() -> bool:
    """Return True if current time is within 9:25-15:05 (with small buffer)."""
    now = datetime.now()
    start = now.replace(hour=9, minute=25, second=0, microsecond=0)
    end = now.replace(hour=15, minute=5, second=0, microsecond=0)
    return start <= now <= end


async def _collect_sse_snapshots() -> None:
    from app.services import sse_scraper_service  # noqa: PLC0415

    try:
        count = await sse_scraper_service.fetch_and_save()
        logger.info("SSE snapshot collection complete: %d rows", count)
    except Exception:
        logger.exception("SSE snapshot collection failed")


async def sse_trade_hours_job() -> None:
    """Collect SSE snapshots during trading hours (9:30-15:00).

    Called by APScheduler every 10 minutes on weekdays.
    Adds a random jitter before fetching to avoid predictable patterns.
    """
    if not _is_workday():
        logger.debug("Skipping SSE collection — not a workday")
        return
    if not _in_trading_hours():
        logger.debug("Skipping SSE collection — outside trading hours")
        return

    jitter = random.uniform(0, JITTER_SEC)
    logger.info("SSE trade-hours job triggered, jitter=%.1fs", jitter)
    await asyncio.sleep(jitter)
    await _collect_sse_snapshots()


async def sse_post_close_job() -> None:
    """Collect the final closing snapshot at 15:30.

    A single post-close sweep to ensure we have the official closing data.
    """
    if not _is_workday():
        logger.debug("Skipping post-close collection — not a workday")
        return

    logger.info("SSE post-close job triggered")
    await _collect_sse_snapshots()


# ------------------------------------------------------------------
# Daily data ingestion jobs (TuShare "daily" + "daily_basic" APIs)
# ------------------------------------------------------------------

async def _fetch_yesterday_daily_quotes() -> None:
    """Fetch yesterday's full-market daily quotes and persist to DB."""
    yesterday = date.today() - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    trade_date = yesterday.strftime("%Y%m%d")

    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.repositories import quote_repo  # noqa: PLC0415
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    try:
        async with async_session_factory() as db:
            if await quote_repo.trade_date_exists(db, yesterday):
                logger.info("Skipping quotes backfill — trade_date=%s already exists", trade_date)
                return

        service = TuShareIngestService()
        async with async_session_factory() as db:
            result = await service.ingest_daily_quotes(db, trade_date)
            await db.commit()
            logger.info("Daily quotes backfill: trade_date=%s upserted=%d", trade_date, result.get("upserted", 0))
    except Exception:
        logger.exception("Daily quotes backfill failed for trade_date=%s", trade_date)


async def _fetch_yesterday_daily_basic() -> None:
    """Fetch yesterday's full-market daily_basic indicators and persist to DB."""
    yesterday = date.today() - timedelta(days=1)
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    trade_date = yesterday.strftime("%Y%m%d")

    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.repositories import daily_basic_repo  # noqa: PLC0415
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    try:
        async with async_session_factory() as db:
            if await daily_basic_repo.trade_date_exists(db, yesterday):
                logger.info("Skipping daily_basic backfill — trade_date=%s already exists", trade_date)
                return

        service = TuShareIngestService()
        async with async_session_factory() as db:
            result = await service.ingest_daily_basic(db, trade_date)
            await db.commit()
            logger.info("Daily basic backfill: trade_date=%s upserted=%d", trade_date, result.get("upserted", 0))
    except Exception:
        logger.exception("Daily basic backfill failed for trade_date=%s", trade_date)


async def daily_quotes_backfill_job() -> None:
    """Run daily quotes backfill after market close (16:30 Mon-Fri).

    Fetches yesterday's trade date data so there's enough buffer for
    TuShare to have processed the day's results.
    """
    if not _is_workday():
        logger.debug("Skipping daily quotes backfill — not a workday")
        return

    logger.info("Daily quotes backfill job triggered")
    await _fetch_yesterday_daily_quotes()


async def daily_basic_backfill_job() -> None:
    """Run daily_basic backfill after market close (16:45 Mon-Fri).

    Must run after daily_quotes_backfill_job since daily_basic uses
    the same trade_date source.
    """
    if not _is_workday():
        logger.debug("Skipping daily_basic backfill — not a workday")
        return

    logger.info("Daily basic backfill job triggered")
    await _fetch_yesterday_daily_basic()


# ------------------------------------------------------------------
# Industry research metrics (dual-track: worker via MQ, scheduler direct)
# ------------------------------------------------------------------

async def industry_metrics_refresh_job() -> None:
    """Refresh industry research metrics (17:05 Mon-Fri, after quote backfills)."""
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import industry_metric_service  # noqa: PLC0415

    logger.info("Industry metrics refresh job triggered")
    try:
        async with async_session_factory() as db:
            result = await industry_metric_service.ingest_industry_metrics(db, "pig")
            await db.commit()
        logger.info(
            "Industry metrics refresh done: source=%s upserted=%s signal=%s",
            result.get("source"), result.get("upserted"), result.get("signal"),
        )
    except Exception:
        logger.exception("Industry metrics refresh failed")


async def securities_refresh_job() -> None:
    """Refresh industry ETF/CB daily bars (17:10 Mon-Fri, after industry_metrics).

    日增量窗口（SCHEDULED_BACKFILL_DAYS）而非全年回补：幂等 upsert 兜底偶发缺口，
    避免每个交易日对 TuShare 打满整年请求。首年历史由手动任务全量回补。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415
    from app.services import securities_service  # noqa: PLC0415

    logger.info("Securities refresh job triggered")
    try:
        async with async_session_factory() as db:
            result = await securities_service.ingest_industry_securities(
                db, "pig", backfill_days=securities_service.SCHEDULED_BACKFILL_DAYS
            )
            await db.commit()
        logger.info(
            "Securities refresh done: etf_upserted=%s cb_upserted=%s",
            result.get("etf_upserted"), result.get("cb_upserted"),
        )
    except Exception:
        logger.exception("Securities refresh failed")
