"""读路径不得因"表里今天没数据"就返回空——应回落到最近可用日并标注陈旧度。

离线：仓储/最近快照日/判据日 seam 全部 monkeypatch，不打库不打网。三条读路径：

- 板块资金流 `{as_of, stale_days, items}`
- 北向 `{as_of, stale_days, source_status, items}`
- 大盘资金流历史 `history_as_of`/`history_stale_days`

`as_of` 一律取"该表实际持有的最近一行"，而不是"今天"。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, get_args

import pytest

from app.schemas.market_data import NorthboundSeriesOut
from app.services import market_data_service as mds
from app.services import market_service

TODAY = date(2026, 9, 17)  # 周四


class RecordingCache:
    """CacheClient 替身（JSON 语义、可断言键）。"""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.set_calls: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ttl))


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _NullDb:
    """占位 session：除 `_latest_snapshot_day` 外每个 DB 调用都必须被 patch 掉。"""

    def __init__(self, value: Any = None) -> None:
        self.value = value
        self.stmts: list[Any] = []

    async def execute(self, stmt: Any) -> _ScalarResult:
        self.stmts.append(stmt)
        return _ScalarResult(self.value)


def _patch_today(monkeypatch: pytest.MonkeyPatch, day: date = TODAY) -> None:
    monkeypatch.setattr(mds, "_today_sh", lambda: day)


def _patch_latest_snapshot(monkeypatch: pytest.MonkeyPatch, day: date | None) -> list[Any]:
    """替换最近快照日 seam；返回记录的调用参数（供断言"用 resolved 日查表"）。"""
    calls: list[Any] = []

    async def _latest(*args: Any, **kwargs: Any) -> date | None:
        calls.append(args)
        return day

    monkeypatch.setattr(mds, "_latest_snapshot_day", _latest)
    return calls


class _DimAwareCtx:
    """`sector_moneyflow_snapshots` 的内存替身：``max(trade_date)`` 支持 dimension 过滤。

    执行语句时按绑定参数里出现的 dimension 字面量挑子集；无过滤则取全表最大日。
    """

    def __init__(self, days_by_dim: dict[str, date]) -> None:
        self.days_by_dim = days_by_dim
        self.stmts: list[Any] = []

    async def execute(self, stmt: Any) -> _ScalarResult:
        self.stmts.append(stmt)
        wanted = {v for v in stmt.compile().params.values() if isinstance(v, str)}
        days = [d for dim, d in self.days_by_dim.items() if not wanted or dim in wanted]
        return _ScalarResult(max(days) if days else None)

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _snapshot(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "board_code": "BK1211",
        "board_name": "汽车",
        "pct_change": 1.35,
        "main_net_inflow": 3.7e9,
        "super_large_net": 2.7e9,
        "large_net": 9.3e8,
        "up_count": 235,
        "down_count": 87,
        "main_net_ratio": 6.46,
        "lead_stock_name": "比亚迪",
        "lead_stock_code": "002594",
        "lead_stock_pct": 3.1,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _bound_rows(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "trade_date": date(2026, 9, 7),
        "net_amount": -12.5,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _flow_row(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "trade_date": date(2026, 9, 3),
        "main_net": -8.8e9,
        "super_large_net": -5.1e9,
        "large_net": -3.7e9,
        "mid_net": 1.2e9,
        "small_net": 7.6e9,
        "main_ratio": -2.1,
        "close": 3120.5,
        "pct_change": -0.72,
        "amount": 7.3e11,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _FakeEm:
    def __init__(self, today: Any) -> None:
        self.today = today

    async def fetch_market_moneyflow_today(self) -> Any:
        return self.today


# ---------------------------------------------------------------------------
# _latest_snapshot_day —— "该表最近有数据的日期"的唯一实现
# ---------------------------------------------------------------------------


async def test_latest_snapshot_day_reads_max_and_is_none_when_empty() -> None:
    from app.models.market_data import SectorMoneyflowSnapshot

    db = _NullDb(date(2026, 9, 8))
    assert await mds._latest_snapshot_day(db, SectorMoneyflowSnapshot.trade_date) == date(
        2026, 9, 8
    )
    assert "max" in str(db.stmts[0]).lower()
    assert await mds._latest_snapshot_day(_NullDb(None), SectorMoneyflowSnapshot.trade_date) is None
    # 非日期返回值（脏列/聚合意外）一律当"没有可用日"，不得把字符串当日返回
    dirty = await mds._latest_snapshot_day(
        _NullDb("2026-09-08"), SectorMoneyflowSnapshot.trade_date
    )
    assert dirty is None


# ---------------------------------------------------------------------------
# /market/sector-moneyflow —— 回落到表内最近快照日
# ---------------------------------------------------------------------------


async def test_latest_snapshot_day_applies_optional_filters() -> None:
    """过滤参数必须进 WHERE：不带过滤时不得凭空加条件。"""
    from app.models.market_data import SectorMoneyflowSnapshot

    filtered = _NullDb(date(2026, 9, 8))
    await mds._latest_snapshot_day(
        filtered,
        SectorMoneyflowSnapshot.trade_date,
        SectorMoneyflowSnapshot.dimension == "concept",
    )
    sql = str(filtered.stmts[0])
    assert "max" in sql.lower()
    assert "dimension" in sql.lower(), "维度过滤必须进入聚合语句"

    unfiltered = _NullDb(date(2026, 9, 9))
    await mds._latest_snapshot_day(unfiltered, SectorMoneyflowSnapshot.trade_date)
    assert "where" not in str(unfiltered.stmts[0]).lower(), "无过滤时不得带 WHERE"


async def test_sector_moneyflow_falls_back_to_latest_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _repo(db, day, dimension, limit):  # noqa: ANN001
        return (
            []
            if day == date(2026, 9, 17)
            else [
                type(
                    "S",
                    (),
                    {
                        "board_code": "BK1211",
                        "board_name": "汽车",
                        "pct_change": 1.35,
                        "main_net_inflow": 3.7e9,
                        "super_large_net": 2.7e9,
                        "large_net": 9.3e8,
                        "up_count": 235,
                        "down_count": 87,
                        "main_net_ratio": 6.46,
                        "lead_stock_name": "比亚迪",
                        "lead_stock_code": "002594",
                        "lead_stock_pct": 3.1,
                    },
                )()
            ]
        )

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)
    monkeypatch.setattr(mds, "_latest_snapshot_day", lambda *a, **k: _async(date(2026, 9, 8)))
    monkeypatch.setattr(mds, "_today_sh", lambda: date(2026, 9, 17))
    out = await mds.get_sector_moneyflow(None, "industry", 15)
    assert out["as_of"] == "2026-09-08"
    assert out["stale_days"] == 9
    assert out["items"][0]["board_code"] == "BK1211"


async def _async(value: Any) -> Any:
    return value


async def test_sector_moneyflow_queries_the_resolved_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_today(monkeypatch)
    calls = _patch_latest_snapshot(monkeypatch, date(2026, 9, 8))
    seen: list[tuple[Any, ...]] = []

    async def _repo(db, day, dimension, limit):  # noqa: ANN001
        seen.append((day, dimension, limit))
        return [_snapshot()]

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)

    out = await mds.get_sector_moneyflow(None, "concept", 15)

    assert len(calls) == 1, "最近快照日必须来自 _latest_snapshot_day"
    assert seen == [(date(2026, 9, 8), "concept", mds.SECTOR_MONEYFLOW_CACHE_LIMIT)]
    assert out["as_of"] == "2026-09-08"
    assert out["stale_days"] == 9
    assert out["items"][0]["lead_stock_name"] == "比亚迪"


async def test_sector_moneyflow_latest_day_is_per_dimension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最新一天只有 industry 行时，concept 请求必须回落到 concept 自己的最近日。

    否则会拿 industry 的最近日去查 concept（``as_of``/``stale_days`` 有值但 ``items``
    为空），正是本次回落要消除的"非空 as_of + 空列表"组合。
    """
    _patch_today(monkeypatch)
    ctx = _DimAwareCtx({"industry": date(2026, 9, 9), "concept": date(2026, 9, 8)})
    monkeypatch.setattr("app.core.database.async_session_factory", lambda: ctx)
    seen: list[tuple[Any, ...]] = []

    async def _repo(db, day, dimension, limit):  # noqa: ANN001
        seen.append((day, dimension, limit))
        return [_snapshot()]

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)

    industry = await mds.get_sector_moneyflow(None, "industry", 15)
    concept = await mds.get_sector_moneyflow(None, "concept", 15)

    assert industry["as_of"] == "2026-09-09"
    assert concept["as_of"] == "2026-09-08", "concept 的 as_of 必须是 concept 自己的最近日"
    assert concept["stale_days"] == 9
    assert seen == [
        (date(2026, 9, 9), "industry", mds.SECTOR_MONEYFLOW_CACHE_LIMIT),
        (date(2026, 9, 8), "concept", mds.SECTOR_MONEYFLOW_CACHE_LIMIT),
    ], "明细查询日必须与各自维度解析出的最近日一致"


