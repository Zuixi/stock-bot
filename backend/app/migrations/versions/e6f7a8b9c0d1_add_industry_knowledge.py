"""add industry_knowledge table + pig seed

Revision ID: e6f7a8b9c0d1
Revises: d5a6b7c8d9e0
Create Date: 2026-09-03 14:00:00.000000

P6 知识库：行业知识内容表（机构图谱 / 数据权威性原则 / 思维导图，JSONB 纯内容管理）。
同 kind 允许多行（org 每机构一行），按 sort 排序读取；内容即数据，第二行业零表结构改动。
种子内容单点维护在 app/services/industry_knowledge_seed.py（迁移与单测共用同一份；
本仓库 alembic env 本就运行于 app 包内 —— env.py 已 import app.config/app.models，
故迁移 import app 内容模块与既有运行方式一致）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.services.industry_knowledge_seed import build_pig_knowledge_rows

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "industry_knowledge",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        # org | principle | mindmap（org 多行，principle/mindmap 各一行）
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("sort", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_industry_knowledge_lookup",
        "industry_knowledge",
        ["industry_key", "kind", "sort"],
        unique=False,
    )

    knowledge = sa.table(
        "industry_knowledge",
        sa.column("industry_key", sa.String),
        sa.column("kind", sa.String),
        sa.column("payload", JSONB),
        sa.column("sort", sa.Integer),
    )
    op.bulk_insert(knowledge, build_pig_knowledge_rows())


def downgrade() -> None:
    op.drop_index("idx_industry_knowledge_lookup", table_name="industry_knowledge")
    op.drop_table("industry_knowledge")
