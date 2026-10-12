"""Repositories for market_data face tables."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar, cast
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, nullslast, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import (
    Announcement,
    BlockTrade,
    DragonTigerEntry,
    MarketMoneyflowDaily,
    MarketSentimentIntraday,
    NorthboundDaily,
    SectorMoneyflowSnapshot,
    ShareFloat,
    StockRepurchase,
)
from app.models.stock import Stock

# asyncpg 单条语句 bind 参数上限 32767（实测解禁任务 share_float 近 7 日窗口一次
# 拉到数千行 × 8 列 → InterfaceError 静默崩了 13 天）。写多行的 upsert 必须显式分片，
# 单批 500 行 × 8 列 = 4000 个参数，留足余量。
UPSERT_CHUNK = 500

RowT = TypeVar("RowT")


def chunk_rows(rows: Sequence[RowT], size: int = UPSERT_CHUNK) -> Iterator[list[RowT]]:
    """把多行 INSERT 的入参切成 ``size`` 行一批（asyncpg 绑定参数上限防护）。"""
    if size <= 0:
        raise ValueError(f"chunk size must be positive, got {size}")
    for start in range(0, len(rows), size):
        yield list(rows[start : start + size])


def _dec(v: Any) -> Decimal | None:
    """``Numeric(20,4)`` 列的入参归一：``float`` → ``Decimal``（asyncpg 的 numeric 编码器
    只收 Decimal/str，收 float 会 TypeError）。

    缺值（``None``/上游``-``）原样返回 ``None`` —— 绝不落 ``Decimal(0)``：0 是"成交额 0 元"
    这一事实断言，而缺失是"不知道"。
    """
    if v is None or isinstance(v, Decimal):
        return v
    return Decimal(str(v))


async def upsert_sector_moneyflow(
    db: AsyncSession, trade_date: date, dimension: str, rows: list[dict[str, Any]]
) -> int:
    """当日快照幂等覆盖（盘中每次轮询 upsert）。

    ``amount``/``flat_count``（Task 2）与其它列同权：冲突时一并刷新 —— 收盘态覆盖盘中态
    靠的就是这条 DO UPDATE。``amount`` 是 ``Numeric(20,4)``，asyncpg 的 numeric 编码器
    不收 ``float``，故在此显式 ``Decimal(str(v))``（与 ``limit_up_service`` 的
    ``seal_fund`` 同款）；缺值仍走 ``None``（**绝不 0**）。
    """
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
            "amount": _dec(r.get("amount")),
            "flat_count": r.get("flat_count"),
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
                "amount": pg_insert(SectorMoneyflowSnapshot).excluded.amount,
                "flat_count": pg_insert(SectorMoneyflowSnapshot).excluded.flat_count,
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


async def list_sector_moneyflow_ranked(
    db: AsyncSession, trade_date: date, dimension: str, limit: int
) -> list[SectorMoneyflowSnapshot]:
    """当日某维度板块按 **涨幅降序 + 板块码升序** 取 Top-N（Task 2 读表分支专用）。

    这是 ``/market/hot-boards`` 的进榜判据（与上游 ``fid=f3`` 的"涨幅榜"**排序语义**一致）：
    ``pct_change DESC`` 配 ``board_code ASC`` 是**确定性 tiebreak** —— 同涨幅的两行
    在两次请求间不得互换位置（上游 ``fid=f3`` 的次级顺序不保证稳定）。``NULLS LAST``
    让缺涨幅的行排在最后（服务层再稳定排序一次并截断 Top-10）。

    注意行集口径：表里的行来自 ``fid=f62``（主力净流入）Top-100 的**逐次 upsert 并集**，
    不是 ``fid=f3`` 的 Top-100，名次与"最新一次截取"可能不同（见 Task 2 报告 concerns）。
    本函数只负责**排序**，不改变行集。
    """
    stmt = (
        select(SectorMoneyflowSnapshot)
        .where(
            SectorMoneyflowSnapshot.trade_date == trade_date,
            SectorMoneyflowSnapshot.dimension == dimension,
        )
        .order_by(
            nullslast(desc(SectorMoneyflowSnapshot.pct_change)),
            SectorMoneyflowSnapshot.board_code.asc(),
        )
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_sector_moneyflow_range(
    db: AsyncSession, dimension: str, start: date, end: date
) -> list[SectorMoneyflowSnapshot]:
    """某维度 **[start, end] 闭区间**的窗口读（Task 6 的赛段/连续性统计消费）。

    纯读：按 ``trade_date, board_code`` 升序返回全部行（不截断）—— 窗口统计要的是
    完整序列，"缺日"由调用方与交易日历对比得出（缺日 **不得** 被当成 0 流入）。
    本任务无消费方，保留单日版 :func:`list_sector_moneyflow` 不动。
    """
    stmt = (
        select(SectorMoneyflowSnapshot)
        .where(
            SectorMoneyflowSnapshot.dimension == dimension,
            SectorMoneyflowSnapshot.trade_date >= start,
            SectorMoneyflowSnapshot.trade_date <= end,
        )
        .order_by(
            SectorMoneyflowSnapshot.trade_date.asc(),
            SectorMoneyflowSnapshot.board_code.asc(),
        )
    )
    return list((await db.execute(stmt)).scalars().all())


async def sector_moneyflow_day_has_rows(db: AsyncSession, trade_date: date) -> bool:
    """该日是否已有**任何**维度的快照行（Task 2 的 15:35 缺行补写判据）。

    三维度由一次 ``ingest_sector_moneyflow`` 在同一事务里写入，故"日级"判据足够：要么
    三个维度都有当日行，要么一行都没有（不存在"只写了一半"的正常状态）。

    **已知边界**（ruling 的字面口径，未擅自放宽）：盘中轮询写的行也算"有行"，所以
    "容器 15:05 缺席、但当天盘中已经写过" 的日子 15:35 会 no-op —— 那天不会补抓收盘
    终态。`updated_at` 无法区分"盘中 tick"与"15:05 EOD"（15:00 的 tick 与 15:05 同分钟），
    故没有可靠的新鲜度判据；务实收敛点应是"15:05 之后是否还有抓取"（见 Task 2 报告 concerns）。
    """
    stmt = (
        select(SectorMoneyflowSnapshot.id)
        .where(SectorMoneyflowSnapshot.trade_date == trade_date)
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


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
    deduped = list({(r["trade_date"], r["ts_code"], r["reason"]): r for r in rows}.values())
    values = [
        {
            "trade_date": r["trade_date"],
            "ts_code": r["ts_code"],
            "name": r.get("name"),
            "close": r.get("close"),
            "pct_change": r.get("pct_change"),
            "turnover_rate": r.get("turnover_rate"),
            "amount": r.get("amount"),
            "l_buy": r.get("l_buy"),
            "l_sell": r.get("l_sell"),
            "l_amount": r.get("l_amount"),
            "net_amount": r.get("net_amount"),
            "net_rate": r.get("net_rate"),
            "amount_rate": r.get("amount_rate"),
            "float_values": r.get("float_values"),
            "reason": r["reason"],
            "source": "tushare:top_list",
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
            "trade_date": r["trade_date"],
            "ts_code": r["ts_code"],
            "price": r.get("price"),
            "volume": r.get("volume"),
            "amount": r.get("amount"),
            "buyer": r.get("buyer"),
            "seller": r.get("seller"),
            "source": "tushare:block_trade",
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
        rows.append(
            {
                "trade_date": trade.trade_date.isoformat(),
                "ts_code": trade.ts_code,
                "symbol": trade.ts_code.split(".")[0],
                "name": stock_name,
                "price": trade.price,
                "volume": trade.volume,
                "amount": trade.amount,
                "buyer": trade.buyer,
                "seller": trade.seller,
            }
        )
    return rows


async def upsert_share_floats(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """限售解禁明细入库（uq_share_floats_dedupe 冲突 DO NOTHING——计划快照，重复直接跳过）。

    ann_date 可为 NULL：Postgres 唯一约束不判重 NULL → NULL ann_date 行可能重复入库（可接受）。
    """
    if not rows:
        return 0
    values = [
        {
            "ann_date": r.get("ann_date"),
            "float_date": r["float_date"],
            "ts_code": r["ts_code"],
            "float_share": r.get("float_share"),
            "float_ratio": r.get("float_ratio"),
            "holder_name": r.get("holder_name"),
            "share_type": r.get("share_type"),
            "source": "tushare:share_float",
        }
        for r in rows
    ]
    # 必须分片：近 7 日窗口的行数轻易越过 32767/8 行上限（见 UPSERT_CHUNK 注释）
    upserted = 0
    for batch in chunk_rows(values):
        stmt = (
            pg_insert(ShareFloat)
            .values(batch)
            .on_conflict_do_nothing(constraint="uq_share_floats_dedupe")
        )
        # Core INSERT 的 execute 运行时返回 CursorResult（带 rowcount）；Result 存根无该属性
        result = cast("CursorResult[Any]", await db.execute(stmt))
        upserted += int(result.rowcount)
    await db.flush()
    return upserted


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
        rows.append(
            {
                "ann_date": sf.ann_date.isoformat() if sf.ann_date else None,
                "float_date": sf.float_date.isoformat(),
                "ts_code": sf.ts_code,
                "symbol": sf.ts_code.split(".")[0],
                "name": stock_name,
                "float_share": sf.float_share,
                "float_ratio": sf.float_ratio,
                "holder_name": sf.holder_name,
                "share_type": sf.share_type,
            }
        )
    return rows


async def upsert_repurchases(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """股票回购入库（uq_stock_repurchases_dedupe 冲突 DO UPDATE——进度/数量/价格区间会修订）。

    同批 (ann_date, ts_code, proc) 重复行 ON CONFLICT 不自处理，先保留末次出现去重。
    """
    if not rows:
        return 0
    deduped = list({(r["ann_date"], r["ts_code"], r["proc"]): r for r in rows}.values())
    values = [
        {
            "ann_date": r["ann_date"],
            "ts_code": r["ts_code"],
            "end_date": r.get("end_date"),
            "proc": r["proc"],
            "exp_date": r.get("exp_date"),
            "vol": r.get("vol"),
            "amount": r.get("amount"),
            "high_limit": r.get("high_limit"),
            "low_limit": r.get("low_limit"),
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
        rows.append(
            {
                "ann_date": rp.ann_date.isoformat(),
                "ts_code": rp.ts_code,
                "symbol": rp.ts_code.split(".")[0],
                "name": stock_name,
                "proc": rp.proc,
                "end_date": rp.end_date.isoformat() if rp.end_date else None,
                "exp_date": rp.exp_date.isoformat() if rp.exp_date else None,
                "vol": rp.vol,
                "amount": rp.amount,
            }
        )
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
            "trade_date": r["trade_date"],
            "main_net": r.get("main_net"),
            "super_large_net": r.get("super_large_net"),
            "large_net": r.get("large_net"),
            "mid_net": r.get("mid_net"),
            "small_net": r.get("small_net"),
            "main_ratio": r.get("main_ratio"),
            "close": r.get("close"),
            "pct_change": r.get("pct_change"),
            "amount": r.get("amount"),
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
    stmt = select(MarketMoneyflowDaily).order_by(MarketMoneyflowDaily.trade_date.desc()).limit(days)
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def upsert_intraday_snapshot(
    db: AsyncSession,
    trade_date: date,
    captured_at: datetime,
    rows: list[dict[str, Any]],
) -> int:
    """盘中分时点幂等 upsert（Task 12）。

    池行数即 ``zt_count``；``max_streak`` 由池内 ``max(streak)`` 计算（实操里
    scheduler 端已经算好再传，这里是接受 dict 输入的薄封装：取 ``zt_count`` /
    ``max_streak`` 即可，其它计数盘中无源，固定为 0）。

    ``ON CONFLICT (trade_date, captured_at) DO NOTHING``：scheduler 5min 节拍
    上同一 ``captured_at`` 跑两遍（coalesce 合并/重启追跑）不产生重复行；
    **不**用 ``DO UPDATE``——盘中点数据是"何时拍下的快照"，事后修正是新一行而非
    改旧行。
    """
    if not rows:
        return 0
    zt_count = len(rows)
    max_streak = 0
    for r in rows:
        try:
            s = int(r.get("streak") or 0)
        except (TypeError, ValueError):
            s = 0
        if s > max_streak:
            max_streak = s
    values = [
        {
            "trade_date": trade_date,
            "captured_at": captured_at,
            "zt_count": zt_count,
            "dt_count": 0,  # 盘中无 daily_quotes partial day 信号
            "zb_count": 0,  # 盘中无炸板率
            "max_streak": max_streak,
        }
    ]
    stmt = (
        pg_insert(MarketSentimentIntraday)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_market_sentiment_intraday_date_captured")
    )
    result = cast("CursorResult[Any]", await db.execute(stmt))
    await db.flush()
    return int(result.rowcount)


async def list_intraday_snapshot(
    db: AsyncSession, trade_date: date
) -> list[MarketSentimentIntraday]:
    """某交易日盘中分时点序列（按 ``captured_at`` 升序，端点契约）。"""
    stmt = (
        select(MarketSentimentIntraday)
        .where(MarketSentimentIntraday.trade_date == trade_date)
        .order_by(MarketSentimentIntraday.captured_at)
    )
    return list((await db.execute(stmt)).scalars().all())


# 东财 EOD 增强：**只**允许写这三列 + ``enrich_source``。判定列（``is_lu`` / ``touched``
# / ``is_zb`` / ``streak`` / ``days_span`` / ``boards_in_window`` / ``missing_days`` /
# ``prev_is_lu``）恒由交易所限价决定，东财池永不覆写（口径冻结，见计划全局约束）。
# 改这条 SQL 时必须同步 `tests/test_limit_up_stock_daily.py` 的 SET 子句守卫。
_ENRICH_SOURCE = "eastmoney_eod"

_ENRICH_SQL = """
UPDATE market_limit_up_stock_daily AS f
SET seal_time = v.seal_time,
    seal_fund = v.seal_fund,
    break_count = v.break_count,
    enrich_source = :enrich_source