async def test_sector_moneyflow_empty_table_is_explicit_not_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, None)

    async def _repo(*args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("表空时不得查当日明细")

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)
    cache = RecordingCache()

    out = await mds.get_sector_moneyflow(cache, "industry", 15)

    assert out == {"as_of": None, "stale_days": None, "items": []}
    assert cache.set_calls == [], "空 payload 不入缓存（下一次轮询即可恢复）"


async def test_sector_moneyflow_cache_key_carries_as_of_and_hit_skips_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 8))
    cache = RecordingCache()
    hits = 0

    async def _repo(db, day, dimension, limit):  # noqa: ANN001
        nonlocal hits
        hits += 1
        return [_snapshot(board_code="BK0001"), _snapshot(board_code="BK0002")]

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)

    first = await mds.get_sector_moneyflow(cache, "industry", 1)
    assert len(first["items"]) == 1 and first["items"][0]["board_code"] == "BK0001"
    assert cache.set_calls[0][0] == "market:sector-moneyflow:industry:2026-09-08"
    assert len(cache.set_calls[0][1]["items"]) == 2, "缓存全量行，请求时再按 limit 切片"

    second = await mds.get_sector_moneyflow(cache, "industry", 2)
    assert hits == 1, "同一天必须缓存命中，不再查表"
    assert [i["board_code"] for i in second["items"]] == ["BK0001", "BK0002"]
    assert second["as_of"] == "2026-09-08" and second["stale_days"] == 9


