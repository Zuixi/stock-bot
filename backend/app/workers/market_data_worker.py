"""Market-data ingestion worker (manual trigger via /tasks/fetch-market-data)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, get_args

from app.core.database import async_session_factory
from app.schemas.task import MarketDataJobType
from app.workers.base_worker import BaseWorker

_KNOWN_TYPES: frozenset[str] = frozenset(get_args(MarketDataJobType))


def _opt_date(v: Any) -> date | None:
    from app.services.market_data_service import _d

    return _d(str(v)) if v else None


async def _run(job: str, params: dict[str, Any]) -> dict[str, Any]:
    from app.services import (
        announcement_service,
        concept_service,
        limit_up_service,
        market_data_service,
    )

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
        elif job == "market_moneyflow":
            result = await market_data_service.ingest_market_moneyflow_daily(
                db, days=int(params.get("days", 10))
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
        elif job == "price_limits":
            result = await market_data_service.ingest_stock_price_limits(
                db,
                trade_date=_opt_date(params.get("trade_date")),
                window_days=int(params.get("window_days", 20)),
            )
        elif job == "sentiment_daily":
            # 盘后落库不需要缓存（cache=None）：避免命中 Redis JSON 后 as_of 变 str。
            result = await limit_up_service.persist_snapshot(db, None)
        elif job == "concept_members":
            result = await concept_service.ingest_concept_members(db)
        elif job == "reconcile":
            from app.services import reconciliation_service  # noqa: PLC0415

            # 手动全量对账：幂等，无缺口时零 TuShare 请求
            result = await reconciliation_service.reconcile_market_data(db)
        else:
            # Defensive: unreachable via process() — the type is validated there.
            return {"status": "failed", "error": f"unknown market_data type: {job}"}
        await db.commit()
    return {"status": "completed", "type": job, **result}


class MarketDataWorker(BaseWorker):
    """市场数据面采集任务（全球指数/资金流/北向/龙虎榜/大宗/解禁/回购/公告）。"""

    queue_key = "market_data.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        """Execute one ingest job; service exceptions propagate (BaseWorker marks the
        task failed). Expected payload keys (top-level or nested in "params"):
        global_index_daily/sector_moneyflow/concept_members — none;
        backfill_global_index — years(=2);
        northbound/share_floats/repurchases — days(=30/7/7); dragon_tiger/block_trades —
        trade_date(yyyymmdd, optional); announcements — days(=3). Unknown types return
        a failed dict without touching the DB.
        """
        job = str(payload.get("type") or "")
        if job not in _KNOWN_TYPES:
            return {"status": "failed", "error": f"unknown market_data type: {job}"}
        params = payload.get("params") or {}
        return await _run(job, {**payload, **params})
