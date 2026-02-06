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


class RawBseRecord(BaseModel):
    """Raw record from BSE nqxxController/nqxxCnzq.do API.

    Field names match BSE API response exactly.
    Based on browser network capture of https://www.bse.cn/nq/listedcompany.html
    """

    # Stock identification
    xxzqdm: str | None = Field(default=None, description="股票代码")
    xxzqjc: str | None = Field(default=None, description="股票简称")
    xxzqjb: str | None = Field(default=None, description="股票级别/类型")

    # Location info
    xxssdq: str | None = Field(default=None, description="所属地区")

    # Listing info
    fxssrq: str | None = Field(default=None, description="发行上市日期")

    # Industry
    xxhyzl: str | None = Field(default=None, description="行业种类")

    # Other fields - use str | int | float to handle API response types
    xxcqcx: str | None = Field(default=None, description="是否常续询价")
    xxcfgbz: str | None = Field(default=None, description="是否采用特殊标准")
    xxfcbj: str | None = Field(default=None, description="发行定价方式")
    xxgprq: str | None = Field(default=None, description="改革日期")
    xxgxsj: str | None = Field(default=None, description="更新日期")
    xxhbzl: str | None = Field(default=None, description="货币种类")
    xxisin: str | None = Field(default=None, description="ISIN码")
    xxjsfl: str | float | None = Field(default=None, description="计算费率")
    xxjsrq: str | None = Field(default=None, description="计算日期")
    xxmbxl: str | int | None = Field(default=None, description="每股面值")
    xxmgmz: str | int | None = Field(default=None, description="每股面值单位")
    xxqtyw: str | None = Field(default=None, description="其他业务类型")
    xxzgb: str | int | None = Field(default=None, description="总股本")
    xxzhbl: str | int | None = Field(default=None, description="占流通比例")
    xxzrdw: str | int | None = Field(default=None, description="转让单位")
    xxzrlx: str | None = Field(default=None, description="转让类型")
    xxzrzt: str | None = Field(default=None, description="转让状态")
    xxzsssl: str | int | None = Field(default=None, description="转让市值")

    class Config:
        extra = "allow"  # Allow additional fields not explicitly defined


class RawSzseRecord(BaseModel):
    """Raw record from SZSE ShowReport/data API.

    Field names match SZSE API response (lowercase keys).
    """

    # Common fields for A-share/CDR/AB list
    bk: str | None = Field(default=None, description="板块")
    agdm: str | None = Field(default=None, description="A股/证券代码")
    agjc: str | None = Field(default=None, description="A股/证券简称")
    agssrq: str | None = Field(default=None, description="上市日期")
    agzgb: str | None = Field(default=None, description="总股本/总份数")
    agltgb: str | None = Field(default=None, description="流通股本/流通份数")
    zhbl: str | None = Field(default=None, description="CDR对应比例")
    sshymc: str | None = Field(default=None, description="所属行业")
    ylbz: str | None = Field(default=None, description="未盈利")
    sfjybjqcy: str | None = Field(default=None, description="具有表决权差异安排")
    gskzjglx: str | None = Field(default=None, description="具有协议控制架构")

    # B-share fields (if present)
    bgdm: str | None = Field(default=None, description="B股代码")
    bgjc: str | None = Field(default=None, description="B股简称")
    bgssrq: str | None = Field(default=None, description="B股上市日期")
    bgzgb: str | None = Field(default=None, description="B股总股本")
    bgltgb: str | None = Field(default=None, description="B股流通股本")

    class Config:
        extra = "allow"  # Allow additional fields not explicitly defined