async def test_sector_moneyflow_key_changes_with_snapshot_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缓存键必须含快照日：旧日键与无日键的 payload 都不得再被服务。

    两条毒饵都埋下（旧日 09-08 + 无日 legacy），只有"键含当日"的实现才会 miss 并
    重新解析；任何丢掉日期的键都会命中无日毒饵，把 DAYLESS/STALE 当结果返回。
    """
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 9))
    cache = RecordingCache()
    cache.store["market:sector-moneyflow:industry:2026-09-08"] = {
        "as_of": "2026-09-08",
        "stale_days": 9,
        "items": [{"board_code": "STALE"}],
    }
    cache.store["market:sector-moneyflow:industry"] = {
        "as_of": "2026-09-08",
        "stale_days": 9,
        "items": [{"board_code": "DAYLESS"}],
    }

    async def _repo(db, day, dimension, limit):  # noqa: ANN001
        return [_snapshot(board_code="FRESH")]

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)

    out = await mds.get_sector_moneyflow(cache, "industry", 15)

    assert out["as_of"] == "2026-09-09" and out["stale_days"] == 8
    assert out["items"][0]["board_code"] == "FRESH"
    assert cache.set_calls[-1][0] == "market:sector-moneyflow:industry:2026-09-09"


def test_map_sector_moneyflow_row_requires_lead_stock_columns() -> None:
    """映射直读属性：列被改名/拼错时必须炸，不得静默降级成 ``None``（fail-loud）。"""
    incomplete = SimpleNamespace(
        board_code="BK1211",
        board_name="汽车",
        pct_change=1.35,
        main_net_inflow=3.7e9,
        super_large_net=2.7e9,
        large_net=9.3e8,
        up_count=235,
        down_count=87,
        main_net_ratio=6.46,
    )
    with pytest.raises(AttributeError):
        mds._map_sector_moneyflow_row(incomplete)


# ---------------------------------------------------------------------------
# /market/northbound —— 停更标注
# ---------------------------------------------------------------------------


async def test_northbound_discontinued_when_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 7))

    async def _repo(db, days):  # noqa: ANN001
        return [
            _bound_rows(trade_date=date(2026, 8, 20), net_amount=5.0),
            _bound_rows(trade_date=date(2026, 9, 7), net_amount=-12.5),
        ]

    monkeypatch.setattr(mds.market_data_repo, "list_northbound", _repo)

    out = await mds.get_northbound_series(None, 30)

    assert out["as_of"] == "2026-09-07"
    assert out["stale_days"] == 10
    assert out["source_status"] == "discontinued"
    assert out["items"] == [
        {"date": "2026-08-20", "net_amount": 5.0},
        {"date": "2026-09-07", "net_amount": -12.5},
    ], "升序不变"


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (date(2026, 9, 17), "live"),  # 0 天
        (date(2026, 9, 16), "live"),  # 1 天
        (date(2026, 9, 12), "live"),  # 边界 5 天
        (date(2026, 9, 11), "discontinued"),  # 6 天
    ],
)
async def test_northbound_source_status_threshold(
    monkeypatch: pytest.MonkeyPatch, as_of: date, expected: str
) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, as_of)

    async def _repo(db, days):  # noqa: ANN001
        return [_bound_rows(trade_date=as_of)]

    monkeypatch.setattr(mds.market_data_repo, "list_northbound", _repo)

    out = await mds.get_northbound_series(None, 30)
    assert out["source_status"] == expected


async def test_northbound_empty_table_is_discontinued_none_as_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, None)

    async def _repo(*args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("表空时不得查明细")

    monkeypatch.setattr(mds.market_data_repo, "list_northbound", _repo)
    cache = RecordingCache()

    out = await mds.get_northbound_series(cache, 30)

    assert out == {
        "as_of": None,
        "stale_days": None,
        "source_status": "discontinued",
        "items": [],
    }
    assert cache.set_calls == []


def test_northbound_source_status_literal_has_no_unreachable_member() -> None:
    """声明面只保留代码能产出的两个值：多一个 "stale" 会逼前端适配死分支。"""
    members = get_args(NorthboundSeriesOut.model_fields["source_status"].annotation)
    assert members == ("live", "discontinued")


async def test_northbound_cache_key_carries_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 7))
    cache = RecordingCache()
    hits = 0

    async def _repo(db, days):  # noqa: ANN001
        nonlocal hits
        hits += 1
        return [_bound_rows()]

    monkeypatch.setattr(mds.market_data_repo, "list_northbound", _repo)

    first = await mds.get_northbound_series(cache, 30)
    assert cache.set_calls[0][0] == "market:northbound:30:2026-09-07"
    assert cache.set_calls[0][2] == mds.NORTHBOUND_TTL
    second = await mds.get_northbound_series(cache, 30)
    assert hits == 1 and first == second


# ---------------------------------------------------------------------------
# /market/market-moneyflow —— 历史段单独标注陈旧度
# ---------------------------------------------------------------------------


async def test_market_moneyflow_history_labels_staleness(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 3))
    monkeypatch.setattr(mds, "_get_eastmoney", lambda: _FakeEm({"total": {"main_net": 1.0}}))
    cache = RecordingCache()

    async def _repo(db, days):  # noqa: ANN001
        assert days == 30
        return [_flow_row()]

    monkeypatch.setattr(mds.market_data_repo, "list_market_moneyflow_daily", _repo)

    out = await mds.get_market_moneyflow(cache)

    assert out["history_as_of"] == "2026-09-03"
    assert out["history_stale_days"] == 14
    assert out["today"] == {"total": {"main_net": 1.0}}
    assert out["history"] == [
        {
            "date": "2026-09-03",
            "main_net": -8.8e9,
            "super_large_net": -5.1e9,
            "large_net": -3.7e9,
            "mid_net": 1.2e9,
            "small_net": 7.6e9,
            "main_ratio": -2.1,
            "close": 3120.5,
            "pct_change": -0.72,
            "amount": 7.3e11,
        }
    ]
    assert cache.set_calls[0][0] == "market:market-moneyflow:2026-09-03"


async def test_market_moneyflow_empty_history_nulls_both_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, None)
    monkeypatch.setattr(mds, "_get_eastmoney", lambda: _FakeEm({"total": {"main_net": 1.0}}))

    async def _repo(db, days):  # noqa: ANN001
        return []

    monkeypatch.setattr(mds.market_data_repo, "list_market_moneyflow_daily", _repo)

    out = await mds.get_market_moneyflow(None)

    assert out["history_as_of"] is None and out["history_stale_days"] is None
    assert out["history"] == []
    assert out["today"] == {"total": {"main_net": 1.0}}, "实时段不受历史表影响"


async def test_market_moneyflow_realtime_failure_still_serves_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """东财实时失败只降级实时段（既有行为），历史陈旧度照常标注。"""
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 3))

    def _boom() -> Any:
        raise RuntimeError("no token")

    monkeypatch.setattr(mds, "_get_eastmoney", _boom)

    async def _repo(db, days):  # noqa: ANN001
        return [_flow_row()]

    monkeypatch.setattr(mds.market_data_repo, "list_market_moneyflow_daily", _repo)

    out = await mds.get_market_moneyflow(None)

    assert out["today"] is None
    assert out["history_as_of"] == "2026-09-03" and out["history_stale_days"] == 14


# ---------------------------------------------------------------------------
# Task 8：元数据只描述返回的 items + 缓存命中按当下重算 stale_days
# ---------------------------------------------------------------------------


async def test_sector_moneyflow_stale_days_recomputed_on_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨零点后命中的 payload 不得回放写入时冻结的 stale_days。"""
    _patch_today(monkeypatch, date(2026, 9, 18))  # 缓存是 09-17 写的（stale_days=9）
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 8))
    cache = RecordingCache()
    cache.store["market:sector-moneyflow:industry:2026-09-08"] = {
        "as_of": "2026-09-08",
        "stale_days": 9,  # frozen at write time — must NOT be replayed
        "items": [{"board_code": "CACHED"}],
    }

    async def _repo(*args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("cache hit must not query the table")

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)

    out = await mds.get_sector_moneyflow(cache, "industry", 15)

    assert out["as_of"] == "2026-09-08"
    assert out["stale_days"] == 10, "陈旧度必须按当下重算（09-18 - 09-08）"
    assert out["items"][0]["board_code"] == "CACHED"


