"""Market-data face models: sector moneyflow / dragon tiger / northbound /
block trades / share float / repurchase / announcements."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Integer,
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
