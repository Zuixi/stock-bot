"""一次性修复 CLI：清掉"未收盘当日"的脏行 + 回填指定交易日的 ``daily_quotes``。

背景：``data_init`` 的单股覆盖任务曾把窗口上界取成 ``date.today()``，盘中抓到的
半个市场被写成一条当日行（实测 2026-09-17 只写入 1 行），把 ``max(trade_date)``
顶到未收盘的今天，令所有按日聚合端点集体降级。上界已在 ``data_init`` 改为
:func:`app.services.data_init.last_completed_trading_day`；本脚本负责清理存量脏行，
并把缺列（如 ``pct_chg``）的历史交易日按日重拉一遍。

Usage:
    # 只打印将删除的行（默认 dry-run，绝不写库）
    uv run python scripts/repair_market_day.py --purge-incomplete-today
    # 真正删除，需 --yes 二次确认
    uv run python scripts/repair_market_day.py --purge-incomplete-today --yes
    # 幂等回填：对每个日期全市场重拉 daily_quotes（upsert in place）
    uv run python scripts/repair_market_day.py --backfill 2026-09-14 2026-09-15
    # 回补区间内缺失的复权因子（加法式修复，无需 --yes）
    uv run python scripts/repair_market_day.py --adj-factor 2026-09-01 2026-09-16
    # 复现某天的判断（默认 today）：--as-of 2026-09-17

安全边界：
- 删除只命中"严格晚于最后一个已收盘工作日"的那**一个** ``trade_date``，且该日
  行数 < ``0.9 ×`` 在市标的数；已收盘的缺列日（行数满、pct_chg 空）只回填不删除。
- 无 ``--yes`` 则只读；``--backfill`` 是 upsert，重复执行收敛到同一结果，且**拒绝**
  "未收盘的今天"（晚于最后一个已收盘工作日的日期一律跳过）。
- ``--adj-factor`` 只 UPDATE 已有行（不插行、不删行），是**加法式**修复，因此不需要
  ``--yes``：它最多把 NULL 因子填上，绝不可能让已有数据变少或变错。它只处理"已拉过
  因子但个别日缺失"的股票（从未拉过的股票由线上懒加载路径一次拉全，见
  ``quote_service.backfill_missing_adj_factors``）。
- 修复成功后 best-effort 失效 ``market:*`` 派生读缓存（Redis 故障不影响修复结果）；
  ``--adj-factor`` 另需失效 ``quote:kline:*``（K 线响应把 ``adjust_available=false``
  一起缓存 600s，不清则控件最长 5 分钟仍显示禁用）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.repositories import limit_up_repo
from app.services.data_init import last_completed_trading_day
from app.services.market_day_service import MIN_ROW_RATIO

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _parse_day(raw: str) -> date:
    """接受 20260914 与 2026-09-14 两种写法。"""
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"invalid date {raw!r} (expected YYYYMMDD or YYYY-MM-DD)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一次性修复 market 日级数据")
    parser.add_argument(
        "--purge-incomplete-today",
        action="store_true",
        help="删除最新交易日若是「未收盘当日」且行数不足的全部行（需 --yes 才执行）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行删除；缺省为 dry-run（只打印影响行数）",
    )
    parser.add_argument(
        "--backfill",
        nargs="+",
        type=_parse_day,
        metavar="YYYYMMDD",
        help="对给定交易日全市场重拉 daily_quotes（幂等 upsert，修 pct_chg 等缺列）",
    )
    parser.add_argument(
        "--adj-factor",
        nargs=2,
        type=_parse_day,
        metavar=("START", "END"),
        help="回补区间内缺失的 adj_factor（只 UPDATE 已有行，加法式修复，无需 --yes）",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_day,
        metavar="YYYYMMDD",
        help="判定「最后一个已收盘工作日」的基准日（默认 today，用于复现历史判断）",
    )
    return parser.parse_args()


async def _universe_count(db: AsyncSession) -> int:
    """在市标的总数 —— 行数阈值分母。"""
    return int((await db.execute(select(func.count()).select_from(Stock))).scalar_one())


async def _rows_on(db: AsyncSession, day: date) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(DailyQuote).where(DailyQuote.trade_date == day)
            )
        ).scalar_one()
    )


async def _pct_chg_rows_on(db: AsyncSession, day: date) -> int:
    return int(
        (
            await db.execute(
                select(func.count(DailyQuote.pct_chg)).where(DailyQuote.trade_date == day)
            )
        ).scalar_one()
    )


async def incomplete_latest_day_rows(
    db: AsyncSession, *, today: date | None = None
) -> tuple[date | None, int]:
    """最新的 ``trade_date`` 若是"未收盘当日"且行数不足 → ``(日期, 行数)``。

    三个条件缺一不可（任一不满足都返回 ``(None, 0)``，即不删）：

    1. 库非空（无 ``daily_quotes`` 无从谈起）；
    2. 最新日**严格晚于**最后一个已收盘工作日 —— 只有这种日子才可能有未收盘数据；
    3. 该日行数 < ``0.9 ×`` 在市标的数 —— 收盘日的正常行数远高于这条线。

    已收盘的缺列日（09-14/15 那种行数满但 ``pct_chg`` 空）不在此列，交给
    ``--backfill`` 重拉，不做删除。
    """
    candidate = await limit_up_repo.latest_quote_date(db)
    if candidate is None:
        return None, 0
    if candidate <= last_completed_trading_day(today):
        return None, 0
    universe = await _universe_count(db)
    if universe <= 0:
        return None, 0
    rows = await _rows_on(db, candidate)
    if rows >= MIN_ROW_RATIO * universe:
        return None, 0
    return candidate, rows


async def purge_incomplete_latest_day(
    db: AsyncSession, *, today: date | None = None
) -> tuple[date | None, int]:
    """删除选中的整个脏日；返回 ``(日期, 删除行数)``，无选中 → ``(None, 0)``。"""
    day, rows = await incomplete_latest_day_rows(db, today=today)
    if day is None:
        return None, 0
    logger.warning("repair: deleting %d incomplete row(s) on %s", rows, day)
    result = await db.execute(delete(DailyQuote).where(DailyQuote.trade_date == day))
    await db.commit()
    return day, int(result.rowcount or 0)


async def backfill_days(
    days: list[date], *, today: date | None = None
) -> dict[date, tuple[int, int]]:
    """按日期全市场重拉 ``daily_quotes``（幂等），返回每日期 ``(行数, pct_chg 非空行数)``。

    晚于最后一个已收盘工作日的日期（即"未收盘的今天"）一律跳过并告警：
    ``--backfill <today>`` 盘中重拉只会把半个市场 upsert 回去，重新造出
    ``--purge-incomplete-today`` 刚删掉的那种脏行。守卫放在本函数（而非 ``main``），
    任何调用方都受保护。
    """
    cutoff = last_completed_trading_day(today)
    for day in sorted(days):
        if day > cutoff:
            logger.warning(
                "repair: refusing to backfill %s — later than last completed trading day %s "
                "(market session unfinished)",
                day,
                cutoff,
            )
    safe_days = [day for day in sorted(days) if day <= cutoff]
    if not safe_days:
        return {}

    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    service = TuShareIngestService(client=get_tushare_client())
    coverage: dict[date, tuple[int, int]] = {}
    for day in safe_days:
        async with async_session_factory() as db:
            result = await service.ingest_daily_quotes(db, day.strftime("%Y%m%d"))
            await db.commit()
            coverage[day] = (await _rows_on(db, day), await _pct_chg_rows_on(db, day))
        logger.info(
            "repair: backfill %s -> upserted=%s rows=%d pct_chg=%d",
            day,
            result.get("upserted"),
            *coverage[day],
        )
    return coverage


async def repair_adj_factors(start: date, end: date) -> dict[str, int]:
    """调用 service 补洞（只 UPDATE 已有行）；返回 ``{stocks, rows, failed}``。"""
    from app.services.quote_service import backfill_missing_adj_factors  # noqa: PLC0415

    async with async_session_factory() as db:
        return await backfill_missing_adj_factors(db, start=start, end=end)


MARKET_CACHE_PATTERN = "market:*"
KLINE_CACHE_PATTERN = "quote:kline:*"


async def _invalidate_pattern(pattern: str, *, label: str) -> int:
    """按前缀失效读缓存，best-effort。

    Best-effort：Redis 不可用（或 ``get_redis_pool`` 构造失败）绝不能让已经落库
    的修复报错，一律吞掉并记 0。
    """
    from app.core.redis import CacheClient, get_redis_pool  # noqa: PLC0415

    try:
        cache = CacheClient(await get_redis_pool())
        dropped = await cache.delete_pattern(pattern)
    except Exception:
        logger.warning(
            "repair: %s cache invalidation failed (ignored — repair already committed)",
            label,
            exc_info=True,
        )
        return 0
    logger.info("repair: invalidated %d %s cache key(s)", dropped, label)
    return dropped


async def _invalidate_market_caches() -> int:
    """失效派生读缓存（``market:day:*`` / ``market:rankings:*`` / SW 快照等）。

    这些 payload 把 ``as_of``/``as_of_quality`` 一起缓存 300s；修复把
    ``max(trade_date)`` 往回挪之后，若不清缓存，端点最长 5 分钟内还会返回
    "脏日仍是 latest"的旧结论。
    """
    return await _invalidate_pattern(MARKET_CACHE_PATTERN, label="market")


async def _invalidate_kline_caches() -> int:
    """失效 K 线响应缓存（``quote:kline:{exchange}:{symbol}:{start}:{end}:{adjust}``）。

    ``get_kline`` 连 raw 分支也会把 ``adjust_available=false`` 一起缓存 600s；因子
    补上之后若不清，端点最长 5 分钟内仍宣称"复权不可用"，控件继续禁用。
    """
    return await _invalidate_pattern(KLINE_CACHE_PATTERN, label="kline")


async def main() -> None:
    args = parse_args()
    if not args.purge_incomplete_today and not args.backfill and not args.adj_factor:
        logger.error(
            "repair: nothing to do — pass --purge-incomplete-today and/or --backfill "
            "and/or --adj-factor"
        )
        sys.exit(2)

    asof = args.as_of or date.today()

    if args.purge_incomplete_today:
        async with async_session_factory() as db:
            day, rows = await incomplete_latest_day_rows(db, today=asof)
        if day is None:
            logger.info(
                "repair: no incomplete latest day to purge (as-of=%s, last completed=%s)",
                asof,
                last_completed_trading_day(asof),
            )
        elif not args.yes:
            logger.warning(
                "[DRY RUN] would delete %d row(s) on %s — re-run with --yes to apply", rows, day
            )
        else:
            async with async_session_factory() as db:
                purged_day, deleted = await purge_incomplete_latest_day(db, today=asof)
            logger.info("repair: purged %d row(s) on %s", deleted, purged_day)
            if deleted:
                await _invalidate_market_caches()

    if args.backfill:
        coverage = await backfill_days(args.backfill, today=asof)
        for day, (rows, pct_rows) in coverage.items():
            logger.info("repair: %s now has rows=%d pct_chg_non_null=%d", day, rows, pct_rows)
        if coverage:
            await _invalidate_market_caches()

    if args.adj_factor:
        adj_start, adj_end = args.adj_factor
        stats = await repair_adj_factors(adj_start, adj_end)
        logger.info(
            "repair: adj_factor %s..%s stocks=%d rows=%d failed=%d",
            adj_start,
            adj_end,
            stats["stocks"],
            stats["rows"],
            stats["failed"],
        )
        if stats["rows"]:  # 有行被改写才需要失效 K 线缓存
            await _invalidate_kline_caches()


if __name__ == "__main__":
    asyncio.run(main())
