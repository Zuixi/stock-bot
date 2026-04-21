"""Backfill historical SSE index snapshots for a date range.

Usage:
    python scripts/backfill_sse_snapshots.py
    python scripts/backfill_sse_snapshots.py --start 2026-04-01 --end 2026-04-21
"""

import argparse
import asyncio
import logging
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_START = date(2026, 4, 1)
DEFAULT_END = date(2026, 4, 21)


async def main(start_date: date, end_date: date) -> None:
    from app.services.sse_scraper_service import batch_backfill, close_http_client

    logger.info("Starting SSE snapshot backfill: %s → %s", start_date, end_date)
    try:
        total = await batch_backfill(start_date, end_date)
        logger.info("Backfill finished: %d total rows saved", total)
    finally:
        await close_http_client()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill SSE index snapshots")
    parser.add_argument(
        "--start", type=date.fromisoformat, default=DEFAULT_START,
        help="Start date (YYYY-MM-DD), default: 2026-04-01",
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, default=DEFAULT_END,
        help="End date (YYYY-MM-DD), default: 2026-04-21",
    )
    args = parser.parse_args()
    asyncio.run(main(args.start, args.end))
