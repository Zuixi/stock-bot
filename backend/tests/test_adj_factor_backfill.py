"""复权因子缺口修复：upsert 的 COALESCE 不变量 + 批量补洞服务（离线，无网无库）。

三块：
1. ``upsert_quotes`` 的 ON CONFLICT 必须对 ``adj_factor`` 用 COALESCE —— 重灌
   （TuShare ``daily`` 不带因子，映射后恒为 NULL）不得抹掉因子管线写好的值；
2. ``quote_service.backfill_missing_adj_factors`` —— 候选股票按区间发现、**每股补齐
   全部历史缺口**（修就修完，不留"最新行有因子、中段 NULL"的锁死态）、无缺口不外呼、
   单股失败不中断、预算截断暴露 ``remaining``、写不出因子记 ``unfilled``、写成功后
   按符号失效 K 线缓存；
3. CLI ``--adj-factor`` 的接线（调 service + 失效 ``quote:kline:*`` + 区间/日期守卫）。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.models.market_data import SectorMoneyflowSnapshot
from app.models.quote import DailyQuote
from app.repositories import quote_repo
from app.services import quote_service

START = date(2026, 9, 1)
END = date(2026, 9, 16)
# 缺口日（在区间内）与更早的缺口日（区间外）—— I1：候选股票要补齐全部历史缺口
GAP_IN_RANGE = date(2026, 9, 9)
GAP_OUT_OF_RANGE = date(2026, 8, 20)


# ── 1. upsert 的 COALESCE 不变量 ────────────────────────────────────


async def test_upsert_quotes_preserves_existing_adj_factor_on_conflict() -> None:
    """CONFLICT 集合里 ``adj_factor`` 必须是 COALESCE(excluded, 既有值)。

    若写成 ``adj_factor = excluded.adj_factor``，任何一次重灌都会把已回补的因子
    抹成 NULL；而 ``get_kline`` 的复权可用性要求请求窗口内**全部**行非空，一行
    被抹掉就足以让复权开关永久禁用。

    断言绑定在生产 ``upsert_quotes`` 真正生成的语句对象上（不是编译后的 SQL 子串）：
    ``_post_values_clause.update_values_to_set`` 是 ``{列名: 更新表达式}``，直接检查
    ``adj_factor`` 是 ``coalesce(excluded.adj_factor, daily_quotes.adj_factor)`` 构造。
    """
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))

    # 每日 ingest 的真实形状：adj_factor 恒为 None（TuShare daily 不带该列）。
    quote = DailyQuote(stock_id=1, trade_date=GAP_IN_RANGE, close=10.5, adj_factor=None)
    await quote_repo.upsert_quotes(db, [quote])

    stmt = db.execute.await_args.args[0]
    conflict = stmt._post_values_clause  # OnConflictDoUpdate
    update_set: dict[str, Any] = dict(conflict.update_values_to_set)

    adj = update_set["adj_factor"]
    assert adj.name == "coalesce"  # 不是裸的 excluded.adj_factor
    clauses = list(adj.clauses)
    assert [(c.table.name, c.name) for c in clauses] == [
        ("excluded", "adj_factor"),
        ("daily_quotes", "adj_factor"),
    ]
    # 其他列仍必须是"新值直接覆盖"（excluded.<col>），别把 COALESCE 误扩到 OHLC。
    for col in ("open", "high", "low", "close", "pre_close", "pct_chg", "volume", "amount"):
        expr = update_set[col]
        assert (expr.table.name, expr.name) == ("excluded", col)


@pytest.mark.parametrize(
    ("table", "index_name", "columns", "where"),
    [
        # migration d1e2f3a4b5c6
        (
            DailyQuote,
            "idx_daily_quotes_stock_id_adj_factor",
            ["stock_id"],
            "adj_factor IS NOT NULL",
        ),
        # migration d7c8b9a0e1f2 — the other index this task's commit created
        (
            SectorMoneyflowSnapshot,
            "ix_sector_moneyflow_dim_date",
            ["dimension", "trade_date"],
            None,
        ),
    ],
)
def test_model_metadata_mirrors_the_indexes_this_task_migrated(
    table: Any, index_name: str, columns: list[str], where: str | None
) -> None:
    """Task 8 的迁移建的索引必须镜像在模型元数据里（autogenerate 守卫）。

    ``migrations/env.py`` 用 ``Base.metadata`` 且没有 ``include_object`` 过滤，所以
    "只在迁移里存在"的索引会被 ``alembic revision --autogenerate`` 判成本地多余而生成
    ``drop_index``（随后一次 autogenerate 迁移就会把索引删掉）。``quote.py`` 已按同一
    约定镜像 ``cf4b8e317fe5`` 建的两个排行索引，本任务新建的两个也必须跟上；名字/列/
    谓词三者任一对不上 autogenerate 就会漂移——partial index 一丢，
    ``list_missing_adj_factor_pairs`` 的 index-only scan 就没了依据。

    （同表更早的 ``ix_sector_moneyflow_date_dim`` 仍是历史遗留未镜像项，不在本任务范围。）
    """
    index = next((idx for idx in table.__table__.indexes if idx.name == index_name), None)
    assert index is not None, f"migration index {index_name} is missing from {table.__name__}"
    assert [column.name for column in index.columns] == columns
    if where is not None:
        assert str(index.dialect_options["postgresql"]["where"]) == where


async def test_list_missing_adj_factor_pairs_query_shape() -> None:
    """缺口发现查询：返回 ``(stock_id, trade_date)`` 对 + 只限"已拉过因子"的股票。"""
    from sqlalchemy.dialects import postgresql

    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [(7, GAP_IN_RANGE), (9, date(2026, 9, 10))])
    )

    pairs = await quote_repo.list_missing_adj_factor_pairs(db, START, END)

    assert pairs == [(7, GAP_IN_RANGE), (9, date(2026, 9, 10))]
    sql = str(db.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "adj_factor IS NULL" in sql  # 找的是缺口行
    assert "adj_factor IS NOT NULL" in sql  # 只补已拉过因子的股票
    assert "trade_date >=" in sql and "trade_date <=" in sql


async def test_list_missing_adj_factor_dates_for_stocks_query_shape() -> None:
    """候选股票的"全部历史缺口"查询：只按 stock_id + adj_factor IS NULL，不设日期上界。"""
    from sqlalchemy.dialects import postgresql

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: [(7, GAP_OUT_OF_RANGE)]))

    pairs = await quote_repo.list_missing_adj_factor_dates_for_stocks(db, [7, 9])

    assert pairs == [(7, GAP_OUT_OF_RANGE)]
    sql = str(db.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "adj_factor IS NULL" in sql
    assert "stock_id IN" in sql
    assert "trade_date >=" not in sql and "trade_date <=" not in sql  # 不按区间收窄

    assert await quote_repo.list_missing_adj_factor_dates_for_stocks(db, []) == []


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
    all_pairs: list[tuple[int, date]] | None = None,
    cache_patterns: list[str] | None = None,
) -> None:
    """接线 service 的全部接缝（``get_stock_by_id`` 的替身可被具体用例覆盖）。"""

    async def fake_list(_db: object, start: date, end: date) -> list[tuple[int, date]]:
        assert (start, end) == (START, END)
        return pairs

    async def fake_all(_db: object, stock_ids: list[int]) -> list[tuple[int, date]]:
        source = pairs if all_pairs is None else all_pairs
        wanted = set(stock_ids)
        return [(s, d) for s, d in source if s in wanted]

    async def fake_get_stock(_db: object, stock_id: int) -> object:
        return SimpleNamespace(id=stock_id, symbol="600519", exchange="Shanghai_Stocks")

    async def fake_update(_db: object, stock_id: int, factors: list) -> int:
        written.append((stock_id, list(factors)))
        return len(factors)

    monkeypatch.setattr(quote_service.quote_repo, "list_missing_adj_factor_pairs", fake_list)
    monkeypatch.setattr(
        quote_service.quote_repo, "list_missing_adj_factor_dates_for_stocks", fake_all
    )
    monkeypatch.setattr(quote_service.stock_repo, "get_stock_by_id", fake_get_stock)
    monkeypatch.setattr(quote_service.quote_repo, "update_adj_factors", fake_update)
    monkeypatch.setattr("app.core.providers.tushare_client.get_tushare_client", lambda: client)

    if cache_patterns is not None:

        class _FakeCache:
            def __init__(self, _redis: object) -> None:
                pass

            async def delete_pattern(self, pattern: str) -> int:
                cache_patterns.append(pattern)
                return 1

        async def fake_pool() -> object:
            return object()

        monkeypatch.setattr(quote_service, "CacheClient", _FakeCache)
        monkeypatch.setattr("app.core.redis.get_redis_pool", fake_pool)


async def test_backfill_missing_adj_factors_writes_only_gap_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选来自区间，但每股要补齐**全部历史缺口**（含区间外的旧洞），只写缺口日。"""
    written: list[tuple[int, list[tuple[date, float]]]] = []
    cache_patterns: list[str] = []
    client = AsyncMock()
    client.fetch_adj_factor = AsyncMock(
        return_value=_FakeFrame(
            _ts_rows(
                ("20260820", 8.90),  # 区间外的旧缺口 → 也必须补（I1）
                ("20260909", 8.6463),  # 区间内缺口 → 写
                ("20260908", 8.6463),  # 非缺口（该行本就有因子）→ 不写
                ("bad", 1.0),  # 日期脏行 → 不写
                ("20260911", None),  # 因子缺失 → 不写
            )
        )
    )
    db = AsyncMock()
    _install_service_fakes(
        monkeypatch,
        pairs=[(7, GAP_IN_RANGE)],
        all_pairs=[(7, GAP_OUT_OF_RANGE), (7, GAP_IN_RANGE)],
        client=client,
        written=written,
        cache_patterns=cache_patterns,
    )

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 1, "rows": 2, "failed": 0, "unfilled": 0, "remaining": 0}
    assert written == [
        (7, [(GAP_OUT_OF_RANGE, 8.90), (GAP_IN_RANGE, 8.6463)]),
    ]
    assert db.commit.await_count == 1  # 每股独立提交，中途失败不丢进度
    # 一次外呼覆盖该股"自己的"缺口范围（区间外旧洞到区间内缺口），不是区间本身
    assert client.fetch_adj_factor.await_args.kwargs == {
        "ts_code": "600519.SH",
        "start_date": "20260820",
        "end_date": "20260909",
    }
    # 写成功后按符号失效 K 线缓存（reconcile 路径同样依赖 service 这一步）
    assert cache_patterns == ["quote:kline:Shanghai_Stocks:600519:*"]


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

    assert stats == {"stocks": 0, "rows": 0, "failed": 0, "unfilled": 0, "remaining": 0}
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
        pairs=[(7, GAP_IN_RANGE), (9, GAP_IN_RANGE)],
        client=client,
        written=written,
    )

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 1, "rows": 1, "failed": 1, "unfilled": 0, "remaining": 0}
    assert written == [(9, [(GAP_IN_RANGE, 8.6463)])]
    assert db.rollback.await_count == 1 and db.commit.await_count == 1


