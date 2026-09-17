"""Task 14：东财板块体系（三套 + 真实 BK code）+ 成分股下钻。

**实测记录（2026-09-18 host curl，`push2delay.eastmoney.com/api/qt/clist/get`——
客户端 `_CLIST_BASE` 同一域；push2 主站在本环境拒连）**：

- 行业板块 `fs=m:90+t:2+f:!50&fid=f3` → `total=496`，首行 `BK1518 种子`
  （f3=7.86 / f6=7621248698.0 成交额 / f62=281576464.0 主力净流入 /
  f104=10 涨 · f105=0 跌 · f106=0 平 / f128=敦煌种业 f140=600354 f136=10.05）
- 概念板块 `fs=m:90+t:3+f:!50` → `total=504`（`BK1645 昨日打二板以上表现` 等）
- 地域板块 `fs=m:90+t:1+f:!50` → `total=31`（`BK0174 西藏板块` 等）
- 成分股 `fs=b:BK1518&fid=f62` → `total=10`（600354 敦煌种业 / 300189 神农种业 / …）
- `f104+f105+f106` = 该板块成分股总数：实测 `BK1211 汽车` 235+11+87=333 ==
  `fs=b:BK1211` 的 `data.total`（故 f106 是"平盘家数"，不是任意占位）

夹具 `fixtures/eastmoney/board_list_industry.json` / `board_stocks_bk1518.json` 是
上述两次请求的**裁剪原始响应**（保留 rc/data.total/diff 外形，diff 截 5 行）。

**离线**：无真实 DB、无真实网络——东财侧用夹具喂 `_get_json`（解析/映射走真实
代码路径），DB 侧 patch `load_day_rows` / `resolve_latest_complete_day`。
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import get_cache
from app.core.providers.eastmoney_client import EastmoneyClient
from app.main import app
from app.schemas.market import HotBoardsOut
from app.services import market_day_service as mds
from app.services import market_service

FIXTURES = Path(__file__).parent / "fixtures" / "eastmoney"
D_TODAY = date(2026, 9, 18)  # 录制日（上海时区），同时是 `_today_sh` 的桩值
D_PREV = date(2026, 9, 17)  # 判据日桩值（T-1）

FS_INDUSTRY = "m:90+t:2+f:!50"
FS_CONCEPT = "m:90+t:3+f:!50"
FS_REGION = "m:90+t:1+f:!50"


def _raw(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 替身：真客户端 + 夹具（HTTP 层以外全走真实代码）
# ---------------------------------------------------------------------------


class _FixtureEM:
    """按 `fs` 返回夹具响应的 `_get_json` 替身；记录每次调用的 params。"""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, base: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"base": base, "path": path, "params": params})
        resp = self._responses[params["fs"]]
        # 上游按 `pz` 截断；夹具必须同构，否则 "limit 透传" 这类断言会假绿。
        pz = params.get("pz")
        diff = resp["data"]["diff"]
        if pz is None or pz >= len(diff):
            return resp
        return {**resp, "data": {**resp["data"], "diff": diff[:pz]}}


def _fixture_client(responses: dict[str, dict[str, Any]]) -> tuple[EastmoneyClient, _FixtureEM]:
    """真 `EastmoneyClient`（真解析/映射），只把 HTTP 层换成夹具。"""
    fake = _FixtureEM(responses)
    client = EastmoneyClient()
    client.__dict__["_get_json"] = fake
    return client, fake


class _FailingEM:
    """东财不可用（两条 fetch 都抛）——回落 / 502 路径的替身。"""

    async def fetch_board_list(self, category: str) -> list[dict[str, Any]]:
        raise RuntimeError("eastmoney unavailable (patched)")

    async def fetch_board_stocks(self, board_code: str, limit: int = 50) -> list[dict[str, Any]]:
        raise RuntimeError("eastmoney unavailable (patched)")


class _StaticEM:
    """直接返回给定归一行的客户端替身（测排序/截断这类服务层逻辑）。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    async def fetch_board_list(self, category: str) -> list[dict[str, Any]]:
        self.calls.append(category)
        return self.rows


