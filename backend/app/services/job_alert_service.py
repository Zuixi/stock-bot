"""任务失败登记——把"job 静默失败"变成 /market/data-freshness 可见。

背景：APScheduler 日志里的 "Job executed successfully" 只说明 **job 函数返回了**；
函数内部 `except Exception: logger.exception(...)` 吞掉的失败在编排层完全不可见
（实测 share_float 因 asyncpg 参数上限崩了 13 天，无人发现）。scheduler 的每个
`except` 块因此额外调用 :func:`record_job_failure`。

存储选型：`CacheClient.set/get` + 单个 JSON 列表（key ``job:failures``），而不是
Redis hash。`CacheClient` 只暴露 get/set/delete/delete_pattern/exists，没有
hset/expire/hgetall（不为告警去扩公共缓存客户端，避免别处误用）；要用 hash 就得绕过
CacheClient 直接拿裸连接池并自己写一遍降级逻辑。JSON 列表用既有 API 即可，
"1 job 1 条 + 7 天 TTL" 的语义等价于 `HSET job:failures <job_id>` + `EXPIRE 604800`。
读改写非原子：本仓库只有单个 scheduler 进程（APScheduler max_instances=1），
最坏情况丢一条告警记录，不会丢数据也不会抛错——这个代价换实现简单是对的。

**告警绝不能反过来打断 job**：本模块所有函数吞掉一切异常（log + 降级），因为
record 是在 job 的 `except` 块里调用的，抛错会把"已处理的失败"升级成"未处理的崩溃"。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.redis import CacheClient, get_redis_pool

logger = logging.getLogger(__name__)

# 失败登记表：一个 key 存 JSON 列表，1 job 1 条（最新覆盖），7 天 TTL 自动过期
JOB_FAILURES_KEY = "job:failures"
JOB_FAILURES_TTL = 604800  # 7 天
MAX_JOB_FAILURES = 50  # 列表上限：TTL 内的 job 数远小于此，防止异常情况无界增长

_SH_TZ = ZoneInfo("Asia/Shanghai")


async def _default_cache() -> CacheClient:
    return CacheClient(await get_redis_pool())


async def record_job_failure(job_id: str, error: str, *, cache: CacheClient | None = None) -> None:
    """登记一次 job 失败（同一 job 只保留最新一条）。任何异常都静默——告警不得打断 job。"""
    entry = {
        "job_id": job_id,
        "error": error,
        "at": datetime.now(_SH_TZ).isoformat(),
    }
    try:
        client = cache if cache is not None else await _default_cache()
        raw = await client.get(JOB_FAILURES_KEY)
        existing = raw if isinstance(raw, list) else []
        kept = [e for e in existing if not (isinstance(e, dict) and e.get("job_id") == job_id)]
        await client.set(JOB_FAILURES_KEY, [entry, *kept][:MAX_JOB_FAILURES], ttl=JOB_FAILURES_TTL)
    except Exception:  # noqa: BLE001 — Redis 挂了/序列化失败都只能告警不能抛
        logger.warning("job failure alert dropped (job_id=%s)", job_id, exc_info=True)


async def list_job_failures(
    limit: int = 20, *, cache: CacheClient | None = None
) -> list[dict[str, Any]]:
    """最近失败的 job（按时间降序，最多 ``limit`` 条）。Redis 不可用 → 空列表。"""
    try:
        client = cache if cache is not None else await _default_cache()
        raw = await client.get(JOB_FAILURES_KEY)
    except Exception:  # noqa: BLE001 — 只读端点不得因 Redis 挂掉而 500
        logger.warning("job failure list unavailable", exc_info=True)
        return []
    if not isinstance(raw, list):
        return []
    entries = [e for e in raw if isinstance(e, dict) and {"job_id", "error", "at"} <= set(e)]
    # 上海时区无夏令时：ISO8601 字符串字典序 == 时间序
    entries.sort(key=lambda e: str(e["at"]), reverse=True)
    return entries[:limit]