async def test_northbound_stale_days_and_status_recomputed_on_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命中缓存时 stale_days / source_status 按当下重算（as_of 来自 items）。"""
    _patch_today(monkeypatch, date(2026, 9, 18))
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 17))
    cache = RecordingCache()
    cache.store["market:northbound:30:2026-09-17"] = {
        "as_of": "2026-09-17",
        "stale_days": 1,  # frozen: 09-18 应为 1 天... 用 12 天前的一天才能看出差别
        "source_status": "live",
        "items": [{"date": "2026-09-06", "net_amount": -12.5}],
    }

    async def _repo(*args: Any, **kwargs: Any) -> list[Any]:
        raise AssertionError("cache hit must not query the table")

    monkeypatch.setattr(mds.market_data_repo, "list_northbound", _repo)

    out = await mds.get_northbound_series(cache, 30)

    assert out["as_of"] == "2026-09-06", "as_of 必须来自返回的 items"
    assert out["stale_days"] == 12, "09-18 - 09-06"
    assert out["source_status"] == "discontinued", ">5 天必须翻成停更"
    assert out["items"][0]["net_amount"] == -12.5


async def test_market_moneyflow_history_stale_recomputed_on_cache_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_today(monkeypatch, date(2026, 9, 20))
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 3))
    cache = RecordingCache()
    cache.store["market:market-moneyflow:2026-09-03"] = {
        "today": None,
        "history": [{"date": "2026-09-03", "main_net": -8.8e9}],
        "history_as_of": "2026-09-03",
        "history_stale_days": 14,  # frozen at write time
    }

    out = await mds.get_market_moneyflow(cache)

    assert out["history_as_of"] == "2026-09-03", "历史元数据来自 history 末行"
    assert out["history_stale_days"] == 17, "09-20 - 09-03"
    assert out["history"][0]["date"] == "2026-09-03"


async def test_northbound_window_without_rows_drops_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 8 核心：表有更早日、但 days 窗口内一行都没有 → 不得返回"非空 as_of + 空 items"。

    旧实现用表级 max 当 as_of：表内最近日 09-07、请求 days=5（窗口 09-12 起）时，
    ``list_northbound`` 返回空列表而 as_of 仍是 09-07。现在元数据由 items 推导，
    无行即全 None / discontinued。
    """
    _patch_today(monkeypatch, TODAY)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 7))

    async def _repo(db, days):  # noqa: ANN001
        return []  # 09-07 早于 09-12 的窗口

    monkeypatch.setattr(mds.market_data_repo, "list_northbound", _repo)
    cache = RecordingCache()

    out = await mds.get_northbound_series(cache, 5)

    assert out == {
        "as_of": None,
        "stale_days": None,
        "source_status": "discontinued",
        "items": [],
    }
    assert cache.set_calls == [], "无行的 payload 不入缓存"


