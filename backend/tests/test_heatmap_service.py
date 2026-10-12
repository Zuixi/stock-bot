"""``heatmap_service``（S1.5 批量读路）契约测试。

离线：``_fetch_rows`` / ``_fetch_concept_boards`` 是 SQL seam，缓存用 JSON 边界
替身，全程不碰库、不打网。锁四件事：

1. SQL 形状：当日优先 + ``< :day`` 回看（不是 ``<=`` 双算）、``pct_chg IS NOT NULL``
   （无涨跌行不进画布）、分组码随行返回；
2. 行规范化：Decimal → float、``NULL`` 保持 ``None``（不造 0）；
3. 缓存边界：payload 必须 JSON 安全；缺 quality 的旧 payload 不谎报 complete；
4. 分组树纯函数：四组行为 + 未分类 + 空入空出。
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.services import heatmap_service as hs

DAY = date(2026, 10, 9)


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "stock_id": 1,
        "symbol": "600000",
        "name": "浦发银行",
        "pct_chg": Decimal("-1.6495"),
        "amount": Decimal("915309.83"),
        "total_mv": Decimal("31773769.74"),
        "sw_l1_code": "480000",
        "concept_code": "BK0500",
    }
    row.update(overrides)
    return row


class _FakeResult:
    def __init__(self, mappings: list[dict[str, Any]]) -> None:
        self._mappings = mappings

    def mappings(self) -> list[dict[str, Any]]:
        return self._mappings


class _BoomSession:
    """任何 SQL 执行都是测试失败（证明被测路径走了 seam）。"""

    async def execute(self, *_a: Any, **_kw: Any) -> Any:
        raise AssertionError("endpoint must not run its own SQL (seam replaced it)")


class _FakeCache:
    """Redis 的 JSON 边界替身：写入必须能 json.dumps（Decimal 当场 TypeError）。"""

    def __init__(self, payload: Any = None) -> None:
        self._raw = None if payload is None else json.dumps(payload, ensure_ascii=False)
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        return None if self._raw is None else json.loads(self._raw)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._raw = json.dumps(value, ensure_ascii=False)
        self.sets.append((key, value, ttl))


# ---------------------------------------------------------------------------
# SQL 形状
# ---------------------------------------------------------------------------


def test_sql_shape_same_day_join_plus_lt_lookback() -> None:
    """市值 = 当日等值 join + ``< :day`` 回看（COALESCE）；两路都查不到才 None。"""
    sql = str(hs._TREEMAP_SQL)
    assert "b.trade_date = :day" in sql, "当日 daily_basic 必须是等值 join"
    assert "bb.trade_date < :day" in sql, "回看必须严格小于当日（当日行已由等值 join 覆盖）"
    assert "COALESCE(b.total_mv, lb.total_mv)" in sql
    # ``<= :day`` 会把回看与当日 join 双算（plan 上多一次无谓索引扫描）
    assert "trade_date <= :day" not in sql


def test_sql_filters_no_pct_chg_rows() -> None:
    """无涨跌行（停牌/缺数）不进画布——放进颜色维度等于造 0%。"""
    sql = str(hs._TREEMAP_SQL)
    assert "pct_chg IS NOT NULL" in sql


def test_sql_returns_group_codes_with_rows() -> None:
    """分组码（sw_l1 / concept）与行情同一条 SQL 回齐，不是二次往返。"""
    sql = str(hs._TREEMAP_SQL)
    assert "AS sw_l1_code" in sql and "AS concept_code" in sql
    assert "sw_industry_members" in sql and "concept_members" in sql


# ---------------------------------------------------------------------------
# 行规范化
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_treemap_normalizes_and_keeps_nulls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decimal → float；缺市值/缺分组码保持 None（不造 0、不猜名）。"""
    rows = [
        _row(),
        _row(
            stock_id=2,
            symbol="000002",
            name="万科A",
            total_mv=None,
            sw_l1_code=None,
            concept_code=None,
        ),
    ]

    async def _fake(db: Any, day: date) -> list[dict[str, Any]]:
        assert db is not None and day == DAY
        return rows

    async def _resolve(db: Any, *, cache: Any = None):  # type: ignore[no-untyped-def]
        from app.services.market_day_service import MarketDay

        return MarketDay(
            day=DAY,
            quality="complete",
            reason=None,
            rows=2,
            universe=5587,
            pct_chg_ratio=1.0,
            limits_present=True,
        )

    monkeypatch.setattr(hs, "_fetch_rows", _fake)
    monkeypatch.setattr(hs.market_day_service, "resolve_latest_complete_day", _resolve)

    out = await hs.get_treemap(_BoomSession(), None)
    assert out.as_of == DAY and out.as_of_quality == "complete"
    assert out.count == 2
    assert out.items[0].pct_chg == pytest.approx(-1.6495)
    assert out.items[0].amount == pytest.approx(915309.83)
    assert out.items[1].total_mv is None
    assert out.items[1].sw_l1_code is None
    assert out.amount_unit == "千元" and out.mv_unit == "万元"


