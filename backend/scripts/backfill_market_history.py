"""历史回补：**一个脚本，两个域**（`sentiment` / `limit_up_stocks`）。

```bash
cd backend
# 预演（只读：不写库、不增强、不 commit）
uv run python scripts/backfill_market_history.py --domain sentiment --dry-run
# 情绪聚合 21 天 → 239 天（E2 安全区间）
uv run python scripts/backfill_market_history.py --domain sentiment
# 个股涨停事实从 0 开始
uv run python scripts/backfill_market_history.py --domain limit_up_stocks
# 冒烟：区间内最近 5 个交易日
uv run python scripts/backfill_market_history.py --domain limit_up_stocks --limit 5
```

**为什么是 2025-10-15 起**（E2）：`streak`/晋级率按 `window_trade_days(lookback) =
lookback + 6`（默认 10+6=16）个交易日窗口算，而窗口取自 `daily_quotes`（2023-05 起）、
限价 `stock_price_limits` 自 2025-09-16 起。限价历史的**前 15 个交易日**的窗口会伸到
限价上线之前 → `LEFT JOIN limits` 落空 → streak 被截平、晋级率系统性低估。第 16 个交易日
（= 2025-10-15）起窗口才完备，到 2026-10-09 共 **239** 天。守卫
`limit_up_service.window_limits_complete` 逐日复核（窗口内**任一日**缺限价即跳过，原因
`window_limits_incomplete`）；`--from` 给更早的日期不会算错，只会被逐日跳过。

**升序执行**：`promo_1to2`/`promo_2to3`/`broken_rate` 读的是**前一日**的 streak（
`promotion_rate(rows, as_of_prev, as_of, ...)`），乱序会把晋级率算错。区间只取
`daily_quotes` 里**真实有行情**的交易日（库里没有持久化交易日历，"哪些日子真有行情"
本身就是最准的可用性判据）。

**逐日原因**（`--dry-run` 只跑只读闸门，通过闸门的日记 `would_write`）：

| 原因码 | 含义 |
|---|---|
| `ok` | 已写入（情绪 1 行 / 个股事实 N 行） |
| `would_write` | dry-run：通过只读闸门、真跑会写 |
| `insufficient_trade_days` | 窗口交易日 < 2（数据起点附近） |
| `price_limits_missing` | 当日无任何限价行 |
| `window_limits_incomplete` | 窗口内存在缺限价的交易日（E2 守卫） |
| `partial_day` | 当日行情行数不足（`is_partial`，半截 ingest）——**不写半截事实** |
| `no_limit_up_rows` / `no_fact_rows` | 实时路径自己的降级判据（原样透传） |
| `error` | 单日异常（已 rollback，不中断整段；有 error 则进程退出码 1） |

**Ruling 18（回补对实时行不可变）**：两个域都以 `backfill` 标记写入，upsert 带
`DO UPDATE ... WHERE <已存储的 source/fact_source> = 'backfill'` 守卫 —— 只修回补行；
当天实时写下的 `local_calc` 行（连同东财增强列）字节不变。所以"黄金日 2026-09-08 不变"
是**保证**，不是"恰好没撞上"。

**不做的事**：不回补东财独有列（`seal_time`/`seal_fund`/`break_count` 是 A 类，历史日
不抓就永久丢 → 恒 NULL，绝不填 0）；不调东财 EOD 增强（历史外呼 239 次既无收益又把成败
绑在第三方可用性上）；不新建 job/队列（`app/scheduler`、`app/workers` 一字未改）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import text

from app.core.database import async_session_factory
from app.repositories import limit_up_repo
from app.services import limit_up_calculator as calc
from app.services import limit_up_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_market_history")

DOMAIN_SENTIMENT = "sentiment"
DOMAIN_LIMIT_UP_STOCKS = "limit_up_stocks"
DOMAINS = (DOMAIN_SENTIMENT, DOMAIN_LIMIT_UP_STOCKS)

# E2 安全起点：限价历史（2025-09-16 起）的第 16 个交易日 = 2025-10-15（到 2026-10-09 共 239 天）。
DEFAULT_START = date(2025, 10, 15)

LOOKBACK = calc.LOOKBACK_TRADE_DAYS
BACKFILL_FACT_SOURCE = limit_up_repo.BACKFILL_SOURCE

REASON_OK = "ok"
REASON_WOULD_WRITE = "would_write"
REASON_INSUFFICIENT_TRADE_DAYS = "insufficient_trade_days"
REASON_PRICE_LIMITS_MISSING = "price_limits_missing"
REASON_WINDOW_INCOMPLETE = "window_limits_incomplete"
REASON_PARTIAL_DAY = "partial_day"
REASON_NO_LIMIT_UP_ROWS = "no_limit_up_rows"
REASON_NO_FACT_ROWS = "no_fact_rows"
REASON_ERROR = "error"

# 交易日区间：真源是 `daily_quotes` 实际有行的日期（与
# `limit_up_repo.list_recent_trade_dates` 同口径），不是 trade_cal 推算或 weekday。
_TRADE_DATES_SQL = text(
    "SELECT DISTINCT trade_date FROM daily_quotes"
    " WHERE trade_date >= :start AND trade_date <= :end"
    " ORDER BY trade_date DESC"
)


@dataclass(frozen=True)
class DayOutcome:
    """单日结果：原因码必填，`detail` 只用于人读（跳过/异常的补充说明）。"""

    trade_date: date
    reason: str
    detail: str | None = None
    rows: int = 0


@dataclass
class BackfillSummary:
    domain: str
    dry_run: bool
    from_date: date
    to_date: date
    days: list[DayOutcome]
    elapsed_s: float = 0.0

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for outcome in self.days:
            counts[outcome.reason] = counts.get(outcome.reason, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def written_days(self) -> int:
        return sum(1 for o in self.days if o.reason == REASON_OK)

    def rows(self) -> int:
        return sum(o.rows for o in self.days)

    def errors(self) -> int:
        return sum(1 for o in self.days if o.reason == REASON_ERROR)

    def format_table(self) -> str:
        """汇总表 + 逐日列出跳过/降级日（"239 天内差值逐日列原因"）。"""
        header = (
            f"=== backfill summary: domain={self.domain} dry_run={self.dry_run}"
            f" range={self.from_date}→{self.to_date} days={len(self.days)}"
            f" written_days={self.written_days()} rows={self.rows()}"
            f" elapsed={self.elapsed_s:.1f}s ==="
        )
        lines = [header, f"{'reason':<28}{'days':>6}{'rows':>8}"]
        for reason, count in self.reason_counts().items():
            rows = sum(o.rows for o in self.days if o.reason == reason)
            lines.append(f"{reason:<28}{count:>6}{rows:>8}")
        skipped = [o for o in self.days if o.reason not in (REASON_OK, REASON_WOULD_WRITE)]
        if skipped:
            lines.append("-- skipped / degraded days --")
            for o in skipped:
                suffix = f"  ({o.detail})" if o.detail else ""
                lines.append(f"{o.trade_date.isoformat()}  {o.reason}{suffix}")
        return "\n".join(lines)


async def list_trade_dates(
    db: AsyncSession, start: date, end: date, *, limit: int = 0
) -> list[date]:
    """区间内**真实有行情**的交易日，**升序**返回。

    SQL 按 DESC 取（`--limit` 取区间内**最近** N 个，与 `backfill_pct_chg.py --limit`
    同语义），返回前统一升序：升序是硬契约（晋级率依赖前一日 streak，乱序会静默算错）。
    """
    rows = (await db.execute(_TRADE_DATES_SQL, {"start": start, "end": end})).scalars().all()
    days = list(rows)
    if limit > 0:
        days = days[:limit]
    return sorted(days)


async def review_day(db: AsyncSession, trade_date: date, lookback: int = LOOKBACK) -> str | None:
    """只读前置闸门：返回跳过原因码；`None` = 可以进入落库。

    顺序即语义：窗口连 2 天都凑不出 → `insufficient_trade_days`；当日无限价 →
    `price_limits_missing`（更本质）；窗口内缺限价 → `window_limits_incomplete`（E2）。
    """
    market_days = await limit_up_repo.list_recent_trade_dates(
        db, trade_date, limit_up_service.window_trade_days(lookback)
    )
    if len(market_days) < 2:
        return REASON_INSUFFICIENT_TRADE_DAYS
    if not await limit_up_repo.has_price_limits(db, trade_date):
        return REASON_PRICE_LIMITS_MISSING
    if not await limit_up_service.window_limits_complete(db, trade_date, lookback):
        return REASON_WINDOW_INCOMPLETE
    return None


async def backfill_sentiment_day(
    db: AsyncSession, trade_date: date, *, dry_run: bool = False
) -> DayOutcome:
    """单日情绪聚合：复用实时路径的 `persist_snapshot`（零新口径），只换 source/增强开关。"""
    reason = await review_day(db, trade_date)
    if reason is not None:
        return DayOutcome(trade_date, reason)
    if dry_run:
        return DayOutcome(trade_date, REASON_WOULD_WRITE)
    try:
        result = await limit_up_service.persist_snapshot(
            db, None, trade_date, fact_source=BACKFILL_FACT_SOURCE, enrich=False
        )
    except Exception as exc:  # noqa: BLE001 —— 单日失败不中断整段；原因码 + rollback 可见
        await db.rollback()
        logger.error("backfill: sentiment %s failed: %s", trade_date, exc, exc_info=True)
        return DayOutcome(trade_date, REASON_ERROR, detail=f"{type(exc).__name__}: {exc}")
    if result.get("status") == "ok":
        await db.commit()
        return DayOutcome(trade_date, REASON_OK, detail=f"zt={result.get('zt_count')}", rows=1)
    return DayOutcome(trade_date, str(result.get("reason") or REASON_NO_LIMIT_UP_ROWS))


async def backfill_stock_day(
    db: AsyncSession, trade_date: date, *, dry_run: bool = False
) -> DayOutcome:
    """单日个股事实：只落判定列（**不**调东财 EOD 增强），半截行情日不写。"""
    reason = await review_day(db, trade_date)
    if reason is not None:
        return DayOutcome(trade_date, reason)
    breadth = await limit_up_repo.fetch_day_breadth(db, trade_date)
    if calc.is_partial(breadth):
        return DayOutcome(trade_date, REASON_PARTIAL_DAY, detail=f"quoted={breadth['quoted']}")
    if dry_run:
        return DayOutcome(trade_date, REASON_WOULD_WRITE)
    try:
        result = await limit_up_service.persist_stock_facts(
            db, trade_date, fact_source=BACKFILL_FACT_SOURCE
        )
    except Exception as exc:  # noqa: BLE001 —— 同上：记录 + rollback，不中断整段
        await db.rollback()
        logger.error("backfill: stock facts %s failed: %s", trade_date, exc, exc_info=True)
        return DayOutcome(trade_date, REASON_ERROR, detail=f"{type(exc).__name__}: {exc}")
    if not result.get("rows"):
        return DayOutcome(trade_date, REASON_NO_FACT_ROWS)
    await db.commit()
    detail = f"is_lu={result['is_lu']} is_zb={result['is_zb']} prev_is_lu={result['prev_is_lu']}"
    return DayOutcome(trade_date, REASON_OK, detail=detail, rows=int(result["rows"]))


async def _backfill_day(
    db: AsyncSession, domain: str, trade_date: date, *, dry_run: bool
) -> DayOutcome:
    if domain == DOMAIN_SENTIMENT:
        return await backfill_sentiment_day(db, trade_date, dry_run=dry_run)
    if domain == DOMAIN_LIMIT_UP_STOCKS:
        return await backfill_stock_day(db, trade_date, dry_run=dry_run)
    raise ValueError(f"unknown domain {domain!r} (choices: {', '.join(DOMAINS)})")


async def run_backfill(
    db: AsyncSession,
    *,
    domain: str,
    start: date,
    end: date,
    limit: int = 0,
    dry_run: bool = False,
) -> BackfillSummary:
    """按交易日**升序**逐日回补，返回含逐日原因与耗时的汇总。"""
    if end < start:
        raise ValueError(f"--from {start.isoformat()} is after --to {end.isoformat()}")
    days = await list_trade_dates(db, start, end, limit=limit)
    logger.info(
        "backfill: domain=%s range=%s→%s trade_days=%d dry_run=%s",
        domain,
        start.isoformat(),
        end.isoformat(),
        len(days),
        dry_run,
    )
    outcomes: list[DayOutcome] = []
    started = time.monotonic()
    for trade_date in sorted(days):  # 升序是硬契约：promo_*/broken_rate 依赖前一日 streak
        outcome = await _backfill_day(db, domain, trade_date, dry_run=dry_run)
        outcomes.append(outcome)
        level = (
            logging.INFO if outcome.reason in (REASON_OK, REASON_WOULD_WRITE) else logging.WARNING
        )
        logger.log(
            level,
            "backfill %s %s → %s%s",
            domain,
            trade_date.isoformat(),
            outcome.reason,
            f" ({outcome.detail})" if outcome.detail else "",
        )
    return BackfillSummary(
        domain=domain,
        dry_run=dry_run,
        from_date=start,
        to_date=end,
        days=outcomes,
        elapsed_s=time.monotonic() - started,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="市场历史回补（一个脚本，两个域）")
    parser.add_argument(
        "--domain",
        choices=DOMAINS,
        required=True,
        help="sentiment=情绪聚合（market_sentiment_daily）；limit_up_stocks=个股涨停事实",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=date.fromisoformat,
        default=DEFAULT_START,
        help=f"起始交易日（YYYY-MM-DD，默认 {DEFAULT_START.isoformat()} = E2 安全起点）",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=date.fromisoformat,
        default=None,
        help="结束交易日（YYYY-MM-DD，默认库内最新有行情的交易日）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只回补区间内**最近** N 个交易日（0=全部；冒烟用 3-5）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读预演：跑闸门、打印逐日原因与汇总表，不写库、不增强、不 commit",
    )
    return parser.parse_args(argv)


async def main(args: argparse.Namespace) -> int:
    async with async_session_factory() as db:
        end = args.to_date or await limit_up_repo.latest_quote_date(db)
        if end is None:
            logger.error("daily_quotes 为空 —— 没有可回补的交易日")
            return 1
        summary = await run_backfill(
            db,
            domain=args.domain,
            start=args.from_date,
            end=end,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    print(summary.format_table())
    return 1 if summary.errors() else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
