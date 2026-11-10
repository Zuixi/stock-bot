"""内容事实层四表（S3.1）。

分工（契约见 ``docs/specs/S3-content.md`` §S3.1）：

- ``content_items``：**事实层**，只由采集/抽取写入。六类内容（公告/研报/政策/快讯/
  协会/第三方）共用一张表，靠 ``content_type`` + ``publisher_tier`` 区分。
- ``content_entities``：内容 ↔ 实体（stock / industry / policy_tag / concept），支撑
  "按实体查内容"的读面。
- ``content_metrics``：从内容抽取的规范化指标，是对比表（S4.2）的原料；``source_span``
  必填，没有原文定位就没有溯源。
- ``user_content_state``：**用户维度**状态机。

刻意不做的事（2026-11-11 owner 裁定）：
1. **只存链接**：不落正文/PDF，没有 ``body_ref``；``url`` 是唯一原文入口。
2. **``content_items`` 上不允许出现任何用户状态列**（``status`` / ``read_at``）——
   "已读"是个人的，写在事实层会导致 A 读完 B 也看不到。用户状态只在
   ``user_content_state``。
3. 两个时间戳（``published_at`` / ``ingested_at``）都必须有，差值即新鲜度。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ContentItem(Base):
    """内容事实层：一条内容 = 一次发布事件。"""

    __tablename__ = "content_items"
    __table_args__ = (
        UniqueConstraint("source_name", "raw_hash", name="uq_content_items_source_hash"),
        Index("ix_content_items_type_published", "content_type", "published_at"),
        Index("ix_content_items_published", "published_at"),
        Index("ix_content_items_review", "review_status"),
        CheckConstraint(
            "review_status IN ('auto','pending','confirmed','rejected')",
            name="chk_content_items_review_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # announcement | report | policy | news | association | third_party | inquiry
    content_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_name: Mapped[str] = mapped_column(String(32), nullable=False)
    # legal | official | sellside | media（勿与 industry_metrics.source_tier 混）
    publisher_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # ★ 发布时点
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # ★ 入库时点（与 published_at 不同，差值即新鲜度）
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # ★ 原文唯一入口（不落正文/PDF）
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # 去重键（正文哈希；正文只在内存态）
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    extract_version: Mapped[int | None] = mapped_column(Integer)
    extract_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="auto")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContentEntity(Base):
    """内容 ↔ 实体关联（多对多，复合主键即去重键）。"""

    __tablename__ = "content_entities"
    __table_args__ = (Index("ix_content_entities_entity", "entity_type", "entity_id"),)

    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_items.id", ondelete="CASCADE"), primary_key=True
    )
    # stock | industry | policy_tag | concept
    entity_type: Mapped[str] = mapped_column(String(24), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(64), primary_key=True)


class ContentMetric(Base):
    """从内容抽取的规范化指标（对比表的原料）。"""

    __tablename__ = "content_metrics"
    __table_args__ = (
        UniqueConstraint(
            "content_id", "entity_id", "metric_key", "period", name="uq_content_metrics_key"
        ),
        # Postgres 视 NULL 互不相等，上面那条唯一约束对 ``period IS NULL``（非期间型
        # 指标，如 ``core_view``）完全不生效——重复抽取会插重复行。用**部分唯一索引**
        # 补上这段空档（非 NULL 的 period 仍由 uq_content_metrics_key 兜）。
        Index(
            "uq_content_metrics_key_nullperiod",
            "content_id",
            "entity_id",
            "metric_key",
            unique=True,
            postgresql_where=text("period IS NULL"),
        ),
        Index("ix_content_metrics_entity", "entity_type", "entity_id", "metric_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # revenue | net_profit | core_view ...
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # 报告期，如 '2026H1'
    period: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[float | None] = mapped_column(Numeric(18, 4))
    # 非数值型（如"核心观点"）
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(16))
    # ★ 原文定位：{"kind":"pdf",...} 或 {"kind":"structured",...}（必填）
    source_span: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserContentState(Base):
    """用户 × 内容状态机（事实层不得出现用户状态列）。"""

    __tablename__ = "user_content_state"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new','read','processed','ignored','archived')",
            name="chk_user_content_state_status",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    content_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("content_items.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="new")
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
