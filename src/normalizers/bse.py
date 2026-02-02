"""BSE record normalization to unified StockRecord schema."""

from datetime import datetime

from src.models.stock import RawBseRecord, StockRecord


def normalize_bse_record(
    raw: RawBseRecord,
    source_url: str,
    asof: datetime,
    *,
    include_raw: bool = False,
) -> StockRecord:
    """Normalize BSE raw record to unified StockRecord.

    Args:
        raw: Raw record from BSE API
        source_url: Source URL for tracking
        asof: Snapshot timestamp
        include_raw: Whether to include raw data in output

    Returns:
        Normalized StockRecord
    """
    # Extract symbol from xxzqdm
    symbol = raw.xxzqdm
    if not symbol or symbol == "-":
        raise ValueError(f"Cannot extract symbol from BSE record: {raw}")

    # Extract name from xxzqjc (stock short name)
    name = raw.xxzqjc or symbol

    # Category for BSE - use stock type if available
    category = "Beijing_Stocks"
    if raw.xxzqjb:
        category = f"Beijing_Stocks_{raw.xxzqjb}"

    # Build normalized record
    record = StockRecord(
        exchange="Beijing_Stocks",
        symbol=symbol,
        name=name,
        full_name=None,  # BSE API doesn't provide full company name
        category=category,
        list_date=raw.fxssrq,  # Listing date (fxssrq field)
        province=raw.xxssdq,  # Region/Province
        status=raw.xxzrzt,  # Transfer status
        csrc_desc=raw.xxhyzl,  # Industry description
        source_url=source_url,
        asof=asof,
        raw=raw.model_dump() if include_raw else None,
    )

    return record
