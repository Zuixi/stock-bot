"""Universe worker: fetches stock list + details and persists to PostgreSQL."""

import logging
import uuid
from datetime import UTC, datetime

from app.core.database import async_session_factory
from app.core.redis import CacheClient, get_redis_pool
from app.models.stock import Stock, StockHistory
from app.repositories import stock_repo
from app.schemas.task import FetchUniverseRequest
from app.services.universe_ingest import (
    UniverseDataProvider,
    merge_detail_into_record,
    parse_listing_date,
)
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


class UniverseWorker(BaseWorker):
    queue_key = "universe.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        req = FetchUniverseRequest.model_validate(payload)
        exchange = req.exchange
        stock_type = req.stock_type
        source = req.source
        detail_source = req.detail_source
        include_details = req.include_details
        detail_retry = req.detail_retry
        detail_sleep_min = req.detail_sleep_min
        detail_sleep_max = req.detail_sleep_max

        logger.info(
            "Fetching universe: exchange=%s stock_type=%s source=%s include_details=%s detail_source=%s",
            exchange,
            stock_type,
            source,
            include_details,
            detail_source,
        )

        provider = UniverseDataProvider(
            detail_sleep_range=(detail_sleep_min, detail_sleep_max),
            detail_retry=detail_retry,
        )

        records = await provider.fetch_universe_records(exchange, stock_type, source=source)
        detail_ok = 0
        detail_fail = 0
        if include_details:
            enriched_records: list[dict] = []
            for record in records:
                symbol = str(record.get("symbol", "")).strip()
                exchange_name = str(record.get("exchange", "")).strip()
                detail = None
                if symbol and exchange_name:
                    detail = await provider.fetch_stock_detail(
                        exchange_name, symbol, detail_source=detail_source
                    )
                if detail:
                    detail_ok += 1
                else:
                    detail_fail += 1
                enriched_records.append(merge_detail_into_record(record, detail))
                await provider.sleep_between_detail_requests()
            records = enriched_records

        async with async_session_factory() as db:
            redis = await get_redis_pool()
            cache = CacheClient(redis)

            inserted = 0
            skipped = 0
            batch_size = 250
            for record in records:
                if not all(
                    key in record and str(record.get(key, "")).strip()
                    for key in ("exchange", "symbol", "name", "category")
                ):
                    skipped += 1
                    logger.warning("Skipping invalid universe record: %s", record)
                    continue
                stock = Stock(
                    exchange=record["exchange"],
                    symbol=record["symbol"],
                    name=record["name"],
                    full_name=record.get("full_name"),
                    category=record["category"],
                    list_date=parse_listing_date(record.get("list_date")),
                    csrc_code=record.get("csrc_code"),
                    csrc_desc=record.get("csrc_desc"),
                    province=record.get("province"),
                    status=record.get("status"),
                    detail=record.get("detail"),
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
                    detail=record.get("detail"),
                    source_url=record.get("source_url"),
                    asof=stock.asof,
                    raw=record.get("raw") or record,
                )
                await stock_repo.upsert_stock(db, stock)
                await stock_repo.insert_stock_history(db, history)
                inserted += 1
                if inserted % batch_size == 0:
                    await db.commit()

            await db.commit()
            await cache.delete_pattern("stock:list:*")
            await cache.delete_pattern("stock:categories:*")

        logger.info("Universe worker done: %d records for exchange=%s", inserted, exchange)
        return {
            "inserted": inserted,
            "skipped": skipped,
            "exchange": exchange,
            "detail_success": detail_ok,
            "detail_failed": detail_fail,
            "source": source,
            "detail_source": detail_source,
        }
