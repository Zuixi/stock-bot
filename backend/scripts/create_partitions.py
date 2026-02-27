"""Create missing annual partitions for daily_quotes and stock_features.

Run this script once a year (or at startup) to ensure the current year's
partition exists before any data is written.

Usage:
    python scripts/create_partitions.py
    python scripts/create_partitions.py --years 2023 2024 2025 2026 2027
"""

import argparse
import asyncio
import logging
from datetime import datetime

from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PARTITION_TABLES = ["daily_quotes", "stock_features"]


def partition_sql(table: str, year: int) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table}_{year} "
        f"PARTITION OF {table} "
        f"FOR VALUES FROM ('{year}-01-01') TO ('{year + 1}-01-01');"
    )


async def create_partitions(years: list[int]) -> None:
    from app.core.database import engine

    async with engine.begin() as conn:
        for table in PARTITION_TABLES:
            for year in years:
                sql = partition_sql(table, year)
                await conn.execute(text(sql))
                logger.info("Ensured partition: %s_%d", table, year)


def parse_args() -> argparse.Namespace:
    current_year = datetime.now().year
    parser = argparse.ArgumentParser(description="Create annual table partitions")
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=[current_year - 1, current_year, current_year + 1],
        help="Years to create partitions for",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(create_partitions(args.years))
