"""add content foundation: content_items / entities / metrics / user_content_state (S3.1)

Revision ID: a1b2c3d4e5f6
Revises: e3f1a7c9d2b4
Create Date: 2026-11-11

S3 资讯地基的四张表（契约见 ``docs/specs/S3-content.md`` §S3.1）。分工：

- ``content_items``：**事实层**，只由采集/抽取写入。``content_type`` + ``publisher_tier``
  区分六类内容（公告/研报/政策/快讯/协会/第三方/问询）。
- ``content_entities``：内容 ↔ 实体（stock / industry / policy_tag / concept），支撑
  "按实体查内容"的读面；复合主键即去重键。
- ``content_metrics``：抽取出的规范化指标（对比表原料）。``source_span`` 必填——
  没有原文定位就没有溯源。
- ``user_content_state``：**用户维度**状态机。

2026-11-11 owner 裁定落到 DDL 上的三处：

1. **只存链接**：没有 ``body_ref``，``url`` 是唯一原文入口（正文只在内存态）。
2. **``content_items`` 上不出现任何用户状态列**（``status`` / ``read_at``）——
   "已读"是个人的，写在事实层会串号。用户状态只在 ``user_content_state``。
3. 两个时间戳（``published_at`` / ``ingested_at``）都必须有，差值即新鲜度。

迁移不引入任何业务默认值，只有 ``now()`` / ``'auto'`` / ``'new'`` / ``'{}'::jsonb``
这类 schema 默认。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "e3f1a7c9d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "content_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # announcement | report | policy | news | association | third_party | inquiry
        sa.Column("content_type", sa.String(length=24), nullable=False),
        sa.Column("source_name", sa.String(length=32), nullable=False),
        # legal | official | sellside | media
        sa.Column("publisher_tier", sa.String(length=16), nullable=False),
        sa.Column("publisher", sa.String(length=128), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "extracted",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("extract_version", sa.Integer(), nullable=True),
        sa.Column("extract_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_name", "raw_hash", name="uq_content_items_source_hash"),
        sa.CheckConstraint(
            "review_status IN ('auto','pending','confirmed','rejected')",
            name="chk_content_items_review_status",
        ),
    )
    op.create_index(
        "ix_content_items_type_published", "content_items", ["content_type", "published_at"]
    )
    op.create_index("ix_content_items_published", "content_items", ["published_at"])
    op.create_index("ix_content_items_review", "content_items", ["review_status"])

    op.create_table(
        "content_entities",
        sa.Column(
            "content_id",
            sa.BigInteger(),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("content_id", "entity_type", "entity_id"),
    )
    op.create_index("ix_content_entities_entity", "content_entities", ["entity_type", "entity_id"])

    op.create_table(
        "content_metrics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "content_id",
            sa.BigInteger(),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=True),
        sa.Column("value", sa.Numeric(18, 4), nullable=True),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("source_span", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_id", "entity_id", "metric_key", "period", name="uq_content_metrics_key"
        ),
    )
    # Postgres 把 NULL 视为互不相等，uq_content_metrics_key 对 ``period IS NULL``
    # （非期间型指标，如 ``core_view``）形同虚设——重复抽取会插重复行。
    # 部分唯一索引补上这段空档，非 NULL 的 period 仍由上面那条约束兜。
    op.create_index(
        "uq_content_metrics_key_nullperiod",
        "content_metrics",
        ["content_id", "entity_id", "metric_key"],
        unique=True,
        postgresql_where=sa.text("period IS NULL"),
    )
    op.create_index(
        "ix_content_metrics_entity",
        "content_metrics",
        ["entity_type", "entity_id", "metric_key"],
    )

    op.create_table(
        "user_content_state",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "content_id",
            sa.BigInteger(),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="new"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "content_id"),
        sa.CheckConstraint(
            "status IN ('new','read','processed','ignored','archived')",
            name="chk_user_content_state_status",
        ),
    )


def downgrade() -> None:
    # 反依赖序：先拆引用 content_items 的三张表，最后拆事实层。
    op.drop_table("user_content_state")
    op.drop_index("uq_content_metrics_key_nullperiod", table_name="content_metrics")
    op.drop_index("ix_content_metrics_entity", table_name="content_metrics")
    op.drop_table("content_metrics")
    op.drop_index("ix_content_entities_entity", table_name="content_entities")
    op.drop_table("content_entities")
    op.drop_index("ix_content_items_review", table_name="content_items")
    op.drop_index("ix_content_items_published", table_name="content_items")
    op.drop_index("ix_content_items_type_published", table_name="content_items")
    op.drop_table("content_items")
