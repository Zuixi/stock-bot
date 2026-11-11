"""Provenance（溯源）读面：显示值 → 口径 → 来源表 → 批次 → 原始 JSONL (P7).

只读、幂等、零新表：血缘来自 ``lineage_registry``，批次来自 ``ingest_batches``，
质量来自 ``data_quality_runs``，新鲜度来自 ``market_day_service``。

性能预算：≤ 3 条聚合 SQL + 1 条批次查询 + 1 条质量查询（单指标）。
调用方（API）负责 60s 缓存；本模块不做缓存，避免"缓存把旧口径固化"。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_quality import IngestBatch
from app.services import data_expectations, lineage_registry, market_day_service

logger = logging.getLogger(__name__)


async def resolve_value(
    db: AsyncSession,
    *,
    lineage: lineage_registry.MetricLineage,
    day: date,
    scope_key: str | None = None,
) -> float | None:
    row = (await db.execute(text(lineage.value_sql), {"day": day, "scope_key": scope_key})).first()
    if row is None or row[0] is None:
        return None
    return float(row[0])


async def _build_definition_only(
    db: AsyncSession, *, metric_key: str, day: date, scope_key: str | None
) -> dict[str, Any] | None:
    """已登记但未接入批次账本的指标：只给"定义 + 最新可见值 + 来源行"，明确标注.

    为什么不硬造 batches/quality：没接 ledger 就是没接，伪造一条会让"溯源"变成
    更精致的谎言。``lineage_status=definition_only`` 让前端/调用方知道边界。
    """
    from app.services.industry_registry import find_metric  # noqa: PLC0415

    found = find_metric(metric_key)
    if found is None:
        return None
    owner, metric = found
    scope = scope_key or owner
    row = (
        await db.execute(
            text(
                """
                SELECT value, period, published_at, source, caliber_version
                FROM industry_metrics
                WHERE industry_key = :scope AND stock_id = 0 AND metric_key = :key
                  AND (published_at IS NULL OR published_at::date <= :day)
                ORDER BY period DESC, published_at DESC NULLS LAST
                LIMIT 1
                """
            ),
            {"scope": scope, "key": metric_key, "day": day},
        )
    ).first()
    value = float(row[0]) if row is not None and row[0] is not None else None
    published_at = row[2] if row is not None else None
    as_of = (
        published_at.date() if published_at is not None else (row[1] if row is not None else day)
    )
    return {
        "metric": {
            "key": metric.key,
            "label": metric.name,
            "unit": metric.unit,
            "caliber_version": metric.caliber_version,
            "definition": metric.description or "",
            "consumers": [],
        },
        "value": {"as_of": as_of.isoformat(), "value": value, "scope_key": scope},
        "sources": [
            {
                "table": "industry_metrics",
                "domain": "industry_metrics",
                "rows": 1 if row is not None else 0,
                "batch_id": None,
                "batch_status": None,
                "raw_path": None,
                "source": row[3] if row is not None else None,
                "caliber_version": row[4] if row is not None else metric.caliber_version,
            }
        ],
        "quality": [],
        "freshness": {
            "expected_as_of": None,
            "lag_trading_days": 0,
            "stale": False,
        },
        "lineage_status": "definition_only",
        "note": "该指标已登记定义与读取面，但未接入 ingest_batches 批次账本；"
        "上面的来源行是实际读取行，不代表完整血缘。",
    }


async def _source_rows(db: AsyncSession, source: lineage_registry.SourceRef, day: date) -> int:
    # 表名/列名来自代码常量（不是用户输入），不存在注入面。
    stmt = text(f"SELECT count(*) FROM {source.table} WHERE {source.date_column} = :day")
    return int((await db.execute(stmt, {"day": day})).scalar_one())


async def _batch_for(db: AsyncSession, *, domain: str, day: date) -> IngestBatch | None:
    stmt = (
        select(IngestBatch)
        .where(
            IngestBatch.domain == domain,
            IngestBatch.business_date == day,
            IngestBatch.status.in_(("accepted", "replayed", "partial")),
        )
        .order_by(IngestBatch.finished_at.desc().nullslast())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _trading_days_between(db: AsyncSession, *, start: date, end: date) -> int:
    if end <= start:
        return 0
    stmt = text(
        "SELECT count(DISTINCT trade_date) FROM daily_quotes "
        "WHERE trade_date > :start AND trade_date <= :end"
    )
    return int((await db.execute(stmt, {"start": start, "end": end})).scalar_one())


async def build(
    db: AsyncSession,
    *,
    metric_key: str,
    day: date,
    scope_key: str | None = None,
) -> dict[str, Any] | None:
    lineage = lineage_registry.get(metric_key)
    if lineage is None:
        return await _build_definition_only(db, metric_key=metric_key, day=day, scope_key=scope_key)
    if lineage.scope_required and not scope_key:
        return None

    value = await resolve_value(db, lineage=lineage, day=day, scope_key=scope_key)

    sources: list[dict[str, Any]] = []
    batch: IngestBatch | None = None
    for ref in lineage.sources:
        rows = await _source_rows(db, ref, day)
        item: dict[str, Any] = {"table": ref.table, "domain": ref.domain, "rows": rows}
        if batch is None:
            batch = await _batch_for(db, domain=ref.domain, day=day)
            if batch is not None:
                item["batch_id"] = batch.id
                item["raw_path"] = batch.raw_path
                item["content_hash"] = batch.content_hash
                item["batch_status"] = batch.status
        sources.append(item)

    quality = [
        item.as_dict()
        for item in await data_expectations.latest_for_day(db, day=day)
        if item.domain in {ref.domain for ref in lineage.sources}
    ]

    latest = await market_day_service.resolve_latest_complete_day(db)
    expected_day = latest.day if latest is not None else None
    lag = (
        await _trading_days_between(db, start=day, end=expected_day)
        if expected_day is not None
        else None
    )

    return {
        "metric": {
            "key": lineage.key,
            "label": lineage.label,
            "unit": lineage.unit,
            "caliber_version": lineage.caliber_version,
            "definition": lineage.definition,
            "consumers": list(lineage.consumers),
        },
        "value": {
            "as_of": day.isoformat(),
            "value": value,
            "scope_key": scope_key,
        },
        "sources": sources,
        "quality": quality,
        "freshness": {
            "expected_as_of": expected_day.isoformat() if expected_day else None,
            "lag_trading_days": lag,
            "stale": bool(expected_day and day < expected_day),
        },
    }
