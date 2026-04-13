"""Quotes worker: fetches daily OHLCV data via CNINFO p_sysapi1015 and persists to PostgreSQL.

Data source: CNINFO WebAPI ``p_sysapi1015`` (free, mcode auth).
See: backend/docs/cninfo_api.md
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from app.core.database import async_session_factory
from app.core.providers.cninfo_client import get_cninfo_client
from app.models.quote import DailyQuote
from app.repositories import quote_repo, stock_repo
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)

_EXCHANGE_ALIASES: dict[str, str] = {
    "sse": "Shanghai_Stocks",
    "szse": "Shenzen_Stocks",
    "bse": "Beijing_Stocks",
    "shanghai_stocks": "Shanghai_Stocks",
    "shenzen_stocks": "Shenzen_Stocks",
    "beijing_stocks": "Beijing_Stocks",
}


def _normalise_exchange(raw: str) -> str:
    return _EXCHANGE_ALIASES.get(raw.lower().strip(), raw)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            from datetime import datetime

            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class QuotesWorker(BaseWorker):
    """Fetches daily OHLCV quotes from CNINFO and persists them to ``daily_quotes``.

    Expected payload keys
    ---------------------
    exchange : str
        Exchange canonical name or alias (e.g. ``"Shanghai_Stocks"`` or ``"sse"``).
    symbols : list[str]
        One or more stock codes to fetch (e.g. ``["600519", "000001"]``).
    start_date : str
        ISO date string ``YYYY-MM-DD`` for the start of the range.
    end_date : str
        ISO date string ``YYYY-MM-DD`` for the end of the range.
    """

    queue_key = "quotes.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        exchange_raw: str = payload.get("exchange") or ""
        symbols: list[str] = payload.get("symbols") or []
        start_date = _parse_date(payload.get("start_date"))
        end_date = _parse_date(payload.get("end_date"))

        if not exchange_raw:
            raise ValueError("'exchange' is required in payload")
        if not symbols:
            raise ValueError("'symbols' must be a non-empty list")
        if start_date is None or end_date is None:
            raise ValueError("'start_date' and 'end_date' are required (YYYY-MM-DD)")
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")

        exchange = _normalise_exchange(exchange_raw)

        logger.info(
            "QuotesWorker task=%s exchange=%s symbols=%s start=%s end=%s",
            task_id, exchange, symbols, start_date, end_date,
        )

        client = get_cninfo_client()
        total_upserted = 0
        failed_symbols: list[str] = []

        async with async_session_factory() as db:
            for symbol in symbols:
                stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
                if stock is None:
                    logger.warning(
                        "QuotesWorker: stock not found exchange=%s symbol=%s, skipping",
                        exchange, symbol,
                    )
                    failed_symbols.append(symbol)
                    continue

                try:
                    raw_quotes = await client.get_daily_quotes_range(
                        symbol, start_date, end_date
                    )
                except Exception as exc:
                    logger.warning(
                        "QuotesWorker: CNINFO fetch failed symbol=%s: %s", symbol, exc
                    )
                    failed_symbols.append(symbol)
                    continue

                if not raw_quotes:
                    logger.info(
                        "QuotesWorker: no data returned for symbol=%s range=%s..%s",
                        symbol, start_date, end_date,
                    )
                    continue

                orm_quotes = [
                    DailyQuote(
                        stock_id=stock.id,
                        trade_date=q["trade_date"],
                        open=q.get("open"),
                        high=q.get("high"),
                        low=q.get("low"),
                        close=q["close"],
                        volume=q.get("volume"),
                        amount=q.get("amount"),
                        adj_factor=None,
                        source=q.get("source", "cninfo:p_sysapi1015"),
                    )
                    for q in raw_quotes
                    if q.get("close") is not None
                ]

                upserted = await quote_repo.upsert_quotes(db, orm_quotes)
                await db.commit()
                total_upserted += upserted
                logger.info(
                    "QuotesWorker: upserted %d records for symbol=%s", upserted, symbol
                )

        result: dict = {
            "status": "completed",
            "exchange": exchange,
            "symbols_requested": len(symbols),
            "symbols_failed": len(failed_symbols),
            "total_upserted": total_upserted,
        }
        if failed_symbols:
            result["failed_symbols"] = failed_symbols

        return result
