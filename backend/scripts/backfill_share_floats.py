#!/usr/bin/env python3
"""回填 spec §S11.1 解禁日历（`share_floats`）：近 24 个月 + 未来 6 个月。

这是**写入口（运维）**，不是读路：读路是既有 `GET /api/v1/market-data/share-floats`
与 S11 的 triage 探测器 `unlock_soon` / journal `evaluation.unlock_in_window`。
本脚本**不新建表、不新建读路、不改调度**。

用法（cwd = `backend/`，token 缺省从 `backend/.env` 读，**不打印**）::

    # 默认窗口：今天-24 个月 … 今天+6 个月（解禁是**事前公告**，未来窗口才是"日历"）
    UV_CACHE_DIR=.uv-cache uv run python scripts/backfill_share_floats.py

    # 只跑一个窗口（幂等验证 / 补洞）
    UV_CACHE_DIR=.uv-cache uv run python scripts/backfill_share_floats.py \
        --from 2026-10-19 --to 2026-10-19

    # 只打印覆盖率（不联网、不写库）
    UV_CACHE_DIR=.uv-cache uv run python scripts/backfill_share_floats.py --coverage

**实测口径（2026-10-19 probe，写在这里以免下一个人再踩）**：

1. `share_float(start_date, end_date)` 过滤的是 **`float_date`（解禁日）**，不是
   `ann_date`（公告日）——probe 一个 19 天窗口回来 `float_date` 只有 1 个取值、
   `ann_date` 却横跨 2016…2026。所以"按公告日窗口"这句话在 API 上落不了地：
   本脚本按 **`float_date` 窗口**分批，这正好也是"解禁日历"需要的形状
   （spec §S11.1 的"近 24 个月 + 未来 6 个月"因此成立）。
2. 单次返回上限 **6000 行**，`offset` **不得大于 100000**。`share_float` 是
   **持有人级**明细（同一天同一只股票可有上千行），所以一个 30 天窗口轻易超过 10 万行
   → 必须分页；分页到底还没取完就把窗口对半拆开重取（`_WindowTooLarge`），
   单日仍超限则**响亮失败**（不静默丢行）。
3. 既有 `TuShareClient.fetch_share_float` 没有 `offset` 参数（一次只拿 6000 行），
   本脚本走 `client._query(...offset/limit...)` 分页。**本 spec 不改该客户端文件**
   （与并行工作冲突），后续应把 `offset/limit` 提成公开参数。

**幂等**：唯一键 `(ann_date, ts_code, holder_name, share_type)` + `ON CONFLICT DO
NOTHING`（`market_data_repo.upsert_share_floats`），重跑同一区间新增行数 = 0。

**退出码**：0 = 全部窗口成功（重跑新增 0 行也算成功）；1 = 参数非法 / 任一窗口抓取
或入库失败（错误逐条打到 stderr，**不静默跳过**）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 本 worktree 的 `backend/.env` 是 git-ignored（常不存在）；与既有回填脚本同规矩，
#: 回落到主 checkout 的 `backend/.env` 取 token，**不打印**其值。
_TOKEN_FALLBACKS = (
    Path(__file__).resolve().parents[1] / ".env",
    Path("/Users/wqz/Developer/stock-bot/backend/.env"),
)


def _ensure_tushare_token() -> None:
    """`TUSHARE_TOKEN` 未在环境里时，从候选 `.env` 读进环境变量。

    `settings` 已在 import 期读过 `.env`，故必须在 import `app.*` **之前**调用。
    """
    import os

    if os.environ.get("TUSHARE_TOKEN"):
        return
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover
        return
    for path in _TOKEN_FALLBACKS:
        if path.exists():
            token = dotenv_values(path).get("TUSHARE_TOKEN") or ""
            if token:
                os.environ["TUSHARE_TOKEN"] = token
                print(f"[ENV ] TUSHARE_TOKEN loaded from {path} (value not printed)")
                return


_ensure_tushare_token()

from sqlalchemy import distinct, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402
from app.models.market_data import ShareFloat  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.repositories import market_data_repo  # noqa: E402
from app.services import market_data_service  # noqa: E402

_SH = ZoneInfo("Asia/Shanghai")

#: TuShare `share_float` 单次返回上限（probe：`limit=6000` 时每页都是 6000）。
PAGE_SIZE = 6000
#: TuShare 对 `offset` 的硬上限（probe：`offset=106000` 直接报错 "offset不能大于100000"）。
MAX_OFFSET = 100_000
#: 默认窗口：近 24 个月 + 未来 6 个月（spec §S11.1 逐字）。
DEFAULT_PAST_MONTHS = 24
DEFAULT_FUTURE_MONTHS = 6
#: 每批解禁日窗口（spec §S11.1：≤90 天/批；30 天让单批的行数先落在分页预算内）。
DEFAULT_CHUNK_DAYS = 30
#: spec 允许的窗口上限：超过它就不再是"分批"。
MAX_CHUNK_DAYS = 90


class _WindowTooLarge(RuntimeError):
    """该窗口在 `offset` 上限内取不完（→ 对半拆开重取）。"""


@dataclass
class BackfillStats:
    """一次回填的可核对产出（打印成 JSON，便于贴进报告）。"""

    windows: int = 0
    pages: int = 0
    splits: int = 0
    rows_fetched: int = 0
    rows_inserted: int = 0
    rows_existing: int = 0
    #: 最近一次成功窗口（解禁日区间）——spec §S11.1 要求 `--coverage` 打印它。
    last_ok_window: tuple[date, date] | None = None
    #: 失败窗口（人话，含上游错误）——非空即非零退出。
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "windows": self.windows,
            "pages": self.pages,
            "splits": self.splits,
            "rows_fetched": self.rows_fetched,
            "rows_inserted": self.rows_inserted,
            "rows_existing": self.rows_existing,
            "last_ok_window": (
                f"{self.last_ok_window[0]}..{self.last_ok_window[1]}"
                if self.last_ok_window
                else None
            ),
            "failures": self.failures,
        }


def _today_sh() -> date:
    return datetime.now(_SH).date()


def _add_months(day: date, months: int) -> date:
    """按自然月平移（月末自动落到该月最后一天，不引入 dateutil 依赖）。"""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    # 逐月退回，直到该月存在这一天（1/31 + 1 月 → 2/28）
    candidate = day
    while True:
        try:
            return candidate.replace(year=year, month=month)
        except ValueError:
            candidate -= timedelta(days=1)


def default_window() -> tuple[date, date]:
    """spec §S11.1 的默认窗口：近 24 个月 + 未来 6 个月。"""
    today = _today_sh()
    return _add_months(today, -DEFAULT_PAST_MONTHS), _add_months(today, DEFAULT_FUTURE_MONTHS)


def chunk_windows(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    """把 ``[start, end]`` 切成每批 ≤``chunk_days`` 天的闭区间（顺序推进）。"""
    if chunk_days <= 0 or chunk_days > MAX_CHUNK_DAYS:
        raise ValueError(f"--chunk-days 必须在 1..{MAX_CHUNK_DAYS}（spec §S11.1：≤90 天/批）")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=chunk_days - 1), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


#: ``fetch(start, end, offset) -> DataFrame``：可注入（幂等用例打桩，不打真网）。
FetchPage = Callable[[date, date, int], Awaitable[pd.DataFrame]]


def _map_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """TuShare 行 → `share_floats` 行（复用 ingest 的同一映射，口径不两处漂移）。"""
    if df is None or len(df) == 0:
        return []
    return market_data_service._map_share_float_rows(df)


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按唯一键去重（同一批内重复会让 `ON CONFLICT DO NOTHING` 之外的 NULL ann_date 行重复入库）。

    键与 ``uq_share_floats_dedupe`` 一致：``(ann_date, ts_code, holder_name, share_type)``。
    """
    seen: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("ann_date"), row["ts_code"], row.get("holder_name"), row.get("share_type"))
        seen.setdefault(key, row)
    return list(seen.values())


