"""东财研报采集写内容事实层（``app/services/content_ingest/research_report.py``）。

钉住四件事：

1. **幂等**：同一窗口重跑第二次 ``inserted=0``，内容行只有一条，指标 upsert 覆盖。
2. **结构化指标**：评级/评级变动/目标价/EPS/PE 的 ``period`` 与 ``unit`` 对齐计划表。
3. **去重标记**：同 ``publisher + stock + rating_change`` 只留最新一条，旧条目写
   ``extracted.superseded_by``；**乱序回补**（先入新的、后入旧的）同样指向新的。
4. **不可溯源的行不入库**：缺 ``stock_code`` / ``pdf_url`` → 计入 ``skipped``、无内容行。

库不可达时整文件跳过（``requires_pg``，与 ``test_content_repo.py`` 同口径）。
用例自建自清：``finally`` 里按 ``raw_hash`` 删内容行（实体/指标靠 ``ON DELETE CASCADE``）。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.database import engine
from app.models.content import ContentEntity, ContentItem, ContentMetric
from app.services.content_ingest import research_report as rr

SHANGHAI = ZoneInfo("Asia/Shanghai")
BEGIN = date(2026, 11, 1)
END = date(2026, 11, 11)

#: 2026-11-11 入的是 2026/2027/2028 三年预测。
YEAR = 2026


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
    reason="PostgreSQL unavailable (content layer ingest needs alembic-upgraded DB)",
)

pytestmark = requires_pg


@asynccontextmanager
async def _session() -> Any:
    """每个用例独立 engine：全局 pool 的连接可能绑在别的（已关闭的）事件循环上，
    全量跑时会在第一个 `execute` 抛 `attached to a different loop`（实测）。"""
    eng = create_async_engine(engine.url.render_as_string(hide_password=False))
    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            yield session
    finally:
        await eng.dispose()


def _row(source_item_id: str, **overrides: Any) -> dict[str, Any]:
    """一条 ``EastmoneyReportClient.fetch_reports`` 真实形状的行。"""
    row: dict[str, Any] = {
        "source_item_id": source_item_id,
        "title": "牧原股份(002714)三季报点评：成本领先，出栏稳增",
        "stock_code": "002714",
        "stock_name": "牧原股份",
        "org_name": "华泰证券",
        "published_at": datetime(2026, 11, 11, tzinfo=SHANGHAI),
        "rating": "买入",
        "rating_change": 1,
        "target_price": 55.5,
        "eps_this_year": 2.1,
        "eps_next_year": 2.6,
        "eps_next_two_year": 3.0,
        "pe_this_year": 20.1,
        "pe_next_year": 16.2,
        "pe_next_two_year": 14.0,
        "industry": "养殖业",
        "industry_code": "BK0477",
        "researcher": "张三",
        "pdf_url": f"https://pdf.dfcfw.com/pdf/H3_{source_item_id}_1.pdf?encode",
    }
    row.update(overrides)
    return row


class _FakeClient:
    """最小东财 client：只实现 service 真正调用的那一个方法。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[date, date]] = []

    async def fetch_reports(
        self, begin_date: date, end_date: date, **_: Any
    ) -> list[dict[str, Any]]:
        self.calls.append((begin_date, end_date))
        return list(self.rows)


def _raw_hashes(*source_item_ids: str) -> tuple[str, ...]:
    return tuple(rr._raw_hash(item_id) for item_id in source_item_ids)


async def _purge(db: Any, raw_hashes: tuple[str, ...]) -> None:
    """只删本用例写的内容行（实体/指标 CASCADE 带走）。"""
    await db.execute(
        delete(ContentItem).where(
            ContentItem.source_name == rr.SOURCE_NAME,
            ContentItem.raw_hash.in_(list(raw_hashes)),
        )
    )
    await db.commit()


async def _item(db: Any, raw_hash: str) -> ContentItem:
    return (
        await db.execute(
            select(ContentItem).where(
                ContentItem.source_name == rr.SOURCE_NAME,
                ContentItem.raw_hash == raw_hash,
            )
        )
    ).scalar_one()


