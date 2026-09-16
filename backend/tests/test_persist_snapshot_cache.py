"""persist_snapshot 的派生缓存失效契约（plans/2026-09-17-data-sync-self-healing.md L4）。

get_calendar 的 Redis 键 `market:limit-up:calendar:{days}` 会固化旧结果集
（非空即缓存、"空结果不缓存"防不住**旧**结果）——实测 9/17 补完两日数据后
日历端点仍只返回 1 点。失效必须内聚在 persist_snapshot 内：任何调用方
（scheduler 定时 / worker 手动 / 对账循环）都不依赖"记得清缓存"。
"""

from datetime import date
from typing import Any

import pytest

from app.services import limit_up_service as svc

AS_OF = date(2026, 9, 16)


def _healthy_snap() -> dict[str, Any]:
    kpis = {
        "zt_count": 91,
        "dt_count": 4,
        "zb_count": 14,
        "broken_rate": 0.1333,
        "yzt_avg_pct": 2.5,
        "promo_1to2": 0.28,
        "promo_1to2_n": 33,
        "promo_2to3": 0.75,
        "promo_2to3_n": 4,
        "max_streak": 6,
    }
    return {
        "as_of": AS_OF,
        "is_partial": False,
        "degraded_reason": None,
        "source": "local_calc",
        "kpis": kpis,
        "echelons": [{"stocks": [{"symbol": "000993.SZ"}]}],
    }


@pytest.fixture
def _persist_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """替身环境：健康快照 + 记录型 repo/失效钩子（无 DB/无 Redis）。"""
    calls: dict[str, Any] = {"upserts": [], "invalidations": 0}

    async def _fake_snapshot(cache: Any, as_of: date | None = None, **kw: Any) -> dict[str, Any]:
        return _healthy_snap()

    async def _fake_upsert(db: Any, row: dict[str, Any]) -> None:
        calls["upserts"].append(row)

    async def _fake_invalidate() -> int:
        calls["invalidations"] += 1
        return 1

    monkeypatch.setattr(svc, "get_snapshot", _fake_snapshot)
    monkeypatch.setattr(svc.limit_up_repo, "upsert_sentiment_daily", _fake_upsert)
    monkeypatch.setattr(svc, "_invalidate_calendar_cache", _fake_invalidate)
    return calls


async def test_ok_persist_invalidates_calendar_cache(_persist_env: dict[str, Any]) -> None:
    result = await svc.persist_snapshot(db=object(), cache=None, as_of=AS_OF)
    assert result["status"] == "ok"
    assert len(_persist_env["upserts"]) == 1
    assert _persist_env["invalidations"] == 1


async def test_skipped_persist_does_not_invalidate(
    monkeypatch: pytest.MonkeyPatch, _persist_env: dict[str, Any]
) -> None:
    snap = _healthy_snap()
    snap["degraded_reason"] = "price_limits_missing"

    async def _degraded(cache: Any, as_of: date | None = None, **kw: Any) -> dict[str, Any]:
        return snap

    monkeypatch.setattr(svc, "get_snapshot", _degraded)
    result = await svc.persist_snapshot(db=object(), cache=None, as_of=AS_OF)
    assert result["status"] == "skipped"
    assert _persist_env["upserts"] == []
    assert _persist_env["invalidations"] == 0
