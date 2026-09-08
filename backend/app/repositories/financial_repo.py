"""Repository for financial statements, report versions and metrics."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financial import (
    BalanceSheetFacts,
    CashFlowStatementFacts,
    FinancialMetric,
    FinancialRawRecord,
    FinancialReportVersion,
    IncomeStatementFacts,
)


# ---------------------------------------------------------------------------
# Raw records
# ---------------------------------------------------------------------------

async def save_raw_record(
    db: AsyncSession,
    *,
    source: str,
    dataset: str,
    stock_id: int | None,
    ts_code: str | None,
    request_params: dict[str, Any] | None,
    source_record_key: str | None,
    payload: dict[str, Any] | None,
    payload_hash: str | None,
    fetched_at: datetime,
) -> FinancialRawRecord:
    stmt = (
        insert(FinancialRawRecord)
        .values(
            source=source,
            dataset=dataset,
            stock_id=stock_id,
            ts_code=ts_code,
            request_params=request_params,
            source_record_key=source_record_key,
            payload=payload,
            payload_hash=payload_hash,
            fetched_at=fetched_at,
        )
        .on_conflict_do_nothing(
            constraint="uq_financial_raw_record",
        )
        .returning(FinancialRawRecord.id)
    )
    result = await db.execute(stmt)
    raw_id = result.scalar_one_or_none()
    await db.flush()
    if raw_id is not None:
        return FinancialRawRecord(id=raw_id)
    # Already exists — fetch the existing row to obtain its id.
    stmt = (
        select(FinancialRawRecord)
        .where(FinancialRawRecord.source == source)
        .where(FinancialRawRecord.dataset == dataset)
        .where(FinancialRawRecord.source_record_key == source_record_key)
        .where(FinancialRawRecord.payload_hash == payload_hash)
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is None:
        raise RuntimeError("raw record missing after conflict-no-op")
    return existing


# ---------------------------------------------------------------------------
# Report versions
# ---------------------------------------------------------------------------

async def upsert_report_version(
    db: AsyncSession,
    *,
    stock_id: int,
    ts_code: str | None,
    end_date: date,
    report_type: str,
    ann_date: date | None,
    f_ann_date: date | None,
    comp_type: str,
    source: str,
    source_record_key: str | None,
    update_flag: str | None,
    currency: str,
    quality_status: str,
    as_of: datetime,
    raw_record_id: int | None,
) -> FinancialReportVersion:
    values = {
        "stock_id": stock_id,
        "ts_code": ts_code,
        "end_date": end_date,
        "report_type": report_type,
        "ann_date": ann_date,
        "f_ann_date": f_ann_date,
        "comp_type": comp_type,
        "source": source,
        "source_record_key": source_record_key,
        "update_flag": update_flag,
        "currency": currency,
        "quality_status": quality_status,
        "as_of": as_of,
        "raw_record_id": raw_record_id,
    }
    stmt = (
        insert(FinancialReportVersion)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_financial_report_version",
            set_={
                "ann_date": insert(FinancialReportVersion).excluded.ann_date,
                "f_ann_date": insert(FinancialReportVersion).excluded.f_ann_date,
                "update_flag": insert(FinancialReportVersion).excluded.update_flag,
                "quality_status": insert(FinancialReportVersion).excluded.quality_status,
                "as_of": insert(FinancialReportVersion).excluded.as_of,
                "raw_record_id": insert(FinancialReportVersion).excluded.raw_record_id,
            },
        )
        .returning(FinancialReportVersion)
    )
    result = await db.execute(stmt)
    version = result.scalar_one()
    await db.flush()
    return version


# ---------------------------------------------------------------------------
# Statement facts
# ---------------------------------------------------------------------------

async def upsert_income_facts(
    db: AsyncSession, report_version_id: int, facts: dict[str, Any]
) -> None:
    facts["report_version_id"] = report_version_id
    stmt = (
        insert(IncomeStatementFacts)
        .values(**facts)
        .on_conflict_do_update(
            constraint="uq_income_statement_facts_ver",
            set_={k: getattr(insert(IncomeStatementFacts).excluded, k) for k in facts if k != "report_version_id"},
        )
    )
    await db.execute(stmt)
    await db.flush()


async def upsert_balance_facts(
    db: AsyncSession, report_version_id: int, facts: dict[str, Any]
) -> None:
    facts["report_version_id"] = report_version_id
    stmt = (
        insert(BalanceSheetFacts)
        .values(**facts)
        .on_conflict_do_update(
            constraint="uq_balance_sheet_facts_ver",
            set_={k: getattr(insert(BalanceSheetFacts).excluded, k) for k in facts if k != "report_version_id"},
        )
    )
    await db.execute(stmt)
    await db.flush()


async def upsert_cashflow_facts(
    db: AsyncSession, report_version_id: int, facts: dict[str, Any]
) -> None:
    facts["report_version_id"] = report_version_id
    stmt = (
        insert(CashFlowStatementFacts)
        .values(**facts)
        .on_conflict_do_update(
            constraint="uq_cash_flow_statement_facts_ver",
            set_={k: getattr(insert(CashFlowStatementFacts).excluded, k) for k in facts if k != "report_version_id"},
        )
    )
    await db.execute(stmt)
    await db.flush()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

async def upsert_metric(
    db: AsyncSession,
    *,
    stock_id: int,
    report_version_id: int | None,
    metric_key: str,
    value: float | None,
    unit: str | None,
    period_type: str | None,
    calc_method: str,
    source: str,
    quality_status: str,
    as_of: datetime,
) -> None:
    values = {
        "stock_id": stock_id,
        "report_version_id": report_version_id,
        "metric_key": metric_key,
        "value": value,
        "unit": unit,
        "period_type": period_type,
        "calc_method": calc_method,
        "source": source,
        "quality_status": quality_status,
        "as_of": as_of,
    }
    # note: unique is (stock_id, report_version_id, metric_key); report_version_id
    # is dropped from the conflict target via constraint and only value metadata
    # that is safe to overwrite is updated.
    stmt = (
        insert(FinancialMetric)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_financial_metric_key",
            set_={
                "value": insert(FinancialMetric).excluded.value,
                "unit": insert(FinancialMetric).excluded.unit,
                "period_type": insert(FinancialMetric).excluded.period_type,
                "calc_method": insert(FinancialMetric).excluded.calc_method,
                "quality_status": insert(FinancialMetric).excluded.quality_status,
                "as_of": insert(FinancialMetric).excluded.as_of,
            },
        )
    )
    await db.execute(stmt)
    await db.flush()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

async def list_report_versions(
    db: AsyncSession,
    stock_id: int,
    *,
    limit: int = 32,
    source: str | None = None,
) -> list[FinancialReportVersion]:
    stmt = (
        select(FinancialReportVersion)
        .where(FinancialReportVersion.stock_id == stock_id)
        .order_by(FinancialReportVersion.end_date.desc())
        .limit(limit)
    )
    if source:
        stmt = stmt.where(FinancialReportVersion.source == source)
    return list((await db.execute(stmt)).scalars().all())


async def get_latest_report_version(
    db: AsyncSession,
    stock_id: int,
    *,
    source: str | None = None,
) -> FinancialReportVersion | None:
    stmt = (
        select(FinancialReportVersion)
        .where(FinancialReportVersion.stock_id == stock_id)
        .order_by(FinancialReportVersion.end_date.desc())
        .limit(1)
    )
    if source:
        stmt = stmt.where(FinancialReportVersion.source == source)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_report_version(
    db: AsyncSession, report_version_id: int
) -> FinancialReportVersion | None:
    stmt = select(FinancialReportVersion).where(
        FinancialReportVersion.id == report_version_id
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_statement_facts(
    db: AsyncSession, report_version_id: int
) -> tuple[IncomeStatementFacts | None, BalanceSheetFacts | None, CashFlowStatementFacts | None]:
    income = (
        await db.execute(
            select(IncomeStatementFacts).where(
                IncomeStatementFacts.report_version_id == report_version_id
            )
        )
    ).scalar_one_or_none()
    balance = (
        await db.execute(
            select(BalanceSheetFacts).where(
                BalanceSheetFacts.report_version_id == report_version_id
            )
        )
    ).scalar_one_or_none()
    cashflow = (
        await db.execute(
            select(CashFlowStatementFacts).where(
                CashFlowStatementFacts.report_version_id == report_version_id
            )
        )
    ).scalar_one_or_none()
    return income, balance, cashflow


async def list_metrics(
    db: AsyncSession, stock_id: int, *, metric_keys: list[str] | None = None
) -> list[FinancialMetric]:
    stmt = (
        select(FinancialMetric)
        .where(FinancialMetric.stock_id == stock_id)
        .order_by(FinancialMetric.report_version_id)
    )
    if metric_keys:
        stmt = stmt.where(FinancialMetric.metric_key.in_(metric_keys))
    return list((await db.execute(stmt)).scalars().all())


async def list_metrics_series(
    db: AsyncSession,
    stock_id: int,
    *,
    metric_keys: list[str] | None = None,
) -> list[tuple[FinancialMetric, date]]:
    """Return (metric, report end_date) pairs ordered by end_date ascending.

    Used to build time series charts of a metric across report periods.
    Only metrics tied to a report version are returned.
    """
    stmt = (
        select(FinancialMetric, FinancialReportVersion.end_date)
        .join(
            FinancialReportVersion,
            FinancialMetric.report_version_id == FinancialReportVersion.id,
        )
        .where(FinancialMetric.stock_id == stock_id)
        .order_by(FinancialReportVersion.end_date.asc())
    )
    if metric_keys:
        stmt = stmt.where(FinancialMetric.metric_key.in_(metric_keys))
    return list((await db.execute(stmt)).all())
