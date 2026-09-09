"""Full-market financial backfill — processes one bounded batch per call.

Shares the same ``FinancialIngestService.ingest_stock_financials`` method the
FinancialWorker uses, so manual (MQ) and scheduled (APScheduler) triggers stay
on one code path. TuShare throttling lives in ``RateLimitedSyncProvider``
(0.5s/request, ~120 req/min), so a sequential per-stock loop self-limits.

Resumability: a stock is considered "done" once it has >=1 financial report
version. Each run skips done stocks and fills up to ``batch_size`` of the rest,
so repeated scheduled runs drain the whole universe over time (new listings get
picked up automatically on the next run).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financial import FinancialReportVersion
from app.models.stock import Stock
from app.services.financial_ingest import FinancialIngestService

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 200


async def list_stocks_missing_financials(db: AsyncSession, *, limit: int) -> list[Stock]:
    """Return up to ``limit`` stocks that have no financial report version yet."""
    done: set[int] = set(
        (await db.execute(select(FinancialReportVersion.stock_id).distinct())).scalars()
    )
    all_stocks = (await db.execute(select(Stock).order_by(Stock.id))).scalars().all()
    missing = [s for s in all_stocks if s.id not in done]
    return missing[:limit]


async def backfill_financial_batch(
    db: AsyncSession, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> dict:
    """Ingest financial data for up to ``batch_size`` stocks lacking it."""
    pending = await list_stocks_missing_financials(db, limit=batch_size)
    if not pending:
        return {"processed": 0, "failed": 0, "message": "no stocks missing financials"}

    service = FinancialIngestService()
    failed: list[str] = []
    for stock in pending:
        try:
            await service.ingest_stock_financials(
                db,
                exchange=stock.exchange,
                symbol=stock.symbol,
            )
        except Exception as exc:  # noqa: BLE001 — per-stock isolation
            logger.exception(
                "financial backfill failed for %s/%s: %s",
                stock.exchange,
                stock.symbol,
                exc,
            )
            failed.append(f"{stock.exchange}/{stock.symbol}")
            await db.rollback()
            continue
        await db.commit()

    count = len(pending)
    logger.info(
        "financial backfill batch: processed=%d failed=%d",
        count,
        len(failed),
    )
    if failed:
        logger.warning("failed stocks: %s", ", ".join(failed))
    return {"processed": count, "failed": len(failed), "failed_symbols": failed}
