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
    Announcement,
    BlockTrade,
    DragonTigerEntry,
    MarketMoneyflowDaily,
    NorthboundDaily,
    SectorMoneyflowSnapshot,
    ShareFloat,
    StockRepurchase,
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
            "lead_stock_name": r.get("lead_stock_name"),
            "lead_stock_code": r.get("lead_stock_code"),
            "lead_stock_pct": r.get("lead_stock_pct"),
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
                "lead_stock_name": pg_insert(SectorMoneyflowSnapshot).excluded.lead_stock_name,
                "lead_stock_code": pg_insert(SectorMoneyflowSnapshot).excluded.lead_stock_code,
                "lead_stock_pct": pg_insert(SectorMoneyflowSnapshot).excluded.lead_stock_pct,
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


async def upsert_share_floats(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """限售解禁明细入库（uq_share_floats_dedupe 冲突 DO NOTHING——计划快照，重复直接跳过）。

    ann_date 可为 NULL：Postgres 唯一约束不判重 NULL → NULL ann_date 行可能重复入库（可接受）。
    """
    if not rows:
        return 0
    values = [
        {
            "ann_date": r.get("ann_date"), "float_date": r["float_date"], "ts_code": r["ts_code"],
            "float_share": r.get("float_share"), "float_ratio": r.get("float_ratio"),
            "holder_name": r.get("holder_name"), "share_type": r.get("share_type"),
            "source": "tushare:share_float",
        }
        for r in rows
    ]
    stmt = (
        pg_insert(ShareFloat)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_share_floats_dedupe")
    )
    # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_share_floats(
    db: AsyncSession, start: date, end: date, symbol: str | None, limit: int = 30
) -> list[dict]:
    """解禁窗口内按解禁日期倒序（LEFT JOIN stocks 补股票名；symbol 过滤 6 位代码）。"""
    sym = func.split_part(ShareFloat.ts_code, ".", 1)
    stmt = (
        select(ShareFloat, Stock.name.label("stock_name"))
        .outerjoin(Stock, sym == Stock.symbol)
        .where(ShareFloat.float_date.between(start, end))
    )
    if symbol:
        stmt = stmt.where(sym == symbol)
    stmt = stmt.order_by(nullslast(desc(ShareFloat.float_date))).limit(limit)
    rows: list[dict] = []
    for sf, stock_name in (await db.execute(stmt)).all():
        rows.append({
            "ann_date": sf.ann_date.isoformat() if sf.ann_date else None,
            "float_date": sf.float_date.isoformat(), "ts_code": sf.ts_code,
            "symbol": sf.ts_code.split(".")[0], "name": stock_name,
            "float_share": sf.float_share, "float_ratio": sf.float_ratio,
            "holder_name": sf.holder_name, "share_type": sf.share_type,
        })
    return rows


