"""add sw industry member scd2 tables

Revision ID: b4c5d6e7f8a9
Revises: f1a2b3c4d5e6
Create Date: 2026-10-12

S1.4 的 SCD2 分类归属：**新建两张表**，不改既有 ``sw_industry_members``。

为什么不给 ``sw_industry_members`` 加列（Task 8 第 0 步实测后的裁定）::

    * 该表的唯一键是 ``(industry_code, stock_code)``——**结构上装不下**同一只股票
      多次进出同一行业的多段区间（上游实测 ``000034.SZ`` 在 801010.SI 有 3 段）；
    * 既有导入路径 ``sw_industry_service.import_sw_members`` 整表 ``DELETE`` 后重灌
      （``data/sw_seed.sql`` 同款），给这张表加 ``effective_from`` 会**静默清空**历史；
    * Ruling 30：保留既有能力。既有读路（板块性能/L1-L3 下钻）今天只用当前态，
      换表的收益不足以承担"全量替换式导入 + SCD2"两种语义挤一张表的复杂度。

故 ``sw_industry_member_periods`` 承载 SCD2，只做 INSERT/UPDATE。
``effective_from`` 对**新表**为 NOT NULL（新表无既有行，不违反"不给既有行硬塞 NOT NULL"）；
``sw_industry_members`` 一列未动。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sw_industry_member_periods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_code", sa.String(length=10), nullable=False),
        sa.Column("stock_code", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=10), nullable=False),
        sa.Column("stock_name", sa.String(length=64), nullable=True),
        # 上游 index_member.in_date → effective_from：NOT NULL 是新表自身的设计，不是回填既有行
        sa.Column("effective_from", sa.Date(), nullable=False),
        # 上游 index_member.out_date → effective_to：NULL = 截至最近一次 ingest 仍是成分
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="tushare:index_member",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # 下钻主路径：某行业在 T 日的成分 → (industry_code, [effective_from, effective_to))
    op.create_index(
        "idx_sw_member_period_industry_from",
        "sw_industry_member_periods",
        ["industry_code", "effective_from"],
    )
    # 个股反查：某股票在 T 日属于哪个行业
    op.create_index(
        "idx_sw_member_period_symbol_from",
        "sw_industry_member_periods",
        ["symbol", "effective_from"],
    )
    # 当前态（effective_to IS NULL）过滤
    op.create_index(
        "idx_sw_member_period_open",
        "sw_industry_member_periods",
        ["industry_code", "effective_to"],
    )
    # 幂等键（Fix round 1）：**表达式唯一索引**，把 NULL 终点折叠成哨兵日期。
    #
    # 为什么不是普通 `UniqueConstraint(..., effective_to)`：PostgreSQL 的 UNIQUE 把
    # NULL 视为彼此不同，于是"至今仍在成分"（effective_to IS NULL）的行**永远不会
    # 冲突** —— 重复 ingest 会插出重复行，幂等当场失效（实测：第二次 upsert 插了
    # 第 2 行）。`COALESCE(effective_to, DATE '9999-12-31')` 把 NULL 折叠成同一个值，
    # 唯一性才真的生效；同时 `effective_from` 也进键，多段区间（同一股票多次进出）
    # 仍各占一行。
    op.create_index(
        "uq_sw_member_period_code_stock_from_to",
        "sw_industry_member_periods",
        [
            "industry_code",
            "stock_code",
            "effective_from",
            sa.text("COALESCE(effective_to, DATE '9999-12-31')"),
        ],
        unique=True,
    )

    op.create_table(
        "sw_member_ingest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("index_codes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("detail", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sw_member_ingest_started", "sw_member_ingest_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("idx_sw_member_ingest_started", table_name="sw_member_ingest_runs")
    op.drop_table("sw_member_ingest_runs")
    op.drop_index("idx_sw_member_period_open", table_name="sw_industry_member_periods")
    op.drop_index("idx_sw_member_period_symbol_from", table_name="sw_industry_member_periods")
    op.drop_index("idx_sw_member_period_industry_from", table_name="sw_industry_member_periods")
    op.drop_table("sw_industry_member_periods")
