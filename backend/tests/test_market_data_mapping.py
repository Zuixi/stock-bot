"""market_data_service 纯映射/组装单测（monkeypatch，不触 DB/网络）。"""

from datetime import date, timedelta

import pytest

from app.services import market_data_service as mds


def test_global_indices_registry_shape():
    assert len(mds.GLOBAL_INDICES) == 9
    asia = [g for g in mds.GLOBAL_INDICES if g["region"] == "asia"]
    americas = [g for g in mds.GLOBAL_INDICES if g["region"] == "americas"]
    assert [g["ts_code"] for g in asia] == [
        "000001.SH", "399001.SZ", "399006.SZ", "HSI", "N225", "KS11",
    ]
    assert [g["ts_code"] for g in americas] == ["DJI", "SPX", "IXIC"]
    assert {g["em_secid"] for g in americas} == {"100.DJIA", "100.SPX", "100.NDX"}


def test_map_index_global_row_nan_vol_to_none():
    row = {
        "ts_code": "KS11", "trade_date": "20260902", "open": 6625.47, "close": 6562.72,
        "high": 6694.57, "low": 6558.3, "vol": float("nan"),
    }
    mapped = mds._map_index_global_row(row)
    assert mapped == {
        "ts_code": "KS11", "trade_date": date(2026, 9, 2),
        "open": 6625.47, "high": 6694.57, "low": 6558.3, "close": 6562.72,
        "volume": None,
    }


@pytest.mark.asyncio
async def test_ingest_global_index_daily_filters_and_upserts(monkeypatch):
    calls: list = []

    async def fake_fetch_index_global(ts_code, start_date, end_date):
        calls.append(ts_code)
        if ts_code != "N225":
            return mds.pd.DataFrame()
        return mds.pd.DataFrame(
            [{"ts_code": "N225", "trade_date": "20260902", "open": 65195.43, "close": 64325.64,
              "high": 65195.43, "low": 64215.47, "vol": 1724660.8}]
        )

    async def fake_fetch_index_daily(ts_code, start_date, end_date):
        return mds.pd.DataFrame()

    upserted: list = []

    async def fake_upsert(db, rows):
        upserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(mds, "_get_tushare", lambda: type("C", (), {
        "fetch_index_global": staticmethod(fake_fetch_index_global),
        "fetch_index_daily": staticmethod(fake_fetch_index_daily),
    })())
    monkeypatch.setattr(mds.index_repo, "upsert_index_dailies", fake_upsert)

    result = await mds.ingest_global_index_daily(db=None, lookback_days=14)
    assert result == {"upserted": 1}
    assert upserted[0].ts_code == "N225" and upserted[0].volume == 1724660.8
    assert set(calls) == {"HSI", "N225", "KS11", "DJI", "SPX", "IXIC"}  # 只拉 index_global 源


@pytest.mark.asyncio
async def test_ingest_global_index_daily_partial_failure(monkeypatch):
    """单指数拉取失败不中断整体 ingest（部分成功仍入库）。"""
    calls: list = []

    async def fake_fetch_index_global(ts_code, start_date, end_date):
        calls.append(ts_code)
        if ts_code == "KS11":
            raise RuntimeError("TuShareClient API 'index_global' failed after 3 retries")
        if ts_code != "N225":
            return mds.pd.DataFrame()
        return mds.pd.DataFrame(
            [{"ts_code": "N225", "trade_date": "20260902", "open": 65195.43, "close": 64325.64,
              "high": 65195.43, "low": 64215.47, "vol": 1724660.8}]
        )

    async def fake_fetch_index_daily(ts_code, start_date, end_date):
        return mds.pd.DataFrame()

    upserted: list = []

    async def fake_upsert(db, rows):
        upserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(mds, "_get_tushare", lambda: type("C", (), {
        "fetch_index_global": staticmethod(fake_fetch_index_global),
        "fetch_index_daily": staticmethod(fake_fetch_index_daily),
    })())
    monkeypatch.setattr(mds.index_repo, "upsert_index_dailies", fake_upsert)

    result = await mds.ingest_global_index_daily(db=None, lookback_days=14)
    assert result == {"upserted": 1}
    assert upserted[0].ts_code == "N225" and upserted[0].volume == 1724660.8
    assert "KS11" in calls and "N225" in calls  # 失败后继续拉取后续指数


@pytest.mark.asyncio
async def test_get_global_index_cards_merges_realtime_and_spark(monkeypatch):
    class _FakeCache:
        def __init__(self) -> None:
            self.store: dict = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ttl=None):
            self.store[key] = value

    async def fake_snapshot(secids):
        return [
            {"code": "N225", "name": "日经225", "price": 64214.48,
             "pct_change": -0.17, "change": -111.16},
            {"code": "KS11", "name": "韩国KOSPI",
             "price": None, "pct_change": None, "change": None},
        ]

    kline_calls: list = []

    async def fake_kline(db, ts_code):
        kline_calls.append(ts_code)
        return [
            type("R", (), {
                "trade_date": date(2026, 7, 28) + timedelta(days=i),
                "close": 60000.0 + i,
            })()
            for i in range(35)
        ]

    monkeypatch.setattr(mds, "_get_eastmoney", lambda: type("C", (), {
        "fetch_index_snapshot": staticmethod(fake_snapshot),
    }))
    monkeypatch.setattr(mds.index_repo, "get_kline", fake_kline)

    cards = await mds.get_global_index_cards(cache=_FakeCache())
    by_code = {c["ts_code"]: c for c in cards}
    assert len(cards) == 9
    assert by_code["N225"]["price"] == 64214.48 and by_code["N225"]["source"] == "realtime"
    assert len(by_code["N225"]["spark"]) == 30  # 35 行裁到 30
    # KS11 实时缺失 → 用日线最后一根 close 兜底（pre_close 为 NULL，逐 close 差值算涨跌）
    assert by_code["KS11"]["price"] == 60034.0 and by_code["KS11"]["source"] == "eod"
    assert by_code["KS11"]["change"] == 1.0 and by_code["KS11"]["pct_change"] == 0.0  # 60033→60034
    # N225 实时透传
    assert by_code["N225"]["change"] == -111.16 and by_code["N225"]["pct_change"] == -0.17
    assert set(kline_calls) == {g["ts_code"] for g in mds.GLOBAL_INDICES}