async def test_ingest_is_idempotent_and_writes_structured_metrics() -> None:
    """两行连跑两遍：第二次 ``inserted=0``；内容/实体/指标口径逐一核对。"""
    first_id, second_id = "rr-test-0001", "rr-test-0002"
    raw_hashes = _raw_hashes(first_id, second_id)
    rows = [
        _row(first_id),
        _row(
            second_id,
            title="温氏股份(300498)月报：黄鸡景气修复",
            stock_code="300498",
            stock_name="温氏股份",
            org_name="中信证券",
            rating="增持",
            rating_change=0,
            target_price=None,
            industry=None,
            industry_code=None,
        ),
    ]
    client = _FakeClient(rows)

    async with _session() as db:
        try:
            await _purge(db, raw_hashes)

            first = await rr.ingest_research_reports(db, BEGIN, END, client=client)
            await db.commit()
            second = await rr.ingest_research_reports(db, BEGIN, END, client=client)
            await db.commit()

            assert first == {"fetched": 2, "inserted": 2, "skipped": 0}
            assert second == {"fetched": 2, "inserted": 0, "skipped": 0}
            assert client.calls == [(BEGIN, END), (BEGIN, END)]

            item = await _item(db, rr._raw_hash(first_id))
            assert item.content_type == "report"
            assert item.source_name == "eastmoney_report"
            assert item.publisher_tier == "sellside"
            assert item.publisher == "华泰证券"
            assert item.url.startswith("https://pdf.dfcfw.com/pdf/H3_")
            # 客户端契约：published_at 是上海 aware 的那个 instant（timestamptz 读回 UTC）
            assert item.published_at == datetime(2026, 11, 11, tzinfo=SHANGHAI)
            assert item.published_at.utcoffset() is not None
            assert item.extracted["source_item_id"] == first_id
            assert item.extracted["stock_code"] == "002714"
            assert item.extracted["industry"] == "养殖业"
            assert item.extracted["rating_change"] == 1
            assert item.extracted["core_view_status"] == "not_extracted"
            assert "missing_reason" not in item.extracted

            entities = (
                await db.execute(
                    select(ContentEntity.entity_type, ContentEntity.entity_id)
                    .where(ContentEntity.content_id == item.id)
                    .order_by(ContentEntity.entity_type)
                )
            ).all()
            assert entities == [("industry", "养殖业"), ("stock", "002714")]

            metrics = {
                (row.metric_key, row.period): row
                for row in (
                    (
                        await db.execute(
                            select(ContentMetric).where(ContentMetric.content_id == item.id)
                        )
                    )
                    .scalars()
                    .all()
                )
            }
            rating = metrics[("rating", None)]
            assert rating.value_text == "买入"
            assert rating.value is None
            assert rating.unit is None

            change = metrics[("rating_change", None)]
            assert float(change.value) == 1.0

            target = metrics[("target_price", None)]
            assert float(target.value) == pytest.approx(55.5)
            assert target.unit == "CNY"

            for offset, eps in enumerate((2.1, 2.6, 3.0)):
                key = ("eps_forecast", f"{YEAR + offset}E")
                assert float(metrics[key].value) == pytest.approx(eps)
                assert metrics[key].unit == "CNY"
            for offset, pe in enumerate((20.1, 16.2, 14.0)):
                key = ("pe_forecast", f"{YEAR + offset}E")
                assert float(metrics[key].value) == pytest.approx(pe)
                assert metrics[key].unit == "x"

            span = target.source_span
            assert span["kind"] == "structured"
            assert span["table"] == "eastmoney_report_list"
            assert span["key"] == first_id
            assert "rating=买入" in span["quote"]
            assert "target_price=55.5" in span["quote"]
            assert "eps_2026=2.1" in span["quote"]

            # 第二行：无目标价/行业 → 指标缺席（不写 0），缺失 key 落 extracted
            other = await _item(db, rr._raw_hash(second_id))
            assert "target_price" in other.extracted["missing_reason"].split(",")
            other_metrics = (
                (
                    await db.execute(
                        select(ContentMetric).where(ContentMetric.content_id == other.id)
                    )
                )
                .scalars()
                .all()
            )
            assert all(m.metric_key != "target_price" for m in other_metrics)
            assert ("industry", "养殖业") not in {
                (e.entity_type, e.entity_id)
                for e in (
                    await db.execute(
                        select(ContentEntity).where(ContentEntity.content_id == other.id)
                    )
                )
                .scalars()
                .all()
            }
        finally:
            await _purge(db, raw_hashes)


