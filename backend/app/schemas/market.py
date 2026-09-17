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
