"""Task 3：覆盖率只读巡检（`/market/data-freshness` 的 `coverage`）。

为什么：`DOMAINS` 只有 4 个域 → 65% / 54.5% 这类洞**当天不告警**；但补漏循环硬编码
3 域（`reconciliation_service.py:228`）+ `sentiment` 特例（`:264`），"把新域塞进 DOMAINS"
只会得到假绿。所以覆盖率是**新增的只读聚合**，与 apply 补漏路径零交集。

本文件覆盖三层：
1. 纯函数 ``compute_coverage``：分母为 0（不产生 0/0）、窗口外的未来脏行、缺日列表、
   ``have`` vs ``complete`` 分叉（Ruling 13）。
2. ``coverage_report``：表/列缺失不得 500（打桩成 ``missing_table`` / ``missing_column``，
   **不真删表**）、每域分母 = 该域自身区间内 ``daily_quotes`` 的交易日、只读。
3. ``/market/data-freshness``：`coverage` 经 schema 透出（additive），端点仍 `apply=False`。

测试风格：离线打桩（假 db 会话按语句形态分派），默认门禁不碰 PG / Redis / 网络。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.exc import ProgrammingError

from app.services import reconciliation_service as rc

# 2026-09 第二/三周：14(一) 15(二) 16(三) 17(四) 18(五) 21(一) 22(二)
D14, D15, D16, D17, D18, D21, D22 = (date(2026, 9, d) for d in (14, 15, 16, 17, 18, 21, 22))
D19, D25 = date(2026, 9, 19), date(2026, 9, 25)  # 周六、下一个周五（脏行候选）
ALL_DAYS = [D14, D15, D16, D17, D18, D21, D22]

#: 巡检域 → (表, 业务日期列)；与 `INSPECT_DOMAINS` 同源断言，防"加了域却没人查"
INSPECT_SOURCES = {
    "sector_moneyflow": ("sector_moneyflow_snapshots", "trade_date"),
    "sentiment_daily": ("market_sentiment_daily", "trade_date"),
    "sentiment_intraday": ("market_sentiment_intraday", "trade_date"),
    "limit_up_stock_daily": ("market_limit_up_stock_daily", "trade_date"),
    "dragon_tiger": ("dragon_tiger_entries", "trade_date"),
    "block_trade": ("block_trades", "trade_date"),
    "share_float": ("share_floats", "float_date"),
    "repurchase": ("stock_repurchases", "ann_date"),
}


# ------------------------------------------------------------------ 假 db 会话


def _sql(stmt: Any) -> str:
    """语句 → 可断言文本：**内联绑定值**（`str(select)` 只给 `:param` 占位符，
    参数值如 `'eod_full'` 根本不出现在文本里，断言会假绿）。"""
    try:
        return str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    except Exception:  # noqa: BLE001 — 编译不了的桩语句退回原文本
        return str(stmt).lower()


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._rows[0][0] if self._rows else None


class _FakeDB:
    """按语句里的表名/列名分派打桩结果；`fail` 让某张表的查询抛 UndefinedTable/Column。

    每个域的查询包在 SAVEPOINT（`begin_nested`）里，失败后 rollback 到保存点即可继续 ——
    本替身忠实实现这一语义，缺表用例才能证明"某域炸了其余域照样渲染"。
    """

    def __init__(self, results: dict[str, list[tuple[Any, ...]]]) -> None:
        self.results = results
        self.fail: dict[str, Exception] = {}
        self.statements: list[str] = []
        self.rollbacks = 0

    async def execute(self, stmt: Any) -> _Result:
        sql = _sql(stmt)
        self.statements.append(sql)
        for table, exc in self.fail.items():
            if table in sql:
                raise exc
        for table, rows in self.results.items():
            if table in sql:
                return _Result(rows)
        raise AssertionError(f"unexpected statement in coverage test: {sql[:160]}")

    def begin_nested(self) -> Any:
        return _Savepoint(self)

    async def rollback(self) -> None:
        self.rollbacks += 1


class _Savepoint:
    def __init__(self, db: _FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> _Savepoint:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _missing(name: str, *, code: str) -> ProgrammingError:
    """构造 asyncpg 风格缺表/缺列异常（真正判别的是 ``sqlstate``/消息）。"""
    kind = "UndefinedTableError" if code == "42P01" else "UndefinedColumnError"
    exc = ProgrammingError(f"boom ({kind}) {name}", None, Exception(f"{kind}: {name}"))
    exc.sqlstate = code  # type: ignore[attr-defined]
    return exc


# ------------------------------------------------------------------ 1. 纯函数


def test_compute_coverage_full_window() -> None:
    out = rc.compute_coverage(ALL_DAYS, set(ALL_DAYS))
    assert out["have_days"] == len(ALL_DAYS)
    assert out["trading_days"] == len(ALL_DAYS)
    assert out["complete_days"] == len(ALL_DAYS)
    assert out["cover_pct"] == 100.0
    assert out["missing_days"] == []


def test_compute_coverage_empty_denominator_is_none_not_zero() -> None:
    """分母为 0 时 cover_pct 必须是 None —— 0/0 渲染成 0% 会把"没数据"说成"全缺"。"""
    out = rc.compute_coverage([], {D14})
    assert out["cover_pct"] is None
    assert out["trading_days"] == 0
    assert out["have_days"] == 0  # 窗口外（无交易日）的行不计入分子
    assert out["missing_days"] == []
    assert out["extra_days"] == [D14.isoformat()]  # 但不静默丢弃：单列出来


def test_compute_coverage_ignores_dirty_rows_outside_window() -> None:
    """未来脏行（周六/节假日/口径外的行）不得抬高分子，也不得伪造缺日。"""
    out = rc.compute_coverage(ALL_DAYS, {D14, D19, D25})
    assert out["have_days"] == 1
    assert out["trading_days"] == 7
    assert out["cover_pct"] == 14.29
    assert out["missing_days"] == [d.isoformat() for d in ALL_DAYS[1:]]
    assert out["extra_days"] == [D19.isoformat(), D25.isoformat()]  # 升序


def test_compute_coverage_missing_days_are_sorted_ascending() -> None:
    """缺日列表升序，且只覆盖该域自身的 [first_day, last_day]（Ruling 3）。"""
    out = rc.compute_coverage(ALL_DAYS, {D16, D22})
    assert out["trading_days"] == 5  # 16/17/18/21/22：早于 D16 的日子不属于本域窗口
    assert out["have_days"] == 2
    assert out["missing_days"] == [D17.isoformat(), D18.isoformat(), D21.isoformat()]
    assert out["extra_days"] == []
    assert out["missing_days"] == sorted(out["missing_days"])


def test_compute_coverage_restricts_window_to_own_data_range() -> None:
    """分母只取"该域自己数据的 [first_day, last_day] 内的交易日"——域内缺日才算洞。

    该域只有 09-14 与 09-16 两天有行 → 窗口 = [09-14, 09-16] → 分母 3（14/15/16），
    09-17 及之后**不属于**本域窗口（那是"这个域还没开始记"而不是"缺"）。
    """
    out = rc.compute_coverage(ALL_DAYS, {D14, D16})
    assert out["trading_days"] == 3
    assert out["have_days"] == 2
    assert out["complete_days"] == 2
    assert out["cover_pct"] == 66.67
    assert out["missing_days"] == [D15.isoformat()]
    assert out["extra_days"] == []


def test_compute_coverage_have_and_complete_diverge() -> None:
    """Ruling 13：`sector_moneyflow` 盘中 poll 行只是 inflow-Top-100 子集。

    `have_days` 复现历史覆盖形状（~65%），`complete_days` 只认 `eod_full` → 0%；
    `cover_pct` 基于 complete_days，`have_days` 原样带回。
    """
    out = rc.compute_coverage(ALL_DAYS, {D14, D15, D16}, complete_days={D15, D17})
    assert out["have_days"] == 3  # 有行（poll）的三天照实带回
    assert out["complete_days"] == 1  # 只有 D15 是"完整日"（D17 既无行也不算完整）
    assert out["cover_pct"] == 33.33  # 完整日 / 交易日 = 1/3
    assert out["missing_days"] == []  # 窗口内 14/15/16 都有行（D17+ 在本域窗口外）


def test_compute_coverage_complete_days_defaults_to_have_days() -> None:
    """其余域没有产地之分：complete_days 恒等于 have_days（不是"另一个 0"）。"""
    out = rc.compute_coverage(ALL_DAYS, {D14, D15})
    assert out["complete_days"] == out["have_days"] == 2


def test_compute_coverage_complete_days_bounded_by_own_have_days() -> None:
    """完整日必须是"有行"的子集——脏数据不得造出 complete > have。"""
    out = rc.compute_coverage(ALL_DAYS, {D14, D21}, complete_days={D21, D22, D19})
    assert out["complete_days"] == 1  # 只认交集（D21；D22 无行、D19 不是交易日）
    assert out["have_days"] == 2
    assert out["cover_pct"] == 16.67  # complete(D21) / trading(14,15,16,17,18,21)


def test_compute_coverage_cover_pct_rounds_to_two_decimals() -> None:
    """单日数据 → 窗口就是那一天 → 1/1；要量"多日窗口"必须显式给 window。"""
    out = rc.compute_coverage([D14, D15, D16], {D14})
    assert out["trading_days"] == 1 and out["cover_pct"] == 100.0
    wide = rc.compute_coverage([D14, D15, D16], {D14}, window=(D14, D16))
    assert wide["trading_days"] == 3
    assert wide["have_days"] == 1
    assert wide["cover_pct"] == 33.33


# ------------------------------------------------------------------ 2. coverage_report


class _FakeCoverageDB(_FakeDB):
    """在 `_FakeDB` 之上按语句形态分派（同表可能有多条查询）。

    - `max(<date>), min(<date>)` → 该域数据的 [first_day, last_day] 区间行
    - `group by` + 完整日产地谓词（如 `eod_full`）→ `eod_days`（"完整日"行集）
    - 其余 `group by` → `days`（有任何行的日子）
    """

    def __init__(
        self,
        days: dict[str, list[tuple[date, int]]],
        eod_days: dict[str, list[tuple[date, int]] | None] | None = None,
        quotes: list[tuple[date, int]] | None = None,
        limits: list[tuple[date, int]] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__({})
        self.days = days
        self.eod_days = eod_days or {}
        # 交易日历的两个源（TRADING_DAY_SOURCES）：默认 quotes 有、limits 为空
        self.quotes = QUOTES_DAYS if quotes is None else quotes
        self.limits = [] if limits is None else limits
        self.fail = kw.pop("fail", {})

    async def execute(self, stmt: Any) -> _Result:
        sql = _sql(stmt)
        self.statements.append(sql)
        for table, exc in self.fail.items():
            if table in sql:
                raise exc
        if "daily_quotes" in sql or "stock_price_limits" in sql:
            rows = self.quotes if "daily_quotes" in sql else self.limits
            # 日历查询：`max(trade_date)` → 一列；distinct 交易日 → 一列
            return _Result([(d,) for d, _ in rows])
        # `eod_days` 的 None = "该域确实有行，但一行 eod_full 都没有"（真库 pre-T2 形状）：
        # 该域的完整日谓词下应当**查不到任何日子**，此时若 have 也归零就是串谓词的 bug
        if "__none__" in sql:  # 不变式用例的"恒不命中"哨兵（只用于 have 查询）
            return _Result([])
        book = self.eod_days if "eod_full" in sql else self.days
        rows: Any = next((r for table, r in book.items() if table in sql), [])
        if "group by" in sql:
            return _Result(list(rows or []))
        days = [d for d, _ in (rows or [])]
        return _Result([(max(days), min(days))] if days else [(None, None)])


# 每域用**多日**打桩：单日域的窗口退化成一天（100%），量不出"交易日 vs 自然日"的分母
DAY_ROWS: dict[str, list[tuple[date, int]]] = {
    "sector_moneyflow_snapshots": [(D14, 10), (D15, 10), (D16, 10)],
    "market_sentiment_daily": [(D14, 1), (D15, 1), (D16, 1)],
    "market_sentiment_intraday": [(D14, 5), (D16, 4)],  # D15 缺 → 2/3
    "market_limit_up_stock_daily": [(D14, 200), (D15, 180), (D16, 210)],
    "dragon_tiger_entries": [(D14, 30), (D16, 12)],  # D15 缺
    "block_trades": [(D14, 20), (D15, 18), (D16, 22)],
    "share_floats": [(D14, 7), (D15, 9), (D16, 4)],
    "stock_repurchases": [(D15, 3), (D16, 5)],  # D14 缺
}
QUOTES_DAYS = [(d, 5500) for d in (D14, D15, D16)]


#: `sector_moneyflow` 的"完整日"（eod_full）：默认 `None` = 一行都没有
#: （复现真库"上线前只有 poll 行"的形状；有 eod 行时传 [(day, n), ...]）
EOD_ROWS: dict[str, list[tuple[date, int]] | None] = {"sector_moneyflow_snapshots": None}


def _report_db(**kw: Any) -> _FakeCoverageDB:
    return _FakeCoverageDB(
        days=kw.pop("days", DAY_ROWS),
        eod_days=kw.pop("eod_days", EOD_ROWS),
        quotes=kw.pop("quotes", QUOTES_DAYS),
        limits=kw.pop("limits", []),
        **kw,
    )


async def test_coverage_report_covers_every_inspect_domain() -> None:
    """巡检域一个都不能少：少一个就是"洞当天不告警"复发。"""
    report = await rc.coverage_report(_report_db())
    assert [r["domain"] for r in report] == list(rc.INSPECT_DOMAINS)
    assert set(rc.INSPECT_DOMAINS) == set(INSPECT_SOURCES)


async def test_coverage_report_source_of_truth_matches_query_table() -> None:
    """域 → 表/日期列必须有唯一真源（不许测试与实现各写一份漂移）。"""
    for domain, (table, column) in INSPECT_SOURCES.items():
        src = rc._COVERAGE_SOURCES[domain]
        assert (src.table, src.date_column) == (table, column)


async def test_coverage_report_denominator_is_union_trading_days() -> None:
    """分母 = 窗口内 `daily_quotes` ∪ `stock_price_limits` 的**交易日**（不是自然日）。

    名字里的"并集"是 Task 8 更正的结果：原名 `..._is_daily_quotes_trading_days`，
    但实现与断言用的是 `TRADING_DAY_SOURCES` 的并集（单源会把自己的缺日算成巡检域的
    缺日；并集补出主源缺日时另由 `degraded_trading_calendar` 标注）。`daily_quotes`
    没数据时并集也可能为空 → `cover_pct=None`（由下一条用例钉住）。
    """
    report = await rc.coverage_report(_report_db())
    by_domain = {r["domain"]: r for r in report}
    sent = by_domain["sentiment_daily"]
    assert sent["trading_days"] == 3  # 分母不是自然日（09-12/13 是周末，不在 daily_quotes）
    assert sent["have_days"] == 3
    assert sent["complete_days"] == 3
    assert sent["cover_pct"] == 100.0
    assert by_domain["sentiment_intraday"]["trading_days"] == 3  # 交易日分母（不是自然日）
    assert by_domain["sentiment_intraday"]["have_days"] == 2
    assert by_domain["sentiment_intraday"]["cover_pct"] == 66.67
    assert by_domain["sentiment_intraday"]["missing_days"] == [D15.isoformat()]


async def test_coverage_report_no_quotes_days_yields_none_pct() -> None:
    report = await rc.coverage_report(_report_db(quotes=[]))
    assert all(r["cover_pct"] is None for r in report)
    assert all(r["trading_days"] == 0 for r in report)


async def test_coverage_report_missing_table_does_not_raise() -> None:
    """迁移未应用 → 该域标 `missing_table`，其余域照常渲染（不得 500）。"""
    db = _report_db(
        fail={"market_limit_up_stock_daily": _missing("market_limit_up_stock_daily", code="42P01")}
    )
    report = await rc.coverage_report(db)
    by_domain = {r["domain"]: r for r in report}
    assert by_domain["limit_up_stock_daily"]["status"] == "missing_table"
    assert by_domain["limit_up_stock_daily"]["cover_pct"] is None
    assert by_domain["limit_up_stock_daily"]["have_days"] == 0
    assert by_domain["limit_up_stock_daily"]["row_count"] == 0
    # 其余域不受影响（各域窗口内数值不变）
    assert by_domain["sentiment_daily"]["status"] == "ok"
    assert by_domain["sentiment_daily"]["cover_pct"] == 100.0
    assert by_domain["dragon_tiger"]["have_days"] == 2
    assert by_domain["share_float"]["have_days"] == 3
    # sector_moneyflow：3 天有行（poll）但**完整日 0** —— Ruling 13 的核心形状
    assert by_domain["sector_moneyflow"]["have_days"] == 3
    assert by_domain["sector_moneyflow"]["complete_days"] == 0
    assert by_domain["sector_moneyflow"]["cover_pct"] == 0.0


async def test_coverage_report_missing_capture_kind_column_is_flagged() -> None:
    """`capture_kind` 列缺失（迁移只跑了一半）→ `missing_column`，不是 500。"""
    db = _report_db(fail={"sector_moneyflow_snapshots": _missing("capture_kind", code="42703")})
    report = await rc.coverage_report(db)
    by_domain = {r["domain"]: r for r in report}
    assert by_domain["sector_moneyflow"]["status"] == "missing_column"
    assert by_domain["sector_moneyflow"]["cover_pct"] is None
    assert by_domain["sentiment_daily"]["status"] == "ok"


async def test_coverage_report_quotes_empty_database_yields_none_pct() -> None:
    """`daily_quotes` 为空（全新库）→ 每域 0 交易日 + cover_pct=None（绝不是 0%）。"""
    report = await rc.coverage_report(_report_db(quotes=[]))
    by_domain = {r["domain"]: r for r in report}
    assert by_domain["sentiment_daily"]["trading_days"] == 0
    assert by_domain["sentiment_daily"]["cover_pct"] is None
    assert by_domain["sentiment_daily"]["status"] == "ok"


async def test_coverage_report_recovers_session_after_missing_table() -> None:
    """缺表域的语句被 SAVEPOINT 隔离：会话不被污染，后续域查询照常发出。"""
    db = _report_db(fail={"block_trades": _missing("block_trades", code="42P01")})
    report = await rc.coverage_report(db)
    assert {r["domain"]: r["status"] for r in report}["share_float"] == "ok"
    assert len([s for s in db.statements if "share_floats" in s]) == 2  # 缺表之后仍有查询


async def test_coverage_report_is_read_only() -> None:
    """只读巡检：语句里不得出现写操作（INSERT/UPDATE/DELETE/ALTER/TRUNCATE）。"""
    db = _report_db()
    await rc.coverage_report(db)
    for sql in db.statements:
        assert not any(kw in sql for kw in ("insert", "update", "delete", "alter", "truncate")), sql


async def test_coverage_report_limit_up_stock_daily_not_in_repairable_domains() -> None:
    """`limit_up_stock_daily` 在巡检列表里但**不可自愈**（无 `_refetch_*`）。

    它一旦出现在 refetched/apply 路径就是假绿 —— 这条断言把两套域的边界钉死。
    """
    assert "limit_up_stock_daily" in rc.INSPECT_DOMAINS
    assert "limit_up_stock_daily" not in rc.DOMAINS
    assert not hasattr(rc, "_refetch_limit_up_stock_daily")


async def test_coverage_report_inspect_domains_never_repairable() -> None:
    """所有巡检域都没有回补器；`DOMAINS`（可自愈域）恒等于有 `_refetch_*` 的域。"""
    for domain in rc.INSPECT_DOMAINS:
        assert not hasattr(rc, f"_refetch_{domain}"), domain
    assert set(rc.DOMAINS) == {"daily_quotes", "daily_basic", "price_limits", "sentiment"}


async def test_coverage_report_never_touches_refetch_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖率聚合调用期间不得触发任何 `_refetch_*`（只读巡检的机械守卫）。"""
    called: list[str] = []

    def _boom(name: str):
        async def _f(db: Any, day: date) -> dict[str, Any]:
            called.append(name)
            return {}

        return _f

    for domain in rc.DOMAINS:
        monkeypatch.setattr(rc, f"_refetch_{domain}", _boom(domain))
    await rc.coverage_report(_report_db())
    assert called == []


