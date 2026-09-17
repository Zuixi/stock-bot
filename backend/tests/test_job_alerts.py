"""任务失败登记 `job:failures`（把"job 静默失败"变成 /market/data-freshness 可见）。

契约要点：`record_job_failure` 是在 job 的 `except` 块里被调用的，它自己绝不能抛——
Redis 挂了不能让"已处理的 job 失败"升级成"未处理的崩溃"。因此所有 Redis 异常静默
吞掉（log + return），读路径同样降级为空列表。
"""

from typing import Any

import pytest
import redis

from app.services import job_alert_service as jas


class _FakeCache:
    """只实现 job_alert_service 用到的 CacheClient 表面（get/set）。"""

    def __init__(self, *, explode: bool = False) -> None:
        self._explode = explode
        self.data: dict[str, Any] = {}
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any | None:
        if self._explode:
            raise redis.exceptions.ConnectionError("redis down")
        return self.data.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if self._explode:
            raise redis.exceptions.ConnectionError("redis down")
        self.data[key] = value
        self.sets.append((key, value, ttl))


async def test_record_and_list_job_failure() -> None:
    cache = _FakeCache()
    await jas.record_job_failure("share_float_daily", "InterfaceError('boom')", cache=cache)
    out = await jas.list_job_failures(cache=cache)
    assert len(out) == 1
    assert out[0]["job_id"] == "share_float_daily"
    assert "boom" in out[0]["error"]
    # `at` 是上海时区 ISO8601（前端零解析直显）
    assert out[0]["at"].startswith("20") and "T" in out[0]["at"]


async def test_record_keeps_one_entry_per_job_newest_first() -> None:
    """同一 job 重复失败只保留最新一条（对应 Redis hash 的 field 语义：1 job 1 条）。"""
    cache = _FakeCache()
    await jas.record_job_failure("a_job", "first", cache=cache)
    await jas.record_job_failure("b_job", "other", cache=cache)
    await jas.record_job_failure("a_job", "second", cache=cache)
    out = await jas.list_job_failures(cache=cache)
    assert [e["job_id"] for e in out] == ["a_job", "b_job"]
    assert "second" in out[0]["error"]
    assert "first" not in out[0]["error"]


async def test_failures_key_has_7_day_ttl() -> None:
    cache = _FakeCache()
    await jas.record_job_failure("a_job", "boom", cache=cache)
    assert cache.sets and cache.sets[0][0] == jas.JOB_FAILURES_KEY
    assert cache.sets[0][2] == 604800  # 7 天


async def test_redis_outage_never_raises() -> None:
    """告警不能反过来打断 job：record 静默、list 降级为空。"""
    down = _FakeCache(explode=True)
    await jas.record_job_failure("a_job", "boom", cache=down)  # 不抛
    assert await jas.list_job_failures(cache=down) == []


async def test_list_job_failures_honours_limit_and_skips_garbage() -> None:
    cache = _FakeCache()
    for i in range(5):
        await jas.record_job_failure(f"job_{i}", "boom", cache=cache)
    assert len(await jas.list_job_failures(cache=cache)) == 5
    assert len(await jas.list_job_failures(limit=2, cache=cache)) == 2
    cache.data[jas.JOB_FAILURES_KEY] = "not-a-list"
    assert await jas.list_job_failures(cache=cache) == []


# ---------------------------------------------------------------- 接线契约


async def test_freshness_endpoint_exposes_failed_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败登记只有出现在巡检端点里才有价值（否则又是一条躺在 Redis 里的日志）。"""
    from app.api.v1 import market_data as api

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        return {
            "as_of": "2026-09-17",
            "expected_latest": "2026-09-16",
            "expected_days": 10,
            "universe": 5546,
            "quotes_symbols_latest": 5485,
            "degraded_calendar": False,
            "apply": False,
            "domains": {},
        }

    async def _list(limit: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "job_id": "share_float_daily",
                "error": "InterfaceError('boom')",
                "at": "2026-09-17T17:30:01+08:00",
            }
        ]

    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    monkeypatch.setattr(jas, "list_job_failures", _list)
    out = await api.get_data_freshness()
    assert out.apply is False
    assert [f.job_id for f in out.failed_jobs] == ["share_float_daily"]
    assert "boom" in out.failed_jobs[0].error


async def test_freshness_endpoint_survives_redis_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """只读巡检不得因 Redis 挂掉而 500：failed_jobs 降级为空列表。"""
    from app.api.v1 import market_data as api

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        return {
            "as_of": "2026-09-17",
            "expected_latest": "2026-09-16",
            "expected_days": 10,
            "universe": 5546,
            "quotes_symbols_latest": 5485,
            "degraded_calendar": False,
            "apply": False,
            "domains": {},
        }

    async def _down() -> Any:
        raise redis.exceptions.ConnectionError("redis down")

    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    monkeypatch.setattr(jas, "get_redis_pool", _down)
    out = await api.get_data_freshness()
    assert out.failed_jobs == []


async def test_every_registered_scheduler_job_is_alert_wired() -> None:
    """回归防护：新增 scheduler job 时忘记接告警 = 又一次静默失败。

    契约是"注册 id 必须在 jobs.py 里出现"——共享 callable（reconcile 3 触发点、SSE 2 tick）
    也会以 id 字符串字面量出现在 jobs.py 的登记里，所以这条断言对两种情况都成立。
    """
    import pathlib

    from app.scheduler.runner import create_scheduler

    source = pathlib.Path(jas.__file__).parents[1].joinpath("scheduler/jobs.py").read_text()
    scheduler = create_scheduler()
    scheduler.start()
    try:
        unwired = [job.id for job in scheduler.get_jobs() if f'"{job.id}"' not in source]
    finally:
        scheduler.shutdown(wait=False)
    assert unwired == []
