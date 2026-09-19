"""`_AGG_SQL` 计数口径守卫（SQLite 内存库，无 Postgres，默认 `uv run pytest` 门禁）。

`aggregate_boards` 的计数口径是所有板块读接口的承重不变量（`member_count` /
`unresolved_count` / `priced_count` / 涨跌分桶 / `avg_pct` / `is_active` / 排序 tiebreak），
但真库用例带 `@pytest.mark.e2e`，CI 的默认门禁看不到——`count(px.pct_chg)` 改成 `count(*)`
或删掉 `WHERE b.is_active` 都能绿着合入。`_AGG_SQL` 只用 CTE、`count(*) FILTER`、LEFT JOIN、
`NULLS LAST`（SQLite 3.30+ 全部支持），故按 `test_limit_up_window_sql.py` 的先例，把这段 SQL
**直接 import 进 SQLite 内存库**跑，钉在默认门禁里。

本文件只覆盖 `_AGG_SQL`；`_HISTORY_SQL` 用 `(array_agg(...))[1]` 取首日 open，SQLite 没有
等价物（`group_concat` 需另写取首元素逻辑，测的就不是生产 SQL 了），其口径仍由
`test_concept_repo.py` 的 `test_member_history_stats_keeps_unknown_never_broken_as_none`
（`-m e2e`）守卫。

夹具刻意让每一处口径都有一个真实"反例行"，断言才不是空转：

- `BK0001`：1 个未解析（`stock_id IS NULL`）+ 1 个已解析但 as_of 当日无行情（**未解析 ≠
  无行情**）+ 涨/平/跌各 1 + 1 个当日有行但 `pct_chg IS NULL`（只进 `member_count`）。
- `BK0002`/`BK0012`：全未解析 → `avg_pct IS NULL`（排最后）。
- `BK0010`/`BK0011`：单成分、均价都是 2.0 → 钉 `board_code ASC` tiebreak。
- `BK0003`：`is_active=false`，带一个成分 → 必须整体缺席。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.repositories import concept_repo

AS_OF = "2026-09-18"
PREV = "2026-09-17"

_SCHEMA = [
    "CREATE TABLE concept_boards (board_code TEXT PRIMARY KEY, board_name TEXT, is_active INTEGER)",
    "CREATE TABLE concept_members (board_code TEXT, symbol TEXT, stock_id INTEGER)",
    "CREATE TABLE daily_quotes (stock_id INTEGER, trade_date TEXT, pct_chg REAL)",
]

# stock_id 101/102/103 当日涨/平/跌；104 当日行在但 pct_chg 为 NULL；105 只有前一日行情。
_QUOTES = [
    (101, AS_OF, 10.0),
    (102, AS_OF, 0.0),
    (103, AS_OF, -5.0),
    (104, AS_OF, None),
    (105, PREV, 7.0),
    (106, AS_OF, 2.0),
    (107, AS_OF, 2.0),
]

_BOARDS = [
    ("BK0001", "主板块", 1),
    ("BK0002", "全未解析甲", 1),
    ("BK0003", "停用板", 0),
    ("BK0010", "同均值甲", 1),
    ("BK0011", "同均值乙", 1),
    ("BK0012", "全未解析乙", 1),
]

_MEMBERS = [
    ("BK0001", "XNULL", None),  # 未解析
    ("BK0001", "XUNPRICED", 105),  # 已解析、当日无行情 → unpriced（不是 unresolved）
    ("BK0001", "XUP", 101),
    ("BK0001", "XFLAT", 102),
    ("BK0001", "XDOWN", 103),
    ("BK0001", "XNULLPCT", 104),  # 当日有行、pct_chg NULL → 不计入 priced/avg
    ("BK0002", "XN1", None),
    ("BK0003", "XOFF", 101),
    ("BK0010", "XT1", 106),
    ("BK0011", "XT2", 107),
    ("BK0012", "XN2", None),
]


@pytest.fixture()
def agg_engine() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        for stmt in _SCHEMA:
            conn.exec_driver_sql(stmt)
        conn.execute(
            text("INSERT INTO concept_boards VALUES (:a, :b, :c)"),
            [{"a": code, "b": name, "c": active} for code, name, active in _BOARDS],
        )
        conn.execute(
            text("INSERT INTO concept_members VALUES (:a, :b, :c)"),
            [{"a": code, "b": sym, "c": sid} for code, sym, sid in _MEMBERS],
        )
        conn.execute(
            text("INSERT INTO daily_quotes VALUES (:a, :b, :c)"),
            [{"a": sid, "b": day, "c": pct} for sid, day, pct in _QUOTES],
        )
    yield engine
    engine.dispose()


def _run_agg(engine: Engine) -> list[dict[str, Any]]:
    """跑生产 SQL 常量本体（不复制文本，见 `test_limit_up_window_sql.py` 的同一取舍）。"""
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(concept_repo._AGG_SQL),
                {"as_of": AS_OF, "limit": 100, "offset": 0},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _by_code(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["board_code"]: r for r in rows}


def test_unresolved_counts_only_null_stock_id(agg_engine: Engine) -> None:
    """`unresolved_count` 只数 `stock_id IS NULL`：已解析但当日无行情的是 unpriced，不是未解析。"""
    main = _by_code(_run_agg(agg_engine))["BK0001"]
    assert main["member_count"] == 6, "member_count 是全部成分行（含未解析/无行情/NULL pct）"
    assert main["unresolved_count"] == 1, "XUNPRICED 有 stock_id，不得计入 unresolved"


def test_priced_buckets_partition_and_null_pct_chg_is_excluded(agg_engine: Engine) -> None:
    """涨/平/跌三桶恰好分割 `priced_count`，且 NULL pct_chg 不进任何桶、不进均价。"""
    rows = _run_agg(agg_engine)
    for row in rows:
        assert row["priced_count"] == row["up_count"] + row["flat_count"] + row["down_count"], row

    main = _by_code(rows)["BK0001"]
    assert main["priced_count"] == 3, "pct_chg IS NULL 的行不算有行情（count(px.pct_chg)）"
    assert (main["up_count"], main["flat_count"], main["down_count"]) == (1, 1, 1)
    assert main["avg_pct"] == pytest.approx((10.0 + 0.0 - 5.0) / 3), (
        "avg_pct 分母是 priced_count，NULL pct_chg 不得当作 0 拉低均值"
    )
    assert _by_code(rows)["BK0002"]["avg_pct"] is None


def test_order_is_avg_desc_nulls_last_then_board_code_asc(agg_engine: Engine) -> None:
    """`avg_pct DESC NULLS LAST, board_code ASC`：2.0 的两板按码升序，NULL 均价排最后。"""
    codes = [r["board_code"] for r in _run_agg(agg_engine)]
    assert codes == ["BK0010", "BK0011", "BK0001", "BK0002", "BK0012"]


def test_inactive_board_is_filtered(agg_engine: Engine) -> None:
    """`WHERE b.is_active`：停用板整体缺席（成分行保留也不得进聚合）。"""
    assert "BK0003" not in _by_code(_run_agg(agg_engine))
