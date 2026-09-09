"""Financial statements, metrics and valuation endpoints.

Mounted at ``/api/v1/exchanges/{exchange}/stocks/{symbol}`` (see config in
``api/v1/__init__.py``).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import DbDep
from app.schemas.financial import (
    FinancialStatementsOut,
    FinancialSummaryOut,
    MetricHistoryOut,
    ValuationHistoryOut,
)
from app.services import financial_service

router = APIRouter()


@router.get("/{symbol}/financial-summary", response_model=FinancialSummaryOut)
async def get_financial_summary(exchange: str, symbol: str, db: DbDep) -> FinancialSummaryOut:
    try:
        return await financial_service.get_financial_summary(db, exchange, symbol)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{symbol}/financial-statements", response_model=FinancialStatementsOut)
async def get_financial_statements(
    exchange: str,
    symbol: str,
    db: DbDep,
    period_count: Annotated[int, Query(ge=1, le=40)] = 8,
) -> FinancialStatementsOut:
    try:
        return await financial_service.get_financial_statements(
            db, exchange, symbol, period_count=period_count
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get(
    "/{symbol}/financial-metrics/history",
    response_model=list[MetricHistoryOut],
)
async def get_financial_metrics_history(
    exchange: str,
    symbol: str,
    db: DbDep,
    metric_keys: Annotated[str, Query(...)] = "roe,gross_margin,net_margin,debt_to_asset",
) -> list[MetricHistoryOut]:
    keys = [k.strip() for k in metric_keys.split(",") if k.strip()]
    try:
        return await financial_service.get_metrics_history(db, exchange, symbol, metric_keys=keys)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{symbol}/valuation-history", response_model=ValuationHistoryOut)
async def get_valuation_history(
    exchange: str,
    symbol: str,
    db: DbDep,
    metric: str = "pe_ttm",
    range: str = "3y",
) -> ValuationHistoryOut:
    if range not in ("1y", "3y", "5y"):
        raise HTTPException(status_code=422, detail="range must be one of 1y/3y/5y")
    try:
        return await financial_service.get_valuation_history(
            db, exchange, symbol, metric=metric, range_=range
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
