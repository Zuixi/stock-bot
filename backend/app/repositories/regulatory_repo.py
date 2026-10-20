"""监管异动雷达的数据访问（只读、批量、反 N+1）。

四条查询形状即契约：

1. **全市场近 13 个交易日行情**（≈7 万行）——用于「今日候选 + 近 10 日异动历史」。
   这一条走 `stream_quotes`（服务端游标 + 按 `stock_id` 分组消费），**不整包物化**：
   整包实测 Python 峰值 84MB / RSS +170MB，512MB 容器里并发就是 exit 137；
2. **候选股近 35 个交易日行情**（≈几百只 × 35 行）——用于 10/30 日累计与事件计数；
3. **当日涨跌停价 + 连板事实**（≈5500 + 120 行）——涨停判定与明日触发价的分母；
4. **换手率旁路**（近 8 日 `daily_basic_indicators`，SQL 只回命中）——独立于价格口径的
   `turnover_only[]`；另有一条 `oldest_recent_trade_day` 给 `?date=` 的 250 交易日窗。

基准指数用 ``lag(close)`` 现算日涨跌幅，**不用** ``index_dailies.pre_close``：
实测 000688.SH 的 pre_close 全列为 NULL、其余 4 个指数近月也有 8 天空值。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

_QUOTE_COLUMNS = """
    q.stock_id, s.symbol, s.name, s.market, s.exchange, q.trade_date, q.close, q.pre_close
