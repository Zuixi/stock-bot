"""跨页一致性检查（P7）——同一个事实不得有两套数.

两条独立路径对同一天求同一个量：一条读派生表（页面实际展示的），一条从底座重算。
不一致不自动修复：先让人看见，再决定是哪条路径错了（自动"修"会把错误固化）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ConsistencyCheck:
    key: str
    label: str
    sql_a: str
    sql_b: str
    tolerance: float = 0.0
    relative: bool = False


CHECKS: tuple[ConsistencyCheck, ...] = (
    ConsistencyCheck(
        key="up_count",
        label="上涨家数（广度表 vs 行情重算）",
        sql_a="SELECT up_count::numeric FROM market_breadth_daily WHERE trade_date = :day",
        # 与 `market_panorama_ingest_service._BREADTH_SQL` 同口径：上涨 = 涨幅 ≥ 1%
        # （0%~1% 计入 flat）。用 `> 0` 会把这 1103 只"微涨"重复计进上涨。
        sql_b="SELECT count(*)::numeric FROM daily_quotes WHERE trade_date = :day AND pct_chg >= 1",
    ),
    ConsistencyCheck(
        key="down_count",
        label="下跌家数（广度表 vs 行情重算）",
        sql_a="SELECT down_count::numeric FROM market_breadth_daily WHERE trade_date = :day",
        sql_b="SELECT count(*)::numeric FROM daily_quotes WHERE trade_date = :day AND pct_chg < 0",
    ),
    ConsistencyCheck(
        key="flat_count",
        label="平盘家数（广度表 vs 行情重算）",
        sql_a="SELECT flat_count::numeric FROM market_breadth_daily WHERE trade_date = :day",
        sql_b=(
            "SELECT count(*)::numeric FROM daily_quotes "
            "WHERE trade_date = :day AND pct_chg >= 0 AND pct_chg < 1"
        ),
    ),
    ConsistencyCheck(
        key="limit_up_count",
        label="涨停家数（广度表 vs 限价表重算）",
        sql_a="SELECT limit_up_count::numeric FROM market_breadth_daily WHERE trade_date = :day",
        sql_b=(
            "SELECT count(*)::numeric FROM daily_quotes q "
            "JOIN stock_price_limits l ON l.trade_date = q.trade_date AND l.stock_id = q.stock_id "
            "WHERE q.trade_date = :day AND l.up_limit IS NOT NULL "
            "AND q.close >= l.up_limit - 0.005"
        ),
    ),
    ConsistencyCheck(
        key="limit_down_count",
        label="跌停家数（广度表 vs 限价表重算）",
        sql_a="SELECT limit_down_count::numeric FROM market_breadth_daily WHERE trade_date = :day",
        sql_b=(
            "SELECT count(*)::numeric FROM daily_quotes q "
            "JOIN stock_price_limits l ON l.trade_date = q.trade_date AND l.stock_id = q.stock_id "
            "WHERE q.trade_date = :day AND l.down_limit IS NOT NULL "
            "AND q.close <= l.down_limit + 0.005"
        ),
    ),
    ConsistencyCheck(
        key="total_amount",
        label="全市场成交额（广度表 vs 行情求和）",
        sql_a="SELECT amount::numeric FROM market_breadth_daily WHERE trade_date = :day",
        sql_b="SELECT sum(amount)::numeric FROM daily_quotes WHERE trade_date = :day",
        tolerance=0.001,
        relative=True,
    ),
)


async def _scalar(db: AsyncSession, sql: str, day: date) -> float | None:
    value = (await db.execute(text(sql), {"day": day})).scalar_one_or_none()
    return float(value) if value is not None else None


async def check(db: AsyncSession, *, day: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in CHECKS:
        a = await _scalar(db, item.sql_a, day)
        b = await _scalar(db, item.sql_b, day)
        if a is None or b is None:
            status = "missing"
            diff = None
        else:
            diff = abs(a - b)
            allowed = item.tolerance * max(abs(a), abs(b), 1.0) if item.relative else item.tolerance
            status = "ok" if diff <= allowed else "mismatch"
        out.append(
            {
                "key": item.key,
                "label": item.label,
                "derived": a,
                "recomputed": b,
                "diff": diff,
                "status": status,
            }
        )
    return out
