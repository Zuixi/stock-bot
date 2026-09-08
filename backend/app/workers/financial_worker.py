"""Financial worker: fetches a stock's financial statements + metrics from TuShare.

Consumes ``stock_bot.financial.fetch`` messages. Each task ingests a single
stock (exchange + symbol), optionally restricted to a report-period window.
"""

from __future__ import annotations

import logging
import uuid

from app.core.database import async_session_factory
from app.services.financial_ingest import FinancialIngestService
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


class FinancialWorker(BaseWorker):
    """Fetches income/balance/cash-flow + fina_indicator for one stock.

    Expected payload keys
    ---------------------
    exchange : str
        Canonical exchange name, e.g. ``Shanghai_Stocks``.
    symbol : str
        Numeric stock symbol, e.g. ``600519``.
    start_date : str, optional
        ISO date string ``YYYY-MM-DD`` to restrict the report period window.
    end_date : str, optional
        ISO date string ``YYYY-MM-DD``.
    """

    queue_key = "financial.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        exchange = payload.get("exchange")
        symbol = payload.get("symbol")
        if not exchange or not symbol:
            raise ValueError("'exchange' and 'symbol' are required")

        start_date = payload.get("start_date") or ""
        end_date = payload.get("end_date") or ""

        logger.info(
            "FinancialWorker task=%s exchange=%s symbol=%s window=[%s,%s]",
            task_id, exchange, symbol, start_date or "all", end_date or "all",
        )

        service = FinancialIngestService()
        summary: dict = {"status": "completed"}
        async with async_session_factory() as db:
            result = await service.ingest_stock_financials(
                db,
                exchange=exchange,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )
            await db.commit()
            summary.update(result)
        return summary
