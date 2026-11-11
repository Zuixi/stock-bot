"""P7 血缘注册表 + 一致性检查的契约（纯函数，不连库）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services import consistency_service, lineage_registry


def test_six_metrics_with_unique_keys() -> None:
    assert len(lineage_registry.METRICS) == 6
    for key, item in lineage_registry.METRICS.items():
        assert item.key == key
        assert item.label and item.unit and item.caliber_version
        assert ":day" in item.value_sql
        assert item.sources, f"{key} 必须至少声明一个来源表"
        assert item.consumers, f"{key} 必须声明至少一个消费端点"
        assert all(path.startswith("/api/v1/") for path in item.consumers)


def test_scope_metric_requires_scope_key() -> None:
    item = lineage_registry.get("sw_l1_pct_chg")
    assert item is not None
    assert item.scope_required
    assert ":scope_key" in item.value_sql
    assert lineage_registry.get("not-exist") is None


async def test_consistency_detects_mismatch_and_missing() -> None:
    class _Result:
        def __init__(self, value: Any) -> None:
            self._value = value

        def scalar_one_or_none(self) -> Any:
            return self._value

    class _Db:
        def __init__(self, values: list[Any]) -> None:
            self._values = list(values)

        async def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
            return _Result(self._values.pop(0))

    out = await consistency_service.check(
        _Db([100, 100, 90, 91, 50, 50, 20, 20, 0, 0, 1_000_000, 1_000_500]), day=date(2026, 11, 10)
    )
    by_key = {item["key"]: item for item in out}
    assert by_key["up_count"]["status"] == "ok"
    assert by_key["down_count"]["status"] == "mismatch"
    # 相对容差 0.1%：100 万 vs 100.05 万在容差内
    assert by_key["total_amount"]["status"] == "ok"

    out = await consistency_service.check(
        _Db([None, 100, 90, None, 50, None, 20, None, None, 0, 1.0, 1.0]), day=date(2026, 11, 10)
    )
    assert {item["key"]: item["status"] for item in out}["up_count"] == "missing"
    assert {item["key"]: item["status"] for item in out}["down_count"] == "missing"
