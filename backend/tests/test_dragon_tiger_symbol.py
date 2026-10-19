"""龙虎榜按标的过滤（P0-2）—— 服务层单测，不触 DB/网络。

锁三条不变量：

1. ``symbol`` 给出时只回该标的的行，且**跨交易日**（个股上榜是稀疏事件；逐日全市场榜
   对绝大多数个股为空，这正是个股详情页"龙虎榜"标签常年空白的根因）。
2. 缓存键带 ``symbol`` 且带 ``v2`` 前缀 —— 旧键 ``market:dragon-tiger:{date}`` 缓存的是
   **全市场**行，复用它会把 A 股的榜当成 B 股返回（本功能唯一的静默错误风险点）。
3. 不给 ``symbol`` 时逐字沿用旧行为：表内最新交易日、全市场、净买入额降序。

DB 侧谓词（``ts_code IN (...)`` 而非 ``LIKE '%...%'``）由 ``ts_code_candidates`` 的
纯单测钉住；S11 实测通配匹配把 50 条查询从 0.015s 拉到 4.9s。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from app.repositories import market_data_repo as repo
from app.services import market_data_service as mds

D14, D15, D16 = date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)


# ---------------------------------------------------------------------------
# 替身：库内行 / 缓存 / session
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    """``DragonTigerEntry`` 的读取面替身（服务只读这些列）。"""

    trade_date: date
    ts_code: str
    name: str
    net_amount: float | None = 0.0
    close: float | None = 10.0
    pct_change: float | None = 1.0
    turnover_rate: float | None = 2.0
    amount: float | None = 1.0e8
    l_buy: float | None = 6.0e7
    l_sell: float | None = 4.0e7
    l_amount: float | None = 1.0e8
    reason: str = "日涨幅偏离值达7%"


#: 000488 跨两日上榜；600000/300750 只在最新日 —— 用来区分"个股跨日"与"逐日全市场"。
ENTRIES: list[_Entry] = [
    _Entry(D16, "600000.SH", "浦发银行", net_amount=9.0e7),
    _Entry(D16, "300750.SZ", "宁德时代", net_amount=8.0e7),
    _Entry(D16, "000488.SZ", "晨鸣纸业", net_amount=5.0e6, reason="日换手率达20%"),
    _Entry(D15, "600000.SH", "浦发银行", net_amount=1.0e7),
    _Entry(D14, "000488.SZ", "晨鸣纸业", net_amount=2.0e7),
]
LATEST = D16


@dataclass
class _FakeCache:
    """最小 Redis 替身：只关心"键 → 值"，并记录 get/set 的键序。"""

    store: dict[str, Any] = field(default_factory=dict)
    gets: list[str] = field(default_factory=list)
    sets: list[str] = field(default_factory=list)

    async def get(self, key: str) -> Any:
        self.gets.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.sets.append(key)
        self.store[key] = value


class _FakeSession:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@dataclass
class _Calls:
    by_symbol: list[tuple[str, int]] = field(default_factory=list)
    by_date: list[tuple[date, int, str | None]] = field(default_factory=list)
    max_date: int = 0


def _install(monkeypatch: pytest.MonkeyPatch, entries: list[_Entry] | None = None) -> _Calls:
    """把服务用的 session 工厂 + 三个 repo 函数换成内存实现（语义与 SQL 对齐）。"""
    rows = ENTRIES if entries is None else entries
    calls = _Calls()

    async def fake_max_date(db: object) -> date | None:
        calls.max_date += 1
        return max((e.trade_date for e in rows), default=None)

    def _pick(symbol: str | None) -> list[_Entry]:
        if not symbol:
            return list(rows)
        candidates = set(repo.ts_code_candidates(symbol))
        return [e for e in rows if e.ts_code in candidates]

    async def fake_by_symbol(db: object, symbol: str, limit: int = 15) -> list[_Entry]:
        calls.by_symbol.append((symbol, limit))
        picked = sorted(_pick(symbol), key=lambda e: e.trade_date, reverse=True)
        return picked[:limit]

    async def fake_by_date(
        db: object, trade_date: date, limit: int = 15, symbol: str | None = None
    ) -> list[_Entry]:
        calls.by_date.append((trade_date, limit, symbol))
        picked = [e for e in _pick(symbol) if e.trade_date == trade_date]
        picked.sort(key=lambda e: e.net_amount or 0.0, reverse=True)
        return picked[:limit]

    monkeypatch.setattr(mds.market_data_repo, "max_dragon_tiger_date", fake_max_date)
    monkeypatch.setattr(mds.market_data_repo, "list_dragon_tiger_by_symbol", fake_by_symbol)
    monkeypatch.setattr(mds.market_data_repo, "list_dragon_tiger", fake_by_date)
    monkeypatch.setattr("app.core.database.async_session_factory", lambda: _FakeSession())
    return calls


# ---------------------------------------------------------------------------
# 1) symbol 过滤：只回该标的，且跨交易日
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_symbol_filter_returns_only_that_stock_across_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch)
    rows = await mds.get_dragon_tiger(_FakeCache(), symbol="000488")

    assert {r["ts_code"] for r in rows} == {"000488.SZ"}
    assert {r["symbol"] for r in rows} == {"000488"}
    days = [r["trade_date"] for r in rows]
    assert days == [D16.isoformat(), D14.isoformat()], f"应跨日且最新在前，实际 {days}"
    # 走的是跨日个股查询，不是"最新日的全市场榜再客户端过滤"
    assert calls.by_symbol == [("000488", mds.DRAGON_TIGER_CACHE_LIMIT)]
    assert calls.by_date == []


@pytest.mark.asyncio
async def test_symbol_filter_applies_request_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    rows = await mds.get_dragon_tiger(_FakeCache(), limit=1, symbol="000488")
    assert [r["trade_date"] for r in rows] == [D16.isoformat()]


@pytest.mark.asyncio
async def test_unknown_symbol_returns_empty_and_caches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch)
    cache = _FakeCache()
    assert await mds.get_dragon_tiger(cache, symbol="999999") == []
    assert cache.store == {}


# ---------------------------------------------------------------------------
# 2) 缓存键隔离：A/B 两股交叉请求不串
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_keys_are_isolated_per_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch)
    cache = _FakeCache()

    a = await mds.get_dragon_tiger(cache, symbol="000488")
    b = await mds.get_dragon_tiger(cache, symbol="600000")

    assert {r["ts_code"] for r in a} == {"000488.SZ"}
    assert {r["ts_code"] for r in b} == {"600000.SH"}
    assert not ({r["ts_code"] for r in a} & {r["ts_code"] for r in b})

    # 两个键都带 scope+symbol 且都带 v2 前缀，取值互不覆盖
    assert len(cache.store) == 2
    assert all(k.startswith("market:dragon-tiger:v2:range:") for k in cache.store)
    key_a = f"market:dragon-tiger:v2:range:000488:{LATEST.isoformat()}"
    key_b = f"market:dragon-tiger:v2:range:600000:{LATEST.isoformat()}"
    assert set(cache.store) == {key_a, key_b}
    assert {r["ts_code"] for r in cache.store[key_a]} == {"000488.SZ"}
    assert {r["ts_code"] for r in cache.store[key_b]} == {"600000.SH"}

    # 二次请求命中各自的缓存，不新增 repo 调用，也不串
    assert len(calls.by_symbol) == 2
    a2 = await mds.get_dragon_tiger(cache, symbol="000488")
    assert {r["ts_code"] for r in a2} == {"000488.SZ"}
    assert len(calls.by_symbol) == 2


@pytest.mark.asyncio
async def test_legacy_market_wide_cache_entry_is_never_served_for_a_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧键里是全市场行；v2 键必须让它彻底失效，否则 A 股的榜会当成 B 股返回。"""
    _install(monkeypatch)
    legacy_key = f"market:dragon-tiger:{LATEST.isoformat()}"
    decoy = [{"trade_date": LATEST.isoformat(), "ts_code": "600000.SH", "symbol": "600000"}]
    cache = _FakeCache(store={legacy_key: decoy})

    rows = await mds.get_dragon_tiger(cache, symbol="000488")

    assert [r["ts_code"] for r in rows] == ["000488.SZ", "000488.SZ"]
    assert all(r["ts_code"] != "600000.SH" for r in rows)
    # 旧键既没被读（只有 v2 键进 gets）也没被写
    assert cache.gets == [f"market:dragon-tiger:v2:range:000488:{LATEST.isoformat()}"]
    assert legacy_key not in cache.sets