def _patch_em(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(market_service, "get_eastmoney_client", lambda: client)


def _patch_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(market_service, "_today_sh", lambda: D_TODAY)


class _RecordingCache:
    """内存 CacheClient 替身：读写都记录；`set` 真的可被后续 `get` 命中。

    （与 `test_market_aggregations._RecordingCache` 的区别：那个**故意**不命中，
    只钉"键/调用"；这里需要能验证"第二次请求吃缓存"。）
    """

    def __init__(self, seeds: dict[str, Any] | None = None) -> None:
        self.seeds: dict[str, Any] = dict(seeds or {})
        self.reads: list[str] = []
        self.writes: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any | None:
        self.reads.append(key)
        return self.seeds.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.writes.append((key, value, ttl))
        self.seeds[key] = value


def _resolved(day: date = D_PREV, quality: str = "complete") -> mds.MarketDay:
    return mds.MarketDay(day, quality, None, 5485, 5513, 1.0, True)  # type: ignore[arg-type]


def _patch_local_day(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> list[Any]:
    """回落路径的 DB 侧：判据日 + 共享 loader（两者都记录调用）。"""
    calls: list[Any] = []

    async def _resolve(_db: Any, *, cache: Any = None) -> mds.MarketDay:
        calls.append(("day", cache))
        return _resolved()

    async def _load(_db: Any, day: date, *, cache: Any = None) -> list[dict[str, Any]]:
        calls.append(("rows", day, cache))
        return rows

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)
    monkeypatch.setattr(market_service, "load_day_rows", _load)
    return calls


# ---------------------------------------------------------------------------
# 客户端：板块榜 / 成分股（夹具 = 裁剪的真实响应）
# ---------------------------------------------------------------------------


async def test_client_fetch_board_list_maps_recorded_row() -> None:
    """夹具行 → 归一字段（含 BK code、家数、领涨股、成交额/主力净流入）。"""
    client, fake = _fixture_client({FS_INDUSTRY: _raw("board_list_industry.json")})

    rows = await client.fetch_board_list("industry")

    assert len(rows) == 5
    assert rows[0] == {
        "board_code": "BK1518",
        "board_name": "种子",
        "pct_change": 7.86,
        "amount": 7621248698.0,
        "main_net_inflow": 281576464.0,
        "main_net_ratio": 3.69,
        "up_count": 10,
        "flat_count": 0,
        "down_count": 0,
        "lead_stock_name": "敦煌种业",
        "lead_stock_code": "600354",
        "lead_stock_pct": 10.05,
    }
    # 真实 code：全部 BK 前缀（前端把 code 当板块身份用）
    assert all(str(r["board_code"]).startswith("BK") for r in rows)
    params = fake.calls[0]["params"]
    assert params["fs"] == FS_INDUSTRY
    assert params["fid"] == "f3"  # 涨跌幅降序
    assert "f104" in params["fields"] and "f106" in params["fields"]


@pytest.mark.parametrize(
    ("category", "expected_fs"),
    [("industry", FS_INDUSTRY), ("concept", FS_CONCEPT), ("region", FS_REGION)],
)
async def test_client_fetch_board_list_uses_the_three_board_sets(
    category: str, expected_fs: str
) -> None:
    """三套板块集合各走自己的 `fs`（概念不再是"无数据源"）。"""
    client, fake = _fixture_client({expected_fs: _raw("board_list_industry.json")})

    rows = await client.fetch_board_list(category)  # type: ignore[arg-type]

    assert fake.calls[0]["params"]["fs"] == expected_fs
    assert rows, "三套 fs 都必须有行"


async def test_client_fetch_board_list_rejects_unknown_category() -> None:
    client, _fake = _fixture_client({})

    with pytest.raises(ValueError, match="industry\\|concept\\|region"):
        await client.fetch_board_list("sector")  # type: ignore[arg-type]


async def test_client_fetch_board_stocks_maps_symbols_and_sends_bk_fs() -> None:
    """成分股：`fs=b:BK1518`、`pz=limit`，返回 symbol/name/pct_change/main_net_inflow。"""
    client, fake = _fixture_client({"b:BK1518": _raw("board_stocks_bk1518.json")})

    rows = await client.fetch_board_stocks("BK1518", limit=3)

    assert len(rows) == 3  # 夹具按 pz 截断（上游行为）
    assert [r["symbol"] for r in rows] == ["600354", "300189", "920087"]
    assert rows[0] == {
        "symbol": "600354",
        "name": "敦煌种业",
        "pct_change": 10.05,
        "main_net_inflow": 81849363.0,
    }
    assert fake.calls[0]["params"]["fs"] == "b:BK1518"
    assert fake.calls[0]["params"]["pz"] == 3


# ---------------------------------------------------------------------------
# get_hot_boards：东财路径
# ---------------------------------------------------------------------------


async def test_hot_boards_industry_from_eastmoney_has_real_codes_and_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """东财路径：真实 BK code、非空 leaders、mainNetInflow/amount 齐备、产地可辨。"""
    client, _fake = _fixture_client({FS_INDUSTRY: _raw("board_list_industry.json")})
    _patch_em(monkeypatch, client)
    _patch_today(monkeypatch)
    cache = _RecordingCache()

    payload = await market_service.get_hot_boards("industry", cache)

    assert payload["source"] == "eastmoney_boards"
    assert payload["degraded_reason"] is None
    # 实时快照：as_of 是今天、质量是 partial（不是库内判据日的 complete）
    assert payload["as_of"] == D_TODAY.isoformat()
    assert payload["as_of_quality"] == "partial"
    assert payload["items"][0] == {
        "id": "industry-BK1518",
        "name": "种子",
        "code": "BK1518",
        "changePercent": 7.86,
        "upCount": 10,
        "flatCount": 0,
        "downCount": 0,
        "leaders": [{"symbol": "600354", "name": "敦煌种业", "changePercent": 10.05}],
        "mainNetInflow": 281576464.0,
        "mainNetRatio": 3.69,
        "amount": 7621248698.0,
    }
    for item in payload["items"]:
        assert item["code"].startswith("BK"), "code 必须是真实东财板块码"
        assert item["leaders"], "leaders 不得为空（东财给了主力净流入最大股）"
        assert item["mainNetInflow"] is not None and item["amount"] is not None
    # 新信封字段能过 Pydantic（schema 已随实现更新）
    assert HotBoardsOut.model_validate(payload).source == "eastmoney_boards"
    # 缓存：键按今日（跨零点换键），实时类 TTL = 60s（不是按日聚合的 300s）
    assert cache.reads == [f"market:hot-boards:em:industry:{D_TODAY.isoformat()}"]
    assert cache.writes == [
        (
            f"market:hot-boards:em:industry:{D_TODAY.isoformat()}",
            payload,
            market_service._REALTIME_CACHE_TTL,
        )
    ]


async def test_hot_boards_concept_returns_rows_and_never_touches_the_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """概念板块**不再返回 []**：走 `t:3`，且完全不打库。"""
    client, fake = _fixture_client({FS_CONCEPT: _raw("board_list_industry.json")})
    _patch_em(monkeypatch, client)
    _patch_today(monkeypatch)

    async def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("eastmoney path must not resolve a day or load rows")

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _boom)
    monkeypatch.setattr(market_service, "load_day_rows", _boom)

    payload = await market_service.get_hot_boards("concept", None)

    assert fake.calls[0]["params"]["fs"] == FS_CONCEPT
    assert payload["items"], "concept 必须返回行（旧契约是恒返回空数组）"
    assert all(item["code"].startswith("BK") for item in payload["items"])
    assert payload["source"] == "eastmoney_boards"


