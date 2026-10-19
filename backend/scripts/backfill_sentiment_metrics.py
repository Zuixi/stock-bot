"""补 `market_sentiment_daily` 的两列：今开溢价均值 + 昨日涨停样本数。

```bash
cd backend
# 预演：只算不写
uv run python scripts/backfill_sentiment_metrics.py --dry-run
# 只补最后 60 个交易日
uv run python scripts/backfill_sentiment_metrics.py --from 2026-07-20
# 重算并覆写已有值（默认只补 NULL 的 yzt_avg_open_premium）
uv run python scripts/backfill_sentiment_metrics.py --force
```

**为什么需要它**：2026-10-18 之前 `persist_snapshot` 只写 8 个聚合数字，
`yzt_avg_open_premium` 与 `yzt_n` 每天算出却没落库。补列后历史行是 NULL，
而「今开溢价」卡片的历史追踪需要这些点。

**口径与 `limit_up_calculator.yesterday_payload` 逐字一致**（不另写一套）：

- 昨日涨停股 = 前一交易日 `close >= stock_price_limits.up_limit - 0.005` 的票
  （交易所限价口径，不用名称/代码前缀推算）；
- 今日开盘溢价 = `(daily_quotes.open − stock_price_limits.pre_close) / pre_close × 100`；
  停牌或缺 pre_close 的票**不进均值**，但仍计入 `yzt_n`（样本数 ≠ 有效数）；
- 前一交易日取自 `stock_price_limits` 自身持有的日期（不是 weekday 推算）；
- 缺失落 NULL，**绝不填 0**（0% 是"开盘恰好平昨收"的事实断言）。

**安全边界**：只 UPDATE 这两列，**不碰**任何既有事实（涨停家数/晋级率/来源标记等）；
默认只修 `yzt_avg_open_premium IS NULL` 的行，可中断续跑。
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.core.database import async_session_factory

#: 昨日涨停 cohort → 当日开盘溢价（一次算出全区间，避免逐日 244 次窗口查询）。
#: `n` 计 cohort 全部成员（含今日停牌），`premium` 只对今日有行情且有 pre_close 的票取均值。
_PREMIUM_SQL = text(
    """
WITH cal AS (
    SELECT DISTINCT trade_date
    FROM stock_price_limits
    WHERE trade_date BETWEEN CAST(:start AS date) - INTERVAL '15 days' AND :end
),
days AS (
    SELECT trade_date, lag(trade_date) OVER (ORDER BY trade_date) AS prev_date
    FROM cal
),
lu AS (
    SELECT l.trade_date, l.stock_id
    FROM stock_price_limits l
    JOIN daily_quotes q
      ON q.trade_date = l.trade_date AND q.stock_id = l.stock_id
    WHERE q.close >= l.up_limit - 0.005
)
SELECT d.trade_date,
       count(lu.stock_id) AS yzt_n,
       round(avg((q.open - l.pre_close) / l.pre_close * 100)::numeric, 4) AS premium
FROM days d
JOIN lu ON lu.trade_date = d.prev_date
LEFT JOIN stock_price_limits l
       ON l.trade_date = d.trade_date AND l.stock_id = lu.stock_id
LEFT JOIN daily_quotes q
       ON q.trade_date = d.trade_date AND q.stock_id = lu.stock_id
WHERE d.trade_date BETWEEN :start AND :end
GROUP BY d.trade_date
"""
)

_TARGETS_SQL = text(
    """
SELECT trade_date
FROM market_sentiment_daily
WHERE trade_date BETWEEN :start AND :end
  AND (CAST(:force AS boolean) OR yzt_avg_open_premium IS NULL)
ORDER BY trade_date
"""
)

_UPDATE_SQL = text(
    """
UPDATE market_sentiment_daily
SET yzt_avg_open_premium = :premium,
    yzt_n = :yzt_n
WHERE trade_date = :trade_date
"""
)


@dataclass
class Summary:
    targets: int = 0
    updated: int = 0
    no_cohort: int = 0
    no_premium: int = 0


async def main(args: argparse.Namespace) -> int:
    async with async_session_factory() as db:
        bounds = (
            await db.execute(
                text("SELECT min(trade_date), max(trade_date) FROM market_sentiment_daily")
            )
        ).one()
        start = args.from_date or bounds[0]
        end = args.to_date or bounds[1]
        if start is None or end is None:
            print("market_sentiment_daily 为空，无可回补")
            return 0

        rows = (await db.execute(_PREMIUM_SQL, {"start": start, "end": end})).all()
        computed = {r.trade_date: (r.yzt_n, r.premium) for r in rows}

        targets = (
            (await db.execute(_TARGETS_SQL, {"start": start, "end": end, "force": args.force}))
            .scalars()
            .all()
        )

        summary = Summary(targets=len(targets))
        for day in targets:
            if day not in computed:
                summary.no_cohort += 1
                continue
            yzt_n, premium = computed[day]
            if premium is None:
                summary.no_premium += 1
            # Decimal（numeric round 的结果）→ float：列是 Float，asyncpg 也接受 Decimal。
            value = float(premium) if isinstance(premium, Decimal) else premium
            if args.dry_run:
                summary.updated += 1
                continue
            await db.execute(
                _UPDATE_SQL,
                {"trade_date": day, "premium": value, "yzt_n": int(yzt_n or 0)},
            )
            summary.updated += 1
        if not args.dry_run:
            await db.commit()

    label = "would update" if args.dry_run else "updated"
    print(
        f"{label}: {summary.updated}/{summary.targets} rows"
        f"（无 cohort 跳过 {summary.no_cohort}，有 cohort 但溢价全缺 {summary.no_premium}）"
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="补情绪表今开溢价与样本数")
    parser.add_argument("--from", dest="from_date", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="to_date", type=date.fromisoformat, default=None)
    parser.add_argument("--dry-run", action="store_true", help="只算不写")
    parser.add_argument(
        "--force",
        action="store_true",
        help="重算并覆写已有值（默认只补 yzt_avg_open_premium 为 NULL 的行）",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
