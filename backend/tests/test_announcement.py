"""公告：cninfo 映射 + 类目配置面 + 分页截断守卫 + service 去重（monkeypatch）。"""

from datetime import datetime

import pytest

from app.services import announcement_service as anns


@pytest.mark.asyncio
async def test_client_fetch_maps(monkeypatch):
    from app.core.providers.cninfo_client import CninfoClient

    client = CninfoClient()

    async def fake_post(url, data):
        assert data["category"].startswith("category_yjdbg_szsh;")
        return {
            "announcements": [
                {
                    "announcementId": "1225542181",
                    "secCode": "002762",
                    "secName": "金发拉比",
                    "announcementTitle": "<em>金发拉比</em>2026年半年度报告",
                    "announcementTime": 1788278400000,
                    "adjunctUrl": "finalpage/2026-09-02/1225542181.PDF",
                }
            ]
        }

    monkeypatch.setattr(client, "_post_json", fake_post)
    rows = await client.fetch_announcements(
        "2026-08-20~2026-09-03", "report", page_size=30, max_pages=1
    )
    assert rows == [
        {
            "announcement_id": "1225542181",
            "sec_code": "002762",
            "sec_name": "金发拉比",
            "title": "金发拉比2026年半年度报告",
            # 1788278400000ms = 2026-09-01 16:00 UTC = 上海 2026-09-02 00:00（naive 上海 wall-clock）  # noqa: E501
            "announce_time": datetime(2026, 9, 2, 0, 0),
            "category": "report",
            "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-09-02/1225542181.PDF",
        }
    ]


def test_category_config_is_self_consistent():
    """类目配置面（ADR 0019 硬纪律：漏登记一个类目 = 静默漏采）。

    - 三条配置表（类目元组 / category code / 分页上限）必须覆盖同一集合；
    - `equity_change` 必须指向股本变动类目 `category_gqbd_szsh`（实测 1102 条/7 交易日的那个）；
    - code 不得重复（否则两个类目互相污染）。
    """
    from app.core.providers.cninfo_client import (
        ANNOUNCEMENT_CATEGORIES,
        ANNOUNCEMENT_CATEGORY_QUERY,
        ANNOUNCEMENT_FETCH_LIMITS,
    )

    assert set(ANNOUNCEMENT_CATEGORIES) == {"report", "event", "equity_change"}
    assert set(ANNOUNCEMENT_CATEGORY_QUERY) == set(ANNOUNCEMENT_CATEGORIES)
    assert set(ANNOUNCEMENT_FETCH_LIMITS) == set(ANNOUNCEMENT_CATEGORIES)
    assert ANNOUNCEMENT_CATEGORY_QUERY["equity_change"] == "category_gqbd_szsh"
    codes = list(ANNOUNCEMENT_CATEGORY_QUERY.values())
    assert len(set(codes)) == len(codes)
    # 高体量类目必须给更大分页（否则 3 天窗口会被静默截断）
    assert (
        ANNOUNCEMENT_FETCH_LIMITS["equity_change"][0]
        * ANNOUNCEMENT_FETCH_LIMITS["equity_change"][1]
        > ANNOUNCEMENT_FETCH_LIMITS["event"][0] * ANNOUNCEMENT_FETCH_LIMITS["event"][1]
    )


def _raw(announcement_id: str) -> dict:
    """构造一条**合法**的原始 cninfo 记录（page 级 monkeypatch 之后仍会过映射函数）。"""
    return {
        "announcementId": announcement_id,
        "secCode": "000001",
        "secName": "X",
        "announcementTitle": f"<em>X</em>{announcement_id}",
        "announcementTime": 1788278400000,
        "adjunctUrl": f"finalpage/2026-09-02/{announcement_id}.PDF",
    }


@pytest.mark.asyncio
async def test_fetch_uses_per_category_page_size(monkeypatch):
    """分页参数必须取该类目的档位（`equity_change` 与 `report` 不同档）。"""
    from app.core.providers.cninfo_client import (
        ANNOUNCEMENT_FETCH_LIMITS,
        CninfoClient,
    )

    client = CninfoClient()
    seen: list[dict] = []

    async def fake_post(url, data):
        seen.append(data)
        return {"announcements": []}

    monkeypatch.setattr(client, "_post_json", fake_post)
    await client.fetch_announcements("2026-10-08~2026-10-16", "equity_change")
    assert seen[0]["pageSize"] == ANNOUNCEMENT_FETCH_LIMITS["equity_change"][0]
    assert seen[0]["category"] == "category_gqbd_szsh"


