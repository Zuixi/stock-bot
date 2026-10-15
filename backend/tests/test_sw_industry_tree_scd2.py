"""T19 — 申万行业树（``/market/sw-industry/tree``）的官方成员源 = SCD2 ``is_current``。

为什么要锁（controller 实测的缺陷）：同一个 UI 卡片渲染的两个端点对"同一事实"不一致——

* ``GET /market/sw-industry/tree`` 走 ``sw_industry_members``（XLS 快照，整表替换、会
  陈旧）并按"其下 L3 的并集"上卷：L1 合计 5587、29/31 个行业计数与 drill 不同；
* ``GET /market/sw-industry/drill/levels/*`` 走 ``sw_industry_member_periods``
  （``is_current``，自身层级）：L1 合计 5211。

本文件把 S1.4 的判据（"SCD2 归属是唯一真相"）机械化到树上：

1. 每个节点的 ``officialCount`` 是**自身层级**的 SCD2 成员数，**不是**其下 L3 的并集
   （上游存在直接挂在 L1/L2 而没落在任何 L3 的成员，上卷会偏少）；
2. ``stockCount = officialCount + customCount`` 恒成立，自定义标签只贡献
   **增量**（不重复计入 ``officialCount``，也不再把官方成员数抬成"官方+自定义"）；
3. 读路只走 ``SwIndustryMemberPeriod``，缓存键带版本号，且失效端删的是**同一个**键
   ——旧的 ``market:sw-tree`` payload 不可能被当成新口径返回。
"""

from __future__ import annotations

import inspect
import re
from typing import Any

from app.api.v1 import stocks as stocks_api
from app.services import market_service

# ---------------------------------------------------------------------------
# 纯函数夹具：一棵"上游直挂 L1/L2"的最小树
# ---------------------------------------------------------------------------

_CLASSES: list[dict[str, Any]] = [
    {"industry_code": "801000", "industry_name": "农林牧渔", "level": 1, "parent_code": None},
    {"industry_code": "801010", "industry_name": "种植业", "level": 2, "parent_code": "801000"},
    {"industry_code": "801011", "industry_name": "粮食种植", "level": 3, "parent_code": "801010"},
    {"industry_code": "801012", "industry_name": "其他种植", "level": 3, "parent_code": "801010"},
    {"industry_code": "801020", "industry_name": "养殖业", "level": 2, "parent_code": "801000"},
    {"industry_code": "801021", "industry_name": "生猪养殖", "level": 3, "parent_code": "801020"},
    {"industry_code": "802000", "industry_name": "基础化工", "level": 1, "parent_code": None},
    {"industry_code": "802010", "industry_name": "化学原料", "level": 2, "parent_code": "802000"},
    {"industry_code": "802011", "industry_name": "纯碱", "level": 3, "parent_code": "802010"},
]

#: 官方成员按**自身层级**给出：801000 有 {A,B,C,D}（C/D 直挂 L1、不在任何 L3），
#: 801020 有 {D}，801021 有 {E}——故 801000 的自身层级数（4）≠ 其下成员并集（{A,B,E}=3）。
_OFFICIAL: dict[str, set[str]] = {
    "801000": {"A", "B", "C", "D"},
    "801010": {"A", "B"},
    "801011": {"A"},
    "801012": {"B"},
    "801020": {"D"},
    "801021": {"E"},
    "802000": {"F"},
    "802010": {"F"},
    "802011": {"F"},
}
_CUSTOM_L2: dict[str, set[str]] = {"801020": {"X"}}  # X 只被用户标签归到 L2
_CUSTOM_L3: dict[str, set[str]] = {"801011": {"A", "Y"}}  # A 与官方重叠；Y 是净增量
_UNCATEGORIZED: list[tuple[str, str | None]] = [("Z", "未知行业"), ("W", "其他传媒")]