async def test_reconcile_domains_unchanged_by_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    """补漏路径的域集不得被巡检域污染（E1 的回归守卫）。"""
    assert rc.DOMAINS == ("daily_quotes", "daily_basic", "price_limits", "sentiment")


async def test_coverage_report_sector_moneyflow_complete_days_use_eod_full() -> None:
    """`sector_moneyflow` 的完整日 = 有 `capture_kind='eod_full'` 行的日子（产地决定可信度）。"""
    db = _report_db()
    await rc.coverage_report(db)
    eod_stmts = [s for s in db.statements if "eod_full" in s]
    assert eod_stmts, "sector_moneyflow 必须单独查 eod_full 行"
    assert all("capture_kind" in s for s in eod_stmts)
    # 且 complete 查询只针对 sector_moneyflow（其余域 complete_days == have_days，无需第二次查询）
    assert len(eod_stmts) == 1


async def test_coverage_report_warns_when_rows_exist_but_have_days_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """不变式守卫：域里有行却报 `have_days=0` → WARNING（真库真踩过：完整日谓词串进 have 查询）。

    这里把 `have` 查询强行打桩成"总是空"来**复现那个 bug**（等价于把完整日谓词串进
    have 查询）：不变式守卫必须告警，而不是让整域静默归零。
    """
    from sqlalchemy import select as _select

    from app.models.market_data import SectorMoneyflowSnapshot

    db = _report_db()
    real_stmt = rc._coverage_row_days_stmt

    def _empty_have(src: rc._CoverageSource, *, complete_only: bool = False):  # type: ignore[no-untyped-def]
        """have 查询换成"恒不命中"的语句（假 db 见到该哨兵就回空行集）。"""
        return _select(SectorMoneyflowSnapshot.trade_date).where(
            SectorMoneyflowSnapshot.board_code == "__none__"
        )

    rc._coverage_row_days_stmt = _empty_have  # type: ignore[assignment]
    try:
        with caplog.at_level("WARNING"):
            report = await rc.coverage_report(db)
    finally:
        rc._coverage_row_days_stmt = real_stmt  # type: ignore[assignment]
    assert "invariant violated" in caplog.text
    # 正常路径（have 查得到行）不得刷这条 WARNING
    caplog.clear()
    with caplog.at_level("WARNING"):
        clean = await rc.coverage_report(_report_db())
    assert "invariant violated" not in caplog.text
    assert {r["domain"]: r["have_days"] for r in clean}["sector_moneyflow"] == 3
    assert {r["domain"]: r["have_days"] for r in report}["sector_moneyflow"] == 0


