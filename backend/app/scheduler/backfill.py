"""One-off full-market financial backfill.

Run inside the scheduler container:
    python -m app.scheduler.backfill [--batch N] [--max-batches M]

Loops over the whole universe (skipping stocks that already have financials)
until none remain, honoring TuShare's global rate limit (0.5s/request) via
``backfill_financial_batch``. Safe to re-run at any time (idempotent upserts).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.core.database import async_session_factory
from app.services.financial_backfill import backfill_financial_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger(__name__)


async def _run(*, batch: int, max_batches: int | None) -> None:
    total_processed = 0
    total_failed = 0
    batch_idx = 0
    while max_batches is None or batch_idx < max_batches:
        async with async_session_factory() as db:
            result = await backfill_financial_batch(db, batch_size=batch)
            await db.commit()
        processed = result["processed"]
        failed = result["failed"]
        total_processed += processed
        total_failed += failed
        batch_idx += 1
        logger.info(
            "backfill round=%d processed=%d failed=%d (cumulative processed=%d)",
            batch_idx,
            processed,
            failed,
            total_processed,
        )
        if processed == 0:
            logger.info("No stocks missing financials — backfill complete.")
            break
    logger.info(
        "backfill finished: rounds=%d processed=%d failed=%d",
        batch_idx,
        total_processed,
        total_failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Full-market financial backfill")
    parser.add_argument("--batch", type=int, default=200, help="stocks per round (default 200)")
    parser.add_argument(
        "--max-batches", type=int, default=None, help="stop after N rounds (default: until done)"
    )
    args = parser.parse_args()
    asyncio.run(_run(batch=args.batch, max_batches=args.max_batches))


if __name__ == "__main__":
    main()
