# backend/app/services/industry_ingest_diff.py
"""ingest 行级 diff + 内容哈希 + 源节奏表（纯函数，离线单测锁定）.

三层增量门控的 L2/L3 计算核心：官方统计会修订历史，diff 必须全量比对而非
水位跳过；修订 old→new 留痕本身就是投研信号（统计局回修幅度可分析）。

刻意不 import app.config / settings / DB / httpx：本模块必须能在无环境
依赖下被单测直接拉起（next_due 节奏表因此从 service 迁入此处）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta

_VALUE_TOL = 1e-6

_ROW_KEYS = (
    "industry_key",
    "stock_id",
    "metric_key",
    "source",
    "source_tier",
    "freq",
    "period",
    "value",
    "unit",
    "extra",
)

# 各源发布节奏 → next_due 距今天数；未知源默认日更（宁多查不漏数据）
_SOURCE_CADENCE_DAYS = {
    "xuantian": 30,  # 能繁月度行月更
    "caaa": 32,  # 次月 20-27 日发布，保守 +2 天
    "akshare_soozhu": 1,  # 日价
    "akshare_sina": 1,  # LH 期货交易日
}


def next_due(source: str, now: datetime) -> datetime:
    """各源发布节奏 → 下次应查时间（未知源默认日更，宁多查不漏数据）."""
    return now + timedelta(days=_SOURCE_CADENCE_DAYS.get(source, 1))


def _diff_key(r: dict) -> tuple[str, str, date]:
    return (r["metric_key"], r["freq"], r["period"])


def canonical_content_hash(rows: list[dict]) -> str:
    """行集合 → 16 hex sha256；排序消除顺序敏感，定点化消除 float 表示漂移."""
    canon = sorted(
        (
            {
                "metric_key": r["metric_key"],
                "freq": r["freq"],
                "period": r["period"].isoformat(),
                "value": round(float(r["value"]), 4),
            }
            for r in rows
        ),
        key=lambda x: (x["metric_key"], x["freq"], x["period"]),
    )
    return hashlib.sha256(json.dumps(canon, ensure_ascii=False).encode()).hexdigest()[:16]


def compute_ingest_actions(fetched: list[dict], existing: list[dict]) -> dict:
    """fetched vs existing（同 source）→ new/changed/unchanged/orphaned + 修订留痕.

    existing 为空（首抓）→ 全部 new。orphaned（库有响应无）只计数不删除：
    官方数据语义下不镜像上游删除，异常走人工核查。
    value 比较先 float() 归一（兼容 Numeric(18,4) 回读 Decimal），再 1e-6 容差。
    """
    by_key = {_diff_key(r): r for r in existing}
    fetched_keys = set()
    new: list[dict] = []
    changed: list[dict] = []
    revisions: list[dict] = []
    unchanged = 0
    for r in fetched:
        k = _diff_key(r)
        fetched_keys.add(k)
        cur = by_key.get(k)
        if cur is None:
            new.append(r)
        elif abs(float(r["value"]) - float(cur["value"])) > _VALUE_TOL:
            changed.append(r)
            revisions.append(
                {
                    "metric_key": k[0],
                    "freq": k[1],
                    "period": k[2],
                    "old": float(cur["value"]),
                    "new": float(r["value"]),
                }
            )
        else:
            unchanged += 1
    return {
        "new": new,
        "changed": changed,
        "unchanged_count": unchanged,
        "orphaned_count": len([k for k in by_key if k not in fetched_keys]),
        "revisions": revisions,
    }
