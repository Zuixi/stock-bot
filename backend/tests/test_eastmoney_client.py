"""EastmoneyClient 解析单测（不打真实网络，monkeypatch _get_json）。"""

import asyncio

import pytest

from app.core.providers.eastmoney_client import EastmoneyClient, _map_concept_board


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
    client.__dict__["_get_json"] = _FakeEM(
        {
            "get": {
                "rc": 0,
                "data": {
                    "diff": [
                        {
                            "f2": 64214.48,
                            "f3": -0.17,
                            "f4": -111.16,
                            "f12": "N225",
                            "f13": 100,
                            "f14": "日经225",
                        },
                        {
                            "f2": "-",
                            "f3": "-",
                            "f4": "-",
                            "f12": "KS11",
                            "f13": 100,
                            "f14": "韩国KOSPI",
                        },
                    ]
                },
            }
        }
    )._get_json
    rows = await client.fetch_index_snapshot(["100.N225", "100.KS11"])
    assert rows[0] == {
        "code": "N225",
        "secid": "100.N225",
        "name": "日经225",
        "price": 64214.48,
        "pct_change": -0.17,
        "change": -111.16,
    }
    assert rows[1]["price"] is None and rows[1]["pct_change"] is None and rows[1]["change"] is None


async def test_fetch_sector_moneyflow_maps_fields_yuan():
    client = EastmoneyClient()
    client.__dict__["_get_json"] = _FakeEM(
        {
            "get": {
                "rc": 0,
                "data": {
                    "diff": [
                        {
                            "f12": "BK1203",
                            "f14": "非银金融",
                            "f3": 0.28,
                            "f62": 2151238400.0,
                            "f66": 1925688320.0,
                            "f72": 225550080.0,
                            "f104": 48,
                            "f105": 26,
                            "f184": 4.15,
                            "f128": "中信证券",
                            "f136": 5.21,
                            "f140": "600030",
                        },
                    ]
                },
            }
        }
    )._get_json
    rows = await client.fetch_sector_moneyflow("industry")
    assert rows[0] == {
        "board_code": "BK1203",
        "board_name": "非银金融",
        "pct_change": 0.28,
        "main_net_inflow": 2151238400.0,
        "super_large_net": 1925688320.0,
        "large_net": 225550080.0,
        "up_count": 48,
        "down_count": 26,
        "main_net_ratio": 4.15,
        "lead_stock_name": "中信证券",
        "lead_stock_code": "600030",
        "lead_stock_pct": 5.21,
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
    client.__dict__["_get_json"] = _FakeEM(
        {
            "get": {
                "rc": 0,
                "data": {
                    "diff": [
                        {
                            "f12": "000001",
                            "f14": "上证指数",
                            "f62": -100.0,
                            "f66": -60.0,
                            "f72": -40.0,
                            "f78": 10.0,
                            "f84": 90.0,
                            "f184": -1.6,
                        },
                        {
                            "f12": "399001",
                            "f14": "深证成指",
                            "f62": -50.0,
                            "f66": "-",
                            "f72": -50.0,
                            "f78": 5.0,
                            "f84": 45.0,
                            "f184": -0.8,
                        },
                    ]
                },
            }
        }
    )._get_json
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


def _board(code: str) -> dict:
    return {"f12": code, "f14": code, "f104": 0, "f105": 0, "f106": 0}


def test_fetch_concept_boards_paginates_and_dedupes(monkeypatch):
    """pz 服务端上限 100：必须翻页，且翻页抖动产生的重复行要去重（实测 504 板）。"""
    page1 = {"data": {"total": 150, "diff": [_board(f"BK{i:04d}") for i in range(100)]}}
    page2 = {
        "data": {
            "total": 150,
            "diff": [_board("BK0099")] + [_board(f"BK{i:04d}") for i in range(100, 150)],
        }
    }
    calls: list[int] = []

    async def fake_get_json(self, base, path, params):
        calls.append(params["pn"])
        assert params["fid"] == "f12"  # 按代码排序，盘中翻页稳定
        # f106(平盘家数) 必须在 fields 内，否则 member_total 低估（见 mapper 注释）
        assert params["fields"] == "f12,f14,f104,f105,f106"
        return page1 if params["pn"] == 1 else page2

    monkeypatch.setattr(EastmoneyClient, "_get_json", fake_get_json)
    boards = asyncio.run(EastmoneyClient().fetch_concept_boards())
    assert calls == [1, 2]
    assert len(boards) == 150 and boards[0]["board_code"] == "BK0000"


def test_fetch_concept_boards_stops_on_empty_page(monkeypatch):
    """total 报大但返回空页时必须收敛（防死循环把 5 分钟任务变成长驻）。"""

    async def fake_get_json(self, base, path, params):
        return {"data": {"total": 9999, "diff": []}}

    monkeypatch.setattr(EastmoneyClient, "_get_json", fake_get_json)
    assert asyncio.run(EastmoneyClient().fetch_concept_boards()) == []


def test_paged_clist_stops_at_max_pages_when_total_never_reached(monkeypatch):
    """翻页护栏：total 撒谎 + 服务端永远返回非空页时，最多 60 次请求就收手。

    60 = `_MAX_PAGES`（20→60，2026-09-18：最大板 BK0596 member_total=3870，20×100=2000 拿不全）。
    钉**行为**而非常量：每页返回一个不同的新 code（去重不收敛）且 total 恒为 999999。
    """

    pages_requested: list[int] = []

    async def counting_get_json(self, base, path, params):
        pages_requested.append(params["pn"])
        return {"data": {"total": 999999, "diff": [_board(f"BK{params['pn']:04d}")]}}

    monkeypatch.setattr(EastmoneyClient, "_get_json", counting_get_json)
    boards = asyncio.run(EastmoneyClient().fetch_concept_boards())
    assert pages_requested == list(range(1, 61)), "护栏必须在第 60 页收手（不请求第 61 页）"
    assert len(boards) == 60


def test_concept_member_fields_are_stable(monkeypatch):
    """字段形状实测钉住：f12 代码 / f14 名称 / f13 市场（1=沪,0=深）。"""

    async def fake_get_json(self, base, path, params):
        assert params["fs"] == "b:BK0501"
        assert params["fields"] == "f12,f13,f14"
        return {"data": {"total": 1, "diff": [{"f12": "601091", "f14": "C沈鼓", "f13": 1}]}}

    monkeypatch.setattr(EastmoneyClient, "_get_json", fake_get_json)
    rows = asyncio.run(EastmoneyClient().fetch_concept_members("BK0501"))
    assert rows == [{"symbol": "601091", "name": "C沈鼓", "market_flag": 1}]


def test_map_concept_board_member_total_sums_up_down_flat():
    """member_total = f104+f105+f106；实测 BK1753 55+6+2=63 = 成分真值。"""
    assert _map_concept_board(
        {"f12": "BK1753", "f14": "光刻胶", "f104": 55, "f105": 6, "f106": 2}
    ) == {"board_code": "BK1753", "board_name": "光刻胶", "member_total": 63}
    # 单边/多边缺值：缺的项按 0 计
    assert (
        _map_concept_board({"f12": "BK1", "f14": "X", "f104": 5, "f105": "-", "f106": "-"})[
            "member_total"
        ]
        == 5
    )
    # 三项全缺：None（不造 0，避免把"未知"记成"空板块"）
    assert (
        _map_concept_board({"f12": "BK2", "f14": "Y", "f104": "-", "f105": "-", "f106": "-"})[
            "member_total"
        ]
        is None
    )
