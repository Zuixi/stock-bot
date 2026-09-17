"""任务失败登记 `job:failures`（把"job 静默失败"变成 /market/data-freshness 可见）。

契约要点：
- `record_job_failure` 在 job 的 `except` 块里被调用，它自己绝不能抛——Redis 挂了不能让
  "已处理的 job 失败"升级成"未处理的崩溃"。所有 Redis 异常静默吞掉（log + return），
  读路径同样降级为空列表。
- 登记表用 Redis hash（``HSET job:failures <job_id>`` + ``EXPIRE 604800``）：每条失败是一次
  按字段原子的写，不做"整表读改写"，否则两个 job 并发失败会互相覆盖、丢一条告警。
- TTL 是 key 级不是条目级 → 读时再按 7 天保留窗口过滤旧条目。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import redis

from app.services import job_alert_service as jas

_SH_TZ = ZoneInfo("Asia/Shanghai")


class _FakeRedis:
    """假 raw redis：只实现登记表用到的 hash 命令（hset/expire/hgetall）。

    刻意**不提供** get/set：登记必须是一次按字段原子的 HSET，不能是"整表读改写 JSON
    列表"（那种实现会在这里 AttributeError → 被静默吞掉 → 条目丢失，测试因此变红）。
    """

    def __init__(self, *, explode: bool = False) -> None:
        self._explode = explode
        self.hash: dict[str, str] = {}
        self.expires: list[tuple[str, int]] = []

    def _check(self) -> None:
        if self._explode:
            raise redis.exceptions.ConnectionError("redis down")

    async def hset(self, key: str, field: str, value: str) -> int:
        self._check()
        self.hash[field] = value
        return 1

    async def expire(self, key: str, ttl: int) -> bool:
        self._check()
        self.expires.append((key, ttl))
        return True

    async def hgetall(self, key: str) -> dict[str, str]:
        self._check()
        return dict(self.hash)


def _stored(cache: _FakeRedis, job_id: str) -> dict[str, Any]:
    return json.loads(cache.hash[job_id])


async def test_record_and_list_job_failure() -> None:
    cache = _FakeRedis()
    await jas.record_job_failure("share_float_daily", "InterfaceError('boom')", client=cache)
    out = await jas.list_job_failures(client=cache)
    assert len(out) == 1
    assert out[0]["job_id"] == "share_float_daily"
    assert "boom" in out[0]["error"]
    # `at` 是上海时区 ISO8601 且带 6 位微秒（见 test_at_has_microsecond_precision）
    assert out[0]["at"].startswith("20") and "T" in out[0]["at"]


async def test_record_keeps_one_entry_per_job_newest_first() -> None:
    """同一 job 重复失败只保留最新一条（对应 Redis hash 的 field 语义：1 job 1 条）。"""
    cache = _FakeRedis()
    await jas.record_job_failure("a_job", "first", client=cache)
    await jas.record_job_failure("b_job", "other", client=cache)
    await jas.record_job_failure("a_job", "second", client=cache)
    assert set(cache.hash) == {"a_job", "b_job"}  # 每个 job 一个 field，无列表整表重写
    out = await jas.list_job_failures(client=cache)
    assert [e["job_id"] for e in out] == ["a_job", "b_job"]
    assert "second" in out[0]["error"]
    assert "first" not in out[0]["error"]


async def test_two_concurrent_jobs_each_keep_their_entry() -> None:
    """并发失败不得互相覆盖：登记是按字段原子写，不是整表读改写。

    这条测试在旧实现（CacheClient.get → 列表前插 → set 整表）下必红：假 redis 没有
    get/set → 写入被静默吞掉 → 两个 field 都不存在。
    """
    cache = _FakeRedis()
    await jas.record_job_failure("sse_trade_hours", "sse boom", client=cache)
    await jas.record_job_failure("sector_moneyflow_poll", "sector boom", client=cache)
    assert set(cache.hash) == {"sse_trade_hours", "sector_moneyflow_poll"}


async def test_failure_registry_key_and_ttl() -> None:
    cache = _FakeRedis()
    await jas.record_job_failure("a_job", "boom", client=cache)
    assert cache.expires == [(jas.JOB_FAILURES_KEY, 604800)]  # 7 天
    assert jas.JOB_FAILURES_KEY == "job:failures"


async def test_record_caps_error_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """`error` 必须封顶：SQLAlchemy StatementError 的 str() 会内嵌整条 6000 行 INSERT。"""
    cache = _FakeRedis()
    await jas.record_job_failure("a_job", "x" * 6000, client=cache)
    assert len(_stored(cache, "a_job")["error"]) == jas.MAX_ERROR_CHARS == 500


async def test_at_has_microsecond_precision(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一秒内的两次失败必须可排序：isoformat 省略为零的微秒会让顺序不确定。"""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2026, 9, 17, 17, 30, 1, 0, tzinfo=tz)

    monkeypatch.setattr(jas, "datetime", _FrozenDatetime)
    cache = _FakeRedis()
    await jas.record_job_failure("a_job", "boom", client=cache)
    assert _stored(cache, "a_job")["at"] == "2026-09-17T17:30:01.000000+08:00"