async def _fetch_page(client: Any, start: date, end: date, offset: int) -> pd.DataFrame:
    """取一页（`share_float` 的公开方法没有 offset，见模块 docstring 第 3 条）。"""
    # 走 ``_query`` 而不是 ``fetch_share_float``：后者没有 offset 参数（见模块 docstring 第 3 条）
    return await client._query(
        "share_float",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        offset=offset,
        limit=PAGE_SIZE,
    )


async def _fetch_window(
    fetch: FetchPage,
    db: AsyncSession,
    start: date,
    end: date,
    stats: BackfillStats,
) -> None:
    """取一个窗口的**全部**行并逐页入库（每页一次 commit，长跑可中断可重入）。"""
    offset = 0
    while True:
        df = await fetch(start, end, offset)
        stats.pages += 1
        rows = _map_rows(df)
        if rows:
            unique = _dedupe(rows)
            stats.rows_fetched += len(rows)
            inserted = await market_data_repo.upsert_share_floats(db, unique)
            stats.rows_inserted += inserted
            stats.rows_existing += len(unique) - inserted
            await db.commit()
        if len(df) < PAGE_SIZE:
            stats.last_ok_window = (start, end)
            return
        offset += PAGE_SIZE
        if offset > MAX_OFFSET:
            raise _WindowTooLarge(
                f"{start}..{end} 在 offset={MAX_OFFSET} 内取不完（单窗口 > "
                f"{MAX_OFFSET + PAGE_SIZE} 行）"
            )