"""


async def list_market_days(db: AsyncSession, as_of: date, limit: int) -> list[date]:
    """最近 ``limit`` 个有行情的交易日（升序）。真源是 ``daily_quotes`` 的实际日期。"""
    rows = (
        await db.execute(
            text(
                """
                SELECT DISTINCT trade_date
                FROM daily_quotes
                WHERE trade_date <= :as_of
                ORDER BY trade_date DESC
                LIMIT :limit
                """
            ),
            {"as_of": as_of, "limit": limit},
        )
    ).scalars()
    return sorted(rows.all())


async def load_quotes(
    db: AsyncSession, start: date, end: date, stock_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """行情窗口；``stock_ids`` 为空 = 全市场。返回按 ``(stock_id, trade_date)`` 升序。"""
    if stock_ids is not None and not stock_ids:
        return []
    sql = f"""
        SELECT {_QUOTE_COLUMNS}
        FROM daily_quotes q
        JOIN stocks s ON s.id = q.stock_id
        WHERE q.trade_date BETWEEN :start AND :end
    """
    params: dict[str, Any] = {"start": start, "end": end}
    if stock_ids is not None:
        sql += " AND q.stock_id IN :stock_ids"
        params["stock_ids"] = stock_ids
    sql += " ORDER BY q.stock_id, q.trade_date"
    stmt = text(sql)
    if stock_ids is not None:
        stmt = stmt.bindparams(bindparam("stock_ids", expanding=True))
    rows = (await db.execute(stmt, params)).mappings().all()
    return [dict(r) for r in rows]


async def stream_quotes(db: AsyncSession, start: date, end: date) -> AsyncIterator[RowMapping]:
    """全市场行情窗口的**流式**读法（与 `load_quotes` 同列、同排序，但不整包返回）。

    为什么要它：近 13 个交易日 × 全市场 ≈ 7 万行，`mappings().all()` 会先物化整个结果集
    再转成 7 万个 dict；在 512MB 的 api 容器里实测 Python 峰值 84MB / RSS +170MB，
    并发冷请求叠加后就是 exit 137。调用方按 `stock_id` 分组顺序流式消费 ⇒ 常驻内存
    与股数无关。

    约束：结果按 `(stock_id, trade_date)` 升序，调用方靠「stock_id 变了就结算上一只」工作；
    必须**完整耗尽**（或关闭生成器）以释放服务端游标。
    """
    stmt = text(
        f"""
        SELECT {_QUOTE_COLUMNS}
        FROM daily_quotes q
        JOIN stocks s ON s.id = q.stock_id
        WHERE q.trade_date BETWEEN :start AND :end
        ORDER BY q.stock_id, q.trade_date
        """
    ).execution_options(max_row_buffer=5000)
    result = await db.stream(stmt, {"start": start, "end": end})
    async for row in result.mappings():
        yield row


async def load_index_pct(
    db: AsyncSession, codes: list[str], start: date, end: date
) -> dict[str, dict[date, float]]:
    """各基准指数逐日涨跌幅（%）。首日缺前收 → 该日不参与（None 由调用方跳过）。"""
    if not codes:
        return {}
    stmt = text(
        """
        WITH idx AS (
            SELECT ts_code, trade_date, close,
                   lag(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close
            FROM index_dailies
            WHERE ts_code IN :codes
              AND trade_date <= :end
              AND trade_date >= :start
        )
        SELECT ts_code, trade_date, close, prev_close
        FROM idx
        WHERE prev_close IS NOT NULL AND prev_close > 0
        """
    ).bindparams(bindparam("codes", expanding=True))
    rows = (await db.execute(stmt, {"codes": codes, "start": start, "end": end})).mappings().all()
    out: dict[str, dict[date, float]] = {}
    for row in rows:
        pct = (float(row["close"]) / float(row["prev_close"]) - 1.0) * 100.0
        out.setdefault(str(row["ts_code"]), {})[row["trade_date"]] = pct
    return out


async def oldest_recent_trade_day(db: AsyncSession, limit: int) -> date | None:
    """最近 ``limit`` 个交易日里最早的一天（``?date=`` 的允许下界）。

    用「倒序取 limit 个不同日期，再取最小」而不是 `date - interval`：交易日历/节假日由
    `daily_quotes` 自己回答，不靠工作日推算。
    """
    row = (
        await db.execute(
            text(
                """
                SELECT trade_date FROM (
                    SELECT DISTINCT trade_date
                    FROM daily_quotes
                    ORDER BY trade_date DESC
                    LIMIT :limit
                ) recent
                ORDER BY trade_date ASC
                LIMIT 1
                """
            ),
            {"limit": limit},
        )
    ).scalar_one_or_none()
    return row


async def load_price_limits(db: AsyncSession, as_of: date) -> dict[int, dict[str, float]]:
    """当日交易所限价（唯一权威）+ 收盘。缺失的股票不在这里 → 调用方不得用名称推限幅。"""
    rows = (
        await db.execute(
            text(
                """
                SELECT l.stock_id, l.pre_close, l.up_limit, l.down_limit, q.close
                FROM stock_price_limits l
                JOIN daily_quotes q
                  ON q.stock_id = l.stock_id AND q.trade_date = l.trade_date
                WHERE l.trade_date = :as_of
                """
            ),
            {"as_of": as_of},
        )
    ).mappings()
    return {
        int(r["stock_id"]): {
            "pre_close": float(r["pre_close"]) if r["pre_close"] is not None else 0.0,
            "up_limit": float(r["up_limit"]) if r["up_limit"] is not None else 0.0,
            "down_limit": float(r["down_limit"]) if r["down_limit"] is not None else 0.0,
            "close": float(r["close"]),
        }
        for r in rows.all()
    }


async def turnover_data_as_of(db: AsyncSession, as_of: date) -> date | None:
    """`daily_basic_indicators` 截至 ``as_of`` 的最新日期（换手率口径是否可用）。"""
    return (
        await db.execute(
            text(
                """
                SELECT max(trade_date)
                FROM daily_basic_indicators
                WHERE trade_date <= :as_of
                """
            ),
            {"as_of": as_of},
        )
    ).scalar_one_or_none()


async def load_turnover_only_hits(db: AsyncSession, as_of: date) -> list[dict[str, Any]]:
    """换手率口径命中（**价格口径之外**的独立触发条件）.

    交易所规则：连续 3 个交易日内日均换手率 ≥ 前 5 个交易日日均的 **30 倍**，且 3 日
    累计换手率 ≥ **20%**。这里只返回**通过该口径**的票；调用方再剔除已经出现在
    `items` 里的票，剩下的就是「价格没到线、但换手率单独达标」的旁路。

    口径纪律：只用 `turnover_rate`（`turnover_rate_f` 实测全列 NULL）；`n3=3 / n5=5`
    要求最近 3 天与前 5 天都有行——停牌缺行不判（不拿更早的换手率顶替）。
    """
    rows = (
        await db.execute(
            text(
                """
                WITH days AS (
                    SELECT trade_date,
                           row_number() OVER (ORDER BY trade_date DESC) AS rn
                    FROM (
                        SELECT DISTINCT trade_date
                        FROM daily_quotes
                        WHERE trade_date <= :as_of
                        ORDER BY trade_date DESC
                        LIMIT 9
                    ) recent
                ),
                db AS (
                    SELECT b.stock_id, b.turnover_rate, d.rn
                    FROM daily_basic_indicators b
                    JOIN days d ON d.trade_date = b.trade_date
                    WHERE b.turnover_rate IS NOT NULL
                ),
                agg AS (
                    SELECT stock_id,
                           count(*) FILTER (WHERE rn <= 3) AS n3,
                           count(*) FILTER (WHERE rn BETWEEN 4 AND 8) AS n5,
                           sum(turnover_rate) FILTER (WHERE rn <= 3) AS sum3,
                           sum(turnover_rate) FILTER (WHERE rn BETWEEN 4 AND 8) AS sum5
                    FROM db
                    GROUP BY stock_id
                )
                SELECT s.symbol, s.name, s.market, s.exchange,
                       q.close, q.pre_close,
                       a.sum3, a.sum5,
                       a.sum3 / 3.0 AS avg3,
                       a.sum5 / 5.0 AS avg5,
                       (a.sum3 / 3.0) / NULLIF(a.sum5 / 5.0, 0) AS ratio
                FROM agg a
                JOIN stocks s ON s.id = a.stock_id
                JOIN daily_quotes q
                  ON q.stock_id = a.stock_id AND q.trade_date = :as_of
                WHERE a.n3 = 3
                  AND a.n5 = 5
                  AND a.sum5 > 0
                  AND (a.sum3 / 3.0) >= 30.0 * (a.sum5 / 5.0)
                  AND a.sum3 >= 20.0
                ORDER BY ratio DESC, s.symbol
                """
            ),
            {"as_of": as_of},
        )
    ).mappings()
    return [dict(r) for r in rows.all()]


async def load_limit_up_facts(db: AsyncSession, as_of: date) -> dict[int, dict[str, Any]]:
    """当日 ``market_limit_up_stock_daily`` 的连板事实（缺行 = 该股当天不可判，不填 0）。"""
    rows = (
        await db.execute(
            text(
                """
                SELECT stock_id, streak, prev_is_lu, days_span, boards_in_window
                FROM market_limit_up_stock_daily
                WHERE trade_date = :as_of
                """
            ),
            {"as_of": as_of},
        )
    ).mappings()
    return {
        int(r["stock_id"]): {
            "streak": int(r["streak"]) if r["streak"] is not None else None,
            "prev_is_lu": bool(r["prev_is_lu"]),
            "days_span": int(r["days_span"]) if r["days_span"] is not None else None,
            "boards_in_window": int(r["boards_in_window"])
            if r["boards_in_window"] is not None
            else None,
        }
        for r in rows.all()
    }
