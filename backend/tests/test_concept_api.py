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
from datetime import date
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from app.core.database import async_session_factory, engine
from app.core.redis import CacheClient, close_redis_pool, get_redis_pool
from app.main import app
from app.repositories import market_data_repo
from app.services import concept_service

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
        await CacheClient(await get_redis_pool()).delete_pattern("market:concept:list:*")
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
