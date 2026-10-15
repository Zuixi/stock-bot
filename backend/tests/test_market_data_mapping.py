"""market_data_service 纯映射/组装单测（monkeypatch，不触 DB/网络）。"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services import market_data_service as mds


def test_global_indices_registry_shape():
    assert len(mds.GLOBAL_INDICES) == 19
    asia = [g for g in mds.GLOBAL_INDICES if g["region"] == "asia"]
    americas = [g for g in mds.GLOBAL_INDICES if g["region"] == "americas"]
    assert [g["ts_code"] for g in asia] == [
        "000001.SH",
        "399001.SZ",
        "399006.SZ",
        "000300.SH",
        "000905.SH",
        "000688.SH",
        "000016.SH",
        "899050.BJ",
        "HSI",
        "HKTECH",  # 恒生科技指数（TuShare index_global 的代码是 HKTECH，不是 HSTECH）
        "N225",
        "KS11",
        "XIN9",  # 富时中国A50 指数期货（新加坡）
    ]
    assert [g["ts_code"] for g in americas] == ["DJI", "SPX", "IXIC", "NDX", "SOX", "HXC"]
    # 东财延迟行情只覆盖部分标的：无 secid 的（NDX/SOX/HXC）**不得**出现在这里——
    # 否则读路径会把它们拼进 secids，取回空值或错值。
    assert {g["em_secid"] for g in americas if g.get("em_secid")} == {
        "100.DJIA",
        "100.SPX",
        "100.IXIC",  # IXIC（纳斯达克综合）的东财 secid；100.NDX 归 NDX（纳斯达克100）
    }
    assert {g["sina_code"] for g in americas if g.get("sina_code")} == {
        "gb_$ndx",
        "gb_$sox",
        "gb_$hxc",
    }
    # A 股宽基全部走 TuShare 日线回补 + 东财实时 secid
    cn = [g for g in asia if g["market"] == "CN"]
    assert {g["source"] for g in cn} == {"index_daily"}
    assert {g["em_secid"] for g in cn} == {
        "1.000001",
        "0.399001",
        "0.399006",
        "1.000300",
        "1.000905",
        "1.000688",
        "1.000016",
        "0.899050",
    }


def test_global_indices_metadata_five_fields_all_non_empty():
    """S1.3 验收判据：**全部**标的「名称 / 数据源代码 / 交易时段 / 时区 / 币种」非空率 100%。

    逐条断言而不是 `len(set(...)) == len(...)`：后者在"全都漏填"时也会通过（同一个空串）。
    """
    assert mds.GLOBAL_INDICES, "登记表不得为空（空表会让下面的循环零次通过）"
    for g in mds.GLOBAL_INDICES:
        for field in ("name", "ts_code", "session", "timezone", "currency"):
            value = g.get(field)
            assert isinstance(value, str) and value.strip(), (
                f"{g.get('ts_code') or '<无代码>'} 的 {field} 为空：{value!r}"
            )
        # 数据源代码 = 登记表的 source（另加行情源分流用的 quote_provider）
        assert g["source"] in {"index_daily", "index_global", "sina_index"}
        assert g["quote_provider"] in {"eastmoney", "sina"}
        # 时区必须是可解析的 IANA 名（拼错时这里就炸，而不是等到某天换算交易时段时才发现）
        ZoneInfo(g["timezone"])


def test_a50_declares_singapore_cny_night_session():
    """导图点名要求：A50 必须显式标「市场=新加坡 / 时段=CNY 夜盘」，不得与 A 股时段混淆。"""
    a50 = next(g for g in mds.GLOBAL_INDICES if g["ts_code"] == "XIN9")
    assert a50["market"] == "SG"
    assert a50["timezone"] == "Asia/Singapore"
    assert a50["currency"] == "CNY"
    assert "夜盘" in a50["session"] and "T+1" in a50["session"]
    # 与 A 股时段的**逐字不同**——这是"不得混淆"的可机械检测形式
    cn_sessions = {g["session"] for g in mds.GLOBAL_INDICES if g["market"] == "CN"}
    assert a50["session"] not in cn_sessions
    assert a50["timezone"] not in {g["timezone"] for g in mds.GLOBAL_INDICES if g["market"] == "CN"}


def test_ixic_and_ndx_are_distinct_instruments():
    """S1.3 串号回归的**登记表侧**判据：IXIC（纳斯达克综合）与 NDX（纳斯达克100）
    必须是两条不同标的，且各自绑定**不同**的行情代码。

    最新值不等的运行时判据见 `test_ixic_and_ndx_latest_values_differ`（同文件），
    本用例只锁"配置层面没有把它们串成同一个东西"。
    """
    by_code = {g["ts_code"]: g for g in mds.GLOBAL_INDICES}
    ixic, ndx = by_code["IXIC"], by_code["NDX"]
    assert ixic["name"] != ndx["name"]
    assert "综合" in ixic["name"] and "100" in ndx["name"]
    # 两套行情查找键都不得相同（相同 = 两个标的会拿到同一格价 → 就是串号本身）
    assert ixic["sina_code" if ixic.get("sina_code") else "em_secid"] != ndx["sina_code"]
    assert ixic.get("sina_code") != ndx.get("sina_code")
    assert ixic.get("em_secid") != ndx.get("em_secid")


@pytest.mark.asyncio
async def test_ixic_and_ndx_latest_values_differ(monkeypatch):
    """S1.3 行为判据：两个标的的**最新值不得相等**（相等即串号；上游真相等则该样本无效）。

    这里喂的是 2026-10-09 收盘的**真实值**（IXIC 27366.1698 / NDX 30883.1478，同源于
    新浪美股指数行情，见 `docs/design/data-source.md` 附.5 实机记录），走完整读路径
    的组装逻辑；断言落在"两格价不相等"上，而不是"等于某个魔法数"。
    """

    class _NoCache:
        async def get(self, key):
            return None

        async def set(self, key, value, ttl=None):
            # 有价 → 会写缓存；本用例走"不读缓存"的路径，写入直接吞掉
            return None

    async def fake_snapshot(secids):
        return [
            {
                "code": "IXIC",
                "secid": "100.IXIC",
                "name": "纳斯达克",
                "price": 27366.1698,
                "pct_change": 0.64,
                "change": 172.8295,
            }
        ]

    async def fake_sina_quotes(codes):
        assert codes == ["gb_$ndx", "gb_$sox", "gb_$hxc"]
        return {
            "gb_$ndx": {
                "code": "gb_$ndx",
                "name": "纳斯达克100",
                "price": 30883.1478,
                "pct_change": 0.51,
                "change": 157.3384,
                "quote_time": "2026-10-10 05:30:00",
            }
        }

    async def fake_kline(db, ts_code):
        return []

    monkeypatch.setattr(
        mds,
        "_get_eastmoney",
        lambda: type("C", (), {"fetch_index_snapshot": staticmethod(fake_snapshot)})(),
    )
    monkeypatch.setattr(mds, "_get_sina_quotes", lambda: fake_sina_quotes)
    monkeypatch.setattr(mds.index_repo, "get_kline", fake_kline)

    cards = await mds.get_global_index_cards(cache=_NoCache())
    by_code = {c["ts_code"]: c for c in cards}
    ixic, ndx = by_code["IXIC"], by_code["NDX"]
    assert ixic["price"] == 27366.1698
    assert ndx["price"] == 30883.1478
    assert ixic["price"] != ndx["price"]
    assert ixic["price_source"] == "realtime"
    assert ndx["price_source"] == "realtime_sina"


def test_map_index_global_row_nan_vol_to_none():
    row = {
        "ts_code": "KS11",
        "trade_date": "20260902",
        "open": 6625.47,
        "close": 6562.72,
        "high": 6694.57,
        "low": 6558.3,
        "vol": float("nan"),
    }
    mapped = mds._map_index_global_row(row)
    assert mapped == {
        "ts_code": "KS11",
        "trade_date": date(2026, 9, 2),
        "open": 6625.47,
        "high": 6694.57,
        "low": 6558.3,
        "close": 6562.72,
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
            [
                {
                    "ts_code": "N225",
                    "trade_date": "20260902",
                    "open": 65195.43,
                    "close": 64325.64,
                    "high": 65195.43,
                    "low": 64215.47,
                    "vol": 1724660.8,
                }
            ]
        )

    async def fake_fetch_index_daily(ts_code, start_date, end_date):
        return mds.pd.DataFrame()

    upserted: list = []

    async def fake_upsert(db, rows):
        upserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(
        mds,
        "_get_tushare",
        lambda: type(
            "C",
            (),
            {
                "fetch_index_global": staticmethod(fake_fetch_index_global),
                "fetch_index_daily": staticmethod(fake_fetch_index_daily),
            },
        )(),
    )
    monkeypatch.setattr(mds.index_repo, "upsert_index_dailies", fake_upsert)

    result = await mds.ingest_global_index_daily(db=None, lookback_days=14)
    assert result == {"upserted": 1}
    assert upserted[0].ts_code == "N225" and upserted[0].volume == 1724660.8
    # 只拉 index_global 源（sina_index 的 NDX/SOX/HXC 本轮无日线适配器 → 显式跳过）
    assert set(calls) == {"HSI", "HKTECH", "N225", "KS11", "DJI", "SPX", "IXIC", "XIN9"}


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
            [
                {
                    "ts_code": "N225",
                    "trade_date": "20260902",
                    "open": 65195.43,
                    "close": 64325.64,
                    "high": 65195.43,
                    "low": 64215.47,
                    "vol": 1724660.8,
                }
            ]
        )

    async def fake_fetch_index_daily(ts_code, start_date, end_date):
        return mds.pd.DataFrame()

    upserted: list = []

    async def fake_upsert(db, rows):
        upserted.extend(rows)
        return len(rows)

    monkeypatch.setattr(
        mds,
        "_get_tushare",
        lambda: type(
            "C",
            (),
            {
                "fetch_index_global": staticmethod(fake_fetch_index_global),
                "fetch_index_daily": staticmethod(fake_fetch_index_daily),
            },
        )(),
    )
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
            {
                "code": "N225",
                "secid": "100.N225",
                "name": "日经225",
                "price": 64214.48,
                "pct_change": -0.17,
                "change": -111.16,
            },
            {
                "code": "KS11",
                "secid": "100.KS11",
                "name": "韩国KOSPI",
                "price": None,
                "pct_change": None,
                "change": None,
            },
        ]

    kline_calls: list = []

    async def fake_kline(db, ts_code):
        kline_calls.append(ts_code)
        return [
            type(
                "R",
                (),
                {
                    "trade_date": date(2026, 7, 28) + timedelta(days=i),
                    "close": 60000.0 + i,
                },
            )()
            for i in range(35)
        ]

    monkeypatch.setattr(
        mds,
        "_get_eastmoney",
        lambda: type(
            "C",
            (),
            {
                "fetch_index_snapshot": staticmethod(fake_snapshot),
            },
        ),
    )

    # 新浪一路必须被短路：本用例只关心东财 + EOD 兜底，不得真发网络请求
    async def fake_sina_quotes(codes):
        return {}

    monkeypatch.setattr(mds, "_get_sina_quotes", lambda: fake_sina_quotes)
    monkeypatch.setattr(mds.index_repo, "get_kline", fake_kline)

    cards = await mds.get_global_index_cards(cache=_FakeCache())
    by_code = {c["ts_code"]: c for c in cards}
    assert len(cards) == 19
    assert by_code["N225"]["price"] == 64214.48 and by_code["N225"]["price_source"] == "realtime"
    assert len(by_code["N225"]["spark"]) == 30  # 35 行裁到 30
    # KS11 实时缺失 → 用日线最后一根 close 兜底（pre_close 为 NULL，逐 close 差值算涨跌）
    assert by_code["KS11"]["price"] == 60034.0 and by_code["KS11"]["price_source"] == "eod"
    assert by_code["KS11"]["change"] == 1.0 and by_code["KS11"]["pct_change"] == 0.0  # 60033→60034
    # N225 实时透传
    assert by_code["N225"]["change"] == -111.16 and by_code["N225"]["pct_change"] == -0.17
    assert set(kline_calls) == {g["ts_code"] for g in mds.GLOBAL_INDICES}
    # 新浪未返回且本用例的 fake_kline 对所有代码都给了日线 → NDX 走 EOD 兜底。
    # 重点在"**有价时绝不落到 none**，无价时绝不落到 0"。
    assert by_code["NDX"]["price_source"] == "eod"
    assert by_code["NDX"]["price"] == 60034.0
    assert by_code["NDX"]["data_source"] == "sina_index"
    # 每条卡片都带齐五要素与数据源代码（S1.3 判据在端点层的体现）
    for c in cards:
        assert c["session"] and c["timezone"] and c["currency"] and c["data_source"]


@pytest.mark.asyncio
async def test_ingest_sector_moneyflow_uses_today_and_both_dims(monkeypatch):
    fetched: list[str] = []

    async def fake_flow(dimension):
        fetched.append(dimension)
        if dimension != "industry":
            return []
        return [
            {
                "board_code": "BK1203",
                "board_name": "非银金融",
                "pct_change": 0.28,
                "main_net_inflow": 2151238400.0,
                "super_large_net": 1925688320.0,
                "large_net": 225550080.0,
                "up_count": 48,
                "down_count": 26,
                "main_net_ratio": 4.15,
            }
        ]

    upserts: list = []

    async def fake_upsert(db, trade_date, dimension, rows, *, capture_kind="poll"):
        upserts.append((trade_date, dimension, rows, capture_kind))
        return len(rows)

    monkeypatch.setattr(
        mds,
        "_get_eastmoney",
        lambda: type(
            "C",
            (),
            {
                "fetch_sector_moneyflow": staticmethod(fake_flow),
            },
        ),
    )
    monkeypatch.setattr(mds.market_data_repo, "upsert_sector_moneyflow", fake_upsert)

    result = await mds.ingest_sector_moneyflow(db=None)
    assert result == {"industry": 1, "concept": 0, "region": 0}
    assert set(fetched) == {"industry", "concept", "region"}
    assert upserts[0][0] == mds._today_sh() and upserts[0][1] == "industry"
    # 盘中轮询恒写 capture_kind='poll'（当日完整态由 15:05 的 eod_full 全量捕获负责）
    assert {u[3] for u in upserts} == {"poll"}, upserts


def test_map_hsgt_rows_string_to_float():
    """``north_money`` → ``amount``（万元），并按交易日打上行级口径（Ruling 62/63）。"""
    df = mds.pd.DataFrame(
        [
            {"trade_date": "20260902", "north_money": "244809.28"},
            {"trade_date": "20260901", "north_money": "273259.26"},
        ]
    )
    rows = mds._map_hsgt_rows(df)
    assert rows == [
        {"trade_date": date(2026, 9, 2), "amount": 244809.28, "amount_kind": "turnover"},
        {"trade_date": date(2026, 9, 1), "amount": 273259.26, "amount_kind": "turnover"},
    ]


def test_map_hsgt_rows_labels_pre_breakpoint_as_net_buy():
    """断点（2024-08-19）之前的上游值必须是 ``net_buy``，**不是**成交额。

    这正是本次要修的错标：同一列两种口径，行级标记必须跟着交易日走，不能整表一个假设。
    """
    df = mds.pd.DataFrame(
        [
            {"trade_date": "20240816", "north_money": "-3209.23"},
            {"trade_date": "20240819", "north_money": "77584.24"},
        ]
    )
    rows = mds._map_hsgt_rows(df)
    assert rows == [
        {"trade_date": date(2024, 8, 16), "amount": -3209.23, "amount_kind": "net_buy"},
        {"trade_date": date(2024, 8, 19), "amount": 77584.24, "amount_kind": "turnover"},
    ]


def test_map_top_list_rows():
    df = mds.pd.DataFrame(
        [
            {
                "trade_date": "20260902",
                "ts_code": "000019.SZ",
                "name": "深粮控股",
                "close": 7.18,
                "pct_change": -9.3434,
                "turnover_rate": 14.8,
                "amount": 453755737.0,
                "l_sell": 112970021.2,
                "l_buy": 30878730.2,
                "l_amount": 143848751.4,
                "net_amount": -82091291.0,
                "net_rate": -18.09,
                "amount_rate": 31.7,
                "float_values": 3153460155.46,
                "reason": "日跌幅偏离值达到7%的前5只证券",
            }
        ]
    )
    rows = mds._map_top_list_rows(df)
    r = rows[0]
    assert r["ts_code"] == "000019.SZ" and r["symbol"] == "000019"
    assert r["trade_date"] == date(2026, 9, 2) and r["net_amount"] == -82091291.0
    assert r["reason"].startswith("日跌幅偏离值")


def test_map_top_list_rows_truncates_long_reason():
    """reason 列 String(160)：TuShare 超长上榜原因映射层截断，避免 DB 报错。"""
    df = mds.pd.DataFrame(
        [
            {
                "trade_date": "20260902",
                "ts_code": "000019.SZ",
                "reason": "长" * 200,
            }
        ]
    )
    reason = mds._map_top_list_rows(df)[0]["reason"]
    assert len(reason) == 160


def test_map_block_trade_rows():
    df = mds.pd.DataFrame(
        [
            {
                "ts_code": "000488.SZ",
                "trade_date": "20260902",
                "price": 1.88,
                "vol": 50.0,
                "amount": 94.0,
                "buyer": "机构专用",
                "seller": "机构专用",
            }
        ]
    )
    rows = mds._map_block_trade_rows(df)
    assert rows[0] == {
        "trade_date": date(2026, 9, 2),
        "ts_code": "000488.SZ",
        "symbol": "000488",
        "price": 1.88,
        "volume": 50.0,
        "amount": 94.0,
        "buyer": "机构专用",
        "seller": "机构专用",
    }


def test_map_block_trade_rows_dedupes_intra_batch_duplicates():
    """大宗交易无稳定业务键：同批重复行映射后按去重键（date+code+buyer+seller+price+vol）保留一条。"""
    df = mds.pd.DataFrame(
        [
            {
                "ts_code": "000488.SZ",
                "trade_date": "20260902",
                "price": 1.88,
                "vol": 50.0,
                "amount": 94.0,
                "buyer": "机构专用",
                "seller": "机构专用",
            },
            {
                "ts_code": "000488.SZ",
                "trade_date": "20260902",
                "price": 1.88,
                "vol": 50.0,
                "amount": 94.0,
                "buyer": "机构专用",
                "seller": "机构专用",
            },
        ]
    )
    assert len(mds._dedupe_block_trade_rows(mds._map_block_trade_rows(df))) == 1


def test_map_share_float_rows():
    df = mds.pd.DataFrame(
        [
            {
                "ts_code": "002747.SZ",
                "ann_date": "20260901",
                "float_date": "20260902",
                "float_share": 60000.0,
                "float_ratio": 0.0069,
                "holder_name": "朱樟兴",
                "share_type": "股权激励限售流通",
            }
        ]
    )
    rows = mds._map_share_float_rows(df)
    r = rows[0]
    assert r["float_date"] == date(2026, 9, 2) and r["ann_date"] == date(2026, 9, 1)
    assert r["float_share"] == 60000.0 and r["symbol"] == "002747"


def test_map_share_float_rows_null_ann_date():
    """share_float ann_date 可为 NULL：NaN → None（unique 约束对 NULL 不判重，见 repo）。"""
    df = mds.pd.DataFrame(
        [
            {
                "ts_code": "002747.SZ",
                "ann_date": float("nan"),
                "float_date": "20260902",
                "float_share": 60000.0,
                "float_ratio": float("nan"),
                "holder_name": "朱樟兴",
                "share_type": "股权激励限售流通",
            }
        ]
    )
    r = mds._map_share_float_rows(df)[0]
    assert r["ann_date"] is None and r["float_ratio"] is None


def test_map_repurchase_rows_nan_exp_date():
    df = mds.pd.DataFrame(
        [
            {
                "ts_code": "002120.SZ",
                "ann_date": "20260902",
                "end_date": "20260831",
                "proc": "完成",
                "exp_date": float("nan"),
                "vol": 12074600.0,
                "amount": 87945900.0,
                "high_limit": 8.05,
                "low_limit": 6.17,
            }
        ]
    )
    rows = mds._map_repurchase_rows(df)
    r = rows[0]
    assert r["proc"] == "完成" and r["exp_date"] is None and r["amount"] == 87945900.0
    assert r["ann_date"] == date(2026, 9, 2) and r["end_date"] == date(2026, 8, 31)


def test_map_repurchase_rows_dedupes_intra_batch_duplicates():
    """回购同批 (ann_date, ts_code, proc) 重复行 ON CONFLICT 不自处理，映射后 Python 端去重。"""
    df = mds.pd.DataFrame(
        [
            {"ts_code": "002120.SZ", "ann_date": "20260902", "proc": "完成", "vol": 12074600.0},
            {"ts_code": "002120.SZ", "ann_date": "20260902", "proc": "完成", "vol": 12074700.0},
        ]
    )
    rows = mds._dedupe_repurchase_rows(mds._map_repurchase_rows(df))
    assert len(rows) == 1 and rows[0]["vol"] == 12074700.0  # 保留末次出现


def _fake_client_with_cal(cal_days: list[str]):
    """带 fetch_trade_cal 的假 TuShare 客户端（记录 fetch_top_list 调用日期）。"""

    class _Fake:
        top_list_calls: list[str] = []

        async def fetch_trade_cal(self, start_date="", end_date="", is_open="1"):
            return mds.pd.DataFrame([{"cal_date": d} for d in cal_days])

        async def fetch_top_list(self, trade_date):
            self.top_list_calls.append(trade_date)
            return mds.pd.DataFrame()

    return _Fake()


@pytest.mark.asyncio
async def test_open_trading_days_since_ranges_and_caps(monkeypatch):
    monkeypatch.setattr(mds, "_CATCHUP_LOOKBACK_DAYS", 10)
    client = _fake_client_with_cal(["20260902", "20260903", "20260904", "20260905"])
    # last=09-01 → (09-01, today=09-05] 窗口内的交易日全取
    days = await mds._open_trading_days_since(client, date(2026, 9, 1), date(2026, 9, 5))
    assert days == [date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 5)]
    # last 晚于 today → 空（已追平）
    assert await mds._open_trading_days_since(client, date(2026, 9, 5), date(2026, 9, 5)) == []
    # last=None（空表）→ 只保留最近 _CATCHUP_LOOKBACK_DAYS 个交易日
    monkeypatch.setattr(mds, "_CATCHUP_LOOKBACK_DAYS", 3)
    days = await mds._open_trading_days_since(client, None, date(2026, 9, 5))
    assert days == [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 5)]


@pytest.mark.asyncio
async def test_ingest_dragon_tiger_catchup_pulls_each_missing_day(monkeypatch):
    client = _fake_client_with_cal(["20260903", "20260904"])

    async def fake_max(db):
        return date(2026, 9, 2)

    upsert_counts: list[int] = []

    async def fake_upsert(db, rows):
        upsert_counts.append(len(rows))
        return len(rows)

    monkeypatch.setattr(mds.market_data_repo, "max_dragon_tiger_date", fake_max)
    monkeypatch.setattr(mds.market_data_repo, "upsert_dragon_tiger", fake_upsert)
    monkeypatch.setattr(mds, "_get_tushare", lambda: client)

    result = await mds.ingest_dragon_tiger(db=None)
    assert result == {"upserted": 0, "days": 2}
    assert client.top_list_calls == ["20260903", "20260904"]  # 09-03 缺口被补上


@pytest.mark.asyncio
async def test_ingest_dragon_tiger_explicit_date_skips_catchup(monkeypatch):
    client = _fake_client_with_cal(["20260904"])

    async def unexpected_max(db):  # pragma: no cover — 指定日期时不应触库查 max
        raise AssertionError("max_dragon_tiger_date should not be called")

    async def fake_upsert(db, rows):
        return 0

    monkeypatch.setattr(mds.market_data_repo, "max_dragon_tiger_date", unexpected_max)
    monkeypatch.setattr(mds.market_data_repo, "upsert_dragon_tiger", fake_upsert)
    monkeypatch.setattr(mds, "_get_tushare", lambda: client)

    result = await mds.ingest_dragon_tiger(db=None, trade_date=date(2026, 9, 4))
    assert result == {"upserted": 0, "days": 1}
    assert client.top_list_calls == ["20260904"]


@pytest.mark.asyncio
async def test_global_index_cards_distinguish_markets_with_same_code_digits(monkeypatch):
    """不同市场前缀 + 相同数字段的 secid（1.000001 vs 0.000001）必须各自取到正确报价。

    回归背景：快照曾按「数字段短码」索引（_em_code），沪/深同号段会静默错配；
    改按完整 secid 索引后本用例锁定该不变量。
    """

    class _FakeCache:
        def __init__(self) -> None:
            self.store: dict = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ttl=None):
            self.store[key] = value

    # 用登记表的构造函数造条目（而不是手写 dict）：这样"新增的必填字段"不会只在这里漏掉。
    entries = [
        mds._instrument(
            ts_code="T1.SH",
            name="沪测试",
            market="CN",
            region="asia",
            em_secid="1.000001",
            source="index_daily",
        ),
        mds._instrument(
            ts_code="T2.SZ",
            name="深测试",
            market="CN",
            region="asia",
            em_secid="0.000001",
            source="index_daily",
        ),
    ]
    monkeypatch.setattr(mds, "GLOBAL_INDICES", entries)

    async def fake_snapshot(secids):
        return [
            {
                "code": "000001",
                "secid": "1.000001",
                "name": "沪测试",
                "price": 111.0,
                "pct_change": 1.0,
                "change": 1.0,
            },
            {
                "code": "000001",
                "secid": "0.000001",
                "name": "深测试",
                "price": 222.0,
                "pct_change": 2.0,
                "change": 2.0,
            },
        ]

    async def fake_kline(db, ts_code):
        return []

    monkeypatch.setattr(
        mds,
        "_get_eastmoney",
        lambda: type("C", (), {"fetch_index_snapshot": staticmethod(fake_snapshot)}),
    )

    async def fake_sina_quotes(codes):
        return {}  # 本用例只覆盖东财一路；短路新浪避免真网络

    monkeypatch.setattr(mds, "_get_sina_quotes", lambda: fake_sina_quotes)
    monkeypatch.setattr(mds.index_repo, "get_kline", fake_kline)

    cards = await mds.get_global_index_cards(cache=_FakeCache())
    by_code = {c["ts_code"]: c for c in cards}
    assert by_code["T1.SH"]["price"] == 111.0
    assert by_code["T2.SZ"]["price"] == 222.0


@pytest.mark.asyncio
async def test_global_indices_endpoint_carries_five_fields(monkeypatch):
    """端点层契约（S1.3 交付物「全球标的元数据表」的可读形态）。

    直测 handler（仓库惯例，不引入 TestClient）：断言 `GlobalIndexCardOut` 真的把
    五要素与 `data_source`/`price_source` 透出去——服务层有字段但 schema 漏字段
    （或反之）都会在这里转红。
    """
    from app.api.v1 import market_data as api

    async def fake_cards(cache=None):
        return [
            {
                "ts_code": "IXIC",
                "name": "纳斯达克综合指数",
                "market": "US",
                "region": "americas",
                "session": "09:30-16:00",
                "timezone": "America/New_York",
                "currency": "USD",
                "price": 27366.1698,
                "change": 172.8295,
                "pct_change": 0.64,
                "spark": [1.0, 2.0],
                "updated_at": "2026-10-16T10:00:00+08:00",
                "data_source": "index_global",
                "price_source": "realtime",
            },
            {
                "ts_code": "NDX",
                "name": "纳斯达克100指数",
                "market": "US",
                "region": "americas",
                "session": "09:30-16:00",
                "timezone": "America/New_York",
                "currency": "USD",
                "price": 30883.1478,
                "change": 157.3384,
                "pct_change": 0.51,
                "spark": [],
                "updated_at": "2026-10-16T10:00:00+08:00",
                "data_source": "sina_index",
                "price_source": "realtime_sina",
            },
        ]

    monkeypatch.setattr(mds, "get_global_index_cards", fake_cards)

    out = await api.get_global_indices(cache=None)
    by_code = {c.ts_code: c for c in out}
    assert set(by_code) == {"IXIC", "NDX"}
    for card in out:
        assert card.name and card.session and card.timezone and card.currency and card.data_source
    assert by_code["IXIC"].price != by_code["NDX"].price  # 串号回归在端点层同样成立


@pytest.mark.asyncio
async def test_card_name_comes_from_registry_not_upstream(monkeypatch):
    """卡片名一律取登记表 —— 上游名字与登记名不一致时**不得**反过来覆盖登记名。

    回归背景：东财把"纳斯达克"的 secid（`100.NDX`）与"纳斯达克100"混用，新浪对
    `gb_$ndx` 又回「纳斯达克100」；若卡片名取上游，`IXIC`（纳斯达克**综合**）会显示成
    「纳斯达克」、与 `NDX`（纳斯达克100）在页面上串号 —— 正是 S1.3 要消灭的那处错误。
    """

    class _Cache:
        async def get(self, key):
            return None

        async def set(self, key, value, ttl=None):
            return None

    async def fake_snapshot(secids):
        return [
            {
                "code": "IXIC",
                "secid": "100.IXIC",
                "name": "纳斯达克",  # ← 上游名字（与登记名不同）
                "price": 27366.17,
                "pct_change": 0.64,
                "change": 172.83,
            }
        ]

    async def fake_sina_quotes(codes):
        return {
            "gb_$ndx": {
                "code": "gb_$ndx",
                "name": "纳斯达克100",
                "price": 30883.15,
                "pct_change": 0.51,
                "change": 157.34,
            }
        }

    async def fake_kline(db, ts_code):
        return []

    monkeypatch.setattr(
        mds,
        "_get_eastmoney",
        lambda: type("C", (), {"fetch_index_snapshot": staticmethod(fake_snapshot)})(),
    )
    monkeypatch.setattr(mds, "_get_sina_quotes", lambda: fake_sina_quotes)
    monkeypatch.setattr(mds.index_repo, "get_kline", fake_kline)

    cards = {c["ts_code"]: c for c in await mds.get_global_index_cards(cache=_Cache())}
    assert cards["IXIC"]["name"] == "纳斯达克综合指数"  # 登记名，不是上游的「纳斯达克」
    assert cards["NDX"]["name"] == "纳斯达克100指数"
    assert cards["IXIC"]["name"] != cards["NDX"]["name"]
    # IXIC 与 NDX 的行情查找键必须不同（否则两格价来自同一行情 → 最新值必然相等）
    ixic = next(g for g in mds.GLOBAL_INDICES if g["ts_code"] == "IXIC")
    ndx = next(g for g in mds.GLOBAL_INDICES if g["ts_code"] == "NDX")
    assert ixic["em_secid"] != ndx.get("em_secid")


@pytest.mark.asyncio
async def test_price_source_none_when_no_quote_and_no_history(monkeypatch):
    """**"没有价"必须是 `None` + `price_source="none"`，绝不落成 0。**

    这是 S1.3 brief 最在意的那条不变量（"不得显示假数据"）：`0` 会被读成"平盘"、
    在页面上是一个合法点位，而 `None` + `none` 会被前端渲染成 `—` 与「数据源待接入」。

    什么情况下红：有人把缺价分支写成 `last_close or 0`、把 `_PRICE_NONE` 误标成 `_PRICE_EOD`，
    或让 `any(price is not None)` 的缓存判据在全空时也写缓存（那样一次上游抖动会冻结 60s）。
    """

    class _Cache:
        def __init__(self) -> None:
            self.store: dict = {}
            self.writes = 0

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ttl=None):
            self.writes += 1
            self.store[key] = value

    cache = _Cache()

    async def empty_snapshot(secids):
        return []  # 东财一路空

    async def empty_sina(codes):
        return {}  # 新浪一路空

    async def empty_kline(db, ts_code):
        return []  # 库内无日线

    monkeypatch.setattr(
        mds,
        "_get_eastmoney",
        lambda: type("C", (), {"fetch_index_snapshot": staticmethod(empty_snapshot)})(),
    )
    monkeypatch.setattr(mds, "_get_sina_quotes", lambda: empty_sina)
    monkeypatch.setattr(mds.index_repo, "get_kline", empty_kline)

    cards = await mds.get_global_index_cards(cache=cache)
    for c in cards:
        assert c["price"] is None, f"{c['ts_code']} 无行情时 price 应为 None，实得 {c['price']!r}"
        assert c["price_source"] == "none", f"{c['ts_code']} 应标 none，实得 {c['price_source']}"
        assert c["change"] is None and c["pct_change"] is None
        assert c["spark"] == []
        # 五要素不因"没价"而丢——元数据与行情是两件事
        assert c["session"] and c["timezone"] and c["currency"]
    # 全空 payload **不写缓存**：否则一次上游抖动会把"全无价"冻结整个 TTL
    assert cache.writes == 0