async def test_sector_moneyflow_empty_items_drop_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """防御性不变量：解析出 as_of 但明细为空时，元数据一并归零。"""
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 8))

    async def _repo(db, day, dimension, limit):  # noqa: ANN001
        return []

    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow", _repo)

    out = await mds.get_sector_moneyflow(None, "industry", 15)

    assert out == {"as_of": None, "stale_days": None, "items": []}


async def test_market_moneyflow_history_metadata_derived_from_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """防御性不变量：表级 max 有值但 history 返回空 → 两个历史字段都是 None。"""
    _patch_today(monkeypatch)
    _patch_latest_snapshot(monkeypatch, date(2026, 9, 3))
    monkeypatch.setattr(mds, "_get_eastmoney", lambda: _FakeEm({"total": {"main_net": 1.0}}))

    async def _repo(db, days):  # noqa: ANN001
        return []

    monkeypatch.setattr(mds.market_data_repo, "list_market_moneyflow_daily", _repo)

    out = await mds.get_market_moneyflow(None)

    assert out["history_as_of"] is None and out["history_stale_days"] is None
    assert out["history"] == []


# ---------------------------------------------------------------------------
# /market/rankings —— is_latest_trading_day 必须显式判据（不再恒真）
# ---------------------------------------------------------------------------


