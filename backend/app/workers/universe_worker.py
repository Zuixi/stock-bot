"""Universe worker: fetches stock list and persists to PostgreSQL."""

import logging
import uuid
from datetime import UTC, datetime

from app.core.database import async_session_factory
from app.core.redis import CacheClient, get_redis_pool
from app.models.stock import Stock, StockHistory
from app.repositories import stock_repo
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


class UniverseWorker(BaseWorker):
    queue_key = "universe.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        exchange = payload.get("exchange", "").lower()
        stock_type = payload.get("stock_type")

        logger.info("Fetching universe: exchange=%s, stock_type=%s", exchange, stock_type)

        # Delegate to the existing CLI fetcher infrastructure
        records = await self._fetch_records(exchange, stock_type)

        async with async_session_factory() as db:
            redis = await get_redis_pool()
            cache = CacheClient(redis)

            inserted = 0
            for record in records:
                stock = Stock(
                    exchange=record["exchange"],
                    symbol=record["symbol"],
                    name=record["name"],
                    full_name=record.get("full_name"),
                    category=record["category"],
                    list_date=record.get("list_date"),
                    csrc_code=record.get("csrc_code"),
                    csrc_desc=record.get("csrc_desc"),
                    province=record.get("province"),
                    status=record.get("status"),
                    asof=datetime.now(UTC),
                )
                history = StockHistory(
                    exchange=stock.exchange,
                    symbol=stock.symbol,
                    name=stock.name,
                    full_name=stock.full_name,
                    category=stock.category,
                    list_date=stock.list_date,
                    csrc_code=stock.csrc_code,
                    csrc_desc=stock.csrc_desc,
                    province=stock.province,
                    status=stock.status,
                    source_url=record.get("source_url"),
                    asof=stock.asof,
                    raw=record.get("raw"),
                )
                await stock_repo.upsert_stock(db, stock)
                await stock_repo.insert_stock_history(db, history)
                inserted += 1

            await db.commit()
            await cache.delete_pattern("stock:list:*")
            await cache.delete_pattern("stock:categories:*")

        logger.info("Universe worker done: %d records for exchange=%s", inserted, exchange)
        return {"inserted": inserted, "exchange": exchange}

    async def _fetch_records(self, exchange: str, stock_type: str | None) -> list[dict]:
        """Bridge to the existing src/ fetcher infrastructure."""
        import sys
        from pathlib import Path

        project_root = Path(__file__).parents[4]
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

        from datetime import datetime as dt

        try:
            if exchange == "sse":
                from src.fetchers.sse.fetcher import SseFetcher
                from src.normalizers.sse import normalize_sse_record
                from src.config import load_config
                from src.models.config import SseConfig

                cfg = SseConfig.from_yaml(load_config("sse"))
                fetcher = SseFetcher(cfg)
                asof = dt.now(UTC)
                records = []
                async for raw, url, ts in fetcher.iter_raw_records(asof):
                    rec = normalize_sse_record(raw, url, ts)
                    d = rec.model_dump()
                    d["source_url"] = url
                    records.append(d)
                return records

            elif exchange == "bse":
                from src.fetchers.bse.fetcher import BseFetcher
                from src.normalizers.bse import normalize_bse_record
                from src.config import load_config
                from src.models.config import BseConfig

                cfg = BseConfig.from_yaml(load_config("bse"))
                fetcher = BseFetcher(cfg)
                asof = dt.now(UTC)
                records = []
                async for raw, url, ts in fetcher.iter_raw_records(asof):
                    rec = normalize_bse_record(raw, url, ts)
                    d = rec.model_dump()
                    d["source_url"] = url
                    records.append(d)
                return records

        except Exception as e:
            logger.warning("Fetcher not available (%s), returning empty list", e)

        return []