async def test_coverage_report_trading_calendar_is_union_of_sources() -> None:
    """分母 = `daily_quotes` ∪ `stock_price_limits`：行情自己缺日时不得把洞算到巡检域头上。

    构造（桩）：主源缺 D15、补漏源 D15 有行 —— 只用主源当分母就少算一个交易日，
    行情缺口被误报成业务缺口。（早期报告的真库证据"`daily_quotes` 缺
    2026-09-09..09-17 共 7 个交易日"**已撤回**：那是窗口化查询的产物，真库覆盖该区间
    全部日期。契约必须由桩钉住，真库状态会变。）

    并集**补出了主源缺的日子** → `degraded_trading_calendar=True`（评审 Important #1：
    该标记的语义是"并集提供了主源缺失的交易日"，不是"主源表不存在"）。
    """
    db = _report_db(
        quotes=[(D14, 5500), (D16, 5500)],  # 主源缺 D15
        limits=[(D15, 5500)],  # 补漏源有 D15
    )
    report = await rc.coverage_report(db)
    by_domain = {r["domain"]: r for r in report}
    assert by_domain["sentiment_daily"]["trading_days"] == 3  # D14/15/16 都在
    assert by_domain["sentiment_daily"]["cover_pct"] == 100.0
    assert by_domain["sentiment_daily"]["degraded_trading_calendar"] is True


