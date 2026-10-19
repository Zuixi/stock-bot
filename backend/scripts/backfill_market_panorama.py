"""回补「A股全景」三张日表：广度 / 11 桶 / 申万一级行业。

```bash
cd backend
uv run python scripts/backfill_market_panorama.py --dry-run
uv run python scripts/backfill_market_panorama.py --from 2025-10-15
uv run python scripts/backfill_market_panorama.py --from 2025-10-15 --to 2026-10-16
```

口径与写入口在 `app/services/market_panorama_ingest_service.py`（日终 job 共用同一套
SQL，避免两份桶边界漂移）。默认起点 `2025-10-15`（限价安全窗口），重复运行幂等
（ON CONFLICT），可中断续跑。
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from sqlalchemy import text

from app.core.database import async_session_factory
from app.services import market_panorama_ingest_service as ingest

DEFAULT_START = date(2025, 10, 15)


async def main(args: argparse.Namespace) -> int:
    async with async_session_factory() as db:
        bounds = (
            await db.execute(text("SELECT min(trade_date), max(trade_date) FROM daily_quotes"))
        ).one()
        start = args.from_date or max(bounds[0], DEFAULT_START)
        end = args.to_date or bounds[1]
        if start is None or end is None:
            print("daily_quotes 为空，无可回补")
            return 0

        breadth_rows = await ingest.compute_breadth_rows(db, start, end)
        industry_rows = await ingest.compute_industry_rows(db, start, end)

        print(f"范围 {start} → {end}：广度 {len(breadth_rows)} 天，行业 {len(industry_rows)} 行")
        if args.dry_run:
            print("dry-run：不写库")
            return 0

        await ingest.upsert_rows(db, breadth_rows, industry_rows, source="backfill")
        await db.commit()

    print(
        f"写入完成：广度 {len(breadth_rows)} 天 / 桶 {len(breadth_rows) * 11} 行 / "
        f"行业 {len(industry_rows)} 行"
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回补 A股全景三张日表")
    parser.add_argument("--from", dest="from_date", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="to_date", type=date.fromisoformat, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
