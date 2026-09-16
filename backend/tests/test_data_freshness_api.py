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
            "degraded_calendar": False,
            "apply": kw.get("apply", True),
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