async def test_backfill_missing_adj_factors_isolates_get_stock_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_stock_by_id`` 的瞬时 DB 错误计入 failed 并继续，不得毁掉整批（M7）。"""
    written: list[tuple[int, list[tuple[date, float]]]] = []
    client = AsyncMock()
    client.fetch_adj_factor = AsyncMock(return_value=_FakeFrame(_ts_rows(("20260909", 8.6463))))
    db = AsyncMock()
    _install_service_fakes(
        monkeypatch,
        pairs=[(7, GAP_IN_RANGE), (9, GAP_IN_RANGE)],
        client=client,
        written=written,
    )

    async def flaky_get_stock(_db: object, stock_id: int) -> object:
        if stock_id == 7:
            raise RuntimeError("connection reset")
        return SimpleNamespace(id=stock_id, symbol="600519", exchange="Shanghai_Stocks")

    monkeypatch.setattr(quote_service.stock_repo, "get_stock_by_id", flaky_get_stock)

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 1, "rows": 1, "failed": 1, "unfilled": 0, "remaining": 0}
    assert written == [(9, [(GAP_IN_RANGE, 8.6463)])]


async def test_backfill_missing_adj_factors_budget_reports_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单次调用股票数有上限；被截断的股票计入 ``remaining``，而非静默丢弃（I2）。"""
    written: list[tuple[int, list[tuple[date, float]]]] = []
    client = AsyncMock()
    client.fetch_adj_factor = AsyncMock(return_value=_FakeFrame(_ts_rows(("20260909", 8.6463))))
    db = AsyncMock()
    _install_service_fakes(
        monkeypatch,
        pairs=[(7, GAP_IN_RANGE), (9, GAP_IN_RANGE), (11, GAP_IN_RANGE)],
        client=client,
        written=written,
    )

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END, max_stocks=2)

    assert stats["remaining"] == 1  # 第 3 只没被处理，但也没被吞掉
    assert [s for s, _ in written] == [7, 9]
    assert client.fetch_adj_factor.await_count == 2


