"""SSE record normalization to unified StockRecord schema."""

from datetime import datetime

from src.models.stock import RawSseRecord, StockRecord


# Mapping of STOCK_TYPE values to category names
STOCK_TYPE_MAP = {
    "1": "主板A股",
    "2": "主板B股",
    "8": "科创板",
}


def normalize_sse_record(
    raw: RawSseRecord,
    source_url: str,
    asof: datetime,
    *,
    stock_type: str = "1",
    include_raw: bool = False,
) -> StockRecord:
    """Normalize SSE raw record to unified StockRecord.

    Args:
        raw: Raw record from SSE API
        source_url: Source URL for tracking
        asof: Snapshot timestamp
        stock_type: STOCK_TYPE filter value used in request
        include_raw: Whether to include raw data in output

    Returns:
        Normalized StockRecord
    """
    # Extract symbol (priority: A_STOCK_CODE > B_STOCK_CODE > COMPANY_CODE)
    symbol = raw.A_STOCK_CODE
    if not symbol or symbol == "-":
        symbol = raw.B_STOCK_CODE
    if not symbol or symbol == "-":
        symbol = raw.COMPANY_CODE
    if not symbol or symbol == "-":
        raise ValueError(f"Cannot extract symbol from record: {raw}")

    # Extract name (priority: SEC_NAME_CN > COMPANY_ABBR > SEC_NAME_FULL)
    name = raw.SEC_NAME_CN
    if not name:
        name = raw.COMPANY_ABBR
    if not name:
        name = raw.SEC_NAME_FULL
    if not name:
        name = symbol  # Fallback to symbol

    # Full name
    full_name = raw.FULL_NAME or raw.SEC_NAME_FULL

    # Category: combine STOCK_TYPE for grouping
    category = f"STOCK_TYPE_{stock_type}"
    if stock_type in STOCK_TYPE_MAP:
        category = f"{category}_{STOCK_TYPE_MAP[stock_type]}"

    # Build normalized record
    record = StockRecord(
        exchange="Shanghai_Stocks",
        symbol=symbol,
        name=name,
        full_name=full_name,
        category=category,
        list_date=raw.LIST_DATE,
        csrc_code=raw.CSRC_CODE,
        csrc_desc=raw.CSRC_CODE_DESC,
        province=raw.AREA_NAME_DESC,
        status=raw.STATE_CODE_STOCK,
        source_url=source_url,
        asof=asof,
        raw=raw.model_dump() if include_raw else None,
    )

    return record
