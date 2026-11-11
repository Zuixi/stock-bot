"""东财券商研报 → 内容事实层（``content_items`` / ``content_entities`` / ``content_metrics``）。

口径（``plans/2026-11-11-wave2-research-reports.md`` + ``docs/specs/S3-content.md``）：

- 只存链接：``url=pdf_url``，不落正文/PDF；``raw_hash`` 由 ``infoCode`` 派生。
- 空值不写 0：缺字段不产生指标行，缺失的 key 记进 ``extracted.missing_reason``。
- 指标全部带 ``source_span``（``kind='structured'``，指向列表接口而非 PDF）。
- 去重（写入时）：同 ``publisher + stock + rating_change`` 只留 ``published_at`` 最新一条，
  旧条目写 ``extracted.superseded_by = 新条目 id``（不删除，保留可查）。乱序回补
  （先入新的、后入旧的）同样把旧条目指向已存在的新条目。

幂等：``content_items`` 靠 ``(source_name, raw_hash)`` DO NOTHING，指标靠
``uq_content_metrics_key`` upsert 覆盖，实体靠复合主键；同一窗口重跑 → ``inserted=0``。
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers.eastmoney_report_client import EastmoneyReportClient
from app.models.content import ContentItem
from app.repositories import content_repo, market_data_repo

logger = logging.getLogger(__name__)

CONTENT_TYPE = "report"
SOURCE_NAME = "eastmoney_report"
PUBLISHER_TIER = "sellside"

_SOURCE_SPAN_TABLE = "eastmoney_report_list"
_STOCK_CODE_RE = re.compile(r"^\d{6}$")

#: EPS/PE 预测列的顺序与年偏移（东财只给当年 + 后两年）。
_FORECAST_YEAR_OFFSETS = (0, 1, 2)


def _raw_hash(source_item_id: str) -> str:
    """去重键：研报唯一 id 的稳定哈希（正文不入库，哈希即身份）。"""
    return hashlib.sha256(f"eastmoney_report:{source_item_id}".encode()).hexdigest()


def _text(value: Any) -> str | None:
    """空白/None → ``None``（"缺字段"与"空串"必须同义，否则会写出无值指标）。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _stock_code(value: Any) -> str | None:
    """股票代码归一为 6 位字符串；数值型补零（``2956`` → ``002956``）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = f"{value:06d}"
    else:
        text = str(value).strip()
    return text if _STOCK_CODE_RE.match(text) else None


def _numeric(value: Any) -> float | None:
    """数值列 → float；非数值（占位符/文本）→ ``None``。``bool`` 不算数值。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _quote(row: dict[str, Any], year: int) -> str:
    """``source_span.quote``：把结构化来源字段压成可读快照。"""
    parts = [
        f"rating={_text(row.get('rating')) or ''}",
        f"target_price={_text(row.get('target_price')) or ''}",
    ]
    parts.extend(f"eps_{year + off}={_text(row.get(col)) or ''}" for off, col in _eps_cols())
    return ";".join(parts)


def _eps_cols() -> list[tuple[int, str]]:
    return [
        (0, "eps_this_year"),
        (1, "eps_next_year"),
        (2, "eps_next_two_year"),
    ]


def _pe_cols() -> list[tuple[int, str]]:
    return [
        (0, "pe_this_year"),
        (1, "pe_next_year"),
        (2, "pe_next_two_year"),
    ]


def _source_span(row: dict[str, Any], year: int) -> dict[str, Any]:
    return {
        "kind": "structured",
        "table": _SOURCE_SPAN_TABLE,
        "key": row["source_item_id"],
        "quote": _quote(row, year),
    }