async def test_hot_boards_sorts_by_change_and_keeps_top_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务层自己排序/截断：不依赖上游 `fid/pz` 的排序实现（乱序输入 → 有序输出）。"""

    def _row(code: str, pct: float | None) -> dict[str, Any]:
        return {
            "board_code": code,
            "board_name": f"板块{code}",
            "pct_change": pct,
            "amount": 1.0,
            "main_net_inflow": 2.0,
            "main_net_ratio": 3.0,
            "up_count": 1,
            "flat_count": 0,
            "down_count": 0,
            "lead_stock_name": "龙头",
            "lead_stock_code": "600000",
            "lead_stock_pct": 1.0,
        }

    rows = [_row(f"BK{i:04d}", 1.0) for i in range(12)]
    rows[0] = _row("BK9999", None)  # 缺涨幅排最后，且不参与比较
    rows[1] = _row("BK0000", 99.0)
    _patch_em(monkeypatch, _StaticEM(rows))
    _patch_today(monkeypatch)

    payload = await market_service.get_hot_boards("industry", None)

    assert len(payload["items"]) == 10
    assert payload["items"][0]["code"] == "BK0000"
    assert all(item["changePercent"] is not None for item in payload["items"])


async def test_hot_boards_eastmoney_failure_falls_back_and_is_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """东财炸 → 本地分组 + `source`/`degraded_reason` 双标注（可被判据日口径消费）。"""
    _patch_em(monkeypatch, _FailingEM())
    _patch_today(monkeypatch)
    calls = _patch_local_day(
        monkeypatch,
        [
            {"csrc_desc": "电子", "pct_chg": 3.0},
            {"csrc_desc": "电子", "pct_chg": -1.0},
            {"csrc_desc": "银行", "pct_chg": 0.5},
        ],
    )
    cache = _RecordingCache()

    payload = await market_service.get_hot_boards("industry", cache)

    assert payload["items"][0]["id"] == "industry-电子"
    assert [item["code"] for item in payload["items"]] == ["", ""], "本地分组没有 BK 码"
    assert payload["items"][0]["leaders"] == []
    assert payload["source"] == "local_grouping"
    assert payload["degraded_reason"] == "eastmoney_unavailable"
    # 回落沿用 T+1 判据日/质量
    assert payload["as_of"] == D_PREV.isoformat()
    assert payload["as_of_quality"] == "complete"
    assert [c[0] for c in calls] == ["day", "rows"]
    out = HotBoardsOut.model_validate(payload)
    assert (out.source, out.degraded_reason) == ("local_grouping", "eastmoney_unavailable")
    # 回落 payload 是按日聚合（日键 + 判据日）→ 仍是 300s，别被实时 TTL 一起改小
    assert cache.writes == [
        (
            f"market:hot-boards:industry:{D_PREV.isoformat()}",
            payload,
            market_service._MARKET_CACHE_TTL,
        )
    ]


async def test_hot_boards_unmarked_cached_payloads_are_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """换源前写入的老 payload（无 `source`）一律当 miss——否则会回放"无产地标注"的 items。"""
    _patch_em(monkeypatch, _FailingEM())
    _patch_today(monkeypatch)
    calls = _patch_local_day(monkeypatch, [{"csrc_desc": "电子", "pct_chg": 1.0}])
    cache = _RecordingCache(
        {
            # 东财键里塞一份没有 source 的旧 payload
            f"market:hot-boards:em:industry:{D_TODAY.isoformat()}": {
                "as_of": D_TODAY.isoformat(),
                "as_of_quality": "partial",
                "as_of_reason": None,
                "items": [{"code": "BK0000"}],
            },
            # 本地键里同样塞一份没有 source 的旧 payload
            f"market:hot-boards:industry:{D_PREV.isoformat()}": {
                "as_of": D_PREV.isoformat(),
                "as_of_quality": "complete",
                "as_of_reason": None,
                "items": [{"code": ""}],
            },
        }
    )

    payload = await market_service.get_hot_boards("industry", cache)

    assert cache.reads == [
        f"market:hot-boards:em:industry:{D_TODAY.isoformat()}",
        f"market:hot-boards:industry:{D_PREV.isoformat()}",
    ]
    assert [c[0] for c in calls] == ["day", "rows"], "两份无标注 payload 都必须重算"
    assert payload["source"] == "local_grouping"
    assert payload["items"][0]["id"] == "industry-电子"


async def test_hot_boards_concept_fallback_is_empty_and_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """concept 无本地口径 → 东财炸时**空 items + 降级标注**，绝不回落到省份分组。

    本地白名单只有 ``industry→csrc_desc`` / ``region→province``；旧写法
    ``"csrc_desc" if category == "industry" else "province"`` 会给 concept 发一份
    **省份**行（``id=concept-省XX``），跨口径冒充概念板块。这里喂的库行就带
    ``province``：如果映射表被写回旧形态，items 立刻非空 → 红。
    """
    _patch_em(monkeypatch, _FailingEM())
    _patch_today(monkeypatch)
    calls = _patch_local_day(
        monkeypatch,
        [
            {"province": "上海", "pct_chg": 5.0},
            {"province": "上海", "pct_chg": -2.0},
            {"province": "广东", "pct_chg": 1.0},
        ],
    )
    cache = _RecordingCache()

    payload = await market_service.get_hot_boards("concept", cache)

    assert payload["items"] == [], "concept 没有本地口径，必须空而不是省份行"
    assert [c[0] for c in calls] == ["day", "rows"], "回落路径仍走判据日（键含判据日）"
    assert payload["source"] == "local_grouping"
    assert payload["degraded_reason"] == "eastmoney_unavailable"
    out = HotBoardsOut.model_validate(payload)
    assert (out.source, out.degraded_reason, out.items) == (
        "local_grouping",
        "eastmoney_unavailable",
        [],
    )
    # 降级 payload 仍缓存（按日键 + 按日 TTL），避免每次请求都重算
    assert cache.writes == [
        (
            f"market:hot-boards:concept:{D_PREV.isoformat()}",
            payload,
            market_service._MARKET_CACHE_TTL,
        )
    ]


async def test_realtime_board_caches_use_the_60s_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个实时键（板块榜 + 成分股）TTL = 60s；按日聚合仍是 300s。

    spec §4.5「实时类 60s」：payload 的 ``as_of`` 只有日粒度，"快照多旧"无处可辨，
    而 ``HotSectors`` 盘中 30s 轮询一次 —— 300s 会把同一份快照回放约 10 次。
    """
    client, _fake = _fixture_client(
        {
            FS_INDUSTRY: _raw("board_list_industry.json"),
            "b:BK1518": _raw("board_stocks_bk1518.json"),
        }
    )
    _patch_em(monkeypatch, client)
    _patch_today(monkeypatch)
    cache = _RecordingCache()

    await market_service.get_hot_boards("industry", cache)
    await market_service.get_board_stocks("BK1518", 3, cache)

    assert market_service._REALTIME_CACHE_TTL == 60, "spec 实时类 = 60s"
    assert market_service._MARKET_CACHE_TTL == 300, "按日聚合不得被一起改小"
    assert {key: ttl for key, _value, ttl in cache.writes} == {
        f"market:hot-boards:em:industry:{D_TODAY.isoformat()}": 60,
        "market:board-stocks:BK1518:3": 60,
    }


