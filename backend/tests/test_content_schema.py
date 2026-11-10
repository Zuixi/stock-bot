"""内容事实层四表的**真库 schema** 不变量（S3.1）。

``tests/test_content_model.py`` 钉的是 ``Base.metadata``（纯元数据，不连库）；
本文件钉的是**迁移真跑出来的库**——模型与迁移其中一方漂移就会红。判据来自
``docs/specs/S3-content.md`` §S3.1 与 2026-11-11 owner 裁定。

刻意覆盖的几条"违反即设计事故"：

1. ``content_items`` 上**没有** ``body_ref``（只存链接）、``status`` / ``read_at``
   （用户状态必须归 ``user_content_state``）。
2. ``uq_content_items_source_hash`` 存在，否则同一原文重放会重复入库。
3. 两个复合主键（``content_entities`` / ``user_content_state``）顺序精确。
4. 子表 FK 是 ``ON DELETE CASCADE``——删内容不能留孤儿行。

反射走 ``app.core.database.engine``（与生产同一份 ``DATABASE_URL``），因此必须
跑在 alembic 迁移过的库上。引擎是 asyncpg 的：直接 ``inspect(engine.sync_engine)``
会在同步测试里抛 ``MissingGreenlet``，所以统一在 ``conn.run_sync`` 的绿谷上下文里
反射一次（``inspect(sync_conn)``），结果解析成纯 dict 供各用例断言。

无库环境跳过（``requires_pg``），CI 无 PG 时不该红。
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import engine

CONTENT_TABLES = ("content_items", "content_entities", "content_metrics", "user_content_state")


def _pg_self_check() -> bool:
    """库是否可达（用 asyncpg 直连 ``engine.url``，不进 SQLAlchemy 事件循环）。"""

    async def _go() -> bool:
        url = engine.url.set(drivername="postgresql")
        conn = await asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database=url.database,
        )
        try:
            return True
        finally:
            await conn.close()

    try:
        return asyncio.run(_go())
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_self_check(),
    reason="PostgreSQL unavailable (schema invariants need alembic-upgraded DB)",
)


def _reflect(sync_conn: Any) -> dict[str, Any]:
    """同步反射全量 schema（只在 ``run_sync`` 的绿谷上下文里调用）。"""
    insp = inspect(sync_conn)
    tables = insp.get_table_names()
    present = [t for t in CONTENT_TABLES if t in tables]
    return {
        "tables": tables,
        "columns": {t: [c["name"] for c in insp.get_columns(t)] for t in present},
        "uniques": {
            t: {u["name"]: u["column_names"] for u in insp.get_unique_constraints(t)}
            for t in present
        },
        "pks": {t: insp.get_pk_constraint(t)["constrained_columns"] for t in present},
        "indexes": {
            t: {i["name"]: i["column_names"] for i in insp.get_indexes(t)} for t in present
        },
        # 部分唯一索引必须连 ``unique`` 标志一起钉，只看列名会被普通索引蒙混过关。
        "indexes_unique": {
            t: {i["name"]: bool(i.get("unique")) for i in insp.get_indexes(t)} for t in present
        },
        "fks": {
            t: [
                {
                    "constrained_columns": fk["constrained_columns"],
                    "referred_table": fk["referred_table"],
                    "referred_columns": fk["referred_columns"],
                    "ondelete": (fk.get("options") or {}).get("ondelete"),
                }
                for fk in insp.get_foreign_keys(t)
            ]
            for t in present
        },
    }


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """对已迁移的库反射一次：``inspect(engine.sync_engine)`` 的等价安全版本。"""

    async def _load() -> dict[str, Any]:
        # 独立 engine：全局 engine 的连接池可能绑在别的测试已关闭的事件循环上，
        # 直接复用会在全量跑时抛 `Event loop is closed`（实测过）。
        eng = create_async_engine(engine.url.render_as_string(hide_password=False))
        try:
            async with eng.connect() as conn:
                return await conn.run_sync(_reflect)
        finally:
            await eng.dispose()

    return asyncio.run(_load())


@requires_pg
def test_all_four_tables_exist(schema: dict[str, Any]) -> None:
    names = set(schema["tables"])
    for table in CONTENT_TABLES:
        assert table in names, f"{table} missing from migrated schema"


@requires_pg
def test_content_items_has_no_body_ref_or_user_state_columns(schema: dict[str, Any]) -> None:
    """2026-11-11 裁定：只存链接；用户状态不落事实层。"""
    cols = set(schema["columns"]["content_items"])
    assert "body_ref" not in cols
    assert "status" not in cols
    assert "read_at" not in cols


@requires_pg
def test_content_items_unique_source_hash(schema: dict[str, Any]) -> None:
    unique = schema["uniques"]["content_items"]
    assert unique.get("uq_content_items_source_hash") == ["source_name", "raw_hash"]


@requires_pg
def test_content_entities_composite_pk(schema: dict[str, Any]) -> None:
    assert schema["pks"]["content_entities"] == ["content_id", "entity_type", "entity_id"]


@requires_pg
def test_user_content_state_composite_pk(schema: dict[str, Any]) -> None:
    assert schema["pks"]["user_content_state"] == ["user_id", "content_id"]


@requires_pg
def test_entity_lookup_indexes_exist(schema: dict[str, Any]) -> None:
    """两张子表的"按实体查内容/指标"索引——读面性能的承重索引。"""
    assert schema["indexes"]["content_entities"].get("ix_content_entities_entity") == [
        "entity_type",
        "entity_id",
    ]
    assert schema["indexes"]["content_metrics"].get("ix_content_metrics_entity") == [
        "entity_type",
        "entity_id",
        "metric_key",
    ]


@requires_pg
def test_content_metrics_unique_key(schema: dict[str, Any]) -> None:
    unique = schema["uniques"]["content_metrics"]
    assert unique.get("uq_content_metrics_key") == [
        "content_id",
        "entity_id",
        "metric_key",
        "period",
    ]


def test_content_metrics_nullperiod_partial_unique_index(schema: dict[str, Any]) -> None:
    """``period IS NULL`` 的去重索引必须在真库里且是 unique。

    ``uq_content_metrics_key`` 对 NULL 行不生效（PG 视 NULL 互不相等），没有这条
    部分唯一索引，非期间型指标（``core_view`` 等）重复抽取就会插重复行。
    """
    assert schema["indexes"]["content_metrics"].get("uq_content_metrics_key_nullperiod") == [
        "content_id",
        "entity_id",
        "metric_key",
    ]
    assert schema["indexes_unique"]["content_metrics"]["uq_content_metrics_key_nullperiod"] is True


@requires_pg
def test_content_entities_fk_cascades(schema: dict[str, Any]) -> None:
    fk = next(
        fk
        for fk in schema["fks"]["content_entities"]
        if fk["constrained_columns"] == ["content_id"]
    )
    assert fk["referred_table"] == "content_items"
    assert fk["referred_columns"] == ["id"]
    assert fk["ondelete"] == "CASCADE"
