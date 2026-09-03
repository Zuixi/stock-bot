"""Persistence contract tests for industry signal quality verification."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from app.models.industry_research import (
    IndustryDataQualitySnapshot,
    IndustrySignalEvaluation,
    IndustrySignalEvent,
)
from app.repositories.industry_metric_repo import (
    create_signal_event,
    latest_quality_snapshot,
    latest_signal_event,
    list_due_signal_evaluations,
    list_event_evaluations,
    list_signal_events,
    upsert_quality_snapshot,
    upsert_signal_evaluation,
)


def named_unique(model: type[Any], name: str) -> set[str]:
    constraint = next(
        item
        for item in model.__table__.constraints
        if isinstance(item, UniqueConstraint) and item.name == name
    )
    return {column.name for column in constraint.columns}


def compile_sql(statement: Any, *, literal_binds: bool = False) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": literal_binds},
        )
    )


class FakeScalarResult:
    def __init__(self, *, one: Any = None, many: list[Any] | None = None) -> None:
        self.one = one
        self.many = many or []

    def scalar_one(self) -> Any:
        return self.one

    def scalar_one_or_none(self) -> Any:
        return self.one

    def scalars(self) -> FakeScalarResult:
        return self

    def all(self) -> list[Any]:
        return self.many


class FakeSession:
    def __init__(self, result: FakeScalarResult) -> None:
        self.result = result
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> FakeScalarResult:
        self.statements.append(statement)
        return self.result


def test_verification_models_declare_constraints_jsonb_and_cascade():
    assert named_unique(IndustryDataQualitySnapshot, "uq_industry_quality_date") == {
        "industry_key",
        "as_of",
    }
    assert named_unique(IndustrySignalEvent, "uq_industry_signal_event") == {
        "industry_key",
        "event_date",
        "signal_type",
        "phase",
    }
    assert named_unique(IndustrySignalEvaluation, "uq_industry_signal_evaluation") == {
        "signal_event_id",
        "horizon_days",
        "methodology_version",
    }

    foreign_key = next(
        iter(IndustrySignalEvaluation.signal_event_id.property.columns[0].foreign_keys)
    )
    assert foreign_key.ondelete == "CASCADE"
    for column_name in ("status", "score", "start_snapshot", "end_snapshot"):
        assert column_name in IndustrySignalEvaluation.__table__.columns

    jsonb_columns = {
        IndustryDataQualitySnapshot: {"details"},
        IndustrySignalEvent: {"basis", "basis_periods", "quality_snapshot"},
        IndustrySignalEvaluation: {
            "start_snapshot",
            "end_snapshot",
            "criteria_results",
            "insufficient_reasons",
        },
    }
    for model, names in jsonb_columns.items():
        assert all(isinstance(model.__table__.columns[name].type, JSONB) for name in names)


def test_models_declare_lookup_indexes():
    assert {index.name for index in IndustryDataQualitySnapshot.__table__.indexes} >= {
        "idx_industry_quality_lookup"
    }
    assert {index.name for index in IndustrySignalEvent.__table__.indexes} >= {
        "idx_industry_signal_events_lookup"
    }
    assert {index.name for index in IndustrySignalEvaluation.__table__.indexes} >= {
        "idx_industry_signal_evaluations_due"
    }


async def test_quality_snapshot_repository_upserts_and_returns_latest():
    stored = SimpleNamespace(id=1)
    db = FakeSession(FakeScalarResult(one=stored))
    row = {
        "industry_key": "pig",
        "as_of": date(2026, 9, 3),
        "status": "healthy",
        "signal_ready": True,
        "ready_count": 3,
        "missing_count": 0,
        "stale_count": 0,
        "rejected_count": 0,
        "partial_count": 0,
        "details": [],
    }

    assert await upsert_quality_snapshot(db, row) is stored
    sql = compile_sql(db.statements[-1])
    assert "ON CONFLICT ON CONSTRAINT uq_industry_quality_date DO UPDATE" in sql
    assert "details = excluded.details" in sql

    assert await latest_quality_snapshot(db, "pig") is stored
    sql = compile_sql(db.statements[-1], literal_binds=True)
    assert "industry_data_quality_snapshots.industry_key = 'pig'" in sql
    assert "industry_data_quality_snapshots.as_of DESC" in sql


async def test_signal_event_repository_is_immutable_and_lists_newest_first():
    stored = SimpleNamespace(id=11)
    db = FakeSession(FakeScalarResult(one=stored, many=[stored]))
    row = {
        "industry_key": "pig",
        "event_date": date(2026, 9, 3),
        "previous_signal_type": None,
        "previous_phase": None,
        "signal_type": "买入",
        "phase": "萧条期",
        "basis": {},
        "basis_periods": {},
        "quality_snapshot": {},
        "rule_version": "pig-cycle-v1",
    }

    assert await create_signal_event(db, row) is stored
    sql = compile_sql(db.statements[-1])
    assert "ON CONFLICT ON CONSTRAINT uq_industry_signal_event DO NOTHING" in sql
    assert "DO UPDATE" not in sql
    assert "RETURNING" in sql

    assert await latest_signal_event(db, "pig") is stored
    assert "industry_signal_events.event_date DESC" in compile_sql(db.statements[-1])

    assert await list_signal_events(db, "pig", 7) == [stored]
    sql = compile_sql(db.statements[-1], literal_binds=True)
    assert "industry_signal_events.event_date DESC" in sql
    assert "LIMIT 7" in sql


async def test_evaluation_repository_upserts_and_due_query_is_scoped_and_ordered():
    stored = SimpleNamespace(id=21)
    db = FakeSession(FakeScalarResult(one=stored, many=[stored]))
    row = {
        "signal_event_id": 11,
        "horizon_days": 30,
        "methodology_version": "pig-cycle-v1",
        "target_date": date(2026, 10, 3),
        "status": "pending",
        "rules": [],
        "start_snapshot": {},
        "end_snapshot": None,
        "criteria_results": None,
        "insufficient_reasons": None,
        "score": None,
        "evaluated_at": None,
    }

    assert await upsert_signal_evaluation(db, row) is stored
    sql = compile_sql(db.statements[-1])
    assert "ON CONFLICT ON CONSTRAINT uq_industry_signal_evaluation DO UPDATE" in sql
    assert "status = excluded.status" in sql

    assert await list_due_signal_evaluations(db, "pig", date(2026, 10, 3)) == [stored]
    sql = compile_sql(db.statements[-1], literal_binds=True)
    assert "industry_signal_events.industry_key = 'pig'" in sql
    assert "industry_signal_evaluations.status = 'pending'" in sql
    assert "industry_signal_evaluations.target_date <= '2026-10-03'" in sql
    assert "industry_signal_evaluations.target_date" in sql and " ASC" in sql

    assert await list_event_evaluations(db, [11, 12]) == [stored]
    sql = compile_sql(db.statements[-1], literal_binds=True)
    assert "industry_signal_evaluations.signal_event_id IN (11, 12)" in sql
    assert "industry_signal_evaluations.horizon_days" in sql and " ASC" in sql


async def test_list_event_evaluations_skips_database_for_empty_ids():
    db = FakeSession(FakeScalarResult(many=[]))
    assert await list_event_evaluations(db, []) == []
    assert db.statements == []
