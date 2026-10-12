"""sw member ingest runs: coverage columns + constrain unique key

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-12

Fix round 1 的两项结构修正（评审 C1/I1/I2/I5）：

1. 幂等键收口为 **表达式唯一索引** ``uq_sw_member_period_code_stock_from_to``：
   ``(industry_code, stock_code, effective_from, COALESCE(effective_to, DATE '9999-12-31'))``。
   为什么必须是**表达式**而不是普通 ``UniqueConstraint(..., effective_to)``
   （Fix round 1 实测踩到）：PostgreSQL 的 UNIQUE 视 NULL 彼此不同，于是
   ``effective_to IS NULL``（"至今仍在成分"，占绝大多数）的行**永不冲突** ——
   重复 ingest 会插重复行、幂等当场失效（实测第二次 upsert 真的插了第 2 行）。
   ``COALESCE`` 把 NULL 折叠成同一哨兵值后唯一性才生效。
   ``b4`` 已直接建成目标形状；本迁移只在**旧形状仍在**时收口，
   故 "b4 → c5" 与逐版本升级两条路径都收敛到同一形状。
2. ``sw_member_ingest_runs`` 加 4 列（全部带 server_default，**既有行不需要回填**）：
   ``rows_inserted`` / ``rows_updated``（区分"重跑新增 0"与"真有新行"）、
   ``fully_ingested``（False = limit 截断或有失败 → **不得抬高读面 as_of 上界**）、
   ``zero_row_indices``（上游返回 0 行的指数个数，下界信号之一）；
   并把 ``detail`` 由 String(255) 放宽到 String(512)（要放逐级计数与失败预览）。

**不给既有行硬塞 NOT NULL**：4 个新列都有 ``server_default``，既有行自动取默认值。
既有 ``sw_industry_members`` 依旧一列未动。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 幂等键收口为**表达式唯一索引**（NULL 终点折叠成哨兵）。
    #
    #    为什么必须改（Fix round 1 实测踩到）：PostgreSQL 的 UNIQUE 视 NULL 彼此不同，
    #    含 effective_to 的普通唯一键对 `effective_to IS NULL`（至今仍在成分）的行
    #    **永不冲突** → 重复 ingest 插重复行，幂等当场失效。
    #
    #    两条升级路径都收敛：b4（新建）已直接建目标形状；从旧版上来的库仍是旧形状，
    #    故这里按"目标形状不存在就重建"处理，且只在旧形状确实在时 drop。
    bind = op.get_bind()
    if (
        bind.execute(
            sa.text("SELECT to_regclass('public.uq_sw_member_period_code_stock_from_to')")
        ).scalar()
        is None
    ):
        old_constraint = bind.execute(
            sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid = "
                "'sw_industry_member_periods'::regclass AND contype = 'u' "
                "AND conname = 'uq_sw_member_period_code_stock_from'"
            )
        ).scalar()
        if old_constraint is not None:
            op.drop_constraint(
                "uq_sw_member_period_code_stock_from",
                "sw_industry_member_periods",
                type_="unique",
            )
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

    # 2) ingest 运行记录的覆盖列
    op.add_column(
        "sw_member_ingest_runs",
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sw_member_ingest_runs",
        sa.Column("rows_updated", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sw_member_ingest_runs",
        sa.Column("fully_ingested", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "sw_member_ingest_runs",
        sa.Column("zero_row_indices", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column(
        "sw_member_ingest_runs",
        "detail",
        existing_type=sa.String(length=255),
        type_=sa.String(length=512),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "sw_member_ingest_runs",
        "detail",
        existing_type=sa.String(length=512),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.drop_column("sw_member_ingest_runs", "zero_row_indices")
    op.drop_column("sw_member_ingest_runs", "fully_ingested")
    op.drop_column("sw_member_ingest_runs", "rows_updated")
    op.drop_column("sw_member_ingest_runs", "rows_inserted")

    bind = op.get_bind()
    if (
        bind.execute(
            sa.text("SELECT to_regclass('public.uq_sw_member_period_code_stock_from_to')")
        ).scalar()
        is not None
    ):
        op.drop_index(
            "uq_sw_member_period_code_stock_from_to", table_name="sw_industry_member_periods"
        )
    existing_uq = bind.execute(
        sa.text(
            "SELECT conname FROM pg_constraint WHERE conrelid = "
            "'sw_industry_member_periods'::regclass AND contype = 'u'"
        )
    ).scalar()
    if existing_uq is None:
        op.create_unique_constraint(
            "uq_sw_member_period_code_stock_from",
            "sw_industry_member_periods",
            ["industry_code", "stock_code", "effective_from"],
        )
