"""存储维护：分区自动创建 + 保留策略（P6）.

两件事都不接外部 API、不改业务语义：

- ``ensure_daily_quotes_partitions``：每月 1 日与启动时补未来分区。**缺分区 = 写入直接
  失败**（比静默丢行好），所以这个 job 的失败必须进 ``job:failures``。
- ``run_retention``：清理账本与盘中快照。原则是"只删有明确保留期的派生/账本数据"，
  行情/限售/财务原始行一律不删（它们的保留由归档脚本负责）。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(value.year + value.month // 12, value.month % 12 + 1, 1)


async def ensure_daily_quotes_partitions(
    db: Any, *, through: date | None = None, parent: str = "daily_quotes"
) -> list[str]:
    """补 ``今天 ~ through``（默认 +2 个月）之间缺失的月分区；非分区表直接返回空。"""
    relkind = (
        await db.execute(text("SELECT relkind FROM pg_class WHERE relname = :t"), {"t": parent})
    ).scalar_one_or_none()
    if isinstance(relkind, (bytes, bytearray)):
        relkind = relkind.decode()
    if relkind != "p":
        return []

    today = date.today()
    end = through or _next_month(_next_month(today))
    existing = set(
        (
            await db.execute(
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
    cur = _month_start(today)
    while cur <= _month_start(end):
        name = f"{parent}_{cur:%Y_%m}"
        if name not in existing:
            nxt = _next_month(cur)
            await db.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {parent} "
                    f"FOR VALUES FROM ('{cur.isoformat()}') TO ('{nxt.isoformat()}')"
                )
            )
            created.append(name)
        cur = _next_month(cur)
    return created


#: 保留期（天）。改这里等于改契约，需同步 docs/deployment/operations.md。
RETENTION_DAYS: dict[str, int] = {
    "ingest_quarantine": 90,
    "data_quality_runs": 180,
    "ingest_batches": 365,
    "sector_moneyflow_poll": 90,
    "financial_raw_records": 365,
}


async def run_retention(db: Any, *, dry_run: bool = False) -> dict[str, int]:
    """按保留期清理；返回每张表的删除行数（``dry_run`` 只数不删）。"""
    out: dict[str, int] = {}
    statements: list[tuple[str, str, str]] = [
        (
            "ingest_quarantine",
            "SELECT count(*) FROM ingest_quarantine "
            "WHERE created_at < now() - (:days * interval '1 day')",
            "DELETE FROM ingest_quarantine WHERE created_at < now() - (:days * interval '1 day')",
        ),
        (
            "data_quality_runs",
            "SELECT count(*) FROM data_quality_runs "
            "WHERE checked_at < now() - (:days * interval '1 day')",
            "DELETE FROM data_quality_runs WHERE checked_at < now() - (:days * interval '1 day')",
        ),
        (
            "ingest_batches",
            "SELECT count(*) FROM ingest_batches "
            "WHERE started_at < now() - (:days * interval '1 day')",
            "DELETE FROM ingest_batches WHERE started_at < now() - (:days * interval '1 day')",
        ),
        (
            "sector_moneyflow_poll",
            "SELECT count(*) FROM sector_moneyflow_snapshots "
            "WHERE capture_kind = 'poll' AND trade_date < current_date - (:days_int)::int",
            "DELETE FROM sector_moneyflow_snapshots "
            "WHERE capture_kind = 'poll' AND trade_date < current_date - (:days_int)::int",
        ),
        (
            "financial_raw_records",
            "SELECT count(*) FROM financial_raw_records "
            "WHERE fetched_at < now() - (:days * interval '1 day')",
            "DELETE FROM financial_raw_records "
            "WHERE fetched_at < now() - (:days * interval '1 day')",
        ),
    ]
    for key, count_sql, delete_sql in statements:
        days = RETENTION_DAYS[key]
        params = {"days": days, "days_int": days}
        if dry_run:
            count = int((await db.execute(text(count_sql), params)).scalar_one())
        else:
            count = int((await db.execute(text(delete_sql), params)).rowcount or 0)
        out[key] = count
        if count:
            logger.info("RETENTION %s deleted=%d days=%d", key, count, days)
    return out