# ---------------------------------------------------------------------------
# 3) 回归保护：缺 symbol 时与改前逐字一致
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_symbol_matches_legacy_latest_date_market_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch)
    cache = _FakeCache()

    rows = await mds.get_dragon_tiger(cache)

    # 改前语义：表内最新交易日 + 全市场 + 净买入额降序
    assert [r["ts_code"] for r in rows] == ["600000.SH", "300750.SZ", "000488.SZ"]
    assert {r["trade_date"] for r in rows} == {LATEST.isoformat()}
    assert calls.by_symbol == [], "缺 symbol 时不得走个股路径"
    assert calls.by_date == [(LATEST, mds.DRAGON_TIGER_CACHE_LIMIT, None)]
    # 全市场键用 all 占位、scope=day，绝不与任何个股键相等
    assert list(cache.store) == [f"market:dragon-tiger:v2:day:all:{LATEST.isoformat()}"]


@pytest.mark.asyncio
async def test_without_symbol_ignores_stale_symbol_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向隔离：个股键存在时，全市场请求不得拿它当结果。"""
    _install(monkeypatch)
    key_a = f"market:dragon-tiger:v2:range:000488:{LATEST.isoformat()}"
    cache = _FakeCache(store={key_a: [{"trade_date": LATEST.isoformat(), "ts_code": "000488.SZ"}]})

    rows = await mds.get_dragon_tiger(cache)

    assert len(rows) == 3
    assert [r["ts_code"] for r in rows] == ["600000.SH", "300750.SZ", "000488.SZ"]


# ---------------------------------------------------------------------------
# 4) date + symbol：在指定交易日内按标的过滤
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_date_and_symbol_together_filter_within_that_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch)

    rows = await mds.get_dragon_tiger(_FakeCache(), D14.isoformat(), symbol="000488")

    assert [r["trade_date"] for r in rows] == [D14.isoformat()]
    assert {r["ts_code"] for r in rows} == {"000488.SZ"}
    # 显式 date → 仍走逐日榜查询（只是 SQL 里多了 symbol 谓词）
    assert calls.by_symbol == [], "date + symbol 不应退化成跨日查询"
    assert calls.by_date == [(D14, mds.DRAGON_TIGER_CACHE_LIMIT, "000488")]


@pytest.mark.asyncio
async def test_date_and_symbol_with_no_hit_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch)
    cache = _FakeCache()
    assert await mds.get_dragon_tiger(cache, D15.isoformat(), symbol="000488") == []
    assert cache.store == {}


# ---------------------------------------------------------------------------
# 4b) 缓存键按查询形态（scope）分段：跨日 / 单日结果集不同，不得互相顶掉
#
# ``date`` 省略时 effective 取表内最新日；``date`` 显式给成同一天时 effective 完全相同 ——
# 此时唯一能区分两种查询的只有 scope。少这一段就会静默串味：
#   跨日先入缓存 → 同日同股的显式 date 请求拿到多日行（违背 date 语义）；
#   单日先入缓存 → 个股页的无 date 请求只拿到一天（**正是 P0-2 要消除的空榜症状**）。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_range_cache_is_not_served_to_a_day_scoped_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """防回归：跨日查询的缓存不得被 ``?symbol=X&date=<表内最新日>`` 当成单日结果返回。"""
    calls = _install(monkeypatch)
    cache = _FakeCache()

    ranged = await mds.get_dragon_tiger(cache, symbol="000488")
    assert [r["trade_date"] for r in ranged] == [D16.isoformat(), D14.isoformat()]

    # date 显式给成"表内最新日"：effective 与上一步相同，唯一的区分就是 scope
    day_rows = await mds.get_dragon_tiger(cache, LATEST.isoformat(), symbol="000488")

    assert [r["trade_date"] for r in day_rows] == [LATEST.isoformat()], (
        "跨日缓存被当成单日结果返回了 —— 缓存键缺 scope 段"
    )
    assert len(calls.by_date) == 1, "显式 date 必须缓存未命中并真查逐日榜"
    assert set(cache.store) == {
        f"market:dragon-tiger:v2:range:000488:{LATEST.isoformat()}",
        f"market:dragon-tiger:v2:day:000488:{LATEST.isoformat()}",
    }


@pytest.mark.asyncio
async def test_day_scoped_cache_is_not_served_to_a_range_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """防回归（原症状）：单日缓存先入后，个股页的无 date 请求不得只拿到一天的行。"""
    _install(monkeypatch)
    cache = _FakeCache()

    one_day = await mds.get_dragon_tiger(cache, LATEST.isoformat(), symbol="000488")
    assert [r["trade_date"] for r in one_day] == [LATEST.isoformat()]

    ranged = await mds.get_dragon_tiger(cache, symbol="000488")

    assert [r["trade_date"] for r in ranged] == [D16.isoformat(), D14.isoformat()], (
        "单日缓存被当成跨日结果返回了 —— 个股页又只剩一天"
    )
    assert set(cache.store) == {
        f"market:dragon-tiger:v2:range:000488:{LATEST.isoformat()}",
        f"market:dragon-tiger:v2:day:000488:{LATEST.isoformat()}",
    }


# ---------------------------------------------------------------------------
# 5) 索引友好谓词：ts_code 候选（不许退化成 LIKE）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("000488", ["000488.SZ", "000488.SH", "000488.BJ"]),
        ("600000", ["600000.SZ", "600000.SH", "600000.BJ"]),
        ("000488.SZ", ["000488.SZ"]),  # 已是 ts_code → 单值等值
        (" 600000.sh ", ["600000.SH"]),  # 归一：去空白 + 大写
    ],
)
def test_ts_code_candidates_are_index_friendly(symbol: str, expected: list[str]) -> None:
    assert repo.ts_code_candidates(symbol) == expected


# ---------------------------------------------------------------------------
# 6) 端点契约：symbol 必须被接受并透传到服务层
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_forwards_symbol_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.v1 import market_data as api

    seen: list[tuple[Any, ...]] = []

    async def fake_get_dragon_tiger(
        cache: Any, date_iso: str | None = None, limit: int = 15, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        seen.append((date_iso, limit, symbol))
        return [
            {
                "trade_date": LATEST.isoformat(),
                "ts_code": "000488.SZ",
                "symbol": "000488",
                "reason": "日换手率达20%",
            }
        ]

    monkeypatch.setattr(mds, "get_dragon_tiger", fake_get_dragon_tiger)

    out = await api.get_dragon_tiger_endpoint(_FakeCache(), None, "000488", 15)

    assert seen == [(None, 15, "000488")]
    assert [o.symbol for o in out] == ["000488"]
