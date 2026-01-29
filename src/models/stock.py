"""Stock record models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ExchangeName = Literal["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]


class StockRecord(BaseModel):
    """Normalized stock record for universe storage.

    This is the unified schema across all exchanges.
    """

    exchange: ExchangeName = Field(description="Exchange identifier")
    symbol: str = Field(description="Stock code (e.g., '600105')")
    name: str = Field(description="Stock short name (e.g., '永鼎股份')")
    full_name: str | None = Field(default=None, description="Full company name")
    category: str = Field(description="Stock category/type from exchange (e.g., 'STOCK_TYPE_1')")
    
    # Additional metadata
    list_date: str | None = Field(default=None, description="Listing date (YYYYMMDD)")
    csrc_code: str | None = Field(default=None, description="CSRC industry code")
    csrc_desc: str | None = Field(default=None, description="CSRC industry description")
    province: str | None = Field(default=None, description="Registration province")
    status: str | None = Field(default=None, description="Company/stock status code")
    
    # Source tracking
    source_url: str = Field(description="Source URL or request identifier")
    asof: datetime = Field(description="Snapshot timestamp")
    
    # Optional: preserve raw data for debugging
    raw: dict | None = Field(default=None, description="Original record from exchange")


class RawSseRecord(BaseModel):
    """Raw record from SSE commonQuery.do API.

    Field names match SSE API response exactly.
    """

    A_STOCK_CODE: str | None = Field(default=None)
    B_STOCK_CODE: str | None = Field(default=None)
    COMPANY_CODE: str | None = Field(default=None)
    SEC_NAME_CN: str | None = Field(default=None, description="证券简称")
    SEC_NAME_FULL: str | None = Field(default=None, description="证券全称")
    COMPANY_ABBR: str | None = Field(default=None, description="公司简称")
    FULL_NAME: str | None = Field(default=None, description="公司全称")
    FULL_NAME_IN_ENGLISH: str | None = Field(default=None)
    COMPANY_ABBR_EN: str | None = Field(default=None)
    
    STOCK_TYPE: str | None = Field(default=None, description="股票类型")
    LIST_BOARD: str | None = Field(default=None, description="上市板块")
    LIST_DATE: str | None = Field(default=None, description="上市日期")
    DELIST_DATE: str | None = Field(default=None, description="退市日期")
    
    CSRC_CODE: str | None = Field(default=None, description="证监会行业代码")
    CSRC_CODE_DESC: str | None = Field(default=None, description="证监会行业描述")
    AREA_NAME: str | None = Field(default=None, description="地区代码")
    AREA_NAME_DESC: str | None = Field(default=None, description="地区名称")
    
    STATE_CODE: str | None = Field(default=None, description="公司状态")
    STATE_CODE_STOCK: str | None = Field(default=None, description="股票状态")
    PRODUCT_STATUS: str | None = Field(default=None)
    
    NUM: str | None = Field(default=None, description="序号")

    class Config:
        extra = "allow"  # Allow additional fields not explicitly defined