def _content_row(row: dict[str, Any]) -> dict[str, Any]:
    """归一后的研报行 → ``content_items`` 行（``extracted`` 不含 ``missing_reason``）。"""
    return {
        "content_type": CONTENT_TYPE,
        "source_name": SOURCE_NAME,
        "publisher_tier": PUBLISHER_TIER,
        "publisher": _text(row.get("org_name")),
        "title": _text(row.get("title")) or "",
        "published_at": row["published_at"],
        "url": row["pdf_url"],
        "raw_hash": _raw_hash(row["source_item_id"]),
        "extracted": {
            "source_item_id": row["source_item_id"],
            "stock_code": row["stock_code"],
            "stock_name": _text(row.get("stock_name")),
            "org_name": _text(row.get("org_name")),
            "industry": _text(row.get("industry")),
            "industry_code": row.get("industry_code"),
            "rating_change": row.get("rating_change"),
            # Plan 2 明确不接 LLM：显示"未抽取"，不编造核心观点。
            "core_view_status": "not_extracted",
        },
    }


def _entities(row: dict[str, Any]) -> list[tuple[str, str]]:
    """该研报关联的实体：自身股票必入，东财行业名非空时补行业（不混申万）。"""
    found: list[tuple[str, str]] = [("stock", row["stock_code"])]
    industry = _text(row.get("industry"))
    if industry:
        found.append(("industry", industry))
    return list(dict.fromkeys(found))


def _metric_rows(row: dict[str, Any], content_id: int) -> tuple[list[dict[str, Any]], list[str]]:
    """研报行 → 指标行 + 缺失的 metric key 列表（空值不写 0）。"""
    year = row["published_at"].year
    span = _source_span(row, year)
    stock_code = row["stock_code"]
    metrics: list[dict[str, Any]] = []
    missing: list[str] = []

    def add(
        metric_key: str,
        *,
        value: float | None = None,
        value_text: str | None = None,
        period: str | None = None,
        unit: str | None = None,
    ) -> None:
        metrics.append(
            {
                "content_id": content_id,
                "entity_type": "stock",
                "entity_id": stock_code,
                "metric_key": metric_key,
                "period": period,
                "value": value,
                "value_text": value_text,
                "unit": unit,
                "source_span": span,
            }
        )

    rating = _text(row.get("rating"))
    if rating is None:
        missing.append("rating")
    else:
        add("rating", value_text=rating)

    rating_change = row.get("rating_change")
    if rating_change is None or _text(rating_change) is None:
        missing.append("rating_change")
    else:
        numeric_change = _numeric(rating_change)
        # 东财 ratingChange 是数值码；非数值文本也保留（value_text），不丢信息。
        add(
            "rating_change",
            value=numeric_change,
            value_text=None if numeric_change is not None else _text(rating_change),
        )

    target_price = _numeric(row.get("target_price"))
    if target_price is None:
        missing.append("target_price")
    else:
        add("target_price", value=target_price, unit="CNY")

    for key, cols, unit in (
        ("eps_forecast", _eps_cols(), "CNY"),
        ("pe_forecast", _pe_cols(), "x"),
    ):
        any_written = False
        for offset, column in cols:
            value = _numeric(row.get(column))
            if value is None:
                continue
            any_written = True
            add(key, value=value, period=f"{year + offset}E", unit=unit)
        if not any_written:
            missing.append(key)

    return metrics, missing


