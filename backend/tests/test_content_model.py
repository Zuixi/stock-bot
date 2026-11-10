"""内容事实层四表的 schema 不变量（**纯元数据**，不连库）。

判据来自 ``docs/specs/S3-content.md`` §S3.1 与 2026-11-11 owner 裁定，其中三条是
"违反即设计事故"：

1. ``content_items`` 上**不允许**出现用户状态列（``status`` / ``read_at``），也不允许
   ``body_ref``——只存链接。
2. ``unique(source_name, raw_hash)`` 必须存在，否则同一原文重放会重复入库。
3. ``source_span`` 必填，没有原文定位就没有溯源。

断言全部打在 ``Base.metadata`` 上，因此无需数据库即可在 CI 跑（无库环境不该红）。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, UniqueConstraint

from app import models
from app.core.database import Base

CONTENT_TABLES = ("content_items", "content_entities", "content_metrics", "user_content_state")


def _table(name: str):
    return Base.metadata.tables[name]


def _cols(name: str) -> set[str]:
    return {c.name for c in _table(name).columns}


def _unique_names(name: str) -> set[str]:
    return {
        c.name
        for c in _table(name).constraints
        if isinstance(c, UniqueConstraint) and c.name is not None
    }


def _check_text(name: str) -> dict[str, str]:
    return {
        c.name: str(c.sqltext)
        for c in _table(name).constraints
        if isinstance(c, CheckConstraint) and c.name is not None
    }


def test_models_exported() -> None:
    for cls in ("ContentItem", "ContentEntity", "ContentMetric", "UserContentState"):
        assert cls in models.__all__
        assert getattr(models, cls).__tablename__ in CONTENT_TABLES


def test_four_tables_registered() -> None:
    for name in CONTENT_TABLES:
        assert name in Base.metadata.tables


def test_content_items_columns() -> None:
    assert _cols("content_items") == {
        "id",
        "content_type",
        "source_name",
        "publisher_tier",
        "publisher",
        "title",
        "published_at",
        "ingested_at",
        "url",
        "raw_hash",
        "extracted",
        "extract_version",
        "extract_confidence",
        "review_status",
        "created_at",
    }


def test_content_items_has_no_user_state_or_body_columns() -> None:
    cols = _cols("content_items")
    # 2026-11-11 裁定：只存链接，不落正文
    assert "body_ref" not in cols
    # 多用户下"已读"是个人状态，写在事实层会串号
    assert "status" not in cols
    assert "read_at" not in cols


def test_content_items_column_types_and_nullability() -> None:
    cols = _table("content_items").columns
    required = {
        "content_type",
        "source_name",
        "publisher_tier",
        "title",
        "published_at",
        "ingested_at",
        "url",
        "raw_hash",
        "extracted",
        "review_status",
        "created_at",
    }
    nullable = {"publisher", "extract_version", "extract_confidence"}
    for name in required:
        assert cols[name].nullable is False, name
    for name in nullable:
        assert cols[name].nullable is True, name

    assert isinstance(cols["id"].type, BigInteger)
    assert cols["id"].primary_key is True
    assert cols["extracted"].type.__class__.__name__ == "JSONB"
    assert cols["extract_confidence"].type.precision == 4
    assert cols["extract_confidence"].type.scale == 3


def test_content_items_server_defaults() -> None:
    cols = _table("content_items").columns
    for name in ("ingested_at", "created_at"):
        assert cols[name].server_default is not None, name
    assert "auto" in str(cols["review_status"].server_default.arg)
    assert "{" in str(cols["extracted"].server_default.arg)


def test_content_items_unique_source_hash() -> None:
    assert "uq_content_items_source_hash" in _unique_names("content_items")
    unique = next(
        c
        for c in _table("content_items").constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_content_items_source_hash"
    )
    assert [col.name for col in unique.columns] == ["source_name", "raw_hash"]


def test_content_items_indexes() -> None:
    indexed = {idx.name: [c.name for c in idx.columns] for idx in _table("content_items").indexes}
    assert indexed["ix_content_items_type_published"] == ["content_type", "published_at"]
    assert indexed["ix_content_items_published"] == ["published_at"]
    assert indexed["ix_content_items_review"] == ["review_status"]


def test_content_items_review_status_check() -> None:
    checks = _check_text("content_items")
    assert "chk_content_items_review_status" in checks
    sql = checks["chk_content_items_review_status"]
    for value in ("auto", "pending", "confirmed", "rejected"):
        assert value in sql, value


def test_content_entities_composite_pk_and_cascade() -> None:
    table = _table("content_entities")
    assert _cols("content_entities") == {"content_id", "entity_type", "entity_id"}
    assert [c.name for c in table.primary_key.columns] == ["content_id", "entity_type", "entity_id"]
    assert not any(c.name == "id" for c in table.columns)

    fk = next(iter(table.foreign_keys))
    assert fk.column.table.name == "content_items"
    assert fk.column.name == "id"
    assert fk.ondelete == "CASCADE"
    assert table.columns["content_id"].nullable is False
    assert table.columns["entity_type"].nullable is False
    assert table.columns["entity_id"].nullable is False


def test_content_metrics_columns() -> None:
    assert _cols("content_metrics") == {
        "id",
        "content_id",
        "entity_type",
        "entity_id",
        "metric_key",
        "period",
        "value",
        "value_text",
        "unit",
        "source_span",
        "created_at",
    }
    cols = _table("content_metrics").columns
    assert cols["id"].primary_key is True
    assert cols["source_span"].nullable is False
    assert cols["source_span"].type.__class__.__name__ == "JSONB"
    assert cols["value"].type.precision == 18
    assert cols["value"].type.scale == 4
    assert cols["value"].nullable is True
    assert cols["value_text"].nullable is True
    assert cols["unit"].nullable is True
    assert cols["period"].nullable is True

    fk = next(iter(_table("content_metrics").foreign_keys))
    assert fk.column.table.name == "content_items"
    assert fk.ondelete == "CASCADE"


def test_content_metrics_unique_key() -> None:
    assert "uq_content_metrics_key" in _unique_names("content_metrics")
    unique = next(
        c
        for c in _table("content_metrics").constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_content_metrics_key"
    )
    assert [col.name for col in unique.columns] == [
        "content_id",
        "entity_id",
        "metric_key",
        "period",
    ]


def test_content_metrics_nullperiod_partial_unique_index() -> None:
    """``period IS NULL`` 的去重靠部分唯一索引（PG 视 NULL 互不相等）。

    没有它，非期间型指标（``core_view`` 等）重复抽取就会插重复行——
    ``uq_content_metrics_key`` 对 NULL 行完全不生效。
    """
    indexes = {idx.name: idx for idx in _table("content_metrics").indexes}
    index = indexes["uq_content_metrics_key_nullperiod"]
    assert index.unique is True
    assert [c.name for c in index.columns] == ["content_id", "entity_id", "metric_key"]
    assert index.dialect_options["postgresql"]["where"] is not None
    assert "period is null" in str(index.dialect_options["postgresql"]["where"]).lower()


def test_user_content_state_composite_pk_and_status_check() -> None:
    table = _table("user_content_state")
    assert _cols("user_content_state") == {"user_id", "content_id", "status", "note", "updated_at"}
    assert [c.name for c in table.primary_key.columns] == ["user_id", "content_id"]

    fk = next(iter(table.foreign_keys))
    assert fk.column.table.name == "content_items"
    assert fk.ondelete == "CASCADE"

    assert table.columns["status"].nullable is False
    assert "new" in str(table.columns["status"].server_default.arg)
    assert table.columns["note"].nullable is True
    assert table.columns["updated_at"].server_default is not None
    assert table.columns["updated_at"].onupdate is not None

    checks = _check_text("user_content_state")
    assert "chk_user_content_state_status" in checks
    sql = checks["chk_user_content_state_status"]
    for value in ("new", "read", "processed", "ignored", "archived"):
        assert value in sql, value