@pytest.mark.asyncio
async def test_fetch_raises_when_truncated(monkeypatch):
    """跑满分页且探针页仍有数据 ⇒ **响亮失败**，绝不静默截断。

    实测背景（2026-10-16）：`equity_change` ≈ 157 条/交易日，用旧的 30×10 档会在
    3 天窗口被截断；而 cninfo 分页只回最新页，被截掉的更早页永久不回补。
    """
    from app.core.providers.cninfo_client import CninfoClient

    client = CninfoClient()

    async def always_full(se_date, category_key, page, page_size):
        return [_raw(f"{category_key}-{page}-{i}") for i in range(page_size)]

    monkeypatch.setattr(client, "_fetch_announcement_page", always_full)
    with pytest.raises(RuntimeError, match="超出分页上限"):
        await client.fetch_announcements("2026-10-08~2026-10-16", "event", page_size=2, max_pages=3)


@pytest.mark.asyncio
async def test_fetch_accepts_exact_multiple(monkeypatch):
    """恰好整页整除（第 max_pages+1 页为空）**不得**误报截断（假阳性会逼人放宽规则）。"""
    from app.core.providers.cninfo_client import CninfoClient

    client = CninfoClient()

    async def exact(se_date, category_key, page, page_size):
        if page <= 2:
            return [_raw(f"p{page}-{i}") for i in range(page_size)]
        return []

    monkeypatch.setattr(client, "_fetch_announcement_page", exact)
    rows = await client.fetch_announcements(
        "2026-10-08~2026-10-16", "event", page_size=2, max_pages=2
    )
    assert len(rows) == 4


def test_normalize_category_rejects_unknown():
    """读面类目过滤：未知类目必须报错（实测 cninfo 对无效 code 静默退化为全量）。"""
    assert anns.normalize_category(None) is None
    assert anns.normalize_category("") is None
    assert anns.normalize_category("equity_change") == "equity_change"
    with pytest.raises(ValueError, match="未知公告类目"):
        anns.normalize_category("category_gddj_szsh")


@pytest.mark.asyncio
async def test_ingest_announcements_dedupes(monkeypatch):
    async def fake_fetch(self, se_date, category_key, page_size=None, max_pages=None):
        if category_key == "report":
            return [
                {
                    "announcement_id": "A1",
                    "sec_code": "000001",
                    "sec_name": "X",
                    "title": "t1",
                    "announce_time": datetime(2026, 9, 2, 8, 0),
                    "category": "report",
                    "pdf_url": None,
                },
                {
                    "announcement_id": "A1",
                    "sec_code": "000001",
                    "sec_name": "X",
                    "title": "t1",
                    "announce_time": datetime(2026, 9, 2, 8, 0),
                    "category": "report",
                    "pdf_url": None,
                },
            ]
        return []

    inserted: list = []

    async def fake_upsert(db, rows):
        inserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(anns.CninfoClient, "fetch_announcements", fake_fetch)
    monkeypatch.setattr(anns.market_data_repo, "upsert_announcements", fake_upsert)

    result = await anns.ingest_announcements(db=None, days=3)
    assert result == {"report": 1, "event": 0, "equity_change": 0}
    assert len(inserted) == 1


@pytest.mark.asyncio
async def test_read_surface_passes_category_through(monkeypatch):
    """读面把类目透传到 repo（缓存键也必须带类目，否则 report 与 equity_change 互相污染）。"""
    captured: dict = {}

    async def fake_list(db, symbol, limit, category=None):
        captured["symbol"] = symbol
        captured["category"] = category
        captured["limit"] = limit
        return []

    monkeypatch.setattr(anns.market_data_repo, "list_announcements", fake_list)
    rows = await anns.get_announcements(cache=None, symbol=None, limit=5, category="equity_change")
    assert rows == []
    assert captured["category"] == "equity_change"
    assert anns.ANNOUNCEMENTS_CACHE_KEY.format(category="equity_change", symbol="all") != (
        anns.ANNOUNCEMENTS_CACHE_KEY.format(category="report", symbol="all")
    )


@pytest.mark.asyncio
async def test_announcements_endpoint_rejects_unknown_category():
    """端点层：未知类目必须 **400**（不得静默退化为"全部"）。

    实测背景（2026-10-16）：cninfo 对不存在的 `category` code 直接返回全量（9,236 条）——
    若不显式拒绝，`?category=` 写错就会把全量公告当"某一类"返回（ADR 0019 硬纪律）。
    """
    from fastapi import HTTPException

    from app.api.v1 import market_data as api

    with pytest.raises(HTTPException) as excinfo:
        await api.get_announcements_endpoint(
            cache=None, symbol=None, limit=5, category="category_gddj_szsh"
        )
    assert excinfo.value.status_code == 400
    assert "未知公告类目" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_announcements_endpoint_passes_category(monkeypatch):
    """端点层：合法类目透传到 service/repo（并回显行）。"""
    from app.api.v1 import market_data as api

    captured: dict = {}

    async def fake_list(db, symbol, limit, category=None):
        captured["category"] = category
        return []

    monkeypatch.setattr(anns.market_data_repo, "list_announcements", fake_list)
    rows = await api.get_announcements_endpoint(
        cache=None, symbol=None, limit=5, category="equity_change"
    )
    assert rows == []
    assert captured["category"] == "equity_change"
