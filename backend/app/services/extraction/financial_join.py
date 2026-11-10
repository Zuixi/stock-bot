"""定期报告 → ``content_metrics`` 的结构化 PIT join（S3.2）。

口径（2026-11-11 owner 裁定）：定期报告的 ``revenue`` / ``net_profit`` **只从
``financial_report_versions`` + ``income_statement_facts`` 取数**，绝不解析 PDF 正文。

三条硬约束：

1. **PIT 强制**：只用 ``f_ann_date <= content.published_at.date()`` 的报告版本。
   财报是"事后修订"最频繁的数据，若取最新版本，2026-10-30 的公告会读到 11-05 才
   公布的修订值——那是未来函数，回测结论全部作废。同一天有多个版本时按
   ``f_ann_date desc, id desc`` 取最后发布的那份（当天已可见的最新口径）。
2. **合并报表优先**：只认 ``comp_type='1'``（合并报表），母公司报表口径不可比。
3. **缺失不补零**：字段为空就写进 ``extracted['missing_reason']``，不落 0
   —— 0 是真实数值（例如零收入），与"没披露"必须区分。

失败路径一律把原因写回 ``content_items.extracted``（``missing_reason``）并返回 0，
让下游能区分"已处理但无数据"和"还没处理"（前者不重试，后者可重试）。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentEntity, ContentItem
from app.models.financial import FinancialReportVersion, IncomeStatementFacts
from app.models.stock import Stock
from app.repositories import content_repo

#: ``<年>年...`` 里的年份；``(?<!\d)`` 防止把 6 位代码/编号的后 4 位当年份。
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})\s*年")

#: 报告期关键词 → 期末日。**两条纪律**：
#: 1. 顺序即优先级：``半年度报告`` 含子串 ``年度报告``，必须先判半年；
#: 2. 必须命中"报告"类词：``2026年度业绩预告`` 是预告不是定期报告，不能被当成
#:    年报（``年度`` 裸词会误伤）。
_PERIOD_KEYWORDS: tuple[tuple[tuple[str, ...], tuple[int, int]], ...] = (
    (("半年度报告", "半年报", "中期报告"), (6, 30)),
    (("第一季度报告", "一季度报告"), (3, 31)),
    (("第三季度报告", "三季度报告"), (9, 30)),
    (("年度报告", "年报"), (12, 31)),
)

#: 报告期标签：期末月 → 期间串（``2026Q1`` / ``2026H1`` / ``2026Q3`` / ``2026FY``）。
_PERIOD_LABEL: dict[int, str] = {3: "Q1", 6: "H1", 9: "Q3", 12: "FY"}

#: 抽取的指标：事实表列 → ``content_metrics.metric_key``。
_METRICS: tuple[tuple[str, str], ...] = (("revenue", "revenue"), ("n_income_attr_p", "net_profit"))


def parse_report_period(title: str) -> date | None:
    """从定期报告标题解析报告期末日；解析不出返回 ``None``。

    只认四种中文定期报告写法（第一季度/一季度、半年度、第三季度/三季度、年度），
    其它标题（临时公告、调研纪要、英文标题）一律 ``None`` —— 猜错报告期比不抽更糟。
    """
    if not title:
        return None
    year_match = _YEAR_RE.search(title)
    if year_match is None:
        return None
    for keywords, (month, day) in _PERIOD_KEYWORDS:
        if any(keyword in title for keyword in keywords):
            return date(int(year_match.group(1)), month, day)
    return None


def _period_label(period: date) -> str:
    """``2026-09-30`` → ``2026Q3``；不认识的期末月退回 ``YYYYQn`` 形式。"""
    suffix = _PERIOD_LABEL.get(period.month)
    if suffix is None:
        return f"{period.year}Q{(period.month - 1) // 3 + 1}"
    return f"{period.year}{suffix}"


#: 本服务可能写入的缺失原因全集。正常路径（成功取到版本）会把它们全部清掉，
#: 再按本次实际缺的字段重写——否则上游补齐/重抽后，旧的 ``no_pit_financial_version``
#: 会永远挂在内容上，UI 会一直说"没数据"。
_ALL_MISSING_REASONS = (
    "no_stock_entity",
    "stock_not_found",
    "unparsed_report_period",
    "no_pit_financial_version",
    "revenue_missing",
    "net_profit_missing",
)


def _update_missing(
    content: ContentItem, reasons: list[str] | None = None, *, clear: bool = False
) -> None:
    """维护 ``extracted['missing_reason']``（逗号分隔，幂等）。

    JSONB 列原地修改不会被 SQLAlchemy 感知，必须整体重新赋值。
    ``clear=True`` 用于正常路径：先清掉本服务写入的旧原因，再把本次仍缺的字段加回去。
    """
    current = dict(content.extracted or {})
    existing = current.get("missing_reason")
    merged: list[str] = []
    if isinstance(existing, str) and existing:
        merged.extend(existing.split(","))
    elif isinstance(existing, list):
        merged.extend(str(item) for item in existing)
    if clear:
        merged = [reason for reason in merged if reason not in _ALL_MISSING_REASONS]
    for reason in reasons or []:
        if reason not in merged:
            merged.append(reason)
    if merged:
        current["missing_reason"] = ",".join(merged)
    else:
        current.pop("missing_reason", None)
    content.extracted = current


async def upsert_financial_metrics_for_content(db: AsyncSession, content_id: int) -> int:
    """把一条定期报告公告的 ``revenue`` / ``net_profit`` 落到 ``content_metrics``。

    返回写入（新增或覆盖）的指标行数；不满足前置条件时返回 0 并（能定位到内容时）
    在 ``extracted['missing_reason']`` 写明原因。**绝不补 0**。
    """
    content = await db.get(ContentItem, content_id)
    if content is None:
        return 0
    if content.content_type != "announcement":
        return 0
    if (content.extracted or {}).get("subtype") != "report":
        return 0

    entity = (
        await db.execute(
            select(ContentEntity)
            .where(
                ContentEntity.content_id == content_id,
                ContentEntity.entity_type == "stock",
            )
            .order_by(ContentEntity.entity_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if entity is None:
        _update_missing(content, ["no_stock_entity"])
        await db.flush()
        return 0

    symbol = entity.entity_id
    stock_id = (
        await db.execute(select(Stock.id).where(Stock.symbol == symbol).order_by(Stock.id).limit(1))
    ).scalar_one_or_none()
    if stock_id is None:
        _update_missing(content, ["stock_not_found"])
        await db.flush()
        return 0

    period = parse_report_period(content.title)
    if period is None:
        _update_missing(content, ["unparsed_report_period"])
        await db.flush()
        return 0

    # ★ PIT：只认公告发布时点之前（含当天）已公布的报告版本。
    published_on: date = content.published_at.date()
    version = (
        await db.execute(
            select(FinancialReportVersion)
            .where(
                FinancialReportVersion.stock_id == stock_id,
                FinancialReportVersion.end_date == period,
                FinancialReportVersion.comp_type == "1",
                FinancialReportVersion.f_ann_date.is_not(None),
                FinancialReportVersion.f_ann_date <= published_on,
            )
            .order_by(
                FinancialReportVersion.f_ann_date.desc(),
                FinancialReportVersion.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if version is None:
        _update_missing(content, ["no_pit_financial_version"])
        await db.flush()
        return 0

    facts = (
        await db.execute(
            select(IncomeStatementFacts).where(IncomeStatementFacts.report_version_id == version.id)
        )
    ).scalar_one_or_none()

    ann_date = version.f_ann_date
    if ann_date is None:  # 上式已过滤 NULL，这里是类型收窄的兜底
        _update_missing(content, ["no_pit_financial_version"])
        await db.flush()
        return 0
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for column, metric_key in _METRICS:
        value = getattr(facts, column, None) if facts is not None else None
        if value is None:
            missing.append(f"{metric_key}_missing")
            continue
        rows.append(
            {
                "content_id": content_id,
                "entity_type": "stock",
                "entity_id": symbol,
                "metric_key": metric_key,
                "period": _period_label(period),
                "value": value,
                "unit": "CNY",
                "source_span": {
                    "kind": "structured",
                    "table": "financial_report_versions",
                    "key": version.id,
                    "ann_date": ann_date.isoformat(),
                    "quote": f"{metric_key}={value}",
                },
            }
        )

    written = await content_repo.upsert_content_metrics(db, rows)
    # 正常路径：先清掉本服务写入的旧原因，再把本次仍缺的字段加回去。
    # 不这样做的话，上游补齐后旧的 ``no_pit_financial_version`` 会永远挂在内容上。
    _update_missing(content, missing, clear=True)
    await db.flush()
    return written
