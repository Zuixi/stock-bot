"""Universe worker: fetches stock list from TuShare and persists to PostgreSQL."""

import logging
import uuid

from app.core.database import async_session_factory
from app.core.redis import CacheClient, get_redis_pool
from app.schemas.task import FetchUniverseRequest
from app.services.tushare_ingest import TuShareIngestService
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


class UniverseWorker(BaseWorker):
    queue_key = "universe.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        req = FetchUniverseRequest.model_validate(payload)
        exchange = req.exchange

        logger.info("Fetching universe via TuShare: exchange=%s", exchange)

        try:
            service = TuShareIngestService()
            async with async_session_factory() as db:
                redis = await get_redis_pool()
                cache = CacheClient(redis)

                result = await service.ingest_stock_universe(
                    db, exchange,
                    enrich_company=req.include_details,
                )

                await cache.delete_pattern("stock:list:*")
                await cache.delete_pattern("stock:categories:*")

            return result
        except Exception as exc:
            logger.error("TuShare ingest failed: %s", exc, exc_info=True)
            return {
                "inserted": 0, "skipped": 0,
                "exchange": exchange, "source": "tushare",
                "error": str(exc),
            }
