"""复权因子缺口修复：upsert 的 COALESCE 不变量 + 批量补洞服务（离线，无网无库）。

三块：
1. ``upsert_quotes`` 的 ON CONFLICT 必须对 ``adj_factor`` 用 COALESCE —— 重灌
   （TuShare ``daily`` 不带因子，映射后恒为 NULL）不得抹掉因子管线写好的值；
2. ``quote_service.backfill_missing_adj_factors`` —— 发现缺口、写入映射值、无缺口
   不外呼、单股失败不中断；
3. CLI ``--adj-factor`` 的接线（调 service + 失效 ``quote:kline:*``）。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.models.quote import DailyQuote
from app.repositories import quote_repo
from app.services import quote_service

START = date(2026, 9, 1)
END = date(2026, 9, 16)


# ── 1. upsert 的 COALESCE 不变量 ────────────────────────────────────


async def test_upsert_quotes_preserves_existing_adj_factor_on_conflict() -> None:
    """CONFLICT 集合里 ``adj_factor`` 必须是 COALESCE(excluded, 既有值)。

    若写成 ``adj_factor = excluded.adj_factor``，任何一次重灌都会把已回补的因子
    抹成 NULL；而 ``get_kline`` 的复权可用性要求请求窗口内**全部**行非空，一行
    被抹掉就足以让复权开关永久禁用。
    """
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))

    # 每日 ingest 的真实形状：adj_factor 恒为 None（TuShare daily 不带该列）。
    quote = DailyQuote(stock_id=1, trade_date=date(2026, 9, 9), close=10.5, adj_factor=None)
    await quote_repo.upsert_quotes(db, [quote])

    stmt = db.execute.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql
    insert_sql, conflict_sql = sql.split("ON CONFLICT", 1)

    # INSERT 路径允许 NULL（全新行无历史值可保，因子由因子管线补）。
    assert "adj_factor" in insert_sql
    assert "adj_factor = coalesce(excluded.adj_factor, daily_quotes.adj_factor)" in conflict_sql
    # 其他列仍必须是"新值直接覆盖"，别把 COALESCE 误扩到 OHLC。
    for col in ("open", "high", "low", "close", "pre_close", "pct_chg", "volume", "amount"):
        assert f"{col} = excluded.{col}" in conflict_sql


async def test_list_missing_adj_factor_pairs_query_shape() -> None:
    """缺口发现查询：返回 ``(stock_id, trade_date)`` 对 + 只限"已拉过因子"的股票。"""
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [(7, date(2026, 9, 9)), (9, date(2026, 9, 10))])
    )

    pairs = await quote_repo.list_missing_adj_factor_pairs(db, START, END)

    assert pairs == [(7, date(2026, 9, 9)), (9, date(2026, 9, 10))]
    sql = str(db.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "adj_factor IS NULL" in sql  # 找的是缺口行
    assert "adj_factor IS NOT NULL" in sql  # 只补已拉过因子的股票
    assert "trade_date >=" in sql and "trade_date <=" in sql


# ── 2. 批量补洞服务 ────────────────────────────────────────────────


class _FakeFrame:
    """最小 DataFrame 替身：只实现 ``to_dict("records")``。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def to_dict(self, _orient: str) -> list[dict]:
        return self._rows


def _ts_rows(*pairs: tuple[str, float | None]) -> list[dict]:
    return [
        {"ts_code": "600519.SH", "trade_date": td, "adj_factor": factor} for td, factor in pairs
    ]


def _install_service_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pairs: list[tuple[int, date]],
    client: AsyncMock,
    written: list[tuple[int, list[tuple[date, float]]]],
) -> None:
    async def fake_list(_db: object, start: date, end: date) -> list[tuple[int, date]]:
        assert (start, end) == (START, END)
        return pairs

    async def fake_get_stock(_db: object, stock_id: int) -> object:
        return SimpleNamespace(id=stock_id, symbol="600519", exchange="Shanghai_Stocks")

    async def fake_update(_db: object, stock_id: int, factors: list) -> int:
        written.append((stock_id, list(factors)))
        return len(factors)

    monkeypatch.setattr(quote_service.quote_repo, "list_missing_adj_factor_pairs", fake_list)
    monkeypatch.setattr(quote_service.stock_repo, "get_stock_by_id", fake_get_stock)
    monkeypatch.setattr(quote_service.quote_repo, "update_adj_factors", fake_update)
    monkeypatch.setattr("app.core.providers.tushare_client.get_tushare_client", lambda: client)


