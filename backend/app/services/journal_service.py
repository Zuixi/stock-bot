"""决策日志：**自动快照** + **到期结果** + **归因统计**（spec §S9.3/§S9.4）。

三层职责（本模块是唯一的计算点，路由只做投影）：

1. :func:`build_snapshot` —— 创建判断时抓一次"当时的市场状态"，写进 ``entries.snapshot``
   jsonb，**之后不随行情变化**（重跑不改）。硬约束：**只读** + **≤5 条查询**。
2. :func:`evaluate_entry` —— 到期后用 ``snapshot.price`` 到 ``due_on`` 最近收盘算
   ``outcome_pct / bench_pct / excess_pct / invalidation_hit``。PIT 纪律：起始价
   **只取快照里那一格**，不取复盘日价格（``tests/test_journal_service.py`` 的对照用例
   把快照价换成复盘日价格必须让结果变红）。
3. :func:`review_entry` / :func:`stats` —— 一次复盘（append-only、五类归因）与自洽统计。

**缺数一律 ``null`` + ``missing_reason``，不得用 0 代替**（spec §S9.3）：0 是一个
有意义的数值（"跌了 0%"），用它表示"没查到"会让用户把"数据缺失"读成"市场没动"。
快照里所有 null 字段的原因都收在 ``snapshot["missing_reason"]`` 里（字段名 → 机器可读
原因），前端只在有原因时提示"该项缺数"。

**为什么快照查询恰好 5 条**（反 N+1，且与条目数无关：一次创建一只标的）：

===================================  ==========================================
查询                                  取的字段
===================================  ==========================================
① daily_quotes + price_limits +        ``price`` / ``pct_chg`` / ``ma20_dist_pct`` /
   daily_basic_indicators（一次 join）  ``valuation_pct`` + 探测器行情窗口/涨停价
② industry_metrics（market scope）     ``sentiment_percentile``
③ 申万一级当日涨跌 + 全市场分位        ``sector_flow_state``
④ announcements                       探测器 ``new_announcement``
⑤ dragon_tiger_entries                探测器 ``dragon_tiger``
===================================  ==========================================

第 ③ 条复用 ``triage_service._SECTOR_SQL``（板块涨跌幅/分位的唯一口径）；
``events`` 复用 S8 探测器（``triage_detectors``，纯函数、无 IO），不在本模块里
重写任何判据 —— 否则"体检说跌破 20 日线、日志说没有"会变成两套互相矛盾的真相。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import sqrt
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_basic import DailyBasicIndicator
from app.models.industry_research import IndustryMetric
from app.models.journal import (
    BET_TYPES,
    ENTRY_KINDS,
    VERDICTS,
    DecisionJournalEntry,
    DecisionJournalReview,
)
from app.models.market_data import Announcement, DragonTigerEntry, StockPriceLimit
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.schemas.journal import JournalEntryCreate
from app.services import triage_detectors as td
from app.services.industry_registry import (
    COMPOSITE_METRIC_KEY,
    COMPOSITE_PERCENTILE_MIN_SAMPLES,
    COMPOSITE_PERCENTILE_WINDOW,
)
from app.services.market_sentiment_composite_service import composite_percentile
from app.services.triage_service import (
    _SECTOR_SQL as SECTOR_SQL,
)
from app.services.triage_service import (
    ANNOUNCEMENT_DAYS,
    DRAGON_TIGER_NATURAL_DAYS,
)

__all__ = [
    "BENCH_KIND_HS300",
    "BENCH_KIND_INDUSTRY",
    "BENCH_NOTE_FALLBACK",
    "BENCH_NOTE_INDUSTRY",
    "BENCH_NOTE_MARKET",
    "COST_ROUND_TRIP_BPS",
    "COST_ROUND_TRIP_PCT",
    "JOURNAL_STATS_VERSION",
    "MULTIPLE_COMPARISON_NOTE",
    "SNAPSHOT_FIELDS",
    "SNAPSHOT_META_FIELDS",
    "SNAPSHOT_VERSION",
    "WILSON_Z",
    "AlreadyReviewed",
    "EntryNotFound",
    "EvaluationNotReady",
    "JournalRow",
    "NotDueYet",
    "bench_note",
    "bench_spec",
    "build_snapshot",
    "create_entry",
    "evaluate_entry",
    "evaluate_entries",
    "get_entry",
    "list_entries",
    "review_entry",
    "snapshot_line",
    "stats",
    "status_counts",
    "wilson_interval",
]

_SH = ZoneInfo("Asia/Shanghai")

#: 快照 schema 版本。**新增/改名字段必须同时改 spec §S9.3 与本文件**（快照是落库的
#: jsonb，历史行不会自动补字段，所以版本号是"这行是哪种形状"的唯一判据）。
#:
#: ``s9.r2``（2026-10-19，S9.7 Phase A）：新增 ``bench_spec`` / ``bench_basis_note``
#: 两个元数据键（基准口径在**创建时**固化，spec §S9.7.3）。``s9.r1`` 的行没有这两个键，
#: 读取方按 ``bet_type=entry.bet_type`` + 当场可得的数据重算（不得改写历史快照）。
SNAPSHOT_VERSION = "s9.r2"

#: 快照固定字段（顺序即上屏顺序）。``tests/test_journal_service.py`` 逐字对拍。
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "price",
    "pct_chg",
    "ma20_dist_pct",
    "sentiment_percentile",
    "sector_flow_state",
    "events",
    "valuation_pct",
)

#: 快照里的**元数据键**：不属于 spec §S9.3 的字段表，但同样属于"这行是哪种形状"。
#: 与 ``SNAPSHOT_FIELDS`` 一起被测试逐字锁死（新增键必须同时改 spec 与测试）。
SNAPSHOT_META_FIELDS: tuple[str, ...] = (
    "version",
    "as_of",
    "quote_as_of",
    "symbol",
    "missing_reason",
    # ★ s9.r2（spec §S9.7.3）：基准口径与它的人话说明，创建时固化、事后不可改
    "bench_spec",
    "bench_basis_note",
)

#: 行情窗口的自然日下界：≈130 个交易日 —— 同时覆盖 MA20（20 行）、探测器（61 行）
#: 与 ``pe_ttm`` 近 120 日区间（120 行）。窗口只多不少，多出来的行不参与计算。
QUOTE_WINDOW_NATURAL_DAYS = 200
#: ``ma20_dist_pct`` 的均线天数（spec §S9.3 逐字：收盘距 MA20 的 %）
MA_DAYS = 20
#: ``valuation_pct`` 的区间窗口（spec §S9.3：``pe_ttm`` 近 120 日区间位置）
VALUATION_DAYS = 120
#: 快照里最多几条事件（spec §S9.3：最多 3 条 ``{detector, headline}``）
EVENT_LIMIT = 3
#: 基准指数：市场级判断（无 symbol）与无申万归属的标的都用沪深300
HS300_TS_CODE = "000300.SH"
#: ``stats`` 里占比类字段的最小样本（spec §S9.5：``reviewed_n < 5`` 时占比为 ``null``）
MIN_REVIEWED_FOR_SHARE = 5
#: 单次列表最多返回多少条（**不是**统计口径：``snapshot_summary`` 走独立计数查询，
#: 所以截断不会让"待复盘 N 条"变小；``truncated`` 字段显式告知）。
LIST_LIMIT = 200

#: 三态（spec §S9.1）：``open`` 未到期 / ``due`` 到期待复盘 / ``reviewed`` 已复盘。
#: 状态**不落库**（由 ``due_on`` 与 reviews 行派生），避免"两处状态漂移"。
STATUSES: tuple[str, ...] = ("open", "due", "reviewed")

# ── spec §S9.7.2 净成本（本模块是决策日志口径的唯一常量登记处） ─────────────
#
# 为什么需要一个成本常量：毛收益会让所有统计虚高（outcome/bench/excess 都是"毛"的）。
# 12bp 的构成（**2026-10-19 定的保守估计**，不是精确逐笔费用）：
#
# ==========  ======  ==========================================================
# 项目         bp      假设
# ==========  ======  ==========================================================
# 佣金（双边）  5       万 2.5 × 2 边（含最低 5 元佣金被摊平后的保守值）
# 印花税（卖侧） 5      0.05%（2023-08-28 起减半后的现行税率）
# 过户费+滑点   2       过户费 0.001%×2 边 + 被动成交滑点 ~0.2bp 的取整余量
# ==========  ======  ==========================================================
#
# 不做（spec 明写 v2）：按持有天数分档的成本、税费身份差异、融资成本。
#
# **改动纪律**：改这个常量必须同时改 ①spec §S9.7.2 ②``JOURNAL_STATS_VERSION``
# ③``tests/test_journal_s97.py`` 的钉死用例 —— 三者一起变，历史统计才不会
# 被一个静默的常量改动整体改写口径（``tests/test_journal_s97.py`` 有红/绿对照：
# 改常量 → 净口径逐值断言立刻变）。
COST_ROUND_TRIP_BPS = 12
#: 百分点形式的同一笔成本（= 0.12）。净口径一律由它推出，不在别处写字面量。
COST_ROUND_TRIP_PCT = round(COST_ROUND_TRIP_BPS / 100.0, 4)
#: **统计产物版本**（spec §S9.7.2 的"统计产物版本"）：净口径/基准/区间口径的版本戳。
#: 改 ``COST_ROUND_TRIP_BPS``、``WILSON_Z`` 或基准口径时必须同时改这里（有钉死用例）。
JOURNAL_STATS_VERSION = "s97.a1"

# ── spec §S9.7.3 基准口径 ───────────────────────────────────────────────────
#: 基准种类 ①：**所属申万一级成分股的等权平均收益**。诚实标注：这**不是**申万一级
#: 指数行情（库里没有指数日线），是"成员股等权平均"，差异写进 ``bench_basis_note``。
BENCH_KIND_INDUSTRY = "industry_equal_weight"
#: 基准种类 ②：沪深300（``index_dailies`` 真实指数行情）
BENCH_KIND_HS300 = "hs300"
#: 人话文案（后端下发，前端不硬编码，避免两处措辞漂移）
BENCH_NOTE_INDUSTRY = "基准=所属申万一级成分股等权平均收益（非指数行情）"
BENCH_NOTE_FALLBACK = "基准=沪深300（无申万一级归属，已退化为市场基准）"
BENCH_NOTE_MARKET = "基准=沪深300（市场级判断）"

# ── spec §S9.7.7 区间估计 ───────────────────────────────────────────────────
#: Wilson 95% 区间的 z 值（双侧 0.975 分位）。固定 1.96，不用 scipy（零新依赖）。
WILSON_Z = 1.96
#: 统计页固定标注（spec §S9.7.7 逐字，前端原样展示）：
#: 按 45 个桶（S9.6 的 5×3×3）检索时，总会有桶看起来"显著"——我们**不做**多重比较校正。
MULTIPLE_COMPARISON_NOTE = "未做多重比较校正：按 45 个桶检索时总会有桶看起来显著"


# ── 异常：服务层不抛 HTTPException，语义由路由映射（404/409/422） ────────────


class JournalError(Exception):
    """决策日志的服务层错误基类。"""


class EntryNotFound(JournalError):
    """条目不存在**或不属于当前用户**（两者对外都是 404：不泄露别人有没有这条）。"""


class AlreadyReviewed(JournalError):
    """该条目已经复盘过（``unique(entry_id)`` 的真实含义：不可覆盖历史）。"""


class NotDueYet(JournalError):
    """还没到期就复盘（到期日 = ``created_at::date + horizon_days``）。"""


class EvaluationNotReady(JournalError):
    """自动结果算不出来（缺行情/缺基准）：**不得**用 0 兜底写入。"""


# ── 小工具 ──────────────────────────────────────────────────────────────────


def _to_uuid(uid: uuid.UUID | str) -> uuid.UUID:
    return uid if isinstance(uid, uuid.UUID) else uuid.UUID(str(uid))


def today_sh() -> date:
    """上海时区的"今天"（到期判定统一用它，避免 UTC 造成一天偏差）。"""
    return datetime.now(_SH).date()


def _pct(end: float, start: float) -> float | None:
    if start == 0:
        return None
    return round((end / start - 1.0) * 100.0, 4)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def entry_status(entry: DecisionJournalEntry, reviewed: bool, today: date) -> str:
    """``reviewed`` > ``due`` > ``open``：已复盘优先（一条判断只能有一种状态）。"""
    if reviewed:
        return "reviewed"
    return "due" if entry.due_on <= today else "open"


def wilson_interval(successes: int, n: int, *, z: float = WILSON_Z) -> dict[str, Any] | None:
    """Wilson 95% 区间（spec §S9.7.7）：**比例必须带区间**，``n = 0`` 时返回 ``None``。

    为什么不用 ``p ± 1.96·sqrt(p(1-p)/n)``（Wald）：小样本 + p 接近 0/1 时 Wald 会给出
    ``[1.0, 1.0]`` 这种"5/5 一定全对"的假精确；Wilson 的区间宽度随样本缩小而张开，
    5/5 给的是 ``[0.58, 1.00]`` —— 那才是"5 次里 5 次对"能支撑的结论。

    ``n = 0`` 返回 ``None``（不是 ``[0, 1]`` 也不是 ``[0, 0]``）：没有样本时任何区间
    都是编造的，前端显示"样本不足"用 ``None`` 作判据。

    返回值固定四个键（与契约 §4 ``share_ci`` 逐字一致）：
    ``{"n", "successes", "ci_low", "ci_high"}``，比例保留 4 位、边界夹到 ``[0, 1]``。
    """
    if n <= 0:
        return None
    k = min(max(int(successes), 0), int(n))
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    # p(1-p) 用 k/n 计算；n=1 且 k=0/1 时 half 也不会退化为 0（Wilson 的关键性质）
    half = z * sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return {
        "n": int(n),
        "successes": k,
        "ci_low": round(max(0.0, center - half), 4),
        "ci_high": round(min(1.0, center + half), 4),
    }


# ── §S9.7.3 基准口径（创建时固化进快照，评估时按快照选基准） ────────────────


def bench_spec(
    *, symbol: str | None, bet_type: str, sector: dict[str, Any] | None = None
) -> dict[str, Any]:
    """算一条判断的 ``bench_spec``（spec §S9.7.3 的三种 ``bet_type``）。

    ============  ==========================================  ====================
    ``bet_type``  基准                                        ``fallback``
    ============  ==========================================  ====================
    ``direction`` 所属申万一级成分股等权平均（无行业 → 沪深300）  有行业时 ``False``
    ``relative``  同上（赌的是跑赢同行）                        同上
    ``market``    沪深300（**指定的**基准，不是退化）            恒为 ``False``
    ============  ==========================================  ====================

    ``fallback = True`` 只表示一件事：**本来要行业基准，但拿不到行业归属，退化到了
    沪深300**。它是"诚实标注"的一部分——用户看统计时会知道这条的基准不是他选的那个。
    """
    kind = bet_type if bet_type in BET_TYPES else "direction"
    code: str | None = None
    name: str | None = None
    if symbol is not None and kind != "market" and isinstance(sector, dict):
        raw_code = sector.get("code")
        if raw_code:
            code = str(raw_code)
            name = str(sector.get("name") or raw_code)
    if code is None:
        return {
            "bet_type": kind,
            "kind": BENCH_KIND_HS300,
            "industry_code": None,
            "industry_name": None,
            # 显式选了 market → 沪深300 就是他要的；否则是退化的
            "fallback": kind != "market",
        }
    return {
        "bet_type": kind,
        "kind": BENCH_KIND_INDUSTRY,
        "industry_code": code,
        "industry_name": name,
        "fallback": False,
    }


def bench_note(spec: dict[str, Any] | None) -> str:
    """把 ``bench_spec`` 翻成一句人话（创建与评估共用同一函数，措辞不会漂移）。"""
    spec = spec or {}
    if spec.get("kind") == BENCH_KIND_INDUSTRY:
        return BENCH_NOTE_INDUSTRY
    if spec.get("bet_type") == "market":
        return BENCH_NOTE_MARKET
    return BENCH_NOTE_FALLBACK


@dataclass(frozen=True)
class JournalRow:
    """一条判断 + 它的复盘（``None`` = 还没复盘） + 派生状态。"""

    entry: DecisionJournalEntry
    review: DecisionJournalReview | None
    status: str


# ── §S9.3 快照 ──────────────────────────────────────────────────────────────


def _empty_snapshot(symbol: str | None, as_of: date) -> dict[str, Any]:
    return {
        "version": SNAPSHOT_VERSION,
        "as_of": as_of.isoformat(),
        # 价格实际取自哪个交易日（可能早于 as_of：周末/盘前创建）
        "quote_as_of": None,
        "symbol": symbol,
        "price": None,
        "pct_chg": None,
        "ma20_dist_pct": None,
        "sentiment_percentile": None,
        "sector_flow_state": None,
        "events": [],
        "valuation_pct": None,
        # 基准口径（spec §S9.7.3）：创建时固化，事后不可改；默认值 = 市场级退化口径，
        # 真正的 spec 在 ``build_snapshot`` 末尾按 bet_type + 行业归属算出来覆盖
        "bench_spec": {
            "bet_type": "direction",
            "kind": BENCH_KIND_HS300,
            "industry_code": None,
            "industry_name": None,
            "fallback": True,
        },
        "bench_basis_note": BENCH_NOTE_FALLBACK,
        "missing_reason": {},
    }


async def _quote_rows(db: AsyncSession, symbol: str, as_of: date) -> list[Any]:
    """① 一次 join 取全快照需要的行情面：收盘/前收/量 + 涨停价 + ``pe_ttm``。"""
    stmt = (
        select(
            DailyQuote.trade_date,
            DailyQuote.close,
            DailyQuote.pre_close,
            DailyQuote.high,
            DailyQuote.low,
            DailyQuote.amount,
            Stock.name,
            StockPriceLimit.up_limit,
            DailyBasicIndicator.pe_ttm,
        )
        .select_from(DailyQuote)
        .join(Stock, Stock.id == DailyQuote.stock_id)
        .outerjoin(
            StockPriceLimit,
            (StockPriceLimit.stock_id == DailyQuote.stock_id)
            & (StockPriceLimit.trade_date == DailyQuote.trade_date),
        )
        .outerjoin(
            DailyBasicIndicator,
            (DailyBasicIndicator.stock_id == DailyQuote.stock_id)
            & (DailyBasicIndicator.trade_date == DailyQuote.trade_date),
        )
        .where(
            Stock.symbol == symbol,
            DailyQuote.trade_date <= as_of,
            DailyQuote.trade_date >= as_of - timedelta(days=QUOTE_WINDOW_NATURAL_DAYS),
        )
        .order_by(DailyQuote.trade_date)
    )
    rows = (await db.execute(stmt)).all()
    # 同一 symbol 在不同交易所各有一行 stocks 时 join 会重复出行：按交易日取首行
    # （A 股代码在交易所间不重叠；这里的去重只为"别让窗口里出现重复日"）。
    by_date: dict[date, Any] = {}
    for row in rows:
        by_date.setdefault(row.trade_date, row)
    return [by_date[day] for day in sorted(by_date)]


async def _sentiment_percentile(db: AsyncSession, as_of: date) -> tuple[float | None, str | None]:
    """② 情绪综合指标分位（``industry_metrics`` market scope 最近值，spec §S9.3）。"""
    rows = (
        await db.execute(
            select(IndustryMetric.period, IndustryMetric.value)
            .where(
                IndustryMetric.industry_key == "market",
                IndustryMetric.stock_id == 0,
                IndustryMetric.metric_key == COMPOSITE_METRIC_KEY,
                IndustryMetric.period <= as_of,
                IndustryMetric.value.is_not(None),
            )
            .order_by(IndustryMetric.period.desc())
            .limit(COMPOSITE_PERCENTILE_WINDOW)
        )
    ).all()
    values = [float(value) for _, value in reversed(rows) if value is not None]
    if not values:
        return None, "no_market_sentiment_row"
    _, percentile = composite_percentile(
        values,
        COMPOSITE_PERCENTILE_WINDOW,
        min_samples=COMPOSITE_PERCENTILE_MIN_SAMPLES,
    )
    if percentile is None:
        # 样本不足**不给假分位**（与 S1.13 读面同一纪律）：null + 原因，不是 0
        return None, "percentile_below_min_samples"
    return percentile, None


async def _sector_state(
    db: AsyncSession, symbol: str, as_of: date
) -> tuple[dict[str, Any] | None, str | None]:
    """③ 所属申万一级当日涨跌幅 + 全市场 L1 分位（复用体检的同一口径）。"""
    # SECTOR_SQL 是 triage_service 的 TextClause（同一份口径，不复制 SQL）
    row = (await db.execute(SECTOR_SQL, {"as_of": as_of, "symbols": [symbol]})).mappings().first()
    if row is None:
        return None, "no_sector_data"
    return (
        {
            "code": str(row["l1_code"]),
            "name": str(row["l1_name"] or row["l1_code"]),
            "pct": round(float(row["pct"]), 4),
            "rank": round(float(row["pr"]), 4),
        },
        None,
    )


async def _announcements(db: AsyncSession, symbol: str, as_of: date) -> list[td.AnnRow]:
    """④ 近 N 个自然日公告（探测器 ``new_announcement`` 的唯一输入）。"""
    cutoff = datetime.combine(as_of - timedelta(days=ANNOUNCEMENT_DAYS), time.min)
    rows = (
        await db.execute(
            select(
                Announcement.announcement_id,
                Announcement.title,
                Announcement.category,
                Announcement.announce_time,
            )
            .where(Announcement.sec_code == symbol, Announcement.announce_time >= cutoff)
            .order_by(Announcement.announce_time.desc())
            .limit(20)
        )
    ).all()
    return [
        td.AnnRow(
            announcement_id=str(announcement_id),
            title=str(title or ""),
            category=str(category or ""),
            announcement_time=announce_time,
        )
        for announcement_id, title, category, announce_time in rows
    ]


async def _dragon_tiger(
    db: AsyncSession, symbol: str, recent_days: Sequence[date], as_of: date
) -> list[td.DtRow]:
    """⑤ 龙虎榜（探测器 ``dragon_tiger`` 的唯一输入）。"""
    stmt = select(DragonTigerEntry.trade_date, DragonTigerEntry.net_amount).where(
        DragonTigerEntry.ts_code.startswith(f"{symbol}.")
    )
    if recent_days:
        stmt = stmt.where(DragonTigerEntry.trade_date.in_(list(recent_days)))
    else:
        stmt = stmt.where(
            DragonTigerEntry.trade_date >= as_of - timedelta(days=DRAGON_TIGER_NATURAL_DAYS)
        )
    rows = (await db.execute(stmt)).all()
    return [
        td.DtRow(trade_date=trade_date, net_amount=float(net_amount or 0))
        for trade_date, net_amount in rows
    ]


def _events(ctx: td.TriageContext, symbol: str) -> list[dict[str, str]]:
    """跑全部 S8 探测器，取最要紧的 ≤3 条（``{detector, headline}``）。

    排序按 severity 上限（risk → event → attention → background），同级按当日
    振幅降序 —— 与 ``triage_service.compose`` 同一套排序键，页面上"两个地方说的
    是同一件事"才不会因为排序不同而看起来矛盾。
    """
    items = [
        item for spec in td.TRIAGE_DETECTORS if (item := td.detect(spec, ctx, symbol)) is not None
    ]
    items.sort(key=lambda item: (td.SEVERITY_RANK[item.severity], -abs(item.day_pct or 0.0)))
    return [{"detector": item.detector, "headline": item.headline} for item in items[:EVENT_LIMIT]]


async def build_snapshot(
    db: AsyncSession,
    *,
    symbol: str | None,
    as_of: date,
    bet_type: str = "direction",
) -> dict[str, Any]:
    """抓一次"当时的市场状态"（**只读 + ≤5 条查询**，spec §S9.3）。

    市场级判断（``symbol is None``）只有情绪一项可算：价格/均线/估值/板块都
    挂在标的身上 → 全部 ``null`` + ``missing_reason``，``events = []``。

    ``bet_type``（spec §S9.7.3）只影响快照末尾的两个元数据键（``bench_spec`` /
    ``bench_basis_note``），**不新增查询**：行业归属复用第 ③ 条已经取到的
    ``sector_flow_state``，绝不为了基准再查一次库。
    """
    snap = _empty_snapshot(symbol, as_of)
    missing: dict[str, str] = snap["missing_reason"]

    if symbol is None:
        for key in ("price", "pct_chg", "ma20_dist_pct", "sector_flow_state", "valuation_pct"):
            missing[key] = "market_level_no_symbol"
    else:
        rows = await _quote_rows(db, symbol, as_of)
        effective = as_of
        quotes: list[td.QuoteRow] = []
        limit_row: td.LimitRow | None = None
        name = symbol
        if rows:
            last = rows[-1]
            effective = last.trade_date
            snap["quote_as_of"] = effective.isoformat()
            name = str(last.name or symbol)
            snap["price"] = round(float(last.close), 4)
            base = last.pre_close
            if base is None and len(rows) > 1:
                # 历史行缺 pre_close：退化为上一行收盘（与 triage 同口径，绝不用 adj_factor 推）
                base = rows[-2].close
            pct = _pct(float(last.close), float(base)) if base is not None else None
            if pct is None:
                missing["pct_chg"] = "no_pre_close"
            else:
                snap["pct_chg"] = pct
            closes = [float(row.close) for row in rows[-MA_DAYS:]]
            if len(closes) >= MA_DAYS:
                ma = sum(closes) / len(closes)
                dist = _pct(float(last.close), ma)
                if dist is None:
                    missing["ma20_dist_pct"] = "ma20_is_zero"
                else:
                    snap["ma20_dist_pct"] = dist
            else:
                missing["ma20_dist_pct"] = "insufficient_history"
            pe_values = [float(row.pe_ttm) for row in rows if row.pe_ttm is not None][
                -VALUATION_DAYS:
            ]
            if len(pe_values) >= 2:
                low, high = min(pe_values), max(pe_values)
                if high > low:
                    snap["valuation_pct"] = round((pe_values[-1] - low) / (high - low) * 100.0, 2)
                else:
                    missing["valuation_pct"] = "flat_pe_range"
            else:
                missing["valuation_pct"] = "no_pe_ttm"
            prev_close: float | None = None
            for row in rows:
                close = float(row.close)
                # 历史行缺 pre_close：退化为**上一行**收盘（与 triage 同口径，绝不用
                # adj_factor 推）；首行仍缺 → 用自身收盘（当日涨跌幅记 0%，可复核）。
                pre_close = (
                    float(row.pre_close)
                    if row.pre_close is not None
                    else (prev_close if prev_close is not None else close)
                )
                quotes.append(
                    td.QuoteRow(
                        trade_date=row.trade_date,
                        close=close,
                        pre_close=pre_close,
                        amount=float(row.amount or 0),
                    )
                )
                prev_close = close
            if last.up_limit is not None:
                limit_row = td.LimitRow(
                    up_limit=float(last.up_limit),
                    high=float(last.high or 0),
                    close=float(last.close),
                    pre_close=float(last.pre_close or 0),
                )
        else:
            missing["price"] = "no_daily_quote"
            missing["pct_chg"] = "no_daily_quote"
            missing["ma20_dist_pct"] = "no_daily_quote"
            missing["valuation_pct"] = "no_daily_quote"
            quotes = []

        sector, sector_reason = await _sector_state(db, symbol, effective)
        if sector is None:
            missing["sector_flow_state"] = sector_reason or "no_sector_data"
        snap["sector_flow_state"] = sector

        anns = await _announcements(db, symbol, effective)
        recent_days = [row.trade_date for row in quotes[-3:]]
        dragon = await _dragon_tiger(db, symbol, recent_days, effective)

        ctx = td.TriageContext(
            as_of=effective,
            names={symbol: name},
            symbols_order=[symbol],
            quotes={symbol: quotes} if quotes else {},
            limits={symbol: limit_row} if limit_row is not None else {},
            announcements={symbol: anns} if anns else {},
            dragon_tiger={symbol: dragon} if dragon else {},
        )
        if sector is not None:
            code = sector["code"]
            ctx.sector_of[symbol] = code
            ctx.sector_name[code] = sector["name"]
            ctx.sector_pct[code] = float(sector["pct"])
            ctx.sector_rank[code] = float(sector["rank"])
        snap["events"] = _events(ctx, symbol)

    percentile, reason = await _sentiment_percentile(db, as_of)
    if percentile is None:
        missing["sentiment_percentile"] = reason or "no_market_sentiment_row"
    snap["sentiment_percentile"] = percentile
    # ★ spec §S9.7.3：基准口径在**创建时**固化（不查库：复用上面的行业状态）
    spec = bench_spec(
        symbol=symbol,
        bet_type=bet_type,
        sector=snap["sector_flow_state"] if isinstance(snap["sector_flow_state"], dict) else None,
    )
    snap["bench_spec"] = spec
    snap["bench_basis_note"] = bench_note(spec)
    return snap


# ── §S9.4 到期结果（单条 / 批量共用一套口径） ───────────────────────────────


#: 标的的期末行情 + 区间极值。列序：window_low / window_high / end_close / end_trade_date
def _symbol_face_sql() -> str:
    sub = """
        (SELECT q2.{col}
           FROM daily_quotes q2
           JOIN stocks s2 ON s2.id = q2.stock_id
          WHERE s2.symbol = {symbol}
            AND q2.trade_date <= {end}
          ORDER BY q2.trade_date DESC
          LIMIT 1) AS {alias}"""
    return f"""
    SELECT
        min(q.low)  AS window_low,
        max(q.high) AS window_high,
        {sub.format(col="close", symbol=":symbol", end=":due_on", alias="end_close")},
        {sub.format(col="trade_date", symbol=":symbol", end=":due_on", alias="end_trade_date")}
      FROM daily_quotes q
      JOIN stocks s ON s.id = q.stock_id
     WHERE s.symbol = :symbol
       AND q.trade_date > :start_date
       AND q.trade_date <= :due_on
    """


#: 批量版（列表页用）：一条 SQL 覆盖 N 个条目 —— 查询数与条目数**无关**（反 N+1）。
#:
#: 为什么用 ``CAST(:ids AS bigint[])`` 而不是 ``:ids::bigint[]``：SQLAlchemy 的 bind
#: 正则带 ``(?!:)`` 前瞻（保护 PostgreSQL 的 ``::`` cast），写成 ``:ids::bigint[]``
#: 时参数**根本不会被识别**，SQL 会原样带着 ``:ids`` 发到 PG 报语法错。
_SYMBOL_FACE_BATCH_SQL = text(
    """
    WITH req(id, symbol, start_date, end_date) AS (
        SELECT * FROM unnest(
            CAST(:ids AS bigint[]),
            CAST(:symbols AS text[]),
            CAST(:starts AS date[]),
            CAST(:ends AS date[])
        ) AS t(id, symbol, start_date, end_date)
    )
    SELECT req.id,
           w.window_low,
           w.window_high,
           e.close      AS end_close,
           e.trade_date AS end_trade_date
      FROM req
      LEFT JOIN LATERAL (
          SELECT min(q.low) AS window_low, max(q.high) AS window_high
            FROM daily_quotes q
            JOIN stocks s ON s.id = q.stock_id
           WHERE s.symbol = req.symbol
             AND q.trade_date > req.start_date
             AND q.trade_date <= req.end_date
      ) w ON true
      LEFT JOIN LATERAL (
          SELECT q.close, q.trade_date
            FROM daily_quotes q
            JOIN stocks s ON s.id = q.stock_id
           WHERE s.symbol = req.symbol
             AND q.trade_date <= req.end_date
           ORDER BY q.trade_date DESC
           LIMIT 1
      ) e ON true
    """
)

#: 指数（沪深300）的期末行情 + 区间极值 + 起始收盘。列序：
#: window_low / window_high / end_close / end_trade_date / start_close
_INDEX_FACE_BATCH_SQL = text(
    """
    WITH req(id, code, start_date, end_date) AS (
        SELECT * FROM unnest(
            CAST(:ids AS bigint[]),
            CAST(:codes AS text[]),
            CAST(:starts AS date[]),
            CAST(:ends AS date[])
        ) AS t(id, code, start_date, end_date)
    )
    SELECT req.id,
           w.window_low,
           w.window_high,
           e.close       AS end_close,
           e.trade_date  AS end_trade_date,
           s.close       AS start_close
      FROM req
      LEFT JOIN LATERAL (
          SELECT min(i.low) AS window_low, max(i.high) AS window_high
            FROM index_dailies i
           WHERE i.ts_code = req.code
             AND i.trade_date > req.start_date
             AND i.trade_date <= req.end_date
      ) w ON true
      LEFT JOIN LATERAL (
          SELECT i.close, i.trade_date
            FROM index_dailies i
           WHERE i.ts_code = req.code
             AND i.trade_date <= req.end_date
           ORDER BY i.trade_date DESC
           LIMIT 1
      ) e ON true
      LEFT JOIN LATERAL (
          SELECT i.close
            FROM index_dailies i
           WHERE i.ts_code = req.code
             AND i.trade_date <= req.start_date
           ORDER BY i.trade_date DESC
           LIMIT 1
      ) s ON true
    """
)

#: 申万一级"行业指数平均"：成员起止收盘的等权平均涨跌幅（与 ``triage_service._SECTOR_SQL``
#: 同一口径）。起止各取"该成员 <= 日期 的最近收盘"，停牌股自动回落到停牌前价格。
_SECTOR_BENCH_BATCH_SQL = text(
    """
    WITH req(id, l1_code, start_date, end_date) AS (
        SELECT * FROM unnest(
            CAST(:ids AS bigint[]),
            CAST(:codes AS text[]),
            CAST(:starts AS date[]),
            CAST(:ends AS date[])
        ) AS t(id, l1_code, start_date, end_date)
    )
    SELECT req.id, b.bench_pct
      FROM req
      LEFT JOIN LATERAL (
          WITH l1_members AS (
              SELECT m.symbol
                FROM sw_industry_members m
                JOIN sw_industry_classes c3
                  ON c3.industry_code = m.industry_code AND c3.level = 3
                JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
                JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
               WHERE c1.industry_code = req.l1_code
          ),
          ids AS (
              SELECT s.id FROM stocks s JOIN l1_members m ON m.symbol = s.symbol
          ),
          start_px AS (
              SELECT DISTINCT ON (q.stock_id) q.stock_id, q.close
                FROM daily_quotes q JOIN ids ON ids.id = q.stock_id
               WHERE q.trade_date <= req.start_date
               ORDER BY q.stock_id, q.trade_date DESC
          ),
          end_px AS (
              SELECT DISTINCT ON (q.stock_id) q.stock_id, q.close
                FROM daily_quotes q JOIN ids ON ids.id = q.stock_id
               WHERE q.trade_date <= req.end_date
               ORDER BY q.stock_id, q.trade_date DESC
          )
          SELECT avg((e.close / NULLIF(s.close, 0) - 1) * 100) AS bench_pct
            FROM start_px s JOIN end_px e USING (stock_id)
           WHERE s.close > 0
      ) b ON true
    """
)


@dataclass(frozen=True)
class _Plan:
    """一条判断的"算结果所需的全部输入"（**全部来自行内快照**，不查库）。"""

    entry: DecisionJournalEntry
    start_date: date
    start_price: float | None
    kind: str
    threshold: float | None
    sector_code: str | None
    bet_type: str


def _plan(entry: DecisionJournalEntry) -> _Plan:
    snapshot: dict[str, Any] = entry.snapshot or {}
    start_date = _as_date(snapshot.get("quote_as_of")) or _as_date(snapshot.get("as_of"))
    if start_date is None:
        start_date = entry.created_at.date() if entry.created_at else entry.due_on
    invalidation: dict[str, Any] = entry.invalidation or {}
    kind = str(invalidation.get("kind") or "")
    threshold: float | None = None
    raw = invalidation.get("value")
    if kind in {"price_below", "price_above"} and raw is not None:
        try:
            threshold = float(raw)
        except (TypeError, ValueError):
            threshold = None
    sector = snapshot.get("sector_flow_state") or {}
    code = sector.get("code") if isinstance(sector, dict) else None
    price = snapshot.get("price")
    # 基准口径优先读**快照里**的 bench_spec（创建时固化，历史行不会随代码变化而改口径）；
    # 没有 bench_spec 的老行（s9.r1）退回列上的 bet_type（迁移给了默认 direction）。
    spec = snapshot.get("bench_spec")
    spec_bet = spec.get("bet_type") if isinstance(spec, dict) else None
    bet_type = str(spec_bet or getattr(entry, "bet_type", None) or "direction")
    if bet_type not in BET_TYPES:
        bet_type = "direction"
    return _Plan(
        entry=entry,
        start_date=start_date,
        start_price=None if price is None else float(price),
        kind=kind,
        threshold=threshold,
        sector_code=str(code) if code else None,
        bet_type=bet_type,
    )


def _face_from_row(row: Any) -> dict[str, Any]:
    if row is None:
        return {
            "window_low": None,
            "window_high": None,
            "end_close": None,
            "end_trade_date": None,
            "start_close": None,
        }
    return {
        "window_low": row[1],
        "window_high": row[2],
        "end_close": row[3],
        "end_trade_date": row[4],
        "start_close": row[5] if len(row) > 5 else None,
    }


def _ids(plans: Sequence[_Plan]) -> list[int]:
    return [int(plan.entry.id) for plan in plans]


async def _symbol_faces(db: AsyncSession, plans: Sequence[_Plan]) -> dict[int, dict[str, Any]]:
    if not plans:
        return {}
    rows = (
        await db.execute(
            _SYMBOL_FACE_BATCH_SQL,
            {
                "ids": _ids(plans),
                "symbols": [str(plan.entry.symbol) for plan in plans],
                "starts": [plan.start_date for plan in plans],
                "ends": [plan.entry.due_on for plan in plans],
            },
        )
    ).all()
    return {int(row[0]): _face_from_row(row) for row in rows}


async def _index_faces(
    db: AsyncSession, plans: Sequence[_Plan], *, code: str
) -> dict[int, dict[str, Any]]:
    if not plans:
        return {}
    rows = (
        await db.execute(
            _INDEX_FACE_BATCH_SQL,
            {
                "ids": _ids(plans),
                "codes": [code for _ in plans],
                "starts": [plan.start_date for plan in plans],
                "ends": [plan.entry.due_on for plan in plans],
            },
        )
    ).all()
    return {int(row[0]): _face_from_row(row) for row in rows}


async def _sector_benches(db: AsyncSession, plans: Sequence[_Plan]) -> dict[int, float | None]:
    if not plans:
        return {}
    rows = (
        await db.execute(
            _SECTOR_BENCH_BATCH_SQL,
            {
                "ids": _ids(plans),
                "codes": [str(plan.sector_code) for plan in plans],
                "starts": [plan.start_date for plan in plans],
                "ends": [plan.entry.due_on for plan in plans],
            },
        )
    ).all()
    return {int(row[0]): (None if row[1] is None else round(float(row[1]), 4)) for row in rows}


@dataclass(frozen=True)
class _Bench:
    """一条判断实际用到的基准（**算完之后**的样子：值 + 退化标记 + 人话说明）。"""

    pct: float | None
    reason: str | None
    fallback: bool
    note: str


def _index_bench(face: dict[str, Any], *, fallback: bool) -> _Bench:
    """沪深300 基准：用 ``index_dailies`` 的起止收盘（真实指数行情）。"""
    start_close = face.get("start_close")
    end_close = face.get("end_close")
    pct = (
        None
        if start_close is None or end_close is None
        else _pct(float(end_close), float(start_close))
    )
    return _Bench(
        pct=pct,
        reason=None if pct is not None else "index_bench_unavailable",
        fallback=fallback,
        note=BENCH_NOTE_FALLBACK if fallback else BENCH_NOTE_MARKET,
    )


def _assemble(
    plan: _Plan,
    face: dict[str, Any],
    *,
    bench: _Bench,
    market_level: bool,
    as_of: date | None,
) -> dict[str, Any]:
    """把"快照 + 期末行情 + 基准"装配成结果（**纯函数**：单条与批量共用，口径不会分叉）。"""
    entry = plan.entry
    end_trade_date = face.get("end_trade_date")
    result: dict[str, Any] = {
        "due_on": entry.due_on.isoformat(),
        # 数据截至哪天（不是"到期日"）：期末那根 K 线的实际交易日
        "price_as_of": (end_trade_date or entry.due_on).isoformat(),
        "outcome_pct": None,
        "bench_pct": None,
        "excess_pct": None,
        # ★ spec §S9.7.2 净口径（毛 − 12bp 往返成本）；既有字段类型一个都不变
        "net_outcome_pct": None,
        "net_excess_pct": None,
        "invalidation_hit": None,
        # ★ spec §S9.7.3：基准到底是什么（人话 + 是否退化），与 bench_pct 同时下发
        "bench_basis_note": bench.note,
        "bench_fallback": bench.fallback,
        "missing_reason": {},
    }
    missing: dict[str, str] = result["missing_reason"]
    end_close = face.get("end_close")

    if market_level:
        # 市场级：outcome 与 bench 都用沪深300（"市场有没有按我说的走"）
        if bench.pct is None:
            missing["outcome_pct"] = bench.reason or "index_bench_unavailable"
            missing["bench_pct"] = bench.reason or "index_bench_unavailable"
        else:
            result["outcome_pct"] = bench.pct
            result["bench_pct"] = bench.pct
    else:
        if plan.start_price is None:
            missing["outcome_pct"] = "snapshot_price_missing"
        elif end_close is None:
            missing["outcome_pct"] = "no_close_on_or_before_due_on"
        else:
            pct = _pct(float(end_close), plan.start_price)
            if pct is None:
                missing["outcome_pct"] = "start_price_is_zero"
            else:
                result["outcome_pct"] = pct
        if bench.pct is None:
            missing["bench_pct"] = bench.reason or "bench_unavailable"
        else:
            result["bench_pct"] = bench.pct

    if plan.kind == "date_only":
        result["invalidation_hit"] = (as_of or today_sh()) >= entry.due_on
    elif plan.kind in {"price_below", "price_above"}:
        if plan.threshold is None:
            missing["invalidation_hit"] = "invalidation_value_missing"
        elif face.get("window_low") is None or face.get("window_high") is None:
            missing["invalidation_hit"] = "no_quote_window"
        elif plan.kind == "price_below":
            result["invalidation_hit"] = float(face["window_low"]) <= plan.threshold
        else:
            result["invalidation_hit"] = float(face["window_high"]) >= plan.threshold
    else:
        missing["invalidation_hit"] = "invalidation_kind_unknown"

    gross_outcome = result["outcome_pct"]
    bench_pct = result["bench_pct"]
    if gross_outcome is not None and bench_pct is not None:
        result["excess_pct"] = round(gross_outcome - bench_pct, 4)

    # 净口径 = 毛口径 − 12bp（spec §S9.7.2）。缺数时**不补 0**：null + 同一原因
    if gross_outcome is not None:
        result["net_outcome_pct"] = round(gross_outcome - COST_ROUND_TRIP_PCT, 4)
    else:
        missing["net_outcome_pct"] = missing.get("outcome_pct") or "outcome_pct_unavailable"
    if result["net_outcome_pct"] is not None and bench_pct is not None:
        result["net_excess_pct"] = round(result["net_outcome_pct"] - bench_pct, 4)
    else:
        missing["net_excess_pct"] = (
            missing.get("net_outcome_pct") or missing.get("bench_pct") or "net_excess_unavailable"
        )
    return result


async def evaluate_entries(
    db: AsyncSession,
    entries: Sequence[DecisionJournalEntry],
    *,
    as_of: date | None = None,
) -> dict[int, dict[str, Any]]:
    """**批量**算到期结果（列表页用）：数据查询数与条目数无关（反 N+1）。

    PIT：起始价**只**取 ``snapshot["price"]``（市场级取沪深300 在快照日的收盘）；
    区间统计严格取 ``(快照日, due_on]``，不使用快照日及之前的数据。

    基准（spec §S9.7.3）按 ``bet_type`` 选，**查询数仍然是 3 条封顶**：

    ==========================  ================================================
    谁需要                     查什么
    ==========================  ================================================
    有 symbol 的判断           标的的期末行情/区间极值（一条批 LATERAL）
    要行业基准的判断           申万一级成员等权平均（一条批 LATERAL）
    要沪深300 的判断            指数起止收盘（一条批 LATERAL，三种情形合并）
    ==========================  ================================================
    """
    plans = [_plan(entry) for entry in entries]
    if not plans:
        return {}
    symbol_plans = [plan for plan in plans if plan.entry.symbol]
    market_plans = [plan for plan in plans if not plan.entry.symbol]
    # 显式 market 赌注：outcome 看标的、bench 看沪深300
    market_bet_plans = [plan for plan in symbol_plans if plan.bet_type == "market"]
    # 要行业基准的：标的存在 + 不是 market 赌注 + 快照里有申万一级归属
    industry_plans = [
        plan for plan in symbol_plans if plan.bet_type != "market" and plan.sector_code
    ]
    # 无论有没有行业归属都可能退化：没有行业的直接进沪深300 名单
    no_sector_plans = [
        plan for plan in symbol_plans if plan.bet_type != "market" and not plan.sector_code
    ]

    faces = await _symbol_faces(db, symbol_plans)
    sector_benches = await _sector_benches(db, industry_plans)
    # 没有申万归属**或**该行业的等权平均缺数 → 退化为沪深300（spec §S9.4/§S9.7.3）
    fallback_plans = [
        plan for plan in industry_plans if sector_benches.get(int(plan.entry.id)) is None
    ]
    index_faces = await _index_faces(
        db,
        [*market_plans, *market_bet_plans, *fallback_plans, *no_sector_plans],
        code=HS300_TS_CODE,
    )

    results: dict[int, dict[str, Any]] = {}
    for plan in plans:
        entry_id = int(plan.entry.id)
        market_level = not plan.entry.symbol
        if market_level:
            face = index_faces.get(entry_id) or _face_from_row(None)
            bench = _index_bench(face, fallback=plan.bet_type != "market")
        else:
            face = faces.get(entry_id) or _face_from_row(None)
            if plan.bet_type == "market":
                bench = _index_bench(
                    index_faces.get(entry_id) or _face_from_row(None), fallback=False
                )
            elif sector_benches.get(entry_id) is not None:
                bench = _Bench(
                    pct=sector_benches[entry_id],
                    reason=None,
                    fallback=False,
                    note=BENCH_NOTE_INDUSTRY,
                )
            else:
                bench = _index_bench(
                    index_faces.get(entry_id) or _face_from_row(None), fallback=True
                )
        results[entry_id] = _assemble(
            plan, face, bench=bench, market_level=market_level, as_of=as_of
        )
    return results


async def evaluate_entry(
    db: AsyncSession,
    entry: DecisionJournalEntry,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """单条结果（= 批量版取自己那条：**同一套口径**，不会出现"详情与列表不一致"）。"""
    return (await evaluate_entries(db, [entry], as_of=as_of))[int(entry.id)]


# ── 创建 / 读取 / 复盘 / 统计 ───────────────────────────────────────────────


def snapshot_line(snapshot: dict[str, Any] | None) -> str:
    """列表行的一行快照摘要（spec §S9.1「快照摘要（1 行）」）。

    **只拼有值的字段**（缺数就少一段，不补 0、不写"0 元"）；一段都没有时明说
    "创建时没有取到快照"——那是一个可核对的事实，不是一个空字符串。
    文案由后端给（前端同文兜底），避免两处措辞漂移。
    """
    snap = snapshot or {}
    parts: list[str] = []
    price = snap.get("price")
    if price is not None:
        parts.append(f"价格 {float(price):.2f} 元")
    pct = snap.get("pct_chg")
    if pct is not None:
        parts.append(f"当日 {float(pct):+.2f}%")
    percentile = snap.get("sentiment_percentile")
    if percentile is not None:
        parts.append(f"情绪分位 {round(float(percentile))}")
    events = snap.get("events") or []
    if events:
        parts.append(f"当日事件 {len(events)} 条")
    return " · ".join(parts) if parts else "创建时没有取到快照"


async def create_entry(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    payload: JournalEntryCreate,
    *,
    now: datetime | None = None,
) -> DecisionJournalEntry:
    """写入一条判断（快照在**创建时**抓，``due_on`` 在**写入时**固化）。

    ``payload`` 是 ``app/schemas/journal.py`` 的 ``JournalEntryCreate``，字段：
    ``symbol/kind/bet_type/thesis/invalidation/horizon_days``。``invalidation`` 的 DB
    形状由 schema 的 ``to_json()`` 产出（``{"kind":..., "value":...}``）。

    ``bet_type``（spec §S9.7.3）同时落**列**（聚合用）与``snapshot.bench_spec``
    （不可变口径，评估时以它为准）：一处是查询用的结构，一处是"当时怎么定的"证据。
    """
    uid = _to_uuid(user_id)
    created_at = now or datetime.now(_SH)
    as_of = created_at.date()
    bet_type = payload.bet_type
    snapshot = await build_snapshot(db, symbol=payload.symbol, as_of=as_of, bet_type=bet_type)
    entry = DecisionJournalEntry(
        user_id=uid,
        symbol=payload.symbol,
        kind=payload.kind,
        bet_type=bet_type,
        thesis=payload.thesis,
        invalidation=payload.invalidation.to_json(),
        horizon_days=payload.horizon_days,
        snapshot=snapshot,
        created_at=created_at,
        due_on=as_of + timedelta(days=payload.horizon_days),
    )
    db.add(entry)
    await db.flush()
    return entry


async def list_entries(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    *,
    status: str | None = None,
    today: date | None = None,
) -> list[JournalRow]:
    """按状态取列表（``open`` 未到期 / ``due`` 到期待复盘 / ``reviewed`` 已复盘）。

    **不在这里算 evaluation**：那会让每条一次查询（N+1）。自动结果由
    ``GET /api/v1/journal/{id}`` 单条返回（同一份 ``evaluate_entry``）。
    """
    uid = _to_uuid(user_id)
    today = today or today_sh()
    stmt = (
        select(DecisionJournalEntry, DecisionJournalReview)
        .outerjoin(DecisionJournalReview, DecisionJournalReview.entry_id == DecisionJournalEntry.id)
        .where(DecisionJournalEntry.user_id == uid)
    )
    if status == "reviewed":
        stmt = stmt.where(DecisionJournalReview.id.is_not(None))
    elif status == "due":
        stmt = stmt.where(DecisionJournalReview.id.is_(None), DecisionJournalEntry.due_on <= today)
    elif status == "open":
        stmt = stmt.where(DecisionJournalReview.id.is_(None), DecisionJournalEntry.due_on > today)
    elif status is not None:
        raise ValueError(f"未知 status：{status}（只接受 {list(STATUSES)}）")
    stmt = stmt.order_by(DecisionJournalEntry.created_at.desc(), DecisionJournalEntry.id.desc())
    stmt = stmt.limit(LIST_LIMIT)
    rows = (await db.execute(stmt)).all()
    return [
        JournalRow(
            entry=entry,
            review=review,
            status=entry_status(entry, review is not None, today),
        )
        for entry, review in rows
    ]


async def status_counts(
    db: AsyncSession, user_id: uuid.UUID | str, *, today: date | None = None
) -> dict[str, int]:
    """三态计数（一条聚合查询，**与条数上限无关**：截断列表不会让角标变少）。"""
    uid = _to_uuid(user_id)
    today = today or today_sh()
    row = (
        await db.execute(
            select(
                func.count(DecisionJournalEntry.id),
                func.count(DecisionJournalReview.id),
                func.count(DecisionJournalEntry.id).filter(
                    DecisionJournalReview.id.is_(None),
                    DecisionJournalEntry.due_on <= today,
                ),
                func.count(DecisionJournalEntry.id).filter(
                    DecisionJournalReview.id.is_(None),
                    DecisionJournalEntry.due_on > today,
                ),
            )
            .select_from(DecisionJournalEntry)
            .outerjoin(
                DecisionJournalReview,
                DecisionJournalReview.entry_id == DecisionJournalEntry.id,
            )
            .where(DecisionJournalEntry.user_id == uid)
        )
    ).one()
    total, reviewed_n, due_n, open_n = (int(value or 0) for value in row)
    return {"total": total, "open": open_n, "due": due_n, "reviewed": reviewed_n}


async def get_entry(
    db: AsyncSession, user_id: uuid.UUID | str, entry_id: int, *, today: date | None = None
) -> JournalRow:
    """取一条（**非本人 → 404**：查询里就带 ``user_id``，不泄露别人有没有这条）。"""
    uid = _to_uuid(user_id)
    today = today or today_sh()
    row = (
        await db.execute(
            select(DecisionJournalEntry, DecisionJournalReview)
            .outerjoin(
                DecisionJournalReview, DecisionJournalReview.entry_id == DecisionJournalEntry.id
            )
            .where(
                DecisionJournalEntry.id == entry_id,
                DecisionJournalEntry.user_id == uid,
            )
        )
    ).first()
    if row is None:
        raise EntryNotFound(f"决策日志条目不存在：{entry_id}")
    entry, review = row
    return JournalRow(
        entry=entry, review=review, status=entry_status(entry, review is not None, today)
    )


async def review_entry(
    db: AsyncSession,
    user_id: uuid.UUID | str,
    entry_id: int,
    *,
    verdict: str,
    note: str | None = None,
    now: datetime | None = None,
) -> DecisionJournalReview:
    """写入一次复盘（append-only）：未到期/已复盘/结果未就绪都**拒绝**，不静默兜底。"""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict 越界：{verdict}（只接受 {list(VERDICTS)}）")
    today = (now or datetime.now(_SH)).date()
    row = await get_entry(db, user_id, entry_id, today=today)
    if row.review is not None:
        raise AlreadyReviewed(f"条目 {entry_id} 已复盘，不可再评")
    if today < row.entry.due_on:
        raise NotDueYet(f"条目 {entry_id} 尚未到期（due_on={row.entry.due_on}）")
    evaluation = await evaluate_entry(db, row.entry, as_of=today)
    outcome = evaluation["outcome_pct"]
    bench = evaluation["bench_pct"]
    invalidation_hit = evaluation["invalidation_hit"]
    if outcome is None or bench is None or invalidation_hit is None:
        raise EvaluationNotReady(
            f"自动结果未就绪（缺行情/基准），不得用 0 兜底写入：{evaluation['missing_reason']}"
        )
    review = DecisionJournalReview(
        entry_id=row.entry.id,
        outcome_pct=outcome,
        bench_pct=bench,
        excess_pct=round(outcome - bench, 4),
        invalidation_hit=bool(invalidation_hit),
        verdict=verdict,
        note=note,
        reviewed_at=now or datetime.now(_SH),
    )
    db.add(review)
    try:
        await db.flush()
    except IntegrityError as exc:  # unique(entry_id) 兜底：并发两次复盘也只能进一条
        raise AlreadyReviewed(f"条目 {entry_id} 已复盘，不可再评") from exc
    return review


async def stats(db: AsyncSession, user_id: uuid.UUID | str) -> dict[str, Any]:
    """统计（spec §S9.4 + §S9.7.2/§S9.7.6/§S9.7.7）：**两条聚合查询封顶**。

    自洽约束（每一句都被 ``tests/test_journal_*.py`` 钉死）：

    - ``by_verdict`` / ``by_attribution`` 合计 = ``reviewed_n``；
    - ``by_kind`` 合计 = ``n``（spec §S9.7.6：``pass`` 计入**判断**统计）；
    - ``avg_excess_pct`` / ``avg_net_excess_pct`` **排除 ``kind=pass``**（spec §S9.7.6：
      放弃的判断没有收益，把它算进平均会把"没做"混进"做了"的收益）；
    - ``discipline_break_share`` 在 ``reviewed_n < 5`` 时为 ``null``（不是 0）；
    - 比例一律同时给 Wilson 95% 区间（``share_ci``，``n=0`` 时为 ``None``）。

    为什么两个名字给**同一组计数**：spec 的验收判据是"``by_attribution`` 合计 =
    ``reviewed_n``（算术自洽）"，而前端类型（``JournalStats.by_verdict``）要的是
    "五枚举 → 条数"的纯数字映射（占比由前端用 ``count / reviewed_n`` 自己算）。
    两处都必须是**数值**，不能是 ``{n, share}`` 对象——否则前端把对象当子节点渲染会崩。

    ``stats`` 的**产物版本**是 :data:`JOURNAL_STATS_VERSION`（口径常量一变必须一起 bump）。

    查询设计（反 N+1 的延伸：与用户条数无关）：

    ① 一条聚合拿齐 ``n / reviewed_n`` + 五个 ``by_kind`` 计数 + ``win`` 成功数
       （``FILTER`` 子句比五次往返便宜，且保持 1 条）；
    ② 一条聚合按 ``(verdict, kind)`` 分组拿 ``count`` 与 ``avg(excess_pct)``，
       verdict 分栏与"排除 pass 的收益均值"都从它推导。
    """
    uid = _to_uuid(user_id)
    # 净超额的"赢"判据：excess_pct - 12bp > 0（与 COST_ROUND_TRIP_PCT 同一个常量）
    cost = Decimal(str(COST_ROUND_TRIP_PCT))
    total_row = (
        await db.execute(
            select(
                func.count(DecisionJournalEntry.id),
                func.count(DecisionJournalReview.id),
                *(
                    func.count(DecisionJournalEntry.id).filter(DecisionJournalEntry.kind == kind)
                    for kind in ENTRY_KINDS
                ),
                func.count(DecisionJournalReview.id).filter(
                    DecisionJournalReview.excess_pct > cost
                ),
            )
            .select_from(DecisionJournalEntry)
            .outerjoin(
                DecisionJournalReview,
                DecisionJournalReview.entry_id == DecisionJournalEntry.id,
            )
            .where(DecisionJournalEntry.user_id == uid)
        )
    ).one()
    n = int(total_row[0] or 0)
    reviewed_n = int(total_row[1] or 0)
    by_kind = {kind: int(total_row[2 + index] or 0) for index, kind in enumerate(ENTRY_KINDS)}
    win_n = int(total_row[2 + len(ENTRY_KINDS)] or 0)

    grouped = (
        await db.execute(
            select(
                DecisionJournalReview.verdict,
                DecisionJournalEntry.kind,
                func.count(DecisionJournalReview.id),
                func.avg(DecisionJournalReview.excess_pct),
            )
            .join(DecisionJournalEntry, DecisionJournalEntry.id == DecisionJournalReview.entry_id)
            .where(DecisionJournalEntry.user_id == uid)
            .group_by(DecisionJournalReview.verdict, DecisionJournalEntry.kind)
        )
    ).all()
    counts: dict[str, int] = {verdict: 0 for verdict in VERDICTS}
    #: 收益口径的样本：``pass`` 是"我决定不动"，没有收益可算（spec §S9.7.6）
    revenue_n = 0
    revenue_sum = 0.0
    for verdict, kind, count, avg in grouped:
        key = str(verdict)
        group_n = int(count or 0)
        counts[key] = counts.get(key, 0) + group_n
        if str(kind) == "pass":
            continue
        revenue_n += group_n
        if avg is not None:
            # 先按组四舍五入再乘条数：与"逐条求和"在 1e-4 内一致，且不丢精度口径
            revenue_sum += round(float(avg), 4) * group_n

    share_available = reviewed_n >= MIN_REVIEWED_FOR_SHARE
    gross_avg = revenue_sum / revenue_n if revenue_n else None
    return {
        "n": n,
        "reviewed_n": reviewed_n,
        "pending_n": n - reviewed_n,
        "by_verdict": dict(counts),
        "by_attribution": dict(counts),
        # ★ 净口径是默认口径（spec §S9.7.2）：毛口径只在这里出现一次，供"口径说明"用
        "avg_excess_pct": None if gross_avg is None else round(gross_avg, 4),
        "avg_net_excess_pct": (
            None if gross_avg is None else round(gross_avg - COST_ROUND_TRIP_PCT, 4)
        ),
        "discipline_break_share": (
            round(counts["discipline_break"] / reviewed_n, 4) if share_available else None
        ),
        "min_reviewed_for_share": MIN_REVIEWED_FOR_SHARE,
        "sample_sufficient": share_available,
        # ★ spec §S9.7.6 选择偏差：pass 在判断统计里有独立分栏
        "by_kind": by_kind,
        # ★ spec §S9.7.7：比例必须带区间（n=0 → None，不是 [0,1]）
        "share_ci": {
            "discipline_break": wilson_interval(counts["discipline_break"], reviewed_n),
            "win": wilson_interval(win_n, reviewed_n),
        },
        "net_cost_bps": COST_ROUND_TRIP_BPS,
        "multiple_comparison_note": MULTIPLE_COMPARISON_NOTE,
    }
