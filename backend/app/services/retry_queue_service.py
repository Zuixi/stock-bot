"""失败任务重试队列 + dead letter（R1）.

设计（见 ``plans/2026-11-11-reliability-and-research-quality.md`` §R1）：

- 失败 → ``ZSET job:retry``（score = next_attempt_at，member = job_id）
- scheduler 每 60s 取到期项重跑；成功 → 清空 attempt；失败 → 指数退避
- 超过 :data:`MAX_ATTEMPTS` → ``HASH job:retry:dead`` + ``job:failures`` 告警
- **只登记幂等 job**：列表由 ``scheduler/jobs.RETRYABLE_JOBS`` 决定；高频轮询不进队列

为什么不用表：重试是**短期状态**（最长 3 小时），Redis 的原子操作足够，且失败本身
已经落在 ``job:failures``；不需要再建一张"重试历史"表。

Redis 不可用 → 所有函数吞异常（与 ``job_alert_service`` 同一纪律）：重试是加法，
不能把"已处理的失败"升级成"未处理的崩溃"。
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis

from app.core.redis import get_redis_pool

logger = logging.getLogger(__name__)

#: 当前调用是否由重试调度器发起。job 内部 catch 后调 `_alert_job_failure` 时，
#: 据此决定"首次入队"还是"累计重试次数"（否则每次重试都会把 attempt 重置成 0）。
IN_RETRY: ContextVar[bool] = ContextVar("retry_in_retry", default=False)

RETRY_KEY = "job:retry"
ATTEMPTS_KEY = "job:retry:attempts"
DEAD_KEY = "job:retry:dead"
DEAD_TTL = 30 * 24 * 60 * 60  # 30 天：足够事后排查，不会无限涨
MAX_ATTEMPTS = 5
#: 第 N 次失败后的等待（秒）：1 / 5 / 15 / 60 / 180 分钟
BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900, 3600, 10800)


def _next_attempt(attempt: int) -> tuple[datetime, int]:
    """``attempt`` = 已失败次数（0 = 首次失败）。返回 (下次时间, 本次 attempt 值)。"""
    delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
    return datetime.now(UTC) + timedelta(seconds=delay), attempt + 1


async def enqueue(job_id: str, *, attempt: int = 0, client: Redis | None = None) -> datetime | None:
    """把 job 放进重试队列；返回下次尝试时间（Redis 不可用 → None）。"""
    try:
        redis = client if client is not None else await get_redis_pool()
        next_at, attempt_value = _next_attempt(attempt)
        await redis.zadd(RETRY_KEY, {job_id: next_at.timestamp()})
        await redis.hset(ATTEMPTS_KEY, job_id, attempt_value)
        logger.info("RETRY enqueued %s attempt=%d at=%s", job_id, attempt, next_at.isoformat())
        return next_at
    except Exception:  # noqa: BLE001 — 重试是加法，不允许反噬失败处理
        logger.warning("retry enqueue dropped (job_id=%s)", job_id, exc_info=True)
        return None


async def due_jobs(
    *, now: datetime | None = None, limit: int = 20, client: Redis | None = None
) -> list[str]:
    """取出到期 job（**先出队后执行**：执行失败会重新入队，崩溃则由 ``job:failures`` 兜底）。"""
    try:
        redis = client if client is not None else await get_redis_pool()
        ts = (now or datetime.now(UTC)).timestamp()
        # ZRANGEBYSCORE + ZREM 不是原子操作；dispatcher 是单实例（max_instances=1），
        # 重复执行最多让幂等 job 多跑一次，代价可接受；用 Lua 会在 Redis 不可用时
        # 多一条失败路径，不值得。
        ids = await redis.zrangebyscore(RETRY_KEY, "-inf", ts, start=0, num=limit)
        if ids:
            await redis.zrem(RETRY_KEY, *ids)
        return [value.decode() if isinstance(value, bytes) else str(value) for value in ids]
    except Exception:  # noqa: BLE001
        logger.warning("retry due lookup failed", exc_info=True)
        return []


async def attempts_of(job_id: str, *, client: Redis | None = None) -> int:
    try:
        redis = client if client is not None else await get_redis_pool()
        raw = await redis.hget(ATTEMPTS_KEY, job_id)
        return int(raw or 0)
    except Exception:  # noqa: BLE001
        return 0


async def is_queued(job_id: str, *, client: Redis | None = None) -> bool:
    """是否已在重试队列（供 dispatcher 判断"job 自己已重新入队，不要清状态"）。"""
    try:
        redis = client if client is not None else await get_redis_pool()
        return await redis.zscore(RETRY_KEY, job_id) is not None
    except Exception:  # noqa: BLE001
        return False


async def clear(job_id: str, *, client: Redis | None = None) -> None:
    """成功后清理重试状态（不动 ``job:failures``：最近一次失败仍需可见）。"""
    try:
        redis = client if client is not None else await get_redis_pool()
        await redis.hdel(ATTEMPTS_KEY, job_id)
        await redis.zrem(RETRY_KEY, job_id)
    except Exception:  # noqa: BLE001
        logger.warning("retry clear failed (job_id=%s)", job_id, exc_info=True)


async def retry_or_dead(job_id: str, error: str, *, client: Redis | None = None) -> str:
    """失败后再排队或进 dead letter；返回 ``retry|dead|dropped``。"""
    attempt = await attempts_of(job_id, client=client)
    if attempt >= MAX_ATTEMPTS:
        await _mark_dead(job_id, error, attempt=attempt, client=client)
        return "dead"
    next_at = await enqueue(job_id, attempt=attempt, client=client)
    return "retry" if next_at is not None else "dropped"


async def _mark_dead(job_id: str, error: str, *, attempt: int, client: Redis | None = None) -> None:
    try:
        redis = client if client is not None else await get_redis_pool()
        entry = {
            "job_id": job_id,
            "error": error[:500],
            "attempts": attempt + 1,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        await redis.hset(DEAD_KEY, job_id, json.dumps(entry, ensure_ascii=False))
        await redis.expire(DEAD_KEY, DEAD_TTL)
        await redis.hdel(ATTEMPTS_KEY, job_id)
        await redis.zrem(RETRY_KEY, job_id)
        logger.error("RETRY dead letter %s attempts=%d: %s", job_id, attempt + 1, error[:200])
    except Exception:  # noqa: BLE001
        logger.warning("retry dead-letter write failed (job_id=%s)", job_id, exc_info=True)


async def list_queued(*, limit: int = 50, client: Redis | None = None) -> list[dict[str, Any]]:
    """当前排队中的 job 与下次尝试时间（供 /ops/retries）。"""
    try:
        redis = client if client is not None else await get_redis_pool()
        items = await redis.zrange(RETRY_KEY, 0, limit - 1, withscores=True)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for member, score in items:
        job_id = member.decode() if isinstance(member, bytes) else str(member)
        out.append(
            {
                "job_id": job_id,
                "next_attempt_at": datetime.fromtimestamp(float(score), tz=UTC).isoformat(),
                "attempts": await attempts_of(job_id, client=client),
            }
        )
    return out


async def list_dead(*, limit: int = 50, client: Redis | None = None) -> list[dict[str, Any]]:
    try:
        redis = client if client is not None else await get_redis_pool()
        raw = await redis.hgetall(DEAD_KEY)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for value in raw.values():
        try:
            out.append(json.loads(value))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda item: str(item.get("at", "")), reverse=True)
    return out[:limit]
