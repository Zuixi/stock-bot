"""Pure unit tests for the per-stock SW chain assembly (stock-detail breadcrumb)."""

from app.services.market_service import assemble_sw_chain


def _node(code: str, level: int, name: str) -> dict:
    return {
        "industry_code": code,
        "level": level,
        "industry_name": name,
    }


def test_assemble_sw_chain_orders_l3_first_rows_into_l1_l2_l3():
    # Recursive CTE returns L3 first, then ancestors (L2, L1) — any order must work
    rows = [
        _node("110702", 3, "生猪养殖"),
        _node("110400", 2, "养殖业"),
        _node("110000", 1, "农林牧渔"),
    ]
    assert assemble_sw_chain(rows) == [
        {"level": 1, "code": "110000", "name": "农林牧渔"},
        {"level": 2, "code": "110400", "name": "养殖业"},
        {"level": 3, "code": "110702", "name": "生猪养殖"},
    ]


def test_assemble_sw_chain_empty_rows_yield_empty_chain():
    assert assemble_sw_chain([]) == []


def test_assemble_sw_chain_partial_tree_degrades_gracefully():
    # Parent lookup broken mid-tree: keep found levels, never raise
    rows = [_node("110702", 3, "生猪养殖")]
    assert assemble_sw_chain(rows) == [
        {"level": 3, "code": "110702", "name": "生猪养殖"},
    ]


def test_assemble_sw_chain_keeps_single_entry_per_level():
    # Malformed duplicate levels must not explode — last one wins deterministically
    rows = [
        _node("110000", 1, "农林牧渔"),
        _node("110000", 1, "农林牧渔(旧)"),
    ]
    assert assemble_sw_chain(rows) == [
        {"level": 1, "code": "110000", "name": "农林牧渔(旧)"},
    ]