async def test_backfill_missing_adj_factors_counts_unfilled_without_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TuShare 还没有该日因子 → 不提交、计入 ``unfilled``，{rows:0,failed:0} 不能读成健康（M4）。"""
    written: list[tuple[int, list]] = []
    client = AsyncMock()
    client.fetch_adj_factor = AsyncMock(return_value=_FakeFrame([]))
    db = AsyncMock()
    _install_service_fakes(
        monkeypatch,
        pairs=[(7, GAP_IN_RANGE)],
        client=client,
        written=written,
    )

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 1, "rows": 0, "failed": 0, "unfilled": 1, "remaining": 0}
    assert written == [] and db.commit.await_count == 0


async def test_backfill_missing_adj_factors_partial_response_is_unfilled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TuShare 只返回部分缺口日 → 已填部分提交，但该股仍记 ``unfilled``（残留可见）。"""
    written: list[tuple[int, list[tuple[date, float]]]] = []
    client = AsyncMock()
    client.fetch_adj_factor = AsyncMock(return_value=_FakeFrame(_ts_rows(("20260820", 8.90))))
    db = AsyncMock()
    _install_service_fakes(
        monkeypatch,
        pairs=[(7, GAP_IN_RANGE)],
        all_pairs=[(7, GAP_OUT_OF_RANGE), (7, GAP_IN_RANGE)],
        client=client,
        written=written,
    )

    stats = await quote_service.backfill_missing_adj_factors(db, start=START, end=END)

    assert stats == {"stocks": 1, "rows": 1, "failed": 0, "unfilled": 1, "remaining": 0}
    assert written == [(7, [(GAP_OUT_OF_RANGE, 8.90)])]
    assert db.commit.await_count == 1


