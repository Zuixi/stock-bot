"""Securities worker: ingest ETF/convertible-bond daily bars (TuShare) via queue.

Triggered by ``POST /api/v1/tasks/fetch-securities``; the scheduler runs the
same ingest service directly with a short incremental window (dual-track,
one shared service method).
"""

from __future__ import annotations

import logging
import uuid

from app.core.database import async_session_factory
from app.services import securities_service
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


class SecuritiesWorker(BaseWorker):
    """Ingests fund_etf_daily / cb_daily rows for a configured industry.

    Expected payload keys (all optional)
    ------------------------------------
    industry_key : str (default "pig")
    backfill_days : int (default 365, 1..1825 — 回补窗口，透传给 ingest)
    """

    queue_key = "securities.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        industry_key = payload.get("industry_key", "pig")
        backfill_days = int(payload.get("backfill_days", 365))

        logger.info(
            "SecuritiesWorker task=%s industry=%s backfill_days=%s",
            task_id, industry_key, backfill_days,
        )

        async with async_session_factory() as db:
            result = await securities_service.ingest_industry_securities(
                db, industry_key, backfill_days=backfill_days
            )
            await db.commit()
        return {"status": "completed", **result}
