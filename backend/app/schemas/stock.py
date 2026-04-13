"""Stock request/response schemas."""

from datetime import date, datetime

from pydantic import BaseModel


class StockOut(BaseModel):
    id: int
    exchange: str
    symbol: str
    name: str
    full_name: str | None
    category: str
    list_date: date | None
    csrc_code: str | None
    csrc_desc: str | None
    province: str | None
    status: str | None
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
