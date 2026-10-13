"""add northbound_daily amount_kind + rename net_amount to amount

Revision ID: b6a1f0c2d7e4
Revises: 84476cb7b838
Create Date: 2026-10-13

Task 16 / Ruling 62-63：``northbound_daily`` 存的 ``moneyflow_hsgt.north_money``
**同一列两种口径**（``2024-08-19`` 换语义，实证见 ``docs/design/data-source.md`` 附.9）。
列名 ``net_amount``（净流入额）与断点后的真实口径（**成交额**）矛盾，于是：

1. **加** ``amount_kind``（``'net_buy'`` | ``'turnover'``，``NOT NULL`` + ``CHECK``）——
   口径变成**行级机器可读**，任何读表者（SQL / 报表 / 服务）都不必再靠日期或数值正负猜。
2. **回填** 既有行：``trade_date >= 2024-08-19`` → ``'turnover'``，之前 → ``'net_buy'``。
   （dev 库 42 行全在断点后，故全部回填为 ``'turnover'``。）
3. **改名** ``net_amount`` → ``amount``：字段名不再宣称净流入。``amount`` 的含义
   **由同行的 ``amount_kind`` 决定**，这是"单值列 + 口径判别列"的标签联合，
   比"两个互斥可空列"少一半空值语义，也不用把断点固化成表结构。

**不改任何数值**（只加列 + 回填口径 + 改名），既有能力（表 / ingest / 读面）一行不删
（Ruling 30）。``ALTER TABLE ... RENAME COLUMN`` / ``ADD COLUMN`` 都是元数据操作，
本迁移不重写表。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6a1f0c2d7e4"
down_revision: str | None = "84476cb7b838"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 与 ``app/services/northbound_caliber.NORTHBOUND_TURNOVER_SINCE`` 同值。
#: 迁移**不 import 应用代码**（迁移是冻结的历史），一致性由单测钉住。
_TURNOVER_SINCE = "2024-08-19"

_AMOUNT_KIND_CHECK = "amount_kind IN ('net_buy', 'turnover')"


def upgrade() -> None:
    op.add_column(
        "northbound_daily",
        sa.Column("amount_kind", sa.String(length=16), nullable=True),
    )
    # 先回填再置 NOT NULL：既有行必须拿到自己的口径，否则 NOT NULL 无从满足。
    op.execute(
        f"""
        UPDATE northbound_daily
           SET amount_kind = CASE
                 WHEN trade_date >= DATE '{_TURNOVER_SINCE}' THEN 'turnover'
                 ELSE 'net_buy'
               END
        """
    )
    op.alter_column(
        "northbound_daily",
        "amount_kind",
        existing_type=sa.String(length=16),
        nullable=False,
    )
    op.create_check_constraint("ck_northbound_amount_kind", "northbound_daily", _AMOUNT_KIND_CHECK)
    op.alter_column(
        "northbound_daily",
        "net_amount",
        new_column_name="amount",
        existing_type=sa.Float(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "northbound_daily",
        "amount",
        new_column_name="net_amount",
        existing_type=sa.Float(),
        existing_nullable=True,
    )
    op.drop_constraint("ck_northbound_amount_kind", "northbound_daily", type_="check")
    op.drop_column("northbound_daily", "amount_kind")