async def upsert_repurchases(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """股票回购入库（uq_stock_repurchases_dedupe 冲突 DO UPDATE——进度/数量/价格区间会修订）。

    同批 (ann_date, ts_code, proc) 重复行 ON CONFLICT 不自处理，先保留末次出现去重。
    """
    if not rows:
        return 0
    deduped = list({
        (r["ann_date"], r["ts_code"], r["proc"]): r for r in rows
    }.values())
    values = [
        {
            "ann_date": r["ann_date"], "ts_code": r["ts_code"],
            "end_date": r.get("end_date"), "proc": r["proc"], "exp_date": r.get("exp_date"),
            "vol": r.get("vol"), "amount": r.get("amount"),
            "high_limit": r.get("high_limit"), "low_limit": r.get("low_limit"),
            "source": "tushare:repurchase",
        }
        for r in deduped
    ]
    stmt = (
        pg_insert(StockRepurchase)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_stock_repurchases_dedupe",
            set_={
                "end_date": pg_insert(StockRepurchase).excluded.end_date,
                "exp_date": pg_insert(StockRepurchase).excluded.exp_date,
                "vol": pg_insert(StockRepurchase).excluded.vol,
                "amount": pg_insert(StockRepurchase).excluded.amount,
                "high_limit": pg_insert(StockRepurchase).excluded.high_limit,
                "low_limit": pg_insert(StockRepurchase).excluded.low_limit,
            },
        )
    )
    # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_repurchases(
    db: AsyncSession, start: date, end: date, symbol: str | None, limit: int = 30
) -> list[dict]:
    """回购窗口内按公告日期倒序（LEFT JOIN stocks 补股票名；symbol 过滤 6 位代码）。"""
    sym = func.split_part(StockRepurchase.ts_code, ".", 1)
    stmt = (
        select(StockRepurchase, Stock.name.label("stock_name"))
        .outerjoin(Stock, sym == Stock.symbol)
        .where(StockRepurchase.ann_date.between(start, end))
    )
    if symbol:
        stmt = stmt.where(sym == symbol)
    stmt = stmt.order_by(nullslast(desc(StockRepurchase.ann_date))).limit(limit)
    rows: list[dict] = []
    for rp, stock_name in (await db.execute(stmt)).all():
        rows.append({
            "ann_date": rp.ann_date.isoformat(), "ts_code": rp.ts_code,
            "symbol": rp.ts_code.split(".")[0], "name": stock_name,
            "proc": rp.proc, "end_date": rp.end_date.isoformat() if rp.end_date else None,
            "exp_date": rp.exp_date.isoformat() if rp.exp_date else None,
            "vol": rp.vol, "amount": rp.amount,
        })
    return rows


async def upsert_announcements(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """公告入库（uq_announcements_cninfo_id 冲突 DO NOTHING——按 announcement_id 判重）。"""
    if not rows:
        return 0
    stmt = (
        pg_insert(Announcement)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_announcements_cninfo_id")
    )
    # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_announcements(
    db: AsyncSession, symbol: str | None, limit: int
) -> list[Announcement]:
    """公告按披露时间倒序（symbol 可选过滤 6 位代码）。"""
    stmt = select(Announcement)
    if symbol:
        stmt = stmt.where(Announcement.sec_code == symbol)
    stmt = stmt.order_by(desc(Announcement.announce_time)).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def upsert_market_moneyflow_daily(db: AsyncSession, rows: list[dict]) -> int:
    """大盘资金流日线幂等覆盖（历史回补与每日增量共用）。"""
    if not rows:
        return 0
    values = [
        {
            "trade_date": r["trade_date"], "main_net": r.get("main_net"),
            "super_large_net": r.get("super_large_net"), "large_net": r.get("large_net"),
            "mid_net": r.get("mid_net"), "small_net": r.get("small_net"),
            "main_ratio": r.get("main_ratio"), "close": r.get("close"),
            "pct_change": r.get("pct_change"), "amount": r.get("amount"),
            "source": r.get("source") or "em:fflow_daykline",
        }
        for r in rows
    ]
    stmt = (
        pg_insert(MarketMoneyflowDaily)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_market_moneyflow_date",
            set_={
                "main_net": pg_insert(MarketMoneyflowDaily).excluded.main_net,
                "super_large_net": pg_insert(MarketMoneyflowDaily).excluded.super_large_net,
                "large_net": pg_insert(MarketMoneyflowDaily).excluded.large_net,
                "mid_net": pg_insert(MarketMoneyflowDaily).excluded.mid_net,
                "small_net": pg_insert(MarketMoneyflowDaily).excluded.small_net,
                "main_ratio": pg_insert(MarketMoneyflowDaily).excluded.main_ratio,
                "close": pg_insert(MarketMoneyflowDaily).excluded.close,
                "pct_change": pg_insert(MarketMoneyflowDaily).excluded.pct_change,
                "amount": pg_insert(MarketMoneyflowDaily).excluded.amount,
            },
        )
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_market_moneyflow_daily(db: AsyncSession, days: int) -> list[MarketMoneyflowDaily]:
    """最近 N 个交易日的大盘资金流日线，升序。"""
    stmt = (
        select(MarketMoneyflowDaily)
        .order_by(MarketMoneyflowDaily.trade_date.desc())
        .limit(days)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return rows
