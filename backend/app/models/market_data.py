"""Market-data face models: sector moneyflow / dragon tiger / northbound /
block trades / share float / repurchase / announcements."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SectorMoneyflowSnapshot(Base):
    """东财板块主力资金流当日快照（盘中每 5 分钟 upsert 覆盖，跨日保留）。金额单位：元。"""

    __tablename__ = "sector_moneyflow_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "dimension", "board_code", name="uq_sector_moneyflow_dim_code_date"
        ),
        # Created by migration d7c8b9a0e1f2 (per-dimension max(trade_date) lookup);
        # mirrored here so `alembic revision --autogenerate` does not emit drop_index.
        # `ix_sector_moneyflow_date_dim` (migration 9d4e7a2c8b1f) predates the
        # convention and is still unmirrored — pre-existing drift, not this task's.
        Index("ix_sector_moneyflow_dim_date", "dimension", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)  # industry|concept|region
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    board_name: Mapped[str | None] = mapped_column(String(32))
    pct_change: Mapped[float | None] = mapped_column(Float)
    main_net_inflow: Mapped[float | None] = mapped_column(Float)  # 元
    super_large_net: Mapped[float | None] = mapped_column(Float)  # 元
    large_net: Mapped[float | None] = mapped_column(Float)  # 元
    main_net_ratio: Mapped[float | None] = mapped_column(Float)  # %
    up_count: Mapped[int | None] = mapped_column(Integer)
    down_count: Mapped[int | None] = mapped_column(Integer)
    # data.eastmoney.com/bkzj/ 排行页同款"主力净流入最大股"
    lead_stock_name: Mapped[str | None] = mapped_column(String(32))
    lead_stock_code: Mapped[str | None] = mapped_column(String(12))
    lead_stock_pct: Mapped[float | None] = mapped_column(Float)  # %
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarketMoneyflowDaily(Base):
    """沪深两市大盘资金流日线（东财 fflow/daykline，沪深双 secid 服务端合成口径）。

    五档恒等式：主力 = 超大单 + 大单；主力+中单+小单 = 0。金额单位：元。
    """

    __tablename__ = "market_moneyflow_daily"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_market_moneyflow_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    main_net: Mapped[float | None] = mapped_column(Float)  # 元
    super_large_net: Mapped[float | None] = mapped_column(Float)  # 元
    large_net: Mapped[float | None] = mapped_column(Float)  # 元
    mid_net: Mapped[float | None] = mapped_column(Float)  # 元
    small_net: Mapped[float | None] = mapped_column(Float)  # 元
    main_ratio: Mapped[float | None] = mapped_column(Float)  # %
    close: Mapped[float | None] = mapped_column(Float)  # 上证收盘点位
    pct_change: Mapped[float | None] = mapped_column(Float)  # %
    amount: Mapped[float | None] = mapped_column(Float)  # 成交额，元（源为亿元，×1e8）
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="em:fflow_daykline")


class DragonTigerEntry(Base):
    """龙虎榜个股明细（TuShare top_list）。金额单位：元。"""

    __tablename__ = "dragon_tiger_entries"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "ts_code", "reason", name="uq_dragon_tiger_date_code_reason"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(32))
    close: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    l_buy: Mapped[float | None] = mapped_column(Float)
    l_sell: Mapped[float | None] = mapped_column(Float)
    l_amount: Mapped[float | None] = mapped_column(Float)
    net_amount: Mapped[float | None] = mapped_column(Float)
    net_rate: Mapped[float | None] = mapped_column(Float)
    amount_rate: Mapped[float | None] = mapped_column(Float)
    float_values: Mapped[float | None] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(160), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:top_list")


class NorthboundDaily(Base):
    """北向资金每日净流入（TuShare moneyflow_hsgt，盘后）。单位：万元。"""

    __tablename__ = "northbound_daily"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_northbound_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    net_amount: Mapped[float | None] = mapped_column(Float)  # 万元
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="tushare:moneyflow_hsgt"
    )


class BlockTrade(Base):
    """大宗交易（TuShare block_trade）。price 元 / volume 万股 / amount 万元。"""

    __tablename__ = "block_trades"
    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "ts_code",
            "buyer",
            "seller",
            "price",
            "volume",
            name="uq_block_trades_dedupe",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)  # 万股
    amount: Mapped[float | None] = mapped_column(Float)  # 万元
    buyer: Mapped[str | None] = mapped_column(Text)
    seller: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:block_trade")


class ShareFloat(Base):
    """限售解禁（TuShare share_float）。float_share 万股 / float_ratio %。"""

    __tablename__ = "share_floats"
    __table_args__ = (
        UniqueConstraint(
            "ann_date", "ts_code", "holder_name", "share_type", name="uq_share_floats_dedupe"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ann_date: Mapped[date | None] = mapped_column(Date)
    float_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    float_share: Mapped[float | None] = mapped_column(Float)  # 万股
    float_ratio: Mapped[float | None] = mapped_column(Float)  # %
    holder_name: Mapped[str | None] = mapped_column(Text)
    share_type: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:share_float")


class StockRepurchase(Base):
    """股票回购（TuShare repurchase）。vol 股 / amount 元。"""

    __tablename__ = "stock_repurchases"
    __table_args__ = (
        UniqueConstraint("ann_date", "ts_code", "proc", name="uq_stock_repurchases_dedupe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ann_date: Mapped[date] = mapped_column(Date, nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    proc: Mapped[str] = mapped_column(String(16), nullable=False)  # 实施/完成/...
    exp_date: Mapped[date | None] = mapped_column(Date)
    vol: Mapped[float | None] = mapped_column(Float)  # 股
    amount: Mapped[float | None] = mapped_column(Float)  # 元
    high_limit: Mapped[float | None] = mapped_column(Float)
    low_limit: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="tushare:repurchase")


class Announcement(Base):
    """公告快讯（巨潮 cninfo，财报+重大事项两类）。"""

    __tablename__ = "announcements"
    __table_args__ = (UniqueConstraint("announcement_id", name="uq_announcements_cninfo_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    announcement_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sec_code: Mapped[str] = mapped_column(String(12), nullable=False)
    sec_name: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    announce_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)  # report | event
    pdf_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StockPriceLimit(Base):
    """交易所口径的每日涨跌停价（TuShare stk_limit），连板判定的唯一权威基准。

    为什么不用「名称含 ST + 代码前缀推比例」：2026-09-08 实测权威 up_limit 判出 75 只涨停，
    名称启发式判出 83 只，10 处分歧里 9 只是 ST 名称股（当日真实限幅 10%，如 ST晨鸣
    up_limit=2.13 / pre_close=1.94）。stocks.name 是当日快照而非历史名称，历史回放必错。
    pre_close 由 stk_limit 原生提供（交易所口径、已含除权调整；**需客户端显式传 fields**），
    禁止用 LAG(close) 现算。注意语义：该行 pre_close 是「本交易日的前收」，
    要算某日涨跌幅就得取**该日行**的 pre_close。

    建键说明：用 stock_id 而非 ts_code——stocks 表没有 ts_code 列（只在 detail JSONB 里），
    JSONB join 需函数索引且每次读都要过 stocks。不额外建索引：唯一键已服务日筛，窗口侧走
    hash join（实测单日 5,499 行 hash 2.6ms / 317kB），加第二个索引是纯写放大。
    """

    __tablename__ = "stock_price_limits"
    __table_args__ = (UniqueConstraint("trade_date", "stock_id", name="uq_price_limit_date_stock"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    stock_id: Mapped[int] = mapped_column(nullable=False)
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)  # 溯源用
    pre_close: Mapped[float | None] = mapped_column(Numeric(12, 4))
    up_limit: Mapped[float | None] = mapped_column(Numeric(12, 4))
    down_limit: Mapped[float | None] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketSentimentDaily(Base):
    """短线情绪周期聚合（约 10 个数字/交易日）。

    纯派生缓存：可随时由 daily_quotes + stock_price_limits 重建，绝不是真相来源。
    存在的唯一理由是跨月时序图重算昂贵——逐日明细不落表，因为 daily_quotes 本身就是历史。
    """

    __tablename__ = "market_sentiment_daily"
    __table_args__ = (UniqueConstraint("trade_date", name="uq_sentiment_daily_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    zt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    zb_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    broken_rate: Mapped[float | None] = mapped_column(Float)
    yzt_avg_pct: Mapped[float | None] = mapped_column(Float)
    promo_1to2: Mapped[float | None] = mapped_column(Float)
    promo_1to2_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    promo_2to3: Mapped[float | None] = mapped_column(Float)
    promo_2to3_n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_streak_symbol: Mapped[str | None] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="local_calc")


class MarketSentimentIntraday(Base):
    """盘中分时序列缓存（Task 12）。

    派生缓存：每 5 分钟 ``intraday_sentiment_poll`` 抓一次东财涨停池，池行数+max_streak
    入同一 (trade_date, captured_at) 行；端点按 ``captured_at`` 升序取。
    与 ``MarketSentimentDaily`` 并列——本表是盘中节拍、那张是日终聚合；都不是真相，
    真源是 daily_quotes + 实时东财池。
    """

    __tablename__ = "market_sentiment_intraday"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "captured_at", name="uq_market_sentiment_intraday_date_captured"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    zb_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