@pytest.mark.asyncio
async def test_get_treemap_empty_db_degrades_to_empty_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空库（判据日 None）回空信封：不 500、不缓存、as_of=None。"""

    async def _none(db: Any, *, cache: Any = None):
        return None

    monkeypatch.setattr(hs.market_day_service, "resolve_latest_complete_day", _none)
    cache = _FakeCache()
    out = await hs.get_treemap(_BoomSession(), cache)  # type: ignore[arg-type]
    assert out.as_of is None and out.count == 0 and out.items == []
    assert cache.sets == [], "空结果不得写缓存"


# ---------------------------------------------------------------------------
# 缓存边界
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_roundtrip_is_json_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """写缓存的 payload 必须过 ``json.dumps``（Decimal 在替身里当场炸）。"""
    rows = [_row()]

    async def _fake(db: Any, day: date) -> list[dict[str, Any]]:
        return rows

    async def _resolve(db: Any, *, cache: Any = None):  # type: ignore[no-untyped-def]
        from app.services.market_day_service import MarketDay

        return MarketDay(
            day=DAY,
            quality="complete",
            reason=None,
            rows=1,
            universe=5587,
            pct_chg_ratio=1.0,
            limits_present=True,
        )

    monkeypatch.setattr(hs, "_fetch_rows", _fake)
    monkeypatch.setattr(hs.market_day_service, "resolve_latest_complete_day", _resolve)

    cache = _FakeCache()
    first = await hs.get_treemap(_BoomSession(), cache)  # type: ignore[arg-type]
    assert len(cache.sets) == 1
    assert cache.sets[0][0] == f"market:heatmap:treemap:{DAY.isoformat()}"
    assert cache.sets[0][2] == hs._CACHE_TTL
    # 第二次走缓存：数值是 float（JSON 边界后不再有 Decimal）
    second = await hs.get_treemap(_BoomSession(), cache)  # type: ignore[arg-type]
    assert second.count == first.count
    assert isinstance(second.items[0].pct_chg, float)


@pytest.mark.asyncio
async def test_legacy_cache_payload_without_quality_not_reported_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧部署的缓存 payload 没有 ``as_of_quality``：默认值不得谎报 complete。"""
    legacy = {
        "as_of": DAY.isoformat(),
        "count": 1,
        "amount_unit": "千元",
        "mv_unit": "万元",
        "items": [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "pct_chg": -1.65,
                "amount": 915309.83,
                "total_mv": 31773769.74,
                "sw_l1_code": "480000",
                "concept_code": "BK0500",
            }
        ],
    }

    async def _resolve(db: Any, *, cache: Any = None):  # type: ignore[no-untyped-def]
        from app.services.market_day_service import MarketDay

        return MarketDay(
            day=DAY,
            quality="complete",
            reason=None,
            rows=1,
            universe=5587,
            pct_chg_ratio=1.0,
            limits_present=True,
        )

    monkeypatch.setattr(hs.market_day_service, "resolve_latest_complete_day", _resolve)
    out = await hs.get_treemap(_BoomSession(), _FakeCache(legacy))  # type: ignore[arg-type]
    assert out.as_of_quality == "partial", "缺证据时只能保守标注"


