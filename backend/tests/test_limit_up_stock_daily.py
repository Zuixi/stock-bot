"""个股级涨停事实表（market_limit_up_stock_daily）：行构造 / 口径不变式 / EOD 增强边界。

默认门禁（不触 DB）：纯函数行构造四态（涨停 / 炸板 / 昨日涨停今日未板 / 停牌缺行）
+ enrichment「只写东财列」的机械守卫（SQL 的 SET 子句与行 dict 都不含判定列）。

真库口径不变式逐个打 `@pytest.mark.e2e`：直连 backend/.env 指向的 dev DB
（127.0.0.1:5433/stock_bot），只自建自清 `2026-09-08` 一天的行——历史日、无其它写入者。
运行：`uv run pytest tests/test_limit_up_stock_daily.py -m e2e`
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date
from decimal import Decimal
from typing import Any, get_args

import pytest
from sqlalchemy import text

from app.repositories import limit_up_repo, market_data_repo
from app.schemas.task import MarketDataJobType
from app.services import intraday_sentiment_service, limit_up_service

D1, D2, D3, D4 = date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8)
MARKET_DAYS = [D1, D2, D3, D4]
AS_OF, AS_OF_PREV = D4, D3
ACCEPTANCE_DAY = date(2026, 9, 8)  # 验收日：zt_count=75 / zb_count=39（真库实测）

# 判定列（恒由交易所限价决定）：东财 EOD 增强**永不**触碰，任何写路径都不得出现在
# SET 子句或行 dict 里。改动 enrich 写路径时这份清单就是防回归的锚。
JUDGMENT_COLUMNS = frozenset(
    {
        "is_lu",
        "touched",
        "is_zb",
        "streak",
        "days_span",
        "boards_in_window",
        "missing_days",
        "prev_is_lu",
    }
)

FACT_ROW_KEYS = frozenset(
    {
        "trade_date",
        "stock_id",
        "symbol",
        "is_lu",
        "touched",
        "is_zb",
        "prev_is_lu",
        "streak",
        "days_span",
        "boards_in_window",
        "missing_days",
        "seal_time",
        "seal_fund",
        "break_count",
        "fact_source",
        "enrich_source",
    }
)


def _win(
    stock_id: int,
    symbol: str,
    trade_date: date,
    *,
    is_lu: bool,
    touched: bool | None = None,
    streak_upto: int = 0,
) -> dict[str, Any]:
    """`fetch_limit_up_window` 的一行（字段集与 `_WINDOW_SQL` 的 SELECT 对齐）。"""
    return {
        "stock_id": stock_id,
        "symbol": symbol,
        "name": f"N{symbol}",
        "trade_date": trade_date,
        "close": 10.0,
        "open": 9.5,
        "high": 10.0,
        "amount": 1.0e8,
        "pre_close": 9.0,
        "up_limit": 10.0,
        "down_limit": 8.2,
        "is_lu": is_lu,
        "touched": is_lu if touched is None else touched,
        "streak_upto": streak_upto,
        "sw_l1_code": None,
        "sw_l1_name": None,
        "sw_l3_code": None,
        "sw_l3_name": None,
    }


def _four_state_inputs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """四态 + 一条炸板-only（不在候选窗口）的窗口行 / 炸板行。"""
    window_rows = [
        # 1) 两连板：今日收盘涨停（streak_upto=2）
        _win(1, "600001", D1, is_lu=False),
        _win(1, "600001", D2, is_lu=False),
        _win(1, "600001", D3, is_lu=True, streak_upto=1),
        _win(1, "600001", D4, is_lu=True, streak_upto=2),
        # 2) 昨日涨停今日炸板（在候选窗口内）
        _win(2, "600002", D1, is_lu=False),
        _win(2, "600002", D2, is_lu=False),
        _win(2, "600002", D3, is_lu=True, streak_upto=1),
        _win(2, "600002", D4, is_lu=False, touched=True),
        # 3) 昨日涨停今日未板（收盘未触板）
        _win(3, "600003", D3, is_lu=True, streak_upto=1),
        _win(3, "600003", D4, is_lu=False, touched=False),
        # 4) 昨日涨停今日停牌：as_of 无行 → 行集里不该出现
        _win(4, "600004", D3, is_lu=True, streak_upto=1),
        # 5) 窗口内停牌两日（missing_days=2），今日未板
        _win(5, "600005", D3, is_lu=True, streak_upto=1),
        _win(5, "600005", D4, is_lu=False, touched=False),
    ]
    broken_rows = [
        {"stock_id": 2, "symbol": "600002", "name": "N600002"},
        # 炸板-only：今日触板但昨日未涨停 → `_WINDOW_SQL` 的 cand 不含它
        {"stock_id": 7, "symbol": "600007", "name": "N600007"},
    ]
    return window_rows, broken_rows


def _by_id(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {r["stock_id"]: r for r in rows}


# ---------------------------------------------------------------------------
# 默认门禁：行构造四态（纯函数，不触 DB）
# ---------------------------------------------------------------------------


def test_row_construction_four_states() -> None:
    """涨停 / 炸板 / 昨日涨停今日未板 / 停牌缺行 四态的行集与判定列。"""
    window_rows, broken_rows = _four_state_inputs()
    rows = limit_up_service.build_stock_fact_rows(
        window_rows,
        broken_rows,
        as_of=AS_OF,
        as_of_prev=AS_OF_PREV,
        market_days=MARKET_DAYS,
    )
    by_id = _by_id(rows)

    # 停牌缺行：as_of 无行情行 → 事实表**不造行**（不是造一行 is_lu=false）
    assert set(by_id) == {1, 2, 3, 5, 7}
    assert len(rows) == len(by_id), "同一 stock_id 不得出现两行（窗口 ∪ 炸板需去重）"
    assert all(r["trade_date"] == AS_OF for r in rows)
    assert all(r["fact_source"] == "local_calc" for r in rows)
    assert all(r["enrich_source"] is None for r in rows)

    # 1) 收盘涨停：streak/跨度与梯队同源
    lu = by_id[1]
    assert (lu["is_lu"], lu["touched"], lu["is_zb"]) == (True, True, False)
    assert lu["prev_is_lu"] is True
    assert lu["streak"] == 2
    assert (lu["days_span"], lu["boards_in_window"], lu["missing_days"]) == (2, 2, 0)

    # 2) 炸板（在候选窗口内，昨日涨停）：一行、streak 归零、判定列为交易所限价口径
    zb = by_id[2]
    assert (zb["is_lu"], zb["touched"], zb["is_zb"]) == (False, True, True)
    assert zb["prev_is_lu"] is True
    assert zb["streak"] == 0
    assert (zb["days_span"], zb["boards_in_window"], zb["missing_days"]) == (2, 1, 0)

    # 3) 昨日涨停今日未板（未触板）：is_zb 必须为 false（touched AND NOT is_lu）
    broken_lu = by_id[3]
    assert (broken_lu["is_lu"], broken_lu["touched"], broken_lu["is_zb"]) == (False, False, False)
    assert broken_lu["prev_is_lu"] is True
    assert broken_lu["streak"] == 0
    assert (broken_lu["days_span"], broken_lu["boards_in_window"]) == (2, 1)

    # 5) 窗口内停牌两日：missing_days 显式落列（披露停牌，而不是"无行"）
    suspended = by_id[5]
    assert suspended["missing_days"] == 2
    assert suspended["prev_is_lu"] is True

    # 7) 炸板-only：不在候选窗口内 → 窗口派生列落 NULL（缺失 ≠ 0），streak 归零
    only_zb = by_id[7]
    assert (only_zb["is_lu"], only_zb["touched"], only_zb["is_zb"]) == (False, True, True)
    assert only_zb["prev_is_lu"] is False
    assert only_zb["streak"] == 0
    assert only_zb["days_span"] is None
    assert only_zb["boards_in_window"] is None
    assert only_zb["missing_days"] is None


def test_row_construction_never_fills_missing_with_zero() -> None:
    """东财独有列（封板时间/封单/炸板次数）本地路径恒 NULL，绝不填 0。"""
    window_rows, broken_rows = _four_state_inputs()
    rows = limit_up_service.build_stock_fact_rows(
        window_rows,
        broken_rows,
        as_of=AS_OF,
        as_of_prev=AS_OF_PREV,
        market_days=MARKET_DAYS,
    )
    for row in rows:
        assert row["seal_time"] is None
        assert row["seal_fund"] is None
        assert row["break_count"] is None
        assert row["seal_fund"] != 0
        assert row["break_count"] != 0


def test_row_construction_keys_are_the_table_contract() -> None:
    """落库 dict 的键集 == 表列集（少列会让 upsert 静默丢字段）。"""
    window_rows, broken_rows = _four_state_inputs()
    rows = limit_up_service.build_stock_fact_rows(
        window_rows,
        broken_rows,
        as_of=AS_OF,
        as_of_prev=AS_OF_PREV,
        market_days=MARKET_DAYS,
        fact_source="backfill",
    )
    assert {frozenset(r) for r in rows} == {FACT_ROW_KEYS}
    assert {r["fact_source"] for r in rows} == {"backfill"}


# ---------------------------------------------------------------------------
# 默认门禁：EOD 增强只允许写东财独有列
# ---------------------------------------------------------------------------


def test_enrich_sql_set_clause_has_no_judgment_columns() -> None:
    """SQL 的 SET 子句只允许 4 列（seal_time/seal_fund/break_count/enrich_source）。"""
    sql = market_data_repo.build_enrich_sql_for_audit()
    assert "SET" in sql.upper() and "WHERE" in sql.upper()
    set_clause = sql.upper().split("SET", 1)[1].split("WHERE", 1)[0]
    assigned = {
        part.split("=", 1)[0].strip().lower() for part in set_clause.split(",") if "=" in part
    }
    assert assigned == {"seal_time", "seal_fund", "break_count", "enrich_source"}
    assert assigned.isdisjoint(JUDGMENT_COLUMNS), assigned & JUDGMENT_COLUMNS


def test_build_enriched_rows_drops_judgment_and_pool_only_fields() -> None:
    """池行 → 增强行：只留东财独有的四列，判定列/池统计列（streak/days/boards）全丢。"""
    pool_rows: list[dict[str, Any]] = [
        {
            "symbol": "600001",
            "name": "N600001",
            "streak": 3,
            "days": 2,
            "boards": 3,
            "seal_time": "09:31:00",
            "seal_fund": 12345678.5,
            "break_count": 0,
            "board_name": "半导体",
            "amount": 1.0e8,
        }
    ]
    rows = intraday_sentiment_service.build_enriched_rows(pool_rows)
    assert len(rows) == 1
    assert set(rows[0]) == {"symbol", "seal_time", "seal_fund", "break_count"}
    assert set(rows[0]).isdisjoint(JUDGMENT_COLUMNS)
    assert rows[0]["seal_time"] == "09:31:00"
    assert rows[0]["seal_fund"] == Decimal("12345678.5")
    assert rows[0]["break_count"] == 0


def test_build_enriched_rows_keeps_missing_as_none() -> None:
    """池里缺的字段落 NULL，绝不落 0；无 symbol 的池行丢弃。"""
    rows = intraday_sentiment_service.build_enriched_rows(
        [{"symbol": "600002"}, {"symbol": None, "seal_time": "10:00:00"}]
    )
    assert len(rows) == 1
    assert rows[0]["seal_time"] is None
    assert rows[0]["seal_fund"] is None
    assert rows[0]["break_count"] is None


async def test_enrich_stock_facts_from_pool_passes_only_eastmoney_rows(monkeypatch) -> None:
    """service 传给 repo 的行 dict 不得含判定列（防回归：SQL 与行 dict 双守卫）。"""
    captured: dict[str, Any] = {}

    async def fake_enrich(db: Any, as_of: date, rows: list[dict[str, Any]]) -> int:
        captured.update({"db": db, "as_of": as_of, "rows": rows})
        return 3

    monkeypatch.setattr(market_data_repo, "enrich_limit_up_stock_daily_from_pool", fake_enrich)
    pool_rows = [
        {
            "symbol": "600001",
            "streak": 2,
            "seal_time": "09:31:00",
            "seal_fund": 1.0,
            "break_count": 1,
        }
    ]
    n = await limit_up_service.enrich_stock_facts_from_pool("SENTINEL_DB", AS_OF, pool_rows)
    assert n == 3
    assert captured["db"] == "SENTINEL_DB"
    assert captured["as_of"] == AS_OF
    rows = captured["rows"]
    assert len(rows) == 1
    assert set(rows[0]).isdisjoint(JUDGMENT_COLUMNS)
    assert "streak" not in rows[0]


async def test_enrich_stock_facts_from_pool_empty_pool_is_noop(monkeypatch) -> None:
    """空池 / 无 symbol 池：不落 SQL，返回 0。"""
    called: list[Any] = []

    async def fake_enrich(db: Any, as_of: date, rows: list[dict[str, Any]]) -> int:
        called.append(rows)
        return 0

    monkeypatch.setattr(market_data_repo, "enrich_limit_up_stock_daily_from_pool", fake_enrich)
    assert await limit_up_service.enrich_stock_facts_from_pool(None, AS_OF, []) == 0
    assert called == []


def test_broken_board_predicate_is_the_breadth_predicate() -> None:
    """炸板选行谓词必须与 `_BREADTH_SQL` 的 zb_count 逐字一致（防两处口径漂移）。"""
    broken_sql = limit_up_repo.build_day_broken_sql_for_audit()
    for fragment in ("q.high >= l.up_limit - 0.005", "q.close < l.up_limit - 0.005"):
        assert fragment in broken_sql, fragment
        assert fragment in limit_up_repo._BREADTH_SQL, fragment
    join = "JOIN stock_price_limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date"
    assert join in broken_sql


def _collapse(sql: str) -> str:
    """折叠空白后再比较：`_BREADTH_SQL` 里 `l.up_limit  - 0.005` 有两个空格（与下一行
    `<=` 对齐），逐字节比较会假红；谓词**语义**一致才是要守的东西。"""
    return " ".join(sql.split())


def test_is_lu_predicate_is_the_breadth_zt_count_predicate() -> None:
    """默认门禁守卫：`is_lu` 与 `zt_count` 必须是同一个谓词（同源，非各写一份）。

    这两处一旦漂移，「个股 is_lu 数 == 聚合 zt_count」这条口径不变式就静默失效——而真库
    不变式用例在 e2e 标记后面（默认 addopts 排除），所以这里补一个**默认门禁能红**的守卫：
    改动列名（close↔high）/ 比较符 / 阈值 任意一处都会红。
    """
    window_sql = _collapse(limit_up_repo.build_window_sql_for_explain())
    breadth_sql = _collapse(limit_up_repo._BREADTH_SQL)
    predicate = "q.close >= l.up_limit - 0.005"
    # 同一个谓词文本必须同时出现在两侧（口径同源）
    assert predicate in window_sql, window_sql
    assert predicate in breadth_sql, breadth_sql
    # is_lu 必须**恰好**由该谓词定义（COALESCE 到 false），不许换列/换阈值/换比较符
    assert f"COALESCE({predicate}, false) AS is_lu" in window_sql
    # zt_count 必须**恰好**由该谓词定义
    assert f"count(*) FILTER (WHERE {predicate}) AS zt_count" in breadth_sql


def test_fact_is_lu_count_equals_window_limit_up_count() -> None:
    """默认门禁守卫：事实行集的 is_lu 计数 == 窗口 as_of 行的涨停只数。

    行集 = 窗口 `as_of` 行 ∪ 炸板行 ⇒ union 只能**加**非涨停的炸板票：
    - 涨停票一只都不能被丢（丢一只 `count(is_lu) < zt_count`，真库不变式红）；
    - 同 `stock_id` 的炸板行不得覆盖窗口行的判定（否则 is_lu 从 true 变 false）；
    - 去重（同一票两行会让 count 虚高）。
    """
    window_rows = [
        _win(11, "600011", D3, is_lu=False),
        _win(11, "600011", D4, is_lu=True, streak_upto=1),
        _win(12, "600012", D3, is_lu=True, streak_upto=1),
        _win(12, "600012", D4, is_lu=True, streak_upto=2),
        _win(13, "600013", D4, is_lu=False, touched=False),  # 昨日涨停今日未板
    ]
    broken_rows = [
        # 防御态：涨停票同时被炸板查询命中（两谓词互斥，正常不会出现）——窗口行必须赢
        {"stock_id": 11, "symbol": "600011", "name": "N600011"},
        {"stock_id": 14, "symbol": "600014", "name": "N600014"},  # 炸板-only
    ]
    rows = limit_up_service.build_stock_fact_rows(
        window_rows,
        broken_rows,
        as_of=AS_OF,
        as_of_prev=AS_OF_PREV,
        market_days=MARKET_DAYS,
    )
    by_id = _by_id(rows)
    window_lu = [r for r in window_rows if r["trade_date"] == AS_OF and r["is_lu"]]
    assert len(rows) == len(by_id) == 4, "union 必须去重且不丢行"
    assert set(by_id) == {11, 12, 13, 14}
    assert sum(1 for r in rows if r["is_lu"]) == len(window_lu) == 2
    assert by_id[11]["is_lu"] is True, "炸板行不得覆盖窗口涨停行的判定"
    assert by_id[14]["is_lu"] is False and by_id[14]["is_zb"] is True
    assert by_id[13]["is_lu"] is False and by_id[13]["is_zb"] is False


class _RecordingSession:
    """只记录 commit 顺序的假 session（编排顺序是硬约束，必须可断言）。"""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def commit(self) -> None:
        self.events.append("commit")


def _patch_persist(monkeypatch, events: list[str]) -> None:
    from app.services import limit_up_service as lus

    async def fake_persist(db: Any, as_of: date, *, fact_source: str = "local_calc"):
        events.append("persist")
        return {"rows": 178, "is_lu": 75, "is_zb": 39, "prev_is_lu": 95}

    monkeypatch.setattr(lus, "persist_stock_facts", fake_persist)


async def test_persist_and_enrich_commits_facts_before_enrichment(monkeypatch) -> None:
    """顺序硬约束：落判定列 → commit →（才）东财 EOD 增强。"""
    events: list[str] = []
    _patch_persist(monkeypatch, events)
    from app.services import intraday_sentiment_service as iss
    from app.services import limit_up_service as lus

    async def fake_fetch(trade_date: str) -> list[dict[str, Any]]:
        events.append(f"fetch:{trade_date}")
        return [{"symbol": "600001", "seal_time": "09:31:00", "seal_fund": 1.0, "break_count": 0}]

    async def fake_enrich(db: Any, as_of: date, pool_rows: list[dict[str, Any]]) -> int:
        events.append("enrich")
        return 1

    monkeypatch.setattr(iss, "fetch_intraday_pool", fake_fetch)
    monkeypatch.setattr(lus, "enrich_stock_facts_from_pool", fake_enrich)

    result = await lus.persist_and_enrich_stock_facts(_RecordingSession(events), AS_OF)
    assert events == ["persist", "commit", "fetch:20260908", "enrich"]
    assert result["enriched"] == 1 and result["enrich_reason"] is None
    assert (result["rows"], result["is_lu"], result["is_zb"]) == (178, 75, 39)


async def test_persist_and_enrich_is_best_effort_on_pool_failure(monkeypatch) -> None:
    """东财外呼抛错：只记原因码，事实施工结果原样返回、不抛错（B 类优先于 A 类）。"""
    events: list[str] = []
    _patch_persist(monkeypatch, events)
    from app.services import intraday_sentiment_service as iss
    from app.services import limit_up_service as lus

    async def fake_fetch(trade_date: str) -> list[dict[str, Any]]:
        raise RuntimeError("eastmoney down")

    monkeypatch.setattr(iss, "fetch_intraday_pool", fake_fetch)

    result = await lus.persist_and_enrich_stock_facts(_RecordingSession(events), AS_OF)
    assert result["enrich_reason"] == "pool_fetch_failed"
    assert result["enriched"] == 0
    assert result["rows"] == 178 and result["is_lu"] == 75
    assert events == ["persist", "commit"]


async def test_persist_and_enrich_distinguishes_empty_pool_and_no_rows(monkeypatch) -> None:
    """空池 / 池有票但 0 命中必须可区分（后者才是"数据丢了"的信号）。"""
    from app.services import intraday_sentiment_service as iss
    from app.services import limit_up_service as lus

    async def fake_enrich(db: Any, as_of: date, pool_rows: list[dict[str, Any]]) -> int:
        return 0

    monkeypatch.setattr(lus, "enrich_stock_facts_from_pool", fake_enrich)

    async def empty_fetch(trade_date: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(iss, "fetch_intraday_pool", empty_fetch)
    events: list[str] = []
    _patch_persist(monkeypatch, events)
    empty = await lus.persist_and_enrich_stock_facts(_RecordingSession(events), AS_OF)
    assert empty["enrich_reason"] == "empty_pool"

    async def some_fetch(trade_date: str) -> list[dict[str, Any]]:
        return [{"symbol": "600001", "seal_time": "09:31:00", "seal_fund": 1.0, "break_count": 0}]

    monkeypatch.setattr(iss, "fetch_intraday_pool", some_fetch)
    events2: list[str] = []
    _patch_persist(monkeypatch, events2)
    no_rows = await lus.persist_and_enrich_stock_facts(_RecordingSession(events2), AS_OF)
    assert no_rows["enrich_reason"] == "no_fact_rows"


async def test_empty_pool_logs_a_warning(monkeypatch, caplog) -> None:
    """Ruling 10：空池必须**警告**（不只是原因码）——A 类封板明细当天丢了要可见。

    与"池有票但 0 命中"并列：两种都 `logger.warning`，区别只在原因码。
    """
    from app.services import intraday_sentiment_service as iss
    from app.services import limit_up_service as lus

    async def empty_fetch(trade_date: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(iss, "fetch_intraday_pool", empty_fetch)
    events: list[str] = []
    _patch_persist(monkeypatch, events)

    with caplog.at_level("WARNING"):
        empty = await lus.persist_and_enrich_stock_facts(_RecordingSession(events), AS_OF)

    assert empty["enrich_reason"] == lus.ENRICH_REASON_EMPTY_POOL
    assert "pool is empty" in caplog.text
    assert str(AS_OF) in caplog.text


async def test_enrichment_write_failure_is_best_effort(monkeypatch, caplog) -> None:
    """增强**写库**抛错也必须降级为原因码（Task 8）：facts 已 commit，绝不回滚/上抛。

    变异：把 `enrich_stock_facts_from_pool` 的调用移出 `try` → 本例抛 `RuntimeError` 变红。
    """
    from app.services import intraday_sentiment_service as iss
    from app.services import limit_up_service as lus

    async def some_fetch(trade_date: str) -> list[dict[str, Any]]:
        return [{"symbol": "600001", "seal_time": "09:31:00", "seal_fund": 1.0, "break_count": 0}]

    async def boom(db: Any, as_of: date, pool_rows: list[dict[str, Any]]) -> int:
        raise RuntimeError("write conflict")

    monkeypatch.setattr(iss, "fetch_intraday_pool", some_fetch)
    monkeypatch.setattr(lus, "enrich_stock_facts_from_pool", boom)
    events: list[str] = []
    _patch_persist(monkeypatch, events)

    with caplog.at_level("WARNING"):
        result = await lus.persist_and_enrich_stock_facts(_RecordingSession(events), AS_OF)

    assert result["enrich_reason"] == lus.ENRICH_REASON_WRITE_FAILED
    assert result["enriched"] == 0
    # 事实结果原样返回、facts 已 commit（事件序列里没有 rollback）
    assert (result["rows"], result["is_lu"], result["is_zb"]) == (178, 75, 39)
    assert events == ["persist", "commit"]
    assert "write failed" in caplog.text


# ---------------------------------------------------------------------------
# 默认门禁：双轨制接线（schema / worker / scheduler）
# ---------------------------------------------------------------------------


def test_job_types_registered() -> None:
    members = set(get_args(MarketDataJobType))
    assert "limit_up_stock_facts" in members
    # 15:05 独立增强 job 已被裁决取消（UPDATE-only 在当日行存在前必然 0 命中）
    assert "intraday_pool_eod" not in members


async def test_worker_dispatches_limit_up_stock_facts(monkeypatch) -> None:
    called: list[Any] = []

    async def fake_pipeline(db: Any, as_of: date, *, fact_source: str = "local_calc"):
        called.append((db, as_of, fact_source))
        return {
            "rows": 178,
            "is_lu": 75,
            "is_zb": 39,
            "prev_is_lu": 95,
            "enriched": 12,
            "enrich_reason": None,
        }

    from app.services import limit_up_service as lus

    monkeypatch.setattr(lus, "persist_and_enrich_stock_facts", fake_pipeline)
    from tests.test_market_data_worker import _fake_session_factory

    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    from app.workers.market_data_worker import MarketDataWorker

    result = await MarketDataWorker().process(
        task_id=None, payload={"type": "limit_up_stock_facts", "params": {"trade_date": "20260908"}}
    )
    assert result["status"] == "completed" and result["type"] == "limit_up_stock_facts"
    assert result["is_lu"] == 75 and result["rows"] == 178 and result["enriched"] == 12
    assert called[0][1] == ACCEPTANCE_DAY


async def test_worker_limit_up_stock_facts_alerts_on_enrichment_loss(monkeypatch) -> None:
    """worker 轨道与定时轨道同款可见性：增强丢数据也要进 `job:failures`。"""

    async def fake_pipeline(db: Any, as_of: date, *, fact_source: str = "local_calc"):
        return {
            "rows": 178,
            "is_lu": 75,
            "is_zb": 39,
            "prev_is_lu": 95,
            "enriched": 0,
            "enrich_reason": "no_fact_rows",
        }

    recorded: list[Any] = []

    async def fake_record(job_id: str, error: str, **kwargs: Any) -> None:
        recorded.append((job_id, error))

    from app.services import job_alert_service as jas
    from app.services import limit_up_service as lus

    monkeypatch.setattr(lus, "persist_and_enrich_stock_facts", fake_pipeline)
    monkeypatch.setattr(jas, "record_job_failure", fake_record)
    from tests.test_market_data_worker import _fake_session_factory

    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    from app.workers.market_data_worker import MarketDataWorker

    result = await MarketDataWorker().process(
        task_id=None, payload={"type": "limit_up_stock_facts", "params": {"trade_date": "20260908"}}
    )
    assert result["status"] == "completed"
    assert recorded and recorded[0][0] == "limit_up_stock_facts"
    assert "no_fact_rows" in recorded[0][1]


async def test_worker_limit_up_stock_facts_falls_back_to_latest_complete_day(monkeypatch) -> None:
    """worker 不给 trade_date → 取最新完整交易日（与定时轨道同参），不是"今天"。"""
    called: list[Any] = []

    async def fake_pipeline(db: Any, as_of: date, *, fact_source: str = "local_calc"):
        called.append(as_of)
        return {
            "rows": 1,
            "is_lu": 1,
            "is_zb": 0,
            "prev_is_lu": 0,
            "enriched": 0,
            "enrich_reason": "empty_pool",
        }

    class _MarketDay:
        day = ACCEPTANCE_DAY

    async def fake_resolve(db: Any, **kwargs: Any) -> _MarketDay:
        return _MarketDay()

    from app.services import limit_up_service as lus
    from app.services import market_day_service as mds

    monkeypatch.setattr(lus, "persist_and_enrich_stock_facts", fake_pipeline)
    monkeypatch.setattr(mds, "resolve_latest_complete_day", fake_resolve)
    from tests.test_market_data_worker import _fake_session_factory

    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    from app.workers.market_data_worker import MarketDataWorker

    result = await MarketDataWorker().process(
        task_id=None, payload={"type": "limit_up_stock_facts"}
    )
    assert result["status"] == "completed"
    assert called == [ACCEPTANCE_DAY]


async def test_job_alerts_when_eod_enrichment_hits_no_rows(monkeypatch) -> None:
    """增强 0 命中/失败必须进 `job:failures`（A 类数据静默丢失要可见），且 job 不失败。"""
    from contextlib import asynccontextmanager

    class _Session:
        async def commit(self) -> None:
            return None

    @asynccontextmanager
    async def fake_session_factory():
        yield _Session()

    class _MarketDay:
        day = ACCEPTANCE_DAY

    async def fake_resolve(db: Any, **kwargs: Any) -> _MarketDay:
        return _MarketDay()

    async def fake_pipeline(db: Any, as_of: date, *, fact_source: str = "local_calc"):
        return {
            "rows": 178,
            "is_lu": 75,
            "is_zb": 39,
            "prev_is_lu": 95,
            "enriched": 0,
            "enrich_reason": "no_fact_rows",
        }

    alerts: list[Any] = []

    async def fake_alert(job_ids: Any, exc: BaseException) -> None:
        alerts.append((job_ids, exc))

    from app.scheduler import jobs as scheduler_jobs
    from app.services import limit_up_service as lus
    from app.services import market_day_service as mds

    monkeypatch.setattr(lus, "persist_and_enrich_stock_facts", fake_pipeline)
    monkeypatch.setattr(mds, "resolve_latest_complete_day", fake_resolve)
    monkeypatch.setattr("app.core.database.async_session_factory", fake_session_factory)
    monkeypatch.setattr(scheduler_jobs, "_alert_job_failure", fake_alert)
    # `_is_workday` 是纯 weekday 判断（jobs.py）：不打桩则周六/周日早返回、alerts 为空
    # → 测试只在工作日绿。同仓其它驱动 workday 守卫 job 的用例都显式钉死它。
    monkeypatch.setattr(scheduler_jobs, "_is_workday", lambda: True)

    await scheduler_jobs.limit_up_stock_facts_job()

    assert alerts, "0 命中必须登记 job failure"
    assert alerts[0][0] == "limit_up_stock_facts"
    assert "no_fact_rows" in str(alerts[0][1])


async def test_scheduler_registers_facts_job_with_shanghai_cron() -> None:
    """17:20 单 job（落库 + EOD 增强）；15:05 的独立增强 job 不得存在。"""
    from apscheduler.triggers.cron import CronTrigger

    from app.scheduler.runner import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()
    try:
        job = scheduler.get_job("limit_up_stock_facts")
        assert job is not None, "limit_up_stock_facts job not registered"
        assert isinstance(job.trigger, CronTrigger)
        assert str(job.trigger.timezone) == "Asia/Shanghai"
        fields = {f.name: str(f) for f in job.trigger.fields}
        assert fields["day_of_week"] == "mon-fri", fields
        assert fields["hour"] == "17", fields
        assert fields["minute"] == "20", fields
        # 盘后日频任务：不设 per-job 宽限 → 继承 job_defaults(None)，停摆后迟到也补跑
        assert job.misfire_grace_time is None
        assert scheduler.get_job("intraday_pool_eod") is None
    finally:
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 真库（`-m e2e`）：口径不变式
# ---------------------------------------------------------------------------


@pytest.fixture
async def _clean_acceptance_day() -> AsyncGenerator[None, None]:
    """只清理验收日一天（历史日，无其它写入者）；前后各清一次，失败路径也不留脏行。"""
    from app.core.database import async_session_factory, engine

    async def _delete() -> None:
        async with async_session_factory() as db:
            await db.execute(
                text("DELETE FROM market_limit_up_stock_daily WHERE trade_date = :d"),
                {"d": ACCEPTANCE_DAY},
            )
            await db.commit()

    await _delete()
    try:
        yield
    finally:
        await _delete()
        await engine.dispose()


@pytest.mark.e2e
async def test_broken_board_rows_match_breadth_zb_count(_clean_acceptance_day: None) -> None:
    """真库：炸板选行数 == `fetch_day_breadth` 的 zb_count（2026-09-08 实测 39）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        rows = await limit_up_repo.fetch_day_broken_board_stocks(db, ACCEPTANCE_DAY)
        breadth = await limit_up_repo.fetch_day_breadth(db, ACCEPTANCE_DAY)
    assert breadth["zb_count"] == 39
    assert len(rows) == breadth["zb_count"]
    assert len({r["stock_id"] for r in rows}) == len(rows), "炸板行按 stock_id 唯一"
    assert all(r["symbol"] and r["stock_id"] for r in rows)


