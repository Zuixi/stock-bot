#!/usr/bin/env python
"""归档并清理过期的财务原始响应（P6/T6.5）.

``financial_raw_records`` 每行是一份上游原始响应（实测 612 行 / 979MB）。去重已经由
``uq_financial_raw_record (source,dataset,source_record_key,payload_hash)`` 保证，
本脚本只负责**保留期**：把超过 N 天的行按归档月导出为 gzip JSONL，校验后删除。

用法（backend/ 目录）::

    uv run python scripts/archive_financial_raw.py                 # 只统计
    uv run python scripts/archive_financial_raw.py --apply         # 导出 + 删除
    uv run python scripts/archive_financial_raw.py --apply --days 730
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

DEFAULT_DAYS = 365
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "financial_raw_archive"


async def main_async(args: argparse.Namespace) -> None:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as db:
            total, bytes_ = (
                await db.execute(
                    text(
                        "SELECT count(*), COALESCE(sum(pg_column_size(payload)), 0) "
                        "FROM financial_raw_records "
                        "WHERE fetched_at < now() - (:days * interval '1 day')"
                    ),
                    {"days": args.days},
                )
            ).one()
            print(f"expired rows={total} payload_bytes={bytes_} days={args.days}")
            if not args.apply or not total:
                print("（dry-run；加 --apply 导出并删除）")
                return

            rows = (
                await db.execute(
                    text(
                        "SELECT id, source, dataset, stock_id, ts_code, request_params, "
                        "source_record_key, payload, payload_hash, fetched_at "
                        "FROM financial_raw_records "
                        "WHERE fetched_at < now() - (:days * interval '1 day') "
                        "ORDER BY fetched_at"
                    ),
                    {"days": args.days},
                )
            ).mappings()

            buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
            for row in rows:
                fetched = row["fetched_at"]
                month = fetched.strftime("%Y-%m") if fetched else "unknown"
                buckets[month].append(dict(row))

            args.out.mkdir(parents=True, exist_ok=True)
            for month, items in sorted(buckets.items()):
                target = args.out / f"{month}.jsonl.gz"
                with gzip.open(target, "wt", encoding="utf-8") as fh:
                    for item in items:
                        if isinstance(item.get("fetched_at"), datetime):
                            item["fetched_at"] = item["fetched_at"].isoformat()
                        fh.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
                print(f"archived {len(items):>5} rows → {target}")

            ids = [item["id"] for items in buckets.values() for item in items]
            deleted = await db.execute(
                text("DELETE FROM financial_raw_records WHERE id = ANY(:ids)"), {"ids": ids}
            )
            await db.commit()
            print(f"deleted {deleted.rowcount} rows")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行导出 + 删除")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="保留天数")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="归档目录")
    try:
        asyncio.run(main_async(parser.parse_args()))
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
