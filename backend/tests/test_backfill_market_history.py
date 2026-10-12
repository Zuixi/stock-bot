"""历史回补脚本（一个脚本，两个域）：区间生成 / E2 窗口守卫 / Ruling 18 不可变性 / dry-run。

默认闸门（不触 DB）：

- 交易日区间生成只取 `daily_quotes` 真实有行的日期，且**升序**（`promo_*`/`broken_rate`
  依赖前一日 streak，乱序会把晋级率算错）；
- `window_limits_complete` 的正/反例（E2：窗口内**任一日**缺限价 → 不完备）；
- `--dry-run` 不写库、不增强、不 commit；
- 重跑收敛（同一批日、同一原因分布）；
- Ruling 18：回补 upsert 的 `DO UPDATE ... WHERE <stored>='backfill'` 守卫（编译 SQL 断言）
  —— 实时行永不被回补重标/覆写。

真库语义另打 `@pytest.mark.e2e`（2000-01-03/04 哨兵日，自建自清，不碰真实历史日）：
`uv run pytest tests/test_backfill_market_history.py -m e2e`
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import limit_up_repo
from app.services import limit_up_calculator as calc
from app.services import limit_up_service
from scripts import backfill_market_history as bmh

D1, D2, D3 = date(2025, 10, 15), date(2025, 10, 16), date(2025, 10, 17)
DAYS = [D1, D2, D3]
# 哨兵日：2000 年，真实历史里绝不存在（真库用例自建自清，不碰任何真实交易日）
SENTINEL_REALTIME = date(2000, 1, 3)
SENTINEL_BACKFILL = date(2000, 1, 4)


class _ScalarRows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def scalars(self) -> _ScalarRows:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeDb:
    """最小假 session：记录 execute 参数与 commit 次数（默认闸门绝不触真库）。"""

    def __init__(self, trade_dates: list[date] | None = None) -> None:
        self.trade_dates = list(trade_dates or [])
        self.calls: list[tuple[Any, Any]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt: Any, params: Any = None) -> _ScalarRows:
        self.calls.append((stmt, params))
        return _ScalarRows(self.trade_dates)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _OneRow:
    rowcount = 1


class _CapturingSession:
    """只捕获 SQL 语句（用于编译断言 Ruling 18 的守卫形状）。"""

    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _OneRow:
        self.statements.append(stmt)
        return _OneRow()

    async def flush(self) -> None:
        return None


def _compiled(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _sentiment_row(trade_date: date, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "zt_count": 1,
        "dt_count": 0,
        "zb_count": 0,
        "broken_rate": 0.0,
        "yzt_avg_pct": 0.0,
        "promo_1to2": 0.0,
        "promo_1to2_n": 0,
        "promo_2to3": 0.0,
        "promo_2to3_n": 0,
        "max_streak": 1,
        "max_streak_symbol": "000001",
        "source": "backfill",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# 交易日区间生成
# ---------------------------------------------------------------------------


async def test_list_trade_dates_returns_ascending_days_of_the_range() -> None:
    """SQL 回 DESC，函数必须返回**升序**；区间参数逐字下传（真源是 daily_quotes）。"""
    db = _FakeDb([D3, D2, D1])  # 模拟 SQL 的 ORDER BY trade_date DESC
    days = await bmh.list_trade_dates(db, D1, D3)
    assert days == [D1, D2, D3]
    _, params = db.calls[0]
    assert params == {"start": D1, "end": D3}
    sql = str(db.calls[0][0]).lower()
    assert "daily_quotes" in sql and "distinct" in sql and "desc" in sql


async def test_list_trade_dates_limit_keeps_the_most_recent_days_ascending() -> None:
    """`--limit` 语义与 `backfill_pct_chg._load_trade_dates` 一致：取最近 N 个（仍升序）。"""
    db = _FakeDb([D3, D2, D1])
    assert await bmh.list_trade_dates(db, D1, D3, limit=2) == [D2, D3]
    assert await bmh.list_trade_dates(db, D1, D3, limit=0) == [D1, D2, D3]


async def test_list_trade_dates_empty_range_is_empty() -> None:
    assert await bmh.list_trade_dates(_FakeDb([]), D1, D3) == []


def test_default_start_is_the_e2_safe_start() -> None:
    """E2 契约：安全起点 = 限价历史第 `window_trade_days(lookback)` 个交易日（默认 10+6=16）。

    提前 `--from` 不会错算——守卫会逐日跳过 `window_limits_incomplete`；但默认值必须是
    文档里实测的 2025-10-15，否则"跑一次拿全历史"会静默退化。
    """
    assert limit_up_service.window_trade_days(calc.LOOKBACK_TRADE_DAYS) == 16
    assert bmh.DEFAULT_START == date(2025, 10, 15)


# ---------------------------------------------------------------------------
# E2 窗口完备性守卫：窗口内任一日缺限价即不完备
# ---------------------------------------------------------------------------


def _patch_window(monkeypatch: Any, days: list[date], missing: list[date]) -> None:
    async def fake_list(db: Any, as_of: date, limit: int) -> list[date]:
        assert limit == limit_up_service.window_trade_days(calc.LOOKBACK_TRADE_DAYS)
        return days

    async def fake_missing(db: Any, dates: list[date]) -> list[date]:
        return missing

    monkeypatch.setattr(limit_up_repo, "list_recent_trade_dates", fake_list)
    monkeypatch.setattr(limit_up_repo, "missing_price_limit_dates", fake_missing)


async def test_window_limits_complete_true_when_every_window_day_has_limits(monkeypatch) -> None:
    window = [date(2025, 9, 16) + timedelta(days=i) for i in range(16)]
    _patch_window(monkeypatch, window, [])
    assert await limit_up_service.window_limits_complete("DB", window[-1]) is True


async def test_window_limits_complete_false_when_any_window_day_lacks_limits(monkeypatch) -> None:
    """不是只看目标日：窗口**起点**缺限价（限价上线前的日子）也必须判不完备。"""
    window = [date(2025, 9, 16) + timedelta(days=i) for i in range(16)]
    _patch_window(monkeypatch, window, [window[0]])
    assert await limit_up_service.window_limits_complete("DB", window[-1]) is False


async def test_window_limits_complete_false_when_window_is_short(monkeypatch) -> None:
    """窗口凑不满（数据起点附近）→ 不完备：截断窗口会把 streak 拉平。"""
    window = [date(2025, 10, 15) + timedelta(days=i) for i in range(2)]
    _patch_window(monkeypatch, window, [])
    assert await limit_up_service.window_limits_complete("DB", window[-1]) is False


async def test_window_limits_complete_false_when_as_of_is_not_the_window_end(monkeypatch) -> None:
    """as_of 自己不是窗口末日（无行情/非交易日）→ 不完备。"""
    window = [date(2025, 9, 16) + timedelta(days=i) for i in range(16)]
    _patch_window(monkeypatch, window, [])
    assert await limit_up_service.window_limits_complete("DB", date(2025, 11, 30)) is False


# ---------------------------------------------------------------------------
# 逐日编排：升序 / 原因可辨 / dry-run 不写库 / 重跑收敛
# ---------------------------------------------------------------------------


def _patch_days(monkeypatch: Any, days: list[date]) -> None:
    async def fake_days(db: Any, start: date, end: date, *, limit: int = 0) -> list[date]:
        return list(days) if not limit else list(days)[-limit:]

    monkeypatch.setattr(bmh, "list_trade_dates", fake_days)


async def test_run_backfill_sentiment_iterates_ascending_and_records_reasons(monkeypatch) -> None:
    """升序执行 + 逐日原因（前置闸门拦下的日子不进落库）。"""
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> str | None:
        return bmh.REASON_WINDOW_INCOMPLETE if trade_date == D2 else None

    persisted: list[date] = []

    async def fake_persist(
        db: Any,
        cache: Any,
        as_of: date | None = None,
        *,
        fact_source: str = "local_calc",
        enrich: bool = True,
    ) -> dict[str, Any]:
        assert fact_source == "backfill" and enrich is False, "回补不得走东财增强"
        persisted.append(as_of)  # type: ignore[arg-type]
        return {"status": "ok", "trade_date": as_of, "zt_count": 1}

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", fake_persist)

    db = _FakeDb()
    summary = await bmh.run_backfill(db, domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D3)

    assert persisted == [D1, D3], "必须升序且跳过的日子不得落库"
    assert [o.reason for o in summary.days] == [
        bmh.REASON_OK,
        bmh.REASON_WINDOW_INCOMPLETE,
        bmh.REASON_OK,
    ]
    assert summary.reason_counts() == {bmh.REASON_OK: 2, bmh.REASON_WINDOW_INCOMPLETE: 1}
    assert summary.written_days() == 2
    assert db.commits == 2, "逐日落库逐日 commit（可中断续跑）"


async def test_run_backfill_sentiment_records_service_skip_reason(monkeypatch) -> None:
    """实时路径自己的降级判据（partial_day/no_limit_up_rows）原样落到逐日原因里。"""
    _patch_days(monkeypatch, [D1])

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def fake_persist(
        db: Any, cache: Any, as_of: date | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "skipped", "reason": "no_limit_up_rows"}

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", fake_persist)

    summary = await bmh.run_backfill(_FakeDb(), domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D1)
    assert [o.reason for o in summary.days] == ["no_limit_up_rows"]
    assert summary.written_days() == 0


async def test_run_backfill_stock_domain_skips_degraded_and_never_enriches(monkeypatch) -> None:
    """个股域：同为 partial 的日子跳过（不写半截事实）；**绝不**调东财 EOD 增强。"""
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def fake_breadth(db: Any, as_of: date) -> dict[str, int]:
        quoted = 100 if as_of == D2 else 5400
        return {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": quoted}

    calls: list[tuple[date, str]] = []

    async def fake_facts(
        db: Any, as_of: date, *, fact_source: str = "local_calc"
    ) -> dict[str, int]:
        calls.append((as_of, fact_source))
        return {"rows": 178, "is_lu": 75, "is_zb": 39, "prev_is_lu": 95}

    async def forbidden_enrich(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("历史回补不得调用东财 EOD 增强")

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_repo, "fetch_day_breadth", fake_breadth)
    monkeypatch.setattr(limit_up_service, "persist_stock_facts", fake_facts)
    monkeypatch.setattr(limit_up_service, "enrich_stock_facts_from_pool", forbidden_enrich)
    monkeypatch.setattr(limit_up_service, "persist_and_enrich_stock_facts", forbidden_enrich)

    db = _FakeDb()
    summary = await bmh.run_backfill(db, domain=bmh.DOMAIN_LIMIT_UP_STOCKS, start=D1, end=D3)

    assert calls == [(D1, "backfill"), (D3, "backfill")], "升序 + fact_source=backfill"
    assert [o.reason for o in summary.days] == [
        bmh.REASON_OK,
        bmh.REASON_PARTIAL_DAY,
        bmh.REASON_OK,
    ]
    assert summary.rows() == 356
    assert db.commits == 2


async def test_run_backfill_dry_run_writes_nothing(monkeypatch) -> None:
    """`--dry-run` 不写库、不 commit、不进任何落库/增强函数。"""
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> str | None:
        return bmh.REASON_PRICE_LIMITS_MISSING if trade_date == D2 else None

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run 不得调用落库/增强")

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", forbidden)
    monkeypatch.setattr(limit_up_service, "persist_stock_facts", forbidden)
    monkeypatch.setattr(limit_up_service, "persist_and_enrich_stock_facts", forbidden)

    db = _FakeDb()
    summary = await bmh.run_backfill(
        db, domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D3, dry_run=True
    )

    assert db.commits == 0
    assert summary.dry_run is True
    assert [o.reason for o in summary.days] == [
        bmh.REASON_WOULD_WRITE,
        bmh.REASON_PRICE_LIMITS_MISSING,
        bmh.REASON_WOULD_WRITE,
    ]
    assert summary.written_days() == 0, "dry-run 不报写入日"


async def test_dry_run_stock_domain_writes_nothing(monkeypatch) -> None:
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def fake_breadth(db: Any, as_of: date) -> dict[str, int]:
        return {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": 5400}

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry-run 不得调用落库/增强")

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_repo, "fetch_day_breadth", fake_breadth)
    monkeypatch.setattr(limit_up_service, "persist_stock_facts", forbidden)

    db = _FakeDb()
    summary = await bmh.run_backfill(
        db, domain=bmh.DOMAIN_LIMIT_UP_STOCKS, start=D1, end=D3, dry_run=True
    )
    assert db.commits == 0
    assert [o.reason for o in summary.days] == [bmh.REASON_WOULD_WRITE] * 3
    assert summary.rows() == 0


async def test_run_backfill_rerun_converges(monkeypatch) -> None:
    """幂等：连跑两次 → 同一批日、同一原因分布、table 行数不变（回补行被原地修复）。"""
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    table: dict[date, int] = {}

    async def fake_persist(
        db: Any, cache: Any, as_of: date | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        table[as_of] = table.get(as_of, 0) + 1  # type: ignore[index]  # upsert: 行数不变
        return {"status": "ok", "trade_date": as_of, "zt_count": 1}

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", fake_persist)

    first = await bmh.run_backfill(_FakeDb(), domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D3)
    rows_after_first = len(table)
    second = await bmh.run_backfill(_FakeDb(), domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D3)

    assert first.reason_counts() == second.reason_counts() == {bmh.REASON_OK: 3}
    assert [o.trade_date for o in first.days] == [o.trade_date for o in second.days] == DAYS
    assert len(table) == rows_after_first == 3, "重跑必须收敛到同一行集"
    assert sum(table.values()) == 6, "每次调用各 upsert 一次（行数不变）"


async def test_backfill_survives_a_failing_day(monkeypatch) -> None:
    """单日异常必须记录成原因码并 rollback，不得中断整段回补（可中断续跑）。"""
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def flaky(
        db: Any, cache: Any, as_of: date | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if as_of == D2:
            raise RuntimeError("boom")
        return {"status": "ok", "trade_date": as_of, "zt_count": 1}

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", flaky)

    db = _FakeDb()
    summary = await bmh.run_backfill(db, domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D3)

    assert [o.reason for o in summary.days] == [bmh.REASON_OK, bmh.REASON_ERROR, bmh.REASON_OK]
    assert "boom" in (summary.days[1].detail or "")
    assert db.rollbacks == 1 and db.commits == 2
    assert summary.errors() == 1


# ---------------------------------------------------------------------------
# 汇总表 / CLI 契约
# ---------------------------------------------------------------------------


async def test_summary_table_lists_reasons_counts_and_skipped_days(monkeypatch) -> None:
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> str | None:
        return None if trade_date != D2 else bmh.REASON_WINDOW_INCOMPLETE

    async def fake_persist(
        db: Any, cache: Any, as_of: date | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "ok", "trade_date": as_of, "zt_count": 1}

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", fake_persist)

    summary = await bmh.run_backfill(_FakeDb(), domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D3)
    table = summary.format_table()

    assert "domain" in table and str(bmh.DOMAIN_SENTIMENT) in table
    assert bmh.REASON_OK in table and bmh.REASON_WINDOW_INCOMPLETE in table
    assert "2025-10-16" in table, "跳过日必须逐日列原因（差值可解释）"
    assert table.count(bmh.REASON_OK) >= 1


def test_cli_parses_domains_range_and_flags() -> None:
    args = bmh.parse_args(
        [
            "--domain",
            "limit_up_stocks",
            "--from",
            "2025-10-15",
            "--to",
            "2025-10-20",
            "--limit",
            "5",
            "--dry-run",
        ]
    )
    assert args.domain == "limit_up_stocks"
    assert args.from_date == date(2025, 10, 15)
    assert args.to_date == date(2025, 10, 20)
    assert args.limit == 5
    assert args.dry_run is True
    assert bmh.parse_args(["--domain", "sentiment"]).from_date == bmh.DEFAULT_START
    assert bmh.parse_args(["--domain", "sentiment"]).to_date is None
    # 未知域必须直接报错（choices），不能静默 fallback
    with pytest.raises(SystemExit):
        bmh.parse_args(["--domain", "nope"])


# ---------------------------------------------------------------------------
# Ruling 18：回补 upsert 只能修回补行（默认闸门：编译 SQL 断言守卫形状）
# ---------------------------------------------------------------------------


async def test_backfill_sentiment_upsert_is_guarded_to_backfill_rows() -> None:
    """`DO UPDATE ... WHERE market_sentiment_daily.source = 'backfill'`（存储在表里的列）。"""
    session = _CapturingSession()
    await limit_up_repo.upsert_sentiment_daily(session, _sentiment_row(D1), backfill_only=True)
    sql = _compiled(session.statements[0])
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql
    after_conflict = sql.split("ON CONFLICT", 1)[1]
    set_clause, sep, guard = after_conflict.partition("WHERE")
    assert sep, "回补 upsert 必须带 WHERE 守卫"
    assert "source" not in set_clause, "SET 不得含 source —— 否则能把实时行重标成 backfill"
    assert re.search(r"(?:market_sentiment_daily\.)?source = 'backfill'", guard), guard
    assert "excluded.source" not in guard, "守卫必须看**已存储**的 source，不是新值"
    assert "'backfill'" in sql.split("ON CONFLICT", 1)[0], "新行必须以 source=backfill 落库"


async def test_realtime_sentiment_upsert_keeps_existing_behaviour() -> None:
    """实时路径不加守卫（仍是原来的覆写语义），但 SET 依旧排除 source（不重标）。"""
    session = _CapturingSession()
    await limit_up_repo.upsert_sentiment_daily(session, _sentiment_row(D1, source="local_calc"))
    sql = _compiled(session.statements[0])
    after_conflict = sql.split("ON CONFLICT", 1)[1]
    assert "WHERE" not in after_conflict
    assert "source" not in after_conflict.split("DO UPDATE", 1)[1]


def _fact_row(trade_date: date, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trade_date": trade_date,
        "stock_id": 1,
        "symbol": "000001",
        "is_lu": True,
        "touched": True,
        "is_zb": False,
        "prev_is_lu": False,
        "streak": 1,
        "days_span": 1,
        "boards_in_window": 1,
        "missing_days": 0,
        "seal_time": None,
        "seal_fund": None,
        "break_count": None,
        "fact_source": "backfill",
        "enrich_source": None,
    }
    row.update(overrides)
    return row


async def test_backfill_fact_upsert_is_guarded_and_keeps_enrichment_columns() -> None:
    """事实表：守卫看 `fact_source`；SET 仍不含东财列（seal_*/break_count/enrich_source）。"""
    session = _CapturingSession()
    await limit_up_repo.upsert_limit_up_stock_daily(session, [_fact_row(D1)], backfill_only=True)
    sql = _compiled(session.statements[0])
    after_conflict = sql.split("ON CONFLICT", 1)[1]
    set_clause, sep, guard = after_conflict.partition("WHERE")
    assert sep, "回补 upsert 必须带 WHERE 守卫"
    assert re.search(r"(?:market_limit_up_stock_daily\.)?fact_source = 'backfill'", guard), guard
    assert "excluded.fact_source" not in guard, "守卫必须看**已存储**的 fact_source"
    for column in ("seal_time", "seal_fund", "break_count", "enrich_source"):
        assert column not in set_clause, f"回补不得写东财列 {column}"
    assert "is_lu" in set_clause, "判定列仍由回补修复（B 类可回算）"


async def test_persist_stock_facts_backfill_marks_rows_and_guards_upsert(monkeypatch) -> None:
    """service → repo 接线：`fact_source="backfill"` 必须下传成守卫，而不是无守卫覆写。"""
    captured: dict[str, Any] = {}

    async def fake_upsert(
        db: Any, rows: list[dict[str, Any]], *, backfill_only: bool = False
    ) -> int:
        captured["rows"] = rows
        captured["backfill_only"] = backfill_only
        return len(rows)

    async def fake_days(db: Any, as_of: date, limit: int) -> list[date]:
        return [D1, D2]

    async def fake_window(
        db: Any, as_of: date, as_of_prev: date, window_start: date
    ) -> list[dict[str, Any]]:
        return []

    async def fake_broken(db: Any, as_of: date) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(limit_up_repo, "upsert_limit_up_stock_daily", fake_upsert)
    monkeypatch.setattr(limit_up_repo, "list_recent_trade_dates", fake_days)
    monkeypatch.setattr(limit_up_repo, "fetch_limit_up_window", fake_window)
    monkeypatch.setattr(limit_up_repo, "fetch_day_broken_board_stocks", fake_broken)

    await limit_up_service.persist_stock_facts("DB", D2, fact_source="backfill")
    assert captured["backfill_only"] is True

    captured.clear()
    await limit_up_service.persist_stock_facts("DB", D2)
    assert captured["backfill_only"] is False, "实时路径语义不变（仍可覆写）"


async def test_persist_snapshot_backfill_marks_source_and_guards_upsert(monkeypatch) -> None:
    """`persist_snapshot(fact_source="backfill")` → 行 source=backfill + 守卫 + 不增强。"""
    captured: dict[str, Any] = {}
    enrich_calls: list[Any] = []

    async def fake_snapshot(
        cache: Any,
        as_of: date | None = None,
        lookback: int = 0,
        *,
        mode: str = "close",
        enrich: bool = True,
    ) -> dict[str, Any]:
        captured["enrich"] = enrich
        return {
            "as_of": as_of,
            "source": "local_calc",
            "degraded_reason": None,
            "is_partial": False,
            "kpis": {
                "zt_count": 1,
                "dt_count": 0,
                "zb_count": 0,
                "broken_rate": 0.0,
                "yzt_avg_pct": 0.0,
                "promo_1to2": 0.0,
                "promo_1to2_n": 0,
                "promo_2to3": 0.0,
                "promo_2to3_n": 0,
                "max_streak": 1,
            },
            "echelons": [],
        }

    async def fake_upsert(db: Any, row: dict[str, Any], *, backfill_only: bool = False) -> int:
        captured["row"] = row
        captured["backfill_only"] = backfill_only
        return 1

    async def fake_invalidate() -> int:
        return 0

    async def fake_enrich(*args: Any, **kwargs: Any) -> dict[str, Any]:
        enrich_calls.append(args)
        return {}

    monkeypatch.setattr(limit_up_service, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(limit_up_repo, "upsert_sentiment_daily", fake_upsert)
    monkeypatch.setattr(limit_up_service, "_invalidate_calendar_cache", fake_invalidate)
    monkeypatch.setattr(limit_up_service, "enrich_from_web", fake_enrich)

    result = await limit_up_service.persist_snapshot(
        "DB", None, D1, fact_source="backfill", enrich=False
    )
    assert result["status"] == "ok"
    assert captured["row"]["source"] == "backfill"
    assert captured["backfill_only"] is True
    assert captured["enrich"] is False and enrich_calls == []


async def test_persist_snapshot_realtime_defaults_are_unchanged(monkeypatch) -> None:
    """默认参数必须仍是"实时口径"：source=local_calc、无守卫、允许增强。"""
    captured: dict[str, Any] = {}

    async def fake_snapshot(
        cache: Any,
        as_of: date | None = None,
        lookback: int = 0,
        *,
        mode: str = "close",
        enrich: bool = True,
    ) -> dict[str, Any]:
        captured["enrich"] = enrich
        return {
            "as_of": as_of,
            "source": "local_calc",
            "degraded_reason": None,
            "is_partial": False,
            "kpis": {
                "zt_count": 1,
                "dt_count": 0,
                "zb_count": 0,
                "broken_rate": 0.0,
                "yzt_avg_pct": 0.0,
                "promo_1to2": 0.0,
                "promo_1to2_n": 0,
                "promo_2to3": 0.0,
                "promo_2to3_n": 0,
                "max_streak": 1,
            },
            "echelons": [],
        }

    async def fake_upsert(db: Any, row: dict[str, Any], *, backfill_only: bool = False) -> int:
        captured["row"] = row
        captured["backfill_only"] = backfill_only
        return 1

    async def fake_invalidate() -> int:
        return 0

    monkeypatch.setattr(limit_up_service, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(limit_up_repo, "upsert_sentiment_daily", fake_upsert)
    monkeypatch.setattr(limit_up_service, "_invalidate_calendar_cache", fake_invalidate)

    await limit_up_service.persist_snapshot("DB", None, D1)
    assert captured["row"]["source"] == "local_calc"
    assert captured["backfill_only"] is False
    assert captured["enrich"] is True


async def test_sentiment_degraded_gates_still_skip(monkeypatch) -> None:
    """回补不得绕过既有降级闸门（partial_day / no_limit_up_rows 一律不写）。"""

    async def fake_snapshot(cache: Any, as_of: date | None = None, **kwargs: Any) -> dict[str, Any]:
        return {"as_of": as_of, "is_partial": True, "degraded_reason": "partial_day"}

    async def forbidden(*args: Any, **kwargs: Any) -> int:
        raise AssertionError("降级日不得写库")

    monkeypatch.setattr(limit_up_service, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(limit_up_repo, "upsert_sentiment_daily", forbidden)

    result = await limit_up_service.persist_snapshot(
        "DB", None, D1, fact_source="backfill", enrich=False
    )
    assert result == {"status": "skipped", "reason": "partial_day"}


# ---------------------------------------------------------------------------
# 真库（`-m e2e`）：Ruling 18 的 SQL 语义
# ---------------------------------------------------------------------------


@pytest.fixture
async def _clean_sentinel_days() -> Any:
    """哨兵日（2000-01-03/04）自建自清；失败路径也不留脏行。"""
    from sqlalchemy import text

    from app.core.database import async_session_factory, engine

    async def _delete() -> None:
        async with async_session_factory() as db:
            await db.execute(
                text("DELETE FROM market_sentiment_daily WHERE trade_date IN (:a, :b)"),
                {"a": SENTINEL_REALTIME, "b": SENTINEL_BACKFILL},
            )
            await db.execute(
                text("DELETE FROM market_limit_up_stock_daily WHERE trade_date IN (:a, :b)"),
                {"a": SENTINEL_REALTIME, "b": SENTINEL_BACKFILL},
            )
            await db.commit()

    await _delete()
    try:
        yield
    finally:
        await _delete()
        await engine.dispose()


@pytest.mark.e2e
async def test_backfill_upsert_never_relabels_realtime_sentiment_row(
    _clean_sentinel_days: Any,
) -> None:
    """真库：source=local_calc 的行被回补 upsert 逐字节拒绝，回补行才可被修复。"""
    from sqlalchemy import text

    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        await limit_up_repo.upsert_sentiment_daily(
            db, _sentiment_row(SENTINEL_REALTIME, source="local_calc", zt_count=7)
        )
        await limit_up_repo.upsert_sentiment_daily(
            db, _sentiment_row(SENTINEL_BACKFILL, zt_count=7)
        )
        await db.commit()

        before = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM market_sentiment_daily WHERE trade_date IN (:a, :b)"
                        " ORDER BY trade_date"
                    ),
                    {"a": SENTINEL_REALTIME, "b": SENTINEL_BACKFILL},
                )
            )
            .mappings()
            .all()
        )
        assert len(before) == 2

        # 回补尝试覆写两天：实时行必须纹丝不动，回补行必须被修好
        await limit_up_repo.upsert_sentiment_daily(
            db,
            _sentiment_row(SENTINEL_REALTIME, source="backfill", zt_count=999),
            backfill_only=True,
        )
        await limit_up_repo.upsert_sentiment_daily(
            db,
            _sentiment_row(SENTINEL_BACKFILL, source="backfill", zt_count=999),
            backfill_only=True,
        )
        await db.commit()

        after = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM market_sentiment_daily WHERE trade_date IN (:a, :b)"
                        " ORDER BY trade_date"
                    ),
                    {"a": SENTINEL_REALTIME, "b": SENTINEL_BACKFILL},
                )
            )
            .mappings()
            .all()
        )

    realtime_before, backfill_before = before
    realtime_after, backfill_after = after
    assert realtime_after == realtime_before, "实时行被回补改写了（Ruling 18 违规）"
    assert realtime_after["source"] == "local_calc" and realtime_after["zt_count"] == 7
    assert backfill_after["zt_count"] == 999, "回补行必须可被回补修复"
    assert backfill_before["zt_count"] == 7


@pytest.mark.e2e
async def test_backfill_upsert_never_touches_realtime_fact_row(
    _clean_sentinel_days: Any,
) -> None:
    """真库：fact_source=local_calc 的行（含东财增强列）不得被回补改写或重标。"""
    from sqlalchemy import text

    from app.core.database import async_session_factory

    realtime_row = _fact_row(
        SENTINEL_REALTIME,
        fact_source="local_calc",
        seal_time="09:31:00",
        seal_fund=Decimal("1234.5000"),
        break_count=2,
        enrich_source="eastmoney_eod",
    )
    async with async_session_factory() as db:
        await limit_up_repo.upsert_limit_up_stock_daily(db, [realtime_row])
        await db.commit()
        before = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM market_limit_up_stock_daily"
                        " WHERE trade_date IN (:a, :b) ORDER BY trade_date"
                    ),
                    {"a": SENTINEL_REALTIME, "b": SENTINEL_BACKFILL},
                )
            )
            .mappings()
            .all()
        )[0]

        # 回补尝试"修"实时行（is_lu 反转 + 重标 fact_source）→ 必须被守卫拒绝
        await limit_up_repo.upsert_limit_up_stock_daily(
            db,
            [
                _fact_row(
                    SENTINEL_REALTIME, is_lu=False, is_zb=True, streak=0, fact_source="backfill"
                )
            ],
            backfill_only=True,
        )
        await db.commit()
        after = (
            (
                await db.execute(
                    text(
                        "SELECT * FROM market_limit_up_stock_daily"
                        " WHERE trade_date IN (:a, :b) ORDER BY trade_date"
                    ),
                    {"a": SENTINEL_REALTIME, "b": SENTINEL_BACKFILL},
                )
            )
            .mappings()
            .all()
        )[0]

    assert after == before, "实时事实行被回补改写了（Ruling 18 违规）"
    assert after["fact_source"] == "local_calc" and after["enrich_source"] == "eastmoney_eod"
    assert after["is_lu"] is True and after["streak"] == 1
    assert after["seal_time"] == "09:31:00"


@pytest.mark.e2e
async def test_backfill_can_repair_its_own_rows(_clean_sentinel_days: Any) -> None:
    """反向守卫：回补行必须真的能被第二次回补修复（否则"幂等"变成"写不动"）。"""
    from sqlalchemy import text

    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        await limit_up_repo.upsert_limit_up_stock_daily(
            db, [_fact_row(SENTINEL_BACKFILL, fact_source="backfill")]
        )
        await db.commit()
        updated = await limit_up_repo.upsert_limit_up_stock_daily(
            db,
            [_fact_row(SENTINEL_BACKFILL, fact_source="backfill", streak=9, missing_days=3)],
            backfill_only=True,
        )
        await db.commit()
        row = (
            (
                await db.execute(
                    text("SELECT * FROM market_limit_up_stock_daily WHERE trade_date = :a"),
                    {"a": SENTINEL_BACKFILL},
                )
            )
            .mappings()
            .one()
        )
    assert updated == 1
    assert row["streak"] == 9 and row["missing_days"] == 3
    assert row["fact_source"] == "backfill"
