"""jobs 层对账薄封装契约（plans/2026-09-17-data-sync-self-healing.md L3/L2 触发）。

16:30/16:45 不再"只补 T-1 + exists-skip"（停摆一天=永久洞、partial 行死锁），
改为单域对账；全量对账 job 无工作日守卫——周末启动也要能补周五的洞。
"""

from typing import Any

import pytest

from app.scheduler import jobs


@pytest.fixture
def _env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {
            "domains": {
                "daily_quotes": {"status": "ok"},
                "daily_basic": {"status": "ok"},
                "price_limits": {"status": "ok"},
                "sentiment": {"status": "ok"},
            }
        }

    monkeypatch.setattr(jobs, "_is_workday", lambda: True)
    # jobs 内为函数内 import → patch 源模块属性，调用时取到替身
    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    return {"calls": calls}


async def test_quotes_job_is_single_domain_reconcile(_env: dict[str, Any]) -> None:
    await jobs.daily_quotes_backfill_job()
    assert _env["calls"] == [{"only": {"daily_quotes"}}]


async def test_basic_job_is_single_domain_reconcile(_env: dict[str, Any]) -> None:
    await jobs.daily_basic_backfill_job()
    assert _env["calls"] == [{"only": {"daily_basic"}}]


async def test_full_reconcile_job_no_scope_no_workday_guard(_env: dict[str, Any]) -> None:
    monkeypatch_none_workday = _env  # noqa: F841 — fixture already forces workday; guard absence见下
    await jobs.reconcile_market_data_job()
    assert _env["calls"] == [{"only": None}]


async def test_full_reconcile_job_runs_on_weekend(
    monkeypatch: pytest.MonkeyPatch, _env: dict[str, Any]
) -> None:
    monkeypatch.setattr(jobs, "_is_workday", lambda: False)
    await jobs.reconcile_market_data_job()
    assert _env["calls"] == [{"only": None}]  # 对账无工作日守卫：周末也要补周五的洞


def test_fetch_yesterday_helpers_removed() -> None:
    """exists-skip 语义已死——回归防护，防止"顺手"复活。"""
    assert not hasattr(jobs, "_fetch_yesterday_daily_quotes")
    assert not hasattr(jobs, "_fetch_yesterday_daily_basic")


# ---------------------------------------------------------------- worker trigger


async def test_worker_reconcile_job_type_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """market_data.fetch 队列新增 reconcile 类型：运维可随时手动触发对账。"""
    from app.schemas.task import MarketDataFetchRequest
    from app.workers import market_data_worker

    MarketDataFetchRequest(type="reconcile")  # Literal 放行

    calls: list[dict[str, Any]] = []

    class _Session:
        async def commit(self) -> None:
            return None

    class _Ctx:
        async def __aenter__(self) -> Any:
            return _Session()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"domains": {}}

    monkeypatch.setattr(market_data_worker, "async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    result = await market_data_worker._run("reconcile", {})
    assert result["status"] == "completed"
    assert calls and "only" not in calls[0]