@pytest.mark.asyncio
async def test_ingest_sector_moneyflow_uses_today_and_both_dims(monkeypatch):
    fetched: list[str] = []

    async def fake_flow(dimension):
        fetched.append(dimension)
        if dimension != "industry":
            return []
        return [{"board_code": "BK1203", "board_name": "非银金融", "pct_change": 0.28,
                 "main_net_inflow": 2151238400.0, "super_large_net": 1925688320.0,
                 "large_net": 225550080.0, "up_count": 48, "down_count": 26,
                 "main_net_ratio": 4.15}]

    upserts: list = []

    async def fake_upsert(db, trade_date, dimension, rows):
        upserts.append((trade_date, dimension, rows))
        return len(rows)

    monkeypatch.setattr(mds, "_get_eastmoney", lambda: type("C", (), {
        "fetch_sector_moneyflow": staticmethod(fake_flow),
    }))
    monkeypatch.setattr(mds.market_data_repo, "upsert_sector_moneyflow", fake_upsert)

    result = await mds.ingest_sector_moneyflow(db=None)
    assert result == {"industry": 1, "concept": 0}
    assert set(fetched) == {"industry", "concept"}
    assert upserts[0][0] == mds._today_sh() and upserts[0][1] == "industry"


def test_map_hsgt_rows_string_to_float():
    df = mds.pd.DataFrame([
        {"trade_date": "20260902", "north_money": "244809.28"},
        {"trade_date": "20260901", "north_money": "273259.26"},
    ])
    rows = mds._map_hsgt_rows(df)
    assert rows == [
        {"trade_date": date(2026, 9, 2), "net_amount": 244809.28},
        {"trade_date": date(2026, 9, 1), "net_amount": 273259.26},
    ]


def test_map_top_list_rows():
    df = mds.pd.DataFrame([{
        "trade_date": "20260902", "ts_code": "000019.SZ", "name": "深粮控股", "close": 7.18,
        "pct_change": -9.3434, "turnover_rate": 14.8, "amount": 453755737.0, "l_sell": 112970021.2,
        "l_buy": 30878730.2, "l_amount": 143848751.4, "net_amount": -82091291.0, "net_rate": -18.09,
        "amount_rate": 31.7, "float_values": 3153460155.46,
        "reason": "日跌幅偏离值达到7%的前5只证券",
    }])
    rows = mds._map_top_list_rows(df)
    r = rows[0]
    assert r["ts_code"] == "000019.SZ" and r["symbol"] == "000019"
    assert r["trade_date"] == date(2026, 9, 2) and r["net_amount"] == -82091291.0
    assert r["reason"].startswith("日跌幅偏离值")


def test_map_top_list_rows_truncates_long_reason():
    """reason 列 String(160)：TuShare 超长上榜原因映射层截断，避免 DB 报错。"""
    df = mds.pd.DataFrame([{
        "trade_date": "20260902", "ts_code": "000019.SZ", "reason": "长" * 200,
    }])
    reason = mds._map_top_list_rows(df)[0]["reason"]
    assert len(reason) == 160


def test_map_block_trade_rows():
    df = mds.pd.DataFrame([{
        "ts_code": "000488.SZ", "trade_date": "20260902", "price": 1.88, "vol": 50.0,
        "amount": 94.0, "buyer": "机构专用", "seller": "机构专用",
    }])
    rows = mds._map_block_trade_rows(df)
    assert rows[0] == {
        "trade_date": date(2026, 9, 2), "ts_code": "000488.SZ", "symbol": "000488",
        "price": 1.88, "volume": 50.0, "amount": 94.0,
        "buyer": "机构专用", "seller": "机构专用",
    }


def test_map_block_trade_rows_dedupes_intra_batch_duplicates():
    """大宗交易无稳定业务键：同批重复行映射后按去重键（date+code+buyer+seller+price+vol）保留一条。"""
    df = mds.pd.DataFrame([
        {"ts_code": "000488.SZ", "trade_date": "20260902", "price": 1.88, "vol": 50.0,
         "amount": 94.0, "buyer": "机构专用", "seller": "机构专用"},
        {"ts_code": "000488.SZ", "trade_date": "20260902", "price": 1.88, "vol": 50.0,
         "amount": 94.0, "buyer": "机构专用", "seller": "机构专用"},
    ])
    assert len(mds._dedupe_block_trade_rows(mds._map_block_trade_rows(df))) == 1
