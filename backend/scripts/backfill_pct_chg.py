"""Backfill daily_quotes.pct_chg/pre_close from TuShare ``daily`` (authoritative).

Usage:
    cd backend && uv run python scripts/backfill_pct_chg.py --years 3
    cd backend && uv run python scripts/backfill_pct_chg.py --limit 5   # smoke test

The stored ``pct_chg``/``pre_close`` came only from TuShare's native fields until
now being persisted; this reconciles historical rows that predate Task 2.2.

Authoritative source only: every value is re-fetched from TuShare by trade_date.
Do NOT replace this with ``LAG(close)`` over ``daily_quotes`` — on ex-dividend
days the reference close is the adjusted previous close, so a window function
would produce wrong pct_chg precisely there. TuShare's native values already
account for corporate actions.

Idempotent: rows are updated in place with the same source values, so re-running
the same window converges and can be safely interrupted/resumed by date.

Cost: ~250 trade dates per year ⇒ ~750 ``fetch_daily`` calls for the default
3-year window. Plan for TuShare quota / run off-hours. The client already
throttles and retries.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import text

from app.core.database import async_session_factory
from app.core.providers.tushare_client import get_tushare_client
from app.repositories import quote_repo
from app.services.tushare_ingest import TuShareIngestService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _load_trade_dates(start: date, end: date, limit: int) -> list[date]:
    stmt = text(
        "SELECT DISTINCT trade_date FROM daily_quotes "
        "WHERE trade_date >= :start AND trade_date <= :end "
        "ORDER BY trade_date DESC"
    )
    async with async_session_factory() as db:
        dates = (
            (await db.execute(stmt, {"start": start, "end": end})).scalars().all()
        )
    if limit > 0:
        dates = dates[:limit]
    return list(dates)


async def main(years: int, limit: int, start_date: date | None, end_date: date | None) -> None:
    client = get_tushare_client()
    service = TuShareIngestService(client=client, data_saver=None)

    end = end_date or date.today()
    start = start_date or (end - timedelta(days=365 * years))

    dates = await _load_trade_dates(start, end, limit)
    if not dates:
        logger.warning("No daily_quotes trade_date in [%s, %s] — nothing to backfill", start, end)
        return

    logger.info(
        "Backfilling pct_chg for %d trade_date(s): %s … %s", len(dates), dates[-1], dates[0]
    )

    async with async_session_factory() as db:
        id_map = await service._build_stock_id_map(db)

    total_updated = 0
    for trade_date in dates:
        ymd = trade_date.strftime("%Y%m%d")
        df = await client.fetch_daily(trade_date=ymd)
        if df.empty:
            logger.warning("%s: TuShare returned no rows, skipped", ymd)
            continue

        rows: list[tuple[int, float | None, float | None]] = []
        for record in df.to_dict("records"):
            stock_id = id_map.get(str(record.get("ts_code", "")))
            if stock_id is None or record.get("pct_chg") is None:
                continue
            rows.append((stock_id, record.get("pre_close"), record.get("pct_chg")))

        async with async_session_factory() as db:
            updated = await quote_repo.update_pct_chg_for_date(db, trade_date, rows)
            await db.commit()
        total_updated += updated
        logger.info("%s: updated=%d", ymd, updated)

    logger.info(
        "Backfill finished: %d trade_date(s), %d row(s) updated", len(dates), total_updated
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=3, help="Trailing window size (default 3)")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only the N most recent trade dates (0 = all; use 3-5 for a smoke test)",
    )
    parser.add_argument(
        "--start-date", type=date.fromisoformat, default=None, help="Window start (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=date.fromisoformat, default=None, help="Window end (YYYY-MM-DD)"
    )
    args = parser.parse_args()
    asyncio.run(main(args.years, args.limit, args.start_date, args.end_date))
