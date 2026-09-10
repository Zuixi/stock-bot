"""Rankings must ride the Task 2.1 indexes — Tier-2-equivalent plan guard.

Marked ``e2e`` because it needs the real Postgres: the default addopts run
(``-m 'not e2e and not bench'``) excludes it. Run explicitly with
``uv run pytest tests/test_rankings_index.py -v -m e2e``.
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import text

from app.core.database import async_session_factory, engine

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncGenerator[None, None]:
    """Each test gets a fresh event loop (function-scoped), so drop pooled
    connections in the loop that created them — otherwise the next test reuses
    a connection bound to the closed loop ("Event loop is closed")."""
    yield
    await engine.dispose()

# (query label, the index Task 2.1 created for it, the table it lives on)
_PLAN_CASES = [
    (
        "gainers",
        "idx_daily_quotes_date_pct",
        "daily_quotes",
        "SELECT stock_id FROM daily_quotes "
        "WHERE trade_date = (SELECT max(trade_date) FROM daily_quotes) "
        "AND pct_chg IS NOT NULL ORDER BY pct_chg DESC, stock_id ASC LIMIT 20",
    ),
    (
        "amount",
        "idx_daily_quotes_date_amount",
        "daily_quotes",
        "SELECT stock_id FROM daily_quotes "
        "WHERE trade_date = (SELECT max(trade_date) FROM daily_quotes) "
        "AND pct_chg IS NOT NULL ORDER BY amount DESC, stock_id ASC LIMIT 20",
    ),
    (
        "turnover_rate",
        "idx_daily_basic_date_turnover",
        "daily_basic_indicators",
        "SELECT stock_id FROM daily_basic_indicators "
        "WHERE trade_date = (SELECT max(trade_date) FROM daily_quotes) "
        "AND turnover_rate IS NOT NULL ORDER BY turnover_rate DESC, stock_id ASC LIMIT 20",
    ),
]


def _walk(node: dict[str, Any], out: list[dict[str, Any]]) -> None:
    out.append(node)
    for child in node.get("Plans", []):
        _walk(child, out)


@pytest.mark.parametrize(("label", "index_name", "relation", "sql"), _PLAN_CASES)
@pytest.mark.asyncio
async def test_ranking_query_plan_rides_its_index(
    label: str, index_name: str, relation: str, sql: str
) -> None:
    explain = f"EXPLAIN (FORMAT JSON) {sql}"
    async with async_session_factory() as db:
        plan = (await db.execute(text(explain))).scalar_one()

    # asyncpg may hand back the JSON column as a str depending on driver config.
    if isinstance(plan, str):
        plan = json.loads(plan)

    nodes: list[dict[str, Any]] = []
    _walk(plan[0]["Plan"], nodes)

    matching = [n for n in nodes if n.get("Index Name") == index_name]
    node_summary = [(n["Node Type"], n.get("Index Name")) for n in nodes]
    assert matching, f"{label}: expected {index_name} in plan, got {node_summary}"
    assert matching[0]["Node Type"] in {"Index Scan", "Index Only Scan"}, (
        f"{label}: {index_name} node is not an index scan: {matching[0]['Node Type']}"
    )
    assert any(n.get("Relation Name") == relation for n in matching), (
        f"{label}: {index_name} does not read {relation}"
    )
    # A Sort node means the planner abandoned the index-ordered scan.
    assert all(n["Node Type"] != "Sort" for n in nodes), (
        f"{label}: planner fell back to Sort: {node_summary}"
    )
