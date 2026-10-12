#!/usr/bin/env python3
"""回补 spec §S1.8 的 6 个全市场情绪/宽度指标到 `industry_metrics`（`industry_key='market'`）。

这是**运维入口**，不是读路：读路是 S1.2 的 `GET /api/v1/metrics/{latest,series}`。

用法（cwd = `backend/`，走 `DATABASE_URL`；本机无 `.env` 时显式给 URL）::

    # 默认：回补最近 400 个日历日的日频序列（≥1 年）
    DATABASE_URL=postgresql+asyncpg://... .venv/bin/python scripts/backfill_market_sentiment.py

    # 指定区间
    .venv/bin/python scripts/backfill_market_sentiment.py --from 2025-10-01 --to 2026-10-09

    # 回补后只打印覆盖率对账（不写库）
    .venv/bin/python scripts/backfill_market_sentiment.py --coverage-only

退出码：0 = 成功；1 = 参数非法 / 无 `daily_quotes` 数据。

**幂等**：唯一键 `(industry_key, stock_id, metric_key, source, freq, period)` +
`ON CONFLICT DO UPDATE`，同一区间重跑新增行数 = 0（ADR 0014 的 B 类可回补）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.services import market_sentiment_service as svc  # noqa: E402


def _parse_day(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


async def _latest_quote_day(db: AsyncSession) -> date | None:
    """`daily_quotes` 的最近交易日（`None` = 表空）。回补窗口的上界锚在**数据**上，
    不锚在 `date.today()`：今天休市时按今天回补只会得到一片空。"""
    value = (await db.execute(text("SELECT max(trade_date) FROM daily_quotes"))).scalar_one()
    return cast("date | None", value)


async def _run(args: argparse.Namespace) -> int:
    # 覆盖率窗口默认 = 本次回补区间（否则分母是**整库**的交易日数，看起来像大面积缺数）
    coverage_lo = args.coverage_from
    coverage_hi = args.coverage_to
    async with async_session_factory() as db:
        if not args.coverage_only:
            if args.day_from or args.day_to:
                day_hi = args.day_to or await _latest_quote_day(db)
                if day_hi is None:
                    print("✘ daily_quotes 为空 —— 没有可回补的底座", file=sys.stderr)
                    return 1
                day_lo = args.day_from or (day_hi - timedelta(days=args.lookback))
                stats = await svc.upsert_metric_range(db, day_lo, day_hi, warmup_days=args.warmup)
                await db.commit()
                print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
                coverage_lo, coverage_hi = day_lo, day_hi
            else:
                stats = await svc.backfill_sentiment_metrics(
                    db, lookback_days=args.lookback, warmup_days=args.warmup
                )
                print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
                coverage_lo, coverage_hi = stats.day_lo, stats.day_hi
            if stats.quote_days == 0:
                print("✘ 区间内 daily_quotes 无任何交易日 —— 没有可回补的底座", file=sys.stderr)
                return 1

        coverage = await svc.metric_coverage(db, day_lo=coverage_lo, day_hi=coverage_hi)
        print(
            "\n=== 覆盖率对账（covered = 有值 / null = 有行但值为 NULL / missing = 交易日无行）==="
        )
        header = (
            f"{'metric_key':<28}{'from':<12}{'to':<12}"
            f"{'rows':>6}{'covered':>9}{'null':>7}{'missing':>9}  reasons"
        )
        print(header)
        print("-" * len(header))
        for cov in coverage:
            print(
                f"{cov.metric_key:<28}"
                f"{cov.period_from.isoformat() if cov.period_from else '—':<12}"
                f"{cov.period_to.isoformat() if cov.period_to else '—':<12}"
                f"{cov.rows:>6}{cov.covered_days:>9}{cov.null_days:>7}"
                f"{len(cov.missing_days):>9}  "
                f"{json.dumps(cov.missing_reasons, ensure_ascii=False)}"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from", dest="day_from", type=_parse_day, default=None, help="起始交易日 YYYY-MM-DD"
    )
    parser.add_argument(
        "--to", dest="day_to", type=_parse_day, default=None, help="结束交易日 YYYY-MM-DD"
    )
    parser.add_argument(
        "--lookback", type=int, default=400, help="未给 --from 时回看的日历日数（默认 400 ≥ 1 年）"
    )
    parser.add_argument(
        "--warmup", type=int, default=60, help="MA20 窗口预热日历日数（默认 60 > 19 个交易日）"
    )
    parser.add_argument("--coverage-only", action="store_true", help="只打印覆盖率对账，不写库")
    parser.add_argument("--coverage-from", type=_parse_day, default=None)
    parser.add_argument("--coverage-to", type=_parse_day, default=None)
    args = parser.parse_args()
    if args.lookback <= 0 or args.warmup < 30:
        print(
            "✘ --lookback 须 > 0 且 --warmup 须 >= 30（MA20 需 19 个交易日的历史）", file=sys.stderr
        )
        return 1
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