async def backfill_window(
    fetch: FetchPage,
    db: AsyncSession,
    start: date,
    end: date,
    stats: BackfillStats,
) -> None:
    """一个窗口；取不完就对半拆开（递归到底 = 单日，单日仍超限则抛错）。"""
    try:
        await _fetch_window(fetch, db, start, end, stats)
    except _WindowTooLarge:
        if start >= end:
            raise
        mid = start + (end - start) // 2
        next_start = mid + timedelta(days=1)
        stats.splits += 1
        print(f"[SPLIT] {start}..{end} 超过分页上限 → 拆成 {start}..{mid} + {next_start}..{end}")
        await backfill_window(fetch, db, start, mid, stats)
        await backfill_window(fetch, db, next_start, end, stats)


async def backfill_range(
    fetch: FetchPage,
    db: AsyncSession,
    start: date,
    end: date,
    *,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
    stats: BackfillStats | None = None,
) -> BackfillStats:
    """按 ``chunk_days`` 分批回填 ``[start, end]``（解禁日闭区间）。

    单个窗口失败**不中断整轮**：错误记进 ``stats.failures`` 后继续下一批
    （这样一次长时间回填不会因为一个坏窗口前功尽弃），但调用方据 ``failures`` 非零退出。
    """
    stats = stats or BackfillStats()
    for window_start, window_end in chunk_windows(start, end, chunk_days):
        stats.windows += 1
        try:
            await backfill_window(fetch, db, window_start, window_end, stats)
        except Exception as exc:  # noqa: BLE001 —— 失败要可见，不能让一个窗口炸掉整轮
            await db.rollback()
            message = f"{window_start}..{window_end}: {type(exc).__name__}: {exc}"
            stats.failures.append(message)
            print(f"[FAIL] {message}", file=sys.stderr)
        print(
            f"[OK  ] {window_start}..{window_end} "
            f"累计 fetched={stats.rows_fetched} inserted={stats.rows_inserted} "
            f"dup={stats.rows_existing} pages={stats.pages}",
            flush=True,
        )
    return stats


@dataclass(frozen=True)
class Coverage:
    """覆盖率快照（spec §S11.1：有解禁记录的股票数 / 全市场股票数 / 最近成功窗口）。"""

    stocks_with_unlock: int
    universe: int
    rows: int
    ann_date_from: date | None
    ann_date_to: date | None
    float_date_from: date | None
    float_date_to: date | None
    upcoming_30d: int
    upcoming_90d: int
    future_rows: int

    @property
    def share_pct(self) -> float:
        return self.stocks_with_unlock / self.universe * 100.0 if self.universe else 0.0


async def coverage(db: AsyncSession) -> Coverage:
    """只读覆盖率（不联网）：覆盖率 = 有解禁记录的股票数 / 全市场股票数。"""

    async def _one(stmt: Any) -> Any:
        return (await db.execute(stmt)).scalar()

    today = _today_sh()
    return Coverage(
        stocks_with_unlock=int(await _one(select(func.count(distinct(ShareFloat.ts_code)))) or 0),
        universe=int(await _one(select(func.count(Stock.id))) or 0),
        rows=int(await _one(select(func.count(ShareFloat.id))) or 0),
        ann_date_from=await _one(select(func.min(ShareFloat.ann_date))),
        ann_date_to=await _one(select(func.max(ShareFloat.ann_date))),
        float_date_from=await _one(select(func.min(ShareFloat.float_date))),
        float_date_to=await _one(select(func.max(ShareFloat.float_date))),
        upcoming_30d=int(
            await _one(
                select(func.count(distinct(ShareFloat.ts_code))).where(
                    ShareFloat.float_date > today,
                    ShareFloat.float_date <= today + timedelta(days=30),
                )
            )
            or 0
        ),
        upcoming_90d=int(
            await _one(
                select(func.count(distinct(ShareFloat.ts_code))).where(
                    ShareFloat.float_date > today,
                    ShareFloat.float_date <= today + timedelta(days=90),
                )
            )
            or 0
        ),
        future_rows=int(
            await _one(select(func.count(ShareFloat.id)).where(ShareFloat.float_date > today)) or 0
        ),
    )


