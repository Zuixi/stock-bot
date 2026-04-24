"""Stock request/response schemas."""

from datetime import date, datetime

from pydantic import BaseModel


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


class StockListParams(BaseModel):
    exchange: str | None = None
    category: str | None = None
    keyword: str | None = None


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