async def _mark_superseded(db: AsyncSession, content_id: int, row: dict[str, Any]) -> None:
    """同 ``publisher + stock + rating_change`` 只留最新一条（乱序回补也正确）。

    JSONB 原地修改不被 SQLAlchemy 跟踪，必须整体**重新赋值** ``item.extracted``。
    """
    rating_change = row.get("rating_change")
    if rating_change is None:
        return
    extracted = row["extracted"]
    publisher = extracted.get("org_name")
    stock_code = extracted.get("stock_code")
    if publisher is None or stock_code is None:
        return

    others = list(
        (
            await db.execute(
                select(ContentItem)
                .where(
                    ContentItem.content_type == CONTENT_TYPE,
                    ContentItem.source_name == SOURCE_NAME,
                    ContentItem.publisher == publisher,
                    ContentItem.extracted["stock_code"].astext == stock_code,
                    ContentItem.extracted["rating_change"].astext == str(rating_change),
                    ContentItem.id != content_id,
                )
                .order_by(ContentItem.published_at.desc(), ContentItem.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if not others:
        return

    current = (
        await db.execute(select(ContentItem).where(ContentItem.id == content_id))
    ).scalar_one()
    latest = others[0]
    current_is_latest = (current.published_at, current.id) >= (
        latest.published_at,
        latest.id,
    )
    if current_is_latest:
        for other in others:
            other.extracted = {**(other.extracted or {}), "superseded_by": current.id}
    else:
        current.extracted = {**(current.extracted or {}), "superseded_by": latest.id}
    await db.flush()


async def ingest_research_reports(
    db: AsyncSession,
    begin_date: date,
    end_date: date,
    *,
    client: Any | None = None,
    page_size: int = 100,
    max_pages: int = 50,
) -> dict[str, int]:
    """按日期区间抓取东财研报并写内容事实层，返回 ``fetched/inserted/skipped``。

    ``inserted`` 是**实际新增**的内容行数（同一窗口重跑 → 0，指标 upsert 覆盖）。
    缺 ``source_item_id`` / 6 位 ``stock_code`` / ``pdf_url`` / ``published_at`` 的行
    不入库（没有原文入口或无法定序的内容不可溯源），计入 ``skipped``。
    """
    report_client = client or EastmoneyReportClient()
    rows = await report_client.fetch_reports(
        begin_date, end_date, page_size=page_size, max_pages=max_pages
    )

    content_rows: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    skipped = 0
    seen: set[str] = set()
    for row in rows:
        source_item_id = _text(row.get("source_item_id"))
        stock_code = _stock_code(row.get("stock_code"))
        pdf_url = _text(row.get("pdf_url"))
        title = _text(row.get("title"))
        if (
            source_item_id is None
            or stock_code is None
            or pdf_url is None
            or title is None
            or row.get("published_at") is None
        ):
            skipped += 1
            logger.warning(
                "skip eastmoney report row (missing identity/stock/pdf/date): %s %s %s",
                row.get("source_item_id"),
                row.get("stock_code"),
                row.get("title"),
            )
            continue
        if source_item_id in seen:
            # 客户端已按 infoCode 去重；这里再兜一次，避免同一批次撞唯一键。
            continue
        seen.add(source_item_id)

        payload = {
            **row,
            "source_item_id": source_item_id,
            "stock_code": stock_code,
            "pdf_url": pdf_url,
            "title": title,
        }
        payloads.append(payload)
        content_rows.append(_content_row(payload))

    inserted = 0
    for batch in market_data_repo.chunk_rows(content_rows):
        inserted += await content_repo.upsert_content_items(db, batch)

    ids_by_hash = await content_repo.get_content_ids_by_hashes(
        db, [row["raw_hash"] for row in content_rows], SOURCE_NAME
    )

    for payload, content_row in zip(payloads, content_rows, strict=True):
        content_id = ids_by_hash.get(content_row["raw_hash"])
        if content_id is None:  # 并发删除/裁剪 → 跳过，不抛错
            continue

        await content_repo.insert_content_entities(db, content_id, _entities(payload))
        metrics, missing = _metric_rows(payload, content_id)
        await content_repo.upsert_content_metrics(db, metrics)
        if missing:
            current = (
                await db.execute(select(ContentItem).where(ContentItem.id == content_id))
            ).scalar_one()
            # 重放时以库里已有的 extracted 为底（保住此前写入的 superseded_by）。
            current.extracted = {
                **(current.extracted or {}),
                "missing_reason": ",".join(missing),
            }
            await db.flush()

        await _mark_superseded(db, content_id, {**payload, "extracted": content_row["extracted"]})

    logger.info(
        "ingest_research_reports %s~%s fetched=%s inserted=%s skipped=%s",
        begin_date,
        end_date,
        len(rows),
        inserted,
        skipped,
    )
    return {"fetched": len(rows), "inserted": inserted, "skipped": skipped}
