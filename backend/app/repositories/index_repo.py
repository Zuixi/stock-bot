"""Index daily repository: kline, latest snapshot, and bulk upsert."""

from datetime import date

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.index_daily import IndexDaily
from app.models.quote import DailyQuote


async def get_kline(
    db: AsyncSession,
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[IndexDaily]:
    stmt = select(IndexDaily).where(IndexDaily.ts_code == ts_code).order_by(IndexDaily.trade_date)
    if start_date:
        stmt = stmt.where(IndexDaily.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(IndexDaily.trade_date <= end_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest(
    db: AsyncSession,
    ts_codes: list[str],
) -> list[IndexDaily]:
    """Return the latest row for each ts_code in the given list."""
    if not ts_codes:
        return []

    from sqlalchemy import func

    subq = (
        select(
            IndexDaily.ts_code,
            func.max(IndexDaily.trade_date).label("max_date"),
        )
        .where(IndexDaily.ts_code.in_(ts_codes))
        .group_by(IndexDaily.ts_code)
        .subquery()
    )
    stmt = select(IndexDaily).join(
        subq,
        (IndexDaily.ts_code == subq.c.ts_code) & (IndexDaily.trade_date == subq.c.max_date),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_single(
    db: AsyncSession,
    ts_code: str,
) -> IndexDaily | None:
    stmt = (
        select(IndexDaily)
        .where(IndexDaily.ts_code == ts_code)
        .order_by(desc(IndexDaily.trade_date))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def max_trade_date_by_code(db: AsyncSession, ts_codes: list[str]) -> dict[str, date]:
    """各指数在 ``index_dailies`` 里已有的最新交易日（缺行即不在字典里）。

    给启动自愈用：
    没有它，``data_init`` 每次重启都会把「近一年」重拉一遍并 upsert
    （实测 6 个指数 × 242 行／次）。
    与个股行情（`list_stocks_missing_daily_coverage`）、基本面（`existing_dates` 差集）同一种做法：
    **先查库、再决定窗口**。
    """
    if not ts_codes:
        return {}
    stmt = (
        select(IndexDaily.ts_code, func.max(IndexDaily.trade_date))
        .where(IndexDaily.ts_code.in_(ts_codes))
        .group_by(IndexDaily.ts_code)
    )
    rows = (await db.execute(stmt)).all()
    return {str(code): day for code, day in rows if day is not None}


async def list_recent_quote_days(db: AsyncSession, as_of: date, limit: int) -> list[date]:
    """最近 ``limit`` 个**有行情**的交易日（升序）——启动覆盖校验的日历基准.

    用 ``daily_quotes`` 而不是工作日推算：节假日/停市由行情自身回答。
    """
    rows = (
        await db.execute(
            select(DailyQuote.trade_date)
            .where(DailyQuote.trade_date <= as_of)
            .group_by(DailyQuote.trade_date)
            .order_by(DailyQuote.trade_date.desc())
            .limit(limit)
        )
    ).all()
    return sorted(row[0] for row in rows)


async def trade_dates_by_code(
    db: AsyncSession, ts_codes: list[str], start: date, end: date
) -> dict[str, set[date]]:
    """各指数在 ``[start, end]`` 内**实际有**的交易日（覆盖校验用）。"""
    if not ts_codes:
        return {}
    rows = (
        await db.execute(
            select(IndexDaily.ts_code, IndexDaily.trade_date).where(
                IndexDaily.ts_code.in_(ts_codes),
                IndexDaily.trade_date >= start,
                IndexDaily.trade_date <= end,
            )
        )
    ).all()
    out: dict[str, set[date]] = {}
    for code, day in rows:
        out.setdefault(str(code), set()).add(day)
    return out


async def upsert_index_dailies(db: AsyncSession, rows: list[IndexDaily]) -> int:
    """Bulk upsert index daily rows; returns the number of rows affected."""
    from sqlalchemy.dialects.postgresql import insert

    if not rows:
        return 0

    values = [
        {
            "ts_code": r.ts_code,
            "trade_date": r.trade_date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "pre_close": r.pre_close,
            "volume": r.volume,
            "amount": r.amount,
        }
        for r in rows
    ]

    stmt = (
        insert(IndexDaily)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_index_dailies_code_date",
            set_={
                "open": insert(IndexDaily).excluded.open,
                "high": insert(IndexDaily).excluded.high,
                "low": insert(IndexDaily).excluded.low,
                "close": insert(IndexDaily).excluded.close,
                "pre_close": insert(IndexDaily).excluded.pre_close,
                "volume": insert(IndexDaily).excluded.volume,
                "amount": insert(IndexDaily).excluded.amount,
            },
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount  # type: ignore[attr-defined, no-any-return]