# ── 3. CLI 接线 ────────────────────────────────────────────────────


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _cli_args(
    repair: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    adj_factor: list[date] | None,
    as_of: date | None,
) -> None:
    import argparse

    monkeypatch.setattr(
        repair,
        "parse_args",
        lambda: argparse.Namespace(
            purge_incomplete_today=False,
            yes=False,
            backfill=None,
            adj_factor=adj_factor,
            as_of=as_of,
        ),
    )


AS_OF = date(2026, 9, 17)  # 09-16 是最后一个已收盘工作日（09-17 当天不算）


async def test_main_adj_factor_repair_invalidates_kline_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--adj-factor`` 必须走到 service，并在有行被改写后失效 K 线缓存。"""
    from scripts import repair_market_day as repair

    _cli_args(repair, monkeypatch, adj_factor=[START, END], as_of=AS_OF)
    monkeypatch.setattr(repair, "async_session_factory", lambda: _FakeSession())
    called: list[tuple[date, date]] = []

    async def fake_service(_db: object, *, start: date, end: date) -> dict[str, int]:
        called.append((start, end))
        return {"stocks": 21, "rows": 165, "failed": 0, "unfilled": 0, "remaining": 0}

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
    from scripts import repair_market_day as repair

    _cli_args(repair, monkeypatch, adj_factor=[START, END], as_of=AS_OF)
    monkeypatch.setattr(repair, "async_session_factory", lambda: _FakeSession())

    async def fake_service(_db: object, *, start: date, end: date) -> dict[str, int]:
        return {"stocks": 0, "rows": 0, "failed": 0, "unfilled": 1, "remaining": 0}

    monkeypatch.setattr("app.services.quote_service.backfill_missing_adj_factors", fake_service)
    invalidated: list[bool] = []

    async def spy_invalidate() -> int:
        invalidated.append(True)
        return 0

    monkeypatch.setattr(repair, "_invalidate_kline_caches", spy_invalidate)

    await repair.main()

    assert invalidated == []


async def test_main_adj_factor_rejects_inverted_range(monkeypatch: pytest.MonkeyPatch) -> None:
    """``START > END`` 必须报错退出，而不是静默空跑（M5）。"""
    from scripts import repair_market_day as repair

    _cli_args(repair, monkeypatch, adj_factor=[END, START], as_of=AS_OF)
    called: list[bool] = []

    async def fake_service(_db: object, *, start: date, end: date) -> dict[str, int]:
        called.append(True)
        return {}

    monkeypatch.setattr("app.services.quote_service.backfill_missing_adj_factors", fake_service)

    with pytest.raises(SystemExit) as exc:
        await repair.main()
    assert exc.value.code == 2
    assert called == []


async def test_main_adj_factor_skips_days_past_last_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """晚于最后一个已收盘工作日的日期被裁掉，不对外呼空结果（M5，镜像 --backfill）。"""
    from scripts import repair_market_day as repair

    future = date(2026, 9, 20)  # 周日；区间右端越界
    _cli_args(repair, monkeypatch, adj_factor=[START, future], as_of=AS_OF)
    monkeypatch.setattr(repair, "async_session_factory", lambda: _FakeSession())
    called: list[tuple[date, date]] = []

    async def fake_service(_db: object, *, start: date, end: date) -> dict[str, int]:
        called.append((start, end))
        return {"stocks": 0, "rows": 0, "failed": 0, "unfilled": 0, "remaining": 0}

    monkeypatch.setattr("app.services.quote_service.backfill_missing_adj_factors", fake_service)

    await repair.main()

    assert called == [(START, END)]  # 右端被裁到 09-16（最后一个已收盘工作日）


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
