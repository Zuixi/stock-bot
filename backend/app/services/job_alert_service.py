"""任务失败登记——把"job 静默失败"变成 /market/data-freshness 可见。

背景：APScheduler 日志里的 "Job executed successfully" 只说明 **job 函数返回了**；
函数内部 `except Exception: logger.exception(...)` 吞掉的失败在编排层完全不可见
（实测 share_float 因 asyncpg 参数上限崩了 13 天，无人发现）。scheduler 的每个
`except` 块因此额外调用 :func:`record_job_failure`。

存储选型：Redis hash ``job:failures`` + ``HSET <job_id> <json>`` + ``EXPIRE 604800``。
不用 "单 key JSON 列表的读改写"：那种写法是 read → prepend → write 三步，两个不同 job
并发失败（SSE 每 10 分钟、板块轮询每 5 分钟互不相同，``max_instances=1`` 只约束单个
job）会互相覆盖，丢掉一条——甚至可能丢最新的那条。hash 的每次 HSET 只写自己的 field，
天然免掉这一步，也正是 Task 4 brief 给的接口。代价：TTL 是 key 级而非条目级，所以读路径
额外按 ``now - 7 天`` 过滤（见 :func:`list_job_failures`）。

`error` 在**写入边界**就截断到 :data:`MAX_ERROR_CHARS`：SQLAlchemy ``StatementError`` 的
``str()`` 会把整条语句内嵌进来（本次修的正是 6000 行 INSERT），而失败登记会被公开只读
端点返回。

**告警绝不能反过来打断 job**：本模块所有函数吞掉一切异常（log + 降级），因为
record 是在 job 的 `except` 块里调用的，抛错会把"已处理的失败"升级成"未处理的崩溃"。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from app.core.redis import get_redis_pool

logger = logging.getLogger(__name__)

# 失败登记表：一个 hash，field = job_id（1 job 1 条，最新覆盖），7 天 TTL 自动过期
JOB_FAILURES_KEY = "job:failures"
JOB_FAILURES_TTL = 604800  # 7 天
# 单条 error 的字符上限：repr(StatementError) 会内嵌整条 6000 行 INSERT（公开端点最多回 20 条）
MAX_ERROR_CHARS = 500
# 读时保留窗口：TTL 是 key 级，每次写刷新，不按条目过滤会让 6 天前的条目续命到 13 天
RETENTION = timedelta(days=7)

_SH_TZ = ZoneInfo("Asia/Shanghai")


async def record_job_failure(job_id: str, error: str, *, client: Redis | None = None) -> None:
    """登记一次 job 失败（同一 job 只保留最新一条）。任何异常都静默——告警不得打断 job。"""
    entry = {
        "job_id": job_id,
        "error": error[:MAX_ERROR_CHARS],
        # 显式 timespec：isoformat() 在微秒为 0 时省略小数位，同一秒内的顺序就不确定了
        "at": datetime.now(_SH_TZ).isoformat(timespec="microseconds"),
    }
    try:
        redis_client = client if client is not None else await get_redis_pool()
        # HSET 只碰自己的 field（无读改写），随后刷新整表 TTL
        await redis_client.hset(JOB_FAILURES_KEY, job_id, json.dumps(entry, ensure_ascii=False))
        await redis_client.expire(JOB_FAILURES_KEY, JOB_FAILURES_TTL)
    except Exception:  # noqa: BLE001 — Redis 挂了/序列化失败都只能告警不能抛
        logger.warning("job failure alert dropped (job_id=%s)", job_id, exc_info=True)


async def list_job_failures(
    limit: int = 20, *, client: Redis | None = None
) -> list[dict[str, Any]]:
    """最近失败的 job（按时间降序，最多 ``limit`` 条）。Redis 不可用 → 空列表。"""
    try:
        redis_client = client if client is not None else await get_redis_pool()
        raw = await redis_client.hgetall(JOB_FAILURES_KEY)
    except Exception:  # noqa: BLE001 — 只读端点不得因 Redis 挂掉而 500
        logger.warning("job failure list unavailable", exc_info=True)
        return []
    if not isinstance(raw, dict):
        return []
    # 上海时区无夏令时：固定宽度的 ISO8601 字符串字典序 == 时间序
    cutoff = (datetime.now(_SH_TZ) - RETENTION).isoformat(timespec="microseconds")
    entries: list[dict[str, Any]] = []
    for value in raw.values():
        try:
            entry = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(entry, dict) or not {"job_id", "error", "at"} <= set(entry):
            continue
        at = str(entry["at"])
        if at < cutoff:  # TTL 是 key 级：旧条目在 TTL 被刷新后仍可能存活，读时再过滤
            continue
        # 读时再截一次：修复前写入的条目可能带着整条语句（防御公开端点）
        entry["error"] = str(entry["error"])[:MAX_ERROR_CHARS]
        entries.append(entry)
    entries.sort(key=lambda e: str(e["at"]), reverse=True)
    return entries[:limit]
