"""S3.7「有效研报」判定规则 —— 逐条判据的纯函数测试（不连库、不 mock 服务）。

用 ``SimpleNamespace`` 当 ``content_metrics`` / ``content_entities`` 行的替身：
本模块只按属性名读字段（``metric_key`` / ``value`` / ``value_text`` /
``entity_type`` / ``entity_id``），所以替身不必是真实 ORM 对象 ——
这样判据测试也不会因为模型改动而无声漂移。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services import research_report_service as rrs


def _metric(key: str, value: Any = None, value_text: str | None = None) -> Any:
    return SimpleNamespace(metric_key=key, value=value, value_text=value_text)


def _entity(entity_type: str, entity_id: Any) -> Any:
    return SimpleNamespace(entity_type=entity_type, entity_id=entity_id)


INDUSTRY_ENTITY = [_entity("industry", "生猪养殖")]
TARGET_PRICE = [_metric("target_price", value=38.5)]
GOOD_EXTRACTED = {"industry": "生猪养殖"}


def _evaluate(
    *,
    review_status: str = "auto",
    extracted: dict[str, Any] | None = None,
    title: str = "某券商研报",
    metrics: list[Any] | None = None,
    entities: list[Any] | None = None,
) -> rrs.ReportEvaluation:
    return rrs.evaluate_report(
        review_status=review_status,
        extracted=GOOD_EXTRACTED if extracted is None else extracted,
        title=title,
        metrics=[TARGET_PRICE[0]] if metrics is None else metrics,
        entities=INDUSTRY_ENTITY if entities is None else entities,
    )


# ---------------------------------------------------------------------------
# 判据 1：人工终态压倒一切
# ---------------------------------------------------------------------------


def test_confirmed_is_effective_even_without_quant_or_industry() -> None:
    """人工确认 → 有效，且不再叠加任何自动判据原因。"""
    result = _evaluate(review_status="confirmed", extracted={}, metrics=[], entities=[])

    assert result.effective is True
    assert result.reasons == []


def test_rejected_is_human_rejected() -> None:
    """人工驳回 → 无效，原因码 ``human_rejected``（不是被自动判据排除）。"""
    result = _evaluate(review_status="rejected")

    assert result.effective is False
    assert result.reasons == ["human_rejected"]


def test_pending_is_not_human_terminal() -> None:
    """``pending`` 还没被人碰过，与 ``auto`` 走同一套自动判据。"""
    result = _evaluate(review_status="pending", extracted={}, metrics=[], entities=[])

    assert result.effective is False
    assert result.reasons == ["no_quantitative_field", "industry_mismatch"]


# ---------------------------------------------------------------------------
# 判据 2 / 3：去重 + 有数据支撑
# ---------------------------------------------------------------------------


def test_superseded_report_is_ineffective() -> None:
    """``superseded_by`` 有值（ingest 判为重复）→ 无效，原因 ``superseded``。"""
    result = _evaluate(extracted={"industry": "生猪养殖", "superseded_by": 42})

    assert result.effective is False
    assert result.reasons == ["superseded"]


def test_no_quantitative_field() -> None:
    """没有任何带值的量化字段 → 无效（只有非量化 key 也不算）。"""
    result = _evaluate(metrics=[_metric("core_view", value_text="景气度上行")])

    assert result.effective is False
    assert result.reasons == ["no_quantitative_field"]


def test_quantitative_field_from_value_text_only() -> None:
    """``rating`` 只有文本值也算有数据支撑。"""
    result = _evaluate(metrics=[_metric("rating", value_text="买入")])

    assert result.effective is True
    assert result.reasons == []


def test_quantitative_field_without_value_is_not_enough() -> None:
    """字段在、但 value 与 value_text 都空 → 仍然没有数据支撑。"""
    result = _evaluate(metrics=[_metric("target_price")])

    assert result.effective is False
    assert result.reasons == ["no_quantitative_field"]


# ---------------------------------------------------------------------------
# 判据 4：行业相关
# ---------------------------------------------------------------------------


def test_industry_entity_satisfies_industry_rule() -> None:
    """已关联行业实体 → 通过行业判据（标题可以不命中关键词）。"""
    result = _evaluate(title="关于产能去化的思考", entities=INDUSTRY_ENTITY)

    assert result.effective is True
    assert result.reasons == []


def test_industry_entity_with_empty_id_does_not_count() -> None:
    """``entity_id`` 为空的行业实体不算关联。"""
    result = _evaluate(extracted={}, entities=[_entity("industry", "  ")])

    assert result.effective is False
    assert result.reasons == ["industry_mismatch"]


def test_title_containing_industry_satisfies_industry_rule() -> None:
    """标题命中 ``extracted["industry"]`` → 通过行业判据（无行业实体也行）。"""
    result = _evaluate(title="生猪养殖：2026H1 景气拐点确认", entities=[])

    assert result.effective is True
    assert result.reasons == []


def test_no_industry_and_no_industry_entity() -> None:
    """既没行业实体、标题也不命中 → 无效（``industry_mismatch``）。"""
    result = _evaluate(
        extracted={"industry": "生猪养殖"},
        title="某券商周报",
        entities=[_entity("stock", "002714.SZ")],
    )

    assert result.effective is False
    assert result.reasons == ["industry_mismatch"]


def test_missing_industry_key_falls_back_to_entity_check_only() -> None:
    """``extracted`` 没写 industry 时，标题链路跳过，只能靠行业实体过。"""
    without_entity = _evaluate(extracted={}, title="生猪养殖深度", entities=[])
    with_entity = _evaluate(extracted={}, title="生猪养殖深度", entities=INDUSTRY_ENTITY)

    assert without_entity.effective is False
    assert without_entity.reasons == ["industry_mismatch"]
    assert with_entity.effective is True


# ---------------------------------------------------------------------------
# 全绿 / 配置驱动
# ---------------------------------------------------------------------------


def test_all_checks_pass() -> None:
    """三条判据全过 → 有效且无原因。"""
    result = _evaluate()

    assert result.effective is True
    assert result.reasons == []


def test_all_reasons_are_reported_in_order() -> None:
    """多条判据同时不过时，原因**全部**返回且顺序与判据顺序一致。"""
    result = _evaluate(
        extracted={"industry": "生猪养殖", "superseded_by": 7},
        title="某券商周报",
        metrics=[],
        entities=[],
    )

    assert result.effective is False
    assert result.reasons == ["superseded", "no_quantitative_field", "industry_mismatch"]


def test_require_quantitative_disabled_skips_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``REPORT_RULES["require_quantitative"]=False`` → 判据 3 整条跳过（配置驱动）。"""
    monkeypatch.setitem(rrs.REPORT_RULES, "require_quantitative", False)

    result = _evaluate(metrics=[])

    assert result.effective is True
    assert result.reasons == []


def test_require_industry_disabled_skips_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``REPORT_RULES["require_industry"]=False`` → 判据 4 整条跳过。"""
    monkeypatch.setitem(rrs.REPORT_RULES, "require_industry", False)

    result = _evaluate(extracted={}, title="某券商周报", entities=[])

    assert result.effective is True
    assert result.reasons == []
