"""「A股全景」日级派生表的 ORM 镜像（迁移 ``e3f1a7c9d2b4``）。

⚠️ **只有 schema，没有读/写侧**：这三张表的 reader（端点/仓储）与 writer（每日派生任务）
尚未落地，本文件存在的唯一理由是让 ``Base.metadata`` 与迁移保持一致 ——
``migrations/env.py`` 直接吃 ``Base.metadata`` 且没有 ``include_object`` 过滤，
"只在迁移里存在"的表会被 ``alembic revision --autogenerate`` 判成本地多余，
生成 ``drop_table``（下一次 autogenerate 迁移就会把三张表删掉）。

列顺序/类型/可空性/约束/索引与迁移**逐字对应**：任一漂移都会让 autogenerate 漂移
（"表在 ORM 里、列不一样" 同样会生成 add_column/drop_column）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MarketBreadthDaily(Base):
    """市场宽度日级聚合（一天一行）。读/写侧尚未落地，仅 ORM 镜像。"""

    __tablename__ = "market_breadth_daily"
    __table_args__ = (
        UniqueConstraint("trade_date", name="uq_market_breadth_daily_date"),
        Index("ix_market_breadth_daily_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    up_count: Mapped[int] = mapped_column(Integer, nullable=False)
    down_count: Mapped[int] = mapped_column(Integer, nullable=False)
    flat_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 缺失不落 0：无涨停价的早期日子可为 NULL
    limit_up_count: Mapped[int | None] = mapped_column(Integer)
    limit_down_count: Mapped[int | None] = mapped_column(Integer)
    median_pct: Mapped[float | None] = mapped_column(Float)
    mean_pct: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # 千元
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="local_calc", default="local_calc"
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketBreadthBucketDaily(Base):
    """涨跌分布 11 桶的日级明细（一天 11 行）。读/写侧尚未落地，仅 ORM 镜像。"""

    __tablename__ = "market_breadth_bucket_daily"
    __table_args__ = (
        UniqueConstraint("trade_date", "bucket", name="uq_market_breadth_bucket_daily_date_bucket"),
        Index("ix_market_breadth_bucket_daily_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


class MarketIndustryDaily(Base):
    """申万一级行业日级聚合（一天 31 行）。读/写侧尚未落地，仅 ORM 镜像。"""

    __tablename__ = "market_industry_daily"
    __table_args__ = (
        UniqueConstraint("trade_date", "industry_code", name="uq_market_industry_daily_date_code"),
        Index("ix_market_industry_daily_date", "trade_date"),
        Index("ix_market_industry_daily_code_date", "industry_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    industry_code: Mapped[str] = mapped_column(String(16), nullable=False)
    industry_name: Mapped[str] = mapped_column(String(32), nullable=False)
    pct_chg: Mapped[float | None] = mapped_column(Float)
    up_count: Mapped[int] = mapped_column(Integer, nullable=False)
    down_count: Mapped[int] = mapped_column(Integer, nullable=False)
    flat_count: Mapped[int] = mapped_column(Integer, nullable=False)
    limit_up_count: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))  # 千元
    breadth: Mapped[float | None] = mapped_column(Float)  # 上涨家数 / 有行情家数
    # C 类归属：当前申万快照回溯
    attribution: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="current_sw_snapshot",
        default="current_sw_snapshot",
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="local_calc", default="local_calc"
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
