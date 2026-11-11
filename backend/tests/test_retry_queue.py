"""R1 重试队列：入队/到期/退避/上限进 DLQ；幂等 job 白名单。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.scheduler import jobs
from app.services import retry_queue_service as rq


class _FakeRedis:
    """只实现重试服务用到的最小 Redis 面（dict 语义，时间由参数控制）。"""

    def __init__(self) -> None:
        self.z: dict[str, float] = {}
        self.h: dict[str, dict[str, Any]] = {}
        self.ttl: dict[str, int] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.z.update({f"{key}:{k}": v for k, v in mapping.items()})
        return len(mapping)

    async def zrangebyscore(
        self, key: str, minimum: Any, maximum: Any, *, start: int = 0, num: int | None = None
    ) -> list[str]:
        prefix = f"{key}:"
        items = sorted(
            ((k[len(prefix) :], v) for k, v in self.z.items() if k.startswith(prefix)),
            key=lambda kv: kv[1],
        )
        out = [k for k, score in items if float(minimum) <= score <= float(maximum)]
        return out[start : start + num] if num is not None else out[start:]

    async def zrem(self, key: str, *members: str) -> int:
        removed = 0
        for member in members:
            removed += 1 if self.z.pop(f"{key}:{member}", None) is not None else 0
        return removed

    async def zscore(self, key: str, member: str) -> float | None:
        return self.z.get(f"{key}:{member}")

    async def hset(self, key: str, field: str, value: Any) -> int:
        self.h.setdefault(key, {})[field] = value
        return 1

    async def hget(self, key: str, field: str) -> Any | None:
        return self.h.get(key, {}).get(field)

    async def hdel(self, key: str, *fields: str) -> int:
        return sum(1 for f in fields if self.h.get(key, {}).pop(f, None) is not None)

    async def hgetall(self, key: str) -> dict[str, Any]:
        return dict(self.h.get(key, {}))

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True


async def test_enqueue_then_due_then_clear() -> None:
    client = _FakeRedis()
    await rq.enqueue("share_float_daily", client=client)
    assert await rq.attempts_of("share_float_daily", client=client) == 1
    assert await rq.due_jobs(now=datetime.now(UTC) + timedelta(seconds=61), client=client) == [
        "share_float_daily"
    ]
    await rq.clear("share_float_daily", client=client)
    assert await rq.attempts_of("share_float_daily", client=client) == 0
    assert await rq.is_queued("share_float_daily", client=client) is False


async def test_backoff_and_dead_letter_after_max_attempts() -> None:
    client = _FakeRedis()
    await rq.enqueue("daily_basic_backfill", client=client)  # attempt hash = 1
    outcomes: list[str] = []
    for _ in range(rq.MAX_ATTEMPTS):
        outcomes.append(await rq.retry_or_dead("daily_basic_backfill", "boom", client=client))
    assert outcomes[:4] == ["retry"] * 4
    assert outcomes[4] == "dead"
    dead = await rq.list_dead(client=client)
    assert dead and dead[0]["job_id"] == "daily_basic_backfill"
    assert dead[0]["attempts"] == rq.MAX_ATTEMPTS + 1


async def test_due_respects_score_window() -> None:
    client = _FakeRedis()
    await rq.enqueue("dragon_tiger_daily", client=client)
    now = datetime.now(UTC)
    assert await rq.due_jobs(now=now, client=client) == []  # 还没到 1 分钟
    assert await rq.due_jobs(now=now + timedelta(seconds=61), client=client) == [
        "dragon_tiger_daily"
    ]


def test_retryable_jobs_are_serializable_and_exclude_high_frequency() -> None:
    ids = set(jobs.RETRYABLE_JOBS)
    # 高频轮询不进重试队列（下一 tick 就是重试）
    for noisy in (
        "sse_trade_hours",
        "sse_trade_close",
        "sector_moneyflow_poll",
        "announcements_poll",
        "intraday_sentiment_poll",
        "retry_dispatcher",
    ):
        assert noisy not in ids
    # 每个 id 都必须是 jobs.py 里的函数（防止拼错 id 变成永远 dead letter）
    for job_id, fn in jobs.RETRYABLE_JOBS.items():
        assert callable(fn), job_id