# ---------------------------------------------------------------------------
# 端点：/market/boards/{board_code}/stocks
# ---------------------------------------------------------------------------


@pytest.fixture
async def _api(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[httpx.AsyncClient, Any], None]:
    """ASGI 客户端 + 注入的记录型缓存（不碰 redis / 不碰网络）。"""

    async def _cache_dep() -> AsyncGenerator[Any, None]:
        yield _api_cache

    _api_cache = _RecordingCache()
    app.dependency_overrides[get_cache] = _cache_dep
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, _api_cache
    finally:
        app.dependency_overrides.clear()


async def test_board_stocks_endpoint_returns_constituents_and_forwards_limit(
    monkeypatch: pytest.MonkeyPatch, _api: tuple[httpx.AsyncClient, _RecordingCache]
) -> None:
    """真实夹具 + 路由级 200；`limit` 透传成 `pz`；第二次请求吃缓存。"""
    client, cache = _api
    em, fake = _fixture_client({"b:BK1518": _raw("board_stocks_bk1518.json")})
    _patch_em(monkeypatch, em)

    resp = await client.get("/api/v1/market/boards/BK1518/stocks", params={"limit": 3})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [row["symbol"] for row in body] == ["600354", "300189", "920087"]
    assert body[0] == {
        "symbol": "600354",
        "name": "敦煌种业",
        "pct_change": 10.05,
        "main_net_inflow": 81849363.0,
    }
    assert fake.calls[0]["params"]["fs"] == "b:BK1518"
    assert fake.calls[0]["params"]["pz"] == 3
    assert cache.reads == ["market:board-stocks:BK1518:3"]
    assert cache.writes[0][0] == "market:board-stocks:BK1518:3"
    assert cache.writes[0][2] == market_service._REALTIME_CACHE_TTL, "成分股是实时快照 → 60s"

    again = await client.get("/api/v1/market/boards/BK1518/stocks", params={"limit": 3})
    assert again.status_code == 200
    assert len(fake.calls) == 1, "第二次必须吃缓存，不再问上游"