def print_coverage(cov: Coverage, stats: BackfillStats | None = None) -> None:
    """打印覆盖数（数字必须能贴进报告，所以逐项带标签 + 明确"哪个窗口"）。"""
    last = stats.last_ok_window if stats and stats.last_ok_window else None
    print("\n=== S11 解禁日历覆盖率（share_floats） ===")
    print(f"有解禁记录的股票数 : {cov.stocks_with_unlock}")
    print(f"全市场股票数       : {cov.universe}")
    print(f"占比               : {cov.share_pct:.2f}%")
    print(f"表内行数           : {cov.rows}（未来解禁行 {cov.future_rows}）")
    print(f"公告日 ann_date    : {cov.ann_date_from or '—'} … {cov.ann_date_to or '—'}")
    print(f"解禁日 float_date  : {cov.float_date_from or '—'} … {cov.float_date_to or '—'}")
    print(f"未来 30 天有解禁的股票数 : {cov.upcoming_30d}（探测器 unlock_soon 的有效覆盖）")
    print(f"未来 90 天有解禁的股票数 : {cov.upcoming_90d}")
    print(
        "本次运行最近成功窗口     : "
        + (f"{last[0]}..{last[1]}" if last else "—（--coverage 模式不联网，不产生窗口）")
    )
    if cov.stocks_with_unlock < 1000:
        print(
            "⚠ 覆盖不足：spec §S11.4 的达标线是 ≥1000 只有解禁记录；"
            "探测器保留，但无记录的股票一律不输出（不猜「没有解禁」）。",
            file=sys.stderr,
        )


async def _run(args: argparse.Namespace) -> int:
    async with async_session_factory() as db:
        if args.coverage:
            print_coverage(await coverage(db))
            return 0

        default_start, default_end = default_window()
        start: date = args.day_from or default_start
        end: date = args.day_to or default_end
        if start > end:
            print(f"✘ --from {start} 晚于 --to {end}", file=sys.stderr)
            return 1

        from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

        client = get_tushare_client()

        async def fetch(page_start: date, page_end: date, offset: int) -> pd.DataFrame:
            return await _fetch_page(client, page_start, page_end, offset)

        batches = chunk_windows(start, end, args.chunk_days)
        print(
            f"[RUN ] 解禁日窗口 {start} … {end}（{len(batches)} 批，"
            f"每批 ≤{args.chunk_days} 天；"
            f"TuShare 单页 {PAGE_SIZE} 行 / offset≤{MAX_OFFSET}）",
            flush=True,
        )
        stats = await backfill_range(fetch, db, start, end, chunk_days=args.chunk_days)
        print(f"\n=== 回填结果 ===\n{stats.as_dict()}")
        print_coverage(await coverage(db), stats)

        if stats.failures:
            print(
                f"\n✘ {len(stats.failures)} 个窗口失败（数据可能不完整，逐条见上）：",
                file=sys.stderr,
            )
            for failure in stats.failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--from",
        dest="day_from",
        type=date.fromisoformat,
        default=None,
        help="解禁日起 YYYY-MM-DD（缺省 = 今天-24 个月）",
    )
    parser.add_argument(
        "--to",
        dest="day_to",
        type=date.fromisoformat,
        default=None,
        help="解禁日止 YYYY-MM-DD（缺省 = 今天+6 个月）",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=DEFAULT_CHUNK_DAYS,
        help=f"每批解禁日窗口天数（1..{MAX_CHUNK_DAYS}，spec §S11.1 要求 ≤90）",
    )
    parser.add_argument("--coverage", action="store_true", help="只打印覆盖率（不联网、不写库）")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    sys.exit(main())
