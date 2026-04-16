"""Universe ingest helpers — TuShare-only pipeline.

All stock universe data is sourced from TuShare Pro ``stock_basic``.
Legacy AKShare / yfinance / exchange crawler paths have been removed.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

EXCHANGE_TO_CANONICAL = {
    "sse": "Shanghai_Stocks",
    "szse": "Shenzen_Stocks",
    "bse": "Beijing_Stocks",
}


def normalize_exchange(exchange: str) -> tuple[str, str]:
    key = exchange.strip().lower()
    if key in EXCHANGE_TO_CANONICAL:
        return key, EXCHANGE_TO_CANONICAL[key]
    for short, canonical in EXCHANGE_TO_CANONICAL.items():
        if canonical.lower() == key:
            return short, canonical
    raise ValueError(f"Unsupported exchange: {exchange}")


def parse_listing_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_builtin(value: Any) -> Any:
    """Convert numpy/pandas scalars to plain Python types."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool, dict, list)):
        if isinstance(value, dict):
            return {str(k): _to_builtin(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_to_builtin(v) for v in value]
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    return str(value)


def merge_detail_into_record(record: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return record
    merged = dict(record)
    merged["detail"] = detail
    return merged
