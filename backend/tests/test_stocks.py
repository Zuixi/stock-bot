"""Tests for stock endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_stocks_empty(client: AsyncClient) -> None:
    response = await client.get("/api/v1/exchanges/Shanghai_Stocks/stocks")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_list_exchanges(client: AsyncClient) -> None:
    response = await client.get("/api/v1/exchanges")
    assert response.status_code == 200
    exchanges = response.json()
    codes = {e["code"] for e in exchanges}
    assert "Shanghai_Stocks" in codes
    assert "Shenzen_Stocks" in codes
    assert "Beijing_Stocks" in codes


@pytest.mark.asyncio
async def test_get_stock_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/exchanges/Shanghai_Stocks/stocks/999999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stock_enriched_exposes_latest_quote_date(client: AsyncClient) -> None:
    """Enriched 响应必须携带最新行情的 trade_date —— 行情口径的"数据截至"。

    stocks.asof 是名录元数据 ingest 时间（可能长期停更），前端个股头部
    "数据截至" 改绑本字段；断言其为 YYYY-MM-DD 形状而非完整 UTC 时间戳。
    """
    list_resp = await client.get(
        "/api/v1/exchanges/Shanghai_Stocks/stocks", params={"page_size": 1}
    )
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert items, "stocks table should not be empty in a seeded database"
    symbol = items[0]["symbol"]

    resp = await client.get(
        f"/api/v1/exchanges/Shanghai_Stocks/stocks/{symbol}/enriched"
    )
    assert resp.status_code == 200
    body = resp.json()
    quote_date = body.get("latest_quote_date")
    if quote_date is None:
        pytest.skip(f"stock {symbol} has no daily_quotes rows yet")
    assert len(quote_date) == 10 and quote_date[4] == "-" and quote_date[7] == "-"
    assert "T" not in quote_date and "Z" not in quote_date

