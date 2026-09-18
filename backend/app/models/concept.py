"""概念板块与成分股 ORM models（东财口径）。

业务键取 ``symbol`` 而非 ``stock_id``：``stocks`` 名录会滞后（实测冻结在 2026-05-08，
66 只新上市股票缺行），若以 stock_id 为键，名录滞后会静默丢掉成分股；此处把
``stock_id`` 降级为可空解析列，缺失以 ``unresolved_count`` 对外暴露。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConceptBoard(Base):
    """概念板块名录（东财 clist 口径），member_count 为最近一次采集的成分股数。"""

    __tablename__ = "concept_boards"
    __table_args__ = (UniqueConstraint("board_code", name="uq_concept_boards_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    board_name: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="em_clist")
    member_count: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class ConceptMember(Base):
    """概念板块成分股当前态（按 board_code + symbol upsert 覆盖）。

    stock_id 可空：名录未命中新上市股票时留空，不阻塞成分采集。
    """

    __tablename__ = "concept_members"
    __table_args__ = (
        UniqueConstraint("board_code", "symbol", name="uq_concept_member_board_symbol"),
        Index("idx_concept_members_symbol", "symbol"),
        Index("idx_concept_members_stock_id", "stock_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_id: Mapped[int | None] = mapped_column(Integer)
    first_seen_on: Mapped[date] = mapped_column(Date, nullable=False)
    last_seen_on: Mapped[date] = mapped_column(Date, nullable=False)


class ConceptMemberChange(Base):
    """成分变更差分（追加式，永不更新）：历史口径从启用之日开始积累。"""

    __tablename__ = "concept_member_changes"
    __table_args__ = (
        UniqueConstraint(
            "observed_on",
            "board_code",
            "symbol",
            "change_type",
            name="uq_concept_change_row",
        ),
        Index("idx_concept_changes_board_date", "board_code", "observed_on"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(32))
    change_type: Mapped[str] = mapped_column(String(8), nullable=False)
