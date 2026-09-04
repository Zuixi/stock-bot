"""公告：cninfo 映射 + service 去重（monkeypatch）。"""

from datetime import datetime

import pytest

from app.services import announcement_service as anns


@pytest.mark.asyncio
async def test_client_fetch_maps(monkeypatch):
    from app.core.providers.cninfo_client import CninfoClient

    client = CninfoClient()

    async def fake_post(url, data):
        assert data["category"].startswith("category_yjdbg_szsh;")
        return {"announcements": [
            {"announcementId": "1225542181", "secCode": "002762", "secName": "金发拉比",
             "announcementTitle": "<em>金发拉比</em>2026年半年度报告",
             "announcementTime": 1788278400000,
             "adjunctUrl": "finalpage/2026-09-02/1225542181.PDF"}
        ]}

    monkeypatch.setattr(client, "_post_json", fake_post)
    rows = await client.fetch_announcements(
        "2026-08-20~2026-09-03", "report", page_size=30, max_pages=1
    )
    assert rows == [{
        "announcement_id": "1225542181", "sec_code": "002762", "sec_name": "金发拉比",
        "title": "金发拉比2026年半年度报告",
        # 1788278400000ms = 2026-09-01 16:00 UTC = 上海 2026-09-02 00:00（naive 上海 wall-clock）
        "announce_time": datetime(2026, 9, 2, 0, 0),
        "category": "report",
        "pdf_url": "http://static.cninfo.com.cn/finalpage/2026-09-02/1225542181.PDF",
    }]


@pytest.mark.asyncio
async def test_ingest_announcements_dedupes(monkeypatch):
    async def fake_fetch(self, se_date, category_key, page_size=30, max_pages=5):
        if category_key == "report":
            return [
                {"announcement_id": "A1", "sec_code": "000001", "sec_name": "X",
                 "title": "t1", "announce_time": datetime(2026, 9, 2, 8, 0),
                 "category": "report", "pdf_url": None},
                {"announcement_id": "A1", "sec_code": "000001", "sec_name": "X",
                 "title": "t1", "announce_time": datetime(2026, 9, 2, 8, 0),
                 "category": "report", "pdf_url": None},
            ]
        return []

    inserted: list = []

    async def fake_upsert(db, rows):
        inserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(anns.CninfoClient, "fetch_announcements", fake_fetch)
    monkeypatch.setattr(anns.market_data_repo, "upsert_announcements", fake_upsert)

    result = await anns.ingest_announcements(db=None, days=3)
    assert result == {"report": 1, "event": 0}
    assert len(inserted) == 1
