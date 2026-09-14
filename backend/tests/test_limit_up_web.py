"""东财涨停池映射与对账（monkeypatch，无网络）。

Web 只作增强/兜底：它给得出封板时间与封单，给不出权威连板链（lbc 是东财自己的口径）。
本地自算与 Web 的 streak 允许存在口径差，但差异必须可测、可观测，不能静默。
"""

from app.core.providers.eastmoney_client import EastmoneyClient

RAW = {
    "data": {
        "tc": 2,
        "pool": [
            {
                "c": "000981",
                "m": 0,
                "n": "山子高科",
                "zdp": 10.038,
                "amount": 773003856,
                "lbc": 1,
                "fbt": 92500,
                "lbt": 92500,
                "fund": 454003010,
                "zbc": 0,
                "hybk": "汽车零部",
                "zttj": {"days": 1, "ct": 1},
            },
            {
                "c": "600354",
                "m": 1,
                "n": "敦煌种业",
                "zdp": 9.98,
                "amount": 12345678,
                "lbc": 4,
                "fbt": 100312,
                "lbt": 144500,
                "fund": 81200000,
                "zbc": 2,
                "hybk": "种植业",
                "zttj": {"days": 4, "ct": 4},
            },
        ],
    }
}


def test_map_zt_pool_row_normalizes_price_scale_and_times():
    """东财 p/fbt/lbt 是定点整数：价格 ×1000、时间 HHMMSS 需零填充。"""
    rows = [EastmoneyClient._map_zt_pool_row(d) for d in RAW["data"]["pool"]]
    assert rows[0] == {
        "symbol": "000981",
        "name": "山子高科",
        "streak": 1,
        "days": 1,
        "boards": 1,
        "seal_time": "09:25:00",
        "seal_fund": 454003010.0,
        "break_count": 0,
        "board_name": "汽车零部",
        "amount": 773003856.0,
    }
    assert rows[1]["seal_time"] == "10:03:12"
    assert rows[1]["streak"] == 4 and rows[1]["break_count"] == 2


def test_map_zt_pool_row_tolerates_missing_zttj():
    row = {
        "c": "000001",
        "m": 0,
        "n": "X",
        "zdp": 1.0,
        "amount": 1,
        "lbc": 2,
        "fbt": 93000,
        "fund": 0,
        "zbc": 1,
    }
    got = EastmoneyClient._map_zt_pool_row(row)
    assert got["days"] is None and got["boards"] is None


async def test_enrich_injects_seal_fields_only_for_matched_symbols():
    from app.services import limit_up_service as svc

    snapshot = {
        "echelons": [
            {
                "streak": 1,
                "label": "首板",
                "stocks": [
                    {"symbol": "000981", "seal_time": None, "seal_fund": None, "break_count": None},
                    {"symbol": "999999", "seal_time": None, "seal_fund": None, "break_count": None},
                ],
            }
        ]
    }
    web = [{"symbol": "000981", "seal_time": "09:25:00", "seal_fund": 1.0, "break_count": 0}]
    out = svc.apply_web_enrichment(snapshot, web)
    assert out["echelons"][0]["stocks"][0]["seal_time"] == "09:25:00"
    assert out["echelons"][0]["stocks"][1]["seal_time"] is None  # 未匹配 → 保持 None


async def test_web_failure_never_raises(monkeypatch):
    from app.services import limit_up_service as svc

    called = False

    async def _boom(*_a, **_kw):
        nonlocal called
        called = True
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(svc, "_fetch_web_pool", _boom)
    # echelons 必须非空：空快照会走早退分支，`_boom` 根本不被调用，测不到异常路径。
    stock = {"symbol": "000981", "seal_time": None, "seal_fund": None, "break_count": None}
    snapshot = {
        "echelons": [{"streak": 1, "label": "首板", "stocks": [stock]}],
        "degraded_reason": None,
    }
    got = await svc.enrich_from_web(snapshot, "20260908")
    assert called is True
    assert got["degraded_reason"] is None  # 增强失败不改变主路径状态
    assert got["echelons"][0]["stocks"][0]["seal_time"] is None  # 三字段保持 null → 前端 `--`


async def test_enrich_skipped_when_snapshot_degraded(monkeypatch):
    """降级快照不得触发外呼：本来就没数据，再去请求 Web 是白花钱 + 白堵请求。"""
    from app.services import limit_up_service as svc

    called = False

    async def _boom(*_a, **_kw):
        nonlocal called
        called = True
        raise RuntimeError("should not be called")

    monkeypatch.setattr(svc, "_fetch_web_pool", _boom)
    snapshot = {
        "echelons": [{"streak": 1, "label": "首板", "stocks": [{"symbol": "1"}]}],
        "degraded_reason": "partial_day",
    }
    got = await svc.enrich_from_web(snapshot, "20260908")
    assert got["degraded_reason"] == "partial_day"
    assert called is False
