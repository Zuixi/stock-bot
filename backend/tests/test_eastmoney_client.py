"""EastmoneyClient 解析单测（不打真实网络，monkeypatch _get_json）。"""

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
                     "f66": 1925688320.0, "f72": 225550080.0, "f104": 48, "f105": 26, "f184": 4.15},
                ]
            },
        }
    })._get_json
    rows = await client.fetch_sector_moneyflow("industry")
    assert rows[0] == {
        "board_code": "BK1203", "board_name": "非银金融", "pct_change": 0.28,
        "main_net_inflow": 2151238400.0, "super_large_net": 1925688320.0, "large_net": 225550080.0,
        "up_count": 48, "down_count": 26, "main_net_ratio": 4.15,
    }


async def test_fetch_sector_moneyflow_concept_uses_t3():
    client = EastmoneyClient()
    fake = _FakeEM({"get": {"rc": 0, "data": {"diff": []}}})
    client.__dict__["_get_json"] = fake._get_json
    await client.fetch_sector_moneyflow("concept")
    assert "m:90+t:3" in fake.calls[0][1]["fs"]
