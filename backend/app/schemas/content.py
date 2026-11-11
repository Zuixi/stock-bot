"""内容读面契约（S3.x）：列表 / 详情 / 用户状态。

三条契约纪律（对应 ``app/models/content.py`` 的四表分工）：

1. **事实层不带用户状态**：``user_state`` 是**当前登录用户**在
   ``user_content_state`` 里的那一行；匿名请求一律 ``null``（不是 ``"new"``——
   前端要能区分"没登录/没标记"和"标记为未读"）。
2. **列表带指标**：S3.4 要求列表展示定期报告的核心信息（收入/利润），故列表按整页
   批量填充 ``metrics``（后端一次查询，不是 N+1）；无指标的内容给空列表，前端渲染 `—`。
3. **``ContentStateIn.status`` 与库 CHECK 约束逐字一致**（除 ``new`` 外）：
   用户能把状态改成的只有四值；``new`` 是入库默认值，不作为入参（"改回未读"没有
   语义，见 ``docs/specs/S3-content.md``）。
4. **研报有效性在契约里显式**：``effective`` / ``ineffective_reasons`` 只对
   ``content_type='report'`` 有语义（非研报一律 ``None`` / ``[]``）；它们由
   ``app.services.research_report_service.evaluate_report`` 计算并原样下发——
   人工终态 ``confirmed`` / ``rejected`` 压倒自动判据。

``entities`` / ``metrics`` 用独立小模型而非裸 ``dict``：字段少但每个都是前端契约
（``entity_type`` 决定跳转路由，``metric_key`` 决定对比表列），裸 dict 会让拼错
的字段名静默变成 ``undefined`` 上屏。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

#: 用户可写入的内容状态（``new`` 是入库默认值，不可写入）。
ContentStateStatus = Literal["read", "processed", "ignored", "archived"]


class ContentEntityOut(BaseModel):
    """内容 ↔ 实体关联（``stock`` / ``industry`` / ``policy_tag`` / ``concept``）。"""

    entity_type: str
    entity_id: str


class ContentMetricOut(BaseModel):
    """从内容抽取的规范化指标；``source_span`` 必填（没有原文定位就没有溯源）。"""

    metric_key: str
    period: str | None = None
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    source_span: dict[str, Any]


class ContentItemOut(BaseModel):
    """一条内容的读面形状。

    ``published_at`` / ``ingested_at`` 都要给：前者是排序键，后者是新鲜度（差值）。
    """

    id: int
    content_type: str
    source_name: str
    publisher_tier: str
    publisher: str | None = None
    title: str
    published_at: datetime
    ingested_at: datetime
    url: str
    extracted: dict[str, Any] = Field(default_factory=dict)
    review_status: str
    #: 当前用户的状态；匿名或未标记 → ``None``
    user_state: str | None = None
    entities: list[ContentEntityOut] = Field(default_factory=list)
    #: 列表与详情都下发；无指标时为空列表（前端渲染 `—`，不补 0）
    metrics: list[ContentMetricOut] = Field(default_factory=list)
    #: 研报是否有效（S3.7 三条判据 + 人工终态）；非研报 → ``None``（无判定语义）
    effective: bool | None = None
    #: 无效原因码（有序去重，见 ``research_report_service.REASON_*``）；有效/非研报 → ``[]``
    ineffective_reasons: list[str] = Field(default_factory=list)


class ContentStateIn(BaseModel):
    """``PATCH /content/{id}/state`` 入参。"""

    status: ContentStateStatus
    note: str | None = Field(default=None, max_length=2000)
