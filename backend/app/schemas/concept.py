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

from app.schemas.limit_up import EchelonOut


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


class ConceptKpisOut(BaseModel):
    """`GET /api/v1/concepts/{board_code}` 的板内连板 KPI（口径 = 梯队快照，见 §2.3）。

    `zt_count` / `max_streak` 是**同源**于 `/market/limit-up` 的快照：板内梯队被原样过滤，
    service 绝不重算 streak。龙头按 `(-streak, -amount, symbol)` 确定性裁决；无涨停成员时
    四个字段分别为 0/0/null/null（`max_streak=0` 是"无涨停"，不是缺失）。
    """

    zt_count: int
    max_streak: int
    leader_symbol: str | None = None
    leader_name: str | None = None


class ConceptDetailOut(BaseModel):
    """`GET /api/v1/concepts/{board_code}`（§2.2）：单板 `BoardItem` + 板内梯队 + KPI。

    `stock_count` / `unresolved_count` 与 `board.member_count` 同源（`concept_members` 行数）：
    `stock_count = board.member_count - unresolved_count`（= `stock_id IS NOT NULL` 的成分数）。
    """

    as_of: date | None
    membership_as_of: date | None  # **该板**的 max(last_seen_on)，不是全表值
    source: Literal["em_clist"]  # 成分名录来源（东财 clist），与资金流快照源无关
    degraded_reason: str | None = None  # "no_members" | "price_limits_missing" | None
    board: BoardItemOut
    kpis: ConceptKpisOut
    echelons: list[EchelonOut] = Field(default_factory=list)  # 板内过滤，形状与梯队卡一致
    unresolved_count: int
    stock_count: int


class ConceptBySymbolItemOut(BaseModel):
    """个股所属板块的一行：`pct_change` = 该板本地聚合 `avg_pct`（口径见 §2.3）。"""

    board_code: str
    board_name: str
    pct_change: float | None = None


class ConceptBySymbolOut(BaseModel):
    """`GET /api/v1/concepts/by-symbol/{symbol}`（§2.2）：未知 symbol 返回空 items，不是 404。"""

    as_of: date | None
    membership_as_of: date | None
    items: list[ConceptBySymbolItemOut] = Field(default_factory=list)
