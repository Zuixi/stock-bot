"""雷达编排层：用假 repo 验证候选集 / 事件 / 明日触发 / 降级（不连库）.

为什么要这一层：纯函数只证明公式对，**候选集**决定用户看得到谁。三个容易回归的点：
- 停牌复牌的高位股（3 日窗口缺行、算不出 dev3）必须靠「连板梯队」进候选，否则漏报；
- 已达线的票不再给「明日触发价」（持平也触发，给价格是误导）；
- 当日没有涨跌停价时必须显式降级，不能把「没涨停」当成事实。
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.repositories import regulatory_repo
from app.services import regulatory_anomaly_service as svc


def _days(n: int = 35) -> list[date]:
    start = date(2026, 9, 1)
    return [start + timedelta(days=i) for i in range(n)]


DAYS = _days()
AS_OF = DAYS[-1]


def _row(
    stock_id: int,
    symbol: str,
    name: str,
    market: str,
    exchange: str,
    day: date,
    close: float,
    pre: float,
):
    return {
        "stock_id": stock_id,
        "symbol": symbol,
        "name": name,
        "market": market,
        "exchange": exchange,
        "trade_date": day,
        "close": close,
        "pre_close": pre,
    }


def _series_a() -> list[dict]:
    """主板：最后 3 天各 +10% → 3 日偏离 30%，已达线。"""
    closes = [10.0] * 32 + [11.0, 12.1, 13.31]
    rows, prev = [], 10.0
    for i, close in enumerate(closes):
        rows.append(_row(1, "600001", "甲股份", "主板", "Shanghai_Stocks", DAYS[i], close, prev))
        prev = close
    return rows


def _series_b() -> list[dict]:
    """创业板：中间停牌一天 → dev3 算不出；但它是 6 板梯队股，必须进候选并给明日触发。"""
    plan = {
        0: (10.0, 10.0),
        31: (12.0, 10.0),
        33: (14.4, 12.0),
        34: (17.28, 14.4),
    }
    return [
        _row(2, "300001", "乙科技", "创业板", "Shenzen_Stocks", DAYS[i], c, p)
        for i, (c, p) in plan.items()
    ]


def _series_c() -> list[dict]:
    """主板：最后 3 天各 +6% → 3 日偏离 18%，临近（80% 阈值 = 16）。"""
    closes = [10.0] * 32 + [10.6, 11.236, 11.91016]
    rows, prev = [], 10.0
    for i, close in enumerate(closes):
        rows.append(_row(3, "600003", "丙股份", "主板", "Shanghai_Stocks", DAYS[i], close, prev))
        prev = close
    return rows


def _series_d() -> list[dict]:
    """主板：今天首板 +10%，但 3 日偏离只有 10% → 观察档（涨停也要让用户看到）。"""
    closes = [10.0] * 34 + [11.0]
    rows, prev = [], 10.0
    for i, close in enumerate(closes):
        rows.append(_row(4, "600004", "丁股份", "主板", "Shanghai_Stocks", DAYS[i], close, prev))
        prev = close
    return rows


def _series_e() -> list[dict]:
    """主板：6 板梯队股，但**今天停牌**（行情只到昨天）→ 不得进候选.

    它是最容易被「梯队补位」误伤的形态：`market_limit_up_stock_daily` 仍有它、
    `close` 是停牌前最后一笔，若不拦住，页面会把旧价当今日收盘并给出明日触发价。
    """
    closes = [10.0] * 34  # DAYS[0..33]，缺 DAYS[34]（今天）
    rows, prev = [], 10.0
    for i, close in enumerate(closes):
        rows.append(_row(5, "600005", "戊股份", "主板", "Shanghai_Stocks", DAYS[i], close, prev))
        prev = close
    return rows


ALL = _series_a() + _series_b() + _series_c() + _series_d() + _series_e()
RECENT_START = DAYS[-13]


@pytest.fixture
def fake_repo(monkeypatch):
    async def list_market_days(db, as_of, limit):
        return DAYS[-limit:]

    async def load_index_pct(db, codes, start, end):
        return {code: {d: 0.0 for d in DAYS} for code in codes}

    async def load_price_limits(db, as_of):
        return {
            1: {"pre_close": 12.1, "up_limit": 13.31, "down_limit": 10.89, "close": 13.31},
            2: {"pre_close": 14.4, "up_limit": 17.28, "down_limit": 11.52, "close": 17.28},
            3: {"pre_close": 11.236, "up_limit": 12.36, "down_limit": 10.12, "close": 11.91016},
            4: {"pre_close": 10.0, "up_limit": 11.0, "down_limit": 9.0, "close": 11.0},
        }

    async def turnover_data_as_of(db, as_of):
        return AS_OF

    async def load_turnover_only_hits(db, as_of):
        return []

    async def load_limit_up_facts(db, as_of):
        return {
            1: {"streak": 3, "prev_is_lu": True, "days_span": 3, "boards_in_window": 3},
            2: {"streak": 6, "prev_is_lu": True, "days_span": 10, "boards_in_window": 6},
            4: {"streak": 1, "prev_is_lu": False, "days_span": 1, "boards_in_window": 1},
            5: {"streak": 0, "prev_is_lu": True, "days_span": 10, "boards_in_window": 6},
        }

    async def load_quotes(db, start, end, stock_ids=None):
        rows = [r for r in ALL if start <= r["trade_date"] <= end]
        if stock_ids is not None:
            rows = [r for r in rows if r["stock_id"] in stock_ids]
        return sorted(rows, key=lambda r: (r["stock_id"], r["trade_date"]))

    async def stream_quotes(db, start, end):
        # 与 `load_quotes` 同源：线上是同一段 SQL 的流式读法，假 repo 保持一致
        for row in await load_quotes(db, start, end):
            yield row

    for name, fn in {
        "list_market_days": list_market_days,
        "load_index_pct": load_index_pct,
        "load_price_limits": load_price_limits,
        "load_limit_up_facts": load_limit_up_facts,
        "turnover_data_as_of": turnover_data_as_of,
        "load_turnover_only_hits": load_turnover_only_hits,
        "load_quotes": load_quotes,
        "stream_quotes": stream_quotes,
    }.items():
        monkeypatch.setattr(regulatory_repo, name, fn)
    return None


@pytest.mark.asyncio
async def test_candidates_and_status(fake_repo):
    payload = await svc._build(None, AS_OF, 10)
    by_symbol = {i["symbol"]: i for i in payload["items"]}

    assert set(by_symbol) == {"600001", "300001", "600003", "600004"}
    # 戊股份今天停牌（行情只到昨天）：梯队表有它也不得进候选 —— 否则 `close` 是旧价、
    # `day_pct`/`next_trigger` 都在拿停牌前价格冒充今天。
    assert "600005" not in by_symbol

    a = by_symbol["600001"]
    assert a["status"] == "triggered"
    assert a["dev3_pct"] == pytest.approx(30.0)
    assert a["next_trigger"] is None  # 已达线：持平也触发，不给触发价
    assert a["streak"] == 3

    # 停牌复牌股：dev3 算不出（None），但作为 6 板梯队股必须进候选，且能给出明日触发
    b = by_symbol["300001"]
    assert b["dev3_pct"] is None
    assert b["status"] == "watch"
    assert b["boards_in_window"] == 6
    assert b["limit_up"] is True
    assert b["next_trigger"] is not None
    # 最近两日偏离已经 40%（>30% 阈值）：明日持平就触发，needed ≤ 0
    assert b["next_trigger"]["partial_pct"] == pytest.approx(40.0)
    assert b["next_trigger"]["needed_pct"] < 0

    c = by_symbol["600003"]
    assert c["status"] == "near"
    assert c["dev3_pct"] == pytest.approx(18.0)
    assert c["next_trigger"]["needed_pct"] == pytest.approx(8.0)

    d = by_symbol["600004"]
    assert d["status"] == "watch"
    assert d["limit_up"] is True

    assert payload["summary"]["up_triggered"] == 1
    assert payload["summary"]["up_near"] == 1
    assert payload["summary"]["limit_up"] == 3


@pytest.mark.asyncio
async def test_history_only_first_crossing(fake_repo):
    payload = await svc._build(None, AS_OF, 10)
    flat = {d["trade_date"]: d["items"] for d in payload["history"]}
    assert any(i["symbol"] == "600001" for items in flat.values() for i in items)
    # 甲股份在窗口内只应出现一次（连续越线不重复记）
    assert sum(1 for items in flat.values() for i in items if i["symbol"] == "600001") == 1
    assert list(flat) == sorted(flat, reverse=True)


@pytest.mark.asyncio
async def test_missing_limits_degrades(fake_repo, monkeypatch):
    async def empty_limits(db, as_of):
        return {}

    monkeypatch.setattr(regulatory_repo, "load_price_limits", empty_limits)
    payload = await svc._build(None, AS_OF, 10)
    assert payload["degraded_reason"] == "price_limits_missing"
    by_symbol = {i["symbol"]: i for i in payload["items"]}
    # 丁股份只有「今日涨停」这一条入候选的理由；没有限价 → 不入候选，而不是报成 watch
    assert "600004" not in by_symbol
    # 乙科技仍靠连板梯队入候选（限价只是不来判定涨跌停）
    assert "300001" in by_symbol


@pytest.mark.asyncio
async def test_non_trade_day_is_explicit(fake_repo):
    payload = await svc._build(None, AS_OF + timedelta(days=1), 10)
    assert payload["degraded_reason"] == "not_a_trade_day"
    assert payload["items"] == []


@pytest.mark.asyncio
async def test_cache_hit_recomputes_stale_days(fake_repo, monkeypatch):
    """缓存里冻结的陈旧度必须按**当下**重算.

    与 `as_of_quality` 同一条纪律：这两者都是**派生量**，不是库内事实。缓存 TTL 120s、
    跨零点后旧值会一直报到过期 —— 而「12 天前 / 0 天前」正是让人发现数据没在动的唯一信号。
    """
    as_of = svc.market_day_service.now_sh().date() - timedelta(days=12)
    frozen = {
        "as_of": as_of,
        "as_of_quality": "complete",
        "stale_days": 0,  # 写入那天算的
        "degraded_reason": None,
        "limit_price_missing": False,
        "summary": {},
        "items": [],
        "history": [],
        "thresholds": [],
        "caveats": [],
    }

    class FakeCache:
        async def get(self, key):  # noqa: ANN001, ANN201
            return frozen

        async def set(self, key, value, ttl):  # noqa: ANN001, ANN201
            raise AssertionError("缓存命中不应回写")

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return None

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    monkeypatch.setattr(svc, "async_session_factory", FakeSession)

    out = await svc.get_radar(FakeCache(), as_of, 10)

    assert out["stale_days"] == 12
    # `as_of_quality` 同理：它是本次请求的语义（显式传 as_of → requested），不沿用缓存
    assert out["as_of_quality"] == "requested"
    # 缓存内容不得被就地改写（FakeCache.set 未被调用即已断言）
    assert frozen["stale_days"] == 0


@pytest.mark.asyncio
async def test_missing_benchmark_is_explicit(fake_repo, monkeypatch):
    """基准指数缺日必须显式降级，不能长得像「今天没有异动」.

    2026-10-21 发现的真相问题：`daily_deviation` 在基准指数缺日时返回 None，与「个股停牌」
    完全同形；若只靠 KPI 判断，整块板子会显示「已达线 0 / 临近 0」＋每行「窗口缺行（停牌）」，
    读者得到两个错结论。探测器那边早就 `unavailable.add("regulatory_anomaly")` 了，
    读面必须同口径。
    """

    async def no_index(db, codes, start, end):
        return {}

    monkeypatch.setattr(regulatory_repo, "load_index_pct", no_index)
    payload = await svc._build(None, AS_OF, 10)

    assert payload["degraded_reason"] == "benchmark_missing"
    by_symbol = {i["symbol"]: i for i in payload["items"]}
    # 个股行情都在，但偏离算不出 → `None`（不判），不是 0
    assert by_symbol["600001"]["dev3_pct"] is None
    assert by_symbol["600001"]["status"] == "watch"
    # 零 KPI 是「算不出来」，不是「市场无异常」——降级原因就是这句话的载体
    assert payload["summary"]["up_triggered"] == 0
    assert payload["summary"]["up_near"] == 0


@pytest.mark.asyncio
async def test_concurrent_misses_build_only_once(monkeypatch):
    """缓存过期瞬间的并发冷请求只允许算一次（单飞）.

    这是 512MB 容器里最现实的 OOM 入口：一次冷构要扫全市场近 13 日行情，
    TTL 120s 到期瞬间 N 个请求各算一遍 = N 倍内存与 N 倍扫库。
    """
    store: dict[str, dict] = {}
    calls = {"n": 0}

    class FakeCache:
        async def get(self, key):  # noqa: ANN001, ANN201
            return store.get(key)

        async def set(self, key, value, ttl):  # noqa: ANN001, ANN201
            store[key] = value

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return None

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    async def slow_build(db, as_of, history_days):  # noqa: ANN001, ANN202
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return {"as_of": as_of, "items": [], "history": [], "thresholds": [], "caveats": []}

    monkeypatch.setattr(svc, "async_session_factory", FakeSession)
    monkeypatch.setattr(svc, "_build", slow_build)

    results = await asyncio.gather(*(svc.get_radar(FakeCache(), AS_OF, 10) for _ in range(5)))

    assert calls["n"] == 1
    assert len(results) == 5
    assert all(r["as_of"] == AS_OF for r in results)
    # 锁用完即回收，不按 (as_of, history_days) 无界累积
    assert svc._BUILD_LOCKS == {}


@pytest.mark.asyncio
async def test_waiting_requests_hold_no_db_session(monkeypatch):
    """等锁的请求不得占着 DB 连接.

    旧写法把 `async with async_session_factory()` 套在锁外：N 个并发用户等锁时各自握一条
    连接（pool_size=10），10 个以上就开始排队，30s 后报 pool timeout——用户看到的是 500。
    """
    store: dict[str, dict] = {}
    sessions = {"opened": 0}

    class FakeCache:
        async def get(self, key):  # noqa: ANN001, ANN201
            return store.get(key)

        async def set(self, key, value, ttl):  # noqa: ANN001, ANN201
            store[key] = value

    class FakeSession:
        async def __aenter__(self):
            sessions["opened"] += 1
            return None

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    async def slow_build(db, as_of, history_days):  # noqa: ANN001, ANN202
        await asyncio.sleep(0.05)
        return {"as_of": as_of, "items": [], "history": [], "thresholds": [], "caveats": []}

    monkeypatch.setattr(svc, "async_session_factory", FakeSession)
    monkeypatch.setattr(svc, "_build", slow_build)

    await asyncio.gather(*(svc.get_radar(FakeCache(), AS_OF, 10) for _ in range(8)))

    # 8 个并发用户 → 只开了 1 个 session（其余等锁期间不碰连接池）
    assert sessions["opened"] == 1


@pytest.mark.asyncio
async def test_global_semaphore_bounds_concurrent_builds(monkeypatch):
    """`?date=` 把缓存键拉开时，全局信号量必须兜住并发构建数.

    公开接口、无鉴权：`?date=2026-01-05`、`01-06`… 每个都是不同的键，单飞锁不生效。
    没有全局闸门，100 个不同日期 = 100 × 12MB 并发扫库 = OOM。
    """
    live = {"now": 0, "peak": 0}
    built: list[date] = []

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return None

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    async def slow_build(db, as_of, history_days):  # noqa: ANN001, ANN202
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        built.append(as_of)
        try:
            await asyncio.sleep(0.03)
        finally:
            live["now"] -= 1
        return {"as_of": as_of, "items": [], "history": [], "thresholds": [], "caveats": []}

    monkeypatch.setattr(svc, "async_session_factory", FakeSession)
    monkeypatch.setattr(svc, "_build", slow_build)

    dates = [AS_OF - timedelta(days=i) for i in range(6)]
    await asyncio.gather(*(svc.get_radar(None, d, 10) for d in dates))

    assert len(built) == 6  # 不同键 → 各算一次（单飞不适用）
    assert live["peak"] <= svc._MAX_CONCURRENT_BUILDS  # 但并发被信号量压住


@pytest.mark.asyncio
async def test_lock_entry_survives_while_a_waiter_is_building(monkeypatch):
    """单飞锁的键不能在还有等待者/使用者时被提前回收.

    旧写法 `finally: _BUILD_LOCKS.pop(key)`：A 算完就弹键，而 B 还握着同一把锁在等；
    此时 C 的 `setdefault` 会新建一把锁 ⇒ B 与 C 同时冷构，单飞失效（`cache=None` 时必现，
    Redis 恰好不可用也会走到这里）。引用计数只在最后一个使用者退出时回收。
    """
    a_started = asyncio.Event()
    b_started = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()
    builds = 0

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return None

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    async def build(db, as_of, history_days):  # noqa: ANN001, ANN202
        nonlocal builds
        builds += 1
        n = builds
        if n == 1:
            a_started.set()
        elif n == 2:
            b_started.set()
        await (release_a if n == 1 else release_b).wait()
        return {"as_of": as_of, "items": [], "history": [], "thresholds": [], "caveats": []}

    monkeypatch.setattr(svc, "async_session_factory", FakeSession)
    monkeypatch.setattr(svc, "_build", build)
    svc._BUILD_LOCKS.clear()

    key = f"{svc._CACHE_PREFIX}:{AS_OF.isoformat()}:10"
    task_a = asyncio.create_task(svc.get_radar(None, AS_OF, 10))
    await a_started.wait()
    task_b = asyncio.create_task(svc.get_radar(None, AS_OF, 10))
    await asyncio.sleep(0)  # B 走到锁前等待
    assert key in svc._BUILD_LOCKS

    release_a.set()
    await b_started.wait()
    await asyncio.sleep(0)  # 让 A 的 finally 跑完
    assert key in svc._BUILD_LOCKS, "B 还在算，键不能被 A 提前回收"

    release_b.set()
    await asyncio.gather(task_a, task_b)
    assert svc._BUILD_LOCKS == {}


@pytest.mark.asyncio
async def test_future_date_is_rejected_without_building(monkeypatch):
    """未来日期不可能是交易日：直接空结果，且不得进缓存/触发构建（公开 `?date=` 的最小闸门）."""
    called = {"build": 0}

    async def build(db, as_of, history_days):  # noqa: ANN001, ANN202
        called["build"] += 1
        return {}

    monkeypatch.setattr(svc, "_build", build)
    future = svc.market_day_service.now_sh().date() + timedelta(days=7)
    out = await svc.get_radar(None, future, 10)

    assert out["degraded_reason"] == "not_a_trade_day"
    assert out["items"] == []
    assert called["build"] == 0


@pytest.mark.asyncio
async def test_ensure_within_window_rejects_dates_older_than_250_trading_days(monkeypatch):
    """`?date=` 只允许最近 250 个交易日：公开接口的键空间从此有上界."""
    boundary = date(2025, 10, 1)

    async def oldest(db, limit):  # noqa: ANN001, ANN202
        assert limit == svc.DATE_WINDOW_TRADING_DAYS
        return boundary

    class FakeSession:
        async def __aenter__(self):  # noqa: ANN204
            return None

        async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
            return False

    class FakeCache:
        def __init__(self):  # noqa: ANN204
            self.store: dict[str, str] = {}

        async def get(self, key):  # noqa: ANN001, ANN201
            return self.store.get(key)

        async def set(self, key, value, ttl):  # noqa: ANN001, ANN201
            self.store[key] = value

    monkeypatch.setattr(regulatory_repo, "oldest_recent_trade_day", oldest)
    monkeypatch.setattr(svc, "async_session_factory", FakeSession)
    cache = FakeCache()

    with pytest.raises(ValueError, match="250"):
        await svc.ensure_within_window(cache, date(2025, 9, 30))
    await svc.ensure_within_window(cache, boundary)  # 边界当天允许
    assert cache.store[svc._DATE_WINDOW_CACHE_KEY] == boundary.isoformat()


@pytest.mark.asyncio
async def test_turnover_only_bypass_excludes_items_and_maps_fields(fake_repo, monkeypatch):
    """换手率旁路只列「价格未达线」的票，且字段完整（不塞进 items 的第二个口径）."""

    async def hits(db, as_of):  # noqa: ANN001, ANN201
        return [
            # 已在 items 里的票 → 必须剔除（否则同一只票两个口径重复出现）
            {
                "symbol": "600001",
                "name": "甲股份",
                "market": "主板",
                "exchange": "Shanghai_Stocks",
                "close": 13.31,
                "pre_close": 12.1,
                "sum3": 30.0,
                "avg3": 10.0,
                "avg5": 0.3,
                "ratio": 33.0,
            },
            # 价格未达线、仅换手率达标 → 进旁路
            {
                "symbol": "603137",
                "name": "恒尚节能",
                "market": "主板",
                "exchange": "Shanghai_Stocks",
                "close": 26.75,
                "pre_close": 25.2,
                "sum3": 53.91,
                "avg3": 17.97,
                "avg5": 0.32,
                "ratio": 56.02,
            },
        ]

    monkeypatch.setattr(regulatory_repo, "load_turnover_only_hits", hits)
    payload = await svc._build(None, AS_OF, 10)

    assert payload["turnover_available"] is True
    assert [item["symbol"] for item in payload["turnover_only"]] == ["603137"]
    row = payload["turnover_only"][0]
    assert row["name"] == "恒尚节能"
    assert row["turnover_3d_pct"] == pytest.approx(53.91)
    assert row["turnover_ratio"] == pytest.approx(56.0)
    assert row["day_pct"] == pytest.approx(6.15, abs=0.01)


@pytest.mark.asyncio
async def test_turnover_unavailable_is_explicit_and_does_not_query(fake_repo, monkeypatch):
    """daily_basic 未更新到 as_of：旁路为空且 turnover_available=false，不查命中."""

    async def stale(db, as_of):  # noqa: ANN001, ANN201
        return AS_OF - timedelta(days=1)

    async def should_not_run(db, as_of):  # noqa: ANN001, ANN201
        raise AssertionError("换手率数据未更新时不得查命中")

    monkeypatch.setattr(regulatory_repo, "turnover_data_as_of", stale)
    monkeypatch.setattr(regulatory_repo, "load_turnover_only_hits", should_not_run)
    payload = await svc._build(None, AS_OF, 10)

    assert payload["turnover_available"] is False
    assert payload["turnover_only"] == []
    assert payload["turnover_as_of"] == AS_OF - timedelta(days=1)