async def test_list_skips_entries_older_than_retention() -> None:
    """TTL 是 key 级：每次写都刷新 7 天，读时必须按 now-7d 过滤旧条目。"""
    cache = _FakeRedis()
    now = datetime.now(_SH_TZ)
    old = (now - timedelta(days=7, hours=1)).isoformat(timespec="microseconds")
    fresh = (now - timedelta(hours=1)).isoformat(timespec="microseconds")
    cache.hash["old_job"] = json.dumps({"job_id": "old_job", "error": "x", "at": old})
    cache.hash["fresh_job"] = json.dumps({"job_id": "fresh_job", "error": "x", "at": fresh})
    out = await jas.list_job_failures(client=cache)
    assert [e["job_id"] for e in out] == ["fresh_job"]


async def test_list_orders_within_same_second_by_microseconds() -> None:
    cache = _FakeRedis()
    base = datetime.now(_SH_TZ).replace(microsecond=1)
    cache.hash["a_job"] = json.dumps(
        {"job_id": "a_job", "error": "x", "at": base.isoformat(timespec="microseconds")}
    )
    cache.hash["b_job"] = json.dumps(
        {
            "job_id": "b_job",
            "error": "x",
            "at": base.replace(microsecond=2).isoformat(timespec="microseconds"),
        }
    )
    out = await jas.list_job_failures(client=cache)
    assert [e["job_id"] for e in out] == ["b_job", "a_job"]


async def test_redis_outage_never_raises() -> None:
    """告警不能反过来打断 job：record 静默、list 降级为空。"""
    down = _FakeRedis(explode=True)
    await jas.record_job_failure("a_job", "boom", client=down)  # 不抛
    assert await jas.list_job_failures(client=down) == []


async def test_list_job_failures_honours_limit_and_skips_garbage() -> None:
    cache = _FakeRedis()
    for i in range(5):
        await jas.record_job_failure(f"job_{i}", "boom", client=cache)
    assert len(await jas.list_job_failures(client=cache)) == 5
    assert len(await jas.list_job_failures(limit=2, client=cache)) == 2
    cache.hash["not_json"] = "not-json"
    cache.hash["not_dict"] = json.dumps(["nope"])
    cache.hash["missing_fields"] = json.dumps({"job_id": "only"})
    assert {e["job_id"] for e in await jas.list_job_failures(client=cache)} == {
        f"job_{i}" for i in range(5)
    }


# ---------------------------------------------------------------- 接线契约


async def test_share_float_job_failure_lands_in_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """功能级接线证明（不是源码 grep）：job 真炸时登记表真出现该 job 的条目。

    旧实现（CacheClient.get/set 整表列表）在这里必红：假 redis 没有 get/set →
    登记被静默吞掉 → field 不存在。
    """
    from app.scheduler import jobs

    cache = _FakeRedis()

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _boom(db: Any) -> dict[str, Any]:
        raise RuntimeError("InterfaceError: the number of query arguments cannot exceed 32767")

    async def _pool() -> Any:
        return cache

    monkeypatch.setattr(jas, "get_redis_pool", _pool)
    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.market_data_service.ingest_share_floats", _boom)
    monkeypatch.setattr(jobs, "_is_workday", lambda: True)

    await jobs.share_float_daily_job()

    assert "share_float_daily" in cache.hash
    entry = _stored(cache, "share_float_daily")
    assert "32767" in entry["error"]
    assert cache.expires == [(jas.JOB_FAILURES_KEY, 604800)]


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

    async def _list(limit: int = 20, **kw: Any) -> list[dict[str, Any]]:
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
    注意：这条 grep 测试单独看会被默认参数（`_collect_sse_snapshots(job_ids="sse_trade_hours")`）
    等字面量蒙混过关，真正的行为保证由 test_share_float_job_failure_lands_in_registry 承担。
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
