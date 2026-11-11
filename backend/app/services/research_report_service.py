"""「有效研报」判定规则（S3.7）—— 纯函数，不碰库、不碰网络。

契约见 ``docs/specs/S3-content.md`` §S3.7：三条判据（去重 / 行业相关 / 有数据支撑）
必须显式、可解释、可配置，人工终态可压倒自动判定。

**为什么是纯函数**：同一份判定要服务三个出口——API 列表（逐条给"为什么无效"）、
人工裁定队列（人工终态优先）、以及单测（逐条判据）。把 DB 访问混进来，判据就
再也无法被独立钉住，所以本模块只吃调用方已经装配好的 ``extracted`` / ``metrics`` /
``entities``，**任何一处 IO 都不做**。

判据顺序固定（``reasons`` 有序且去重），调用方可以把 ``reasons`` 直接翻成给人看的
"命中说明"，不需要再猜顺序：

1. 人工终态：``confirmed`` → 有效；``rejected`` → 无效（``human_rejected``），
   **立即返回**（人工终态压倒一切自动判据，不再继续判）；
2. 去重：``extracted["superseded_by"]`` 有值 → 无效（``superseded``；
   该字段由 ingest 写入，见 ``app/services/content_ingest``）；
3. 有数据支撑：至少一个量化字段带值 —— ``REPORT_RULES["require_quantitative"]``
   为假时**整条跳过**（配置驱动，改判据不改代码）；
4. 行业相关：``content_entities`` 关联了行业，或标题命中 ``extracted["industry"]``
   —— ``REPORT_RULES["require_industry"]`` 为假时跳过。

``REPORT_RULES`` 的每个键都被**读取**（不是摆设）：``require_quantitative`` /
``require_industry`` 分别开关判据 3/4，``dedup_by_rating_change`` 开关判据 2 ——
去重关闭时 ingest 本就不会写 ``superseded_by``，判定侧同样不据此排除，
让"去重是否生效"这一点只有一个来源。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

#: 判据开关（S3.7 要求配置化，改判定不改代码）。
#: 取值由调用方在启动时按 registry 覆盖，本模块只读不写。
REPORT_RULES: dict[str, bool] = {
    "require_quantitative": True,
    "require_industry": True,
    "dedup_by_rating_change": True,
}

#: 视为"有数据支撑"的量化字段（目标价 / 盈利预测 / 评级及其变动）。
QUANTITATIVE_METRIC_KEYS: frozenset[str] = frozenset(
    {
        "rating",
        "rating_change",
        "target_price",
        "eps_forecast",
        "pe_forecast",
    }
)

#: 人工终态：命中的两个取值分别直接放行 / 直接排除。
HUMAN_CONFIRMED: str = "confirmed"
HUMAN_REJECTED: str = "rejected"

#: ``reasons`` 里的机器可读原因码（前端/日志按它做映射，勿随意改名）。
REASON_HUMAN_REJECTED: str = "human_rejected"
REASON_SUPERSEDED: str = "superseded"
REASON_NO_QUANTITATIVE: str = "no_quantitative_field"
REASON_INDUSTRY_MISMATCH: str = "industry_mismatch"


@dataclass(frozen=True)
class ReportEvaluation:
    """一条研报的判定结果：是否有效 + 命中的原因码（有序、去重）。"""

    effective: bool
    reasons: list[str] = field(default_factory=list)


def _text(value: Any) -> str:
    """把可空字段规约成去空白的字符串（``None`` → ``""``）。"""
    if value is None:
        return ""
    return str(value).strip()


def _has_quantitative_field(metrics: Sequence[Any]) -> bool:
    """是否含至少一个带值（数值或文本）的量化指标。"""
    for metric in metrics:
        if getattr(metric, "metric_key", None) not in QUANTITATIVE_METRIC_KEYS:
            continue
        if getattr(metric, "value", None) is not None:
            return True
        if _text(getattr(metric, "value_text", None)):
            return True
    return False


def _is_industry_related(entities: Sequence[Any], title: str, industry: str) -> bool:
    """行业相关：已关联行业实体，或标题命中 ``extracted["industry"]``。"""
    for entity in entities:
        if getattr(entity, "entity_type", None) == "industry" and _text(
            getattr(entity, "entity_id", None)
        ):
            return True
    return bool(industry) and industry in title


def evaluate_report(
    *,
    review_status: str,
    extracted: dict[str, Any],
    title: str,
    metrics: Sequence[Any],
    entities: Sequence[Any],
) -> ReportEvaluation:
    """判定一条研报是否「有效」，并给出全部命中原因（有序去重）。

    ``review_status`` 里只有 ``confirmed`` / ``rejected`` 是人工终态；
    ``auto`` 与 ``pending`` 都还没被人碰过，走同一套自动判据。
    """
    reasons: list[str] = []

    def fail(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    status = _text(review_status)
    # 判据 1：人工终态压倒自动判据（S3.7「定义可以被人工推翻」）。
    if status == HUMAN_CONFIRMED:
        return ReportEvaluation(effective=True, reasons=[])
    if status == HUMAN_REJECTED:
        return ReportEvaluation(effective=False, reasons=[REASON_HUMAN_REJECTED])

    safe_extracted = extracted or {}

    # 判据 2：去重——被判重复的旧研报不再算有效（不删除，只标记）。
    if REPORT_RULES.get("dedup_by_rating_change", True) and safe_extracted.get("superseded_by"):
        fail(REASON_SUPERSEDED)

    # 判据 3：有数据支撑（可关闭）。
    if REPORT_RULES.get("require_quantitative", True) and not _has_quantitative_field(metrics):
        fail(REASON_NO_QUANTITATIVE)

    # 判据 4：行业相关（可关闭）。
    industry = _text(safe_extracted.get("industry"))
    if REPORT_RULES.get("require_industry", True) and not _is_industry_related(
        entities, title or "", industry
    ):
        fail(REASON_INDUSTRY_MISMATCH)

    return ReportEvaluation(effective=not reasons, reasons=reasons)