async def test_stale_domain_old_vs_new_anchor_evidence() -> None:
    """评审 Important #2 的构造证据：同一份假 DB，旧锚定（夹到域末日）vs 新锚定（日历末日）。

    旧口径：`block_trade` 只到 D14 → 分母缩成 1 天 → `cover_pct=100.0` / 无缺日。
    新口径：分母仍是窗口 3 个交易日 → `33.33` / 缺 D15、D16。
    两行断言并排钉住"夹取会让整域停更看起来健康"这件事本身。
    """
    days = dict(DAY_ROWS)
    days["block_trades"] = [(D14, 20)]  # 自 D15 起整域停更

    old = rc._normalize_coverage_row(
        "block_trade",
        rc._COVERAGE_SOURCES["block_trade"],
        dict(days["block_trades"]),  # 真实调用方传的就是 {date: 行数}
        (D14, D14),  # 旧口径：win_to = 域自己的末日
        [D14, D15, D16],
    )
    assert (old["cover_pct"], old["missing_days"]) == (100.0, [])

    new = {r["domain"]: r for r in await rc.coverage_report(_report_db(days=days))}["block_trade"]
    assert (new["cover_pct"], new["missing_days"]) == (
        33.33,
        [D15.isoformat(), D16.isoformat()],
    )


