"""Daily Basic worker: fetches fundamental indicators via TuShare daily_basic API.

Data source: TuShare Pro ``daily_basic`` API (PE, PB, market cap, turnover, etc.).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta

from app.core.database import async_session_factory
from app.services.tushare_ingest import TuShareIngestService
from app.workers.base_worker import BaseWorker

logger = logging.getLogger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class DailyBasicWorker(BaseWorker):
    """Fetches daily fundamental indicators from TuShare ``daily_basic``.

    Expected payload keys
    ---------------------
    start_date : str
        ISO date string ``YYYY-MM-DD``.
    end_date : str
        ISO date string ``YYYY-MM-DD``.
    """

    queue_key = "daily_basic.fetch"

    async def process(self, task_id: uuid.UUID, payload: dict) -> dict:
        start_date = _parse_date(payload.get("start_date"))
        end_date = _parse_date(payload.get("end_date"))

        if start_date is None or end_date is None:
            raise ValueError("'start_date' and 'end_date' are required (YYYY-MM-DD)")
        if start_date > end_date:
            raise ValueError("start_date must be <= end_date")

        logger.info(
            "DailyBasicWorker task=%s start=%s end=%s",
            task_id, start_date, end_date,
        )

        service = TuShareIngestService()
        trade_dates = await self._get_trade_dates(service, start_date, end_date)

        total_upserted = 0
        total_saved = 0
        failed_dates: list[str] = []

        async with async_session_factory() as db:
            for td in trade_dates:
                try:
                    result = await service.ingest_daily_basic(db, td)
                    total_upserted += result.get("upserted", 0)
                    total_saved += result.get("saved", 0)
                except Exception as exc:
                    logger.warning(
                        "DailyBasicWorker: failed for trade_date=%s: %s", td, exc
                    )
                    failed_dates.append(td)

        summary: dict = {
            "status": "completed",
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
        """Return list of YYYYMMDD trade dates in [start, end]."""
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
            logger.warning("DailyBasicWorker: trade_cal failed, using date range: %s", exc)

        dates: list[str] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                dates.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
        return dates
