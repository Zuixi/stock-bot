"""SZSE record normalization to unified StockRecord schema."""

from __future__ import annotations

import re
from datetime import datetime

from src.models.stock import RawSzseRecord, StockRecord


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str | None) -> str | None:
    if not text:
        return text
    return _TAG_RE.sub("", text).strip()


def normalize_szse_record(
    raw: RawSzseRecord,
    source_url: str,
    asof: datetime,
    *,
    include_raw: bool = False,
) -> StockRecord:
    """Normalize SZSE raw record to unified StockRecord.

    Args:
        raw: Raw record from SZSE API
        source_url: Source URL for tracking
        asof: Snapshot timestamp
        include_raw: Whether to include raw data in output

    Returns:
        Normalized StockRecord
    """
    # Extract symbol
    symbol = raw.agdm or raw.bgdm
    if not symbol or symbol == "-":
        raise ValueError(f"Cannot extract symbol from SZSE record: {raw}")

    # Extract name
    name = _strip_html(raw.agjc) or _strip_html(raw.bgjc) or symbol

    # Category: use board info if available
    category = "Shenzen_Stocks"
    if raw.bk:
        category = f"Shenzen_Stocks_{raw.bk}"

    # Listing date
    list_date = raw.agssrq or raw.bgssrq

    # Industry
    csrc_desc = _strip_html(raw.sshymc) if raw.sshymc else None

    record = StockRecord(
        exchange="Shenzen_Stocks",
        symbol=str(symbol),
        name=name,
        full_name=None,
        category=category,
        list_date=list_date,
        csrc_desc=csrc_desc,
        status=None,
        source_url=source_url,
        asof=asof,
        raw=raw.model_dump() if include_raw else None,
    )

    return record