@pytest.mark.asyncio
async def test_foreign_cache_shape_is_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存里不是 dict 或 items 不是 list：按 miss 走 SQL seam，不炸。"""

    async def _fake(db: Any, day: date) -> list[dict[str, Any]]:
        return [_row()]

    async def _resolve(db: Any, *, cache: Any = None):  # type: ignore[no-untyped-def]
        from app.services.market_day_service import MarketDay

        return MarketDay(
            day=DAY,
            quality="partial",
            reason=None,
            rows=1,
            universe=5587,
            pct_chg_ratio=1.0,
            limits_present=True,
        )

    monkeypatch.setattr(hs, "_fetch_rows", _fake)
    monkeypatch.setattr(hs.market_day_service, "resolve_latest_complete_day", _resolve)
    out = await hs.get_treemap(_BoomSession(), _FakeCache([1, 2, 3]))  # type: ignore[arg-type]
    assert out.count == 1


# ---------------------------------------------------------------------------
# 概念板块名录 / 申万 L1 名录
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concept_boards_shape_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存回读缺键/非 dict 的元素被剔除，不炸、不回半截形状。"""
    rows = [{"board_code": "BK0500", "board_name": "军民融合"}]

    async def _fake(db: Any) -> list[dict[str, Any]]:
        return rows

    monkeypatch.setattr(hs, "_fetch_concept_boards", _fake)
    cache = _FakeCache([{"boardCode": "BK0425", "name": "工程建设"}, {"broken": True}, "junk"])
    out = await hs.list_concept_boards(_BoomSession(), cache)  # type: ignore[arg-type]
    assert out == [{"boardCode": "BK0425", "name": "工程建设"}]


def test_sql_day_key_carry_the_day() -> None:
    """SQL 用具名 ``:day`` 参数（判据日由 market_day_service 决定，不内嵌 today()）。"""
    assert ":day" in str(hs._TREEMAP_SQL)


# ---------------------------------------------------------------------------
# 分组树纯函数
# ---------------------------------------------------------------------------


def test_groups_all_is_single_group() -> None:
    items = [{"symbol": "a"}, {"symbol": "b"}]
    out = hs.build_heatmap_groups(items, group="all")
    assert len(out) == 1 and out[0].name == "全市场" and out[0].count == 2


def test_groups_watchlist_is_single_group() -> None:
    out = hs.build_heatmap_groups([{"symbol": "a"}], group="watchlist")
    assert len(out) == 1 and out[0].name == "自选" and out[0].count == 1


def test_groups_watchlist_empty_is_empty() -> None:
    assert hs.build_heatmap_groups([], group="watchlist") == []


def test_groups_sw_l1_uses_name_map_and_uncategorized() -> None:
    items = [
        {"sw_l1_code": "480000"},
        {"sw_l1_code": "480000"},
        {"sw_l1_code": None},
        {"sw_l1_code": "999999"},
    ]
    out = hs.build_heatmap_groups(items, group="sw_l1", code_to_name={"480000": "银行"})
    assert [(g.code, g.name, g.count) for g in out] == [
        ("480000", "银行", 2),
        ("", "", 1),  # None → 「未分类」组
        ("999999", "", 1),  # 表里没有的码：名字回落空串（前端显示码本身）
    ]


def test_groups_concept_order_follows_first_appearance() -> None:
    items = [{"concept_code": "BK0002"}, {"concept_code": "BK0001"}, {"concept_code": "BK0002"}]
    out = hs.build_heatmap_groups(
        items, group="concept", code_to_name={"BK0002": "乙", "BK0001": "甲"}
    )
    assert [g.code for g in out] == ["BK0002", "BK0001"], (
        "组序 = 行序（首个成员出现序），稳定可复现"
    )


def test_groups_unknown_group_falls_back_to_single() -> None:
    """未知 group 值按「单组全市场」处理（路由层白名单之外的兜底）。"""
    out = hs.build_heatmap_groups([{"symbol": "a"}], group="nonsense")
    assert len(out) == 1 and out[0].name == "全市场"
