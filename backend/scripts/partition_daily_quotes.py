#!/usr/bin/env python
"""把 ``daily_quotes`` 从普通表切换为**按月 RANGE 分区表**（P6）.

为什么不放 Alembic：需要 ``RENAME`` 旧表、保留回滚窗口、按需重建索引，迁移无法
安全表达这些运维步骤。脚本默认 ``--dry-run``，任何写操作都要显式 ``--apply``。

用法（backend/ 目录下）::

    uv run python scripts/partition_daily_quotes.py                 # 只打印计划
    uv run python scripts/partition_daily_quotes.py --apply         # 执行切换（维护窗口）
    uv run python scripts/partition_daily_quotes.py --rollback      # 7 天内回滚
    uv run python scripts/partition_daily_quotes.py --cleanup       # 删除旧表
    uv run python scripts/partition_daily_quotes.py --ensure-partitions  # 只补未来分区

约束（PostgreSQL 硬要求）：分区表的所有唯一约束必须包含分区键 ``trade_date``。
本脚本保留 ``uq_daily_quotes_stock_date (stock_id, trade_date)``（upsert 依赖它）并把
主键改为 ``(id, trade_date)``。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

OLD_TABLE = "daily_quotes_old"
NEW_TABLE = "daily_quotes_part"
TABLE = "daily_quotes"

#: 旧表上的索引/约束名——切换时要先让位给新表（PG 索引名是 schema 级唯一）。
INDEXES = (
    "idx_daily_quotes_date",
    "idx_daily_quotes_date_pct",
    "idx_daily_quotes_date_amount",
    "idx_daily_quotes_stock_id_adj_factor",
)
CONSTRAINTS = ("daily_quotes_pkey", "uq_daily_quotes_stock_date")


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(value.year + value.month // 12, value.month % 12 + 1, 1)


def _months(start: date, end: date) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cur = _month_start(start)
    while cur <= _month_start(end):
        out.append((cur, _next_month(cur)))
        cur = _next_month(cur)
    return out


def _partition_name(month: date) -> str:
    return f"{TABLE}_{month:%Y_%m}"


async def _relkind(conn) -> str | None:  # type: ignore[no-untyped-def]
    value = (
        await conn.execute(text("SELECT relkind FROM pg_class WHERE relname = :t"), {"t": TABLE})
    ).scalar_one_or_none()
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return value


async def _ensure_partitions(conn, *, start: date, end: date) -> list[str]:  # type: ignore[no-untyped-def]
    parent = TABLE if await _relkind(conn) == "p" else NEW_TABLE
    existing = set(
        (
            await conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_inherits i ON i.inhrelid = c.oid "
                    "WHERE i.inhparent = CAST(:p AS regclass)"
                ),
                {"p": parent},
            )
        ).scalars()
    )
    created: list[str] = []
    for month, nxt in _months(start, end):
        name = _partition_name(month)
        if name in existing:
            continue
        await conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {parent} "
                f"FOR VALUES FROM ('{month.isoformat()}') TO ('{nxt.isoformat()}')"
            )
        )
        created.append(name)
    return created


async def _plan(conn) -> dict[str, object]:  # type: ignore[no-untyped-def]
    stats = (
        await conn.execute(
            text(
                "SELECT count(*), min(trade_date), max(trade_date), "
                "count(DISTINCT trade_date), sum(stock_id) FROM daily_quotes"
            )
        )
    ).one()
    size = (
        await conn.execute(text("SELECT pg_size_pretty(pg_total_relation_size('daily_quotes'))"))
    ).scalar_one()
    return {
        "rows": int(stats[0]),
        "min_date": stats[1],
        "max_date": stats[2],
        "days": int(stats[3] or 0),
        "stock_id_sum": int(stats[4] or 0),
        "size": size,
        "relkind": await _relkind(conn),
    }


async def _apply(conn) -> None:  # type: ignore[no-untyped-def]
    plan = await _plan(conn)
    if plan["relkind"] == "p":
        print("daily_quotes 已经是分区表；只补未来分区。")
        created = await _ensure_partitions(
            conn,
            start=_month_start(plan["max_date"]),
            end=_next_month(date.today()),  # type: ignore[arg-type]
        )
        print(f"created partitions: {created}")
        return
    if plan["rows"] == 0:
        raise SystemExit("daily_quotes 为空，拒绝切换（先确认库连对了）")

    start = _month_start(plan["min_date"])  # type: ignore[arg-type]
    end = _next_month(date.today())
    print(f"计划：{plan['rows']} 行 / {plan['size']} / {len(_months(start, end))} 个分区")

    await conn.execute(text(f"DROP TABLE IF EXISTS {NEW_TABLE} CASCADE"))
    await conn.execute(
        text(
            f"CREATE TABLE {NEW_TABLE} "
            f"(LIKE {TABLE} INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING STORAGE "
            f"INCLUDING COMMENTS) PARTITION BY RANGE (trade_date)"
        )
    )
    # 分区表唯一约束必须含分区键；主键同样。名字先加 _p 后缀，切换后再改回。
    await conn.execute(
        text(
            f"ALTER TABLE {NEW_TABLE} ADD CONSTRAINT daily_quotes_pkey_p "
            "PRIMARY KEY (id, trade_date)"
        )
    )
    await conn.execute(
        text(
            f"ALTER TABLE {NEW_TABLE} ADD CONSTRAINT uq_daily_quotes_stock_date_p "
            "UNIQUE (stock_id, trade_date)"
        )
    )
    await _ensure_partitions(conn, start=start, end=end)
    # 索引先建在父表上（自动下发到每个分区），名字加 _p 后缀待切换后再改回。
    for ddl in (
        f"CREATE INDEX idx_daily_quotes_date_p ON {NEW_TABLE} (trade_date)",
        f"CREATE INDEX idx_daily_quotes_date_pct_p ON {NEW_TABLE} (trade_date, pct_chg)",
        f"CREATE INDEX idx_daily_quotes_date_amount_p ON {NEW_TABLE} (trade_date, amount)",
        f"CREATE INDEX idx_daily_quotes_stock_id_adj_factor_p ON {NEW_TABLE} "
        "(stock_id) WHERE adj_factor IS NOT NULL",
    ):
        await conn.execute(text(ddl))

    print("复制数据（单事务）...")
    await conn.execute(text(f"INSERT INTO {NEW_TABLE} SELECT * FROM {TABLE}"))

    check = (
        await conn.execute(
            text(f"SELECT count(*), count(DISTINCT trade_date), sum(stock_id) FROM {NEW_TABLE}")
        )
    ).one()
    if (int(check[0]), int(check[1] or 0), int(check[2] or 0)) != (
        plan["rows"],
        plan["days"],
        plan["stock_id_sum"],
    ):
        raise SystemExit(f"复制校验失败：source={plan} copy={check}")

    # 让旧表的索引/约束名让位（PG 索引名是 schema 级唯一）
    for name in INDEXES:
        await conn.execute(text(f"ALTER INDEX IF EXISTS {name} RENAME TO {name}_old"))
    for name in CONSTRAINTS:
        await conn.execute(text(f"ALTER TABLE {TABLE} RENAME CONSTRAINT {name} TO {name}_old"))

    await conn.execute(text(f"ALTER TABLE {TABLE} RENAME TO {OLD_TABLE}"))
    await conn.execute(text(f"ALTER TABLE {NEW_TABLE} RENAME TO {TABLE}"))
    await conn.execute(
        text(f"ALTER TABLE {TABLE} RENAME CONSTRAINT daily_quotes_pkey_p TO daily_quotes_pkey")
    )
    await conn.execute(
        text(
            f"ALTER TABLE {TABLE} RENAME CONSTRAINT uq_daily_quotes_stock_date_p "
            "TO uq_daily_quotes_stock_date"
        )
    )
    for ddl in (
        "ALTER INDEX idx_daily_quotes_date_p RENAME TO idx_daily_quotes_date",
        "ALTER INDEX idx_daily_quotes_date_pct_p RENAME TO idx_daily_quotes_date_pct",
        "ALTER INDEX idx_daily_quotes_date_amount_p RENAME TO idx_daily_quotes_date_amount",
        "ALTER INDEX idx_daily_quotes_stock_id_adj_factor_p "
        "RENAME TO idx_daily_quotes_stock_id_adj_factor",
    ):
        await conn.execute(text(ddl))
    await conn.execute(text(f"ANALYZE {TABLE}"))
    print(f"切换完成。旧表保留为 {OLD_TABLE}；7 天后跑 --cleanup。")


async def _rollback(conn) -> None:  # type: ignore[no-untyped-def]
    if await _relkind(conn) != "p":
        raise SystemExit("daily_quotes 不是分区表，无需回滚")
    exists_old = (
        await conn.execute(
            text("SELECT count(*) FROM pg_class WHERE relname = :t"), {"t": OLD_TABLE}
        )
    ).scalar_one()
    if not exists_old:
        raise SystemExit(f"{OLD_TABLE} 不存在，无法回滚")
    for name in INDEXES:
        await conn.execute(text(f"ALTER INDEX IF EXISTS {name} RENAME TO {name}_p"))
    await conn.execute(text(f"ALTER TABLE {TABLE} RENAME TO {NEW_TABLE}"))
    await conn.execute(text(f"ALTER TABLE {OLD_TABLE} RENAME TO {TABLE}"))
    for name in INDEXES:
        await conn.execute(text(f"ALTER INDEX IF EXISTS {name}_old RENAME TO {name}"))
    print("已回滚到旧表。")


async def _cleanup(conn) -> None:  # type: ignore[no-untyped-def]
    await conn.execute(text(f"DROP TABLE IF EXISTS {OLD_TABLE} CASCADE"))
    print(f"已删除 {OLD_TABLE}。")


async def main_async(args: argparse.Namespace) -> None:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            if args.rollback:
                await _rollback(conn)
            elif args.cleanup:
                await _cleanup(conn)
            elif args.ensure_partitions:
                created = await _ensure_partitions(
                    conn, start=_month_start(date.today()), end=_next_month(date.today())
                )
                print(f"created partitions: {created}")
            elif args.apply:
                await _apply(conn)
            else:
                print(await _plan(conn))
                print("（dry-run；加 --apply 执行）")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="执行切换（维护窗口）")
    group.add_argument("--rollback", action="store_true", help="回滚到旧表")
    group.add_argument("--cleanup", action="store_true", help="删除旧表")
    group.add_argument("--ensure-partitions", action="store_true", help="只补未来分区")
    main_async_args = parser.parse_args()
    try:
        asyncio.run(main_async(main_async_args))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 运维脚本要给出可读错误
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
