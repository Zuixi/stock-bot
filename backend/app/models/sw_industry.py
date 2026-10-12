"""Shenwan (申万) industry classification ORM models."""

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# ---------------------------------------------------------------------------
# Official SW classification
# ---------------------------------------------------------------------------


class SwIndustryClass(Base):
    """Three-level Shenwan industry classification tree node."""

    __tablename__ = "sw_industry_classes"
    __table_args__ = (
        UniqueConstraint("industry_code", name="uq_sw_class_code"),
        Index("idx_sw_class_level", "level"),
        Index("idx_sw_class_parent", "parent_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    level: Mapped[int] = mapped_column(nullable=False)
    industry_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SwIndustryMember(Base):
    """**当前态快照**：A 股股票 → 申万行业代码。

    ⚠️ 本表**没有历史**：命名 XLS 快照导入时整表 DELETE + INSERT（
    ``sw_industry_service.import_sw_members``），且唯一键是
    ``(industry_code, stock_code)``，同一只股票与同一行业只可能有一行——
    它**不能**用来还原"任一历史交易日的成分"。

    历史分类归属走 :class:`SwIndustryMemberPeriod`（SCD2）。本表保留的原因：
    ① 猪智投/板块等既有读路（``market_service`` 的 SW 性能与下钻）在无历史语境时
    只需当前态；② ``data/sw_seed.sql`` 的整表替换式导入契约未变（Ruling 30：不删既有能力）。

    各 level 的成员都在本表内：L1/L2/L3 的 ``industry_code`` 各占一批行
    （上游 ``index_member`` 对祖级指数同样返回全部成分），故 L1/L2 的成员
    既可直接取本表、也可由 L3 沿 ``parent_code`` 上卷。
    """

    __tablename__ = "sw_industry_members"
    __table_args__ = (
        UniqueConstraint("industry_code", "stock_code", name="uq_sw_member_code_stock"),
        Index("idx_sw_member_industry", "industry_code"),
        Index("idx_sw_member_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SwIndustryMemberPeriod(Base):
    """**SCD2** 申万行业成分归属（逐期生效区间）。

    与 :class:`SwIndustryMember` 的分工：本表是历史分类归属的**唯一真相**，
    每次 ingest 只做 INSERT / UPDATE（绝不整表 DELETE），故历史可累积。

    列语义（全部来自上游 TuShare ``index_member``，不是本地推算）::

        in_date   → effective_from  必填；区间闭端（当日起生效）
        out_date  → effective_to    可空；``NULL`` = 至今仍在此行业
        is_new    → is_current      上游"当前成分"标记，与 ``effective_to IS NULL`` 同义

    **SCD2 的下界与上界（诚实声明）**：

    * 下界 = 上游历史的起点。Tushare ``index_member`` 实测可回溯到 1990 年代
      （样例 ``in_date`` 最早 19930917），故本表**能**还原上市以来的历史归属；
      但**只能还原上游记录过的期间**——本地从未记录过的历史无法回填。
    * 上界 = 最后一次 **ingest 运行日**。此后新建/变更的成分要等下一次 ingest
      才出现；``effective_to IS NULL`` 的语义因此是"**截至最近一次 ingest 仍为成分**"，
      而不是"截至查询当日必然是成分"。任何对外展示必须带 ``as_of``（= 最近一次成功的
      ingest 日期，见 ``SwMemberIngestRun``）与上述口径。
    * 本表**不含**指数历史权重、也不含退市股补齐——上游没给的字段一律不编。

    唯一键 ``(industry_code, stock_code, effective_from)``：同一只股票可以在
    不同区间反复进出同一行业（实测 ``000034.SZ`` 在 801010.SI 有 3 段），
    但同一区间起点不会重复。唯一键**不含** ``effective_to``：区间终点由上游
    修订时（罕见）就地 UPDATE 覆盖同一行。
    """

    __tablename__ = "sw_industry_member_periods"
    __table_args__ = (
        UniqueConstraint(
            "industry_code",
            "stock_code",
            "effective_from",
            name="uq_sw_member_period_code_stock_from",
        ),
        Index("idx_sw_member_period_industry_from", "industry_code", "effective_from"),
        Index("idx_sw_member_period_symbol_from", "symbol", "effective_from"),
        Index("idx_sw_member_period_open", "industry_code", "effective_to"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(64))
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="tushare:index_member",
        server_default="tushare:index_member",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SwMemberIngestRun(Base):
    """SCD2 成分 ingest 的运行记录（**读面用来声明 as_of 上界**）。

    没有它，前端无法区分"这只股票今天真的不在某行业"与"我们最后一次采集是三个月前"。
    ``as_of`` = 最近一次成功运行的 ``trade_date``（ingest 所取的上游快照日）。
    """

    __tablename__ = "sw_member_ingest_runs"
    __table_args__ = (Index("idx_sw_member_ingest_started", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    as_of: Mapped[date | None] = mapped_column(Date)
    index_codes: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    rows_upserted: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ok", server_default="ok"
    )
    detail: Mapped[str | None] = mapped_column(String(255))


# ---------------------------------------------------------------------------
# User-defined custom SW tags per stock
# ---------------------------------------------------------------------------


class StockCustomSwTag(Base):
    """User-assigned SW L2/L3 industry tags for a stock (many-to-many)."""

    __tablename__ = "stock_custom_sw_tags"
    __table_args__ = (
        UniqueConstraint("symbol", "industry_code", name="uq_custom_sw_tag"),
        Index("idx_custom_sw_tag_symbol", "symbol"),
        Index("idx_custom_sw_tag_code", "industry_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    industry_code: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
