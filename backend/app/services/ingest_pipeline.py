"""Ingestion pipeline: fetch → replay → validate → quarantine → persist → seal (P5).

设计约束（见 ``plans/2026-11-11-data-platform-hardening-p5-p7.md`` §3）：

1. **成功 = 数据层终态**，不是 HTTP 200 / 函数返回。只有 ``status='accepted'``
   且 DB 行数对得上的批次才允许下一轮跳过外呼。
2. **失败不丢行**：校验拒绝的行进 ``ingest_quarantine``，带机器可聚合的 ``reason_code``。
3. **已付费的请求不重复付**：外呼失败时优先回放当天 JSONL（``DataSaver.load_dataframe``）。
4. **幂等**：所有写路径 upsert；同一分区重复跑不产生重复账本行。

判据全部在代码里（不作 DB 配置），因为它们是"契约"而不是"参数"。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_quality import IngestBatch, IngestQuarantine
from app.services.data_saver import DataSaver

logger = logging.getLogger(__name__)

#: 校验原因码（机器聚合口径，新增值必须同时更新这份清单）。
REASON_SCHEMA_MISSING_COLUMN = "schema.missing_column"
REASON_REQUIRED_NULL = "value.required_null"
REASON_OUT_OF_RANGE = "value.out_of_range"
REASON_DUPLICATE_KEY = "key.duplicate"
REASON_UNKNOWN_KEY = "mapping.unknown_key"
REASON_CROSS_FIELD = "cross_field.inconsistent"


@dataclass(frozen=True)
class FieldRule:
    """单列校验：类型 / 必填 / 值域 / 枚举."""

    column: str
    required: bool = False
    dtype: str | None = None  # str | int | float | date
    min: float | None = None
    max: float | None = None
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RowRules:
    """一批数据的行级契约."""

    required_columns: tuple[str, ...] = ()
    fields: tuple[FieldRule, ...] = ()
    unique_key: tuple[str, ...] | None = None


@dataclass
class ValidationOutcome:
    accepted: pd.DataFrame
    rejected: list[dict[str, Any]] = field(default_factory=list)
    schema_error: str | None = None


@dataclass(frozen=True)
class IngestSpec:
    """一次采集分区的身份。``params_hash`` 由参数派生，调用方不手填。"""

    domain: str
    business_date: date
    source: str
    api_name: str
    params: dict[str, Any] = field(default_factory=dict)
    capture_kind: str = "eod_full"
    rules: RowRules | None = None
    allow_replay: bool = True

    @property
    def params_hash(self) -> str:
        payload = json.dumps(
            {str(k): str(v) for k, v in sorted(self.params.items())},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class IngestResult:
    domain: str
    business_date: date
    status: str  # accepted|partial|quarantined|replayed|failed|skipped
    rows_fetched: int = 0
    rows_accepted: int = 0
    rows_quarantined: int = 0
    batch_id: int | None = None
    raw_path: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    text_value = str(value).strip()
    return text_value == "" or text_value.lower() == "nan"


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_rows(df: pd.DataFrame, rules: RowRules | None) -> ValidationOutcome:
    """按契约拆分 accepted / rejected；缺列直接判整批 schema drift.

    缺列不是"某些行坏了"，而是**上游契约变了**：整批拒绝 + 明确原因，比把 NULL
    灌进 core 表安全（后者要等页面空一片才会被发现）。
    """
    if rules is None:
        return ValidationOutcome(accepted=df)

    missing = [c for c in rules.required_columns if c not in df.columns]
    if missing:
        return ValidationOutcome(
            accepted=df.iloc[0:0],
            rejected=[],
            schema_error=f"{REASON_SCHEMA_MISSING_COLUMN}:{','.join(missing)}",
        )

    accepted_indices: list[Any] = []
    rejected: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()

    for idx, row in df.iterrows():
        reason: str | None = None
        row_key: str | None = None
        if rules.unique_key:
            row_key = "|".join(str(row.get(k, "")) for k in rules.unique_key)

        for rule in rules.fields:
            value = row.get(rule.column)
            if rule.required and _is_blank(value):
                reason = f"{REASON_REQUIRED_NULL}:{rule.column}"
                break
            if _is_blank(value):
                continue
            if rule.dtype in ("int", "float"):
                number = _as_float(value)
                if number is None:
                    reason = f"{REASON_OUT_OF_RANGE}:{rule.column}"
                    break
                if rule.min is not None and number < rule.min:
                    reason = f"{REASON_OUT_OF_RANGE}:{rule.column}"
                    break
                if rule.max is not None and number > rule.max:
                    reason = f"{REASON_OUT_OF_RANGE}:{rule.column}"
                    break
            if rule.enum is not None and str(value) not in rule.enum:
                reason = f"{REASON_OUT_OF_RANGE}:{rule.column}"
                break

        if reason is None and rules.unique_key:
            key = tuple(str(row.get(k, "")) for k in rules.unique_key)
            if key in seen_keys:
                reason = f"{REASON_DUPLICATE_KEY}:{','.join(rules.unique_key)}"
            else:
                seen_keys.add(key)

        if reason is None:
            accepted_indices.append(idx)
        else:
            rejected.append(
                {
                    "reason_code": reason.split(":")[0],
                    "row_key": row_key,
                    "payload": _jsonable_row(row),
                    "detail": reason,
                }
            )

    accepted = df.loc[accepted_indices] if accepted_indices else df.iloc[0:0]
    return ValidationOutcome(accepted=accepted, rejected=rejected)


def _jsonable_row(row: pd.Series) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            out[str(key)] = None
        elif isinstance(value, (str, bool, int)):
            out[str(key)] = value
        elif isinstance(value, float):
            out[str(key)] = None if pd.isna(value) else value
        elif hasattr(value, "isoformat"):
            out[str(key)] = value.isoformat()
        elif hasattr(value, "item"):
            try:
                out[str(key)] = value.item()
            except Exception:  # noqa: BLE001 — 账本字段不能因为序列化失败而丢行
                out[str(key)] = str(value)
        else:
            out[str(key)] = str(value)
    return out


# ---------------------------------------------------------------------------
# 账本读写（供既有 ingest 服务增量接入；不要求整体重写）
# ---------------------------------------------------------------------------


async def record_batch(
    db: AsyncSession,
    *,
    domain: str,
    business_date: date,
    source: str,
    capture_kind: str,
    params: dict[str, Any],
    rows_fetched: int,
    rows_accepted: int,
    rows_quarantined: int = 0,
    status: str = "accepted",
    raw_path: str | None = None,
    content_hash: str | None = None,
    error: str | None = None,
) -> int:
    """幂等写一条批次账本（同分区只保留最新一次终态）。"""
    params_hash = _params_hash(params)
    stmt = (
        pg_insert(IngestBatch)
        .values(
            domain=domain,
            business_date=business_date,
            source=source,
            capture_kind=capture_kind,
            params_hash=params_hash,
            raw_path=raw_path,
            content_hash=content_hash,
            rows_fetched=rows_fetched,
            rows_accepted=rows_accepted,
            rows_quarantined=rows_quarantined,
            status=status,
            error=error,
            finished_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="uq_ingest_batches_partition",
            set_={
                "raw_path": text("excluded.raw_path"),
                "content_hash": text("excluded.content_hash"),
                "rows_fetched": text("excluded.rows_fetched"),
                "rows_accepted": text("excluded.rows_accepted"),
                "rows_quarantined": text("excluded.rows_quarantined"),
                "status": text("excluded.status"),
                "error": text("excluded.error"),
                "finished_at": text("excluded.finished_at"),
            },
        )
        .returning(IngestBatch.id)
    )
    batch_id = int((await db.execute(stmt)).scalar_one())
    return batch_id


async def record_quarantine(
    db: AsyncSession,
    *,
    batch_id: int,
    domain: str,
    business_date: date,
    rejected: Sequence[dict[str, Any]],
) -> None:
    if not rejected:
        return
    db.add_all(
        [
            IngestQuarantine(
                batch_id=batch_id,
                domain=domain,
                business_date=business_date,
                reason_code=str(item["reason_code"]),
                row_key=item.get("row_key"),
                payload=item["payload"],
            )
            for item in rejected
        ]
    )


async def sealed_rows(
    db: AsyncSession,
    *,
    domain: str,
    business_date: date,
    source: str | None = None,
    capture_kind: str | None = None,
    min_rows: int = 1,
) -> int:
    """该分区已封存的行数（0 = 未封存）。只认 ``status='accepted'``。"""
    stmt = select(IngestBatch.rows_accepted).where(
        IngestBatch.domain == domain,
        IngestBatch.business_date == business_date,
        IngestBatch.status.in_(("accepted", "replayed")),
        IngestBatch.rows_accepted >= min_rows,
    )
    if source:
        stmt = stmt.where(IngestBatch.source == source)
    if capture_kind:
        stmt = stmt.where(IngestBatch.capture_kind == capture_kind)
    stmt = stmt.order_by(IngestBatch.finished_at.desc().nullslast()).limit(1)
    value = (await db.execute(stmt)).scalar_one_or_none()
    return int(value or 0)


async def should_skip(
    db: AsyncSession,
    *,
    domain: str,
    business_date: date,
    db_rows: int,
    source: str | None = None,
    capture_kind: str | None = None,
    min_rows: int = 1,
) -> bool:
    """封存行数达标且与 DB 实际行数一致才跳过（封存不是权威，DB 是权威）。

    账本读不到（迁移未应用/会话不可用/测试替身）⇒ **不跳过**：封存只是省一次外呼的
    优化，不能因为读不到它而改变"该补的还得补"这个语义。
    """
    try:
        sealed = await sealed_rows(
            db,
            domain=domain,
            business_date=business_date,
            source=source,
            capture_kind=capture_kind,
            min_rows=min_rows,
        )
    except Exception:  # noqa: BLE001 — 账本不可用时 fail-open 到"不跳过"
        logger.debug("seal lookup unavailable; not skipping %s %s", domain, business_date)
        return False
    return sealed > 0 and sealed == db_rows


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

Fetcher = Callable[[], Awaitable[pd.DataFrame]]
Parser = Callable[[pd.DataFrame], pd.DataFrame]
Persister = Callable[[AsyncSession, pd.DataFrame], Awaitable[int]]
ExpectationHook = Callable[[AsyncSession, IngestSpec], Awaitable[bool]]


class IngestPipeline:
    """一条分区的完整摄取流程；调用方只需提供 fetch/parse/persist 三个薄函数."""

    def __init__(
        self,
        *,
        saver: DataSaver | None = None,
        lock: bool = True,
        engine: Any = None,
    ) -> None:
        self.saver = saver or DataSaver()
        self.lock = lock
        self._engine = engine

    async def run(
        self,
        db: AsyncSession,
        spec: IngestSpec,
        *,
        fetch: Fetcher,
        parse: Parser | None = None,
        persist: Persister,
        count_rows: Callable[[AsyncSession, date], Awaitable[int]] | None = None,
        expectations: ExpectationHook | None = None,
    ) -> IngestResult:
        parse = parse or (lambda frame: frame)

        if count_rows is not None:
            db_rows = await count_rows(db, spec.business_date)
            if await should_skip(
                db,
                domain=spec.domain,
                business_date=spec.business_date,
                db_rows=db_rows,
                capture_kind=spec.capture_kind,
            ):
                return IngestResult(
                    domain=spec.domain,
                    business_date=spec.business_date,
                    status="skipped",
                    rows_accepted=db_rows,
                    reason="sealed",
                )

        lock_conn = None
        lock_key = f"ingest:{spec.domain}:{spec.business_date}:{spec.source}"
        try:
            if self.lock:
                acquired, lock_conn = await self._acquire_lock(lock_key)
                if not acquired:
                    return IngestResult(
                        domain=spec.domain,
                        business_date=spec.business_date,
                        status="skipped",
                        reason="locked",
                    )

            source = spec.source
            raw_path: str | None = None
            try:
                frame = await fetch()
            except Exception as exc:  # noqa: BLE001 — 外呼失败是常态，先尝试回放
                frame = None
                fetch_error = f"{type(exc).__name__}: {exc}"
                logger.warning("INGEST fetch failed %s: %s", spec.api_name, fetch_error)
                if spec.allow_replay:
                    frame = await self.saver.load_dataframe(
                        spec.api_name,
                        spec.params,
                        day=spec.business_date.isoformat(),
                    )
                    if frame is not None:
                        source = "replay"
                        logger.info("INGEST replayed %s from local JSONL", spec.api_name)
                if frame is None:
                    batch_id = await record_batch(
                        db,
                        domain=spec.domain,
                        business_date=spec.business_date,
                        source=spec.source,
                        capture_kind=spec.capture_kind,
                        params=spec.params,
                        rows_fetched=0,
                        rows_accepted=0,
                        status="failed",
                        error=fetch_error,
                    )
                    await db.commit()
                    return IngestResult(
                        domain=spec.domain,
                        business_date=spec.business_date,
                        status="failed",
                        batch_id=batch_id,
                        reason=fetch_error,
                    )

            if source != "replay":
                raw_path_obj = await self.saver.save_dataframe(
                    spec.api_name,
                    frame,
                    spec.params,
                    exchange=f"all_{spec.business_date:%Y%m%d}",
                )
                raw_path = str(raw_path_obj)

            parsed = parse(frame)
            outcome = validate_rows(parsed, spec.rules)

            if outcome.schema_error:
                batch_id = await record_batch(
                    db,
                    domain=spec.domain,
                    business_date=spec.business_date,
                    source=source,
                    capture_kind=spec.capture_kind,
                    params=spec.params,
                    rows_fetched=len(parsed),
                    rows_accepted=0,
                    rows_quarantined=len(parsed),
                    status="quarantined",
                    raw_path=raw_path,
                    error=outcome.schema_error,
                )
                all_rows = [
                    {
                        "reason_code": REASON_SCHEMA_MISSING_COLUMN,
                        "row_key": None,
                        "payload": _jsonable_row(row),
                    }
                    for _, row in parsed.iterrows()
                ]
                await record_quarantine(
                    db,
                    batch_id=batch_id,
                    domain=spec.domain,
                    business_date=spec.business_date,
                    rejected=all_rows,
                )
                await db.commit()
                return IngestResult(
                    domain=spec.domain,
                    business_date=spec.business_date,
                    status="quarantined",
                    rows_fetched=len(parsed),
                    rows_quarantined=len(parsed),
                    batch_id=batch_id,
                    raw_path=raw_path,
                    reason=outcome.schema_error,
                )

            batch_id = await record_batch(
                db,
                domain=spec.domain,
                business_date=spec.business_date,
                source=source,
                capture_kind=spec.capture_kind,
                params=spec.params,
                rows_fetched=len(parsed),
                rows_accepted=0,
                rows_quarantined=len(outcome.rejected),
                status="pending",
                raw_path=raw_path,
            )
            try:
                accepted = await persist(db, outcome.accepted)
                await record_quarantine(
                    db,
                    batch_id=batch_id,
                    domain=spec.domain,
                    business_date=spec.business_date,
                    rejected=outcome.rejected,
                )
                await db.commit()
            except Exception as exc:  # noqa: BLE001 — DB 失败也要留终态；原始 JSONL 已保留
                await db.rollback()
                message = f"{type(exc).__name__}: {exc}"
                await record_batch(
                    db,
                    domain=spec.domain,
                    business_date=spec.business_date,
                    source=source,
                    capture_kind=spec.capture_kind,
                    params=spec.params,
                    rows_fetched=len(parsed),
                    rows_accepted=0,
                    rows_quarantined=0,
                    status="failed",
                    raw_path=raw_path,
                    error=message,
                )
                await db.commit()
                return IngestResult(
                    domain=spec.domain,
                    business_date=spec.business_date,
                    status="failed",
                    rows_fetched=len(parsed),
                    batch_id=batch_id,
                    raw_path=raw_path,
                    reason=message,
                )

            status = "accepted"
            if outcome.rejected:
                status = "partial"
            expectations_ok = True
            if expectations is not None:
                expectations_ok = await expectations(db, spec)
                if not expectations_ok:
                    status = "partial"

            final_status = status
            if source == "replay" and status == "accepted":
                final_status = "replayed"
            await record_batch(
                db,
                domain=spec.domain,
                business_date=spec.business_date,
                source=source,
                capture_kind=spec.capture_kind,
                params=spec.params,
                rows_fetched=len(parsed),
                rows_accepted=accepted,
                rows_quarantined=len(outcome.rejected),
                status=final_status,
                raw_path=raw_path,
                content_hash=_content_hash(outcome.accepted),
            )
            await db.commit()

            return IngestResult(
                domain=spec.domain,
                business_date=spec.business_date,
                status=final_status if expectations_ok else "partial",
                rows_fetched=len(parsed),
                rows_accepted=accepted,
                rows_quarantined=len(outcome.rejected),
                batch_id=batch_id,
                raw_path=raw_path,
            )
        finally:
            if lock_conn is not None:
                await self._release_lock(lock_conn, lock_key)
                await lock_conn.close()

    async def _acquire_lock(self, key: str) -> tuple[bool, Any]:
        """返回 ``(acquired, conn)``。锁是**优化**不是正确性：拿不到锁的进程
        依然可以被唯一键与 upsert 吸收，所以 DB 不可达时 fail-open（acquired=True）。
        """
        from app.core.database import engine as default_engine  # noqa: PLC0415

        engine = self._engine or default_engine
        try:
            conn = await engine.connect()
        except Exception:  # noqa: BLE001 — 纯单测/DB 未就绪时 fail-open
            return True, None
        try:
            got = (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
                )
            ).scalar()
        except Exception:  # noqa: BLE001
            await conn.close()
            return True, None
        if not got:
            await conn.close()
            return False, None
        return True, conn

    async def _release_lock(self, conn: Any, key: str) -> None:
        try:
            await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
        except Exception:  # noqa: BLE001 — 释放失败只记日志，不能掩盖业务结果
            logger.warning("INGEST advisory unlock failed key=%s", key)


def _params_hash(params: dict[str, Any]) -> str:
    payload = json.dumps(
        {str(k): str(v) for k, v in sorted(params.items())}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _content_hash(df: pd.DataFrame) -> str | None:
    if df.empty:
        return None
    payload = json.dumps(
        [_jsonable_row(row) for _, row in df.iterrows()],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
