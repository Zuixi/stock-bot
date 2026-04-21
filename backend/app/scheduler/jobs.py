"""Scheduled job definitions for SSE index snapshot collection."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

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
