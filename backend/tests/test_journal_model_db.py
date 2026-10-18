"""spec §S9.2 —— **2 张表的 DDL / 约束**真的拒绝越界写入（真 PG，`TEST_DATABASE_URL`）。

两类断言分开写：

- **静态（不连库）**：迁移只新增 2 张表（spec 的验收判据是 ``grep create_table`` = 2）、
  4 条 check + ``unique(entry_id)`` + 2 个索引逐字在位；ORM 模型与迁移的表名/约束名一致。
- **真 PG**：``thesis`` 超 200 字 / ``horizon_days`` 不在 {7,30,90} / ``kind`` 越界 /
  ``verdict`` 越界 / ``invalidation`` 缺键 → DB 拒绝；同一 entry 二次复盘 → **唯一约束**
  拒绝（不是静默覆盖）。

为什么不只测 API：API 层可以靠 pydantic 兜住，但**库是最后一道闸**——另一个写入口
（脚本、回补、未来 worker）绕过 API 时，口径必须还在。所以约束本身要有用例。

所有写入都包在 **回滚** 事务里（Ruling 17：跑完库与跑前一致）；库不可达时 skip 而不是红。
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.journal import (
    BET_TYPES,
    ENTRY_KINDS,
    HORIZON_CHOICES,
    VERDICTS,
    DecisionJournalEntry,
    DecisionJournalReview,
)

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions"
_MIGRATION = _VERSIONS_DIR / "c64df59c0e86_add_decision_journal_tables.py"


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
# 静态：迁移只新增 2 张表 + 约束/索引逐字在位（spec §S9.2 验收判据）
# ---------------------------------------------------------------------------


def test_migration_creates_exactly_two_tables() -> None:
    """spec 验收判据：``grep create_table`` = 2，且就是这两张（不多建持仓/成本表）。"""
    source = _MIGRATION.read_text(encoding="utf-8")
    tables = re.findall(r"op\.create_table\(\s*\"([a-z_]+)\"", source)
    assert len(re.findall(r"create_table", source)) == 2
    assert tables == ["decision_journal_entries", "decision_journal_reviews"]


def test_migration_checks_and_indexes_are_verbatim() -> None:
    """DDL 逐字照 spec：4 条 check 谓词 + unique(entry_id) + 两个索引（含 created_at DESC）。"""
    source = _MIGRATION.read_text(encoding="utf-8")
    for predicate in (
        "char_length(thesis) between 1 and 200",
        "horizon_days in (7, 30, 90)",
        "invalidation ? 'kind' and invalidation ? 'value'",
        "kind in ('watch','buy','sell','pass','hold')",
        "'thesis_wrong','timing_wrong','discipline_break'",
        "'right_for_wrong_reason','luck'",
    ):
        assert predicate in source, f"迁移缺 check 谓词：{predicate}"
    assert 'sa.UniqueConstraint("entry_id"' in source
    assert '["user_id", "due_on"]' in source
    assert '["user_id", sa.text("created_at DESC")]' in source
    # append-only 的反向证据：迁移里没有更新/删除既有数据的语句
    for forbidden in ("op.drop_column", "op.execute(", "op.alter_column"):
        assert forbidden not in source


def test_orm_matches_migration_table_names() -> None:
    assert DecisionJournalEntry.__tablename__ == "decision_journal_entries"
    assert DecisionJournalReview.__tablename__ == "decision_journal_reviews"
    entry_checks = {
        constraint.name
        for constraint in DecisionJournalEntry.__table__.constraints
        if constraint.name
    }
    assert {
        "ck_journal_thesis_len",
        "ck_journal_horizon",
        "ck_journal_invalidation_keys",
        "ck_journal_kind",
        # ★ spec §S9.7.3：bet_type 三枚举（迁移 a7d3e9f1b2c4 加列时同时加）
        "ck_journal_bet_type",
    } <= entry_checks
    review_checks = {
        constraint.name
        for constraint in DecisionJournalReview.__table__.constraints
        if constraint.name
    }
    assert "ck_journal_verdict" in review_checks
    assert "uq_journal_review_entry" in review_checks


def test_enums_are_single_source() -> None:
    """三枚举只在这一处定义；schema/服务/迁移都引用它（漂移 = 库里出现第五种值）。"""
    assert HORIZON_CHOICES == (7, 30, 90)
    assert ENTRY_KINDS == ("watch", "buy", "sell", "pass", "hold")
    # ★ spec §S9.7.3：基准口径三枚举（schema 的 BetType 与它逐字一致）
    assert BET_TYPES == ("direction", "relative", "market")
    assert VERDICTS == (
        "thesis_wrong",
        "timing_wrong",
        "discipline_break",
        "right_for_wrong_reason",
        "luck",
    )


# ---------------------------------------------------------------------------
# 真 PG：约束拒绝越界写入
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


def _entry(**overrides: Any) -> DecisionJournalEntry:
    payload: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "symbol": None,
        "kind": "watch",
        "thesis": "猪价见底，产能去化加速",
        "invalidation": {"kind": "price_below", "value": 38.0},
        "horizon_days": 30,
        "snapshot": {"version": "s9.r1", "price": None, "missing_reason": {}},
        "created_at": datetime(2026, 10, 16, 12, 0),
        "due_on": date(2026, 11, 15),
    }
    payload.update(overrides)
    return DecisionJournalEntry(**payload)


async def _rejects(session: AsyncSession, obj: Any) -> str:
    """在 SAVEPOINT 里插入越界行 → 必须 ``IntegrityError``（外层事务仍可继续）。"""
    with pytest.raises(IntegrityError) as exc:
        async with session.begin_nested():
            session.add(obj)
            await session.flush()
    return str(exc.value)


@requires_pg
async def test_thesis_longer_than_200_is_rejected() -> None:
    async def _go(session: AsyncSession) -> str:
        return await _rejects(session, _entry(thesis="长" * 201))

    message = await _with_rollback(_go)
    assert "ck_journal_thesis_len" in message


@requires_pg
async def test_thesis_must_not_be_empty() -> None:
    async def _go(session: AsyncSession) -> str:
        return await _rejects(session, _entry(thesis=""))

    message = await _with_rollback(_go)
    assert "ck_journal_thesis_len" in message


@requires_pg
async def test_horizon_outside_choices_is_rejected() -> None:
    async def _go(session: AsyncSession) -> str:
        return await _rejects(session, _entry(horizon_days=15))

    message = await _with_rollback(_go)
    assert "ck_journal_horizon" in message


@requires_pg
async def test_kind_outside_choices_is_rejected() -> None:
    async def _go(session: AsyncSession) -> str:
        return await _rejects(session, _entry(kind="hunch"))

    message = await _with_rollback(_go)
    assert "ck_journal_kind" in message


@requires_pg
async def test_bet_type_outside_choices_is_rejected() -> None:
    """spec §S9.7.3：``bet_type`` 越界 → API 422，DB 也拒绝（库是最后一道闸）。"""

    async def _go(session: AsyncSession) -> str:
        return await _rejects(session, _entry(bet_type="hunch"))

    message = await _with_rollback(_go)
    assert "ck_journal_bet_type" in message


@requires_pg
async def test_bet_type_defaults_to_direction() -> None:
    """冒烟：不传 ``bet_type`` 时由 ``server_default`` 回填 ``direction``（不是 NULL）。"""

    async def _go(session: AsyncSession) -> str:
        entry = _entry()
        session.add(entry)
        await session.flush()
        await session.refresh(entry)
        return entry.bet_type

    assert await _with_rollback(_go) == "direction"


@requires_pg
async def test_invalidation_must_carry_kind_and_value() -> None:
    """spec 的 ``invalidation ? 'kind' and invalidation ? 'value'``：缺键 → DB 拒绝。"""

    async def _go(session: AsyncSession) -> tuple[str, str]:
        no_value = await _rejects(session, _entry(invalidation={"kind": "price_below"}))
        no_kind = await _rejects(session, _entry(invalidation={"value": 38.0}))
        return no_value, no_kind

    no_value, no_kind = await _with_rollback(_go)
    assert "ck_journal_invalidation_keys" in no_value
    assert "ck_journal_invalidation_keys" in no_kind


@requires_pg
async def test_verdict_outside_enum_is_rejected() -> None:
    async def _go(session: AsyncSession) -> str:
        entry = _entry()
        session.add(entry)
        await session.flush()
        review = DecisionJournalReview(
            entry_id=entry.id,
            outcome_pct=1.0,
            bench_pct=0.5,
            excess_pct=0.5,
            invalidation_hit=False,
            verdict="gambling",
        )
        return await _rejects(session, review)

    message = await _with_rollback(_go)
    assert "ck_journal_verdict" in message


@requires_pg
async def test_second_review_for_same_entry_is_rejected_by_unique() -> None:
    """spec 验收判据：同一条目重复复盘被 ``unique(entry_id)`` 拒绝（不是静默覆盖）。"""

    async def _go(session: AsyncSession) -> str:
        entry = _entry()
        session.add(entry)
        await session.flush()
        first = DecisionJournalReview(
            entry_id=entry.id,
            outcome_pct=1.0,
            bench_pct=0.5,
            excess_pct=0.5,
            invalidation_hit=False,
            verdict="luck",
        )
        session.add(first)
        await session.flush()
        duplicate = DecisionJournalReview(
            entry_id=entry.id,
            outcome_pct=9.0,
            bench_pct=0.5,
            excess_pct=8.5,
            invalidation_hit=True,
            verdict="thesis_wrong",
        )
        return await _rejects(session, duplicate)

    message = await _with_rollback(_go)
    assert "uq_journal_review_entry" in message


@requires_pg
async def test_due_on_and_snapshot_persist_verbatim() -> None:
    """``due_on`` 与 ``snapshot`` 原样落库（快照是不可变 jsonb，不经过任何再计算）。"""

    async def _go(session: AsyncSession) -> tuple[date, dict[str, Any]]:
        snapshot = {
            "version": "s9.r1",
            "price": 12.34,
            "pct_chg": None,
            "missing_reason": {"pct_chg": "no_pre_close"},
            "events": [{"detector": "drop_sharp", "headline": "跌得急"}],
        }
        entry = _entry(snapshot=snapshot)
        session.add(entry)
        await session.flush()
        await session.refresh(entry)
        return entry.due_on, entry.snapshot

    due_on, snapshot = await _with_rollback(_go)
    assert due_on == date(2026, 11, 15)
    assert snapshot["price"] == 12.34
    assert snapshot["missing_reason"] == {"pct_chg": "no_pre_close"}
    assert snapshot["events"][0]["detector"] == "drop_sharp"