def _assemble() -> list[dict[str, Any]]:
    return market_service.assemble_sw_industry_tree(
        classes=_CLASSES,
        official_symbols=_OFFICIAL,
        custom_l2_symbols=_CUSTOM_L2,
        custom_l3_symbols=_CUSTOM_L3,
        uncategorized_rows=_UNCATEGORIZED,
    )


def _flatten(tree: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for l1 in tree:
        out[l1["code"]] = l1
        for l2 in l1["children"]:
            out[l2["code"]] = l2
            for l3 in l2["children"]:
                out[l3["code"]] = l3
    return out


# ---------------------------------------------------------------------------
# 1) 官方成员取"自身层级"，不上卷
# ---------------------------------------------------------------------------


def test_official_count_is_own_level_not_the_l3_union() -> None:
    """L1/L2 的 ``officialCount`` = 该 ``industry_code`` 自身的 SCD2 成员数。

    801000 自身 4 只，其下 L3+L2 的官方并集只有 {A,B,E}=3 只：若回归到"上卷"实现，
    这里会变成 3（实测上游 24/31 个一级行业存在这种直挂成员，合计偏少 261 只）。
    """
    nodes = _flatten(_assemble())
    assert nodes["801000"]["officialCount"] == 4
    assert nodes["801010"]["officialCount"] == 2
    # 801020 自身 1 只（D）；其下 L3（801021=E）的官方成员**不**上卷
    assert nodes["801020"]["officialCount"] == 1
    assert nodes["801011"]["officialCount"] == 1
    assert nodes["801012"]["officialCount"] == 1
    assert nodes["801021"]["officialCount"] == 1
    assert nodes["802011"]["officialCount"] == 1


def test_custom_tags_contribute_an_increment_only() -> None:
    """自定义标签进 ``customCount``，且只算增量（与官方重叠的不重复计数）。"""
    nodes = _flatten(_assemble())
    # 801011：官方 {A}，自定义 {A,Y} → 增量 {Y}
    assert nodes["801011"]["customCount"] == 1
    assert nodes["801011"]["symbols"] == ["A", "Y"]
    # 801020：官方 {D}，自定义 {X} → 增量 {X}
    assert nodes["801020"]["customCount"] == 1
    # 801010 子树：自定义 {A,Y}，官方 {A,B} → 增量 {Y}
    assert nodes["801010"]["customCount"] == 1
    # 801000 子树：自定义 {A,Y} ∪ {X}，官方 {A,B,C,D} → 增量 {X,Y}
    assert nodes["801000"]["customCount"] == 2
    # 无自定义标签的树枝不凭空产生计数
    assert nodes["802000"]["customCount"] == 0


def test_shape_and_total_invariant_hold_for_every_node() -> None:
    """键/形状对既有消费端不变，且 ``stockCount = officialCount + customCount`` 处处成立。"""
    tree = _assemble()
    nodes = _flatten(tree)
    for code, node in nodes.items():
        assert {"code", "name", "officialCount", "customCount", "stockCount"} <= set(node), code
        assert node["stockCount"] == node["officialCount"] + node["customCount"], code
    assert nodes["801000"]["stockCount"] == 6
    assert nodes["801010"]["children"][0]["code"] == "801011"
    assert nodes["801010"]["children"][1]["code"] == "801012"
    # L2/L1 输出不带 parent_code（保持既有形状）
    assert "parent_code" not in nodes["801010"]
    assert "parent_code" not in nodes["801011"]
    # 层级数不变：2 个 L1 + 3 个 L2 + 4 个 L3
    assert len(tree) == 3  # 2 个真实 L1 + 合成 OTHER
    assert len([n for n in tree if n["code"] != "OTHER"]) == 2


def test_other_bucket_has_no_scd2_counterpart_and_keeps_the_invariant() -> None:
    """合成 ``OTHER`` 桶：``officialCount = 0``，残差记在 ``customCount``（显式声明）。"""
    tree = _assemble()
    other = next(node for node in tree if node["code"] == "OTHER")
    assert other["name"] == "其他"
    assert other["officialCount"] == 0
    assert other["stockCount"] == 2
    assert other["customCount"] == other["stockCount"]
    assert other["stockCount"] == other["officialCount"] + other["customCount"]
    child_names = {child["name"] for child in other["children"]}
    assert child_names == {"未知行业", "其他传媒"}


# ---------------------------------------------------------------------------
# 2) 读路只走 SCD2 + 缓存键版本化（静态锁，防回归）
# ---------------------------------------------------------------------------


def test_tree_read_path_uses_scd2_periods_only() -> None:
    """官方成员只走 ``SwIndustryMemberPeriod``（``is_current``），不得再碰旧快照表。"""
    src = inspect.getsource(market_service.get_sw_industry_tree)
    assert "SwIndustryMemberPeriod" in src
    assert ".is_current.is_(True)" in src
    # 旧 ORM 类 ``SwIndustryMember``（快照表）在树读路里必须绝迹；注意不能误伤
    # ``SwIndustryMemberPeriod``，故用负向前瞻。
    assert re.search(r"SwIndustryMember(?!Period)", src) is None, src


def test_tree_cache_key_is_versioned_and_invalidated_by_the_same_constant() -> None:
    """树缓存键带版本号（旧 payload 不可能冒充新口径），失效端删的是同一个键。"""
    assert market_service.SW_TREE_CACHE_KEY.startswith("market:sw-tree:")
    assert market_service.SW_TREE_CACHE_KEY != "market:sw-tree"
    src = inspect.getsource(market_service.get_sw_industry_tree)
    assert '"market:sw-tree"' not in src
    tag_src = inspect.getsource(stocks_api.set_sw_tags)
    assert "market_service.SW_TREE_CACHE_KEY" in tag_src
    assert '"market:sw-tree"' not in tag_src


# ---------------------------------------------------------------------------
# 3) 缓存：v1 的旧 payload 不可能被当成新口径返回
# ---------------------------------------------------------------------------

_SENTINEL_TREE: list[dict[str, Any]] = [
    {"code": "110000", "name": "旧 payload", "stockCount": 9999, "children": []}
]


class _StubCache:
    """记录键名的假缓存（不碰真 Redis：共享实例上跑着被验证的栈）。"""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.reads: list[str] = []
        self.writes: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> Any | None:
        self.reads.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.writes.append((key, ttl))
        self.store[key] = value


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def __iter__(self) -> Any:
        return iter(self._rows)


class _FakeSession:
    """按 ``execute`` 调用顺序吐出预置结果（顺序 = get_sw_industry_tree 的 5 条查询）。"""

    def __init__(self, results: list[list[Any]]) -> None:
        self._results = list(results)

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._results.pop(0))

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


