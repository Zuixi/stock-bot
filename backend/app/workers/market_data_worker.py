"""Market-data ingestion worker (manual trigger via /tasks/fetch-market-data)."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from app.core.database import async_session_factory
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


def _opt_date(v: Any) -> date | None:
    from app.services.market_data_service import _d

    return _d(str(v)) if v else None


async def _run(job: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services import announcement_service, market_data_service

    async with async_session_factory() as db:
        if job == "global_index_daily":
            result = await market_data_service.ingest_global_index_daily(db)
        elif job == "backfill_global_index":
            result = await market_data_service.backfill_global_index_history(
                db, years=int(params.get("years", 2))
            )
        elif job == "sector_moneyflow":
            result = await market_data_service.ingest_sector_moneyflow(db)
        elif job == "northbound":
            result = await market_data_service.ingest_northbound(
                db, days=int(params.get("days", 30))
            )
        elif job == "dragon_tiger":
            result = await market_data_service.ingest_dragon_tiger(
                db, trade_date=_opt_date(params.get("trade_date"))
            )
        elif job == "block_trades":
            result = await market_data_service.ingest_block_trades(
                db, trade_date=_opt_date(params.get("trade_date"))
            )
        elif job == "share_floats":
            result = await market_data_service.ingest_share_floats(
                db, days=int(params.get("days", 7))
            )
        elif job == "repurchases":
            result = await market_data_service.ingest_repurchases(
                db, days=int(params.get("days", 7))
            )
        elif job == "announcements":
            result = await announcement_service.ingest_announcements(
                db, days=int(params.get("days", 3))
            )
        else:
            return {"status": "failed", "error": f"unknown market_data type: {job}"}
        await db.commit()
    return {"status": "completed", "type": job, **result}


class MarketDataWorker(BaseWorker):
    """市场数据面采集任务（全球指数/资金流/北向/龙虎榜/大宗/解禁/回购/公告）。"""

    queue_key = "market_data.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        job = str(payload.get("type") or "")
        params = payload.get("params") or {}
        try:
            return await _run(job, {**payload, **params})
        except Exception as exc:  # noqa: BLE001
            logger.exception("market_data task %s failed", job)
            return {"status": "failed", "error": str(exc)}