def _patch_rankings(monkeypatch: pytest.MonkeyPatch, day: date, quality: str = "fallback") -> None:
    md = market_service.market_day_service.MarketDay(
        day,
        quality,
        "latest_day_incomplete",
        5485,
        5513,
        1.0,
        True,  # type: ignore[arg-type]
    )

    async def _resolve(_db: Any, *, cache: Any = None) -> Any:
        return md

    async def _rows(_db: Any, _type: str, _day: date, _limit: int) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)
    monkeypatch.setattr(market_service, "_ranking_rows", _rows)


async def test_rankings_fallback_day_is_not_latest_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_service, "_today_sh", lambda: TODAY)
    _patch_rankings(monkeypatch, date(2026, 9, 16), "fallback")

    out = await market_service.get_rankings(None, None, "gainers", 5)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 16)
    assert out.is_latest_trading_day is False, "回落日不得宣称是最新交易日"


async def test_rankings_resolved_today_is_latest_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(market_service, "_today_sh", lambda: TODAY)
    _patch_rankings(monkeypatch, TODAY, "complete")

    out = await market_service.get_rankings(None, None, "gainers", 5)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 17)
    assert out.is_latest_trading_day is True


async def test_rankings_latest_day_and_fallback_quality_coexist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未来脏行顶起候选、resolver 回落到当前预期交易日时，两字段同时成立。

    ``is_latest_trading_day`` 讲"展示的这一天是不是当前预期交易日"，``as_of_quality``
    讲"表内最新行是不是被跳过了"——互不派生：这里展示日 09-17 就是今天且为 True，
    而表内最新行是更脏的 09-18，故 quality="fallback"。不得用 quality 去否掉布尔。
    """
    monkeypatch.setattr(market_service, "_today_sh", lambda: TODAY)
    _patch_rankings(monkeypatch, TODAY, "fallback")

    out = await market_service.get_rankings(None, None, "gainers", 5)  # type: ignore[arg-type]

    assert out.as_of == TODAY
    assert out.is_latest_trading_day is True, "展示日就是当前预期交易日"
    assert out.as_of_quality == "fallback", "表内最新行是另一个（更脏的）未来日"


async def test_rankings_weekend_expects_friday(monkeypatch: pytest.MonkeyPatch) -> None:
    """周六/周日"最近交易日"= 周五；周五的 as_of 才算最新。"""
    monkeypatch.setattr(market_service, "_today_sh", lambda: date(2026, 9, 19))  # 周六

    _patch_rankings(monkeypatch, date(2026, 9, 18), "complete")
    friday = await market_service.get_rankings(None, None, "gainers", 5)  # type: ignore[arg-type]
    assert friday.is_latest_trading_day is True

    _patch_rankings(monkeypatch, date(2026, 9, 17), "fallback")
    thursday = await market_service.get_rankings(None, None, "gainers", 5)  # type: ignore[arg-type]
    assert thursday.is_latest_trading_day is False


async def test_rankings_empty_db_label_uses_shanghai_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`md is None` 的降级分支也必须用上海判据日做 as_of 标签（与同函数主体一致）。"""

    class _PinnedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)  # 宿主本地"今天"（周日），故意不等于上海判据日

    monkeypatch.setattr(market_service, "date", _PinnedDate)
    monkeypatch.setattr(market_service, "_today_sh", lambda: date(2026, 9, 19))  # 周六

    async def _resolve(_db: Any, *, cache: Any = None) -> Any:
        return None

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)

    out = await market_service.get_rankings(None, None, "gainers", 5)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 18), "周六的最近预期交易日是周五（上海时区）"
    assert out.as_of_quality == "partial"
    assert out.items == []
    assert out.is_latest_trading_day is False


async def test_rankings_future_dated_day_is_not_latest_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未来日的脏行（data_init 未收盘日那类）不得被 `>=` 判成"最新交易日"。"""
    monkeypatch.setattr(market_service, "_today_sh", lambda: TODAY)
    _patch_rankings(monkeypatch, date(2026, 9, 18), "partial")

    out = await market_service.get_rankings(None, None, "gainers", 5)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 18)
    assert out.is_latest_trading_day is False
