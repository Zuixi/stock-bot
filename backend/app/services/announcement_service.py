"""公告快讯：巨潮采集（准实时积累）+ 读取。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers.cninfo_client import (
    ANNOUNCEMENT_CATEGORIES,
    CninfoClient,
    get_announcement_client,
)
from app.repositories import market_data_repo

logger = logging.getLogger(__name__)


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
    """按**显式窗口**（``YYYY-MM-DD~YYYY-MM-DD``）拉取并入库，返回逐类目入库行数。

    轮询（近 3 日滚动窗）与回补脚本**共用本函数**（仓库纪律：同一 ingest 不得两套实现）；
    `announcement_id` 内存去重后 `DO NOTHING`，故重复跑同一窗口是幂等的。
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
        result[category_key] = await market_data_repo.upsert_announcements(db, unique)
        logger.info("ingest_announcements %s %s -> %s", category_key, se_date, result[category_key])
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
