"""定期报告 → ``content_metrics`` 结构化 PIT join 的真库不变量（S3.2）。

钉的是三件**做错就静默污染回测**的事：

1. **PIT**：2026-10-30 发布的公告只能读到当天（含）之前已公布的报告版本；
   11-05 才发布的修订值再"新"也不许出现在 10-30 的指标里（未来函数）。
2. **缺失不伪造**：公告早于任何 ``f_ann_date`` 时不写指标，并把
   ``missing_reason='no_pit_financial_version'`` 写回 ``content_items.extracted``。
3. **报告期解析**：四种中文定期报告写法 → 正确期末日；解析不出就 ``None``，
   绝不猜（猜错报告期比不抽更糟）。

库不可达时整文件跳过（``requires_pg``），CI 无 PG 不该红。用例自建自清：固定测试
来源前缀 + 专用 ``symbol='999998'``，``finally`` 里先删内容（子表 CASCADE）、再删
财报事实/版本、最后删股票，失败路径也不留脏行。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import delete, select

from app.core.database import async_session_factory, engine
from app.models.content import ContentEntity, ContentItem, ContentMetric
from app.models.financial import FinancialReportVersion, IncomeStatementFacts
from app.models.stock import Stock
from app.services.extraction.financial_join import (
    parse_report_period,
    upsert_financial_metrics_for_content,
)

#: 测试数据前缀：清理与断言都只认它，绝不碰真实内容/财报行。
TEST_SOURCE = "tfinjoin"
TEST_SYMBOL = "999998"


def _pg_self_check() -> bool:
    """库是否可达（asyncpg 直连，不进 SQLAlchemy 事件循环）。"""

    async def _go() -> bool:
        url = engine.url.set(drivername="postgresql")
        conn = await asyncpg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            database=url.database,
        )
        try:
            return True
        finally:
            await conn.close()

    try:
        return asyncio.run(_go())
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_self_check(),
    reason="PostgreSQL unavailable (financial join invariants need alembic-upgraded DB)",
)

pytestmark = requires_pg


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> Any:
    """每个用例一个事件循环：不 dispose 会把上个循环的连接留给下个用例。"""
    yield
    await engine.dispose()


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("2026年第一季度报告", date(2026, 3, 31)),
        ("2026年一季度报告", date(2026, 3, 31)),
        ("某股份2026年第一季度报告全文", date(2026, 3, 31)),
        ("2026年半年度报告", date(2026, 6, 30)),
        ("某股份2026年半年度报告摘要", date(2026, 6, 30)),
        ("2026年第三季度报告", date(2026, 9, 30)),
        ("2026年三季度报告", date(2026, 9, 30)),
        ("2026年年度报告", date(2026, 12, 31)),
        ("某股份2026年年度报告（修订版）", date(2026, 12, 31)),
        # 解析不出 → None（不猜报告期）
        ("关于召开2026年第一次临时股东大会的通知", None),
        ("2026年投资者关系活动记录表", None),
        ("", None),
        ("2026年度业绩预告", None),
    ],
)
def test_parse_report_period(title: str, expected: date | None) -> None:
    assert parse_report_period(title) == expected


async def _purge(db: Any) -> None:
    """按测试前缀/专用 symbol 自清（顺序：内容 → 财报事实/版本 → 股票）。"""
    await db.execute(delete(ContentItem).where(ContentItem.source_name == TEST_SOURCE))
    stock_ids = (
        (
            await db.execute(
                select(Stock.id).where(
                    Stock.symbol == TEST_SYMBOL,
                    Stock.exchange == "Shanghai_Stocks",
                )
            )
        )
        .scalars()
        .all()
    )
    if stock_ids:
        version_ids = (
            (
                await db.execute(
                    select(FinancialReportVersion.id).where(
                        FinancialReportVersion.stock_id.in_(list(stock_ids))
                    )
                )
            )
            .scalars()
            .all()
        )
        if version_ids:
            await db.execute(
                delete(IncomeStatementFacts).where(
                    IncomeStatementFacts.report_version_id.in_(list(version_ids))
                )
            )
        await db.execute(
            delete(FinancialReportVersion).where(
                FinancialReportVersion.stock_id.in_(list(stock_ids))
            )
        )
        await db.execute(delete(Stock).where(Stock.id.in_(list(stock_ids))))
    await db.commit()


async def _seed_stock(db: Any) -> int:
    stock = Stock(
        exchange="Shanghai_Stocks",
        symbol=TEST_SYMBOL,
        name="测试内容股",
        category="A",
        asof=datetime.now(UTC),
    )
    db.add(stock)
    await db.flush()
    return int(stock.id)


async def _seed_version(
    db: Any,
    stock_id: int,
    *,
    f_ann_date: date,
    revenue: float | None,
    net_profit: float | None,
    source: str,
) -> int:
    """插入一个报告版本 + 一行利润表事实，返回版本 id。"""
    version = FinancialReportVersion(
        stock_id=stock_id,
        end_date=date(2026, 9, 30),
        report_type="1",
        f_ann_date=f_ann_date,
        comp_type="1",
        source=source,
        as_of=datetime.now(UTC),
    )
    db.add(version)
    await db.flush()
    db.add(
        IncomeStatementFacts(
            report_version_id=version.id,
            revenue=revenue,
            n_income_attr_p=net_profit,
        )
    )
    await db.flush()
    return int(version.id)


async def _seed_content(db: Any, *, published_at: datetime, title: str) -> int:
    raw_hash = uuid4().hex
    content = ContentItem(
        content_type="announcement",
        source_name=TEST_SOURCE,
        publisher_tier="legal",
        title=title,
        published_at=published_at,
        url=f"https://example.invalid/{raw_hash}",
        raw_hash=raw_hash,
        extracted={"subtype": "report"},
    )
    db.add(content)
    await db.flush()
    db.add(
        ContentEntity(
            content_id=content.id,
            entity_type="stock",
            entity_id=TEST_SYMBOL,
        )
    )
    await db.flush()
    return int(content.id)


async def test_pit_selects_version_visible_at_publish_time() -> None:
    """10-30 的公告必须读 10-30 已发布的版本（100），而不是 11-05 的修订值（200）。"""
    async with async_session_factory() as db:
        try:
            await _purge(db)
            stock_id = await _seed_stock(db)
            await _seed_version(
                db,
                stock_id,
                f_ann_date=date(2026, 10, 30),
                revenue=100,
                net_profit=10,
                source=TEST_SOURCE,
            )
            await _seed_version(
                db,
                stock_id,
                f_ann_date=date(2026, 11, 5),
                revenue=200,
                net_profit=20,
                source=f"{TEST_SOURCE}_rev",
            )
            content_id = await _seed_content(
                db,
                published_at=datetime(2026, 10, 30, 9, 30, tzinfo=UTC),
                title="测试内容股2026年第三季度报告",
            )
            await db.commit()

            written = await upsert_financial_metrics_for_content(db, content_id)
            await db.commit()
            assert written == 2

            rows = {
                row.metric_key: row
                for row in (
                    await db.execute(
                        select(ContentMetric).where(ContentMetric.content_id == content_id)
                    )
                ).scalars()
            }
            assert set(rows) == {"revenue", "net_profit"}
            assert rows["revenue"].value == Decimal("100")
            assert rows["net_profit"].value == Decimal("10")
            assert rows["revenue"].period == "2026Q3"
            assert rows["revenue"].entity_type == "stock"
            assert rows["revenue"].entity_id == TEST_SYMBOL
            assert rows["revenue"].unit == "CNY"
            span = rows["revenue"].source_span
            assert span["kind"] == "structured"
            assert span["table"] == "financial_report_versions"
            assert span["ann_date"] == "2026-10-30"
            prefix, _, quoted = span["quote"].partition("=")
            assert (prefix, Decimal(quoted)) == ("revenue", Decimal("100"))
        finally:
            await _purge(db)


async def test_no_pit_version_records_missing_reason() -> None:
    """公告早于任何 ``f_ann_date`` → 不写指标，写回 ``no_pit_financial_version``。"""
    async with async_session_factory() as db:
        try:
            await _purge(db)
            stock_id = await _seed_stock(db)
            await _seed_version(
                db,
                stock_id,
                f_ann_date=date(2026, 10, 30),
                revenue=100,
                net_profit=10,
                source=TEST_SOURCE,
            )
            content_id = await _seed_content(
                db,
                published_at=datetime(2026, 10, 1, 9, 30, tzinfo=UTC),
                title="测试内容股2026年第三季度报告",
            )
            await db.commit()

            written = await upsert_financial_metrics_for_content(db, content_id)
            await db.commit()
            assert written == 0

            count = (
                (
                    await db.execute(
                        select(ContentMetric).where(ContentMetric.content_id == content_id)
                    )
                )
                .scalars()
                .all()
            )
            assert count == []

            item = (
                await db.execute(select(ContentItem).where(ContentItem.id == content_id))
            ).scalar_one()
            assert item.extracted["missing_reason"] == "no_pit_financial_version"
        finally:
            await _purge(db)


async def test_unparsed_period_records_missing_reason() -> None:
    """标题解析不出报告期 → 不写指标，写回 ``unparsed_report_period``。"""
    async with async_session_factory() as db:
        try:
            await _purge(db)
            stock_id = await _seed_stock(db)
            await _seed_version(
                db,
                stock_id,
                f_ann_date=date(2026, 10, 30),
                revenue=100,
                net_profit=10,
                source=TEST_SOURCE,
            )
            content_id = await _seed_content(
                db,
                published_at=datetime(2026, 11, 1, tzinfo=UTC),
                title="关于变更公司注册地址的公告",
            )
            await db.commit()

            assert await upsert_financial_metrics_for_content(db, content_id) == 0
            await db.commit()
            item = (
                await db.execute(select(ContentItem).where(ContentItem.id == content_id))
            ).scalar_one()
            assert item.extracted["missing_reason"] == "unparsed_report_period"
        finally:
            await _purge(db)


async def test_success_clears_stale_missing_reason() -> None:
    """上游补齐后重跑：旧的 ``no_pit_financial_version`` 必须消失，不能永久挂着。"""
    async with async_session_factory() as db:
        try:
            await _purge(db)
            stock_id = await _seed_stock(db)
            content_id = await _seed_content(
                db,
                published_at=datetime(2026, 10, 30, 9, 30, tzinfo=UTC),
                title="测试内容股2026年第三季度报告",
            )
            await db.commit()

            assert await upsert_financial_metrics_for_content(db, content_id) == 0
            await db.commit()
            item = (
                await db.execute(select(ContentItem).where(ContentItem.id == content_id))
            ).scalar_one()
            assert "no_pit_financial_version" in item.extracted["missing_reason"]

            await _seed_version(
                db,
                stock_id,
                f_ann_date=date(2026, 10, 30),
                revenue=100,
                net_profit=10,
                source=TEST_SOURCE,
            )
            await db.commit()

            assert await upsert_financial_metrics_for_content(db, content_id) == 2
            await db.commit()
            item = (
                await db.execute(select(ContentItem).where(ContentItem.id == content_id))
            ).scalar_one()
            assert "missing_reason" not in item.extracted
        finally:
            await _purge(db)


async def test_missing_fact_writes_no_zero() -> None:
    """事实表字段为空时只写缺失原因，**不写 0**（0 与"未披露"必须区分）。"""
    async with async_session_factory() as db:
        try:
            await _purge(db)
            stock_id = await _seed_stock(db)
            await _seed_version(
                db,
                stock_id,
                f_ann_date=date(2026, 10, 30),
                revenue=None,
                net_profit=10,
                source=TEST_SOURCE,
            )
            content_id = await _seed_content(
                db,
                published_at=datetime(2026, 10, 30, 9, 30, tzinfo=UTC) + timedelta(days=1),
                title="测试内容股2026年第三季度报告",
            )
            await db.commit()

            assert await upsert_financial_metrics_for_content(db, content_id) == 1
            await db.commit()

            rows = {
                row.metric_key: row
                for row in (
                    await db.execute(
                        select(ContentMetric).where(ContentMetric.content_id == content_id)
                    )
                ).scalars()
            }
            assert set(rows) == {"net_profit"}
            item = (
                await db.execute(select(ContentItem).where(ContentItem.id == content_id))
            ).scalar_one()
            assert "revenue_missing" in item.extracted["missing_reason"]
        finally:
            await _purge(db)
