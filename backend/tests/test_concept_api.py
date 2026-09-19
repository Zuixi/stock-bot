"""`GET /api/v1/concepts` 契约（T6）：默认门禁钉形状适配器，`-m e2e` 直连真库钉口径。

分两层：

- **默认门禁（不连库）**：`hot_board_rows` 的形状适配器——T7 概念卡片直接消费它，
  键名/`id` 前缀错一个前端就渲染 `undefined`。monkeypatch `list_boards` + 假 session，
  所以 `uv run pytest` 在 dev DB 未启动时也全绿。
- **`-m e2e`（真库 + FastAPI app）**：契约字段（`price_source` / `membership_as_of` / `total`）、
  `avg_pct DESC NULLS LAST` 顺序与分页不重叠、东财快照缺行必须 `None`（**绝不 0 填充**）。
  用例只读，且不依赖任何"今天恰好是多少"的数字：期望值全部现查 `concept_members` /
  `concept_boards` / `sector_moneyflow_snapshots` 再对拍。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from app.core.database import async_session_factory, engine
from app.core.redis import CacheClient, close_redis_pool, get_redis_pool
from app.main import app
from app.repositories import concept_repo, limit_up_repo, market_data_repo
from app.schemas.concept import ConceptDetailOut
from app.schemas.stock import StockEnrichedOut
from app.services import concept_service, limit_up_service

# 前端卡片读取的完整键集（frontend/src/shared/api/market.ts HotBoardItem）
HOT_BOARD_KEYS = {
    "id",
    "name",
    "code",
    "changePercent",
    "upCount",
    "flatCount",
    "downCount",
    "leaders",
}
LEADER_KEYS = {"symbol", "name", "changePercent"}
# §2.2 列表响应键集
LIST_KEYS = {
    "as_of",
    "membership_as_of",
    "price_source",
    "flow_source",
    "total",
    "degraded_reason",
    "items",
}
ITEM_KEYS = {
    "board_code",
    "board_name",
    "member_count",
    "unresolved_count",
    "priced_count",
    "up_count",
    "flat_count",
    "down_count",
    "avg_pct",
    "main_net_inflow",
    "main_net_ratio",
    "lead_stock_name",
    "lead_stock_code",
    "lead_stock_pct",
    "leaders",
}
LEADER_ITEM_KEYS = {"symbol", "name", "change_percent"}

# 假 body：一块正常板 + 一块全未解析板（avg_pct=None / 领涨股无行情 → 前端侧必须被过滤）
_CANNED_BODY: dict[str, Any] = {
    "as_of": "2026-09-18",
    "membership_as_of": "2026-09-20",
    "price_source": "local_agg",
    "flow_source": "em_clist",
    "total": 504,
    "degraded_reason": None,
    "items": [
        {
            "board_code": "BK0714",
            "board_name": "CPO概念",
            "member_count": 12,
            "unresolved_count": 1,
            "priced_count": 10,
            "up_count": 7,
            "flat_count": 1,
            "down_count": 2,
            "avg_pct": 3.14159,
            "main_net_inflow": 1234.5,
            "main_net_ratio": 2.5,
            "lead_stock_name": "中际旭创",
            "lead_stock_code": "300308",
            "lead_stock_pct": 9.99,
            "leaders": [
                {"symbol": "300308", "name": "中际旭创", "change_percent": 9.99},
                {"symbol": "300502", "name": "新易盛", "change_percent": 5.5},
            ],
        },
        {
            "board_code": "BK0999",
            "board_name": "全未解析",
            "member_count": 2,
            "unresolved_count": 2,
            "priced_count": 0,
            "up_count": 0,
            "flat_count": 0,
            "down_count": 0,
            "avg_pct": None,
            "main_net_inflow": None,
            "main_net_ratio": None,
            "lead_stock_name": None,
            "lead_stock_code": None,
            "lead_stock_pct": None,
            "leaders": [{"symbol": "600000", "name": "浦发银行", "change_percent": None}],
        },
    ],
}


class _FakeSession:
    """hot_board_rows 自开的会话替身：不连库（list_boards 已被 monkeypatch）。"""

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> None:
        return None


# ---------------------------------------------------------------------------
# 默认门禁：hot_board_rows 形状适配器（monkeypatch list_boards，不触 DB）
# ---------------------------------------------------------------------------


async def test_hot_board_rows_matches_frontend_card_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """键集/`id` 前缀/单位全部对齐行业分支；无行情领涨股（null）不得下发。"""
    calls: list[tuple[str, int, int]] = []

    async def _fake_list_boards(
        db: Any, cache: Any, sort: str, limit: int, offset: int
    ) -> dict[str, Any]:
        calls.append((sort, limit, offset))
        return _CANNED_BODY

    monkeypatch.setattr(concept_service, "list_boards", _fake_list_boards)
    monkeypatch.setattr("app.core.database.async_session_factory", _FakeSession)

    rows = await concept_service.hot_board_rows(None, 10)

    assert calls == [("pct", 10, 0)], "热门板块按均价榜取数（与行业分支 avg_chg DESC 同序）"
    assert len(rows) == 2
    assert all(set(row) == HOT_BOARD_KEYS for row in rows), "键集必须与 HotBoardItem 完全一致"
    assert all(set(leader) == LEADER_KEYS for row in rows for leader in row["leaders"])

    first = rows[0]
    assert first["id"] == "concept-BK0714", "id 必须带 concept- 前缀，避免与 industry-/region- 撞车"
    assert first["code"] == "BK0714"
    assert first["name"] == "CPO概念"
    assert first["changePercent"] == pytest.approx(3.14)
    assert (first["upCount"], first["flatCount"], first["downCount"]) == (7, 1, 2)
    assert [le["symbol"] for le in first["leaders"]] == ["300308", "300502"]

    # 全未解析板：avg_pct=None → 0.00（数字契约）；领涨股无行情 → 整个 leader 被丢弃
    second = rows[1]
    assert second["id"] == "concept-BK0999"
    assert second["changePercent"] == 0.0
    assert second["leaders"] == [], "changePercent 不可为 null（前端 .toFixed 会 TypeError）"


async def _const(value: Any) -> Any:
    return value


async def test_list_boards_degrades_on_missing_quotes_or_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§2.3 降级词表：无行情 → `no_quotes`，无成分 → `no_members`；降级态不写缓存、不聚合。"""
    written: list[tuple[str, int | None]] = []

    class _Cache:
        async def get(self, key: str) -> None:
            return None

        async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
            written.append((key, ttl))

    def _no_aggregate(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("降级态不得聚合（无行情/无成分时聚合没有语义）")

    monkeypatch.setattr(concept_service.concept_repo, "count_active_boards", lambda db: _const(7))
    monkeypatch.setattr(concept_service.concept_repo, "aggregate_boards", _no_aggregate)

    monkeypatch.setattr(concept_service.limit_up_repo, "latest_quote_date", lambda db: _const(None))
    monkeypatch.setattr(
        concept_service.concept_repo, "latest_membership_date", lambda db: _const(date(2026, 9, 20))
    )
    no_quotes = await concept_service.list_boards(None, _Cache(), "pct", 5, 0)
    assert no_quotes["degraded_reason"] == "no_quotes"
    assert no_quotes["as_of"] is None
    assert no_quotes["total"] == 7
    assert no_quotes["items"] == [] and no_quotes["flow_source"] is None

    monkeypatch.setattr(
        concept_service.limit_up_repo, "latest_quote_date", lambda db: _const(date(2026, 9, 18))
    )
    monkeypatch.setattr(
        concept_service.concept_repo, "latest_membership_date", lambda db: _const(None)
    )
    no_members = await concept_service.list_boards(None, _Cache(), "pct", 5, 0)
    assert no_members["degraded_reason"] == "no_members"
    assert no_members["membership_as_of"] is None and no_members["items"] == []

    assert written == [], "降级响应不得进缓存（数据补齐后还要再等一个 TTL 才可见）"


# ---------------------------------------------------------------------------
# `-m e2e`：真库 + FastAPI app
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clear_concept_cache_and_dispose(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[None, None]:
    """e2e 用例前清掉本命名空间缓存（挡住 dev API 留下的旧 as_of），结束释放 DB/Redis 连接池。

    默认门禁的纯适配器用例**不许连库**，按 marker 短路。`delete_pattern` 内部吞掉 Redis
    不可用（本地 6379 未起时是 no-op），故本夹具不引入新的环境依赖。
    """
    is_e2e = request.node.get_closest_marker("e2e") is not None
    if is_e2e:
        client = CacheClient(await get_redis_pool())
        await client.delete_pattern("market:concept:list:*")
        await client.delete_pattern("market:concept:by-symbol:*")
    try:
        yield
    finally:
        if is_e2e:
            await engine.dispose()
            await close_redis_pool()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _db_scalar(sql: str, params: dict[str, Any] | None = None) -> Any:
    async with async_session_factory() as db:
        return (await db.execute(text(sql), params or {})).scalar_one()


async def _concept_flow_rows(as_of: Any) -> dict[str, Any]:
    """服务端实际取数（`list_sector_moneyflow(..., limit=100)`），按 board_code 建索引。

    刻意复用仓库函数而不是另写一条 SQL：东财快照按 `main_net_inflow DESC NULLS LAST LIMIT 100`
    截断，边界（第 100 名 vs 第 101 名）由同一条 ORDER BY 决定；测试另写一份 SQL 会在并列处
    与实现分叉。这里验证的是 T6 新增的 join/None 填充语义，`list_sector_moneyflow` 本身另有测试。
    """
    async with async_session_factory() as db:
        snaps = await market_data_repo.list_sector_moneyflow(db, as_of, "concept", 100)
    return {snap.board_code: snap for snap in snaps}


@pytest.mark.e2e
async def test_concept_list_contract_matches_dev_db() -> None:
    """契约字段 + 与库内对拍 + 顺序/分页确定性（不依赖任何会变的当日数字）。"""
    as_of = await _db_scalar("SELECT max(trade_date) FROM daily_quotes")
    membership_as_of = await _db_scalar("SELECT max(last_seen_on) FROM concept_members")
    total = await _db_scalar("SELECT count(*) FROM concept_boards WHERE is_active")
    assert as_of is not None and membership_as_of is not None, "dev DB 缺行情/成分数据"
    flow_map = await _concept_flow_rows(as_of)

    async with _client() as client:
        resp = await client.get("/api/v1/concepts", params={"limit": 5})
        again = await client.get("/api/v1/concepts", params={"limit": 5})
        next_page = await client.get("/api/v1/concepts", params={"limit": 5, "offset": 5})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == LIST_KEYS
    assert body["as_of"] == as_of.isoformat()
    assert body["membership_as_of"] == membership_as_of.isoformat(), "历史口径声明必须回传"
    assert body["price_source"] == "local_agg"
    assert body["total"] == total, "total = 启用板块总数，与分页无关"
    assert body["degraded_reason"] is None, "有数据时必须为 null"

    items = body["items"]
    assert len(items) == 5
    # avg_pct DESC NULLS LAST：非空值递减，None 全部排在尾部
    avgs = [i["avg_pct"] for i in items]
    non_null = [v for v in avgs if v is not None]
    assert non_null == sorted(non_null, reverse=True)
    assert avgs[len(non_null) :] == [None] * (len(avgs) - len(non_null)), "NULL 必须在最后"
    assert [i["board_code"] for i in again.json()["items"]] == [i["board_code"] for i in items], (
        "同一请求两次必须同序（avg_pct DESC NULLS LAST, board_code ASC）"
    )
    assert {i["board_code"] for i in next_page.json()["items"]}.isdisjoint(
        {i["board_code"] for i in items}
    ), "确定性顺序下相邻分页不得重叠"

    for item in items:
        assert set(item) == ITEM_KEYS
        assert item["member_count"] >= item["priced_count"]
        assert item["priced_count"] == item["up_count"] + item["flat_count"] + item["down_count"]
        assert all(set(leader) == LEADER_ITEM_KEYS for leader in item["leaders"])
        snap = flow_map.get(item["board_code"])
        if snap is None:
            # 东财快照只覆盖当日 Top100 震荡集：缺行必须整体 None，绝不 0 填充
            assert item["main_net_inflow"] is None
            assert item["main_net_ratio"] is None
            assert item["lead_stock_name"] is None
            assert item["lead_stock_code"] is None
            assert item["lead_stock_pct"] is None
        else:
            assert item["main_net_inflow"] == snap.main_net_inflow
            assert item["main_net_ratio"] == snap.main_net_ratio
            assert item["lead_stock_name"] == snap.lead_stock_name
            assert item["lead_stock_code"] == snap.lead_stock_code
            assert item["lead_stock_pct"] == snap.lead_stock_pct
    # 两源分离，且 `flow_source` 是**数据集级**声明（快照表是否有行），不是"本页命中行数"：
    # 逐页统计会让同一 as_of 的首页 = "em_clist"、offset 越界空页 = null。
    assert body["flow_source"] == ("em_clist" if flow_map else None)


@pytest.mark.e2e
async def test_concept_list_offset_past_end_keeps_dataset_level_flow_source() -> None:
    """`offset` 越过末尾（`items == []`）时 `total` 与 `flow_source` 仍须是数据集级的值。

    这是 I1 的症状最明显处：逐页统计 `flow_source` 的旧实现里，越界空页永远返回 `null`，
    哪怕快照表当天有行——同一个 `as_of` 的首页却是 `"em_clist"`，前端据此会误判"资金流源掉了"。
    """
    as_of = await _db_scalar("SELECT max(trade_date) FROM daily_quotes")
    total = await _db_scalar("SELECT count(*) FROM concept_boards WHERE is_active")
    assert as_of is not None, "dev DB 缺行情数据"
    flow_map = await _concept_flow_rows(as_of)
    expected_flow_source = "em_clist" if flow_map else None

    async with _client() as client:
        first = (await client.get("/api/v1/concepts", params={"limit": 5})).json()
        past_end = (
            await client.get("/api/v1/concepts", params={"limit": 5, "offset": total + 10})
        ).json()

    assert past_end["items"] == [], "offset 越界必须是空页"
    assert past_end["total"] == total, "空页不得把 total 归零（total 与分页无关）"
    assert past_end["as_of"] == as_of.isoformat()
    assert past_end["flow_source"] == expected_flow_source
    assert past_end["flow_source"] == first["flow_source"], (
        "同一 as_of 的同一数据集，首页与越界空页必须给出同一个 flow_source"
    )


@pytest.mark.e2e
async def test_concept_list_inflow_sort_reorders_same_page_deterministically() -> None:
    """`sort=inflow` 只重排当前分页：板块集合与 `sort=pct` 相同，顺序按流入降序 + 码升序兜底。"""
    async with _client() as client:
        by_pct = (await client.get("/api/v1/concepts", params={"sort": "pct", "limit": 20})).json()
        by_inflow = (
            await client.get("/api/v1/concepts", params={"sort": "inflow", "limit": 20})
        ).json()

    assert {i["board_code"] for i in by_inflow["items"]} == {
        i["board_code"] for i in by_pct["items"]
    }, "inflow 排序只对 avg_pct 分页重排，不换页"
    assert by_inflow["price_source"] == "local_agg"
    keys = [
        (i["main_net_inflow"] is None, -(i["main_net_inflow"] or 0.0), i["board_code"])
        for i in by_inflow["items"]
    ]
    assert keys == sorted(keys), "main_net_inflow DESC NULLS LAST, board_code ASC"


@pytest.mark.e2e
async def test_concept_list_missing_flow_rows_are_none_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """资金流整段缺失（快照未采集/非震荡集）→ 全部 None 且 `flow_source=null`，不得 0 填充。"""

    async def _no_flow_rows(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(market_data_repo, "list_sector_moneyflow", _no_flow_rows)

    async with _client() as client:
        body = (await client.get("/api/v1/concepts", params={"limit": 5})).json()

    assert body["items"], "成分/行情都在，缺的只是资金流"
    assert body["flow_source"] is None
    for item in body["items"]:
        assert item["main_net_inflow"] is None
        assert item["main_net_inflow"] != 0, "缺失 ≠ 0"
        assert item["main_net_ratio"] is None
        assert item["lead_stock_name"] is None
        assert item["lead_stock_code"] is None
        assert item["lead_stock_pct"] is None


# 卡片契约的 8 键（industry 分支现产物）；委托测试只钉"形状来自 concept_service"这件事
_CONCEPT_ROWS: list[dict[str, Any]] = [
    {
        "id": "concept-BK0714",
        "name": "CPO概念",
        "code": "BK0714",
        "changePercent": 3.14,
        "upCount": 7,
        "flatCount": 1,
        "downCount": 2,
        "leaders": [{"symbol": "300308", "name": "中际旭创", "changePercent": 9.99}],
    }
]


async def test_get_hot_boards_concept_delegates_to_concept_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`category=concept` 必须**委托** concept_service.hot_board_rows（此前硬编码 return []）。

    概念是"当前成分 × 本地行情"的聚合，和行业/地域分支的 `stocks.{csrc_desc,province}`
    单值分组 SQL 没有共同形状；这条测试钉住"概念分支不再自己造行"：
    返回对象是委托函数产物的**同一对象**（不是重新聚合/拼装），且恰好调一次。

    同时钉住前端卡片读取的 8 键——键名漂移要在默认门禁就失败，而不是等前端渲染 undefined。
    """
    from app.services import market_service

    calls: list[tuple[Any, int]] = []

    async def _fake_hot_board_rows(cache: Any, limit: int = 10) -> list[dict[str, Any]]:
        calls.append((cache, limit))
        return _CONCEPT_ROWS

    async def _explode() -> Any:
        raise AssertionError("concept 分支不得另开 DB 会话自造行（必须委托 concept_service）")

    monkeypatch.setattr(concept_service, "hot_board_rows", _fake_hot_board_rows)
    monkeypatch.setattr("app.core.database.async_session_factory", _explode)

    rows = await market_service.get_hot_boards("concept", cache=None)

    assert rows is _CONCEPT_ROWS, "必须是委托产物的同一对象（委托，而非重新推导一份等价数据）"
    assert len(calls) == 1, "恰好委托一次"
    assert calls[0] == (None, 10), "cache 原样透传；limit 与行业分支 LIMIT 10 对齐"
    assert set(rows[0]) == HOT_BOARD_KEYS, "键集必须与前端 HotBoardItem 完全一致"


# ---------------------------------------------------------------------------
# 默认门禁：T8 详情（板内梯队同源 + KPI 裁决 + 降级词表）；T9 成分/反查（不触 DB）
# ---------------------------------------------------------------------------

# `find_board` 只回 board_code/board_name（is_active 无人读，已从仓库层删掉 —— M1）
_BK0501_ROW_META: dict[str, Any] = {
    "board_code": "BK0501",
    "board_name": "次新股",
}

_BK0501_ROW: dict[str, Any] = {
    "board_code": "BK0501",
    "board_name": "次新股",
    "member_count": 2,
    "unresolved_count": 1,
    "priced_count": 1,
    "up_count": 1,
    "flat_count": 0,
    "down_count": 0,
    "avg_pct": 1.5,
}


def _snap_with(echelons: list[dict[str, Any]], *, limits_present: bool, reason: str | None) -> dict:
    return {
        "as_of": date(2026, 9, 18),
        "echelons": echelons,
        "limits_present": limits_present,
        "degraded_reason": reason,
    }


def _patch_detail_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snap: dict,
    members: list[tuple[str, str]],
    membership_as_of: date | None,
    board_meta: dict[str, Any] | None = _BK0501_ROW_META,
) -> None:
    async def _snapshot(cache: Any) -> dict:
        return snap

    monkeypatch.setattr(limit_up_service, "get_snapshot", _snapshot)
    monkeypatch.setattr(concept_repo, "find_board", lambda db, code: _const(board_meta))
    monkeypatch.setattr(concept_repo, "list_member_symbols", lambda db, code: _const(members))
    monkeypatch.setattr(
        concept_repo, "board_membership_date", lambda db, code: _const(membership_as_of)
    )
    monkeypatch.setattr(
        concept_repo,
        "aggregate_boards",
        lambda db, as_of, limit, offset: _const([dict(_BK0501_ROW)]),
    )
    monkeypatch.setattr(market_data_repo, "list_sector_moneyflow", lambda *a, **k: _const([]))
    monkeypatch.setattr(concept_repo, "board_leaders", lambda db, as_of, codes: _const({}))
    monkeypatch.setattr(limit_up_repo, "latest_quote_date", lambda db: _const(date(2026, 9, 18)))


async def test_board_detail_reuses_snapshot_echelons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """板内梯队 = `get_snapshot().echelons` 按成分过滤；streak 不得来自第二套计算。"""
    echelons = [
        {
            "streak": 2,
            "label": "2连板",
            "stocks": [
                {
                    "symbol": "600000",
                    "name": "X",
                    "streak": 2,
                    # Web 增强三件套：卡片要渲染，过滤必须原样带过去（M4）
                    "seal_time": "09:41:03",
                    "seal_fund": 1.23e8,
                    "break_count": 2,
                },
                {"symbol": "999999", "name": "非本板", "streak": 2},
            ],
        }
    ]
    _patch_detail_reads(
        monkeypatch,
        snap=_snap_with(echelons, limits_present=True, reason=None),
        members=[("600000", "X"), ("000001", "Z")],
        membership_as_of=date(2026, 9, 20),
    )

    out = await concept_service.get_board_detail(None, "BK0501", cache=None)

    assert out is not None
    assert [s["symbol"] for s in out["echelons"][0]["stocks"]] == ["600000"], "非成分不得进板内梯队"
    assert out["echelons"][0]["streak"] == 2, "梯队档位原样透传，不重算"
    assert out["kpis"] == {
        "zt_count": 1,
        "max_streak": 2,
        "leader_symbol": "600000",
        "leader_name": "X",
    }
    kept = out["echelons"][0]["stocks"][0]
    assert (kept["seal_time"], kept["seal_fund"], kept["break_count"]) == ("09:41:03", 1.23e8, 2), (
        "封板时间/封单/炸板次数是卡片渲染字段，过滤后必须逐键保留"
    )
    assert out["membership_as_of"] == "2026-09-20", "必须是本板的成分快照日"
    assert out["source"] == "em_clist"
    assert out["degraded_reason"] is None
    assert out["stock_count"] == 1 and out["unresolved_count"] == 1
    assert out["board"]["board_code"] == "BK0501"
    assert out["board"]["board_name"] == "次新股"
    assert out["board"]["member_count"] == 2 and out["board"]["avg_pct"] == 1.5
    assert out["board"]["main_net_inflow"] is None and out["board"]["leaders"] == []


def test_filter_echelons_keeps_snapshot_stock_objects() -> None:
    """纯函数过滤：只按 symbol 交集裁剪，stock 字典**原对象**透传（不重算任何字段）。"""
    keep = {"symbol": "600000", "name": "X", "streak": 2, "amount": 100.0, "seal_fund": 1.0}
    echelons = [
        {"streak": 3, "label": "3连板", "stocks": [{"symbol": "999999", "name": "非本板"}]},
        {"streak": 2, "label": "2连板", "stocks": [keep, {"symbol": "888888", "name": "非本板"}]},
    ]
    out = concept_service._filter_echelons(echelons, {"600000"})
    assert [e["streak"] for e in out] == [2], "空档整档丢弃（前端不接受空 stocks）"
    assert out[0]["stocks"][0] is keep, "必须是快照原始对象，杜绝第二套 streak 计算"
    assert concept_service._ladder_kpis(out)["max_streak"] == 2


def test_stock_streak_missing_is_none_not_zero() -> None:
    """`streak` 缺失 → `None`（不可判），绝不回退 `streak_upto`、绝不写成 0（M2 / 缺失 ≠ 0）。"""
    assert concept_service._stock_streak({"symbol": "X", "streak": 3}) == 3
    assert concept_service._stock_streak({"symbol": "X", "streak_upto": 3}) is None
    assert concept_service._stock_streak({"symbol": "X"}) is None


def test_ladder_kpis_leader_is_first_row_of_highest_echelon() -> None:
    """龙头 = 最高板那一档的第一只（= 卡片首行），不重排。

    快照档内顺序已经是 `amount DESC, symbol ASC`（`calc.ladder`），且**生产快照行不带
    `amount`**。这里刻意用生产形状（无 `amount`）+ 成交额决定的档内顺序：`000002`（900 万）
    排在 `000001`（100 万）前面。旧实现按 `(-streak, -amount, symbol)` 排序，`amount` 缺失时
    退化成 symbol 升序 → 选出 `000001`，与卡片首行分叉（I1 回归）。
    """
    echelon = {
        "streak": 2,
        "label": "2连板",
        "stocks": [
            {"symbol": "000002", "name": "B", "streak": 2},  # 快照里 amount 900 > 100，故排前
            {"symbol": "000001", "name": "A", "streak": 2},
        ],
    }
    kpis = concept_service._ladder_kpis([echelon])
    assert kpis["zt_count"] == 2
    assert kpis["max_streak"] == 2
    assert kpis["leader_symbol"] == "000002", "龙头必须是最高板档的第一行（金额更大的那只）"
    assert kpis["leader_name"] == "B"

    # 即使档内字典带上 amount，也不重排：顺序就是权威（新实现不看 amount）。
    kpis_amounts = concept_service._ladder_kpis(
        [
            {
                "streak": 2,
                "label": "2连板",
                "stocks": [
                    {"symbol": "000002", "name": "B", "streak": 2, "amount": 900.0},
                    {"symbol": "000001", "name": "A", "streak": 2, "amount": 100.0},
                ],
            }
        ]
    )
    assert kpis_amounts["leader_symbol"] == "000002"

    kpis_high = concept_service._ladder_kpis(
        [
            {
                "streak": 3,
                "label": "3连板",
                "stocks": [{"symbol": "000009", "name": "D", "streak": 3}],
            },
            {"streak": 2, "label": "2连板", "stocks": echelon["stocks"]},
        ]
    )
    assert kpis_high["max_streak"] == 3
    assert kpis_high["leader_symbol"] == "000009", "更高板优先"

    empty = concept_service._ladder_kpis([])
    assert empty == {"zt_count": 0, "max_streak": 0, "leader_symbol": None, "leader_name": None}


async def test_board_detail_degrades_when_board_has_no_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无成分行 → `no_members`（板在但空），且不得聚合（空集合聚合没有语义）。"""
    _patch_detail_reads(
        monkeypatch,
        snap=_snap_with([], limits_present=True, reason=None),
        members=[],
        membership_as_of=None,
    )

    def _no_aggregate(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("无成分板不得聚合")

    monkeypatch.setattr(concept_repo, "aggregate_boards", _no_aggregate)

    out = await concept_service.get_board_detail(None, "BK0501", cache=None)

    assert out is not None
    assert out["degraded_reason"] == "no_members"
    assert out["membership_as_of"] is None
    assert out["stock_count"] == 0 and out["unresolved_count"] == 0
    assert out["board"]["member_count"] == 0 and out["board"]["board_name"] == "次新股"
    assert out["echelons"] == []

    # 未知板块码 → None（端点据此 404），不能与"空成分板"混为一谈
    monkeypatch.setattr(concept_repo, "find_board", lambda db, code: _const(None))
    assert await concept_service.get_board_detail(None, "NOPE", cache=None) is None


@pytest.mark.parametrize(
    "snapshot_reason",
    ["no_quotes", "price_limits_missing", "partial_day", "insufficient_trade_days"],
)
async def test_board_detail_propagates_snapshot_degraded_reason(
    monkeypatch: pytest.MonkeyPatch, snapshot_reason: str
) -> None:
    """快照的退化词**原样透传**，不得二次推导（尤其 `no_quotes` 不得被写成限价缺失，I2）。

    这也覆盖了 `no_quotes` 详情态：无行情时板内梯队为空、KPI 全 0，退化原因必须来自快照自身。
    """
    _patch_detail_reads(
        monkeypatch,
        snap=_snap_with([], limits_present=False, reason=snapshot_reason),
        members=[("600000", "X")],
        membership_as_of=date(2026, 9, 20),
    )
    out = await concept_service.get_board_detail(None, "BK0501", cache=None)

    assert out is not None
    assert out["degraded_reason"] == snapshot_reason, "必须与快照逐字一致"
    # schema 闭集注释声明的词都得能过 response_model
    assert ConceptDetailOut(**out).degraded_reason == snapshot_reason


async def test_board_detail_no_limit_up_rows_is_not_a_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`no_limit_up_rows` **刻意不透传**：市场级"今天没有涨停"是合法空态（KPI 诚实报 0）。"""
    _patch_detail_reads(
        monkeypatch,
        snap=_snap_with([], limits_present=True, reason="no_limit_up_rows"),
        members=[("600000", "X")],
        membership_as_of=date(2026, 9, 20),
    )
    out = await concept_service.get_board_detail(None, "BK0501", cache=None)

    assert out is not None and out["degraded_reason"] is None, "空梯队不是概念数据的退化"
    assert out["kpis"] == {
        "zt_count": 0,
        "max_streak": 0,
        "leader_symbol": None,
        "leader_name": None,
    }


async def test_board_detail_no_members_beats_snapshot_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """板内无成分行 → `no_members` 优先于快照自身的退化词（本端点自己的一级空态）。"""
    _patch_detail_reads(
        monkeypatch,
        snap=_snap_with([], limits_present=False, reason="no_quotes"),
        members=[],
        membership_as_of=None,
    )
    out = await concept_service.get_board_detail(None, "BK0501", cache=None)

    assert out is not None and out["degraded_reason"] == "no_members"


def _enriched(symbol: str, change: float | None) -> StockEnrichedOut:
    return StockEnrichedOut(
        id=int(symbol),
        exchange="SSE",
        symbol=symbol,
        name=f"name-{symbol}",
        category="A股",
        asof=datetime(2026, 9, 18),
        change_percent=change,
    )


async def test_board_stocks_reuses_enriched_shape_and_orders_by_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """形状 = `StockEnrichedOut`（前端才能零改映射复用 `StockTable`）；排序确定。"""
    captured: list[list[str]] = []

    async def _fake_enriched(db: Any, symbols: list[str]) -> list[StockEnrichedOut]:
        captured.append(list(symbols))
        return [
            _enriched("600000", None),
            _enriched("000001", 5.0),
            _enriched("000002", 5.0),
            _enriched("000003", 9.9),
        ]

    monkeypatch.setattr(
        concept_repo, "list_member_symbols", lambda db, code: _const([("600000", "X")])
    )
    monkeypatch.setattr(
        concept_service.market_service, "get_stocks_enriched_by_symbols", _fake_enriched
    )

    rows = await concept_service.get_board_stocks(None, "BK0501")

    assert captured == [["600000"]], "必须只查该板成分股"
    assert {"symbol", "name", "exchange", "category", "asof"} <= set(rows[0])
    assert [r["symbol"] for r in rows] == ["000003", "000001", "000002", "600000"]
    assert rows[-1]["change_percent"] is None, "缺行情排最后（缺失 ≠ 0）"


class _RecordingCache:
    def __init__(self, preloaded: dict[str, Any] | None = None) -> None:
        self.preloaded = preloaded or {}
        self.written: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> Any:
        return self.preloaded.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.written.append((key, ttl))


async def test_concepts_by_symbol_orders_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """板码过滤走 `aggregate_boards_for_codes`（单一 SQL 的可选过滤），排序确定，缓存 300s。

    反向查必须在 SQL 里收窄（I3）：这里断言仓库层**收到了**该 symbol 的板码，而不是拿全库
    聚合结果在 Python 侧过滤——后者会让板块在窗口/NULLS LAST 边界处静默消失。
    """
    filtered: list[list[str]] = []

    async def _fake_agg_for_codes(
        db: Any, as_of: date, board_codes: list[str]
    ) -> list[dict[str, Any]]:
        filtered.append(list(board_codes))
        return [
            {"board_code": "BK0001", "board_name": "一", "avg_pct": 1.0},
            {"board_code": "BK0002", "board_name": "二", "avg_pct": None},
            {"board_code": "BK0003", "board_name": "三", "avg_pct": 3.0},
        ]

    monkeypatch.setattr(
        concept_repo,
        "list_symbol_board_codes",
        lambda db, symbol: _const(["BK0002", "BK0001", "BK0003"]),
    )
    monkeypatch.setattr(concept_repo, "aggregate_boards_for_codes", _fake_agg_for_codes)
    monkeypatch.setattr(
        concept_repo,
        "aggregate_boards",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("by-symbol 不得全库聚合后在 Python 侧过滤")
        ),
    )
    monkeypatch.setattr(limit_up_repo, "latest_quote_date", lambda db: _const(date(2026, 9, 18)))
    monkeypatch.setattr(
        concept_repo, "latest_membership_date", lambda db: _const(date(2026, 9, 20))
    )

    cache = _RecordingCache()
    body = await concept_service.get_concepts_by_symbol(None, "601091", cache)

    assert filtered == [["BK0002", "BK0001", "BK0003"]], "板码必须进 SQL（只发一次收窄查询）"
    assert [i["board_code"] for i in body["items"]] == ["BK0003", "BK0001", "BK0002"]
    assert body["items"][-1]["pct_change"] is None
    assert body["as_of"] == "2026-09-18" and body["membership_as_of"] == "2026-09-20"
    assert cache.written == [("market:concept:by-symbol:601091", 300)]

    # 缓存命中 → 直接返回，不再触库
    def _no_codes(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("缓存命中不得再查库")

    monkeypatch.setattr(concept_repo, "list_symbol_board_codes", _no_codes)
    hit = await concept_service.get_concepts_by_symbol(
        None, "601091", _RecordingCache({"market:concept:by-symbol:601091": body})
    )
    assert hit == body

    # 未知 symbol → 空 items（不是 404），且不写缓存
    monkeypatch.setattr(concept_repo, "list_symbol_board_codes", lambda db, symbol: _const([]))
    empty_cache = _RecordingCache()
    unknown = await concept_service.get_concepts_by_symbol(None, "999999999", empty_cache)
    assert unknown["items"] == []
    assert empty_cache.written == []


# ---------------------------------------------------------------------------
# `-m e2e`：T8 详情 / T9 成分与反查（真库 + FastAPI app，只读）
# ---------------------------------------------------------------------------

DETAIL_KEYS = {
    "as_of",
    "membership_as_of",
    "source",
    "degraded_reason",
    "board",
    "kpis",
    "echelons",
    "unresolved_count",
    "stock_count",
}
KPI_KEYS = {"zt_count", "max_streak", "leader_symbol", "leader_name"}
BY_SYMBOL_KEYS = {"as_of", "membership_as_of", "items"}
# 前端 `mapBackendStockEnriched` 会读取的核心键
# （形状必须与既有 /sw-industry/.../stocks/enriched 逐键一致）
ENRICHED_KEYS = {
    "id",
    "symbol",
    "name",
    "exchange",
    "category",
    "asof",
    "latest_price",
    "prev_close",
    "change",
    "change_percent",
    "turnover_rate",
    "circ_mv",
    "amount",
    "latest_quote_date",
    "list_date",
    "sw_chain",
}


async def _member_symbols(board_code: str) -> set[str]:
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text("SELECT symbol FROM concept_members WHERE board_code = :code"),
                {"code": board_code},
            )
        ).scalars()
        return {str(s) for s in rows}


async def _symbol_board_codes(symbol: str) -> set[str]:
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text("SELECT board_code FROM concept_members WHERE symbol = :symbol"),
                {"symbol": symbol},
            )
        ).scalars()
        return {str(c) for c in rows}


async def _active_symbol_board_codes(symbol: str) -> set[str]:
    """该 symbol 的**启用**板块码：聚合 SQL 的 `is_active` 过滤决定的可导航集合。

    与 `_symbol_board_codes` 的差集恰是"已停止采集的板块"——`by-symbol` 不允许返回它们的链接，
    但**也不允许**把启用板块悄悄漏掉（I3 的两种失败模式各钉一头）。
    """
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT cm.board_code FROM concept_members cm "
                    "JOIN concept_boards b ON b.board_code = cm.board_code "
                    "WHERE cm.symbol = :symbol AND b.is_active"
                ),
                {"symbol": symbol},
            )
        ).scalars()
        return {str(c) for c in rows}


@pytest.mark.e2e
async def test_concept_board_detail_contract_matches_dev_db() -> None:
    """信封键集 + 与库内对拍（成分行数/未解析数/本板成分日）+ 梯队必须被成分过滤。"""
    board_code = "BK0501"
    as_of = await _db_scalar("SELECT max(trade_date) FROM daily_quotes")
    membership_as_of = await _db_scalar(
        "SELECT max(last_seen_on) FROM concept_members WHERE board_code = :code",
        {"code": board_code},
    )
    member_rows = await _db_scalar(
        "SELECT count(*) FROM concept_members WHERE board_code = :code", {"code": board_code}
    )
    unresolved = await _db_scalar(
        "SELECT count(*) FROM concept_members WHERE board_code = :code AND stock_id IS NULL",
        {"code": board_code},
    )
    assert as_of is not None and member_rows > 0, "dev DB 缺行情/BK0501 成分"
    members = await _member_symbols(board_code)

    async with _client() as client:
        resp = await client.get(f"/api/v1/concepts/{board_code}")
        again = await client.get(f"/api/v1/concepts/{board_code}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == DETAIL_KEYS
    assert body["as_of"] == as_of.isoformat()
    assert body["membership_as_of"] == membership_as_of.isoformat(), "必须是本板的成分快照日"
    assert body["source"] == "em_clist"
    assert set(body["kpis"]) == KPI_KEYS
    assert body["board"]["board_code"] == board_code
    assert body["board"]["member_count"] == member_rows
    assert body["stock_count"] + body["unresolved_count"] == member_rows, (
        "resolved + unresolved 必须等于成分行数（同一口径）"
    )
    assert body["unresolved_count"] == unresolved
    # 降级词表 = 本端点的闭集（快照原样透传 + no_members 优先；no_limit_up_rows 刻意不在集内）
    assert body["degraded_reason"] in (
        None,
        "no_members",
        "no_quotes",
        "price_limits_missing",
        "partial_day",
        "insufficient_trade_days",
    )

    ladder = [s for echelon in body["echelons"] for s in echelon["stocks"]]
    assert all(s["symbol"] in members for s in ladder), "板内梯队只能含本板成分"
    assert all({"seal_time", "seal_fund", "break_count"} <= set(s) for s in ladder), (
        "Web 增强三件套必须随梯队下发（本地路径可为 null，但键不能丢）"
    )
    assert [e["streak"] for e in body["echelons"]] == sorted(
        (e["streak"] for e in body["echelons"]), reverse=True
    ), "梯队档位保持连板数降序（快照原序）"
    assert body["kpis"]["zt_count"] == len(ladder), "板内涨停家数 = 过滤后梯队行数"
    assert body["kpis"]["max_streak"] == max((s["streak"] for s in ladder), default=0)
    if ladder:
        assert body["kpis"]["leader_symbol"] in {s["symbol"] for s in ladder}
    else:
        assert body["kpis"]["leader_symbol"] is None
    assert again.json() == body, "同一请求两次必须逐字段一致（确定性）"

    # board 行与聚合 SQL 同源（avg_pct 不重算）
    async with async_session_factory() as db:
        agg = await concept_repo.aggregate_boards(db, as_of, 1000, 0)
    row = next(r for r in agg if r["board_code"] == board_code)
    assert body["board"]["avg_pct"] == pytest.approx(row["avg_pct"])
    assert body["board"]["up_count"] == row["up_count"]
    assert body["board"]["down_count"] == row["down_count"]


@pytest.mark.e2e
async def test_concept_board_detail_unknown_code_404() -> None:
    async with _client() as client:
        resp = await client.get("/api/v1/concepts/BKNOSUCH")
    assert resp.status_code == 404, "未知板块码必须 404，不能与空成分板混淆"
    assert "BKNOSUCH" in resp.json()["detail"]


@pytest.mark.e2e
async def test_concept_board_stocks_returns_ordered_enriched_array() -> None:
    """纯数组（无 envelope）+ `StockEnrichedOut` 形状 + `change_percent DESC NULLS LAST` 顺序。"""
    members = await _member_symbols("BK0501")
    async with _client() as client:
        resp = await client.get("/api/v1/concepts/BK0501/stocks")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert isinstance(rows, list) and rows, "BK0501 成分股非空"
    for row in rows:
        assert ENRICHED_KEYS <= set(row), "形状必须能直接交给 mapBackendStockEnriched"
        assert row["symbol"] in members

    keys = [(r["change_percent"] is None, -(r["change_percent"] or 0.0), r["symbol"]) for r in rows]
    assert keys == sorted(keys), "change_percent DESC NULLS LAST, symbol ASC"

    async with _client() as client:
        unknown = await client.get("/api/v1/concepts/BKNOSUCH/stocks")
    assert unknown.status_code == 200 and unknown.json() == []


@pytest.mark.e2e
async def test_concepts_by_symbol_returns_boards_and_empty_for_unknown() -> None:
    """真实成分（601091 沈鼓）→ 恰为该 symbol 的**启用**板块；未知 symbol → 空 items（不是 404）。

    板码进了 SQL 之后，"返回集合"由库内数据唯一决定：既不得多出非成员板块，也不得因窗口/排序
    边界漏掉任何启用板块——故这里钉**相等**（≠ 只钉子集，子集会让静默消失继续通过）。
    """
    codes = await _symbol_board_codes("601091")
    active_codes = await _active_symbol_board_codes("601091")
    assert codes, "dev DB 里 601091 应属于若干概念板块"

    async with _client() as client:
        resp = await client.get("/api/v1/concepts/by-symbol/601091")
        unknown = await client.get("/api/v1/concepts/by-symbol/999999999")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == BY_SYMBOL_KEYS
    assert body["items"], "至少一个板块"
    returned = set()
    for item in body["items"]:
        assert set(item) == {"board_code", "board_name", "pct_change"}
        returned.add(item["board_code"])
    assert returned <= codes, "返回的板块必须是该 symbol 的 concept_members 行（⊆ 反查）"
    assert returned == active_codes, (
        "返回集合必须等于该 symbol 的成分板块 ∩ 启用板（停用板不给链接，启用板一个都不能漏）"
    )
    keys = [
        (i["pct_change"] is None, -(i["pct_change"] or 0.0), i["board_code"]) for i in body["items"]
    ]
    assert keys == sorted(keys), "pct_change DESC NULLS LAST, board_code ASC"

    assert unknown.status_code == 200, "未知 symbol 不是 404"
    assert unknown.json()["items"] == []
