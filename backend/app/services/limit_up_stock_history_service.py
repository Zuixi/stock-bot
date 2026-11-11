"""短线热度股追踪：个股涨停事实的「股票 × 交易日」多日读面。

数据全部来自 `market_limit_up_stock_daily`（唯一事实源，回补后约 261 个交易日）：
本模块**只聚合**已有事实，不重算连板、不插值、不补空列。

三条纪律：

1. **`dates` 只回实际有事实的交易日**。窗口内某天整表缺行（回补没跑到）时，插一个
   全 `none` 的空列等于断言"那天没人涨停"——那是假事实。缺的那天进 `missing_dates`
   并把 `degraded_reason` 置 `facts_gap`，让读面自己说"这里缺数据"。
2. **`none` 是"未上榜"，不是"0 板"也不是"停牌"**。事实表行集是"当日涨停 ∪ 炸板 ∪
   昨日涨停今日断板"的候选集，某股某天没行只说明它没进候选，无法据此断言没涨停过。
3. **`streak` 只在 `limit_up` 格有值**。炸板/断板/未上榜给 `None`（缺失 ≠ 0），
   否则前端会把 `0` 画成"0 连板"这个事实断言。

不做缓存：单条范围查询 + Python 聚合（20 日实测 ~9ms）；缓存只会让回补后的结果延迟
可见，而"回补完立刻能看到"正是本读面的验收标准。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import limit_up_repo

#: 默认窗口交易日数 / 默认连板阈值 / 默认行数上限。
DEFAULT_DAYS = 20
DEFAULT_MIN_STREAK = 2
DEFAULT_LIMIT = 300

#: 单元格状态（前端 `HeatStockCellStatus` 1:1）。
CELL_STATUSES = ("limit_up", "broken", "dropped", "none")

_EMPTY_CELL: dict[str, Any] = {"status": "none", "streak": None}


def _cell_status(fact: dict[str, Any]) -> str:
    """单条事实 → 单元格状态（固定顺序，先到先得）。

    `is_lu` 优先：炸板列 `is_zb` 的定义是 `touched AND NOT is_lu`，两者互斥；但
    `prev_is_lu` 可以与 `is_lu` 同真（连板票），所以顺序不能换——先判 `is_lu` 才能把
    连板票画成涨停而不是"昨日涨停"。
    """
    if fact["is_lu"]:
        return "limit_up"
    if fact["is_zb"]:
        return "broken"
    if fact["prev_is_lu"]:
        return "dropped"
    return "none"


def build_stock_history_rows(
    facts: list[dict[str, Any]],
    dates: list[date],
    *,
    min_streak: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """事实行 × 交易日轴 → 股票行矩阵。

    `dates` 必须**升序**且只含"实际有事实的交易日"；`facts` 的 `trade_date` 不在
    `dates` 里的行被跳过（调用方传的是同源日期集，跳过只是防御）。

    返回 `(rows, total, truncated)`：`total` 是过滤后的行数（未截断），`truncated`
    表示 `total > limit`。

    排序（确定性）：`current_streak DESC, max_streak DESC, boards DESC,
    last_limit_up DESC, symbol ASC`。热度股的读法就是"今天还在连板的先看"，所以
    当前连板高度排第一；`symbol` 兜底保证同分时顺序稳定可测。
    """
    if not dates or not facts:
        return [], 0, False

    date_index = {d: i for i, d in enumerate(dates)}
    by_symbol: dict[str, dict[str, Any]] = {}

    for fact in facts:
        idx = date_index.get(fact["trade_date"])
        if idx is None:
            continue
        symbol = fact["symbol"]
        row = by_symbol.get(symbol)
        if row is None:
            row = {
                "symbol": symbol,
                "name": fact["name"] or symbol,
                "cells": [dict(_EMPTY_CELL) for _ in dates],
            }
            by_symbol[symbol] = row
        status = _cell_status(fact)
        row["cells"][idx] = {
            "status": status,
            # 连板数只在"当日收盘涨停"时是事实；其余状态给 None（缺失 ≠ 0）。
            "streak": int(fact["streak"]) if status == "limit_up" else None,
        }

    rows: list[dict[str, Any]] = []
    for row in by_symbol.values():
        cells = row["cells"]
        lu_streaks = [c["streak"] for c in cells if c["status"] == "limit_up"]
        last_cell = cells[-1]
        last_lu = next(
            (d for d, c in zip(reversed(dates), reversed(cells)) if c["status"] == "limit_up"),
            None,
        )
        rows.append(
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "max_streak": max(lu_streaks) if lu_streaks else 0,
                "boards": len(lu_streaks),
                "broken_days": sum(1 for c in cells if c["status"] == "broken"),
                "last_limit_up": last_lu,
                "current_status": last_cell["status"],
                "current_streak": last_cell["streak"] if last_cell["status"] == "limit_up" else 0,
                "cells": cells,
            }
        )

    kept = [r for r in rows if r["max_streak"] >= min_streak]
    kept.sort(
        key=lambda r: (
            -r["current_streak"],
            -r["max_streak"],
            -r["boards"],
            # 日期降序：`date` 不可取负，用 ordinal（无涨停 → 0 = 最小）。
            -(r["last_limit_up"].toordinal() if r["last_limit_up"] else 0),
            r["symbol"],
        )
    )
    truncated = len(kept) > limit
    return kept[:limit], len(kept), truncated


async def get_stock_history(
    db: AsyncSession,
    *,
    days: int = DEFAULT_DAYS,
    min_streak: int = DEFAULT_MIN_STREAK,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """「最近 N 个交易日 × 股票」涨停事实矩阵（`GET /market/limit-up/stock-history`）。

    `as_of` 取事实表自身的最近交易日（`latest_limit_up_stock_date`），回落到行情表
    （`latest_quote_date`）——回补还没跑过时事实表为空，仍要给出"截止哪天"的语义，
    但此时 `degraded_reason="no_fact_rows"`。

    窗口：`as_of` 往前 `days` 个交易日取自 `daily_quotes` 的实际日期（库内无持久化
    交易日历，"有行情"就是最准的可用性判据）；`dates` 再收窄到**事实表实际有行**的
    那些，缺的进 `missing_dates`。
    """
    as_of = await limit_up_repo.latest_limit_up_stock_date(db)
    if as_of is None:
        as_of = await limit_up_repo.latest_quote_date(db)

    if as_of is None:
        return {
            "as_of": None,
            "window": {"days": days, "start": None, "end": None, "samples": 0},
            "dates": [],
            "min_streak": min_streak,
            "total": 0,
            "truncated": False,
            "degraded_reason": "no_fact_rows",
            "missing_dates": [],
            "rows": [],
        }

    trade_dates = await limit_up_repo.list_recent_trade_dates(db, as_of, days)
    facts: list[dict[str, Any]] = []
    if trade_dates:
        facts = await limit_up_repo.list_limit_up_stock_facts_in_range(
            db, trade_dates[0], trade_dates[-1]
        )

    fact_dates = sorted({f["trade_date"] for f in facts})
    present = set(fact_dates)
    missing_dates = [d for d in trade_dates if d not in present]

    rows, total, truncated = build_stock_history_rows(
        facts, fact_dates, min_streak=min_streak, limit=limit
    )

    if not facts:
        degraded_reason: str | None = "no_fact_rows"
    elif missing_dates:
        degraded_reason = "facts_gap"
    else:
        degraded_reason = None

    return {
        "as_of": as_of,
        "window": {
            "days": days,
            "start": fact_dates[0] if fact_dates else None,
            "end": fact_dates[-1] if fact_dates else None,
            "samples": len(fact_dates),
        },
        "dates": fact_dates,
        "min_streak": min_streak,
        "total": total,
        "truncated": truncated,
        "degraded_reason": degraded_reason,
        "missing_dates": missing_dates,
        "rows": rows,
    }
