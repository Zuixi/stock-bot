"""EastmoneyClient 解析单测（不打真实网络，monkeypatch _get_json）。"""

import pytest

from app.core.providers.eastmoney_client import EastmoneyClient


class _FakeEM:
    """预置响应的假客户端：按 (path 末段) 返回 canned json。"""

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def _get_json(self, base: str, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        return self._responses[path.rsplit("/", 1)[-1]]


async def test_fetch_index_snapshot_parses_and_handles_dash():
    client = EastmoneyClient()
    client.__dict__["_get_json"] = _FakeEM({
        "get": {
            "rc": 0,
            "data": {
                "diff": [
                    {"f2": 64214.48, "f3": -0.17, "f4": -111.16,
                     "f12": "N225", "f13": 100, "f14": "日经225"},
                    {"f2": "-", "f3": "-", "f4": "-",
                     "f12": "KS11", "f13": 100, "f14": "韩国KOSPI"},
                ]
            },
        }
    })._get_json
    rows = await client.fetch_index_snapshot(["100.N225", "100.KS11"])
    assert rows[0] == {
        "code": "N225", "name": "日经225", "price": 64214.48,
        "pct_change": -0.17, "change": -111.16,
    }
    assert rows[1]["price"] is None and rows[1]["pct_change"] is None and rows[1]["change"] is None


async def test_fetch_sector_moneyflow_maps_fields_yuan():
    client = EastmoneyClient()
    client.__dict__["_get_json"] = _FakeEM({
        "get": {
            "rc": 0,
            "data": {
                "diff": [
                    {"f12": "BK1203", "f14": "非银金融", "f3": 0.28, "f62": 2151238400.0,
                     "f66": 1925688320.0, "f72": 225550080.0, "f104": 48, "f105": 26, "f184": 4.15,
                     "f128": "中信证券", "f136": 5.21, "f140": "600030"},
                ]
            },
        }
    })._get_json
    rows = await client.fetch_sector_moneyflow("industry")
    assert rows[0] == {
        "board_code": "BK1203", "board_name": "非银金融", "pct_change": 0.28,
        "main_net_inflow": 2151238400.0, "super_large_net": 1925688320.0, "large_net": 225550080.0,
        "up_count": 48, "down_count": 26, "main_net_ratio": 4.15,
        "lead_stock_name": "中信证券", "lead_stock_code": "600030", "lead_stock_pct": 5.21,
    }


async def test_fetch_sector_moneyflow_region_uses_t1():
    client = EastmoneyClient()
    fake = _FakeEM({"get": {"rc": 0, "data": {"diff": []}}})
    client.__dict__["_get_json"] = fake._get_json
    await client.fetch_sector_moneyflow("region")
    assert "m:90+t:1" in fake.calls[0][1]["fs"]


async def test_fetch_sector_moneyflow_concept_uses_t3():
    client = EastmoneyClient()
    fake = _FakeEM({"get": {"rc": 0, "data": {"diff": []}}})
    client.__dict__["_get_json"] = fake._get_json
    await client.fetch_sector_moneyflow("concept")
    assert "m:90+t:3" in fake.calls[0][1]["fs"]


@pytest.mark.asyncio
async def test_fetch_market_moneyflow_today_sums_two_markets():
    client = EastmoneyClient()
    client.__dict__["_get_json"] = _FakeEM({
        "get": {
            "rc": 0,
            "data": {
                "diff": [
                    {"f12": "000001", "f14": "上证指数", "f62": -100.0, "f66": -60.0,
                     "f72": -40.0, "f78": 10.0, "f84": 90.0, "f184": -1.6},
                    {"f12": "399001", "f14": "深证成指", "f62": -50.0, "f66": "-",
                     "f72": -50.0, "f78": 5.0, "f84": 45.0, "f184": -0.8},
                ]
            },
        }
    })._get_json
    payload = await client.fetch_market_moneyflow_today()
    assert payload["total"]["main_net"] == -150.0  # 沪+深合计
    assert payload["total"]["large_net"] == -90.0
    assert payload["markets"][0]["name"] == "上证指数"
    assert payload["markets"][0]["main_ratio"] == -1.6


@pytest.mark.asyncio
async def test_fetch_market_moneyflow_daily_identity_and_units():
    """恒等式 主力=大单+超大单；amount 源为亿元 → ×1e8。"""
    client = EastmoneyClient()
    line = (
        "2026-09-03,-11506802688.0,17561554944.0,-6054748160.0,"
        "-9870098432.0,-1636704256.0,-0.65,1.00,-0.34,-0.56,-0.09,"
        "3942.09,0.02,13625.12,0.10"
    )
    client.__dict__["_get_json"] = _FakeEM({"get": {"rc": 0, "data": {"klines": [line]}}})._get_json
    rows = await client.fetch_market_moneyflow_daily(5)
    assert len(rows) == 1
    r = rows[0]
    assert r["trade_date"].isoformat() == "2026-09-03"
    assert r["main_net"] == -11506802688.0
    assert abs((r["large_net"] + r["super_large_net"]) - r["main_net"]) < 1.0
    assert r["mid_net"] == -6054748160.0
    assert r["small_net"] == 17561554944.0
    assert r["close"] == 3942.09
    assert r["amount"] == 13625.12 * 1e8


@pytest.mark.asyncio
async def test_fetch_market_moneyflow_daily_skips_broken_identity():
    client = EastmoneyClient()
    bad = "2026-09-02,-2.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,1.0,0.0,1.0,0.0"
    client.__dict__["_get_json"] = _FakeEM({"get": {"rc": 0, "data": {"klines": [bad]}}})._get_json
    assert await client.fetch_market_moneyflow_daily(5) == []