@pytest.mark.e2e
async def test_persist_stock_facts_invariants_on_acceptance_day(
    _clean_acceptance_day: None,
) -> None:
    """真库：count(is_lu) == market_sentiment_daily.zt_count；is_zb == zb_count（75/39）。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        result = await limit_up_service.persist_stock_facts(db, ACCEPTANCE_DAY)
        await db.commit()
        counts = (
            (
                await db.execute(
                    text(
                        "SELECT count(*) AS rows,"
                        " count(*) FILTER (WHERE is_lu) AS is_lu,"
                        " count(*) FILTER (WHERE is_zb) AS is_zb,"
                        " count(*) FILTER (WHERE prev_is_lu) AS prev_is_lu,"
                        " count(*) FILTER (WHERE seal_fund IS NOT NULL) AS sealed,"
                        " count(*) FILTER (WHERE enrich_source IS NOT NULL) AS enriched,"
                        " count(*) FILTER (WHERE fact_source <> 'local_calc') AS other_source"
                        " FROM market_limit_up_stock_daily WHERE trade_date = :d"
                    ),
                    {"d": ACCEPTANCE_DAY},
                )
            )
            .mappings()
            .one()
        )
        sentiment = (
            (
                await db.execute(
                    text(
                        "SELECT zt_count, zb_count FROM market_sentiment_daily"
                        " WHERE trade_date = :d"
                    ),
                    {"d": ACCEPTANCE_DAY},
                )
            )
            .mappings()
            .one()
        )

    assert counts["is_lu"] == 75
    assert counts["is_zb"] == 39
    assert counts["is_lu"] == sentiment["zt_count"], "口径不变式：个股 is_lu 数 == 聚合 zt_count"
    assert counts["is_zb"] == sentiment["zb_count"]
    # 行集 = 当日候选窗口行(151) ∪ 炸板-only(27)；不是 151 也不是 151+39
    assert counts["rows"] == 178
    assert result["rows"] == 178 and result["is_lu"] == 75 and result["is_zb"] == 39
    # 东财独有列本地路径恒 NULL（真库层再确认"缺失 ≠ 0"）
    assert counts["sealed"] == 0 and counts["enriched"] == 0
    assert counts["other_source"] == 0


@pytest.mark.e2e
async def test_eod_enrichment_never_touches_judgment_columns(_clean_acceptance_day: None) -> None:
    """真库：EOD 增强只写东财列 —— is_lu/is_zb 计数与判定列逐行零变化。"""
    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        await limit_up_service.persist_stock_facts(db, ACCEPTANCE_DAY)
        await db.commit()
        before = (
            (
                await db.execute(
                    text(
                        "SELECT symbol, is_lu, touched, is_zb, prev_is_lu, streak, days_span,"
                        " boards_in_window, missing_days, fact_source"
                        " FROM market_limit_up_stock_daily WHERE trade_date = :d ORDER BY symbol"
                    ),
                    {"d": ACCEPTANCE_DAY},
                )
            )
            .mappings()
            .all()
        )
        symbols = [r["symbol"] for r in before[:2]]
        pool_rows = [
            {
                "symbol": symbols[0],
                "name": "X",
                "streak": 9,
                "days": 9,
                "boards": 9,
                "seal_time": "09:31:00",
                "seal_fund": 12345678.5,
                "break_count": 2,
            },
            {
                "symbol": symbols[1],
                "name": "Y",
                "streak": 1,
                "seal_time": "10:02:03",
                "seal_fund": 100.0,
                "break_count": 0,
            },
            {"symbol": "000000", "name": "NOT_IN_TABLE", "seal_time": "11:11:11"},
        ]
        enriched = await limit_up_service.enrich_stock_facts_from_pool(
            db, ACCEPTANCE_DAY, pool_rows
        )
        await db.commit()
        after = (
            (
                await db.execute(
                    text(
                        "SELECT symbol, is_lu, touched, is_zb, prev_is_lu, streak, days_span,"
                        " boards_in_window, missing_days, fact_source,"
                        " seal_time, seal_fund, break_count, enrich_source"
                        " FROM market_limit_up_stock_daily WHERE trade_date = :d ORDER BY symbol"
                    ),
                    {"d": ACCEPTANCE_DAY},
                )
            )
            .mappings()
            .all()
        )

    assert enriched == 2, "池里不存在的 symbol 不得计入"
    assert len(after) == len(before)
    judgment = tuple(JUDGMENT_COLUMNS | {"fact_source"})
    for b, a in zip(before, after, strict=True):
        for col in judgment:
            assert a[col] == b[col], f"{a['symbol']}.{col} 被 EOD 增强改写"
    by_symbol = {r["symbol"]: r for r in after}
    assert by_symbol[symbols[0]]["seal_time"] == "09:31:00"
    assert by_symbol[symbols[0]]["seal_fund"] == Decimal("12345678.5000")
    assert by_symbol[symbols[0]]["break_count"] == 2
    assert by_symbol[symbols[0]]["enrich_source"] == "eastmoney_eod"
    assert by_symbol[symbols[1]]["break_count"] == 0, "0 是合法观测值，不能当缺失"
    assert sum(1 for r in after if r["is_lu"]) == 75, "口径不变式：EOD 增强后 is_lu 计数零变化"
