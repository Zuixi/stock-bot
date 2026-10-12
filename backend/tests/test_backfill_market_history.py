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


def _patch_window(
    monkeypatch: Any,
    days: list[date],
    missing: set[date],
    *,
    spy: list[list[date]] | None = None,
) -> None:
    """替身**必须真的读它收到的参数**，否则用例无法否证被点名的性质（评审 Important #2）。

    - `fake_list` 断言 `limit` 与 `as_of`（守卫必须为**该日**取窗）；
    - `fake_missing` 把收到的 `dates` 记进 `spy`，并按收到的集合算"缺限价"。
    于是把实现回退成 `missing_price_limit_dates(db, [as_of])`（E2 要防的那个 bug）时：
    `spy != [window]` 直接红；且只查 `as_of` 的调用会把"缺限价的窗口起点"看不见 →
    true/false 两个方向都咬得住。
    """

    async def fake_list(db: Any, as_of: date, limit: int) -> list[date]:
        assert limit == limit_up_service.window_trade_days(calc.LOOKBACK_TRADE_DAYS)
        expected_as_of = days[-1] if days else None
        assert as_of == expected_as_of, f"窗口必须为 as_of={expected_as_of} 取，收到 {as_of}"
        return days

    async def fake_missing(db: Any, dates: list[date]) -> list[date]:
        if spy is not None:
            spy.append(list(dates))
        return [d for d in dates if d in missing]

    monkeypatch.setattr(limit_up_repo, "list_recent_trade_dates", fake_list)
    monkeypatch.setattr(limit_up_repo, "missing_price_limit_dates", fake_missing)


async def test_window_limits_complete_true_when_every_window_day_has_limits(monkeypatch) -> None:
    window = [date(2025, 9, 16) + timedelta(days=i) for i in range(16)]
    spy: list[list[date]] = []
    _patch_window(monkeypatch, window, set(), spy=spy)
    assert await limit_up_service.window_limits_complete("DB", window[-1]) is True
    assert spy == [window], "缺失检查必须收到**整段窗口**，而不是只有 as_of"


async def test_window_limits_complete_false_when_any_window_day_lacks_limits(monkeypatch) -> None:
    """不是只看目标日：窗口**起点**缺限价（限价上线前的日子）也必须判不完备。

    缺失日**只通过全窗查询可见**（`fake_missing` 按收到的 dates 求交）——只查 `as_of`
    的实现会返回"无缺失"→ True → 本用例红。
    """
    window = [date(2025, 9, 16) + timedelta(days=i) for i in range(16)]
    spy: list[list[date]] = []
    _patch_window(monkeypatch, window, {window[0]}, spy=spy)
    assert await limit_up_service.window_limits_complete("DB", window[-1]) is False
    assert spy == [window], "缺失检查必须收到**整段窗口**，而不是只有 as_of"


async def test_window_limits_complete_false_when_window_is_short(monkeypatch) -> None:
    """窗口凑不满（数据起点附近）→ 不完备：截断窗口会把 streak 拉平。"""
    window = [date(2025, 10, 15) + timedelta(days=i) for i in range(2)]
    _patch_window(monkeypatch, window, set())
    assert await limit_up_service.window_limits_complete("DB", window[-1]) is False


async def test_window_limits_complete_false_when_as_of_is_not_the_window_end(monkeypatch) -> None:
    """as_of 自己不是窗口末日（无行情/非交易日）→ 不完备。"""
    window = [date(2025, 9, 16) + timedelta(days=i) for i in range(16)]

    async def fake_list(db: Any, as_of: date, limit: int) -> list[date]:
        return window  # 窗口末日 != as_of，故不能用会断言 as_of 的 _patch_window

    async def fake_missing(db: Any, dates: list[date]) -> list[date]:
        return []

    monkeypatch.setattr(limit_up_repo, "list_recent_trade_dates", fake_list)
    monkeypatch.setattr(limit_up_repo, "missing_price_limit_dates", fake_missing)
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