async def test_coverage_report_calendar_not_degraded_when_primary_complete() -> None:
    """主源自己就覆盖窗口内全部交易日（并集没有多出日子）→ 不得误报降级。"""
    db = _report_db(quotes=QUOTES_DAYS, limits=[(D15, 5500)])  # 次源只是子集
    report = await rc.coverage_report(db)
    assert all(r["degraded_trading_calendar"] is False for r in report)


async def test_coverage_report_window_end_anchored_to_calendar_last_day() -> None:
    """窗口末端锚定**日历末日**，不夹到各域自己的最后一行（评审 Important #2）。

    夹到域末日 → 整域停更的域分母随之缩小甚至退化成单日，报 `cover_pct=100` /
    `missing_days=[]`，对"这个域整体停更"完全盲。这里让 block_trade 停在 D14、
    日历到 D16，则它必须报出 D15/D16 两个洞（而不是分母缩成 1 天报 100%）。
    """
    days = dict(DAY_ROWS)
    days["block_trades"] = [(D14, 20)]  # 该域从 D15 起整域停更
    report = await rc.coverage_report(_report_db(days=days))
    row = {r["domain"]: r for r in report}["block_trade"]
    assert row["trading_days"] == 3  # 分母仍是整个窗口的交易日
    assert row["have_days"] == 1
    assert row["cover_pct"] == 33.33  # 明显 < 100
    assert row["missing_days"] == [D15.isoformat(), D16.isoformat()]
    assert row["last_day"] == D14.isoformat()  # 数据跨度如实展示（停更事实可见）


