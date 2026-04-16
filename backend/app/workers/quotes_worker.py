"""Quotes worker: fetches daily OHLCV data via TuShare and persists to PostgreSQL.

Data source: TuShare Pro ``daily`` API.
Best practice: fetch by ``trade_date`` (entire market per call) rather than
looping over individual ``ts_code`` values.

See: docs/references/tushare/A股历史日线.md, docs/references/tushare/reference.md
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta

from app.core.database import async_session_factory
from app.services.tushare_ingest import TuShareIngestService
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
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class QuotesWorker(BaseWorker):
    """Fetches daily OHLCV quotes from TuShare and persists them to ``daily_quotes``.

    Expected payload keys
    ---------------------
    exchange : str
        Exchange canonical name or alias (e.g. ``"Shanghai_Stocks"`` or ``"sse"``).
        Currently unused for TuShare ``daily(trade_date=...)`` which returns all markets.
    symbols : list[str]
        Optional. If empty, fetches entire market for each trade date.
    start_date : str
        ISO date string ``YYYY-MM-DD`` for the start of the range.
    end_date : str
        ISO date string ``YYYY-MM-DD`` for the end of the range.
    """

    queue_key = "quotes.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        exchange_raw: str = payload.get("exchange") or ""
        start_date = _parse_date(payload.get("start_date"))
        end_date = _parse_date(payload.get("end_date"))

        if not exchange_raw:
            raise ValueError("'exchange' is required in payload")
        if start_date is None or end_date is None:
            raise ValueError("'start_date' and 'end_date' are required (YYYY-MM-DD)")
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")

        exchange = _normalise_exchange(exchange_raw)

        logger.info(
            "QuotesWorker task=%s exchange=%s start=%s end=%s",
            task_id, exchange, start_date, end_date,
        )

        service = TuShareIngestService()
        trade_dates = await self._get_trade_dates(service, start_date, end_date)

        total_upserted = 0
        total_saved = 0
        failed_dates: list[str] = []

        async with async_session_factory() as db:
            for td in trade_dates:
                try:
                    result = await service.ingest_daily_quotes(db, td)
                    total_upserted += result.get("upserted", 0)
                    total_saved += result.get("saved", 0)
                except Exception as exc:
                    logger.warning(
                        "QuotesWorker: failed for trade_date=%s: %s", td, exc
                    )
                    failed_dates.append(td)

        summary: dict = {
            "status": "completed",
            "exchange": exchange,
            "trade_dates_processed": len(trade_dates),
            "trade_dates_failed": len(failed_dates),
            "total_upserted": total_upserted,
            "total_saved": total_saved,
        }
        if failed_dates:
            summary["failed_dates"] = failed_dates

        return summary

    async def _get_trade_dates(
        self,
        service: TuShareIngestService,
        start: date,
        end: date,
    ) -> list[str]:
        """Return list of YYYYMMDD trade dates in [start, end] using TuShare trade_cal."""
        try:
            df = await service.client.fetch_trade_cal(
                exchange="SSE",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                is_open="1",
            )
            if not df.empty:
                return sorted(df["cal_date"].astype(str).tolist())
        except Exception as exc:
            logger.warning("QuotesWorker: trade_cal failed, using date range: %s", exc)

        dates: list[str] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                dates.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
        return dates
