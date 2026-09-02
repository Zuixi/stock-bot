"""Securities (ETF / convertible-bond) daily repository — idempotent upserts + per-code series."""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.securities import CbDaily, FundEtfDaily

# 两张日线表共用同一冲突口径：(ts_code, trade_date) — 与迁移内
# uq_fund_etf_daily_code_date / uq_cb_daily_code_date 一致（单测锁定）。
SECURITIES_CONFLICT_COLS = ("ts_code", "trade_date")

_ETF_UQ = "uq_fund_etf_daily_code_date"
_CB_UQ = "uq_cb_daily_code_date"


async def _upsert_daily(db: AsyncSession, model, uq_name: str, rows: list[dict]) -> int:
    """Idempotent bulk upsert of daily rows. Returns affected row count."""
    if not rows:
        return 0
    stmt = pg_insert(model).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint=uq_name,
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "pre_close": stmt.excluded.pre_close,
            "volume": stmt.excluded.volume,
            "amount": stmt.excluded.amount,
        },
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def upsert_fund_etf_daily(db: AsyncSession, rows: list[dict]) -> int:
    return await _upsert_daily(db, FundEtfDaily, _ETF_UQ, rows)


async def upsert_cb_daily(db: AsyncSession, rows: list[dict]) -> int:
    return await _upsert_daily(db, CbDaily, _CB_UQ, rows)


async def get_daily_series(db: AsyncSession, model, ts_code: str, limit: int = 90) -> list:
    """Ascending latest-N daily rows for one code."""
    stmt = (
        select(model)
        .where(model.ts_code == ts_code)
        .order_by(desc(model.trade_date))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(reversed(result.scalars().all()))