async def test_coverage_report_degraded_when_primary_calendar_missing() -> None:
    """主源 `daily_quotes` 不可用（未建表）→ 只用品补漏源 + `degraded_trading_calendar=true`。

    换源必须可见：分母口径变了却标 ok，读的人会把"降级分母"当成正常口径。
    """
    db = _report_db(quotes=[], limits=[(D14, 5500), (D15, 5500), (D16, 5500)])
    db.fail["daily_quotes"] = _missing("daily_quotes", code="42P01")
    report = await rc.coverage_report(db)
    by_domain = {r["domain"]: r for r in report}
    assert by_domain["sentiment_daily"]["degraded_trading_calendar"] is True
    assert by_domain["sentiment_daily"]["trading_days"] == 3
    assert by_domain["sentiment_daily"]["status"] == "ok"


async def test_coverage_report_empty_domain_falls_back_to_recent_window() -> None:
    """一行都没有的域（新表上线前）不得把分母撑成整部行情史。

    真库实测踩过：空域窗口无从取极值 → 分母退化成 829 个交易日、`missing_days` 829 条。
    """
    days = dict(DAY_ROWS)
    days.pop("market_limit_up_stock_daily")
    db = _report_db(days=days)
    report = await rc.coverage_report(db)
    row = {r["domain"]: r for r in report}["limit_up_stock_daily"]
    assert row["have_days"] == 0
    assert row["trading_days"] == 3  # 最近 window_days 内，而不是全部
    assert row["missing_days"] == [D14.isoformat(), D15.isoformat(), D16.isoformat()]
    assert row["first_day"] is None and row["last_day"] is None


