"""公告快讯：巨潮采集（准实时积累）+ 读取。

采集**只写内容事实层**（``content_items`` / ``content_entities``）：旧 ``announcements``
表已不再是写入路径（单写路径纪律）。owner 裁定只存链接：``url`` 即原文入口，
**不落正文/PDF**（``content_items`` 上也没有 ``body`` / ``body_ref`` 列）。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers.cninfo_client import (
    ANNOUNCEMENT_CATEGORIES,
    CninfoClient,
    get_announcement_client,
)
from app.repositories import content_repo, market_data_repo
from app.services.content_ingest.entity_resolver import resolve_stock_entities
from app.services.extraction.financial_join import upsert_financial_metrics_for_content

logger = logging.getLogger(__name__)

CONTENT_TYPE = "announcement"
SOURCE_NAME = "cninfo"
PUBLISHER_TIER = "legal"  # 公告是法定披露，不是卖方/媒体


def _raw_hash(announcement_id: str) -> str:
    """去重键：公告唯一 id 的稳定哈希（正文不入库，哈希即身份）。"""
    return hashlib.sha256(f"cninfo:{announcement_id}".encode()).hexdigest()


def _content_row(r: dict[str, Any]) -> dict[str, Any]:
    """cninfo 原始行 → ``content_items`` 行（只存链接，无正文/PDF）。"""
    # cninfo 映射给的是**上海墙上时间**（naive）；``published_at`` 是 timestamptz，
    # 直接塞 naive 会被 asyncpg 当 UTC，晚间的公告会整体偏 8 小时、PIT 日期跟着错。
    published_at = r["announce_time"]
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return {
        "content_type": CONTENT_TYPE,
        "source_name": SOURCE_NAME,
        "publisher_tier": PUBLISHER_TIER,
        "publisher": r.get("sec_name"),
        "title": r["title"],
        "published_at": published_at,
        "url": r["pdf_url"],
        "raw_hash": _raw_hash(r["announcement_id"]),
        "extracted": {
            "source_item_id": r["announcement_id"],
            "subtype": r["category"],
            "sec_code": r["sec_code"],
            "sec_name": r["sec_name"],
        },
    }


def _entities(row: dict[str, Any]) -> list[tuple[str, str]]:
    """该公告关联的实体：自身 ``sec_code`` 一定入，标题里的其它 A 股代码/名称再补。

    按文本顺序去重（``insert_content_entities`` 的复合主键本身幂等，这里只是不制造
    同一批的重复行）。
    """
    extracted = row["extracted"]
    sec_code: str | None = extracted.get("sec_code")
    sec_name: str | None = extracted.get("sec_name")
    found: list[tuple[str, str]] = [("stock", sec_code)] if sec_code else []
    if sec_code and sec_name:
        found.extend(resolve_stock_entities(row["title"], {sec_name: sec_code}))
    return list(dict.fromkeys(found))


def normalize_category(category: str | None) -> str | None:
    """读面的类目过滤校验：未知类目**报错**，不得静默退化为"全部"。

    为什么必要（ADR 0019 硬纪律）：cninfo 的 `category` 参数对**无效 code 静默退化为全量**
    （实测 2026-10-16：不存在的 `category_gddj_szsh` 返回与不带类目完全相同的 9,236 条）。
    采集侧由 `_assert_announcement_categories` + 分页探针兜住；读面这一层也必须显式拒绝，
    否则 `?category=` 写错就会把全量公告当成"某一类"返回给用户。
    """
    if category is None or category == "":
        return None
    if category not in ANNOUNCEMENT_CATEGORIES:
        raise ValueError(f"未知公告类目 {category!r}；可用：{', '.join(ANNOUNCEMENT_CATEGORIES)}")
    return category


def _se_date(days: int) -> str:
    """cninfo seDate 区间（``YYYY-MM-DD~YYYY-MM-DD``，上海时区语义）。"""
    from app.services.market_data_service import _today_sh  # noqa: PLC0415

    start = _today_sh() - timedelta(days=days)
    return f"{start.isoformat()}~{_today_sh().isoformat()}"


async def ingest_announcements_window(
    db: AsyncSession,
    se_date: str,
    categories: tuple[str, ...] | None = None,
) -> dict[str, int]:
    """按**显式窗口**（``YYYY-MM-DD~YYYY-MM-DD``）拉取并写**内容事实层**，返回逐类目新增行数。

    轮询（近 3 日滚动窗）与回补脚本**共用本函数**（仓库纪律：同一 ingest 不得两套实现）；
    幂等由 ``content_items`` 的 ``(source_name, raw_hash)`` 唯一键 + ``DO NOTHING`` 兜住，
    故重复跑同一窗口第二次返回 0。

    ``pdf_url`` 缺失的行**不入库**（只存链接 = 没有链接就没有原文入口/无法溯源）。

    Returns:
        逐类目的**新增**行数（重放已入库的窗口 → 0），保持旧的 ``dict[str, int]`` 契约。
    """
    client: CninfoClient = get_announcement_client()
    result: dict[str, int] = {}
    for category_key in categories or ANNOUNCEMENT_CATEGORIES:
        rows = await client.fetch_announcements(se_date, category_key)  # type: ignore[arg-type]
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for r in rows:
            if r["announcement_id"] not in seen:
                seen.add(r["announcement_id"])
                unique.append(r)
        with_url: list[dict[str, Any]] = []
        for r in unique:
            if not r.get("pdf_url"):
                # 没有原文入口的公告无法溯源（owner 裁定只存链接）→ 不入库
                logger.warning(
                    "skip announcement without pdf_url: %s %s %s",
                    r.get("announcement_id"),
                    r.get("sec_code"),
                    r.get("title"),
                )
                continue
            with_url.append(r)

        content_rows = [_content_row(r) for r in with_url]
        inserted = 0
        for batch in market_data_repo.chunk_rows(content_rows):
            inserted += await content_repo.upsert_content_items(db, batch)

        ids_by_hash = await content_repo.get_content_ids_by_hashes(
            db, [row["raw_hash"] for row in content_rows], SOURCE_NAME
        )
        for row in content_rows:
            content_id = ids_by_hash.get(row["raw_hash"])
            if content_id is None:  # 已在库里但被裁掉/不存在 → 跳过，不抛错
                continue
            await content_repo.insert_content_entities(db, content_id, _entities(row))
            if row["extracted"].get("subtype") == "report":
                # 定期报告核心数字走结构化 PIT join（不解析 PDF）；非报告公告无此步。
                await upsert_financial_metrics_for_content(db, content_id)

        result[category_key] = inserted
        logger.info("ingest_announcements %s %s -> %s", category_key, se_date, inserted)
    return result


async def ingest_announcements(db: AsyncSession, days: int = 3) -> dict[str, int]:
    """**全部已登记类目**（含股本变动）各拉一次近 ``days`` 日窗口（轮询入口）。

    Returns:
        逐类目的入库行数，键 = ``ANNOUNCEMENT_CATEGORIES``（新增类目会**自动**出现在这里，
        不会因为某处漏改而静默漏采）。
    """
    return await ingest_announcements_window(db, _se_date(days))


ANNOUNCEMENTS_CACHE_KEY = "market:announcements:{category}:{symbol}"
ANNOUNCEMENTS_CACHE_LIMIT = 100  # 端点 limit 上限：缓存整页、按请求切片，limit 不进缓存键
ANNOUNCEMENTS_TTL = 300


async def get_announcements(
    cache: Any | None,
    symbol: str | None = None,
    limit: int = 30,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """公告快讯按披露时间倒序（symbol / category 均可选过滤）。

    缓存 5 分钟；整页缓存 100 行、按请求 `limit` 切片（`limit` 不进缓存键）。
    """
    normalized = normalize_category(category)
    key = ANNOUNCEMENTS_CACHE_KEY.format(category=normalized or "all", symbol=symbol or "all")
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached: list[dict[str, Any]] = cached
            return rows_cached[:limit]
    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        listed = await market_data_repo.list_announcements(
            db, symbol, ANNOUNCEMENTS_CACHE_LIMIT, normalized
        )
        for a in listed:
            rows.append(
                {
                    "announcement_id": a.announcement_id,
                    "sec_code": a.sec_code,
                    "sec_name": a.sec_name,
                    "title": a.title,
                    "announce_time": a.announce_time.isoformat(),
                    "category": a.category,
                    "pdf_url": a.pdf_url,
                }
            )
    if cache is not None and rows:
        await cache.set(key, rows, ttl=ANNOUNCEMENTS_TTL)
    return rows[:limit]
