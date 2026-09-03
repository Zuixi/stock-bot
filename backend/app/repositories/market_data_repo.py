"""Repositories for market_data face tables."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, nullslast, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import (
    BlockTrade,
    DragonTigerEntry,
    NorthboundDaily,
    SectorMoneyflowSnapshot,
)
from app.models.stock import Stock


async def upsert_sector_moneyflow(
    db: AsyncSession, trade_date: date, dimension: str, rows: list[dict[str, Any]]
) -> int:
    """当日快照幂等覆盖（盘中每次轮询 upsert）。"""
    if not rows:
        return 0
    values = [
        {
            "trade_date": trade_date,
            "dimension": dimension,
            "board_code": r["board_code"],
            "board_name": r.get("board_name"),
            "pct_change": r.get("pct_change"),
            "main_net_inflow": r.get("main_net_inflow"),
            "super_large_net": r.get("super_large_net"),
            "large_net": r.get("large_net"),
            "main_net_ratio": r.get("main_net_ratio"),
            "up_count": r.get("up_count"),
            "down_count": r.get("down_count"),
        }
        for r in rows
    ]
    stmt = (
        pg_insert(SectorMoneyflowSnapshot)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_sector_moneyflow_dim_code_date",
            set_={
                "board_name": pg_insert(SectorMoneyflowSnapshot).excluded.board_name,
                "pct_change": pg_insert(SectorMoneyflowSnapshot).excluded.pct_change,
                "main_net_inflow": pg_insert(SectorMoneyflowSnapshot).excluded.main_net_inflow,
                "super_large_net": pg_insert(SectorMoneyflowSnapshot).excluded.super_large_net,
                "large_net": pg_insert(SectorMoneyflowSnapshot).excluded.large_net,
                "main_net_ratio": pg_insert(SectorMoneyflowSnapshot).excluded.main_net_ratio,
                "up_count": pg_insert(SectorMoneyflowSnapshot).excluded.up_count,
                "down_count": pg_insert(SectorMoneyflowSnapshot).excluded.down_count,
                # pg on_conflict 不走 ORM onupdate → 显式刷 updated_at
                "updated_at": func.now(),
            },
        )
    )
    # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_sector_moneyflow(
    db: AsyncSession, trade_date: date, dimension: str, limit: int = 15
) -> list[SectorMoneyflowSnapshot]:
    """当日某维度板块按主力净流入降序（NULL 最后）。"""
    stmt = (
        select(SectorMoneyflowSnapshot)
        .where(
            SectorMoneyflowSnapshot.trade_date == trade_date,
            SectorMoneyflowSnapshot.dimension == dimension,
        )
        .order_by(nullslast(desc(SectorMoneyflowSnapshot.main_net_inflow)))
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def upsert_northbound(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """北向净流入日序列幂等 upsert（冲突时只刷 net_amount；source 首写后不变）。"""
    if not rows:
        return 0
    stmt = (
        pg_insert(NorthboundDaily)
        .values([{**r, "source": "tushare:moneyflow_hsgt"} for r in rows])
        .on_conflict_do_update(
            constraint="uq_northbound_date",
            set_={"net_amount": pg_insert(NorthboundDaily).excluded.net_amount},
        )
    )
    # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_northbound(db: AsyncSession, days: int) -> list[NorthboundDaily]:
    """近 N 日北向净流入按交易日升序（上海时区语义，与 ingest 窗口对齐）。"""
    cutoff = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=days)
    stmt = (
        select(NorthboundDaily)
        .where(NorthboundDaily.trade_date >= cutoff)
        .order_by(NorthboundDaily.trade_date)
    )
    return list((await db.execute(stmt)).scalars().all())


async def upsert_dragon_tiger(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """龙虎榜明细幂等 upsert（uq_dragon_tiger_date_code_reason；行情列可能修订 → DO UPDATE）。

    同批 (date, code, reason) 重复行 ON CONFLICT 不自处理，先保留末次出现去重。
    """
    if not rows:
        return 0
    deduped = list({
        (r["trade_date"], r["ts_code"], r["reason"]): r for r in rows
    }.values())
    values = [
        {
            "trade_date": r["trade_date"], "ts_code": r["ts_code"],
            "name": r.get("name"), "close": r.get("close"), "pct_change": r.get("pct_change"),
            "turnover_rate": r.get("turnover_rate"), "amount": r.get("amount"),
            "l_buy": r.get("l_buy"), "l_sell": r.get("l_sell"), "l_amount": r.get("l_amount"),
            "net_amount": r.get("net_amount"), "net_rate": r.get("net_rate"),
            "amount_rate": r.get("amount_rate"), "float_values": r.get("float_values"),
            "reason": r["reason"], "source": "tushare:top_list",
        }
        for r in deduped
    ]
    stmt = (
        pg_insert(DragonTigerEntry)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_dragon_tiger_date_code_reason",
            set_={
                "name": pg_insert(DragonTigerEntry).excluded.name,
                "close": pg_insert(DragonTigerEntry).excluded.close,
                "pct_change": pg_insert(DragonTigerEntry).excluded.pct_change,
                "turnover_rate": pg_insert(DragonTigerEntry).excluded.turnover_rate,
                "amount": pg_insert(DragonTigerEntry).excluded.amount,
                "l_buy": pg_insert(DragonTigerEntry).excluded.l_buy,
                "l_sell": pg_insert(DragonTigerEntry).excluded.l_sell,
                "l_amount": pg_insert(DragonTigerEntry).excluded.l_amount,
                "net_amount": pg_insert(DragonTigerEntry).excluded.net_amount,
                "net_rate": pg_insert(DragonTigerEntry).excluded.net_rate,
                "amount_rate": pg_insert(DragonTigerEntry).excluded.amount_rate,
                "float_values": pg_insert(DragonTigerEntry).excluded.float_values,
            },
        )
    )
    # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def max_dragon_tiger_date(db: AsyncSession) -> date | None:
    """龙虎榜表内最新交易日（读取端点 date 缺省值）。"""
    stmt = select(func.max(DragonTigerEntry.trade_date))
    scalar: date | None = (await db.execute(stmt)).scalar_one()
    return scalar


async def list_dragon_tiger(
    db: AsyncSession, trade_date: date, limit: int = 15
) -> list[DragonTigerEntry]:
    """某交易日龙虎榜按净买入额降序（NULL 最后）。"""
    stmt = (
        select(DragonTigerEntry)
        .where(DragonTigerEntry.trade_date == trade_date)
        .order_by(nullslast(desc(DragonTigerEntry.net_amount)))
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def upsert_block_trades(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """大宗交易明细入库（uq_block_trades_dedupe 冲突 DO NOTHING——行无稳定业务键，重复直接跳过）。"""
    if not rows:
        return 0
    values = [
        {
            "trade_date": r["trade_date"], "ts_code": r["ts_code"],
            "price": r.get("price"), "volume": r.get("volume"), "amount": r.get("amount"),
            "buyer": r.get("buyer"), "seller": r.get("seller"), "source": "tushare:block_trade",
        }
        for r in rows
    ]
    stmt = (
        pg_insert(BlockTrade)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_block_trades_dedupe")
    )
    # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def max_block_trade_date(db: AsyncSession) -> date | None:
    """大宗交易表内最新交易日（读取端点 date 缺省值）。"""
    stmt = select(func.max(BlockTrade.trade_date))
    scalar: date | None = (await db.execute(stmt)).scalar_one()
    return scalar


async def list_block_trades(
    db: AsyncSession, trade_date: date, symbol: str | None, limit: int = 15
) -> list[dict]:
    """某交易日大宗交易按成交额降序（LEFT JOIN stocks 补股票名；symbol 过滤 6 位代码）。"""
    sym = func.split_part(BlockTrade.ts_code, ".", 1)
    stmt = (
        select(BlockTrade, Stock.name.label("stock_name"))
        .outerjoin(Stock, sym == Stock.symbol)
        .where(BlockTrade.trade_date == trade_date)
    )
    if symbol:
        stmt = stmt.where(sym == symbol)
    stmt = stmt.order_by(BlockTrade.amount.desc().nulls_last()).limit(limit)
    rows: list[dict] = []
    for trade, stock_name in (await db.execute(stmt)).all():
        rows.append({
            "trade_date": trade.trade_date.isoformat(), "ts_code": trade.ts_code,
            "symbol": trade.ts_code.split(".")[0], "name": stock_name,
            "price": trade.price, "volume": trade.volume, "amount": trade.amount,
            "buyer": trade.buyer, "seller": trade.seller,
        })
    return rows
