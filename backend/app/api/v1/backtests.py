"""Single-rule event-study backtest API (spec §S2.3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, require_permissions
from app.schemas.backtest import BacktestIn, BacktestOut
from app.services import backtest_service

router = APIRouter(dependencies=[require_permissions("research:read")])


@router.post("", response_model=BacktestOut, status_code=status.HTTP_201_CREATED)
async def create_backtest(payload: BacktestIn, db: DbDep) -> BacktestOut:
    """跑一次事件研究并落库；PIT 未通过/无信号 → 422，不返回半成品."""
    try:
        run = await backtest_service.run_backtest(
            db,
            scope_key=payload.scope_key,
            rule_key=payload.rule_key,
            rule_version=payload.rule_version,
            window_from=payload.window_from,
            window_to=payload.window_to,
            horizons=payload.horizons,
            benchmark=payload.benchmark,
            split=payload.split.model_dump(by_alias=True),
        )
    except backtest_service.BacktestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return BacktestOut.model_validate(run)


@router.get("/{run_id}", response_model=BacktestOut)
async def get_backtest(run_id: int, db: DbDep) -> BacktestOut:
    run = await db.get(backtest_service.BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    return BacktestOut.model_validate(run)
