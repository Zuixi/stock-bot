"""公告快讯：巨潮采集（准实时积累）+ 读取。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers.cninfo_client import CninfoClient, get_announcement_client
from app.repositories import market_data_repo

logger = logging.getLogger(__name__)


def _se_date(days: int) -> str:
    """cninfo seDate 区间（``YYYY-MM-DD~YYYY-MM-DD``，上海时区语义）。"""
    from app.services.market_data_service import _today_sh  # noqa: PLC0415

    start = _today_sh() - timedelta(days=days)
    return f"{start.isoformat()}~{_today_sh().isoformat()}"


async def ingest_announcements(db: AsyncSession, days: int = 3) -> dict[str, int]:
    """财报 + 重大事项两类目各拉一次，announcement_id 内存去重后 DO NOTHING 入库。

    Returns:
        {"report": 入库行数, "event": 入库行数}
    """
    client: CninfoClient = get_announcement_client()
    se_date = _se_date(days)
    result: dict[str, int] = {}
    for category_key in ("report", "event"):
        rows = await client.fetch_announcements(se_date, category_key)  # type: ignore[arg-type]
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for r in rows:
            if r["announcement_id"] not in seen:
                seen.add(r["announcement_id"])
                unique.append(r)
        result[category_key] = await market_data_repo.upsert_announcements(db, unique)
        logger.info("ingest_announcements %s -> %s", category_key, result[category_key])
    return result


ANNOUNCEMENTS_CACHE_KEY = "market:announcements:{symbol}"
ANNOUNCEMENTS_CACHE_LIMIT = 100  # 端点 limit 上限：缓存整页、按请求切片，limit 不进缓存键
ANNOUNCEMENTS_TTL = 300


async def get_announcements(
    cache: Any | None, symbol: str | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    """公告快讯按披露时间倒序（symbol 可选过滤；缓存 5 分钟，整页 100 行按请求切片）。"""
    key = ANNOUNCEMENTS_CACHE_KEY.format(symbol=symbol or "all")
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            rows_cached: list[dict[str, Any]] = cached
            return rows_cached[:limit]
    from app.core.database import async_session_factory  # noqa: PLC0415

    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for a in await market_data_repo.list_announcements(db, symbol, ANNOUNCEMENTS_CACHE_LIMIT):
            rows.append({
                "announcement_id": a.announcement_id, "sec_code": a.sec_code,
                "sec_name": a.sec_name, "title": a.title,
                "announce_time": a.announce_time.isoformat(),
                "category": a.category, "pdf_url": a.pdf_url,
            })
    if cache is not None and rows:
        await cache.set(key, rows, ttl=ANNOUNCEMENTS_TTL)
    return rows[:limit]