async def test_dedup_marks_older_report_superseded() -> None:
    """同 publisher/stock/rating_change 两行（新的在前）：旧的写 ``superseded_by``。"""
    newer_id, older_id = "rr-test-0101", "rr-test-0102"
    raw_hashes = _raw_hashes(newer_id, older_id)
    rows = [
        _row(newer_id, published_at=datetime(2026, 11, 11, tzinfo=SHANGHAI)),
        _row(older_id, published_at=datetime(2026, 11, 3, tzinfo=SHANGHAI)),
    ]

    async with _session() as db:
        try:
            await _purge(db, raw_hashes)
            result = await rr.ingest_research_reports(db, BEGIN, END, client=_FakeClient(rows))
            await db.commit()
            assert result == {"fetched": 2, "inserted": 2, "skipped": 0}

            newer = await _item(db, rr._raw_hash(newer_id))
            older = await _item(db, rr._raw_hash(older_id))
            assert older.extracted["superseded_by"] == newer.id
            assert "superseded_by" not in newer.extracted
        finally:
            await _purge(db, raw_hashes)


async def test_dedup_handles_reverse_ingest_order() -> None:
    """乱序回补：先入新的、后入旧的 → 旧条目仍指向已存在的新条目。"""
    newer_id, older_id = "rr-test-0201", "rr-test-0202"
    raw_hashes = _raw_hashes(newer_id, older_id)
    newer_row = _row(newer_id, published_at=datetime(2026, 11, 11, tzinfo=SHANGHAI))
    older_row = _row(older_id, published_at=datetime(2026, 11, 4, tzinfo=SHANGHAI))

    async with _session() as db:
        try:
            await _purge(db, raw_hashes)
            await rr.ingest_research_reports(db, BEGIN, END, client=_FakeClient([newer_row]))
            await db.commit()
            newer = await _item(db, rr._raw_hash(newer_id))
            assert "superseded_by" not in newer.extracted

            await rr.ingest_research_reports(db, BEGIN, END, client=_FakeClient([older_row]))
            await db.commit()

            older = await _item(db, rr._raw_hash(older_id))
            assert older.extracted["superseded_by"] == newer.id
            # 新的那条不被反向覆盖
            refreshed_newer = await _item(db, rr._raw_hash(newer_id))
            assert "superseded_by" not in refreshed_newer.extracted
        finally:
            await _purge(db, raw_hashes)


async def test_ingest_skips_rows_without_stock_code_or_pdf_url() -> None:
    """没有 6 位股票代码 / 原文链接 = 不可溯源 → 不入库，计入 ``skipped``。"""
    no_stock_id, no_pdf_id = "rr-test-0301", "rr-test-0302"
    raw_hashes = _raw_hashes(no_stock_id, no_pdf_id)
    rows = [
        _row(no_stock_id, stock_code=None),
        _row(no_pdf_id, pdf_url=None),
    ]

    async with _session() as db:
        try:
            await _purge(db, raw_hashes)
            result = await rr.ingest_research_reports(db, BEGIN, END, client=_FakeClient(rows))
            await db.commit()
            assert result == {"fetched": 2, "inserted": 0, "skipped": 2}

            stored = (
                (
                    await db.execute(
                        select(ContentItem).where(
                            ContentItem.source_name == rr.SOURCE_NAME,
                            ContentItem.raw_hash.in_(list(raw_hashes)),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert stored == []
        finally:
            await _purge(db, raw_hashes)


def test_numeric_rejects_nan() -> None:
    """``float('nan')`` 不是可用数值；写成指标会把 NaN 带进 UI/排序。"""
    assert rr._numeric(float("nan")) is None
    assert rr._numeric("nan") is None
    assert rr._numeric("") is None
    assert rr._numeric(0) == 0.0


async def test_skips_row_with_empty_title() -> None:
    """没有标题的研报不可读，也不该占一条内容行。"""
    rows = [_row("EMPTY-TITLE-1", title="   ")]
    fake = _FakeClient(rows)
    raw_hashes = _raw_hashes("EMPTY-TITLE-1")
    async with _session() as db:
        try:
            await _purge(db, raw_hashes)
            result = await rr.ingest_research_reports(db, BEGIN, END, client=fake)
            await db.commit()
            assert result["skipped"] == 1
            stored = (
                (
                    await db.execute(
                        select(ContentItem).where(ContentItem.raw_hash.in_(list(raw_hashes)))
                    )
                )
                .scalars()
                .all()
            )
            assert stored == []
        finally:
            await _purge(db, raw_hashes)