async def test_coverage_report_row_count_is_actual_rows() -> None:
    report = await rc.coverage_report(_report_db())
    by_domain = {r["domain"]: r for r in report}
    assert by_domain["dragon_tiger"]["row_count"] == 42  # 30 + 12
    assert by_domain["sector_moneyflow"]["row_count"] == 30
    assert by_domain["share_float"]["row_count"] == 20  # 7 + 9 + 4


async def test_coverage_report_first_last_day_echo_range() -> None:
    report = await rc.coverage_report(_report_db())
    by_domain = {r["domain"]: r for r in report}
    assert by_domain["share_float"]["first_day"] == D14.isoformat()
    assert by_domain["share_float"]["last_day"] == D16.isoformat()
    # 窗口口径（分子分母必须同一个窗口）：下界取 max(域首行, since)，**末端锚定日历末日**
    assert rc._coverage_window((D16, D14), since=D15, cal_end=D16) == (D14, D16, D15, D16)
    assert rc._coverage_window((D16, D14), since=None, cal_end=D16) == (D14, D16, D14, D16)
    # 整域停更（域末日 ≪ 日历末日）时末端仍取日历末日 → 洞不会被夹掉
    assert rc._coverage_window((D14, D14), since=D14, cal_end=D22) == (D14, D14, D14, D22)
    # 日历为空（全库无行情）→ 退回域末日，不让窗口末端为 None
    assert rc._coverage_window((D16, D14), since=None, cal_end=None) == (D14, D16, D14, D16)
    assert rc._coverage_window((None, None), since=D15, cal_end=None) == (None, None, None, None)


# ------------------------------------------------------------------ 3. 端点


async def test_coverage_report_degraded_when_union_fills_primary_gap() -> None:
    """主源在库但缺日、由次源补上 → `degraded_trading_calendar=True`（评审 Important #1 的正例）。

    与 `test_coverage_report_calendar_not_degraded_when_primary_complete`（主源完整 →
    必须为 False）配成一对：两条都是**桩**构造的契约，不看会变的真库状态。
    """
    db = _report_db(
        quotes=[(D14, 5500), (D16, 5500)],  # 主源缺 D15
        limits=[(D14, 5500), (D15, 5500), (D16, 5500)],
    )
    report = await rc.coverage_report(db)
    assert all(r["degraded_trading_calendar"] is True for r in report)
    assert {r["domain"]: r["trading_days"] for r in report}["sentiment_daily"] == 3


@pytest.mark.e2e
async def test_coverage_report_calendar_not_degraded_on_real_db() -> None:
    """真库（`e2e`）：`daily_quotes` 当前是 `stock_price_limits` 的超集 → 并集没有多出
    任何日子 → `degraded_trading_calendar` 为 False。

    **说明（Task 8）**：这条是**环境探针**（打印真库当前形状），不是契约 —— 真库状态会变
    （行情自己缺日时该标记**应当**为 true），把 `degraded is False` 当断言会让它随数据
    合法变化而随机变红。契约由两条桩用例钉住：
    `test_coverage_report_calendar_not_degraded_when_primary_complete`（反例）与
    `test_coverage_report_degraded_when_union_fills_primary_gap`（正例）。
    """
    from app.core.database import async_session_factory  # noqa: PLC0415

    async with async_session_factory() as db:
        calendar, degraded = await rc._trading_calendar(db, window_days=30, since=None)
        assert calendar, "真库 daily_quotes 不该为空"
        print(f"[e2e probe] trading_days={len(calendar)} degraded={degraded}")


def test_data_freshness_out_exposes_coverage_additively() -> None:
    """`coverage` 是加字段：既有字段一个不能少，缺省空列表（老 producer 不炸）。"""
    from app.schemas.market_data import DataFreshnessOut

    out = DataFreshnessOut(
        as_of="2026-10-12",
        expected_latest="2026-10-09",
        expected_days=10,
        universe=5513,
        quotes_symbols_latest=5485,
        degraded_calendar=False,
        apply=False,
        domains={},
    )
    assert out.coverage == []
    assert out.adj_factor_repaired is None
    assert out.failed_jobs == []
    assert out.universe == 5513 and out.quotes_symbols_latest == 5485


