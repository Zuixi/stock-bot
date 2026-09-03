"""Persistence contract tests for industry signal quality verification."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, get_args, get_type_hints
from unittest.mock import AsyncMock, Mock

import pytest
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
    lock_signal_event_day,
    upsert_quality_snapshot,
    upsert_signal_evaluation,
)
from app.services.industry_data_quality import IndustryQualityResult, MetricQualityResult
from app.services.industry_metric_service import get_dashboard
from app.services.industry_registry import (
    BROILER_INDUSTRY,
    PIG_INDUSTRY,
    SIGNAL_BUY,
    SIGNAL_SELL,
)
from app.services.industry_signal_verification import (
    assess_current_quality,
    ensure_signal_event,
    evaluate_and_store_signal,
    run_due_signal_evaluations,
    score_verification,
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
        "event_sequence",
    }
    assert IndustrySignalEvent.__table__.columns["event_sequence"].nullable is False
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


def test_evaluation_score_annotation_matches_numeric_decimal_values():
    assert get_args(get_type_hints(IndustrySignalEvaluation)["score"]) == (Decimal | None,)


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


async def test_signal_event_day_lock_uses_stable_postgresql_advisory_key():
    db = FakeSession(FakeScalarResult())

    await lock_signal_event_day(db, "pig", date(2026, 9, 3))

    sql = compile_sql(db.statements[-1], literal_binds=True)
    assert "pg_advisory_xact_lock" in sql
    assert "hashtextextended" in sql
    assert "signal-event:pig:2026-09-03" in sql


async def test_signal_event_repository_is_immutable_and_lists_newest_first():
    stored = SimpleNamespace(id=11)
    db = FakeSession(FakeScalarResult(one=stored, many=[stored]))
    row = {
        "industry_key": "pig",
        "event_date": date(2026, 9, 3),
        "event_sequence": 1,
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
    assert "ON CONFLICT (industry_key, event_date, event_sequence) DO NOTHING" in sql
    assert "DO UPDATE" not in sql
    assert "RETURNING" in sql

    injected_row = {
        **row,
        "id": 999,
        "created_at": datetime(2000, 1, 1, tzinfo=UTC),
    }
    assert await create_signal_event(db, injected_row) is stored
    statement = db.statements[-1]
    assert "id" not in statement.compile().params
    assert "created_at" not in statement.compile().params
    insert_sql = compile_sql(statement)
    insert_columns = insert_sql.split("(", maxsplit=1)[1].split(")", maxsplit=1)[0]
    assert "id" not in {column.strip() for column in insert_columns.split(",")}
    assert "created_at" not in {column.strip() for column in insert_columns.split(",")}

    assert await latest_signal_event(db, "pig") is stored
    latest_sql = compile_sql(db.statements[-1])
    assert "industry_signal_events.event_date DESC" in latest_sql
    assert "industry_signal_events.event_sequence DESC" in latest_sql

    assert await list_signal_events(db, "pig", 7) == [stored]
    sql = compile_sql(db.statements[-1], literal_binds=True)
    assert "industry_signal_events.event_date DESC" in sql
    assert "industry_signal_events.event_sequence DESC" in sql
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


class FakeCache:
    def __init__(self) -> None:
        self.stored = None

    async def get(self, _key):
        return None

    async def set(self, _key, value, *, ttl):
        self.stored = (value, ttl)


@pytest.mark.asyncio
async def test_dashboard_without_valid_signal_returns_nullable_cycle_and_signal(monkeypatch):
    monkeypatch.setattr(
        "app.services.industry_metric_service.repo.latest_rows_by_metric",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "app.services.industry_metric_service.repo.get_metric_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.services.industry_metric_service.repo.list_reference_points",
        AsyncMock(return_value=[]),
    )
    latest_signal = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.services.industry_metric_service.repo.latest_signal", latest_signal
    )
    monkeypatch.setattr(
        "app.services.industry_metric_service.repo.list_signals",
        AsyncMock(return_value=[]),
    )

    result = await get_dashboard(SimpleNamespace(), FakeCache(), "pig")

    assert result.cycle is None
    assert result.signal is None
    assert result.signal_history == []
    latest_signal.assert_awaited_once()


def quality_result(*, status: str = "healthy", signal_ready: bool = True):
    return IndustryQualityResult(
        status=status,
        signal_ready=signal_ready,
        ready_count=3 if signal_ready else 0,
        missing_count=0 if signal_ready else 1,
        stale_count=0,
        rejected_count=0,
        partial_count=0,
        details=[
            MetricQualityResult(
                metric_key=metric_key,
                status="ready" if signal_ready else "missing",
                source=source if signal_ready else None,
                freq=freq if signal_ready else None,
                period=period if signal_ready else None,
                age_days=0 if signal_ready else None,
                reason=None if signal_ready else "no selected observation",
                entity_coverage=None,
            )
            for metric_key, source, freq, period in (
                ("hog_price", "akshare_soozhu", "daily", date(2026, 9, 3)),
                ("hog_corn_ratio", "derived", "daily", date(2026, 9, 3)),
                ("sow_inventory_mom", "derived", "monthly", date(2026, 8, 31)),
            )
        ],
    )


def signal_row(
    *,
    row_id: int = 1,
    signal_type: str = SIGNAL_BUY,
    phase: str = "recovery",
    effective_date: date = date(2026, 9, 3),
):
    return SimpleNamespace(
        id=row_id,
        industry_key="pig",
        signal_type=signal_type,
        phase=phase,
        effective_date=effective_date,
        positions=[],
        reason="reason",
        basis={
            "ratio": 6.5,
            "price": 15.0,
            "sow_mom_series": [-0.2, -0.1, 0.0],
        },
    )


@pytest.mark.asyncio
async def test_quality_uses_engine_source_selection_and_persists_selected_periods(monkeypatch):
    selected = {
        "hog_price": [
            SimpleNamespace(
                source="mock", freq="daily", period=date(2026, 9, 3), value=16.0
            ),
            SimpleNamespace(
                source="akshare_soozhu", freq="daily", period=date(2026, 9, 2), value=15.5
            ),
        ],
        "hog_corn_ratio": [
            SimpleNamespace(
                source="derived", freq="daily", period=date(2026, 9, 2), value=6.4
            )
        ],
        "sow_inventory_mom": [
            SimpleNamespace(
                source="derived", freq="monthly", period=date(2026, 8, 31), value=-0.2
            )
        ],
    }
    latest_rows = AsyncMock(return_value=selected)
    persist = AsyncMock(return_value=SimpleNamespace(id=1))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.latest_rows_by_metric", latest_rows
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_quality_snapshot", persist
    )

    result = await assess_current_quality(
        SimpleNamespace(), PIG_INDUSTRY, as_of=date(2026, 9, 3)
    )

    assert result.signal_ready is True
    by_key = {item.metric_key: item for item in result.details}
    assert by_key["hog_price"].source == "akshare_soozhu"
    assert by_key["hog_price"].freq == "daily"
    assert by_key["hog_price"].period == date(2026, 9, 2)
    payload = persist.await_args.args[1]
    assert payload["as_of"] == date(2026, 9, 3)
    assert next(item for item in payload["details"] if item["metric_key"] == "hog_price")[
        "period"
    ] == "2026-09-02"


@pytest.mark.asyncio
async def test_unavailable_quality_without_previous_signal_is_still_stale(monkeypatch):
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.latest_signal",
        AsyncMock(return_value=None),
    )
    evaluator = Mock()
    monkeypatch.setattr(
        "app.services.industry_signal_verification.cycle_engine.evaluate_pig_cycle", evaluator
    )

    result = await evaluate_and_store_signal(
        SimpleNamespace(),
        PIG_INDUSTRY,
        quality=quality_result(status="unavailable", signal_ready=False),
        effective_date=date(2026, 9, 3),
    )

    assert result.signal is None
    assert result.updated is False
    assert result.stale is True
    evaluator.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_quality_gates_engine_and_retains_previous_signal(monkeypatch):
    previous = signal_row(effective_date=date(2026, 9, 2))
    latest_signal = AsyncMock(return_value=previous)
    upsert_signal = AsyncMock()
    cycle_evaluator = Mock()
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.latest_signal", latest_signal
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal", upsert_signal
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.cycle_engine.evaluate_pig_cycle",
        cycle_evaluator,
    )

    result = await evaluate_and_store_signal(
        SimpleNamespace(),
        PIG_INDUSTRY,
        quality=quality_result(status="unavailable", signal_ready=False),
        effective_date=date(2026, 9, 3),
    )

    cycle_evaluator.assert_not_called()
    upsert_signal.assert_not_awaited()
    assert result.updated is False
    assert result.stale is True
    assert result.signal is previous
    assert result.event is None


@pytest.mark.asyncio
async def test_demo_industry_can_evaluate_signal_but_never_creates_formal_event(monkeypatch):
    built = SimpleNamespace(cycle_input=SimpleNamespace(), basis_periods={})
    output = SimpleNamespace(
        phase="depression", signal="空仓", positions=[], reasons=["demo"], basis={}
    )
    stored = SimpleNamespace(
        id=2,
        industry_key="broiler",
        signal_type="空仓",
        phase="depression",
        effective_date=date(2026, 9, 3),
        basis={},
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification._build_cycle_snapshot",
        AsyncMock(return_value=built),
    )
    evaluator = Mock(return_value=output)
    monkeypatch.setattr(
        "app.services.industry_signal_verification.cycle_engine.evaluate_pig_cycle", evaluator
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal",
        AsyncMock(return_value=stored),
    )
    create_event = AsyncMock()
    upsert_evaluation = AsyncMock()
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.create_signal_event", create_event
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation",
        upsert_evaluation,
    )

    result = await evaluate_and_store_signal(
        SimpleNamespace(),
        BROILER_INDUSTRY,
        quality=quality_result(status="demo", signal_ready=False),
        effective_date=date(2026, 9, 3),
    )

    evaluator.assert_called_once()
    assert result.updated is True
    assert result.stale is False
    assert result.signal is stored
    assert result.event is None
    create_event.assert_not_awaited()
    upsert_evaluation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "current_signal", "current_phase", "creates"),
    [
        (None, SIGNAL_BUY, "recovery", True),
        (signal_row(), SIGNAL_BUY, "recovery", False),
        (signal_row(), SIGNAL_SELL, "prosperity", True),
        (signal_row(), SIGNAL_BUY, "prosperity", True),
    ],
)
async def test_signal_events_are_created_only_for_baseline_or_transition(
    monkeypatch, previous, current_signal, current_phase, creates
):
    current = signal_row(signal_type=current_signal, phase=current_phase)
    event = SimpleNamespace(id=11)
    calls = []

    async def lock(*_args):
        calls.append("lock")

    async def latest(*_args):
        calls.append("latest")
        return previous

    async def create(*_args):
        calls.append("create")
        return event

    latest_event = AsyncMock(side_effect=latest)
    create_event = AsyncMock(side_effect=create)
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.lock_signal_event_day", lock
    )
    upsert_evaluation = AsyncMock(return_value=SimpleNamespace(id=21))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.latest_signal_event", latest_event
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.create_signal_event", create_event
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation",
        upsert_evaluation,
    )

    result = await ensure_signal_event(
        SimpleNamespace(),
        PIG_INDUSTRY,
        current,
        basis_periods={
            "hog_corn_ratio": "2026-09-03",
            "hog_price": "2026-09-03",
            "sow_inventory_mom": "2026-08-31",
        },
        quality=quality_result(),
    )

    assert calls[:2] == ["lock", "latest"]
    if not creates:
        assert result is None
        create_event.assert_not_awaited()
        upsert_evaluation.assert_not_awaited()
    else:
        assert result is event
        assert calls == ["lock", "latest", "create"]
        create_event.assert_awaited_once()
        event_payload = create_event.await_args.args[1]
        assert event_payload["event_sequence"] == (1 if previous is None else 2)
        if current_signal in (SIGNAL_BUY, SIGNAL_SELL):
            assert [call.args[1]["horizon_days"] for call in upsert_evaluation.await_args_list] == [
                30,
                90,
            ]
            assert all(
                call.args[1]["status"] == "pending"
                for call in upsert_evaluation.await_args_list
            )
        else:
            upsert_evaluation.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_create_conflict_under_lock_is_defensive_idempotency(monkeypatch):
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.lock_signal_event_day",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.latest_signal_event",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.create_signal_event",
        AsyncMock(return_value=None),
    )
    upsert_evaluation = AsyncMock()
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation",
        upsert_evaluation,
    )

    result = await ensure_signal_event(
        SimpleNamespace(),
        PIG_INDUSTRY,
        signal_row(),
        basis_periods={"hog_price": "2026-09-03"},
        quality=quality_result(),
    )

    assert result is None
    upsert_evaluation.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_day_a_to_b_to_a_appends_sequences_and_repeated_a_is_idempotent(
    monkeypatch,
):
    stored_events = []

    async def latest(_db, _industry_key):
        return stored_events[-1] if stored_events else None

    async def create(_db, payload):
        event = SimpleNamespace(id=len(stored_events) + 1, **payload)
        stored_events.append(event)
        return event

    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.lock_signal_event_day",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.latest_signal_event", latest
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.create_signal_event", create
    )
    upsert_evaluation = AsyncMock(return_value=SimpleNamespace(id=21))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation",
        upsert_evaluation,
    )
    kwargs = {
        "basis_periods": {
            "hog_corn_ratio": "2026-09-03",
            "hog_price": "2026-09-03",
            "sow_inventory_mom": "2026-08-31",
        },
        "quality": quality_result(),
    }

    first_a = await ensure_signal_event(
        SimpleNamespace(), PIG_INDUSTRY, signal_row(), **kwargs
    )
    event_b = await ensure_signal_event(
        SimpleNamespace(),
        PIG_INDUSTRY,
        signal_row(signal_type=SIGNAL_SELL, phase="prosperity"),
        **kwargs,
    )
    second_a = await ensure_signal_event(
        SimpleNamespace(), PIG_INDUSTRY, signal_row(), **kwargs
    )
    repeated_a = await ensure_signal_event(
        SimpleNamespace(), PIG_INDUSTRY, signal_row(), **kwargs
    )

    assert [event.event_sequence for event in stored_events] == [1, 2, 3]
    assert first_a.id == 1
    assert event_b.id == 2
    assert second_a.id == 3
    assert repeated_a is None
    assert [call.args[1]["signal_event_id"] for call in upsert_evaluation.await_args_list] == [
        1,
        1,
        2,
        2,
        3,
        3,
    ]


def rules_for(horizon: int = 30):
    verification = PIG_INDUSTRY.verification
    assert verification is not None
    return next(item.rules for item in verification.horizons if item.days == horizon)


def snapshot(signal_type: str, ratio: float, price: float, sow_mom: float | None):
    metrics = {
        "hog_corn_ratio": {
            "value": ratio,
            "period": "2026-09-03",
            "source": "derived",
            "freq": "daily",
        },
        "hog_price": {
            "value": price,
            "period": "2026-09-03",
            "source": "akshare_soozhu",
            "freq": "daily",
        },
    }
    if sow_mom is not None:
        metrics["sow_inventory_mom"] = {
            "value": sow_mom,
            "period": "2026-09-30",
            "source": "derived",
            "freq": "monthly",
        }
    return {"signal_type": signal_type, "metrics": metrics}


@pytest.mark.parametrize(
    ("signal_type", "end_values", "expected_score", "expected_status"),
    [
        (SIGNAL_BUY, (6.3, 10.3, -0.1), Decimal("100"), "confirmed"),
        (SIGNAL_BUY, (6.3, 10.0, 0.2), Decimal("55"), "partially_confirmed"),
        (SIGNAL_SELL, (6.2, 10.2, -0.1), Decimal("15"), "invalidated"),
    ],
)
def test_verification_score_is_signal_direction_dependent(
    signal_type, end_values, expected_score, expected_status
):
    start = snapshot(signal_type, 6.0, 10.0, -0.2)
    end = snapshot(signal_type, *end_values)

    result = score_verification(rules_for(), start, end)

    assert result.score == expected_score
    assert result.status == expected_status
    assert len(result.criteria_results) == 3


@pytest.mark.parametrize(
    ("signal_type", "end_ratio"),
    [
        (SIGNAL_BUY, 10.3),
        (SIGNAL_SELL, 9.7),
    ],
)
def test_percentage_rule_exact_three_percent_boundary_gets_full_weight(
    signal_type, end_ratio
):
    rule = replace(rules_for()[0], weight=100)
    start = snapshot(signal_type, 10.0, 10.0, -0.2)
    end = snapshot(signal_type, end_ratio, 10.0, -0.2)

    result = score_verification((rule,), start, end)

    assert result.status == "confirmed"
    assert result.score == Decimal("100")
    assert result.criteria_results[0]["change_pct"] in {"3.00", "-3.00"}
    assert result.criteria_results[0]["score"] == "100"


def test_required_evidence_missing_is_inconclusive():
    result = score_verification(
        rules_for(),
        snapshot(SIGNAL_BUY, 6.0, 10.0, -0.2),
        snapshot(SIGNAL_BUY, 6.3, 10.3, None),
    )
    assert result.status == "inconclusive"
    assert result.score is None
    assert result.insufficient_reasons == ["sow_inventory_mom: missing required evidence"]


@pytest.mark.asyncio
async def test_due_evaluation_before_rule_grace_remains_pending(monkeypatch):
    evaluation = SimpleNamespace(
        id=21,
        signal_event_id=11,
        target_date=date(2026, 10, 3),
        rules=[vars(rule) for rule in rules_for()],
        start_snapshot=snapshot(SIGNAL_BUY, 6.0, 10.0, -0.2),
        methodology_version="pig-cycle-v1",
        horizon_days=30,
    )
    event = SimpleNamespace(id=11, industry_key="pig", signal_type=SIGNAL_BUY)
    db = SimpleNamespace(get=AsyncMock(return_value=event))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.list_due_signal_evaluations",
        AsyncMock(return_value=[evaluation]),
    )
    history = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.get_metric_history", history
    )
    upsert = AsyncMock()
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation", upsert
    )

    result = await run_due_signal_evaluations(
        db, PIG_INDUSTRY, as_of=date(2026, 10, 10)
    )

    assert result.pending == 1
    assert result.evaluated == 0
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_evaluation_missing_frozen_provenance_is_inconclusive(monkeypatch):
    start = snapshot(SIGNAL_BUY, 6.0, 10.0, -0.2)
    start["metrics"]["hog_price"].pop("freq")
    evaluation = SimpleNamespace(
        id=21,
        signal_event_id=11,
        target_date=date(2026, 10, 3),
        rules=[vars(rule) for rule in rules_for()],
        start_snapshot=start,
        methodology_version="pig-cycle-v1",
        horizon_days=30,
    )
    event = SimpleNamespace(id=11, industry_key="pig", signal_type=SIGNAL_BUY)
    db = SimpleNamespace(get=AsyncMock(return_value=event))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.list_due_signal_evaluations",
        AsyncMock(return_value=[evaluation]),
    )
    history = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.get_metric_history", history
    )
    upsert = AsyncMock(return_value=SimpleNamespace(id=21))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation", upsert
    )

    result = await run_due_signal_evaluations(
        db, PIG_INDUSTRY, as_of=date(2026, 12, 1)
    )

    assert result.inconclusive == 1
    payload = upsert.await_args.args[1]
    assert payload["insufficient_reasons"] == [
        "hog_price: missing frozen source or freq"
    ]
    assert all(call.args[2] != "hog_price" for call in history.await_args_list)


@pytest.mark.asyncio
async def test_fallback_selected_actual_freq_is_frozen_and_used_after_registry_change(
    monkeypatch,
):
    quality = quality_result()
    quality = replace(
        quality,
        details=[
            replace(item, freq="weekly") if item.metric_key == "hog_price" else item
            for item in quality.details
        ],
    )
    event = SimpleNamespace(id=11)
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.lock_signal_event_day",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.latest_signal_event",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.create_signal_event",
        AsyncMock(return_value=event),
    )
    evaluations = []

    async def store(_db, payload):
        evaluations.append(payload)
        return SimpleNamespace(id=21, **payload)

    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation", store
    )
    await ensure_signal_event(
        SimpleNamespace(),
        PIG_INDUSTRY,
        signal_row(),
        basis_periods={
            "hog_corn_ratio": "2026-09-03",
            "hog_price": "2026-09-03",
            "sow_inventory_mom": "2026-08-31",
        },
        quality=quality,
    )
    assert evaluations[0]["start_snapshot"]["metrics"]["hog_price"]["freq"] == "weekly"

    evaluation = SimpleNamespace(**evaluations[0])
    db = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(id=11, industry_key="pig", signal_type=SIGNAL_BUY)
        )
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.list_due_signal_evaluations",
        AsyncMock(return_value=[evaluation]),
    )
    calls = []

    async def history(_db, _industry, metric_key, **kwargs):
        calls.append((metric_key, kwargs))
        values = {"hog_corn_ratio": 6.7, "hog_price": 15.5, "sow_inventory_mom": -0.1}

        period = (
            evaluation.target_date
            if metric_key != "sow_inventory_mom"
            else evaluation.target_date.replace(day=28)
        )
        return [
            SimpleNamespace(period=period, value=values[metric_key], source=kwargs["source"])
        ]

    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.get_metric_history", history
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation",
        AsyncMock(return_value=SimpleNamespace(id=21)),
    )
    changed_cfg = replace(
        PIG_INDUSTRY,
        metrics=[
            replace(metric, freq="quarterly") if metric.key == "hog_price" else metric
            for metric in PIG_INDUSTRY.metrics
        ],
    )

    result = await run_due_signal_evaluations(
        db, changed_cfg, as_of=evaluation.target_date
    )

    assert result.confirmed == 1
    hog_price_call = next(kwargs for key, kwargs in calls if key == "hog_price")
    assert hog_price_call["freq"] == "weekly"


@pytest.mark.asyncio
async def test_due_evaluation_uses_frozen_freq_after_registry_changes(monkeypatch):
    evaluation = SimpleNamespace(
        id=21,
        signal_event_id=11,
        target_date=date(2026, 10, 3),
        rules=[vars(rule) for rule in rules_for()],
        start_snapshot=snapshot(SIGNAL_BUY, 6.0, 10.0, -0.2),
        methodology_version="frozen-v1",
        horizon_days=30,
    )
    event = SimpleNamespace(id=11, industry_key="pig", signal_type=SIGNAL_BUY)
    changed_metrics = [
        replace(metric, freq="weekly") if metric.key == "hog_price" else metric
        for metric in PIG_INDUSTRY.metrics
    ]
    changed_cfg = replace(PIG_INDUSTRY, metrics=changed_metrics)
    db = SimpleNamespace(get=AsyncMock(return_value=event))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.list_due_signal_evaluations",
        AsyncMock(return_value=[evaluation]),
    )
    rows = {
        "hog_corn_ratio": [
            SimpleNamespace(period=date(2026, 10, 4), value=6.3, source="derived")
        ],
        "hog_price": [
            SimpleNamespace(
                period=date(2026, 10, 4), value=10.3, source="akshare_soozhu"
            )
        ],
        "sow_inventory_mom": [
            SimpleNamespace(period=date(2026, 10, 31), value=-0.1, source="derived")
        ],
    }
    calls = []

    async def history(_db, _industry, metric_key, **kwargs):
        calls.append((metric_key, kwargs))
        return rows[metric_key]

    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.get_metric_history", history
    )
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation",
        AsyncMock(return_value=SimpleNamespace(id=21)),
    )

    result = await run_due_signal_evaluations(
        db, changed_cfg, as_of=date(2026, 11, 20)
    )

    assert result.confirmed == 1
    hog_price_call = next(kwargs for key, kwargs in calls if key == "hog_price")
    assert hog_price_call == {"limit": 4000, "freq": "daily", "source": "akshare_soozhu"}


@pytest.mark.asyncio
async def test_due_evaluation_uses_first_observation_on_or_after_target(monkeypatch):
    evaluation = SimpleNamespace(
        id=21,
        signal_event_id=11,
        target_date=date(2026, 10, 3),
        rules=[vars(rule) for rule in rules_for()],
        start_snapshot=snapshot(SIGNAL_BUY, 6.0, 10.0, -0.2),
        methodology_version="frozen-v1",
        horizon_days=30,
    )
    event = SimpleNamespace(id=11, industry_key="pig", signal_type=SIGNAL_BUY)
    db = SimpleNamespace(get=AsyncMock(return_value=event))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.list_due_signal_evaluations",
        AsyncMock(return_value=[evaluation]),
    )
    rows = {
        "hog_corn_ratio": [
            SimpleNamespace(period=date(2026, 10, 2), value=99.0, source="derived"),
            SimpleNamespace(period=date(2026, 10, 4), value=6.3, source="derived"),
            SimpleNamespace(period=date(2026, 10, 5), value=1.0, source="derived"),
        ],
        "hog_price": [
            SimpleNamespace(period=date(2026, 10, 4), value=10.3, source="akshare_soozhu")
        ],
        "sow_inventory_mom": [
            SimpleNamespace(period=date(2026, 10, 31), value=-0.1, source="derived")
        ],
    }

    async def history(_db, _industry, metric_key, **_kwargs):
        return rows[metric_key]

    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.get_metric_history", history
    )
    upsert = AsyncMock(return_value=SimpleNamespace(id=21))
    monkeypatch.setattr(
        "app.services.industry_signal_verification.repo.upsert_signal_evaluation", upsert
    )

    result = await run_due_signal_evaluations(
        db, PIG_INDUSTRY, as_of=date(2026, 11, 20)
    )

    assert result.evaluated == 1
    payload = upsert.await_args.args[1]
    assert payload["methodology_version"] == "frozen-v1"
    assert payload["end_snapshot"]["metrics"]["hog_corn_ratio"] == {
        "value": "6.3",
        "period": "2026-10-04",
        "source": "derived",
        "freq": "daily",
    }
    assert payload["status"] == "confirmed"
