"""announcements 旧表 → content_items 回填，再把旧表替换成同名只读视图

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f6
Create Date: 2026-11-11

公告的唯一事实层是 ``content_items``（采集侧已改走 ``app/services/announcement_service.py``，
见 ``docs/specs/S3-content.md`` §S3.1 与 2026-11-11 owner 裁定「只存链接」），旧表
``announcements`` 只剩历史存量。本迁移把存量搬进事实层，然后把旧表**替换成同名只读视图**：
``market_data_repo.list_announcements`` / ``journal_service`` 的读路径零改动继续工作，
而写入会被 PG 直接拒绝（视图带 ``LEFT JOIN LATERAL`` 与表达式列，不是自动可更新视图），
从而物理上堵住"再往旧表写公告"的回头路。

三处刻意选择：

1. **幂等键复用** ``uq_content_items_source_hash``：``raw_hash = sha256("cninfo:{id}")``
   与 ``announcement_service`` 同一口径，重放不会重复入库（``ON CONFLICT DO NOTHING``）。
   回填后的 ``content_entities`` 也按 ``raw_hash`` 反查 ``content_items.id``，不依赖
   ``RETURNING``——冲突跳过的那一行同样会被补上实体关联。
2. ``url`` 只能来自 ``pdf_url``。旧行允许 ``pdf_url IS NULL``，而 owner 裁定
   「只存链接」= 没有原文入口的内容不入事实层；这类行**跳过并计数**，绝不写空串
   （空串会让"原文可达率 100%"变成假绿）。
3. ``downgrade`` **不删** ``content_items`` 行（事实层是权威），只把视图换回表并尽力回填。

``published_at`` 由 ``announce_time``（旧表是 naive wall-clock，语义即**上海时间**——
``cninfo_client`` 的映射注释与 ``tests/test_announcement.py`` 的用例都把
``announce_time`` 当上海墙上时间）显式 ``AT TIME ZONE 'Asia/Shanghai'`` 折算成
instant。**不能**依赖 naive datetime 的隐式转换：那按客户端/会话时区解释，同一份
存量在不同机器上迁移会得到不同 instant（本机 JST 实测偏 1 小时），而 ``downgrade``
用同一时区折回，往返严格可逆。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 旧列名一个不改：这是 ``Announcement`` 模型与两条读路径的既有契约。
_VIEW_SQL = """
CREATE VIEW announcements AS
SELECT ci.id AS id,
       ci.extracted->>'source_item_id' AS announcement_id,
       ce.entity_id AS sec_code,
       ci.extracted->>'sec_name' AS sec_name,
       ci.title AS title,
       ci.published_at AS announce_time,
       ci.extracted->>'subtype' AS category,
       ci.url AS pdf_url,
       ci.created_at AS created_at
FROM content_items ci
LEFT JOIN LATERAL (
  SELECT entity_id FROM content_entities
  WHERE content_id = ci.id AND entity_type = 'stock'
  LIMIT 1
) ce ON TRUE
WHERE ci.content_type = 'announcement'
"""

# 回程：视图 → 表。``announcement_id`` / ``sec_code`` / ``title`` / ``category`` 在旧表
# 是 NOT NULL，故过滤掉缺必需字段的行（best-effort，不因个别残行让 downgrade 失败）。
_RESTORE_SQL = """
INSERT INTO announcements (
    announcement_id, sec_code, sec_name, title, announce_time, category, pdf_url
)
SELECT ci.extracted->>'source_item_id',
       ce.entity_id,
       ci.extracted->>'sec_name',
       ci.title,
       CAST(ci.published_at AT TIME ZONE 'Asia/Shanghai' AS timestamp),
       COALESCE(ci.extracted->>'subtype', 'event'),
       ci.url
FROM content_items ci
JOIN LATERAL (
  SELECT entity_id FROM content_entities
  WHERE content_id = ci.id AND entity_type = 'stock'
  LIMIT 1
) ce ON TRUE
WHERE ci.content_type = 'announcement'
  AND ci.extracted->>'source_item_id' IS NOT NULL
ON CONFLICT (announcement_id) DO NOTHING
"""

_INSERT_ITEM_SQL = """
INSERT INTO content_items (
    content_type, source_name, publisher_tier, publisher,
    title, published_at, url, raw_hash, extracted
) VALUES (
    'announcement', 'cninfo', 'legal', :publisher,
    :title,
    CAST(CAST(:published_at AS timestamp) AT TIME ZONE 'Asia/Shanghai' AS timestamptz),
    :url, :raw_hash,
    CAST(:extracted AS jsonb)
)
ON CONFLICT (source_name, raw_hash) DO NOTHING
"""

# 按幂等键反查 id：即使上面的插入因冲突被跳过，实体关联也必须存在。
_INSERT_ENTITY_SQL = """
INSERT INTO content_entities (content_id, entity_type, entity_id)
SELECT id, 'stock', :sec_code
FROM content_items
WHERE source_name = 'cninfo' AND raw_hash = :raw_hash
ON CONFLICT DO NOTHING
"""


def _legacy_rows(bind: sa.Connection) -> list[Any]:
    return list(
        bind.execute(
            sa.text(
                "SELECT announcement_id, sec_code, sec_name, title, announce_time, "
                "category, pdf_url FROM announcements ORDER BY id"
            )
        ).mappings()
    )


def _raw_hash(announcement_id: str) -> str:
    return hashlib.sha256(f"cninfo:{announcement_id}".encode()).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()

    # 先取快照再写：INSERT 与源表同名会让边读边写变得不可推理。
    rows = _legacy_rows(bind)
    skipped_no_url = 0
    for row in rows:
        if not row["pdf_url"]:
            # 只存链接：没有原文入口的行无法溯源，不入事实层（不写空串）。
            skipped_no_url += 1
            continue
        raw_hash = _raw_hash(row["announcement_id"])
        extracted = {
            "source_item_id": row["announcement_id"],
            "subtype": row["category"],
            "sec_code": row["sec_code"],
            "sec_name": row["sec_name"],
        }
        bind.execute(
            sa.text(_INSERT_ITEM_SQL),
            {
                "publisher": row["sec_name"],
                "title": row["title"],
                "published_at": row["announce_time"],
                "url": row["pdf_url"],
                "raw_hash": raw_hash,
                "extracted": json.dumps(extracted, ensure_ascii=False),
            },
        )
        bind.execute(
            sa.text(_INSERT_ENTITY_SQL),
            {"sec_code": row["sec_code"], "raw_hash": raw_hash},
        )

    if skipped_no_url:
        print(
            f"announcements backfill: skipped {skipped_no_url} row(s) without pdf_url "
            "(link-only policy)"
        )

    # 旧表卸任：先掉索引再掉表（drop_table 也会带走索引，显式写出让意图可读）。
    op.drop_index("ix_announcements_code", table_name="announcements")
    op.drop_index("ix_announcements_time", table_name="announcements")
    op.drop_table("announcements")

    op.execute(_VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS announcements")

    # 形状与 9d4e7a2c8b1f 建的旧表逐列一致（含唯一键与两条索引）。
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

    # 尽力回填；``content_items`` 里的行一个都不删。
    op.execute(_RESTORE_SQL)