def test_data_freshness_out_coverage_item_shape() -> None:
    from app.schemas.market_data import DataFreshnessOut

    out = DataFreshnessOut(
        as_of="2026-10-12",
        expected_latest="2026-10-09",
        expected_days=10,
        universe=5513,
        quotes_symbols_latest=5485,
        degraded_calendar=False,
        apply=False,
        domains={},
        coverage=[
            {
                "domain": "sector_moneyflow",
                "table": "sector_moneyflow_snapshots",
                "date_column": "trade_date",
                "row_count": 5583,
                "have_days": 13,
                "complete_days": 0,
                "trading_days": 20,
                "cover_pct": 0.0,
                "missing_days": ["2026-09-05"],
                "first_day": "2026-09-04",
                "last_day": "2026-10-12",
                "status": "ok",
            }
        ],
    )
    assert out.coverage[0].domain == "sector_moneyflow"
    assert out.coverage[0].complete_days == 0
    assert out.coverage[0].have_days == 13


def test_data_freshness_out_coverage_defaults_never_zero() -> None:
    """缺失渲染 None/`--`，绝不 `?? 0`：缺省 cover_pct 是 None 而不是 0。"""
    from app.schemas.market_data import CoverageOut

    item = CoverageOut(domain="block_trade", table="block_trades", date_column="trade_date")
    assert item.cover_pct is None
    assert item.have_days == 0 and item.complete_days == 0
    assert item.trading_days == 0
    assert item.status == "ok" and item.missing_days == []


def test_market_data_schema_keeps_legacy_import_path() -> None:
    """`DataFreshnessOut` 迁到 market_data.py 后，既有 `schemas.reconciliation` 导入不得断。"""
    from app.schemas import reconciliation
    from app.schemas.market_data import DataFreshnessOut

    assert reconciliation.DataFreshnessOut is DataFreshnessOut
    assert reconciliation.DomainFreshnessOut is not None
    assert reconciliation.AdjFactorRepairOut is not None


async def test_endpoint_returns_coverage_and_stays_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端点仍以 `apply=False` 调对账，并把 `coverage_report` 的结果透出。"""
    from app.api.v1 import market_data as api

    calls: list[dict[str, Any]] = []

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {
            "as_of": "2026-10-12",
            "expected_latest": "2026-10-09",
            "expected_days": 10,
            "universe": 5513,
            "quotes_symbols_latest": 5485,
            "degraded_calendar": False,
            "apply": False,
            "adj_factor_repaired": None,
            "domains": {},
        }

    coverage = [
        {
            "domain": "sentiment_intraday",
            "table": "market_sentiment_intraday",
            "date_column": "trade_date",
            "row_count": 277,
            "have_days": 6,
            "complete_days": 6,
            "trading_days": 11,
            "cover_pct": 54.55,
            "missing_days": [],
            "first_day": "2026-09-17",
            "last_day": "2026-10-12",
            "status": "ok",
        }
    ]

    async def _coverage(db: Any, **kw: Any) -> list[dict[str, Any]]:
        calls.append({"coverage": kw})
        return coverage

    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    monkeypatch.setattr("app.services.reconciliation_service.coverage_report", _coverage)

    out = await api.get_data_freshness()

    assert calls[0] == {"apply": False}  # 对账仍是只读
    assert out.apply is False
    assert len(out.coverage) == 1 and out.coverage[0].domain == "sentiment_intraday"
    assert out.coverage[0].cover_pct == 54.55


async def test_endpoint_survives_coverage_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """覆盖率是加法：它炸了不得把 freshnss 端点打成 500（宁可空列表）。"""
    from app.api.v1 import market_data as api

    class _Ctx:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def _reconcile(db: Any, **kw: Any) -> dict[str, Any]:
        return {
            "as_of": "2026-10-12",
            "expected_latest": "2026-10-09",
            "expected_days": 10,
            "universe": 5513,
            "quotes_symbols_latest": 5485,
            "degraded_calendar": False,
            "apply": kw.get("apply", True),
            "adj_factor_repaired": None,
            "domains": {},
        }

    async def _coverage(db: Any, **kw: Any) -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.core.database.async_session_factory", _Ctx)
    monkeypatch.setattr("app.services.reconciliation_service.reconcile_market_data", _reconcile)
    monkeypatch.setattr("app.services.reconciliation_service.coverage_report", _coverage)

    out = await api.get_data_freshness()
    assert out.coverage == []
    assert out.universe == 5513


def test_coverage_report_signature_has_window_days() -> None:
    """接口契约：`coverage_report(db, *, window_days=30)`（brief 逐字）。"""
    import inspect

    sig = inspect.signature(rc.coverage_report)
    assert sig.parameters["window_days"].default == 30
