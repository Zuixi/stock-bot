"""限价 ingest 的纯映射单测（monkeypatch，不触 DB/网络）。"""

from datetime import date

import pandas as pd

from app.services import market_data_service as mds


def test_map_stk_limit_rows_keeps_exchange_limits_verbatim():
    """限价必须原值落库：不做任何按 ST/前缀的比例重算。"""
    df = pd.DataFrame(
        [
            {
                "trade_date": "20260908",
                "ts_code": "000488.SZ",
                "pre_close": 1.94,
                "up_limit": 2.13,
                "down_limit": 1.75,
            },
        ]
    )
    rows = mds._map_stk_limit_rows(df, date(2026, 9, 8), {"000488.SZ": 42})
    assert rows == [
        {
            "trade_date": date(2026, 9, 8),
            "stock_id": 42,
            "ts_code": "000488.SZ",
            "pre_close": 1.94,
            "up_limit": 2.13,
            "down_limit": 1.75,
        }
    ]


def test_map_stk_limit_rows_skips_unmapped_and_null_limit():
    df = pd.DataFrame(
        [
            {
                "trade_date": "20260908",
                "ts_code": "510300.SH",
                "pre_close": 4.0,
                "up_limit": 4.4,
                "down_limit": 3.6,
            },  # 基金：不在 stocks 表
            {
                "trade_date": "20260908",
                "ts_code": "600000.SH",
                "pre_close": None,
                "up_limit": None,
                "down_limit": None,
            },  # 限价缺失
            {
                "trade_date": "20260908",
                "ts_code": "000001.SZ",
                "pre_close": 11.6,
                "up_limit": 12.76,
                "down_limit": 10.44,
            },
        ]
    )
    rows = mds._map_stk_limit_rows(df, date(2026, 9, 8), {"000001.SZ": 7})
    assert [r["stock_id"] for r in rows] == [7]


def test_fetch_stk_limit_requests_pre_close_field(monkeypatch):
    """`stk_limit` 的 pre_close 是「默认显示=N」字段：不显式传 fields 就不会返回。

    实测 2026-09-08：不传 fields 只回 [trade_date, ts_code, up_limit, down_limit]，
    传 fields 后 5,637/5,637 行 pre_close 非空。漏这一步会让整列 NULL，
    而所有溢价/赚钱效应 KPI 都依赖它。
    """
    import asyncio

    from app.core.providers import tushare_client as tc

    captured: dict[str, str] = {}

    async def _query(api_name: str, fields: str = "", **_kw):
        captured["api"], captured["fields"] = api_name, fields
        return pd.DataFrame()

    client = tc.TuShareClient.__new__(tc.TuShareClient)
    monkeypatch.setattr(client, "_query", _query)
    asyncio.run(client.fetch_stk_limit(trade_date="20260908"))
    assert captured["api"] == "stk_limit"
    assert "pre_close" in captured["fields"]
