"""Industry metrics worker: ingest industry metric series (mock/AKShare) via queue.

Triggered by ``POST /api/v1/tasks/fetch-industry-metrics``; the scheduler runs the
same ingest service directly (dual-track, one shared service method).
"""

from __future__ import annotations

import logging
import uuid

from app.core.database import async_session_factory
from app.services import industry_metric_service
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


class IndustryMetricsWorker(BaseWorker):
    """Ingests industry metric series for a configured industry.

    Expected payload keys (all optional)
    ------------------------------------
    industry_key : str (default "pig")
    source : str (default settings.industry_data_source, "mock" | "akshare")
    months : int (default 37, 1..120 — 月度回补窗口，透传给 ingest)
    """

    queue_key = "industry_metrics.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        industry_key = payload.get("industry_key", "pig")
        source = payload.get("source")
        months = int(payload.get("months", 37))

        logger.info(
            "IndustryMetricsWorker task=%s industry=%s source=%s months=%s",
            task_id, industry_key, source or "default", months,
        )

        async with async_session_factory() as db:
            result = await industry_metric_service.ingest_industry_metrics(
                db, industry_key=industry_key, source=source, months=months
            )
            await db.commit()
        return {"status": "completed", **result}
