# backend/tests/test_xuantian_client.py
"""XuantianClient 纯函数层：5 列原始行 → 标准行；周期解析/哨兵剔除/列映射。不触网。"""

from __future__ import annotations

import json
import pathlib
from datetime import date

from app.core.providers.xuantian_client import parse_capacity_rows

CAPACITY_COLUMNS = ("sow_inventory", "pork_output", "hog_inventory", "hog_slaughter_quarterly")

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "xuantian_capacity.json"


def test_yearly_row_parses_to_dec31():
    rows = parse_capacity_rows([["2009", "4910", "4932.85", "47177.21", "64465"]])
    assert rows[0]["period"] == date(2009, 12, 31)
    assert rows[0]["freq"] == "yearly"
    assert rows[0]["values"]["sow_inventory"] == 4910.0
    assert rows[0]["values"]["pork_output"] == 4932.85
    assert rows[0]["values"]["hog_inventory"] == 47177.21
    assert rows[0]["values"]["hog_slaughter_quarterly"] == 64465.0


def test_quarter_row_parses_to_quarter_end():
    rows = parse_capacity_rows([["2025年二季度（末）", "4043", "1418", "42447", "17143"]])
    assert rows[0]["period"] == date(2025, 6, 30)
    assert rows[0]["freq"] == "quarterly"


def test_monthly_row_keeps_only_sow():
    # 月度行产量/存栏/出栏为哨兵 0：只保留能繁列
    rows = parse_capacity_rows([["2025年7月", "4042", "0", "0", "0"]])
    assert rows[0]["period"] == date(2025, 7, 31)
    assert rows[0]["freq"] == "monthly"
    assert rows[0]["values"] == {"sow_inventory": 4042.0}


def test_yearly_zero_value_column_skipped():
    rows = parse_capacity_rows([["2019", "3080", "0", "31041", "54419"]])
    assert "pork_output" not in rows[0]["values"]


def test_unparseable_period_skipped_not_raised():
    rows = parse_capacity_rows([["2025年十三月", "1", "2", "3", "4"]])
    assert rows == []


def test_all_zero_row_dropped():
    # 能繁也为 0 的行整行丢弃
    rows = parse_capacity_rows([["2025年8月", "0", "0", "0", "0"]])
    assert rows == []


def test_fixture_snapshot_shape():
    """2026-09-29 实机快照：22 行、列结构、覆盖范围——上游改版时此测试先红。"""
    payload = json.loads(FIXTURE.read_text())
    rows = parse_capacity_rows(payload["data"])
    sow_periods = [r["period"] for r in rows if "sow_inventory" in r["values"]]
    assert len(sow_periods) == 22  # 16 年度 + 3 季度 + 3 月度
    assert sow_periods[0] == date(2009, 12, 31)
    assert sow_periods[-1] == date(2025, 10, 31)
    # ASF 崩塌锚点：2018→2019 能繁下滑（数值变化即上游修订，测试红 = 人工复核信号）
    by_period = {r["period"]: r["values"].get("sow_inventory") for r in rows}
    assert by_period[date(2018, 12, 31)] == 3189.0
    assert by_period[date(2019, 12, 31)] == 3080.0
