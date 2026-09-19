"""概念板块读接口响应模型（口径见 plans/2026-09-18-concept-boards-and-new-stocks.md §2.2/§2.3）。

两条字段级约定：

1. 资金流列（`main_net_inflow` / `main_net_ratio` / `lead_stock_*`）全部可空：东财快照一天只
   覆盖当日主力流入 Top-100 的震荡集，**缺失必须留 `None`（= `null`），绝不 0 填充**——0 会被
   前端读成"主力零净流入"而不是"没有这一行数据"。
2. `price_source` 与 `flow_source` 是两个独立字段（`local_agg` / `em_clist`）：价格来自本地
   成分聚合，资金流来自东财快照，两个源不允许混算成一个"综合"指标。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class BoardLeaderOut(BaseModel):
    """板块内涨幅前 2 成分（`pct_chg DESC NULLS LAST, symbol ASC` 的确定性取前二）。"""

    symbol: str
    name: str
    change_percent: float | None = None  # 无行情成分占位为 null（卡片侧会过滤掉）


class BoardItemOut(BaseModel):
    """§2.3 口径的板块行 + 东财资金流快照字段（快照缺行时全为 None）。"""

    board_code: str
    board_name: str
    member_count: int  # 本地 concept_members 行数（权威；不是采集时刻的东财 total）
    unresolved_count: int  # stock_id IS NULL 的成分数（名录滞后暴露面）
    priced_count: int  # 当日 pct_chg 非空的成分数（= up+flat+down）
    up_count: int
    flat_count: int
    down_count: int
    avg_pct: float | None = None  # 分母只有非空 pct_chg（n ≠ member_count），UI 注明
    main_net_inflow: float | None = None  # 元
    main_net_ratio: float | None = None  # %
    lead_stock_name: str | None = None
    lead_stock_code: str | None = None
    lead_stock_pct: float | None = None
    leaders: list[BoardLeaderOut] = Field(default_factory=list)


class ConceptListOut(BaseModel):
    """`GET /api/v1/concepts`（§2.2）：分页 items + 数据集级元信息。"""

    as_of: date | None  # 行情最新交易日（无行情 → None + degraded_reason="no_quotes"）
    # 全体成分行的 max(last_seen_on)：全集口径，不保证是本页这些板块的日期；无成分 → "no_members"
    membership_as_of: date | None
    price_source: Literal["local_agg"]
    flow_source: Literal["em_clist"] | None  # 该 as_of 的东财快照是否有行（数据集级，非本页命中数）
    total: int  # 启用板块总数（与分页无关）
    degraded_reason: str | None = None
    items: list[BoardItemOut] = Field(default_factory=list)
