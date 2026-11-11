"""Ops 只读端点：数据质量 / 存储健康 / 跨页一致性 (P5–P7).

为什么要内部令牌：``/api/v1`` 在生产经 Traefik 对外，这些端点会暴露表大小、死元组、
批次错误文本。默认零配置可启动（本地），生产未配 ``OPS_API_TOKEN`` 时 **fail-closed**
（503），而不是"忘了配就公开"。
"""

from __future__ import annotations

import hmac
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.api.deps import DbDep
from app.config import settings
from app.services import (
    consistency_service,
    data_expectations,
    market_day_service,
    verification_service,
)


async def require_ops_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    expected = settings.ops_api_token
    if expected:
        if not hmac.compare_digest(x_internal_token or "", expected):
            raise HTTPException(status_code=401, detail="OPS_UNAUTHORIZED")
        return
    if settings.app_env == "production":
        raise HTTPException(status_code=503, detail="OPS_TOKEN_NOT_CONFIGURED")


router = APIRouter(dependencies=[Depends(require_ops_token)])


async def _resolve_day(db: Any, value: date | None) -> date:
    if value is not None:
        return value
    latest = await market_day_service.resolve_latest_complete_day(db)
    if latest is None:
        raise HTTPException(status_code=503, detail="NO_COMPLETE_TRADING_DAY")
    return latest.day


@router.get("/data-quality")
async def data_quality(
    db: DbDep,
    date_: date | None = Query(default=None, alias="date"),
) -> dict[str, Any]:
    """声明式期望的当日结果（读时计算，不落库）。"""
    day = await _resolve_day(db, date_)
    results = await data_expectations.evaluate(db, day=day)
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    return {
        "as_of": day.isoformat(),
        "summary": {
            "total": len(results),
            "pass": counts.get("pass", 0),
            "warn": counts.get("warn", 0),
            "fail": counts.get("fail", 0),
            "error": counts.get("error", 0),
            "blocking": data_expectations.has_blocking_failure(results),
        },
        "expectations": [item.as_dict() for item in results],
    }


@router.get("/db-health")
async def db_health(db: DbDep) -> dict[str, Any]:
    """表大小 / 死元组 / 最近 vacuum / ``daily_quotes`` 分区状态。"""
    from sqlalchemy import text  # noqa: PLC0415

    rows = (
        await db.execute(
            text(
                """
                SELECT relname, n_live_tup, n_dead_tup,
                       last_autovacuum, last_autoanalyze, last_analyze,
                       pg_total_relation_size(relid) AS bytes
                FROM pg_stat_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
                LIMIT 15
                """
            )
        )
    ).all()
    tables = [
        {
            "table": row[0],
            "live_tuples": int(row[1] or 0),
            "dead_tuples": int(row[2] or 0),
            "dead_ratio": round((row[2] or 0) / max(row[1] or 0, 1), 4),
            "last_autovacuum": row[3].isoformat() if row[3] else None,
            "last_autoanalyze": row[4].isoformat() if row[4] else None,
            "last_analyze": row[5].isoformat() if row[5] else None,
            # `n_live_tup` 是**估算值**；从未 analyze 过的表可能小得离谱
            # （实测 financial_raw_records 报 612 行、真实 1.15M 行）。这里把
            # "统计过期"显式标出来，避免再拿估算值当结论。
            "stats_stale": row[4] is None and row[5] is None,
            "bytes": int(row[6] or 0),
        }
        for row in rows
    ]

    relkind = (
        await db.execute(text("SELECT relkind FROM pg_class WHERE relname = 'daily_quotes'"))
    ).scalar_one_or_none()
    # asyncpg 把 PG 的 `"char"` 类型返回成 bytes（b'p'），不是 str；
    # 不归一化会让 `relkind == "p"` 永远为 False（实测 2026-11-11：分区表被报成非分区）。
    if isinstance(relkind, (bytes, bytearray)):
        relkind = relkind.decode()
    partition_count = 0
    if relkind == "p":
        partition_count = int(
            (
                await db.execute(
                    text(
                        "SELECT count(*) FROM pg_inherits "
                        "WHERE inhparent = 'daily_quotes'::regclass"
                    )
                )
            ).scalar_one()
        )
    bloat = [row for row in tables if row["dead_ratio"] > 0.2]
    return {
        "daily_quotes": {
            "relkind": relkind,
            "partitioned": relkind == "p",
            "partitions": partition_count,
        },
        "tables": tables,
        "bloat_alerts": [row["table"] for row in bloat],
    }


@router.get("/retries")
async def retries() -> dict[str, Any]:
    """R1 重试队列状态：排队中 + dead letter（只读，不触发重试）。"""
    from app.services import retry_queue_service  # noqa: PLC0415

    return {
        "queued": await retry_queue_service.list_queued(),
        "dead": await retry_queue_service.list_dead(),
        "policy": {
            "max_attempts": retry_queue_service.MAX_ATTEMPTS,
            "backoff_seconds": list(retry_queue_service.BACKOFF_SECONDS),
        },
    }


@router.get("/verification")
async def verification(
    db: DbDep,
    date_: date | None = Query(default=None, alias="date"),
) -> dict[str, Any]:
    """最近一次**跨源**校验结果（东财 push2his 抽样比对收盘价/涨跌幅）。

    只读：不触发外呼（外呼由周六深扫尾巴的 job 执行）。没有记录时 404，
    而不是伪造一个 pass——"没校验过"和"校验通过"是两件事。
    """
    row = await verification_service.latest(db, date_)
    if row is None:
        raise HTTPException(status_code=404, detail="NO_VERIFICATION_RUN")
    return row


@router.get("/consistency")
async def consistency(
    db: DbDep,
    date_: date | None = Query(default=None, alias="date"),
) -> dict[str, Any]:
    """同一事实的两条独立路径对比（派生表 vs 底座重算）。"""
    day = await _resolve_day(db, date_)
    checks = await consistency_service.check(db, day=day)
    return {
        "as_of": day.isoformat(),
        "mismatched": [item["key"] for item in checks if item["status"] == "mismatch"],
        "checks": checks,
    }
