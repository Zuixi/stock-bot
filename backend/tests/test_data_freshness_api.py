"""GET /market/data-freshness 契约（plans/2026-09-17-data-sync-self-healing.md L5）。

只读巡检：复用 reconcile 的 apply=False 路径，绝不触发任何回补——把"静默缺数据"
变成"端点可见"。直测 handler（仓库无 TestClient 惯例，monkeypatch 风格）。
"""

from typing import Any

import pytest

from app.api.v1 import market_data as api


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
            "as_of": "2026-09-17",
            "expected_latest": "2026-09-16",
            "expected_days": 10,
            "universe": 5546,
            "quotes_symbols_latest": 5485,
            "degraded_calendar": False,
            "apply": kw.get("apply", True),
            "adj_factor_repaired": None,
            "domains": {
                "daily_quotes": {
                    "latest_in_db": "2026-09-16",
                    "missing_days": [],
                    "partial_days": [],
                    "refetched": [],
                    "status": "ok",
                }
            },
        }

    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    return {"calls": calls}


async def test_data_freshness_is_dry_run(_env: dict[str, Any]) -> None:
    out = await api.get_data_freshness()
    assert _env["calls"] == [{"apply": False}]
    assert out.apply is False
    assert out.domains["daily_quotes"].status == "ok"
    assert out.domains["daily_quotes"].latest_in_db == "2026-09-16"
    assert out.adj_factor_repaired is None  # 只读巡检：不补因子


async def test_data_freshness_exposes_universe_lag(_env: dict[str, Any]) -> None:
    """threshold 的分母取自 stocks 表，该表冻结时巡检恒为 ok —— 两个数必须并排可见。

    实测 5513(名录) vs 5485(当日实际有行情的股票数)：缺 65 只（1.2%）远够不着
    0.8 容差，只看 universe/status 永远发现不了名录滞后。
    """
    out = await api.get_data_freshness()
    assert out.universe == 5546
    assert out.quotes_symbols_latest == 5485
    assert out.universe - out.quotes_symbols_latest == 61


def test_data_freshness_schema_exposes_adj_factor_repair() -> None:
    """对账补灌的新副作用字段必须经 DataFreshnessOut 透出，而非被 extra 静默丢弃。"""
    from app.schemas.reconciliation import DataFreshnessOut

    out = DataFreshnessOut(
        **{
            "as_of": "2026-09-17",
            "expected_latest": "2026-09-16",
            "expected_days": 10,
            "universe": 5546,
            "quotes_symbols_latest": 5485,
            "degraded_calendar": False,
            "apply": True,
            "domains": {},
            "adj_factor_repaired": {
                "days": ["2026-09-16"],
                "rows": 3,
                "failed": 0,
                "error": None,
            },
        }
    )
    assert out.adj_factor_repaired is not None
    assert out.adj_factor_repaired.rows == 3
    assert out.adj_factor_repaired.days == ["2026-09-16"]


def test_adj_factor_repair_out_defaults_are_safe() -> None:
    """字段缺省不得把端点打成 500：``error`` 默认 None，计数默认 0（M3）。"""
    from app.schemas.reconciliation import AdjFactorRepairOut

    out = AdjFactorRepairOut(days=["2026-09-16"], rows=3, failed=0)  # 未来 producer 省略 error
    assert out.error is None
    assert out.unfilled == 0
    assert out.remaining == 0
