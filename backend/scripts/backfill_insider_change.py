#!/usr/bin/env python3
"""回补 §S1.7 **董监高及关联人持股变动**（官方一手，沪深交易所）到 `industry_metrics`。

**为什么需要**：本指标的 ingest 只覆盖"窗口增量"（按日期取），历史要靠这个入口一次性灌；
并且它同时是**运维对账**入口（逐市场行数 / PIT 覆盖率 / 无披露日的行数）。

用法（cwd = `backend/`，走 `DATABASE_URL`）::

    # 默认：最近 90 个日历日，按 14 天一段（沪市实测 ~69 行/周 ⇒ 14 天窗口 ~140 行，1 页）
    uv run python scripts/backfill_insider_change.py

    # 指定区间
    uv run python scripts/backfill_insider_change.py --from 2026-09-01 --to 2026-10-16

    # 只对账（不联网、不写库）
    uv run python scripts/backfill_insider_change.py --coverage-only

退出码：0 = 成功；1 = 参数非法 / **整段一行都没落库** / 覆盖率对账失败。

**幂等**：唯一键 `(industry_key, stock_id, metric_key, source, freq, period)` + `ON CONFLICT
DO UPDATE` ⇒ 重跑同一区间新增行数 = 0。

⚠️ **PIT**：沪市行 `published_at = FORM_DATE` 当日 21:00；**深市该表没有披露日** ⇒
`published_at=NULL` + `extra.disclosure_date_unavailable=true`（脚本会统计并打印深市
无 PIT 行数 —— 这些行**不得**进回测/信号链路，见 ADR 0019 判据 ③）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func, select  # noqa: E402

from app.models.industry_research import IndustryMetric  # noqa: E402
from app.services import insider_change_service  # noqa: E402

METRIC_KEY = "insider_net_change_daily"
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_CHUNK_DAYS = 14
DEFAULT_SLEEP_SECONDS = 0.5


def _parse_day(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:  # pragma: no cover
        raise argparse.ArgumentTypeError(f"日期须为 YYYY-MM-DD：{raw!r}") from exc


def _windows(day_from: date, day_to: date, chunk_days: int) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cursor = day_from
    while cursor <= day_to:
        end = min(cursor + timedelta(days=chunk_days - 1), day_to)
        out.append((cursor, end))
        cursor = end + timedelta(days=1)
    return out


async def _coverage() -> dict[str, tuple[int, int, date | None, date | None]]:
    """`source_days` 口径：`(行数, 无 PIT 行数, 最早 period, 最晚 period)`。"""
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        stmt = select(
            func.count(IndustryMetric.id),
            func.count(IndustryMetric.id).filter(IndustryMetric.published_at.is_(None)),
            func.min(IndustryMetric.period),
            func.max(IndustryMetric.period),
        ).where(IndustryMetric.metric_key == METRIC_KEY)
        rows, no_pit, first, last = (await db.execute(stmt)).one()
    return {"insider": (int(rows), int(no_pit), first, last)}


def _print_coverage(coverage: dict[str, tuple[int, int, date | None, date | None]]) -> None:
    rows, no_pit, first, last = coverage["insider"]
    print(f"{METRIC_KEY}: rows={rows} 没有 PIT 的行={no_pit} 区间={first}…{last}")
    if rows and no_pit:
        print(
            f"  ⚠️ {no_pit}/{rows} 行没有 published_at（深交所该表无披露日）—— "
            "这些行不得进回测/信号链路（ADR 0019 判据 ③）"
        )


async def _run(args: argparse.Namespace) -> int:
    if args.coverage_only:
        coverage = await _coverage()
        _print_coverage(coverage)
        if coverage["insider"][0] == 0:
            print(f"✘ 覆盖率对账失败：{METRIC_KEY} 一行都没有", file=sys.stderr)
            return 1
        print("✔ 覆盖率对账通过")
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
    print(f"回补 {METRIC_KEY}：{day_from} → {day_to}（{len(windows)} 段）")
    totals = {"events": 0, "rows": 0, "unresolved_symbols": 0, "rows_without_pit": 0}
    for index, (start, end) in enumerate(windows, start=1):
        async with async_session_factory() as db:
            result = await insider_change_service.ingest_insider_range(db, start, end)
            await db.commit()
        for key in totals:
            totals[key] += int(result.get(key, 0))
        print(f"  [{index}/{len(windows)}] {start}~{end} → {result}")
        if index < len(windows) and args.sleep:
            await asyncio.sleep(args.sleep)

    print("合计：", totals)
    if totals["rows"] == 0:
        print(
            "✘ 整段一行都没落库 —— 参数错 / 上游异常 / 区间内无事件，三者必须查清"
            "（不得把采集失败当成「这段没有增减持」）",
            file=sys.stderr,
        )
        return 1
    coverage = await _coverage()
    _print_coverage(coverage)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="day_from", type=_parse_day, default=None)
    parser.add_argument("--to", dest="day_to", type=_parse_day, default=None)
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--coverage-only", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
