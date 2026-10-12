"""新浪美股指数行情客户端单测（纯解析函数 + 批量取数的失败语义，不触网络）。"""

from __future__ import annotations

import pytest

from app.core.providers import sina_index_client as sic


def test_parse_sina_index_quote_reads_name_price_and_change():
    """原始返回（2026-10-16 实机样本逐字，只留前 8 个字段位）。"""
    text = (
        'var hq_str_gb_$ndx="纳斯达克100,30883.1478,0.51,2026-10-10 05:30:00,'
        '157.3384,30900.0649,30946.7377,30770.0552";\n'
        'var hq_str_gb_$ixic="纳斯达克,27366.1698,0.64,2026-10-10 05:30:00,'
        '172.8295,27342.2660,27395.4133,27254.0330";\n'
    )
    quotes = sic.parse_sina_index_quote(text)
    assert set(quotes) == {"gb_$ndx", "gb_$ixic"}
    assert quotes["gb_$ndx"] == {
        "code": "gb_$ndx",
        "name": "纳斯达克100",
        "price": 30883.1478,
        "pct_change": 0.51,
        "quote_time": "2026-10-10 05:30:00",
        "change": 157.3384,
    }
    # 串号回归：两条标的的价必须落在各自的键上
    assert quotes["gb_$ixic"]["price"] == 27366.1698


def test_parse_sina_index_quote_skips_empty_payload():
    """上游对未知代码返回空串 → 按"缺失"处理（不入字典），**不得**当成 0。"""
    text = (
        'var hq_str_gb_zzznotexist="";\n'
        'var hq_str_gb_$ndx="纳斯达克100,1.5,0,2026-10-10 05:30:00,0.1";\n'
    )
    quotes = sic.parse_sina_index_quote(text)
    assert set(quotes) == {"gb_$ndx"}
    assert quotes["gb_$ndx"]["price"] == 1.5


def test_parse_sina_index_quote_dash_is_none_not_zero():
    """`--` / 非数值一律 None（"没有"≠"零"，与 eastmoney `_num` 同口径）。"""
    text = 'var hq_str_gb_$sox="费交所半导体股指数,--,--,2026-10-10 05:16:25,--";\n'
    q = sic.parse_sina_index_quote(text)["gb_$sox"]
    assert q["price"] is None and q["pct_change"] is None and q["change"] is None


def test_parse_sina_index_quote_ignores_unrelated_lines():
    assert sic.parse_sina_index_quote("") == {}
    assert sic.parse_sina_index_quote("not a quote line\nvar other=1;") == {}


@pytest.mark.asyncio
async def test_fetch_index_quotes_empty_codes_makes_no_request(monkeypatch):
    """空代码列表 → 直接返回空字典，**不发起请求**（省一次无用往返）。"""

    class _Boom:
        def __init__(self, *a, **kw):  # pragma: no cover - 不该被构造
            raise AssertionError("空代码列表不得构造 HTTP client")

    monkeypatch.setattr(sic.httpx, "AsyncClient", _Boom)
    assert await sic.fetch_index_quotes([]) == {}


@pytest.mark.asyncio
async def test_fetch_index_quotes_swallows_network_error(monkeypatch):
    """网络异常 → 返回空字典（调用方回落 EOD），不向上抛。"""

    class _FailingClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *a, **kw):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(sic.httpx, "AsyncClient", _FailingClient)
    assert await sic.fetch_index_quotes(["gb_$ndx"]) == {}
