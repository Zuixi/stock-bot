"""自选体检的响应契约（**只读**，无写模型；零新表）。

字段与前端 ``frontend/src/shared/api/triage.ts`` 的 ``TriageResponse`` 一一对应：
``disclaimer`` / ``unavailable`` 都由后端给，前端不硬编码（避免合规文案两处漂移）。
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

#: 分级只用这四枚举（spec §S8.5）：risk / event / attention / background。
#: 与 ``triage_detectors.SEVERITIES`` 的一致性由 ``tests/test_watchlist_triage_api.py``
#: 机械校验（两处漂移 = 响应里冒出第五种分级，前端 `??` 兜底会把它静默画成"背景"）。
TriageSeverity = Literal["risk", "event", "attention", "background"]


class FactOut(BaseModel):
    """一条事实：``label`` + ``value`` + ``unit``（例：收盘 / 11.20 / 元）。"""

    label: str
    value: float | str
    unit: str = ""


class TriageItemOut(BaseModel):
    """一条体检结论：只陈述事实 + 数据截至 + 复核去处。"""

    symbol: str
    name: str
    detector: str
    severity: TriageSeverity
    headline: str
    facts: list[FactOut]
    as_of: date
    source: str
    verify: str


class TriageOut(BaseModel):
    """``GET /api/v1/watchlists/triage`` 的完整载荷。"""

    as_of: date
    #: 今日无命中的自选只数（``items=[]`` 且 ``quiet_count>0`` = 全静默日）
    quiet_count: int
    #: 超出单页上限被折叠的条数（>0 时前端提示"另有 N 项未展示"）
    folded: int
    items: list[TriageItemOut]
    #: 缺数据而未执行的探测器 key（spec §S8.4：不得静默当作无事件）
    unavailable: list[str]
    disclaimer: str
