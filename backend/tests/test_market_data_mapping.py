"""market_data_service 纯映射/组装单测（monkeypatch，不触 DB/网络）。"""

from datetime import date

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
