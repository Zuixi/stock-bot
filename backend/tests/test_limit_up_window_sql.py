"""`_WINDOW_SQL` 连板核心不变量守卫（SQLite 内存库，无 Postgres，默认 `uv run pytest` 门禁）。

`_WINDOW_SQL` 只用 COALESCE、`row_number() OVER (PARTITION BY …)`、LEFT JOIN 与绑定参数，
现代 SQLite 全部支持，因此把 gaps-and-islands 的两条承重不变量钉在默认门禁里，而不是
躲在 `-m e2e` 之后：

1. `streaked` 的 `PARTITION BY` 必须带 `is_lu`（`stock_id, is_lu, grp`）——`grp = rn_all -
   rn_by_val` 只保证组内常量、不保证组间唯一，一段 false 岛与紧接的 true 岛会撞同一 grp，
   被合并后 true 行继承 false 行的计数（实测 600127 报 3 实为 1）。
2. `is_lu` / `touched` 必须 `COALESCE(..., false)`——LEFT JOIN 到缺失限价的交易日会得到 NULL，
   NULL 会让 gaps-and-islands 的 grp 分组错乱（NULL 与 false 不同组）。
3. `cand` 必须 `DISTINCT`——两日都涨停的票（股 3）在候选 CTE 里会出现两次，去掉
   `DISTINCT` 会把窗口行按候选数翻倍，制造出重复 `(stock_id, trade_date)` 的扇出
   （涨停家数 75→94 那类错误的合成版）。这是 `test_window_has_no_fanout_and_full_window`
   的唯一真实触发源：fixture 必须真的有一只在 as_of 与 as_of_prev 双涨停的票。

本文件直接 `import` 模块常量 `limit_up_repo._WINDOW_SQL`，不复制 SQL 文本（复制的文本会
漂移并静默通过）。`_BREADTH_SQL` 用 Postgres 专属的 `count(*) FILTER`，不在此列；真库的
计划形状守卫（走 uq_/idx_ 索引、不逐股 probe）仍留在 `test_limit_up_repo.py`（`-m e2e`）。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.repositories import limit_up_repo

WINDOW_START = "2026-09-01"
AS_OF_PREV = "2026-09-04"
AS_OF = "2026-09-05"

_SCHEMA = [
    "CREATE TABLE daily_quotes ("
    "stock_id INTEGER, trade_date TEXT, close REAL, open REAL, high REAL, amount REAL)",
    "CREATE TABLE stock_price_limits ("
    "stock_id INTEGER, trade_date TEXT, pre_close REAL, up_limit REAL, down_limit REAL)",
    "CREATE TABLE stocks (id INTEGER, symbol TEXT, name TEXT)",
    "CREATE TABLE sw_industry_members (symbol TEXT, industry_code TEXT)",
    "CREATE TABLE sw_industry_classes ("
    "industry_code TEXT, industry_name TEXT, level INTEGER, parent_code TEXT)",
]

# 股 1：is_lu 序列 [T, F, T, T, F]，其中 D2 有行情但 stock_price_limits 无行（COALESCE 兜底）。
# 股 2：零跑股——窗口内连续 4 个非涨停日 + 末日 1 个涨停（候选集要求 as_of/as_of_prev 涨停，
# 所以"完全没有涨停"的股票不会进入窗口；用零跑覆盖该场景）。
# 股 3：双涨停股——as_of 与 as_of_prev 两天都涨停。这正是 `cand` 里同一 stock_id 出现两次
# 的形状，`DISTINCT` 去重后回一行；去掉 `DISTINCT` 会让它扇出（守卫的唯一真实触发源）。
_QUOTES = [
    (1, "2026-09-01", 11.0, 11.0, 11.0, 100.0),  # T
    (1, "2026-09-02", 9.0, 9.0, 9.0, 100.0),    # 有行情、无限价行 → NULL → false
    (1, "2026-09-03", 11.0, 11.0, 11.0, 100.0),  # T
    (1, "2026-09-04", 11.0, 11.0, 11.0, 100.0),  # T（as_of_prev）
    (1, "2026-09-05", 9.0, 9.0, 9.0, 100.0),    # F（as_of）
    (2, "2026-09-01", 9.0, 9.0, 9.0, 50.0),     # F
    (2, "2026-09-02", 9.0, 9.0, 9.0, 50.0),     # F
    (2, "2026-09-03", 9.0, 9.0, 9.0, 50.0),     # F
    (2, "2026-09-04", 9.0, 9.0, 9.0, 50.0),     # F
    (2, "2026-09-05", 11.0, 11.0, 11.0, 50.0),  # T（as_of）
    (3, "2026-09-04", 11.0, 11.0, 11.0, 80.0),  # T（as_of_prev）
    (3, "2026-09-05", 11.0, 11.0, 11.0, 80.0),  # T（as_of）
]

# up_limit=10.0 → close >= 9.995 即涨停。股 1 的 D2 故意缺行。
_LIMITS = [
    (1, "2026-09-01", 10.0, 10.0, 9.0),
    (1, "2026-09-03", 10.0, 10.0, 9.0),
    (1, "2026-09-04", 10.0, 10.0, 9.0),
    (1, "2026-09-05", 10.0, 10.0, 9.0),
    (2, "2026-09-01", 10.0, 10.0, 9.0),
    (2, "2026-09-02", 10.0, 10.0, 9.0),
    (2, "2026-09-03", 10.0, 10.0, 9.0),
    (2, "2026-09-04", 10.0, 10.0, 9.0),
    (2, "2026-09-05", 10.0, 10.0, 9.0),
    (3, "2026-09-04", 10.0, 10.0, 9.0),
    (3, "2026-09-05", 10.0, 10.0, 9.0),
]

_STOCKS = [(1, "000001", "测试一"), (2, "000002", "测试二"), (3, "000003", "测试三")]
_MEMBERS = [("000001", "110703"), ("000002", "110703"), ("000003", "110703")]
_CLASSES = [
    ("110000", "农林牧渔", 1, None),
    ("110700", "养殖业", 2, "110000"),
    ("110703", "生猪养殖", 3, "110700"),
]


@pytest.fixture()
def window_engine() -> Engine:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for stmt in _SCHEMA:
            conn.exec_driver_sql(stmt)
        conn.execute(
            text("INSERT INTO daily_quotes VALUES (:a, :b, :c, :d, :e, :f)"),
            [
                {"a": s, "b": d, "c": c, "d": o, "e": h, "f": amt}
                for s, d, c, o, h, amt in _QUOTES
            ],
        )
        conn.execute(
            text("INSERT INTO stock_price_limits VALUES (:a, :b, :c, :d, :e)"),
            [
                {"a": s, "b": d, "c": pc, "d": ul, "e": dl}
                for s, d, pc, ul, dl in _LIMITS
            ],
        )
        conn.execute(
            text("INSERT INTO stocks VALUES (:a, :b, :c)"),
            [{"a": sid, "b": sym, "c": name} for sid, sym, name in _STOCKS],
        )
        conn.execute(
            text("INSERT INTO sw_industry_members VALUES (:a, :b)"),
            [{"a": sym, "b": code} for sym, code in _MEMBERS],
        )
        conn.execute(
            text("INSERT INTO sw_industry_classes VALUES (:a, :b, :c, :d)"),
            [
                {"a": code, "b": name, "c": level, "d": parent}
                for code, name, level, parent in _CLASSES
            ],
        )
    return engine


def _run_window(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(limit_up_repo._WINDOW_SQL),
            {"window_start": WINDOW_START, "as_of": AS_OF, "as_of_prev": AS_OF_PREV},
        ).mappings().all()
    return [dict(r) for r in rows]


def _by_key(rows: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    return {(r["stock_id"], r["trade_date"]): r for r in rows}


def test_streak_restarts_after_false_island(window_engine: Engine) -> None:
    """false 岛之后紧接 true 岛：true 岛必须从 1 重启。

    反例：`PARTITION BY stock_id, grp`（缺 is_lu）会让 false 岛与 true 岛撞同一 grp，
    合并后 D3/D4 的 streak_upto 变成 2/3（600127 报 3 实为 1 的同类错法）。
    """
    rows = _by_key(_run_window(window_engine))
    assert rows[(1, "2026-09-01")]["streak_upto"] == 1  # 首板
    assert rows[(1, "2026-09-03")]["streak_upto"] == 1  # false 岛后重启
    assert rows[(1, "2026-09-04")]["streak_upto"] == 2


def test_null_limit_day_is_treated_as_non_limit_up(window_engine: Engine) -> None:
    """缺限价行的交易日必须 COALESCE(false) 当非涨停，且不得延长连板。"""
    rows = _by_key(_run_window(window_engine))
    null_day = rows[(1, "2026-09-02")]
    assert null_day["is_lu"] is not None, "is_lu 必须是布尔假而不是 NULL"
    assert null_day["is_lu"] == 0
    assert null_day["touched"] is not None, "touched 必须是布尔假而不是 NULL"
    assert null_day["touched"] == 0
    # 该 NULL 日既不计数也不截断之后 true 岛的起点：次日重启为 1。
    assert rows[(1, "2026-09-03")]["streak_upto"] == 1


def test_zero_run_does_not_inflate_streak(window_engine: Engine) -> None:
    """零跑股（连续非涨停日）的涨停 streak 不得被前面的 false 岛计数抬升。"""
    rows = _by_key(_run_window(window_engine))
    assert rows[(2, "2026-09-01")]["is_lu"] == 0
    assert rows[(2, "2026-09-04")]["streak_upto"] == 4  # false 岛内部计数
    assert rows[(2, "2026-09-05")]["is_lu"] == 1
    assert rows[(2, "2026-09-05")]["streak_upto"] == 1  # 零跑不抬升涨停 streak


def test_window_has_no_fanout_and_full_window(window_engine: Engine) -> None:
    """无扇出（(stock_id, trade_date) 唯一）且回整段交易日（>2 个不同日期）。

    扇出的真实来源是 cand 未 DISTINCT：股 3 在 as_of 与 as_of_prev 双涨停，去掉
    DISTINCT 会让它的每个窗口行都按候选数翻倍，`(stock_id, trade_date)` 出现重复。
    """
    rows = _run_window(window_engine)
    assert rows, "窗口为空"
    keys = [(r["stock_id"], r["trade_date"]) for r in rows]
    assert len(keys) == len(set(keys)), "窗口查询出现扇出（cand 未 DISTINCT）"
    assert len({r["trade_date"] for r in rows}) > 2, "窗口被截成两天：N天M板/停牌缺日全错"
