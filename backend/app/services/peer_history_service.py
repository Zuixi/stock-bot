"""同类历史（peer history）：**创建那一刻**就把"像不像以前"还给用户（spec §S9.6）。

为什么需要这一节：S9.1–S9.5 只在**到期**时给回报——写完那一刻用户什么都没得到，
`plans/2026-10-18-decision-journal.md` 把这称为"留存杀手"。本节让创建响应立刻返回两段
**事实**（不是预测）：

1. **L1 你自己的历史**（:func:`self_history`）：在该用户**已复盘**的日志里，同标的 / 同桶
   各有几条、几对几错、错的都是哪类归因；
2. **L2 全市场同桶**（:func:`market_history`）：离线产物 ``peer_history_stats.json`` 里
   同桶的「股票 × 交易日」样本数与 5/20 日**超额中位**。

三件必须守住的事：

- **PIT**（§S9.6.3）：L1 只能取 ``created_at < 当前条目 created_at`` 且已复盘的条目
  （把未来塞进候选集 = 自己泄露自己，``tests/test_journal_s96.py`` 有红/绿对照）；
  L2 的产物 ``as_of`` 若 ``>= 快照日`` → **拒绝使用**（``stale_guard``）；
- **两层分开**（§S9.6.2）：L1 是自我参照、L2 是背景，**不得**合并成一个"胜率"
  ——合并就是把"我自己"和"市场"混成一句不可解释的话；
- **L2 不含任何用户数据**：产物只由行情/行业/情绪表算出（spec §S9.7.9 的隐私底线：
  不做跨用户聚合）。L1 只查**本人**的条目（查询里就带 ``user_id``）。

**分桶**（§S9.6.1，45 个组合 + ``unknown`` 单列、不合并）：情绪分位 ``<20 / 20–40 /
40–60 / 60–80 / ≥80``；板块当日涨跌 ``≤−2% / (−2%,2%) / ≥2%``；距 MA20
``<−5% / [−5%,5%] / >5%``。边界值改动必须同时改 spec、``SIMILARITY_BUCKETS`` 与
``PEER_HISTORY_VERSION``（三个一起变，历史快照才解释得清）。

**失败不阻断创建**（§S9.6.4）：任何异常 → ``peer_history`` 写成带 ``notes`` 的降级对象
（``pipeline_error``），``POST /journal`` 仍然 201；缺产物 → ``notes=["no_stats_product"]``。
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal import DecisionJournalEntry, DecisionJournalReview

__all__ = [
    "MA20_BOUNDS",
    "MA20_LABELS",
    "MA_DAYS",
    "MIN_MARKET_SAMPLE",
    "MIN_SELF_SAMPLE",
    "NOTE_NO_PRODUCT",
    "NOTE_PAST_ONLY",
    "NOTE_PIPELINE",
    "NOTE_STALE",
    "PEER_HISTORY_VERSION",
    "SECTOR_BOUNDS",
    "SECTOR_LABELS",
    "SENTIMENT_BOUNDS",
    "SENTIMENT_LABELS",
    "SIMILARITY_BUCKETS",
    "STATS_PATH",
    "UNKNOWN_BUCKET",
    "WRONG_VERDICTS",
    "bucket_key",
    "bucket_of",
    "failure",
    "market_history",
    "peer_history",
    "self_history",
]

_SH = ZoneInfo("Asia/Shanghai")

#: 产物 schema 版本。**改分桶边界/口径必须同时 bump**（历史 fast 快照里落的就是这个版本的
#: 结论，版本号是"这行当时按哪套桶算的"的唯一判据）。
PEER_HISTORY_VERSION = "ph-v1"

#: 分桶常量（spec §S9.6.1 逐字；边界含哪一侧是契约的一部分，见各 ``*_label`` 的用例）
SENTIMENT_BOUNDS: tuple[float, ...] = (20.0, 40.0, 60.0, 80.0)
SENTIMENT_LABELS: tuple[str, ...] = ("lt_20", "20_40", "40_60", "60_80", "ge_80")
SECTOR_BOUNDS: tuple[float, float] = (-2.0, 2.0)
SECTOR_LABELS: tuple[str, str, str] = ("below_-2", "mid", "above_2")
MA20_BOUNDS: tuple[float, float] = (-5.0, 5.0)
MA20_LABELS: tuple[str, str, str] = ("neg_5", "mid", "pos_5")
#: ``unknown`` 单列：缺数**不与其他值合并**（spec §S9.6.1），也不当成 0
UNKNOWN_BUCKET = "unknown"
#: 与 ``journal_service`` 的 MA20 口径同一个天数（不为基准另算一套均线）
MA_DAYS = 20

#: L1 最少样本（spec §S9.6.2：< 5 只显示"样本不足"，不给比例）
MIN_SELF_SAMPLE = 5
#: L2 最少样本（spec §S9.6.2：< 30 不给数字）
MIN_MARKET_SAMPLE = 30

#: 离线产物（``build_peer_history_stats.py`` 生成，随仓库入库）
STATS_PATH = Path(__file__).with_name("peer_history_stats.json")

#: 固定文案（后端下发，前端不硬编码）
NOTE_PAST_ONLY = "该统计只描述过去，不预测结果"

#: ``notes`` 里的机器可读原因（spec §S9.6.4 逐字）
NOTE_NO_PRODUCT = "no_stats_product"
NOTE_PIPELINE = "pipeline_error"
NOTE_STALE = "stale_guard"

#: 归因 → "错"。``right_for_wrong_reason`` / ``luck`` **单独计数**（不是"对"，也不是"错"）：
#: spec §S9.6.2 明说"赚了但理由错"与"不可归因"都必须与对错分开。
WRONG_VERDICTS: tuple[str, ...] = ("thesis_wrong", "timing_wrong", "discipline_break")

#: 三个维度的桶定义（供测试与文档逐字对拍；顺序即 45 个组合的枚举顺序）
SIMILARITY_BUCKETS: dict[str, tuple[str, ...]] = {
    "sentiment": (*SENTIMENT_LABELS, UNKNOWN_BUCKET),
    "sector": (*SECTOR_LABELS, UNKNOWN_BUCKET),
    "ma20": (*MA20_LABELS, UNKNOWN_BUCKET),
}


# ── 分桶（纯函数） ─────────────────────────────────────────────────────────


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sentiment_label(value: Any) -> str:
    """情绪分位 → 桶（spec §S9.6.1：``<20`` / ``20–40`` / ``40–60`` / ``60–80`` / ``≥80``）。

    边界归属（逐字）：19.9 → ``lt_20``；20 → ``20_40``；40 → ``40_60``；60 → ``60_80``；
    80 / 80.1 → ``ge_80``；缺数 → ``unknown``。
    """
    pct = _as_float(value)
    if pct is None:
        return UNKNOWN_BUCKET
    for bound, label in zip(SENTIMENT_BOUNDS, SENTIMENT_LABELS, strict=False):
        if pct < bound:
            return label
    return SENTIMENT_LABELS[-1]


def sector_label(value: Any) -> str:
    """板块当日涨跌 → 桶（``≤−2%`` / ``(−2%, 2%)`` / ``≥2%``；缺数 → ``unknown``）。"""
    pct = _as_float(value)
    if pct is None:
        return UNKNOWN_BUCKET
    low, high = SECTOR_BOUNDS
    if pct <= low:
        return SECTOR_LABELS[0]
    if pct < high:
        return SECTOR_LABELS[1]
    return SECTOR_LABELS[2]


def ma20_label(value: Any) -> str:
    """距 MA20 → 桶（``<−5%`` / ``[−5%, 5%]`` / ``>5%``；缺数 → ``unknown``）。"""
    pct = _as_float(value)
    if pct is None:
        return UNKNOWN_BUCKET
    low, high = MA20_BOUNDS
    if pct < low:
        return MA20_LABELS[0]
    if pct <= high:
        return MA20_LABELS[1]
    return MA20_LABELS[2]


def bucket_of(snapshot: dict[str, Any] | None) -> dict[str, str]:
    """把一份**创建时快照**落到三个维度上（只读快照里当时就有的字段）。

    为什么不从行情库现算：§S9.6.1 要求"只用当时就存在的信息"，拿今天的数据去分桶等于
    事后诸葛亮；而快照正是"当时"的唯一证据。
    """
    snap = snapshot or {}
    sector = snap.get("sector_flow_state")
    sector_pct = sector.get("pct") if isinstance(sector, dict) else None
    return {
        "sentiment": sentiment_label(snap.get("sentiment_percentile")),
        "sector": sector_label(sector_pct),
        "ma20": ma20_label(snap.get("ma20_dist_pct")),
    }


def bucket_key(bucket: dict[str, str]) -> str:
    """三个维度拼成一个稳定键（产物与快照都用它，顺序固定：情绪|板块|MA20）。"""
    return "|".join(
        (
            str(bucket.get("sentiment") or UNKNOWN_BUCKET),
            str(bucket.get("sector") or UNKNOWN_BUCKET),
            str(bucket.get("ma20") or UNKNOWN_BUCKET),
        )
    )


# ── L1：你自己的历史（只用本人 + 只用过去） ────────────────────────────────


def _empty_self_group() -> dict[str, Any]:
    return {
        "n": 0,
        "right": 0,
        "wrong": 0,
        "wrong_by": {},
        "right_for_wrong_reason": 0,
        "luck": 0,
        "insufficient": True,
    }


def _accumulate(group: dict[str, Any], verdict: str) -> None:
    group["n"] += 1
    if verdict in WRONG_VERDICTS:
        group["wrong"] += 1
        wrong_by: dict[str, int] = group["wrong_by"]
        wrong_by[verdict] = wrong_by.get(verdict, 0) + 1
    elif verdict == "right_for_wrong_reason":
        group["right_for_wrong_reason"] += 1
    elif verdict == "luck":
        group["luck"] += 1
    # "对"= 已复盘里**没有**被判成错的（口径写在 spec §S9.6.2：错由三类归因定义）


def _finish(group: dict[str, Any]) -> dict[str, Any]:
    group["right"] = group["n"] - group["wrong"]
    group["insufficient"] = group["n"] < MIN_SELF_SAMPLE
    return group


async def self_history(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    bucket: dict[str, str],
    symbol: str | None,
    before: datetime,
) -> dict[str, Any]:
    """L1：**本人**在 ``before`` 之前复盘过的条目，按「同标的 / 同桶」两层分别统计。

    PIT 的两道闸：``created_at < before``（不含未来）且只算**已复盘**（``join reviews``）。
    一次查询取回候选集（用户自己的日志量有限），分桶在内存里做——不逐条查库。
    """
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    rows = (
        await db.execute(
            select(
                DecisionJournalEntry.symbol,
                DecisionJournalEntry.snapshot,
                DecisionJournalReview.verdict,
            )
            .join(
                DecisionJournalReview,
                DecisionJournalReview.entry_id == DecisionJournalEntry.id,
            )
            .where(
                DecisionJournalEntry.user_id == uid,
                DecisionJournalEntry.created_at < before,
            )
        )
    ).all()
    same_symbol = _empty_self_group()
    same_bucket = _empty_self_group()
    for entry_symbol, snapshot, verdict in rows:
        name = str(verdict)
        if symbol is not None and entry_symbol == symbol:
            _accumulate(same_symbol, name)
        if bucket_key(bucket_of(snapshot)) == bucket_key(bucket):
            _accumulate(same_bucket, name)
    return {
        "same_symbol": _finish(same_symbol),
        "same_bucket": _finish(same_bucket),
        "min_sample": MIN_SELF_SAMPLE,
    }


# ── L2：全市场同桶（离线产物，**不含任何用户数据**） ────────────────────────


@lru_cache(maxsize=4)
def _load_document(path: str, mtime_ns: int) -> dict[str, Any]:
    """读产物并按 ``(path, mtime)`` 缓存（换了产物自动失效，不用重启服务）。"""
    del mtime_ns  # 只用于缓存键
    loaded: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return loaded


def load_stats(path: Path | None = None) -> dict[str, Any] | None:
    """读离线产物；文件不存在/损坏 → ``None``（调用方按 ``no_stats_product`` 处理）。"""
    target = path or STATS_PATH
    try:
        stat = target.stat()
        return _load_document(str(target), stat.st_mtime_ns)
    except (OSError, ValueError):
        return None


def market_history(
    bucket: dict[str, str],
    snapshot_day: date,
    *,
    path: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """L2：同桶的「股票 × 交易日」样本数与 5/20 日超额中位。

    返回 ``(block, note)``：``note`` 非空 = 这段**没有可用数字**（产物缺失
    ``no_stats_product`` / 产物比快照还新 ``stale_guard``，spec §S9.6.3）。
    样本 < 30 时中位字段一律 ``None``（"样本不足"由 ``insufficient`` 表达）。
    """
    doc = load_stats(path)
    if doc is None:
        return None, NOTE_NO_PRODUCT
    raw_as_of = doc.get("as_of")
    if not raw_as_of:
        return None, NOTE_NO_PRODUCT
    try:
        as_of = date.fromisoformat(str(raw_as_of))
    except ValueError:
        return None, NOTE_NO_PRODUCT
    if as_of >= snapshot_day:
        # PIT：产物窗口必须**在快照日之前**结束，否则它含有"未来"的行情
        return None, NOTE_STALE
    entry = (doc.get("buckets") or {}).get(bucket_key(bucket)) or {}
    n = int(entry.get("n") or 0)
    sufficient = n >= int(doc.get("min_sample") or MIN_MARKET_SAMPLE)
    return (
        {
            "as_of": as_of.isoformat(),
            "n": n,
            "t20_n": int(entry.get("t20_n") or 0),
            "t5_excess_med": (entry.get("t5_excess_med") if sufficient else None),
            "t20_excess_med": (entry.get("t20_excess_med") if sufficient else None),
            "min_sample": int(doc.get("min_sample") or MIN_MARKET_SAMPLE),
            "insufficient": not sufficient,
        },
        None,
    )


# ── 装配：写进快照、写进响应 ────────────────────────────────────────────────


def failure(reason: str = NOTE_PIPELINE) -> dict[str, Any]:
    """降级对象：**永不阻断创建**，``notes`` 写明原因（spec §S9.6.4）。"""
    return {
        "version": PEER_HISTORY_VERSION,
        "computed_at": datetime.now(_SH).isoformat(),
        "bucket": None,
        "self": None,
        "market": None,
        "notes": [reason],
    }


async def peer_history(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    snapshot: dict[str, Any],
    symbol: str | None,
    computed_at: datetime,
    path: Path | None = None,
) -> dict[str, Any]:
    """装配 ``peer_history``（写进 ``snapshot.peer_history`` 与创建响应）。

    ``notes`` 恒定带两句：``NOTE_PAST_ONLY``（"只描述过去"）+ 任何降级原因。
    时间口径：``computed_at`` 用来做 L1 的 PIT 上界（= 本条目的 ``created_at``）。
    """
    bucket = bucket_of(snapshot)
    snapshot_day = computed_at.date()
    if str(snapshot.get("as_of") or ""):
        try:
            snapshot_day = date.fromisoformat(str(snapshot["as_of"]))
        except ValueError:
            pass
    self_block = await self_history(db, user_id, bucket=bucket, symbol=symbol, before=computed_at)
    market_block, market_note = market_history(bucket, snapshot_day, path=path)
    notes = [NOTE_PAST_ONLY]
    if market_note:
        notes.append(market_note)
    return {
        "version": PEER_HISTORY_VERSION,
        "computed_at": computed_at.isoformat(),
        "bucket": bucket,
        "self": self_block,
        "market": market_block,
        "notes": notes,
    }