async def test_tree_ignores_the_pre_v2_key_and_writes_the_versioned_one(
    monkeypatch: Any,
) -> None:
    """``market:sw-tree``（v1）里的旧 payload 不会被当作新口径返回。"""
    cache = _StubCache()
    cache.store["market:sw-tree"] = _SENTINEL_TREE
    l1_row = {
        "industry_code": "110000",
        "industry_name": "农林牧渔",
        "level": 1,
        "parent_code": None,
    }
    monkeypatch.setattr(
        market_service,
        "async_session_factory",
        lambda: _FakeSession([[l1_row], [], [], [], []]),
    )

    tree = await market_service.get_sw_industry_tree(cache=cache)  # type: ignore[arg-type]

    assert tree != _SENTINEL_TREE
    assert tree[0]["name"] == "农林牧渔"
    assert tree[0]["officialCount"] == 0
    assert cache.writes == [(market_service.SW_TREE_CACHE_KEY, market_service._MARKET_CACHE_TTL)]
    assert cache.reads == [market_service.SW_TREE_CACHE_KEY]

    # 缓存命中：不得再打库（DB 工厂一被调用就炸）
    def _boom() -> Any:
        raise AssertionError("cache hit must not touch the DB")

    monkeypatch.setattr(market_service, "async_session_factory", _boom)
    assert await market_service.get_sw_industry_tree(cache=cache) is tree  # type: ignore[arg-type]
