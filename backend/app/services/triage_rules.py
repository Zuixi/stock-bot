"""规则来源（provenance）的装配层：registry + 静态统计产物 → ``/watchlists/triage/rules``。

**为什么统计放静态 JSON 而不是读时算**：把 12 个月 × 全市场按 8 条规则回放一遍是分钟级
扫描（见 ``scripts/build_triage_rule_stats.py``），HTTP 端点不能这么干。产物随仓库入库，
于是"规则怎么定的"与"历史上它长什么样"都可评审、可 diff、可复跑。

**为什么不在这里造第二条词表**：``rule_text`` / ``definition`` / ``source_tables`` /
``failure_modes`` 全部从 ``triage_detectors.TRIAGE_DETECTORS`` 取，端点只做投影。
两处各写一份必然漂移，最后没人知道卡片上的阈值和统计脚本里的阈值是不是同一个。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, cast

from app.schemas.triage import TriageRuleOut, TriageSeverity, TriageStatsOut
from app.services import triage_detectors as td

#: 统计产物路径（``scripts/build_triage_rule_stats.py`` 的默认输出）
STATS_PATH = Path(__file__).with_name("triage_rule_stats.json")

#: 产物不存在时每条规则的 ``stats_note``：必须显式说明"没算"，不许静默给空统计
MISSING_NOTE = "统计产物尚未生成（运行 backend/scripts/build_triage_rule_stats.py）"

#: 产物存在但不可解析：把链路断点说出来，而不是假装没有统计
BROKEN_NOTE = "统计产物无法解析（重新运行 backend/scripts/build_triage_rule_stats.py）"

#: 产物存在但缺这条规则（例如新增探测器后忘了复跑脚本）
NO_ENTRY_NOTE = "该规则尚无统计产物（新增规则后需复跑 build_triage_rule_stats.py）"


def _read(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """读产物；返回 ``(文档, 兜底说明)`` —— 不存在/损坏都不抛，端点照常 200。"""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, MISSING_NOTE
    except (OSError, json.JSONDecodeError):
        return None, BROKEN_NOTE
    if not isinstance(loaded, dict):
        return None, BROKEN_NOTE
    return loaded, None


def _meta(doc: dict[str, Any]) -> tuple[date, int, str] | None:
    """产物级公共字段（``as_of`` / ``window_months`` / ``basis``）；缺失视为损坏。"""
    try:
        return (
            date.fromisoformat(str(doc["as_of"])),
            int(doc["window_months"]),
            str(doc["basis"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _stats_out(meta: tuple[date, int, str], entry: dict[str, Any]) -> TriageStatsOut | None:
    """把一条规则的统计块投影成响应模型；缺字段/类型不对一律返回 ``None``。

    宁可把一条规则降级成 ``stats=null + stats_note``，也不下发一个字段残缺、
    前端会画成 0 的统计（0 信号与"没算"是两件事）。
    """
    stats = entry.get("stats")
    if not isinstance(stats, dict):
        return None
    try:
        return TriageStatsOut(
            as_of=meta[0],
            window_months=meta[1],
            basis=meta[2],
            signals=int(stats["signals"]),
            days=int(stats["days"]),
            per_day=float(stats["per_day"]),
            t1_excess_med=_opt_float(stats.get("t1_excess_med")),
            t5_excess_med=_opt_float(stats.get("t5_excess_med")),
            t20_excess_med=_opt_float(stats.get("t20_excess_med")),
            t5_p05=_opt_float(stats.get("t5_p05")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def list_rules(path: Path | None = None) -> list[TriageRuleOut]:
    """8 条规则的来源 + 统计（registry 顺序；登录即可读，不查库）。"""
    doc, fallback_note = _read(STATS_PATH if path is None else path)
    meta = _meta(doc) if doc is not None else None
    if doc is not None and meta is None:
        # 产物可解析但缺公共字段：降级成"损坏"，别让 8 条规则各崩一次
        doc, fallback_note, meta = None, BROKEN_NOTE, None
    raw_entries = doc.get("rules") if doc is not None else None
    entries: dict[str, Any] = raw_entries if isinstance(raw_entries, dict) else {}

    rules: list[TriageRuleOut] = []
    for spec in td.TRIAGE_DETECTORS:
        entry = entries.get(spec.key)
        entry = entry if isinstance(entry, dict) else {}
        stats = _stats_out(meta, entry) if meta is not None else None
        raw_note = entry.get("stats_note")
        note = raw_note if isinstance(raw_note, str) and raw_note.strip() else None
        if stats is None and note is None:
            note = fallback_note or NO_ENTRY_NOTE
        rules.append(
            TriageRuleOut(
                key=spec.key,
                label=spec.label,
                severity=cast(TriageSeverity, spec.severity),
                version=spec.version,
                rule_text=spec.rule_text,
                definition=spec.definition,
                thresholds=dict(spec.thresholds),
                source_tables=list(spec.source_tables),
                update_note=spec.update_note,
                failure_modes=list(spec.failure_modes),
                stats=stats,
                stats_note=note,
            )
        )
    return rules
