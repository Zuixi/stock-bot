"""Stock request/response schemas."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class SwChainNode(BaseModel):
    """One node of a stock's Shenwan industry chain (L1→L2→L3)."""

    level: int
    code: str
    name: str


class StockOut(BaseModel):
    id: int
    exchange: str
    symbol: str
    name: str
    area: str | None = None
    industry: str | None = None
    full_name: str | None = None
    enname: str | None = None
    cnspell: str | None = None
    market: str | None = None
    curr_type: str | None = None
    list_status: str | None = None
    list_date: date | None = None
    delist_date: date | None = None
    is_hs: str | None = None
    act_name: str | None = None
    act_ent_type: str | None = None
    # Legacy fields
    category: str
    csrc_code: str | None = None
    csrc_desc: str | None = None
    province: str | None = None
    status: str | None = None
    detail: dict | None = None
    asof: datetime

    model_config = {"from_attributes": True}


class StockEnrichedOut(StockOut):
    """StockOut augmented with latest quote and daily_basic indicators."""

    # Quote fields
    latest_price: float | None = None
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume: int | None = None
    amount: float | None = None
    # Daily basic fields
    pe_ttm: float | None = None
    pb: float | None = None
    total_mv: float | None = None
    circ_mv: float | None = None
    turnover_rate: float | None = None
    # 最新行情的 trade_date —— 行情展示口径的"数据截至"；
    # stocks.asof 是名录元数据 ingest 时间，属另一条管道，不得混用
    latest_quote_date: date | None = None
    # Stock's own SW industry chain L1→L2→L3 (empty when unmapped)
    sw_chain: list[SwChainNode] = Field(default_factory=list)


class StockListParams(BaseModel):
    exchange: str | None = None
    category: str | None = None
    keyword: str | None = None
    sort_by: str | None = None
    sort_order: str | None = None  # "asc" | "desc"


class ExchangeOut(BaseModel):
    code: str
    name_cn: str


class CategoryOut(BaseModel):
    exchange: str
    category: str
    count: int


class UserTagOut(BaseModel):
    """Single user-defined tag attached to a stock."""

    tag_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TagSummary(BaseModel):
    """Aggregated summary of a tag across all stocks."""

    tag_name: str
    stock_count: int