async def test_run_backfill_reports_guard_rejected_day_as_kept_realtime(monkeypatch) -> None:
    """评审 Important #1：被 Ruling 18 守卫拒绝的日**不得**报成 `ok`。

    场景：D1 已有实时行（`local_calc`，守卫拒改 → `upserted=0`）、D2 是真回补（`upserted=1`）。
    → 逐日原因 `[kept_realtime, ok]`，`written_days` 只数 1 天，独立计数列
    `skipped_kept_realtime=1`，`rows` 只数**实际受影响**的行（1，不是 2）。
    """
    _patch_days(monkeypatch, [D1, D2])

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def fake_persist(
        db: Any, cache: Any, as_of: date | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if as_of == D1:  # 已存在实时行 → 守卫让 rowcount 归零
            return {
                "status": "ok",
                "trade_date": as_of,
                "zt_count": 7,
                "upserted": 0,
                "kept_realtime": True,
            }
        return {
            "status": "ok",
            "trade_date": as_of,
            "zt_count": 7,
            "upserted": 1,
            "kept_realtime": False,
        }

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", fake_persist)

    summary = await bmh.run_backfill(_FakeDb(), domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D2)

    assert [o.reason for o in summary.days] == [bmh.REASON_KEPT_REALTIME, bmh.REASON_OK]
    assert summary.written_days() == 1, "被守卫拒绝的日不得计入 written_days"
    assert summary.skipped_kept_realtime() == 1
    assert summary.rows() == 1, "rows 必须是实际受影响行数（0 + 1）"
    table = summary.format_table()
    assert bmh.REASON_KEPT_REALTIME in table
    assert "skipped_kept_realtime=1" in table and "written_days=1" in table
    assert f"{D1.isoformat()}  {bmh.REASON_KEPT_REALTIME}" in table, "被拒日要逐日列出来"


async def test_run_backfill_stock_day_partial_guard_rejection_is_kept_realtime(
    monkeypatch,
) -> None:
    """个股域：行集里只要有行被守卫保留（`upserted < rows`）→ 整日 `kept_realtime`。"""
    _patch_days(monkeypatch, [D1, D2])

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def fake_breadth(db: Any, as_of: date) -> dict[str, int]:
        return {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": 5400}

    async def fake_facts(db: Any, as_of: date, **kwargs: Any) -> dict[str, Any]:
        if as_of == D1:  # 150 行已存在实时行、28 行新写 → 部分被拒
            return {
                "rows": 178,
                "is_lu": 75,
                "is_zb": 39,
                "prev_is_lu": 95,
                "upserted": 28,
                "kept_realtime": True,
            }
        return {
            "rows": 178,
            "is_lu": 75,
            "is_zb": 39,
            "prev_is_lu": 95,
            "upserted": 178,
            "kept_realtime": False,
        }

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_repo, "fetch_day_breadth", fake_breadth)
    monkeypatch.setattr(limit_up_service, "persist_stock_facts", fake_facts)

    summary = await bmh.run_backfill(_FakeDb(), domain=bmh.DOMAIN_LIMIT_UP_STOCKS, start=D1, end=D2)

    assert [o.reason for o in summary.days] == [bmh.REASON_KEPT_REALTIME, bmh.REASON_OK]
    assert summary.written_days() == 1 and summary.skipped_kept_realtime() == 1
    assert summary.rows() == 28 + 178, "rows 必须是 rowcount 之和（被拒的行不算）"
    assert "upserted=28/178" in (summary.days[0].detail or "")


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


class _CommitFailsOn(_FakeDb):
    """`commit()` 在第 N 天失败（Task 8：提交失败必须与写入失败同款隔离）。"""

    def __init__(self, trade_dates: list[date] | None = None, *, fail_on_commit: int = 2) -> None:
        super().__init__(trade_dates)
        self.fail_on_commit = fail_on_commit

    async def commit(self) -> None:
        self.commits += 1
        if self.commits == self.fail_on_commit:
            raise RuntimeError("commit boom")


async def test_backfill_survives_a_failing_commit(monkeypatch) -> None:
    """单日 `commit()` 失败 → 该日记 `error` + rollback，**不**中断整段（Task 8 修）。

    修前 `db.commit()` 在 `try` 之外 → 提交异常直接冒泡出 `run_backfill`，整段回补崩在
    第二天（脚本承诺的 per-day 隔离在"提交"这一步上不成立）。
    """
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def fake_persist(
        db: Any, cache: Any, as_of: date | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        return {"status": "ok", "trade_date": as_of, "zt_count": 1}

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_service, "persist_snapshot", fake_persist)

    db = _CommitFailsOn(fail_on_commit=2)
    summary = await bmh.run_backfill(db, domain=bmh.DOMAIN_SENTIMENT, start=D1, end=D3)

    assert [o.reason for o in summary.days] == [bmh.REASON_OK, bmh.REASON_ERROR, bmh.REASON_OK]
    assert "commit boom" in (summary.days[1].detail or "")
    assert db.rollbacks == 1 and db.commits == 3  # 失败那次也计入（提交被真正尝试过）
    assert summary.errors() == 1


async def test_backfill_stock_survives_a_failing_commit(monkeypatch) -> None:
    """个股事实域同一契约：`commit()` 失败被记成 `error` + rollback，不中断。"""
    _patch_days(monkeypatch, DAYS)

    async def fake_review(db: Any, trade_date: date, lookback: int = 0) -> None:
        return None

    async def fake_breadth(db: Any, trade_date: date) -> dict[str, Any]:
        return {"quoted": 5500}

    async def fake_facts(db: Any, as_of: date, **kwargs: Any) -> dict[str, Any]:
        return {"rows": 10, "upserted": 10, "is_lu": 3, "is_zb": 1, "prev_is_lu": 2}

    monkeypatch.setattr(bmh, "review_day", fake_review)
    monkeypatch.setattr(limit_up_repo, "fetch_day_breadth", fake_breadth)
    monkeypatch.setattr(limit_up_service, "persist_stock_facts", fake_facts)

    db = _CommitFailsOn(fail_on_commit=2)
    summary = await bmh.run_backfill(db, domain=bmh.DOMAIN_LIMIT_UP_STOCKS, start=D1, end=D3)

    assert [o.reason for o in summary.days] == [bmh.REASON_OK, bmh.REASON_ERROR, bmh.REASON_OK]
    assert "commit boom" in (summary.days[1].detail or "")
    assert db.rollbacks == 1 and db.commits == 3
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


async def test_backfill_only_rejects_local_calc_row_before_any_sql() -> None:
    """`backfill_only=True` 必须**拒绝**非回补来源的行（最终评审 Important 2）。

    `ON CONFLICT ... WHERE` 只约束 `DO UPDATE` 分支；插入分支不受约束 —— 一次"回补"批里
    若行字典把 `source` 写成 `local_calc`，就会 INSERT 出**实时行**，从而永久免疫 Ruling 18
    的修复（守卫只修 `backfill` 行）。本用例钉住 fail-fast：抛错且**一条 SQL 都不发**。
    变异：删掉 `upsert_sentiment_daily` 里的来源校验 → 本例变红（静默编译出 INSERT）。
    """
    session = _CapturingSession()
    with pytest.raises(ValueError, match="非回补来源"):
        await limit_up_repo.upsert_sentiment_daily(
            session, _sentiment_row(D1, source="local_calc"), backfill_only=True
        )
    assert session.statements == [], "拒绝必须发生在编译/执行之前（不得静默插脏行）"


async def test_backfill_only_rejects_mixed_source_fact_rows() -> None:
    """事实表同款：一批里混进一行 `fact_source='local_calc'` → 整批拒绝、零 SQL。"""
    session = _CapturingSession()
    rows = [_fact_row(D1), _fact_row(D2, stock_id=2, fact_source="local_calc")]
    with pytest.raises(ValueError, match="非回补来源"):
        await limit_up_repo.upsert_limit_up_stock_daily(session, rows, backfill_only=True)
    assert session.statements == []


async def test_realtime_fact_upsert_still_accepts_local_calc_rows() -> None:
    """对照：实时路径（`backfill_only=False`）必须照旧接受 `local_calc` 行。"""
    session = _CapturingSession()
    await limit_up_repo.upsert_limit_up_stock_daily(
        session, [_fact_row(D1, fact_source="local_calc")], backfill_only=False
    )
    assert len(session.statements) == 1


# ---------------------------------------------------------------------------
# 退出码：一天都没写成（全被守卫拒绝）必须非 0
# ---------------------------------------------------------------------------


def _summary_with(*outcomes: bmh.DayOutcome, dry_run: bool = False) -> bmh.BackfillSummary:
    return bmh.BackfillSummary(
        domain=bmh.DOMAIN_SENTIMENT,
        dry_run=dry_run,
        from_date=D1,
        to_date=D3,
        days=list(outcomes),
        elapsed_s=0.1,
    )


def test_exit_code_ok_when_something_was_written() -> None:
    summary = _summary_with(
        bmh.DayOutcome(D1, bmh.REASON_OK),
        bmh.DayOutcome(D2, bmh.REASON_KEPT_REALTIME),
    )
    assert (summary.written_days(), summary.skipped_kept_realtime()) == (1, 1)
    assert bmh.exit_code(summary) == 0


def test_exit_code_nonzero_when_every_day_was_guarded_out() -> None:
    """最终评审 Important 4：`written_days==0` 且 `skipped_kept_realtime>0` → 退出码 1。

    修前 `main()` 恒 `return 1 if summary.errors() else 0`（只看 errors），运维/CI 会把
    "一行都没回补"当成功 —— 这条钉住失败语义。
    变异：把 `exit_code` 回退成 `1 if summary.errors() else 0` → 本例变红。
    """
    summary = _summary_with(
        bmh.DayOutcome(D1, bmh.REASON_KEPT_REALTIME),
        bmh.DayOutcome(D2, bmh.REASON_KEPT_REALTIME),
    )
    assert summary.errors() == 0
    assert bmh.exit_code(summary) == 1


def test_exit_code_still_zero_for_a_truly_empty_but_clean_run() -> None:
    """对照：既没写成、也不是"被守卫拒绝"（如整体窗口不完备）→ 不新增失败语义。"""
    summary = _summary_with(
        bmh.DayOutcome(D1, bmh.REASON_WINDOW_INCOMPLETE),
        bmh.DayOutcome(D2, bmh.REASON_WINDOW_INCOMPLETE),
    )
    assert summary.skipped_kept_realtime() == 0
    assert bmh.exit_code(summary) == 0


def test_exit_code_dry_run_is_unaffected() -> None:
    """preview 路径：全部 `would_write`（没有真的写库）不得被判失败。"""
    summary = _summary_with(
        *(bmh.DayOutcome(d, bmh.REASON_WOULD_WRITE) for d in (D1, D2, D3)), dry_run=True
    )
    assert summary.written_days() == 0
    assert bmh.exit_code(summary) == 0


async def test_main_returns_exit_code_from_summary(monkeypatch, capsys) -> None:
    """`main()` 必须把 `exit_code()` 的结果当作进程退出码（接线，不只是纯函数）。"""
    summary = _summary_with(bmh.DayOutcome(D1, bmh.REASON_KEPT_REALTIME))

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def fake_latest(db: Any) -> date:
        return D3

    async def fake_run(db: Any, **kw: Any) -> bmh.BackfillSummary:
        return summary

    monkeypatch.setattr(bmh, "async_session_factory", _Ctx)
    monkeypatch.setattr(limit_up_repo, "latest_quote_date", fake_latest)
    monkeypatch.setattr(bmh, "run_backfill", fake_run)

    code = await bmh.main(bmh.parse_args(["--domain", "sentiment"]))
    assert code == 1
    assert "kept_realtime" in capsys.readouterr().out


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
    # 实际受影响行数必须透上来（评审 Important #1）：只有真写了才算 ok
    assert result["upserted"] == 1 and result["kept_realtime"] is False


async def test_persist_snapshot_reports_kept_realtime_when_guard_blocks(monkeypatch) -> None:
    """守卫拒改（rowcount=0）→ `upserted=0` + `kept_realtime=True`，不得谎报成功写入。

    这条是「脚本把被守卫拒绝的日报成 ok」的根因接线测试：service 层丢掉 rowcount，
    上层就永远无法区分"写了"与"被拒绝"。
    """
    captured: dict[str, Any] = {}

    async def fake_snapshot(cache: Any, as_of: date | None = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "as_of": as_of,
            "source": "local_calc",
            "degraded_reason": None,
            "is_partial": False,
            "kpis": {
                "zt_count": 39,
                "dt_count": 0,
                "zb_count": 1,
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
        captured["backfill_only"] = backfill_only
        return 0  # PG：ON CONFLICT DO UPDATE ... WHERE false → rowcount 0

    async def fake_invalidate() -> int:
        return 0

    monkeypatch.setattr(limit_up_service, "get_snapshot", fake_snapshot)
    monkeypatch.setattr(limit_up_repo, "upsert_sentiment_daily", fake_upsert)
    monkeypatch.setattr(limit_up_service, "_invalidate_calendar_cache", fake_invalidate)

    result = await limit_up_service.persist_snapshot(
        "DB", None, D1, fact_source="backfill", enrich=False
    )
    assert captured["backfill_only"] is True
    assert result["upserted"] == 0 and result["kept_realtime"] is True


async def test_persist_stock_facts_surfaces_guard_rejections(monkeypatch) -> None:
    """个股事实：`upserted < rows` → `kept_realtime=True`（整日被拒时 `upserted=0`）。"""
    monkeypatch.setattr(
        limit_up_service,
        "build_stock_fact_rows",
        lambda *a, **k: [{"is_lu": True, "is_zb": False, "prev_is_lu": True}],
    )

    async def fake_days(db: Any, as_of: date, limit: int) -> list[date]:
        return [D1, D2]

    async def fake_window(db: Any, as_of: date, as_of_prev: date, window_start: date) -> list[Any]:
        return []

    async def fake_broken(db: Any, as_of: date) -> list[Any]:
        return []

    monkeypatch.setattr(limit_up_repo, "list_recent_trade_dates", fake_days)
    monkeypatch.setattr(limit_up_repo, "fetch_limit_up_window", fake_window)
    monkeypatch.setattr(limit_up_repo, "fetch_day_broken_board_stocks", fake_broken)

    state = {"upserted": 0}

    async def fake_upsert(db: Any, rows: list[Any], *, backfill_only: bool = False) -> int:
        return state["upserted"]

    monkeypatch.setattr(limit_up_repo, "upsert_limit_up_stock_daily", fake_upsert)

    blocked = await limit_up_service.persist_stock_facts("DB", D2, fact_source="backfill")
    assert blocked["rows"] == 1 and blocked["upserted"] == 0
    assert blocked["kept_realtime"] is True

    state["upserted"] = 1
    written = await limit_up_service.persist_stock_facts("DB", D2, fact_source="backfill")
    assert written["upserted"] == 1 and written["kept_realtime"] is False


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
# 真库（`-m e2e`）：Ruling 18 的 SQL 语义 + 情绪↔事实全量不变式
# ---------------------------------------------------------------------------


# 全量不变式（T4 收口）：情绪聚合 vs 个股事实逐日对拍。
# 动机 = T4 真库验收发现的现状：21 个既有 `local_calc` 日里 **4 天**（2026-09-10/11/14/15）
# 的存量聚合小于"用完整数据重算"的值。根因是**写入时输入未全**，不是口径变化——
# 实测该 4 天的差值恰等于 2026-09-18 08:31/08:34 才落库的行情/限价行中今日判为 zt/zb
# 的行数（09-10 +1 zt、09-11 +1 zt、09-14 +2 zt +1 zb、09-15 +1 zb），而
# `_BREADTH_SQL` 的 zt/zb 谓词自 2026-09-14 引入后**逐字未变**（`git log -S` 证据）。
# 这 4 天已按"纯派生缓存可重建"的模型契约显式实时重算（见 task-4-report.md 修复轮 1）。
_FULL_INVARIANT_SQL = """
SELECT s.trade_date, s.zt_count, s.zb_count, s.source,
       f.rows AS fact_rows, f.is_lu, f.is_zb
FROM market_sentiment_daily s
LEFT JOIN (
    SELECT trade_date,
           count(*) AS rows,
           count(*) FILTER (WHERE is_lu) AS is_lu,
           count(*) FILTER (WHERE is_zb) AS is_zb
    FROM market_limit_up_stock_daily
    GROUP BY trade_date
) f USING (trade_date)
ORDER BY s.trade_date
"""


@pytest.mark.e2e
async def test_sentiment_daily_matches_fact_counts_for_every_day() -> None:
    """全量不变式：`market_sentiment_daily` **每一天**都要 `count(is_lu)==zt_count`
    且 `count(is_zb)==zb_count`（一条 SQL 对拍两张表）。

    - **零行的日子显式报 `missing_fact_rows`**，不得静默跳过：静默跳过正是"情绪行在、
      个股事实缺"这种洞能长期不被发现的原因（同 Task 3 覆盖率巡检的口径）。
    - 只按某一天（如 2026-09-08）打桩会让"其余 238 天悄悄漂移"漏网 —— 这正是本用例
      取代"单日不变式"的理由（`test_limit_up_stock_daily.py` 的验收日用例保留，
      覆盖 is_lu/zb 与行集构成的更细断言）。
    """
    from sqlalchemy import text

    from app.core.database import async_session_factory, engine

    try:
        async with async_session_factory() as db:
            rows = (await db.execute(text(_FULL_INVARIANT_SQL))).mappings().all()
    finally:
        # 与 `_clean_sentinel_days` 同款：engine 是**全局**的，pytest-asyncio 每个用例一个
        # 事件循环 —— 不 dispose 就会把上一个循环的连接留给下个用例（`Event loop is closed`）。
        await engine.dispose()

    assert rows, "market_sentiment_daily 为空 —— 全量不变式无从谈起（先跑回补）"
    missing = [r["trade_date"] for r in rows if not r["fact_rows"]]
    assert missing == [], (
        f"missing_fact_rows（有情绪行但个股事实为 0 行）：{[str(d) for d in missing]}"
    )
    mismatched = [
        (
            str(r["trade_date"]),
            r["zt_count"],
            r["is_lu"],
            r["zb_count"],
            r["is_zb"],
            r["source"],
        )
        for r in rows
        if r["is_lu"] != r["zt_count"] or r["is_zb"] != r["zb_count"]
    ]
    assert mismatched == [], (
        f"情绪聚合与个股事实不一致 (date, zt, is_lu, zb, is_zb, source)：{mismatched}"
    )


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
        rejected_rows = await limit_up_repo.upsert_sentiment_daily(
            db,
            _sentiment_row(SENTINEL_REALTIME, source="backfill", zt_count=999),
            backfill_only=True,
        )
        repaired_rows = await limit_up_repo.upsert_sentiment_daily(
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
    # 真库 rowcount 语义：守卫拒改 → 0（评审 Important #1 的依据），回补行 → 1
    assert rejected_rows == 0, "被守卫拒绝的实时行 rowcount 必须是 0"
    assert repaired_rows == 1, "回补行必须真的被更新（rowcount=1）"
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
        rejected_rows = await limit_up_repo.upsert_limit_up_stock_daily(
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

    assert rejected_rows == 0, "被守卫拒绝的实时事实行 rowcount 必须是 0"
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