async def test_backfill_missing_adj_factors_writes_only_gap_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发现了缺口对 → 外呼取因子 → 只写缺口日（区间外/非缺口/脏日期/缺值一律剔除）。"""
    written: list[tuple[int, list[tuple[date, float]]]] = []
    client = AsyncMock()
    client.fetch_adj_factor = AsyncMock(
        return_value=_FakeFrame(
            _ts_rows(
                ("20260909", 8.6463),  # 缺口 → 写
                ("20260910", 8.645),  # 缺口 → 写
                ("20260908", 8.6463),  # 非缺口（该行本就有因子）→ 不写
                ("20260801", 9.9),  # 区间外 → 不写
                ("bad", 1.0),  # 日期脏行 → 不写
                ("20260911", None),  # 因子缺失 → 不写
            )
        )
    )
    db = AsyncMock()
    gap_pairs = [(7, date(2026, 9, 9)), (7, date(2026, 9, 10))]
    _install_service_fakes(monkeypatch, pairs=gap_pairs, client=client, written=written)

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 1, "rows": 2, "failed": 0}
    assert written == [(7, [(date(2026, 9, 9), 8.6463), (date(2026, 9, 10), 8.645)])]
    assert db.commit.await_count == 1  # 每股独立提交，中途失败不丢进度
    assert client.fetch_adj_factor.await_args.kwargs == {
        "ts_code": "600519.SH",
        "start_date": "20260901",
        "end_date": "20260916",
    }


async def test_backfill_missing_adj_factors_noop_without_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无缺口 → 返回零统计且**不**外呼（不让一次空跑烧掉 API 配额）。"""
    written: list[tuple[int, list]] = []
    client = AsyncMock()
    fetched: list[bool] = []
    client.fetch_adj_factor = AsyncMock(side_effect=lambda **_: fetched.append(True))
    db = AsyncMock()
    _install_service_fakes(monkeypatch, pairs=[], client=client, written=written)

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 0, "rows": 0, "failed": 0}
    assert fetched == []
    assert written == [] and db.commit.await_count == 0


async def test_backfill_missing_adj_factors_isolates_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单股外呼失败只记 failed 并回滚，后续股票照常修复。"""
    written: list[tuple[int, list[tuple[date, float]]]] = []
    client = AsyncMock()
    client.fetch_adj_factor = AsyncMock(
        side_effect=[
            RuntimeError("tushare 权限不足"),
            _FakeFrame(_ts_rows(("20260909", 8.6463))),
        ]
    )
    db = AsyncMock()
    _install_service_fakes(
        monkeypatch,
        pairs=[(7, date(2026, 9, 9)), (9, date(2026, 9, 9))],
        client=client,
        written=written,
    )

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 1, "rows": 1, "failed": 1}
    assert written == [(9, [(date(2026, 9, 9), 8.6463)])]
    assert db.rollback.await_count == 1 and db.commit.await_count == 1


# ── 3. CLI 接线 ────────────────────────────────────────────────────


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


async def test_main_adj_factor_repair_invalidates_kline_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--adj-factor`` 必须走到 service，并在有行被改写后失效 K 线缓存。"""
    import argparse

    from scripts import repair_market_day as repair

    monkeypatch.setattr(
        repair,
        "parse_args",
        lambda: argparse.Namespace(
            purge_incomplete_today=False,
            yes=False,
            backfill=None,
            adj_factor=[START, END],
            as_of=END,
        ),
    )
    monkeypatch.setattr(repair, "async_session_factory", lambda: _FakeSession())
    called: list[tuple[date, date]] = []

    async def fake_service(_db: object, *, start: date, end: date) -> dict[str, int]:
        called.append((start, end))
        return {"stocks": 21, "rows": 165, "failed": 0}

    monkeypatch.setattr("app.services.quote_service.backfill_missing_adj_factors", fake_service)
    invalidated: list[bool] = []

    async def spy_invalidate() -> int:
        invalidated.append(True)
        return 3

    monkeypatch.setattr(repair, "_invalidate_kline_caches", spy_invalidate)

    await repair.main()

    assert called == [(START, END)]
    assert invalidated == [True]


async def test_main_adj_factor_no_write_skips_invalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有行被改写（全失败/无缺口）时不失效缓存，避免无谓的缓存击穿。"""
    import argparse

    from scripts import repair_market_day as repair

    monkeypatch.setattr(
        repair,
        "parse_args",
        lambda: argparse.Namespace(
            purge_incomplete_today=False,
            yes=False,
            backfill=None,
            adj_factor=[START, END],
            as_of=END,
        ),
    )
    monkeypatch.setattr(repair, "async_session_factory", lambda: _FakeSession())

    async def fake_service(_db: object, *, start: date, end: date) -> dict[str, int]:
        return {"stocks": 0, "rows": 0, "failed": 0}

    monkeypatch.setattr("app.services.quote_service.backfill_missing_adj_factors", fake_service)
    invalidated: list[bool] = []

    async def spy_invalidate() -> int:
        invalidated.append(True)
        return 0

    monkeypatch.setattr(repair, "_invalidate_kline_caches", spy_invalidate)

    await repair.main()

    assert invalidated == []


async def test_kline_cache_invalidation_drops_only_kline_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K 线缓存失效只碰 ``quote:kline:*``（别把 market:* 一起清）。"""
    from app.core import redis as redis_module
    from scripts import repair_market_day as repair

    deleted: list[str] = []

    class _FakeRedis:
        async def keys(self, pattern: str) -> list[str]:
            return ["quote:kline:Shanghai_Stocks:600519:2026-09-01:2026-09-16:raw"]

        async def delete(self, *keys: str) -> int:
            deleted.extend(keys)
            return len(keys)

    async def fake_pool() -> _FakeRedis:
        return _FakeRedis()

    monkeypatch.setattr(redis_module, "get_redis_pool", fake_pool)

    dropped = await repair._invalidate_kline_caches()

    assert dropped == 1
    assert deleted == ["quote:kline:Shanghai_Stocks:600519:2026-09-01:2026-09-16:raw"]
