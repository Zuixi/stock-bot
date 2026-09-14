"""连板窗口 SQL 的两条不变量：无扇出、计划不回退。

`-m e2e`（需要真 Postgres）。运行：uv run pytest tests/test_limit_up_repo.py -v -m e2e
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import text

from app.core.database import async_session_factory, engine
from app.repositories import limit_up_repo

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncGenerator[None, None]:
    yield
    await engine.dispose()


async def _window_args(db: Any) -> dict[str, Any]:
    as_of = await limit_up_repo.latest_quote_date(db)
    assert as_of is not None, "daily_quotes 为空，先跑 quotes 回补"
    days = await limit_up_repo.list_recent_trade_dates(db, as_of, 16)
    assert len(days) >= 2
    return {"as_of": as_of, "as_of_prev": days[-2], "window_start": days[0]}


async def test_window_has_no_fanout() -> None:
    """同一 (stock_id, trade_date) 不得出现两次。

    候选集是「今日 ∪ 昨日」涨停并集，两日都涨停的票在 cand 里出现两次会让
    JOIN cand 扇出：实测涨停数从真值 75 变 94，并凭空造出 8 连板。加 DISTINCT 才一致。
    SW 侧同理：sw_industry_members 若一股多行业，LEFT JOIN 也会扇出。
    """
    async with async_session_factory() as db:
        rows = await limit_up_repo.fetch_limit_up_window(db, **await _window_args(db))
    keys = [(r["stock_id"], r["trade_date"]) for r in rows]
    assert len(keys) == len(set(keys)), "窗口查询出现扇出（cand 未 DISTINCT 或成员表一股多行业）"
    assert rows, "窗口为空：stock_price_limits 尚未回补"
    # 窗口必须回整段交易日，而不只是 as_of/as_of_prev 两天：
    # 否则 boards_in_window 恒 ≤ 2、每只票 missing_days 恒 = 窗口天数 - 2。
    assert len({r["trade_date"] for r in rows}) > 2, "窗口被截成两天：N天M板/停牌缺日全错"


async def test_window_rides_stock_date_unique_index() -> None:
    """窗口回查必须走 uq_daily_quotes_stock_date，且不得退化为逐股 probe 的 nested loop。"""
    async with async_session_factory() as db:
        args = await _window_args(db)
        explain = await db.execute(
            text("EXPLAIN (FORMAT JSON) " + limit_up_repo.build_window_sql_for_explain()),
            args,
        )
        plan = explain.scalar_one()
    if isinstance(plan, str):
        plan = json.loads(plan)
    nodes: list[dict[str, Any]] = []
    _walk(plan[0]["Plan"], nodes)
    index_names = {n.get("Index Name") for n in nodes}
    # uq_daily_quotes_stock_date 与 idx_daily_quotes_stock_date 列完全相同，规划器按 OID
    # 任选其一（2026-09-14 实测：本库选 idx_daily_quotes_stock_date；断言 uq_ 会误红，
    # 且 T9 删掉 idx_ 之后才轮到 uq_）。
    assert index_names & {"uq_daily_quotes_stock_date", "idx_daily_quotes_stock_date"}, index_names
    assert not any(n["Node Type"] == "Seq Scan" and n.get("Relation Name") == "daily_quotes"
                   for n in nodes), "daily_quotes 全表扫描"


def _walk(node: dict[str, Any], out: list[dict[str, Any]]) -> None:
    out.append(node)
    for child in node.get("Plans", []):
        _walk(child, out)