FROM unnest(
    CAST(:symbols AS varchar[]),
    CAST(:seal_times AS varchar[]),
    CAST(:seal_funds AS numeric[]),
    CAST(:break_counts AS integer[])
) AS v(symbol, seal_time, seal_fund, break_count)
WHERE f.trade_date = :trade_date AND f.symbol = v.symbol
"""


def build_enrich_sql_for_audit() -> str:
    """给测试用的同源 SQL（守卫"增强只写东财独有列"，避免复制一份会漂移的 SQL）。"""
    return _ENRICH_SQL


async def enrich_limit_up_stock_daily_from_pool(
    db: AsyncSession, as_of: date, rows: list[dict[str, Any]]
) -> int:
    """把东财涨停池的封板时间/封单/炸板次数写进当日事实行（UPDATE-only）。

    ``rows`` 必须是 ``intraday_sentiment_service.build_enriched_rows`` 的输出
    （``symbol`` + 东财三列）。**不 INSERT**：事实行集由交易所限价口径定义，装饰层不得
    造行；池里有、事实表里没有的 symbol 直接忽略（返回的 rowcount 只算真正更新的行）。
    缺失值写 NULL（UPDATE 语义天然如此——池里没给就是 NULL，绝不填 0）。

    单条 `UPDATE ... FROM unnest(...)`（而不是逐行 executemany）：asyncpg 对
    executemany 的 rowcount 恒为 -1，「更新了几行」是本函数的返回值契约（0 命中 =
    A 类数据静默丢失的信号，调用方要据此告警），必须拿得到真数。
    """
    params = [r for r in rows if r.get("symbol")]
    if not params:
        return 0
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            text(_ENRICH_SQL),
            {
                "trade_date": as_of,
                "symbols": [r["symbol"] for r in params],
                "seal_times": [r.get("seal_time") for r in params],
                "seal_funds": [r.get("seal_fund") for r in params],
                "break_counts": [r.get("break_count") for r in params],
                "enrich_source": _ENRICH_SOURCE,
            },
        ),
    )
    await db.flush()
    return int(result.rowcount or 0)
