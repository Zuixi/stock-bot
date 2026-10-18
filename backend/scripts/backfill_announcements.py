#!/usr/bin/env python3
"""回补巨潮公告历史到 `announcements` 表（**三类目**：report / event / equity_change）。

**为什么需要这个入口**：轮询任务（`announcements_poll_job`，8–22 点每 10 分钟）只覆盖
**近 3 日**滚动窗口 ⇒ 管道上线（2026-09-01）之前的公告在库里**一行都没有**；而
spec §S1.7 的 `industrial_capital_net_change`（股本变动 = 5%+ 股东增减持与权益变动）与
后续的观点抽取都需要这段**原料**（[ADR 0009](../../docs/decisions/0009-content-view-separation.md)：
原料/视图分离）。本脚本**不新建表、不新建读路** —— 写的就是轮询任务写的那张表。

用法（cwd = `backend/`，走 `DATABASE_URL`）::

    # 默认：回补最近 90 个日历日，按 7 天一段
    uv run python scripts/backfill_announcements.py

    # 指定区间
    uv run python scripts/backfill_announcements.py --from 2026-09-01 --to 2026-10-16

    # 只对账（不联网、不写库）
    uv run python scripts/backfill_announcements.py --coverage-only

    # 只补某一个类目
    uv run python scripts/backfill_announcements.py --categories equity_change

退出码：0 = 成功；1 = 参数非法 / **整段一行都没入库** / 覆盖率对账发现**该类目 0 行**。
**幂等**：`announcement_id` 唯一键 + `ON CONFLICT DO NOTHING`，重跑同一区间新增行数 = 0。

⚠️ **配额与礼貌**：每段 3 个类目各翻若干页（`ANNOUNCEMENT_FETCH_LIMITS`），段间默认
`sleep 0.5s`；`equity_change` 实测 ≈ **157 条/交易日**，回补半年 ≈ 2 万条、约 26 段 × 3 请求，
属可接受量级。**不要**把 `--chunk-days` 开大：窗口越大越可能撞上分页上限（撞上会
**响亮失败**而不是静默截断，见 `CninfoClient.fetch_announcements`）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func, select  # noqa: E402

from app.core.providers.cninfo_client import ANNOUNCEMENT_CATEGORIES  # noqa: E402
from app.models.market_data import Announcement  # noqa: E402
from app.services import announcement_service  # noqa: E402

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


async def _coverage() -> dict[str, tuple[int, date | None, date | None]]:
    """逐类目的 `(行数, 最早披露日, 最新披露日)` —— 只看库内事实，不联网。"""
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        stmt = (
            select(
                Announcement.category,
                func.count(Announcement.id),
                func.min(Announcement.announce_time),
                func.max(Announcement.announce_time),
            )
            .group_by(Announcement.category)
            .order_by(Announcement.category)
        )
        rows = (await db.execute(stmt)).all()
    return {
        str(category): (int(count), first.date() if first else None, last.date() if last else None)
        for category, count, first, last in rows
    }


def _print_coverage(coverage: dict[str, tuple[int, date | None, date | None]]) -> None:
    print("类目                  行数      最早披露      最新披露")
    for category in ANNOUNCEMENT_CATEGORIES:
        count, first, last = coverage.get(category, (0, None, None))
        print(f"{category:<20} {count:>8}  {str(first or '-'):<12} {str(last or '-'):<12}")


async def _run(args: argparse.Namespace) -> int:
    categories: tuple[str, ...] = tuple(args.categories or ANNOUNCEMENT_CATEGORIES)
    if args.coverage_only:
        coverage = await _coverage()
        _print_coverage(coverage)
        missing = [c for c in categories if coverage.get(c, (0, None, None))[0] == 0]
        if missing:
            print(
                f"✘ 覆盖率对账失败：这些类目**一行都没有**：{', '.join(missing)}",
                file=sys.stderr,
            )
            return 1
        print("✔ 覆盖率对账通过（每个类目都有行）")
        return 0

    day_to = args.day_to or date.today()
    day_from = args.day_from or (day_to - timedelta(days=args.lookback - 1))
    if day_from > day_to:
        print(f"✘ --from({day_from}) 晚于 --to({day_to})", file=sys.stderr)
        return 1
    if args.chunk_days <= 0 or args.lookback <= 0 or args.sleep < 0:
        print("✘ --chunk-days / --lookback 须 > 0，--sleep 须 ≥ 0", file=sys.stderr)
        return 1

    from app.core.database import async_session_factory  # noqa: PLC0415

    windows = _windows(day_from, day_to, args.chunk_days)
    print(
        f"回补 {day_from} → {day_to}（{len(windows)} 段 × {len(categories)} 类目："
        f"{', '.join(categories)}）"
    )
    totals: dict[str, int] = {c: 0 for c in categories}
    for index, (start, end) in enumerate(windows, start=1):
        se_date = f"{start.isoformat()}~{end.isoformat()}"
        async with async_session_factory() as db:
            result = await announcement_service.ingest_announcements_window(
                db, se_date, categories=categories
            )
            await db.commit()
        for key, value in result.items():
            totals[key] = totals.get(key, 0) + value
        print(f"  [{index}/{len(windows)}] {se_date} → {result}")
        if index < len(windows) and args.sleep:
            await asyncio.sleep(args.sleep)

    print("本次新增行数合计：", totals)
    if sum(totals.values()) == 0:
        print("✘ 整段一行都没入库 —— 参数错/上游异常/窗口无公告，二者必须查清", file=sys.stderr)
        return 1

    coverage = await _coverage()
    _print_coverage(coverage)
    missing = [c for c in categories if coverage.get(c, (0, None, None))[0] == 0]
    if missing:
        print(f"✘ 回补后这些类目仍**一行都没有**：{', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="day_from", type=_parse_day, default=None)
    parser.add_argument("--to", dest="day_to", type=_parse_day, default=None)
    parser.add_argument(
        "--lookback",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"未给 --from 时回看的日历日数（默认 {DEFAULT_LOOKBACK_DAYS}）",
    )
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument(
        "--categories",
        nargs="+",
        choices=list(ANNOUNCEMENT_CATEGORIES),
        default=None,
        help="只回补指定类目（默认全部）",
    )
    parser.add_argument(
        "--coverage-only", action="store_true", help="只打印逐类目覆盖率，不写库不联网"
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
