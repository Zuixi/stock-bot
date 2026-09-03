"""market-data face: sector moneyflow / dragon tiger / northbound / block trades /
share float / repurchase / announcements

Revision ID: 9d4e7a2c8b1f
Revises: e6f7a8b9c0d1
Create Date: 2026-09-03

单位口径（已在真实数据源上验证）：
- 东财资金流、top_list、repurchase.amount：元
- block_trade：price 元 / volume 万股 / amount 万元
- share_float.float_share：万股；northbound.net_amount：万元
"""

import sqlalchemy as sa
from alembic import op

revision = "9d4e7a2c8b1f"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sector_moneyflow_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=False),
        sa.Column("board_code", sa.String(length=16), nullable=False),
        sa.Column("board_name", sa.String(length=32), nullable=True),
        sa.Column("pct_change", sa.Float(), nullable=True),
        sa.Column("main_net_inflow", sa.Float(), nullable=True),
        sa.Column("super_large_net", sa.Float(), nullable=True),
        sa.Column("large_net", sa.Float(), nullable=True),
        sa.Column("main_net_ratio", sa.Float(), nullable=True),
        sa.Column("up_count", sa.Integer(), nullable=True),
        sa.Column("down_count", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "dimension", "board_code", name="uq_sector_moneyflow_dim_code_date"
        ),
    )
    op.create_index(
        "ix_sector_moneyflow_date_dim", "sector_moneyflow_snapshots", ["trade_date", "dimension"]
    )

    op.create_table(
        "dragon_tiger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=True),
        sa.Column("close", sa.Float(), nullable=True),
        sa.Column("pct_change", sa.Float(), nullable=True),
        sa.Column("turnover_rate", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("l_buy", sa.Float(), nullable=True),
        sa.Column("l_sell", sa.Float(), nullable=True),
        sa.Column("l_amount", sa.Float(), nullable=True),
        sa.Column("net_amount", sa.Float(), nullable=True),
        sa.Column("net_rate", sa.Float(), nullable=True),
        sa.Column("amount_rate", sa.Float(), nullable=True),
        sa.Column("float_values", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date", "ts_code", "reason", name="uq_dragon_tiger_date_code_reason"
        ),
    )
    op.create_index("ix_dragon_tiger_date", "dragon_tiger_entries", ["trade_date"])

    op.create_table(
        "northbound_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("net_amount", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", name="uq_northbound_date"),
    )

    op.create_table(
        "block_trades",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("buyer", sa.Text(), nullable=True),
        sa.Column("seller", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trade_date",
            "ts_code",
            "buyer",
            "seller",
            "price",
            "volume",
            name="uq_block_trades_dedupe",
        ),
    )
    op.create_index("ix_block_trades_date", "block_trades", ["trade_date"])
    op.create_index("ix_block_trades_code", "block_trades", ["ts_code"])

    op.create_table(
        "share_floats",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("float_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("float_share", sa.Float(), nullable=True),
        sa.Column("float_ratio", sa.Float(), nullable=True),
        sa.Column("holder_name", sa.Text(), nullable=True),
        sa.Column("share_type", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ann_date", "ts_code", "holder_name", "share_type", name="uq_share_floats_dedupe"
        ),
    )
    op.create_index("ix_share_floats_date", "share_floats", ["float_date"])
    op.create_index("ix_share_floats_code", "share_floats", ["ts_code"])

    op.create_table(
        "stock_repurchases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("proc", sa.String(length=16), nullable=False),
        sa.Column("exp_date", sa.Date(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("high_limit", sa.Float(), nullable=True),
        sa.Column("low_limit", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ann_date", "ts_code", "proc", name="uq_stock_repurchases_dedupe"),
    )
    op.create_index("ix_stock_repurchases_date", "stock_repurchases", ["ann_date"])
    op.create_index("ix_stock_repurchases_code", "stock_repurchases", ["ts_code"])

    op.create_table(
        "announcements",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("announcement_id", sa.String(length=32), nullable=False),
        sa.Column("sec_code", sa.String(length=12), nullable=False),
        sa.Column("sec_name", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("announce_time", sa.DateTime(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("announcement_id", name="uq_announcements_cninfo_id"),
    )
    op.create_index("ix_announcements_time", "announcements", ["announce_time"])
    op.create_index("ix_announcements_code", "announcements", ["sec_code"])


def downgrade() -> None:
    op.drop_table("announcements")
    op.drop_table("stock_repurchases")
    op.drop_table("share_floats")
    op.drop_table("block_trades")
    op.drop_table("northbound_daily")
    op.drop_table("dragon_tiger_entries")
    op.drop_table("sector_moneyflow_snapshots")
