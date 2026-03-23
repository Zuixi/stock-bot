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
