"""按日聚合市场端点的统一响应信封。

`as_of_quality` 来自 `market_day_service.resolve_latest_complete_day`（完整性判据），
取值语义：
- ``complete``：候选日（max trade_date）本身完整；
- ``fallback``：候选日脏，回落到窗口内最近的完整日（`as_of_reason="latest_day_incomplete"`）；
- ``partial``：窗口内没有完整日，返回候选日但标注不新鲜 / 或库内无任何行情（as_of=None）。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

AsOfQuality = Literal["complete", "partial", "fallback"]


class MarketListOut(BaseModel):
    """按日聚合端点的统一信封：as_of 与质量标注 + items。"""

    as_of: date | None = None
    as_of_quality: AsOfQuality = "partial"
    as_of_reason: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)


#: 热门板块 items 的产地（与 ``market_service._HOT_BOARD_SOURCE_*`` 对齐）。
HotBoardSource = Literal["eastmoney_boards", "local_grouping"]


class HotBoardsOut(MarketListOut):
    """``/market/hot-boards``：东财板块体系（真实 ``BK`` code）或本地分组回落。

    比 ``MarketListOut`` 多两个**产地判别**字段——前端必须能区分"东财板块"与
    "本地分组降级"，否则回落的 ``code=""`` / ``leaders=[]`` 会被当成东财数据。
    """

    #: **必填**（无默认）：与 `limit_up.py::LimitUpLadderOut.source` 同约定——payload
    #: 少了产地就是契约破损，应当 500 炸出来，而不是让默认值替它撒谎。
    source: HotBoardSource
    degraded_reason: str | None = None


class BoardStockOut(BaseModel):
    """``/market/boards/{board_code}/stocks`` 成分股行（实时东财快照）。"""

    symbol: str
    name: str | None = None
    pct_change: float | None = None
    main_net_inflow: float | None = None  # 元
