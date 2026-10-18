"""spec §S9.7 Phase B1 —— **仓位 / 信心 / 可证伪闸**（真 PG + 纯函数 + 迁移静态检查）。

这一组钉 Phase B1 的三件事，每条对应 spec 的一句验收判据：

1. **仓位**（§S9.7.1）：``position_size`` 落列，``NOT NULL DEFAULT 'none'`` + 四枚举 check；
   越界由 API 422 拦（``tests/test_journal_api.py``），库是最后一道闸（本文件用真 PG 断言）。
2. **信心**（§S9.7.5）：``confidence`` 可空、非空时 1..5；``NULL`` 的语义是"没填"，
   **不参与校准**（不设默认值，避免把没填的人算进校准样本）。
3. **可证伪闸**（§S9.7.8）：``invalidation`` 只有三形态可观测 → ``invalidation_observable`` /
   ``reviewable = true``；历史行里的自由文本形态 → ``false``，且**复盘端点拒绝**（本文件
   在服务层断言 ``NotReviewable``，端点的 422 在 ``test_journal_api.py``）。

合成数据刻意用库里不存在的代码（``T980xx``）与未来日期（2026-10），所有写入包在**回滚**
事务里（Ruling 17：跑完库与跑前一致）；库不可达时 skip 而非红。
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, get_args

import asyncpg
import pytest
from pydantic import ValidationError
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.index_daily import IndexDaily
from app.models.journal import (
    OBSERVABLE_INVALIDATION_KINDS,
    POSITION_SIZES,
    DecisionJournalEntry,
)
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.schemas.journal import (
    INVALIDATION_GUIDANCE,
    INVALIDATION_KINDS,
    JournalEntryCreate,
    PositionSize,
)
from app.services import journal_service as svc

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

AS_OF = date(2026, 10, 16)
DUE_ON = date(2026, 10, 21)
MAIN = "T98001"
CLOSE_START, CLOSE_END = 100.0, 110.0
HS300_START, HS300_END = 4000.0, 4100.0


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


_PG_OK = _pg_reachable()

requires_pg = pytest.mark.skipif(
    not _PG_OK, reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


# ---------------------------------------------------------------------------
# 夹具（回滚事务；库不可达时用例自己 skip）
# ---------------------------------------------------------------------------


async def _with_rollback(factory: Any) -> Any:
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await factory(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    """最小底座：1 只股票 + 两根行情（100 → 110，区间低 95/高 115）+ 沪深300 两根。"""
    await session.execute(
        insert(Stock),
        [
            {
                "exchange": "Shanghai_Stocks",
                "symbol": MAIN,
                "name": f"测试{MAIN}",
                "category": "A股",
                "asof": datetime(2026, 10, 16, 18, 0),
            }
        ],
    )
    stock_id = int(
        (await session.execute(select(Stock.id).where(Stock.symbol == MAIN))).scalar_one()
    )
    await session.execute(
        insert(DailyQuote),
        [
            {
                "stock_id": stock_id,
                "trade_date": AS_OF,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "pre_close": 100.0,
                "amount": 1_000_000.0,
            },
            {
                "stock_id": stock_id,
                "trade_date": DUE_ON,
                "open": 105.0,
                "high": 115.0,
                "low": 95.0,
                "close": 110.0,
                "pre_close": 100.0,
                "amount": 2_000_000.0,
            },
        ],
    )
    await session.execute(
        insert(IndexDaily),
        [
            {"ts_code": svc.HS300_TS_CODE, "trade_date": AS_OF, "close": HS300_START},
            {"ts_code": svc.HS300_TS_CODE, "trade_date": DUE_ON, "close": HS300_END},
        ],
    )
    await session.flush()


def _snapshot() -> dict[str, Any]:
    """创建时的快照（s9.r1 形状：没有 bench_spec → 按列上的 bet_type 兜底）。"""
    return {
        "version": "s9.r1",
        "as_of": AS_OF.isoformat(),
        "quote_as_of": AS_OF.isoformat(),
        "symbol": MAIN,
        "price": CLOSE_START,
        "sector_flow_state": None,
        "missing_reason": {},
    }


def _entry(**overrides: Any) -> DecisionJournalEntry:
    payload: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "symbol": MAIN,
        "kind": "watch",
        "bet_type": "direction",
        "position_size": "none",
        "thesis": "猪价见底，产能去化加速",
        "invalidation": {"kind": "price_below", "value": 50.0},
        "horizon_days": 7,
        "snapshot": _snapshot(),
        "created_at": datetime(2026, 10, 16, 15, 0),
        "due_on": DUE_ON,
    }
    payload.update(overrides)
    return DecisionJournalEntry(**payload)


# ---------------------------------------------------------------------------
# §S9.7.8 可证伪闸：三形态 = 可观测；其它形态 = 不可观测（纯函数，不连库）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalidation",
    [
        {"kind": "price_below", "value": 38.0},
        {"kind": "price_above", "value": 45.0},
        {"kind": "date_only", "value": "2026-12-31"},
        # jsonb 里数值可能是字符串（历史脚本写入）：只要能被判定就仍算可观测
        {"kind": "price_below", "value": "38.5"},
    ],
)
def test_three_observable_forms_are_true(invalidation: dict[str, Any]) -> None:
    """spec §S9.7.8：``price_below`` / ``price_above`` / ``date_only`` 全部可观测。"""
    assert svc.invalidation_observable(invalidation) is True


@pytest.mark.parametrize(
    "invalidation",
    [
        # 自由文本（spec 逐字举的例子："逻辑破坏"）——历史行里可能真的存在
        {"kind": "logic_broken", "value": "逻辑破坏"},
        {"kind": "text", "value": "跌穿成本就撤"},
        # 形态对但值不可判定：算不出 invalidation_hit，就不该允许"复盘"
        {"kind": "price_below", "value": "跌穿成本"},
        {"kind": "price_below", "value": 0},
        {"kind": "price_below", "value": -1},
        {"kind": "price_below", "value": None},
        {"kind": "date_only", "value": "某天"},
        # 缺 kind / 不是 dict / None
        {"value": 38.0},
        {},
        None,
    ],
)
def test_unobservable_forms_are_false(invalidation: dict[str, Any] | None) -> None:
    """不可识别 → ``False``（不是"默认可复盘"）：读面标 ``reviewable=false``。"""
    assert svc.invalidation_observable(invalidation) is False


def test_observable_kind_whitelist_is_the_single_source() -> None:
    """模型/schema 的白名单逐字一致（漂移 = 422 只拦一半、reviewable 判错）。"""
    assert OBSERVABLE_INVALIDATION_KINDS == ("price_below", "price_above", "date_only")
    assert INVALIDATION_KINDS == OBSERVABLE_INVALIDATION_KINDS
    assert tuple(get_args(PositionSize)) == POSITION_SIZES == ("none", "light", "normal", "heavy")


def test_schema_rejects_free_text_invalidation_with_guidance() -> None:
    """spec §S9.7.8：自由文本失效条件 → **422 语义的校验失败 + 指导文案**。

    这里直接调 pydantic（不起 HTTP），断言错误信息里带着"怎么改"（价格/日期/备注），
    而不是 pydantic 默认的 ``union_tag_invalid``——用户看不懂的报错等于没有指导。
    """
    with pytest.raises(ValidationError) as exc:
        JournalEntryCreate(
            symbol=MAIN,
            thesis="猪价见底",
            invalidation={"kind": "logic_broken", "value": "逻辑破坏"},
            horizon_days=7,
        )
    assert "price_below" in str(exc.value)
    assert "价格或日期" in str(exc.value)
    assert INVALIDATION_GUIDANCE in str(exc.value)


def test_schema_accepts_the_three_forms_and_boundaries() -> None:
    """三形态都收；``position_size`` 四档都收；``confidence`` 1/5 收、0/6 拒。"""
    for invalidation in (
        {"kind": "price_below", "value": 38.0},
        {"kind": "price_above", "value": 45.0},
        {"kind": "date_only", "value": "2026-12-31"},
    ):
        payload = JournalEntryCreate(
            symbol=MAIN, thesis="猪价见底", invalidation=invalidation, horizon_days=7
        )
        assert payload.position_size == "none", "仓位缺省 = none（只观察）"
        assert payload.confidence is None, "信心缺省 = 没填（不参与校准，不是 3 分）"
    for size in POSITION_SIZES:
        payload = JournalEntryCreate(
            symbol=MAIN,
            thesis="猪价见底",
            invalidation={"kind": "price_below", "value": 38.0},
            horizon_days=7,
            position_size=size,
            confidence=3,
        )
        assert payload.position_size == size
    for confidence in (1, 5):
        payload = JournalEntryCreate(
            symbol=MAIN,
            thesis="猪价见底",
            invalidation={"kind": "price_below", "value": 38.0},
            horizon_days=7,
            confidence=confidence,
        )
        assert payload.confidence == confidence
    for bad_confidence in (0, 6):
        with pytest.raises(ValidationError):
            JournalEntryCreate(
                symbol=MAIN,
                thesis="猪价见底",
                invalidation={"kind": "price_below", "value": 38.0},
                horizon_days=7,
                confidence=bad_confidence,
            )


# ---------------------------------------------------------------------------
# §S9.7.1/§S9.7.5 库是最后一道闸（真 PG）
# ---------------------------------------------------------------------------


async def _rejects(session: AsyncSession, obj: Any) -> str:
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            session.add(obj)
            await session.flush()
    return str(exc.value)


@requires_pg
async def test_db_rejects_position_size_outside_enum() -> None:
    async def _go(session: AsyncSession) -> str:
        return await _rejects(session, _entry(position_size="half"))

    message = await _with_rollback(_go)
    assert "ck_journal_position_size" in message


@requires_pg
async def test_db_rejects_confidence_outside_1_to_5() -> None:
    async def _go(session: AsyncSession) -> tuple[str, str]:
        below = await _rejects(session, _entry(confidence=0))
        above = await _rejects(session, _entry(confidence=6))
        return below, above

    below, above = await _with_rollback(_go)
    assert "ck_journal_confidence" in below
    assert "ck_journal_confidence" in above


@requires_pg
async def test_position_size_defaults_to_none_and_confidence_defaults_to_null() -> None:
    """``position_size`` 不传 → ``none``（server_default 回填）；``confidence`` 不传 → NULL。"""

    async def _go(session: AsyncSession) -> tuple[str, int | None]:
        entry = DecisionJournalEntry(
            user_id=uuid.uuid4(),
            symbol=None,
            kind="watch",
            thesis="只观察不动",
            invalidation={"kind": "date_only", "value": "2026-12-31"},
            horizon_days=30,
            snapshot=_snapshot(),
            created_at=datetime(2026, 10, 16, 15, 0),
            due_on=DUE_ON,
        )
        session.add(entry)
        await session.flush()
        await session.refresh(entry)
        return entry.position_size, entry.confidence

    position_size, confidence = await _with_rollback(_go)
    assert position_size == "none"
    assert confidence is None, "没填信心 → NULL（不是 3），否则校准样本会被默认值污染"


# ---------------------------------------------------------------------------
# §S9.7.8 集成：三形态 evaluate → invalidation_observable = true；历史自由文本 → false 且不可复盘
# ---------------------------------------------------------------------------


@requires_pg
async def test_evaluation_reports_invalidation_observable_true_for_all_three_forms() -> None:
    """``evaluation.invalidation_observable`` 进结果，且三形态都能算出 ``invalidation_hit``。"""

    async def _go(session: AsyncSession) -> dict[str, dict[str, Any]]:
        await _seed(session)
        out: dict[str, dict[str, Any]] = {}
        cases = {
            "price_below": {"kind": "price_below", "value": 96.0},  # 区间低 95 ≤ 96 → 命中
            "price_above": {"kind": "price_above", "value": 114.0},  # 区间高 115 ≥ 114 → 命中
            "date_only": {"kind": "date_only", "value": DUE_ON.isoformat()},
        }
        for name, invalidation in cases.items():
            entry = _entry(invalidation=invalidation)
            session.add(entry)
            await session.flush()
            out[name] = await svc.evaluate_entry(session, entry, as_of=DUE_ON)
        return out

    results = await _with_rollback(_go)
    for name, result in results.items():
        assert result["invalidation_observable"] is True, f"{name} 应该可观测"
        assert result["invalidation_hit"] is True, f"{name} 的 invalidation_hit 必须算得出来"
    assert results["price_below"]["missing_reason"].get("invalidation_hit") is None
    assert results["date_only"]["invalidation_hit"] is True  # 到期即命中


@requires_pg
async def test_legacy_free_text_invalidation_is_unobservable_and_review_is_rejected() -> None:
    """历史行的自由文本形态：``reviewable=False``，服务层复盘 → ``NotReviewable``（端点 422）。

    **不改写历史数据**：只读判断（``invalidation`` 原样留在库里），拒绝的是"归因"这个动作。
    """

    async def _go(session: AsyncSession) -> tuple[bool, str, dict[str, Any], dict[str, Any]]:
        await _seed(session)
        entry = _entry(
            invalidation={"kind": "logic_broken", "value": "逻辑破坏"},
            due_on=DUE_ON,
        )
        session.add(entry)
        await session.flush()
        evaluation = await svc.evaluate_entry(session, entry, as_of=DUE_ON)
        with pytest.raises(svc.NotReviewable) as exc:
            await svc.review_entry(
                session,
                entry.user_id,
                entry.id,
                verdict="thesis_wrong",
                now=datetime(2026, 10, 22, 15, 0),
            )
        return (
            svc.reviewable(entry),
            str(exc.value),
            dict(entry.invalidation),
            evaluation,
        )

    reviewable, message, invalidation, evaluation = await _with_rollback(_go)
    assert reviewable is False
    assert "不可复盘" in message
    assert "不可观测" in message
    assert "price_below" in message, "拒绝文案必须告诉用户正确形态（可操作，不是只说不行）"
    # 历史数据原样保留（闸只影响读取/写入动作，不改写快照或 invalidation）
    assert invalidation == {"kind": "logic_broken", "value": "逻辑破坏"}
    assert evaluation["invalidation_observable"] is False
    assert evaluation["invalidation_hit"] is None
    assert evaluation["missing_reason"]["invalidation_hit"] == "invalidation_kind_unknown"


# ---------------------------------------------------------------------------
# 迁移：只加列（+ 两条 check），不删不改不搬数据
# ---------------------------------------------------------------------------

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "b1c8d4e7f2a9_add_journal_position_size_confidence.py"


def test_migration_only_adds_position_size_and_confidence() -> None:
    """spec 的硬约束：只加列/约束（``position_size`` 带默认 + NOT NULL；``confidence`` 可空）。"""
    source = _MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def downgrade")[0]
    assert upgrade.count("op.add_column(") == 2
    assert "sa.String(length=16)" in upgrade
    assert 'server_default="none"' in upgrade
    assert "nullable=False" in upgrade
    assert 'sa.Column("confidence", sa.Integer(), nullable=True)' in upgrade
    assert "position_size in ('none','light','normal','heavy')" in upgrade
    assert "confidence between 1 and 5" in upgrade
    for forbidden in (
        "create_table",
        "drop_column",
        "drop_table",
        "alter_column",
        "op.execute(",
        "op.bulk_insert",
        "op.create_index",
        "op.drop_constraint",
    ):
        assert forbidden not in upgrade, f"upgrade 阶段不该出现 {forbidden}（迁移只做加法）"
    # 链在 Phase A 的 bet_type 迁移之后（不并行分叉）
    assert 'down_revision: str | None = "a7d3e9f1b2c4"' in source
    assert len(re.findall(r'revision: str = "[0-9a-f]+"', source)) == 1
    for size in POSITION_SIZES:
        assert f"'{size}'" in upgrade