@pytest.mark.parametrize("board_code", ["bk1518", "BK", "1518", "BK1518x", "BK15-18"])
async def test_board_stocks_endpoint_rejects_malformed_codes(
    monkeypatch: pytest.MonkeyPatch,
    _api: tuple[httpx.AsyncClient, _RecordingCache],
    board_code: str,
) -> None:
    """非 `^BK\\d+$` 一律 400，且**不向上游发请求**。"""
    client, _cache = _api
    em, fake = _fixture_client({})
    _patch_em(monkeypatch, em)

    resp = await client.get(f"/api/v1/market/boards/{board_code}/stocks")

    assert resp.status_code == 400, (board_code, resp.status_code)
    assert fake.calls == []


async def test_board_stocks_endpoint_502_when_eastmoney_fails(
    monkeypatch: pytest.MonkeyPatch, _api: tuple[httpx.AsyncClient, _RecordingCache]
) -> None:
    """上游挂了返回 502，而不是"该板块没有成分股"的假空列表。"""
    client, _cache = _api
    _patch_em(monkeypatch, _FailingEM())

    resp = await client.get("/api/v1/market/boards/BK1518/stocks")

    assert resp.status_code == 502
    assert resp.json()["detail"] == "eastmoney board constituents unavailable"


# ---------- non-vacuity 证据（按 brief 要求记录，非测试代码的一部分）-----------------
#
# 每个 test 跑红的临时变更：
#
# 1) test_client_fetch_board_list_maps_recorded_row:
#    - `_map_board_row` 里删掉 "board_code": str(d.get("f12")) 一行 → 断言字典不等 → 红。
#    - 改成 {"board_code": d.get("f12")[2:]}（剥掉 BK 前缀）→ 同上红。
#    - 把 params["fid"] 改成 "f62" → fid 断言红（证它真在钉排序口径）。
#
# 2) test_client_fetch_board_list_uses_the_three_board_sets:
#    - `_BOARD_FS["concept"]` 改回 "m:90+t:2+f:!50" → fs 断言红。
#    - 让 fetch_board_list 忽略 category 恒用 industry → 同上红。
#
# 3) test_client_fetch_board_stocks_maps_symbols_and_sends_bk_fs:
#    - fs 改 f"b:{board_code}" → 去掉 b: 前缀 → 断言红（证它拼的是成分股集合）。
#    - pz 写死 100 → pz 断言红（limit 不透传）。
#
# 4) test_hot_boards_industry_from_eastmoney_has_real_codes_and_money:
#    - `_hot_board_item` 的 "code" 改成 ""（旧本地口径）→ BK 前缀断言红。
#    - "leaders" 恒为 [] → leaders 断言红。
#    - 删掉 "mainNetInflow"/"mainNetRatio"/"amount" 三个键 → items[0] 整体比较红。
#    - as_of 改回判据日 / as_of_quality 改 "complete" → 对应断言红。
#    - 缓存键去掉 em:{today} → cache.reads/writes 断言红。
#
# 5) test_hot_boards_concept_returns_rows_and_never_touches_the_db:
#    - 恢复 `if category == "concept": return _envelope(None, [])` → items 断言红（这正是
#      旧契约"concept 恒 []"）。
#    - 让 concept 走 t:2 → fs 断言红。
#    - 在 EM 成功路径里插一次 resolve_latest_complete_day → `_boom` 抛 → 红（证"不打库"）。
#
# 6) test_hot_boards_sorts_by_change_and_keeps_top_ten:
#    - 删掉 `items.sort(...)` → items[0] 是乱序输入里的 BK0001 → 红。
#    - 截断改 [:_HOT_BOARD_LIMIT + 1] → len 断言红。
#    - key 表达式改 item["changePercent"]（不再兜 None）→ TypeError → 红。
#
# 7) test_hot_boards_eastmoney_failure_falls_back_and_is_marked:
#    - except 分支改成 `return payload`（空 items）→ items[0] 断言红。
#    - payload 里删掉 "degraded_reason" → schema 默认 None → 断言红。
#    - payload 里删掉 "source" → `source` 是必填字段 → ValidationError → 红（故两个
#      字段都必须显式写，没有"默认值替它撒谎"的余地）。
#    - 回落后仍用今日 as_of → as_of 断言红。
#
# 8) test_hot_boards_unmarked_cached_payloads_are_misses:
#    - 缓存命中判断改回 `isinstance(cached, dict)` → 回放旧 payload（source 缺失 /
#      items 是假数据）→ 断言红。
#    - 只对 EM 键做校验、本地键不校验 → 本用例仍是红（本地键那份 fake payload 会被回放）。
#
# 9) test_board_stocks_endpoint_returns_constituents_and_forwards_limit:
#    - 端点里把 limit 写死 50 → pz 断言红；第二次请求前清掉缓存写入 → calls 断言红。
#    - 返回原始 f* 行 → body[0] 整体比较红。
#
# 10) test_board_stocks_endpoint_rejects_malformed_codes:
#    - 删掉正则校验 → 200（fixture 里没有该 fs，会 KeyError→500）→ 红。
#    - 正则改 r"^BK"（允许多余字符）→ "BK1518x" 用例红。
#
# 11) test_board_stocks_endpoint_502_when_eastmoney_fails:
#    - 端点改成 `except Exception: return []` → status 断言红。
