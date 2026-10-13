"""北向 ``north_money`` 口径判别与读面标注（Ruling 62/63）——纯单测，不触 DB/网络。

为什么值得单独一个文件：这是**用户可见数据正确性**的判据面，跨三层
（``northbound_caliber`` 断点常量 → ``_map_hsgt_rows`` 行级标注 → ``get_northbound_series``
信封），而核心不变量只有一条：**任何路径都不得把断点后的成交额标成"净流入/净买入"**。

背景（实证，勿再讨论）：上游 ``moneyflow_hsgt.north_money`` 在 ``2024-08-19`` 换语义——
断点前 148 日有 83 个负值（净买入）；断点后 502 日零负值零零值（成交额）。见
``docs/design/data-source.md`` 附.9.1 ㉖ / 附.9.2 #1–#5。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.market_data import NorthboundPointOut, NorthboundSeriesOut
from app.services import market_data_service as mds
from app.services.northbound_caliber import (
    NET_BUY,
    NORTHBOUND_AMOUNT_KINDS,
    NORTHBOUND_TURNOVER_SINCE,
    TURNOVER,
    northbound_amount_kind,
)

# ---------------------------------------------------------------------------
# 1. 断点常量本身
# ---------------------------------------------------------------------------


def test_breakpoint_boundaries() -> None:
    """断点日**当天**已是成交额（实证 2024-08-19 起零负值），前一天仍是净买入。"""
    assert northbound_amount_kind(date(2024, 8, 18)) == NET_BUY
    assert northbound_amount_kind(NORTHBOUND_TURNOVER_SINCE) == TURNOVER
    assert northbound_amount_kind(date(2024, 8, 20)) == TURNOVER


def test_caliber_is_a_single_step_with_no_third_value() -> None:
    """逐日扫 4 年：取值只能在两档内，且**恰好一次**跃迁（没有第三类、没有来回跳）。"""
    seen: list[str] = []
    day = date(2023, 1, 1)
    while day <= date(2026, 12, 31):
        kind = northbound_amount_kind(day)
        assert kind in NORTHBOUND_AMOUNT_KINDS, f"{day} 的口径越界: {kind!r}"
        if not seen or seen[-1] != kind:
            seen.append(kind)
        day += timedelta(days=1)
    assert seen == [NET_BUY, TURNOVER], f"应恰好一次跃迁，实际 {seen}"


def test_migration_backfills_the_same_breakpoint() -> None:
    """迁移里的断点是**冻结的历史**（迁移不 import 应用代码），此处把两者钉在一起。

    否则改常量忘改迁移（或反之）会让"库内已回填的口径"与"读面按日期重算的口径"
    在同一行上分歧——正是本任务要消除的那类静默错标。
    """
    versions = Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions"
    matches = sorted(versions.glob("*_add_northbound_amount_kind.py"))
    assert len(matches) == 1, f"应恰有一个加 amount_kind 的迁移，实际 {matches}"
    source = matches[0].read_text(encoding="utf-8")

    literal = re.search(r'_TURNOVER_SINCE = "([0-9]{4}-[0-9]{2}-[0-9]{2})"', source)
    assert literal is not None, "迁移里找不到 _TURNOVER_SINCE 字面量"
    assert literal.group(1) == NORTHBOUND_TURNOVER_SINCE.isoformat()

    # 口径白名单也必须同集：库层 CHECK 与 Python 端不许一个多一个少
    check = re.search(r"_AMOUNT_KIND_CHECK = \"amount_kind IN \(([^)]*)\)\"", source)
    assert check is not None, "迁移里找不到 amount_kind 的 CHECK 表达式"
    assert set(re.findall(r"'([a-z_]+)'", check.group(1))) == set(NORTHBOUND_AMOUNT_KINDS)


# ---------------------------------------------------------------------------
# 2. 读面：窗口口径 + 缺失口径的兜底方向
# ---------------------------------------------------------------------------


def _item(day: str, amount: float, kind: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"date": day, "amount": amount}
    if kind is not None:
        item["amount_kind"] = kind
    return item


def test_window_kind_uniform_mixed_and_empty() -> None:
    """窗口口径：单一口径 → 该值；跨断点混装 → ``mixed``（**禁止求和**）；无行 → ``None``。"""
    turnover_only = mds._northbound_payload(
        date(2026, 9, 7),
        [_item("2026-09-06", 244809.28, TURNOVER), _item("2026-09-07", 273259.26, TURNOVER)],
    )
    assert turnover_only["amount_kind"] == TURNOVER

    net_buy_only = mds._northbound_payload(
        date(2024, 8, 16),
        [_item("2024-08-15", -3209.23, NET_BUY), _item("2024-08-16", 22448.99, NET_BUY)],
    )
    assert net_buy_only["amount_kind"] == NET_BUY

    mixed = mds._northbound_payload(
        date(2024, 8, 19),
        [_item("2024-08-16", -3209.23, NET_BUY), _item("2024-08-19", 77584.24, TURNOVER)],
    )
    assert mixed["amount_kind"] == "mixed", "跨断点必须显式混装，不得静默合并"

    empty = mds._northbound_payload(None, [])
    assert empty["amount_kind"] is None


def test_missing_caliber_is_derived_from_date_never_defaulted_to_net_buy() -> None:
    """缺 ``amount_kind`` 的历史缓存条目按断点补正——**绝不**回落到 net_buy。

    回落方向是这次修复的核心：断点后的值是成交额，若默认标成净买入就是重犯原错。
    """
    post = mds._normalize_northbound_items([_item("2026-09-07", 273259.26)])
    assert post == [{"date": "2026-09-07", "amount": 273259.26, "amount_kind": TURNOVER}]

    pre = mds._normalize_northbound_items([_item("2024-08-16", -3209.23)])
    assert pre == [{"date": "2024-08-16", "amount": -3209.23, "amount_kind": NET_BUY}]

    unknown_kind = mds._normalize_northbound_items([_item("2026-09-07", 1.0, "whatever")])
    assert unknown_kind[0]["amount_kind"] == TURNOVER, "非法取值按日期重算，不猜净买入"


def test_legacy_cache_item_keeps_its_value_under_the_new_field_name() -> None:
    """上线前写进 Redis 的条目值挂在旧名 ``net_amount`` 上：平移到 ``amount`` 且不丢值。"""
    legacy = [{"date": "2026-09-07", "net_amount": 273259.26}]
    assert mds._normalize_northbound_items(legacy) == [
        {"date": "2026-09-07", "amount": 273259.26, "amount_kind": TURNOVER}
    ]


def test_item_without_parseable_date_fails_loudly() -> None:
    """连日期都没有的条目不可能来自本服务写路径：不猜口径，响亮失败。"""
    with pytest.raises(ValueError, match="no parseable date"):
        mds._normalize_northbound_items([{"amount": 1.0}])


async def test_read_path_payload_shape_carries_caliber(monkeypatch: pytest.MonkeyPatch) -> None:
    """读面 payload 的键集**就是** DTO 的键集，且只有 ``amount``/``amount_kind``。

    回归闸门：把 ``net_amount``（净流入口径的字段名）重新塞回 payload 会让本用例红——
    那正是"成交额冒充净买入"的载体。
    """
    monkeypatch.setattr(mds, "_today_sh", lambda: date(2026, 9, 17))

    async def _latest(db: Any, column: Any) -> date:
        return date(2026, 9, 7)

    monkeypatch.setattr(mds, "_latest_snapshot_day", _latest)

    class _Row:
        trade_date = date(2026, 9, 7)
        amount = 273259.26
        amount_kind = TURNOVER

    async def _repo(db: Any, days: int) -> list[Any]:
        return [_Row()]

    monkeypatch.setattr(mds.market_data_repo, "list_northbound", _repo)

    payload = await mds.get_northbound_series(None, 30)

    assert set(payload) == set(NorthboundSeriesOut.model_fields)
    assert payload["amount_kind"] == TURNOVER
    assert payload["items"] == [
        {"date": "2026-09-07", "amount": 273259.26, "amount_kind": TURNOVER}
    ]
    assert "net_amount" not in payload["items"][0]


# ---------------------------------------------------------------------------
# 3. 声明面：口径必填（漏标口径必须 422，不得静默）
# ---------------------------------------------------------------------------


def test_point_schema_requires_caliber() -> None:
    NorthboundPointOut(date="2026-09-07", amount=273259.26, amount_kind=TURNOVER)
    with pytest.raises(ValidationError):
        NorthboundPointOut(date="2026-09-07", amount=273259.26)  # type: ignore[call-arg]


def test_series_schema_accepts_mixed_and_none_but_not_unknown_tokens() -> None:
    ok = NorthboundSeriesOut.model_validate(
        {"as_of": None, "stale_days": None, "source_status": "discontinued", "amount_kind": "mixed"}
    )
    assert ok.amount_kind == "mixed"
    empty = NorthboundSeriesOut.model_validate({"source_status": "discontinued", "items": []})
    assert empty.amount_kind is None
    with pytest.raises(ValidationError):
        NorthboundSeriesOut.model_validate({"amount_kind": "net_inflow"})
