#!/usr/bin/env python3
"""回补东财卖方研报到内容事实层（``content_items`` 等，``content_type='report'``）。

**为什么需要这个入口**：每日任务（``research_reports_daily``，18:40）只覆盖近 3 日滚动
窗口 ⇒ 上线前的研报在库里一行都没有。本脚本写的就是每日任务写的那套表/那套 ingest，
不新建表、不新建读路。

用法（cwd = ``backend/``，走 ``DATABASE_URL``）::

    # 默认：回补最近 90 个日历日，按 7 天一段
    uv run python scripts/backfill_research_reports.py

    # 指定区间（``--start/--end`` 同义）
    uv run python scripts/backfill_research_reports.py --from 2026-09-01 --to 2026-10-16

    # 放大单页/翻页上限（上游 hits 超过上限会**响亮失败**，不会静默截断）
    uv run python scripts/backfill_research_reports.py --page-size 100 --max-pages 80

退出码：0 = 成功；1 = 参数非法 / 整段一行都没取到 / 某段分页截断。
**幂等**：``content_items(source_name, raw_hash)`` 唯一键 + ``ON CONFLICT DO NOTHING``，
重跑同一区间新增行数 = 0；指标按唯一键 upsert 覆盖。

⚠️ 配额与礼貌：段间默认 ``sleep 0.5s``；单段窗口越大越可能撞上分页上限，撞上会抛
``RuntimeError``（``EastmoneyReportClient.fetch_reports``），脚本立即失败。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.content_ingest.research_report import (  # noqa: E402
    SOURCE_NAME,
    ingest_research_reports,
)

DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_CHUNK_DAYS = 7
DEFAULT_SLEEP_SECONDS = 0.5


def _parse_day(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:  # pragma: no cover - argparse 已挡住大部分
        raise argparse.ArgumentTypeError(f"日期须为 YYYY-MM-DD：{raw!r}") from exc


def _windows(day_from: date, day_to: date, chunk_days: int) -> list[tuple[date, date]]:
    """把区间切成**从旧到新**的闭区间段（正序便于中断后续跑）。"""
    out: list[tuple[date, date]] = []
    cursor = day_from
    while cursor <= day_to:
        end = min(cursor + timedelta(days=chunk_days - 1), day_to)
        out.append((cursor, end))
        cursor = end + timedelta(days=1)
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Eastmoney sell-side research reports")
    parser.add_argument("--from", "--start", dest="day_from", type=_parse_day, default=None)
    parser.add_argument("--to", "--end", dest="day_to", type=_parse_day, default=None)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    args = parser.parse_args(argv)

    today = date.today()
    args.day_to = args.day_to or today
    args.day_from = args.day_from or (args.day_to - timedelta(days=args.days - 1))
    if args.day_from > args.day_to:
        parser.error("--from/--start 不能晚于 --to/--end")
    if args.chunk_days < 1 or args.page_size < 1 or args.max_pages < 1:
        parser.error("--chunk-days/--page-size/--max-pages 必须 >= 1")
    return args


async def _run(args: argparse.Namespace) -> int:
    from app.core.database import async_session_factory  # noqa: PLC0415

    windows = _windows(args.day_from, args.day_to, args.chunk_days)
    totals = {"fetched": 0, "inserted": 0, "skipped": 0}
    for index, (begin, end) in enumerate(windows, start=1):
        async with async_session_factory() as db:
            result = await ingest_research_reports(
                db,
                begin,
                end,
                page_size=args.page_size,
                max_pages=args.max_pages,
            )
            await db.commit()
        for key in totals:
            totals[key] += result[key]
        print(
            f"[{index}/{len(windows)}] {begin}~{end}: "
            f"fetched={result['fetched']} inserted={result['inserted']} "
            f"skipped={result['skipped']}"
        )
        if index < len(windows) and args.sleep > 0:
            await asyncio.sleep(args.sleep)

    print(
        f"done source={SOURCE_NAME} range={args.day_from}~{args.day_to} "
        f"fetched={totals['fetched']} inserted={totals['inserted']} "
        f"skipped={totals['skipped']}"
    )
    return 0 if totals["fetched"] > 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
