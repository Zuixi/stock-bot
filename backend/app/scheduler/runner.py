"""Scheduler process entry point — runs APScheduler with SSE collection jobs."""

from __future__ import annotations

import asyncio
import logging
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.scheduler.jobs import (
    daily_basic_backfill_job,
    daily_quotes_backfill_job,
    industry_metrics_refresh_job,
    securities_refresh_job,
    sse_post_close_job,
    sse_trade_hours_job,
    universe_refresh_job,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    # Trading hours: every 10 minutes, Monday–Friday, 9:30–15:00
    # The job itself checks whether we're actually inside the trading window.
    scheduler.add_job(
        sse_trade_hours_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-14",
            minute="*/10",
            timezone="Asia/Shanghai",
        ),
        id="sse_trade_hours",
        name="SSE trade-hours snapshot",
        replace_existing=True,
    )

    # 15:00 final tick (captured by the 14:50 + 15:00 cron above won't fire for hour=15)
    scheduler.add_job(
        sse_trade_hours_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=0,
            timezone="Asia/Shanghai",
        ),
        id="sse_trade_close",
        name="SSE 15:00 closing tick",
        replace_existing=True,
    )

    # Post-close sweep at 15:30
    scheduler.add_job(
        sse_post_close_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=30,
            timezone="Asia/Shanghai",
        ),
        id="sse_post_close",
        name="SSE post-close snapshot",
        replace_existing=True,
    )

    # ── Daily data backfill jobs ──────────────────────────────────
    # Run after market close so TuShare has processed the day's data.
    # Quotes: 16:30 Mon-Fri
    scheduler.add_job(
        daily_quotes_backfill_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=30,
            timezone="Asia/Shanghai",
        ),
        id="daily_quotes_backfill",
        name="Daily quotes backfill",
        replace_existing=True,
    )

    # Daily basic: 16:45 Mon-Fri (after quotes backfill)
    scheduler.add_job(
        daily_basic_backfill_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=45,
            timezone="Asia/Shanghai",
        ),
        id="daily_basic_backfill",
        name="Daily basic backfill",
        replace_existing=True,
    )

    # Industry research metrics: 17:05 Mon-Fri (after quote/daily-basic backfills)
    scheduler.add_job(
        industry_metrics_refresh_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=17,
            minute=5,
            timezone="Asia/Shanghai",
        ),
        id="industry_metrics_refresh",
        name="Industry metrics refresh",
        replace_existing=True,
    )

    # Industry ETF/CB daily bars: 17:10 Mon-Fri (after industry_metrics refresh)
    scheduler.add_job(
        securities_refresh_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=17,
            minute=10,
            timezone="Asia/Shanghai",
        ),
        id="securities_refresh",
        name="Securities (ETF/CB) refresh",
        replace_existing=True,
    )

    # Stock universe metadata: 09:00 Sat weekly (non-trading day, low load;
    # keeps stocks.asof / stocks_history snapshots from freezing forever)
    scheduler.add_job(
        universe_refresh_job,
        CronTrigger(
            day_of_week="sat",
            hour=9,
            minute=0,
            timezone="Asia/Shanghai",
        ),
        id="universe_refresh",
        name="Universe metadata refresh",
        replace_existing=True,
    )

    return scheduler


async def main() -> None:
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started — %d jobs registered", len(scheduler.get_jobs()))
    for job in scheduler.get_jobs():
        logger.info("  • %s  next_run=%s", job.name, job.next_run_time)

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        from app.services.sse_scraper_service import close_http_client  # noqa: PLC0415
        await close_http_client()
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    asyncio.run(main())
