"""决策日志（spec §S9.2）：**两张表、append-only、创建后不可篡改**。

- ``decision_journal_entries``：一次判断（3 行必填）+ **创建时固化的市场快照**
  （``snapshot`` jsonb，内含 ``bench_spec``：基准口径，spec §S9.7.3）+ **固化的到期日**
  （``due_on``，写入时算好，便于索引）+ ``bet_type``（``direction|relative|market``，
  决定基准，落成列而非只存 jsonb：聚合与过滤要能走索引/约束）+ ``position_size``
  （``none|light|normal|heavy``）与可空 ``confidence``（1..5，spec §S9.7.1/§S9.7.5：
  收益要能按风险单位解释、信心要能做校准，两者都必须是**列**而不是 jsonb 里的自由字段）。
- ``decision_journal_reviews``：一次复盘（结果四数 + 五类归因），``unique(entry_id)``
  保证**一条判断只有一次复盘**——第二次写入报冲突，而不是静默覆盖。

为什么把 ``due_on`` 落成列而不是每次 ``created_at + horizon_days`` 现算：
① 列表页按 ``(user_id, due_on)`` 走索引取"待复盘"，现算列无法建索引；
② "什么时候到期"是**写入时的约定**，不是渲染参数——口径（交易日/自然日）
   将来若变，历史条目仍按当时约定解释（与 S1.9 的 PIT 同一纪律）。

``invalidation`` 的三种形状（jsonb，DB 只校验键存在，值的语义由
``app/schemas/journal.py`` 的入参模型钉死）::

    {"kind": "price_below", "value": 38.0}
    {"kind": "price_above", "value": 45.0}
    {"kind": "date_only",   "value": "2026-12-31"}

这三种形态是 spec §S9.7.8 的**可证伪闸**：只有能被行情/日期客观判定的失效条件才算
"可观测"（``OBSERVABLE_INVALIDATION_KINDS``），自由文本（"逻辑破坏"）在入参层就是 422；
历史行里若已经存在不可识别的形态，读取时按 ``reviewable = false`` 处理（不给复盘入口），
**不改写历史数据**。

**没有 UPDATE/DELETE 的代码路径**：路由层不提供 PATCH/PUT/DELETE（
``tests/test_journal_api.py`` 用 grep 断言），复查/更正靠新增一条判断，不靠改历史。
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

#: ``horizon_days`` 的三枚举（spec §S9.2）；与 ``app/schemas/journal.py`` 由测试对拍。
HORIZON_CHOICES: tuple[int, ...] = (7, 30, 90)
#: ``kind`` 的五枚举（spec §S9.1）。Phase A 起 ``pass`` 是**一等公民**：
#: 它**计入判断统计**（``stats.n`` / ``by_kind``）、**不计入收益统计**
#: （``avg_excess_pct`` / ``avg_net_excess_pct``），见 ``journal_service.stats``。
ENTRY_KINDS: tuple[str, ...] = ("watch", "buy", "sell", "pass", "hold")
#: ``bet_type`` 的三枚举（spec §S9.7.3）：决定基准口径，创建时选定后**不可改**。
#: 与 ``app/schemas/journal.py`` 的 ``BetType`` 由测试对拍。
BET_TYPES: tuple[str, ...] = ("direction", "relative", "market")
#: ``position_size`` 的四枚举（spec §S9.7.1）：风险单位分档。
#: 权重 ``{none: 0, light: 0.5, normal: 1, heavy: 2}`` 只在统计层使用（Phase B2），
#: 这里只负责把"哪一档"落下；``none`` = 只观察不下单（不计收益统计，但仍算判断）。
#: 与 ``app/schemas/journal.py`` 的 ``PositionSize`` 由测试对拍。
POSITION_SIZES: tuple[str, ...] = ("none", "light", "normal", "heavy")
#: ``confidence`` 的取值范围（spec §S9.7.5）：1..5 的整数，可空（缺省 = 不参与校准）。
#: 概率映射 ``{1: 0.2, 2: 0.35, 3: 0.5, 4: 0.65, 5: 0.8}`` 固定在服务层常量里。
CONFIDENCE_VALUES: tuple[int, ...] = (1, 2, 3, 4, 5)
#: ``invalidation`` 的**可观测**三形态（spec §S9.7.8）：可证伪闸的唯一白名单。
#: 别的 ``kind``（含历史行里的自由文本形态）一律 ``invalidation_observable = false``。
#: 与 ``app/schemas/journal.py`` 的 ``INVALIDATION_KINDS`` 由测试对拍。
OBSERVABLE_INVALIDATION_KINDS: tuple[str, ...] = ("price_below", "price_above", "date_only")
#: ``verdict`` 的五枚举（spec §S9.2）。``right_for_wrong_reason`` 单列：
#: 避免"赚了就是对的"，让"运气"与"逻辑对但结论错"分开计。
VERDICTS: tuple[str, ...] = (
    "thesis_wrong",
    "timing_wrong",
    "discipline_break",
    "right_for_wrong_reason",
    "luck",
)

#: DDL 逐字照 spec §S9.2（约束名是本仓库的命名，谓词逐字）。
_THESIS_LEN_CHECK = CheckConstraint(
    "char_length(thesis) between 1 and 200", name="ck_journal_thesis_len"
)
_HORIZON_CHECK = CheckConstraint("horizon_days in (7, 30, 90)", name="ck_journal_horizon")
_INVALIDATION_CHECK = CheckConstraint(
    "invalidation ? 'kind' and invalidation ? 'value'", name="ck_journal_invalidation_keys"
)
_KIND_CHECK = CheckConstraint(
    "kind in ('watch','buy','sell','pass','hold')", name="ck_journal_kind"
)
#: spec §S9.7.3：``bet_type`` 越界由 API 422 拦（pydantic Literal），DB 再兜一道
_BET_TYPE_CHECK = CheckConstraint(
    "bet_type in ('direction','relative','market')", name="ck_journal_bet_type"
)
#: spec §S9.7.1：``position_size`` 四枚举（越界 422 在 API，库里是最后一道闸）
_POSITION_SIZE_CHECK = CheckConstraint(
    "position_size in ('none','light','normal','heavy')", name="ck_journal_position_size"
)
#: spec §S9.7.5：``confidence`` 可空，但非空时必须在 1..5（``NULL`` 不参与校准）
_CONFIDENCE_CHECK = CheckConstraint("confidence between 1 and 5", name="ck_journal_confidence")
_VERDICT_CHECK = CheckConstraint(
    "verdict in ('thesis_wrong','timing_wrong','discipline_break','right_for_wrong_reason','luck')",
    name="ck_journal_verdict",
)


class DecisionJournalEntry(Base):
    """一条用户判断（不可改：``thesis/invalidation/horizon_days/symbol`` 创建即冻结）。"""

    __tablename__ = "decision_journal_entries"
    __table_args__ = (
        _THESIS_LEN_CHECK,
        _HORIZON_CHECK,
        _INVALIDATION_CHECK,
        _KIND_CHECK,
        _BET_TYPE_CHECK,
        _POSITION_SIZE_CHECK,
        _CONFIDENCE_CHECK,
        # "待复盘 N 条"与 `status=due` 列表都走这条：user_id + due_on
        Index("idx_journal_entries_user_due", "user_id", "due_on"),
        # 列表默认序（最近写的在前）；spec 要求 created_at 降序
        Index("idx_journal_entries_user_created", "user_id", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: NULL = 市场级判断（结果基准退化为沪深300，见 ``journal_service.evaluate_entry``）
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="watch")
    #: 赌的是什么（spec §S9.7.3）：``direction``（默认）/ ``relative`` / ``market``。
    #: 决定基准口径，创建时写入快照 ``bench_spec``，**事后不可改**。
    bet_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="direction", default="direction"
    )
    #: 风险单位（spec §S9.7.1）：``none`` 默认 = 只观察不下单；创建时选定、不可改。
    #: 落列而非快照字段：``by_size`` 分栏与加权统计要能聚合/过滤。
    position_size: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="none", default="none"
    )
    #: 事前信心（spec §S9.7.5）：1..5，**可空**（NULL = 没填，不参与校准）；
    #: 不设默认值：给一个"中等信心"的默认会把没填的人算进校准样本里。
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thesis: Mapped[str] = mapped_column(Text, nullable=False)
    invalidation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 创建时的市场快照（spec §S9.3）；**不可变**：之后不随行情变化
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: = ``created_at::date + horizon_days``（写入时固化）
    due_on: Mapped[date] = mapped_column(Date, nullable=False)


class DecisionJournalReview(Base):
    """一次复盘（append-only）：结果四数 + 归因五选一 + 可选 note。

    ``unique(entry_id)`` 是**唯一的**"已复盘"判据：不设 ``entries.reviewed_at`` 之类的
    回写列，避免"两处状态漂移"（一条判断是否已复盘，只看本表有没有行）。
    """

    __tablename__ = "decision_journal_reviews"
    __table_args__ = (
        UniqueConstraint("entry_id", name="uq_journal_review_entry"),
        _VERDICT_CHECK,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("decision_journal_entries.id"), nullable=False
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: 绝对收益 %（``snapshot.price`` → ``due_on`` 最近收盘）
    outcome_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    #: 基准 %（沪深300 或所属申万一级平均）
    bench_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    #: ``outcome_pct - bench_pct``
    excess_pct: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    invalidation_hit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
