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
    # 复现某天的判断（默认 today）：--as-of 2026-09-17

安全边界：
- 删除只命中"严格晚于最后一个已收盘工作日"的那**一个** ``trade_date``，且该日
  行数 < ``0.9 ×`` 在市标的数；已收盘的缺列日（行数满、pct_chg 空）只回填不删除。
- 无 ``--yes`` 则只读；``--backfill`` 是 upsert，重复执行收敛到同一结果。
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


async def backfill_days(days: list[date]) -> dict[date, tuple[int, int]]:
    """按日期全市场重拉 ``daily_quotes``（幂等），返回每日期 ``(行数, pct_chg 非空行数)``。"""
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415
    from app.services.tushare_ingest import TuShareIngestService  # noqa: PLC0415

    service = TuShareIngestService(client=get_tushare_client())
    coverage: dict[date, tuple[int, int]] = {}
    for day in sorted(days):
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


async def main() -> None:
    args = parse_args()
    if not args.purge_incomplete_today and not args.backfill:
        logger.error("repair: nothing to do — pass --purge-incomplete-today and/or --backfill")
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

    if args.backfill:
        for day, (rows, pct_rows) in (await backfill_days(args.backfill)).items():
            logger.info("repair: %s now has rows=%d pct_chg_non_null=%d", day, rows, pct_rows)


if __name__ == "__main__":
    asyncio.run(main())
