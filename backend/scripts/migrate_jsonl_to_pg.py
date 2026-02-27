"""Migrate existing JSONL snapshots into PostgreSQL.

Usage:
    python scripts/migrate_jsonl_to_pg.py --snapshot-dir ../data/universe
    python scripts/migrate_jsonl_to_pg.py --snapshot-dir ../data/universe --dry-run
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate JSONL snapshots to PostgreSQL")
    parser.add_argument("--snapshot-dir", required=True, help="Path to data/universe/")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no DB writes")
    parser.add_argument("--latest-only", action="store_true", help="Only migrate the newest snapshot")
    return parser.parse_args()


def find_snapshots(base: Path) -> list[Path]:
    return sorted(
        [d for d in base.iterdir() if d.is_dir() and d.name.startswith("snapshot=")],
        key=lambda p: p.name,
    )


def iter_records(snapshot: Path):
    for exchange_dir in snapshot.iterdir():
        if not exchange_dir.is_dir():
            continue
        for jsonl_file in exchange_dir.glob("class=*.jsonl"):
            with jsonl_file.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning("Skipping invalid JSON in %s: %s", jsonl_file, e)


async def migrate_snapshot(snapshot: Path, dry_run: bool) -> int:
    from app.core.database import async_session_factory
    from app.models.stock import Stock, StockHistory
    from app.repositories import stock_repo

    records = list(iter_records(snapshot))
    logger.info("Found %d records in %s", len(records), snapshot.name)

    if dry_run:
        logger.info("[DRY RUN] Would insert %d records", len(records))
        return len(records)

    count = 0
    async with async_session_factory() as db:
        for rec in records:
            asof_raw = rec.get("asof")
            asof = datetime.fromisoformat(asof_raw) if asof_raw else datetime.utcnow()

            list_date_raw = rec.get("list_date")
            list_date = None
            if list_date_raw and len(list_date_raw) == 8:
                from datetime import date
                list_date = date(
                    int(list_date_raw[:4]),
                    int(list_date_raw[4:6]),
                    int(list_date_raw[6:8]),
                )

            stock = Stock(
                exchange=rec["exchange"],
                symbol=rec["symbol"],
                name=rec["name"],
                full_name=rec.get("full_name"),
                category=rec["category"],
                list_date=list_date,
                csrc_code=rec.get("csrc_code"),
                csrc_desc=rec.get("csrc_desc"),
                province=rec.get("province"),
                status=rec.get("status"),
                asof=asof,
            )
            history = StockHistory(
                exchange=stock.exchange,
                symbol=stock.symbol,
                name=stock.name,
                full_name=stock.full_name,
                category=stock.category,
                list_date=stock.list_date,
                csrc_code=stock.csrc_code,
                csrc_desc=stock.csrc_desc,
                province=stock.province,
                status=stock.status,
                source_url=rec.get("source_url"),
                asof=asof,
                raw=rec.get("raw"),
            )
            await stock_repo.upsert_stock(db, stock)
            await stock_repo.insert_stock_history(db, history)
            count += 1

        await db.commit()

    logger.info("Migrated %d records from %s", count, snapshot.name)
    return count


async def main() -> None:
    args = parse_args()
    base = Path(args.snapshot_dir)
    if not base.exists():
        logger.error("Directory not found: %s", base)
        sys.exit(1)

    snapshots = find_snapshots(base)
    if not snapshots:
        logger.error("No snapshots found in %s", base)
        sys.exit(1)

    if args.latest_only:
        snapshots = [snapshots[-1]]

    total = 0
    for snapshot in snapshots:
        total += await migrate_snapshot(snapshot, args.dry_run)

    logger.info("Done. Total records processed: %d", total)


if __name__ == "__main__":
    asyncio.run(main())
