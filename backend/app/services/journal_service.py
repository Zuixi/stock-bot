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

import csv
import io
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import sqrt
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_basic import DailyBasicIndicator
from app.models.industry_research import IndustryMetric
from app.models.journal import (
    BET_TYPES,
    CONFIDENCE_VALUES,
    ENTRY_KINDS,
    OBSERVABLE_INVALIDATION_KINDS,
    POSITION_SIZES,
    VERDICTS,
    DecisionJournalEntry,
    DecisionJournalReview,
)
from app.models.market_data import Announcement, DragonTigerEntry, StockPriceLimit
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.schemas.journal import JournalEntryCreate
from app.services import peer_history_service as peer_history
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
    "BENCH_NOTE_HS300",
    "BENCH_NOTE_INDUSTRY",
    "CALIBRATION_P",
    "CONSISTENCY_ALWAYS_VERDICTS",
    "CONSISTENCY_NEGATIVE_VERDICTS",
    "CONSISTENCY_NOTE",
    "CONSISTENCY_POSITIVE_VERDICTS",
    "COST_ROUND_TRIP_BPS",
    "COST_ROUND_TRIP_PCT",
    "EARNINGS_IN_WINDOW_NOTE",
    "EXCLUSION_NOTE",
    "EXPORT_CSV_COLUMNS",
    "EXPORT_HINT",
    "JOURNAL_STATS_VERSION",
    "MIN_CALIBRATION_BIN_SAMPLE",
    "MIN_CALIBRATION_SAMPLE",
    "MULTIPLE_COMPARISON_NOTE",
    "SIZE_WEIGHTS",
    "SNAPSHOT_FIELDS",
    "SNAPSHOT_META_FIELDS",
    "SNAPSHOT_VERSION",
    "UNLOCK_IN_WINDOW_NOTE",
    "UNLOCK_RISK_RATIO",
    "WILSON_Z",
    "AlreadyReviewed",
    "EntryNotFound",
    "EvaluationNotReady",
    "JournalRow",
    "NotDueYet",
    "NotReviewable",
    "bench_note",
    "bench_spec",
    "build_snapshot",
    "calibration_summary",
    "consistency_summary",
    "create_entry",
    "evaluate_entry",
    "evaluate_entries",
    "export_csv",
    "export_json",
    "export_rows",
    "get_entry",
    "invalidation_observable",
    "list_entries",
    "review_entry",
    "reviewable",
    "snapshot_line",
    "stats",
    "status_counts",
    "summary",
    "wilson_interval",
]

_SH = ZoneInfo("Asia/Shanghai")

#: 快照 schema 版本。**新增/改名字段必须同时改 spec §S9.3 与本文件**（快照是落库的
#: jsonb，历史行不会自动补字段，所以版本号是"这行是哪种形状"的唯一判据）。
#:
#: ``s9.r2``（2026-10-19，S9.7 Phase A）：新增 ``bench_spec`` / ``bench_basis_note``
#: 两个元数据键（基准口径在**创建时**固化，spec §S9.7.3）。``s9.r1`` 的行没有这两个键，
#: 读取方按 ``bet_type=entry.bet_type`` + 当场可得的数据重算（不得改写历史快照）。
#:
#: ``s9.r3``（2026-10-19，S9.6）：新增 ``peer_history``（创建那一刻的同类历史，
#: spec §S9.6.4）。老行没有它 → 读取方按"当时没有这段信息"处理，**不补算**
#: （补算等于把今天的数据塞进昨天的快照）。
SNAPSHOT_VERSION = "s9.r3"

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
    # ★ s9.r3（spec §S9.6.4）：创建那一刻看到的"同类历史"必须落库——
    # 以后算法/行情变了，用户当时看到的那段仍然可复现（与"不可篡改"同一条哲学）
    "peer_history",
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
#: 改 ``COST_ROUND_TRIP_BPS``、``WILSON_Z``、基准口径**或 ``share_ci.win`` 的样本分母
#: 时必须同时改这里（有钉死用例）。
#:
#: ``s97.b1``（2026-10-19，Phase B2）：``share_ci.win`` 的分母**排除 ``pass``**（裁定 2）、
#: 新增 ``calibration`` / ``by_size`` / ``avg_weighted_excess_pct`` / ``by_kind_median``
#: —— 分母变了就是口径变了，所以版本必须动。
#:
#: ``s97.b2``（2026-10-19，口径修正）：基准按 ``bet_type`` **真正区分**（``direction`` →
#: 沪深300、``relative`` → 行业等权，spec §S9.7.3），且收益均值（``avg_excess_pct`` /
#: ``avg_net_excess_pct`` / ``avg_weighted_excess_pct``）**同时排除 ``pass`` 与
#: ``position_size=none``**（spec §S9.7.1/§S9.7.6）—— 样本集合变了 = 口径变了。
#:
#: ``s97.b3``（2026-10-19，深度优化）：新增 ``consistency``（归因自洽率，契约 §2）——
#: 这是一个**新的比例**，它的样本集合（已复盘且净超额可算）与分子判据（verdict 与
#: 净超额符号的一致性）都是新口径，统计产物因此换代。
JOURNAL_STATS_VERSION = "s97.b3"

# ── spec §S9.7.3 基准口径 ───────────────────────────────────────────────────
#: 基准种类 ①：**所属申万一级成分股的等权平均收益**。诚实标注：这**不是**申万一级
#: 指数行情（库里没有指数日线），是"成员股等权平均"，差异写进 ``bench_basis_note``。
#: 只有 ``bet_type=relative``（赌跑赢同行）用它；``direction`` 用的是沪深300。
BENCH_KIND_INDUSTRY = "industry_equal_weight"
#: 基准种类 ②：沪深300（``index_dailies`` 真实指数行情）。``direction`` / ``market`` 用它。
BENCH_KIND_HS300 = "hs300"
#: 人话文案（后端下发，前端不硬编码，避免两处措辞漂移）
BENCH_NOTE_HS300 = "基准=沪深300"
BENCH_NOTE_INDUSTRY = "基准=所属申万一级成分股等权平均收益（非指数行情）"
BENCH_NOTE_FALLBACK = "基准=沪深300（无申万一级归属，已退化为市场基准）"

# ── spec §S9.7.7 区间估计 ───────────────────────────────────────────────────
#: Wilson 95% 区间的 z 值（双侧 0.975 分位）。固定 1.96，不用 scipy（零新依赖）。
WILSON_Z = 1.96
#: 统计页固定标注（spec §S9.7.7 逐字，前端原样展示）：
#: 按 45 个桶（S9.6 的 5×3×3）检索时，总会有桶看起来"显著"——我们**不做**多重比较校正。
MULTIPLE_COMPARISON_NOTE = "未做多重比较校正：按 45 个桶检索时总会有桶看起来显著"

# ── spec §S9.7.5 校准：信心档 → 固定先验概率 ────────────────────────────────
#: 信心 1..5 → 该档"判断为对"的**固定**先验概率。
#:
#: **不可优化**（spec §S9.7.5 逐字）：这张表是复盘反馈的锚，一旦按历史数据反向拟合，
#: 校准曲线就会变成"自我实现的预言"（用结果解释信心，再用信心解释结果）。
#: 改这张表 = 改了"你的信心跟结果对不对得上"这件事的含义，必须同时改 spec §S9.7.5
#: 与 ``JOURNAL_STATS_VERSION``。
CALIBRATION_P: dict[int, float] = {1: 0.2, 2: 0.35, 3: 0.5, 4: 0.65, 5: 0.8}
#: 校准曲线**总样本**下限（spec §S9.7.5）：``n < 20`` 不给曲线，只给一句原因。
MIN_CALIBRATION_SAMPLE = 20
#: 校准**单档**样本下限（spec §S9.7.5）：``n < 10`` 的档显示"样本不足"，不给命中率。
MIN_CALIBRATION_BIN_SAMPLE = 10
#: 总样本不足时下发的**原因**（前端原样展示；拼上当前条数后用户才知道还差几条）。
CALIBRATION_INSUFFICIENT_NOTE = "样本不足（<20 条已复盘且填了信心）"

# ── spec §S9.7.1 仓位权重（只用于 `avg_weighted_excess_pct`） ───────────────
#: 风险单位 → 权重。``none`` = 只观察不下单 → 权重 0（**排除**，不是乘 0 后混进分母）。
SIZE_WEIGHTS: dict[str, float] = {"none": 0.0, "light": 0.5, "normal": 1.0, "heavy": 2.0}

# ── spec §S9.7.6 收益统计的排除口径（人话，后端下发） ───────────────────────
#: 统计卡原样展示的一句话（前端不改写）：``pass`` 没有方向，不进任何均值；
#: ``position_size=none``（只观察）仍进"判断均值"，但不进"按仓位加权的收益均值"。
EXCLUSION_NOTE = "「我决定不动」不计入均值；「只观察」计入判断均值、不计入按仓位加权的收益均值"

# ── 契约 §2 归因自洽（"我归类的原因"与"净超额的符号"是否方向一致） ──────────
#: 「我错了」这两类：自洽当且仅当**净超额 < 0**（确实亏了，才叫"理由错/时机错"）。
CONSISTENCY_NEGATIVE_VERDICTS: tuple[str, ...] = ("thesis_wrong", "timing_wrong")
#: 「我对了」这两类：自洽当且仅当**净超额 > 0**（赚过成本线，才叫"运气/蒙对"）。
CONSISTENCY_POSITIVE_VERDICTS: tuple[str, ...] = ("right_for_wrong_reason", "luck")
#: 恒自洽（契约 §2 逐字）：纪律问题与盈亏**独立**——破纪律却赚钱，纪律这一条事实
#: 依然成立，不该被收益符号判成"前后矛盾"。这一条是自洽率的**锚**：没有它，
#: 用户会觉得"记纪律问题反而被扣分"，从而学会不记。
CONSISTENCY_ALWAYS_VERDICTS: tuple[str, ...] = ("discipline_break",)
#: 契约 §2 **逐字**文案（前端原样展示，不得改写）。
#: 内层引号是 ASCII 双引号（契约原文如此）：故意不在措辞上做"优化"。
CONSISTENCY_NOTE = '不自洽不代表你说谎：按过程评价自己（如"赚了但理由错"）本来就会与收益符号不一致'

# ── 契约 §1 财报窗口（`evaluation.earnings_in_window` / `earnings_note`） ────
#: 窗口内有披露时的人话模板（``n`` = 窗口内 **distinct 公告日**的个数）。同一天公告
#: 多份报表（例如 Q3 与修正版同日）算**一次**披露——用户感知的"事件"是那一天。
EARNINGS_IN_WINDOW_NOTE = "窗口内含 {n} 次财报披露（{days}），结果可能被财报事件主导"

# ── 契约 §S11.3 解禁窗口（`evaluation.unlock_in_window` / `unlock_note`） ────
#: 窗口内有 ≥3% 解禁时的人话模板（``n`` = 窗口内 ≥3% 的**解禁日**个数；同日多个持有人算
#: 一次解禁，与 ``share_floats`` 的持有人级明细无关）。与财报窗口同构。
#:
#: **分母是总股本**（实机核过：301387 的 63,999,208 股 / ``daily_basic.total_share``
#: 10,730.19 万股 = 59.64%，与源 ``float_ratio`` 59.6441 逐位吻合）。spec §S11.3 的
#: 示例写"占流通"，源数据不支持这个分母（对流通股本是 150%），故文案按真实口径写
#: "占总股本"——宁可偏离示例措辞，也不把 3 倍大的比例当成"占流通"给用户。
UNLOCK_IN_WINDOW_NOTE = "窗口内含 {n} 次解禁（{days}），结果可能被解禁冲击主导"
#: 「解禁窗口」只认**占总股本 ≥ 这个比例**的解禁日（spec §S11.3）：小比例解禁不足以主导结果，
#: 把它算成 true 会让"窗口内有解禁"变成几乎所有股票都成立的空话。
UNLOCK_RISK_RATIO = 3.0

# ── spec §S9.7.9 导出 ───────────────────────────────────────────────────────
#: 统计卡下发的导出地址（前端直接用，不自己拼；改路径必须同时改这里与路由）。
EXPORT_HINT = "/api/v1/journal/export?format=csv"
#: 导出 CSV 的**固定列序**（spec §S9.7.9 逐字；前端/脚本按位置读，不按表头猜）。
EXPORT_CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "symbol",
    "kind",
    "bet_type",
    "position_size",
    "confidence",
    "thesis",
    "invalidation",
    "horizon_days",
    "created_at",
    "due_on",
    "status",
    "outcome_pct",
    "bench_pct",
    "excess_pct",
    "net_excess_pct",
    "verdict",
)


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


class NotReviewable(JournalError):
    """失效条件不可观测（spec §S9.7.8）：允许创建/展示结果，但**不给归因入口**。"""


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


# ── 契约 §2 归因自洽：把"分子/分母"装配成 `stats.consistency`（纯函数） ─────


def consistency_summary(consistent: int, n: int) -> dict[str, Any]:
    """归因自洽率（契约 §2）：调用方只从库里数两个数，**判定与算术全在这里**。

    ================  ==========================================================
    输出               口径
    ================  ==========================================================
    ``n``             已复盘**且净超额可算**的条数（分母）
    ``consistent``    分子：``thesis_wrong``/``timing_wrong`` 且净超额 < 0 ·
                      ``discipline_break``（恒）· ``right_for_wrong_reason``/``luck``
                      且净超额 > 0
    ``share``         ``consistent / n``（4 位）；``n < 5`` 时 **``None``**（不是 0）
    ``ci_low/high``   复用 :func:`wilson_interval`（spec §S9.7.7，**不另造区间**）；
                      ``n < 5`` 时同样 ``None``
    ``note``          契约 §2 逐字免责文案（前端原样展示）
    ================  ==========================================================

    为什么 ``n < 5`` 连点估计都不给（契约 §2 逐字）：3/4 与 4/6 在 4 条样本上
    没有可区分性，给个百分比只会被读成"我的归因有 75% 是对的"。样本不足时只报
    ``n``，页面上写"样本不足"。

    为什么 ``discipline_break`` 恒自洽：纪律与盈亏是**两件独立的事**（契约 §2）。
    若"破纪律却赚钱"被记成不自洽，等于告诉用户"守纪律会扣分"，自洽率就成了
    惩罚诚实记录的指标——那比不统计更糟。

    边界：净超额**恰好等于 0**（毛超额正好 12bp）不算赚也不算亏 → 四个"需要符号"
    的类别全部记不自洽（``>` / ``<` 都为假），与 ``share_ci.win`` 的"恰在成本线上
    不算对"同一口径。
    """
    denominator = max(int(n), 0)
    numerator = min(max(int(consistent), 0), denominator)
    if denominator < MIN_REVIEWED_FOR_SHARE:
        return {
            "n": denominator,
            "consistent": numerator,
            "share": None,
            "ci_low": None,
            "ci_high": None,
            "note": CONSISTENCY_NOTE,
        }
    interval = wilson_interval(numerator, denominator)
    # ``denominator >= 5`` 保证了 interval 不为 None；断言而非猜测（改了上面阈值要一起改）
    assert interval is not None
    return {
        "n": denominator,
        "consistent": numerator,
        "share": round(numerator / denominator, 4),
        "ci_low": interval["ci_low"],
        "ci_high": interval["ci_high"],
        "note": CONSISTENCY_NOTE,
    }


# ── §S9.7.5 校准：`hit_rate` 与 `brier` 都只看**净超额 > 0** ────────────────


def calibration_summary(
    samples: dict[int, tuple[int, int]],
) -> tuple[dict[str, Any] | None, str | None]:
    """把"每档 ``(已复盘条数, 净超额>0 的条数)``"装配成校准曲线（纯函数）。

    ``samples`` 的键是 ``confidence``（1..5），值是该档的 ``(n, hits)``；调用方只负责
    从库里数数，**判定与算术全在这里**（前端拿不到任何"自己算"的自由度）。

    =======================  ==================================================
    输出                     口径
    =======================  ==================================================
    ``n``                    Σ 各档 n = **已复盘且填了信心**的条数（没填 = 不参与）
    ``bins[].p``             :data:`CALIBRATION_P`（固定常量，不可优化）
    ``bins[].hit_rate``      ``hits / n``（**净超额 > 0** 才算对，不是毛收益）
    ``bins[].sufficient``    ``n >= MIN_CALIBRATION_BIN_SAMPLE``（10）
    ``brier``                ``mean((p - I[net_excess>0])**2)``，见下
    =======================  ==================================================

    **brier 为什么能从"每档 n 与 hits"算出来**：``p`` 只取决于 confidence 档，
    同一档内每条记录的 ``p`` 相同，所以不需要逐条求和 ——
    ``Σ_c [(n_c - hits_c)·p_c² + hits_c·(1-p_c)²] / n`` 与"逐条 ``(p - I)²`` 再平均"
    在数学上完全相等（误差只在最后一步的四舍五入）。这样统计仍然只用**固定条数的
    聚合查询**，不把 N 条记录搬到 Python 里。

    **总样本 < 20 时返回 ``(None, 原因)``**（不是空曲线）：19 条样本画出来的曲线只会
    被读成"我的信心很准"，所以宁可不画（spec §S9.7.5 的展示纪律）。
    原因串里带上当前条数：用户要知道"还差几条"才有动力继续记录。
    """
    bins: list[dict[str, Any]] = []
    total = 0
    squared_error = 0.0
    for confidence in CONFIDENCE_VALUES:
        count, hits = samples.get(confidence, (0, 0))
        if count <= 0:
            # 该档没有样本 → **不出这一档**（不是 n=0 / hit_rate=0 的假数据点）
            continue
        p = CALIBRATION_P[confidence]
        total += count
        squared_error += (count - hits) * p * p + hits * (1.0 - p) ** 2
        bins.append(
            {
                "confidence": confidence,
                "p": p,
                "n": count,
                "hit_rate": round(hits / count, 4),
                "sufficient": count >= MIN_CALIBRATION_BIN_SAMPLE,
            }
        )
    if total < MIN_CALIBRATION_SAMPLE:
        # 原因串保留契约里的原话，只在末尾补"当前几条"（去掉原句的右括号再接回）
        return None, f"{CALIBRATION_INSUFFICIENT_NOTE[:-1]}，当前 {total} 条）"
    return {"n": total, "bins": bins, "brier": round(squared_error / total, 4)}, None


# ── §S9.7.8 可证伪闸：失效条件可观测吗（决定"能不能复盘"） ───────────────────


def invalidation_observable(invalidation: dict[str, Any] | None) -> bool:
    """失效条件是否**可观测**（spec §S9.7.8）：能落成 ``invalidation_observable``/``reviewable``。

    ==================  ====================================================
    形状                可观测？
    ==================  ====================================================
    ``price_below``     ``value`` 是正数 → 行情窗口能判定（``True``）
    ``price_above``     同上
    ``date_only``       ``value`` 是合法日期（到期即命中）→ ``True``
    其它/缺失/值无法解析  ``False``（历史行里的自由文本形态，如 ``{"kind":"logic_broken"}``）
    ==================  ====================================================

    **为什么值也要校验**：只看 ``kind`` 会把 ``{"kind":"price_below","value":"跌穿成本"}``
    判成可观测，而评估时它算不出 ``invalidation_hit``（``_plan`` 里 float() 失败）——
    "可复盘"必须与"结果能算"同一个判据，否则用户会被允许给一条算不出结果的判断归因。

    历史数据**不改写**：本函数只影响读取时的 ``reviewable`` / 端点是否拒绝。
    """
    if not isinstance(invalidation, dict):
        return False
    kind = invalidation.get("kind")
    if kind not in OBSERVABLE_INVALIDATION_KINDS:
        return False
    value = invalidation.get("value")
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    if kind == "date_only":
        return _as_date(value) is not None
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def reviewable(entry: DecisionJournalEntry) -> bool:
    """条目能否复盘（= 失效条件可观测，spec §S9.7.8）：响应字段 ``reviewable`` 与端点闸共用。"""
    return invalidation_observable(entry.invalidation)


# ── §S9.7.3 基准口径（创建时固化进快照，评估时按快照选基准） ────────────────


def bench_spec(
    *, symbol: str | None, bet_type: str, sector: dict[str, Any] | None = None
) -> dict[str, Any]:
    """算一条判断的 ``bench_spec``（spec §S9.7.3 的三种 ``bet_type``）。

    ============  ==========================================  ====================
    ``bet_type``  基准                                        ``fallback``
    ============  ==========================================  ====================
    ``direction`` 沪深300（赌"这只会涨" → 方向判断）            恒为 ``False``
    ``relative``  所属申万一级成分股等权平均（无行业 → 沪深300）  无行业归属时 ``True``
    ``market``    沪深300（**指定的**基准，不是退化）            恒为 ``False``
    ============  ==========================================  ====================

    **口径修正（2026-10-19）**：``direction`` 与 ``relative`` 曾都取行业等权（两者同构，
    等于"赌方向"和"赌跑赢同行"用同一把尺）。现在 ``direction`` 对齐沪深300 —— 它赌的是
    "这只/这个行业会涨"，衡量的对象是大盘；``relative`` 才是"跑赢同行"，用行业等权。

    ``fallback = True`` 只表示一件事：**本来要行业基准，但拿不到行业归属，退化到了
    沪深300**。只有 ``relative`` 会退化；``direction`` / ``market`` 的沪深300 是用户选的。
    """
    kind = bet_type if bet_type in BET_TYPES else "direction"
    if kind != "relative":
        return {
            "bet_type": kind,
            "kind": BENCH_KIND_HS300,
            "industry_code": None,
            "industry_name": None,
            "fallback": False,
        }
    code: str | None = None
    name: str | None = None
    if symbol is not None and isinstance(sector, dict):
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
            # 要行业基准却拿不到行业归属 → 唯一的退化情形
            "fallback": True,
        }
    return {
        "bet_type": kind,
        "kind": BENCH_KIND_INDUSTRY,
        "industry_code": code,
        "industry_name": name,
        "fallback": False,
    }


def bench_note(spec: dict[str, Any] | None) -> str:
    """把 ``bench_spec`` 翻成一句人话（创建与评估共用同一函数，措辞不会漂移）。

    文案随 ``kind`` 变：行业等权 → 明说"非指数行情"；沪深300 → 「基准=沪深300」。
    只有 ``relative`` 退化时额外标注"已退化"（``fallback=True``）。
    """
    spec = spec or {}
    if spec.get("kind") == BENCH_KIND_INDUSTRY:
        return BENCH_NOTE_INDUSTRY
    if spec.get("fallback"):
        return BENCH_NOTE_FALLBACK
    return BENCH_NOTE_HS300


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
        # 基准口径（spec §S9.7.3）：创建时固化，事后不可改；默认值 = 沪深300 口径，
        # 真正的 spec 在 ``build_snapshot`` 末尾按 bet_type + 行业归属算出来覆盖
        "bench_spec": {
            "bet_type": "direction",
            "kind": BENCH_KIND_HS300,
            "industry_code": None,
            "industry_name": None,
            "fallback": False,
        },
        "bench_basis_note": BENCH_NOTE_HS300,
        # s9.r3（spec §S9.6.4）：同类历史由 ``create_entry`` 在快照抓完之后填进来；
        # 这里先占位，保证"快照永远有这一格"（读方不用判键在不在，只判值）
        "peer_history": None,
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


#: 批量版（列表页用）：一条 SQL 覆盖 N 个条目 —— 查询数与条目数**无关**（反 N+1）。
#:
#: 为什么用 ``CAST(:ids AS bigint[])`` 而不是 ``:ids::bigint[]``：SQLAlchemy 的 bind
#: 正则带 ``(?!:)`` 前瞻（保护 PostgreSQL 的 ``::`` cast），写成 ``:ids::bigint[]``
#: 时参数**根本不会被识别**，SQL 会原样带着 ``:ids`` 发到 PG 报语法错。
#:
#: 列序（``_face_from_row`` 逐位读，**与指数 face 的列位对齐**）：id / window_low /
#: window_high / end_close / end_trade_date / start_close（标的不需要，恒 NULL，占位）/
#: **first_touch / days_to**。后两列是 spec §S9.7.4 的路径指标，由 ``req`` 里带的
#: 失效条件（``inv_kind`` + ``threshold``）在同一条 SQL 里算出来：区间极值算不出的
#: "**哪一天**首次触达"，必须让 SQL 逐日比较，否则要按天再查一次库（N+1）。
#:
#: 契约 §1 的财报窗口（``window_ann_dates`` / ``all_rows``）与 spec §S11.3 的解禁窗口
#: （``unlock_dates`` / ``unlock_ratios`` / ``unlock_all_rows``）也**并进这一条**：
#: 评估的查询数仍然是 3 条封顶（标的 face / 行业基准 / 沪深300），不因这两个窗口多一条。
_SYMBOL_FACE_BATCH_SQL = text(
    """
    WITH raw_req(id, symbol, start_date, end_date, inv_kind, threshold) AS (
        SELECT * FROM unnest(
            CAST(:ids AS bigint[]),
            CAST(:symbols AS text[]),
            CAST(:starts AS date[]),
            CAST(:ends AS date[]),
            CAST(:inv_kinds AS text[]),
            CAST(:thresholds AS double precision[])
        ) AS t(id, symbol, start_date, end_date, inv_kind, threshold)
    ),
    -- ★ spec §S11.3：把 symbol 还原成 ``share_floats.ts_code``（形如 600519.SH）。
    -- 为什么要在 SQL 里算：``share_floats`` 没有 ``stock_id`` 外键，只能按 ts_code 查；
    -- 而按 ``split_part(ts_code,'.',1)`` 过滤用不上索引 —— 一次列表评估有 ≤200 条判断，
    -- 每条再全表扫一遍 share_floats 就是几十秒（实测 50 条 4.9s）。用 ts_code 等值
    -- （ix_share_floats_code）后是每条一次索引查找。
    -- 后缀映射与 ``EXCHANGE_TO_TS_SUFFIX`` 一致（三值由 stocks 的 CHECK 约束保证）。
    req(id, symbol, start_date, end_date, inv_kind, threshold, ts_code) AS (
        SELECT r.id, r.symbol, r.start_date, r.end_date, r.inv_kind, r.threshold,
               s.symbol || CASE s.exchange
                               WHEN 'Shanghai_Stocks' THEN '.SH'
                               WHEN 'Shenzen_Stocks' THEN '.SZ'
                               ELSE '.BJ'
                           END
          FROM raw_req r
          LEFT JOIN stocks s ON s.symbol = r.symbol
    )
    SELECT req.id,
           w.window_low,
           w.window_high,
           e.close      AS end_close,
           e.trade_date AS end_trade_date,
           CAST(NULL AS double precision) AS start_close,
           ft.first_touch,
           d.days_to,
           earn.window_ann_dates,
           earn.all_rows,
           unl.unlock_dates,
           unl.unlock_ratios,
           unl.unlock_all_rows
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
      -- ★ spec §S9.7.4：首次触达失效条件的**交易日**（阈值由 req 带进来；
      -- date_only / 历史自由文本形态的 inv_kind 为 NULL → 条件恒不成立 → NULL）
      LEFT JOIN LATERAL (
          SELECT min(q.trade_date) AS first_touch
            FROM daily_quotes q
            JOIN stocks s ON s.id = q.stock_id
           WHERE s.symbol = req.symbol
             AND q.trade_date > req.start_date
             AND q.trade_date <= req.end_date
             AND (
                  (req.inv_kind = 'price_below' AND q.low <= req.threshold)
               OR (req.inv_kind = 'price_above' AND q.high >= req.threshold)
             )
      ) ft ON true
      -- ★ 触达用了几个**交易日**（不是自然日）：入场日之后、首次触达日之前/当天的 K 线根数
      LEFT JOIN LATERAL (
          SELECT count(DISTINCT q.trade_date) AS days_to
            FROM daily_quotes q
            JOIN stocks s ON s.id = q.stock_id
           WHERE ft.first_touch IS NOT NULL
             AND s.symbol = req.symbol
             AND q.trade_date > req.start_date
             AND q.trade_date <= ft.first_touch
      ) d ON true
      -- ★ 契约 §1：**(快照日, due_on]** 内的财报披露日（distinct 公告日）。
      -- PIT：窗口本身只看 ``ann_date <= req.end_date`` 的记录（事后才公告的不进窗口）；
      -- ``all_rows`` 只用来区分「有财报数据但不在窗口内」(false) 与「该股没有任何
      -- 财报记录」(null)，它不参与"窗口内发生了什么"的判定。
      -- 只数**带公告日**的财报行：没有 ``ann_date`` 的记录无法用于任何披露判断，
      -- 不拿它冒充"有财报数据"。
      LEFT JOIN LATERAL (
          SELECT array_agg(DISTINCT f.ann_date ORDER BY f.ann_date) FILTER (
                     WHERE f.ann_date > req.start_date
                       AND f.ann_date <= req.end_date
                 ) AS window_ann_dates,
                 count(*) AS all_rows
            FROM financial_report_versions f
            JOIN stocks s ON s.id = f.stock_id
           WHERE s.symbol = req.symbol
             AND f.ann_date IS NOT NULL
      ) earn ON true
      -- ★ spec §S11.3：**(快照日, due_on]** 内的解禁日（按解禁日合计占总股本比）。
      -- PIT：只用 ``ann_date <= req.end_date`` 的记录（公告后才可知）；
      -- ``unlock_all_rows`` 只用来区分「有解禁记录但都不在窗口内」(false) 与「该股没有任何
      -- 解禁记录」(null)，不参与"窗口内发生了什么"的判定。
      -- 两个 array_agg 必须成对：同一个 ORDER BY + 同一个 FILTER，_unlock() 按下标 zip。
      LEFT JOIN LATERAL (
          SELECT array_agg(u.float_date ORDER BY u.float_date) FILTER (
                     WHERE u.float_date > req.start_date
                       AND u.float_date <= req.end_date
                 ) AS unlock_dates,
                 array_agg(u.float_ratio ORDER BY u.float_date) FILTER (
                     WHERE u.float_date > req.start_date
                       AND u.float_date <= req.end_date
                 ) AS unlock_ratios,
                 count(*) AS unlock_all_rows
            FROM (
                -- 同一笔解禁会被多次公告（初次 + 修订）：按 (holder, share_type) 取
                -- **PIT 可见的（ann_date <= due_on）最新一次**，否则同一笔加两次
                -- （实测单日合计 119%，真实 59.6%）。
                SELECT d.float_date, sum(d.float_ratio) AS float_ratio
                  FROM (
                      SELECT DISTINCT ON (sf.float_date, sf.holder_name, sf.share_type)
                             sf.float_date, sf.float_ratio
                        FROM share_floats sf
                       WHERE sf.ts_code = req.ts_code
                         AND sf.ann_date IS NOT NULL
                         AND sf.ann_date <= req.end_date
                       ORDER BY sf.float_date, sf.holder_name, sf.share_type,
                                sf.ann_date DESC
                  ) d
                 GROUP BY d.float_date
            ) u
      ) unl ON true
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
    #: ★ spec §S9.7.3：该条实际要用的基准种类（优先读快照里的 ``bench_spec.kind``；
    #: 老行没有 ``bench_spec`` 时按 ``bet_type`` 推导：``relative`` → 行业，其余 → 沪深300）
    bench_kind: str
    #: ★ ``True`` 只表示"要行业基准却拿不到行业归属"（只有 ``relative`` 会退化）
    bench_fallback: bool
    #: ★ spec §S9.7.8：失效条件可观测（与 ``reviewable`` 同一判据）
    observable: bool


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
    # 没有 bench_spec 的老行（s9.r1）退回列上的 bet_type（迁移给了默认 direction），
    # 并按**修正后**的映射推导种类：direction/market → 沪深300、relative → 行业等权。
    spec = snapshot.get("bench_spec")
    spec = spec if isinstance(spec, dict) else {}
    spec_bet = spec.get("bet_type")
    bet_type = str(spec_bet or getattr(entry, "bet_type", None) or "direction")
    if bet_type not in BET_TYPES:
        bet_type = "direction"
    spec_kind = spec.get("kind")
    bench_kind = (
        str(spec_kind)
        if spec_kind in (BENCH_KIND_HS300, BENCH_KIND_INDUSTRY)
        else (BENCH_KIND_INDUSTRY if bet_type == "relative" else BENCH_KIND_HS300)
    )
    spec_fallback = spec.get("fallback")
    bench_fallback = (
        spec_fallback
        if isinstance(spec_fallback, bool)
        else (bet_type == "relative" and code is None)
    )
    return _Plan(
        entry=entry,
        start_date=start_date,
        start_price=None if price is None else float(price),
        kind=kind,
        threshold=threshold,
        sector_code=str(code) if code else None,
        bet_type=bet_type,
        bench_kind=bench_kind,
        bench_fallback=bench_fallback,
        # 与 reviewable 同一判据：不可观测的失效条件算不出 invalidation_hit，也不能复盘
        observable=invalidation_observable(invalidation),
    )


def _face_from_row(row: Any) -> dict[str, Any]:
    """把一条 face 行拆成字段（按下标读：``_SYMBOL_FACE_BATCH_SQL`` 的列序是契约）。

    指数 face（``_INDEX_FACE_BATCH_SQL``）只有 6 列 → ``first_touch`` / ``days_to``、
    契约 §1 的财报两列（``window_ann_dates`` / ``all_rows``）与 spec §S11.3 的解禁三列
    （``unlock_dates`` / ``unlock_ratios`` / ``unlock_all_rows``）自动为 ``None`` / ``0``
    （市场级判断没有"标的披露日/解禁日"，这两个窗口对它不适用）。
    """
    if row is None:
        return {
            "window_low": None,
            "window_high": None,
            "end_close": None,
            "end_trade_date": None,
            "start_close": None,
            "first_touch": None,
            "days_to": None,
            # 契约 §1：财报窗口（没有 face 行 = 没有任何财报证据）
            "window_ann_dates": None,
            "all_rows": 0,
            # spec §S11.3：解禁窗口（同样：没有 face 行 = 没有任何解禁证据）
            "unlock_dates": None,
            "unlock_ratios": None,
            "unlock_all_rows": 0,
        }
    return {
        "window_low": row[1],
        "window_high": row[2],
        "end_close": row[3],
        "end_trade_date": row[4],
        "start_close": row[5] if len(row) > 5 else None,
        # ★ spec §S9.7.4（只有标的 face 有这两列，见上面的列序契约）
        "first_touch": row[6] if len(row) > 6 else None,
        "days_to": row[7] if len(row) > 7 else None,
        # ★ 契约 §1 财报窗口（同样只在标的 face 里；指数 face 读不到 → 缺省值）
        "window_ann_dates": row[8] if len(row) > 8 else None,
        "all_rows": int(row[9] or 0) if len(row) > 9 else 0,
        # ★ spec §S11.3 解禁窗口（与财报同一条 SQL 的第 10–12 列；成对的 dates/ratios）
        "unlock_dates": row[10] if len(row) > 10 else None,
        "unlock_ratios": row[11] if len(row) > 11 else None,
        "unlock_all_rows": int(row[12] or 0) if len(row) > 12 else 0,
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
                # spec §S9.7.4：失效条件随批次带进去，SQL 直接算"首次触达日"（不新增查询）
                "inv_kinds": [
                    plan.kind if plan.kind in {"price_below", "price_above"} else None
                    for plan in plans
                ],
                "thresholds": [
                    plan.threshold if plan.kind in {"price_below", "price_above"} else None
                    for plan in plans
                ],
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
        note=BENCH_NOTE_FALLBACK if fallback else BENCH_NOTE_HS300,
    )


def _path_metrics(
    plan: _Plan, face: dict[str, Any], *, market_level: bool
) -> tuple[dict[str, Any], dict[str, str]]:
    """spec §S9.7.4 的四个路径指标（**纯函数**：全部来自快照 + 行情窗口，不查库）。

    定义（入场价 = ``snapshot["price"]``；区间 = ``(入场交易日, due_on]``，
    **只用 due_on 当天及之前的行情** —— 与 ``invalidation_hit`` 同一窗口）：

    ==========================  ==================================================
    字段                        口径
    ==========================  ==================================================
    ``mae_pct``                 ``(区间最低价 / 入场价 − 1) × 100``，**夹到 ≤ 0**
    ``first_touch_invalidation`` 首个满足 ``price_below``/``price_above`` 的**交易日**
    ``touched_then_reversed``   有触达 **且** 到期 ``net_outcome_pct > 0``
    ``days_to_invalidation``    入场日之后到首次触达日之间的**交易日数**（K 线根数）
    ==========================  ==================================================

    为什么 ``mae_pct`` 夹到 0：MAE 是"最不利偏移"，区间最低价高于入场价时
    **没有发生不利偏移** → 0。直接给正数会被读成"有利偏移"（那是另一个指标 MFE），
    而 MAE 为零是一个有意义的事实（不是缺数，所以不给 ``null``）。

    ``date_only``：到期即命中，不存在"先触达失效条件、之后反弹"这回事 →
    ``first_touch_invalidation`` / ``days_to_invalidation`` 为 ``null``、
    ``touched_then_reversed`` 为 ``false``（``mae_pct`` 仍是有效的路径事实，照算）。

    ``touched_then_reversed`` 只在"**确实触达** 且 **到期结果算得出来** 且 > 0"时为
    ``true``；任一条件缺 → ``false``（无法证明"先触后反"就不说它发生了）。

    市场级判断（``symbol is None``）：快照里没有 ``price``（价格挂在标的上），
    无从定义"相对入场价的跌幅" → ``mae_pct = null`` + 原因。
    """
    values: dict[str, Any] = {
        "mae_pct": None,
        "first_touch_invalidation": None,
        "touched_then_reversed": False,
        "days_to_invalidation": None,
    }
    missing: dict[str, str] = {}
    if market_level:
        missing["mae_pct"] = "market_level_no_symbol"
        return values, missing
    if plan.start_price is None:
        missing["mae_pct"] = "snapshot_price_missing"
    elif face.get("window_low") is None:
        missing["mae_pct"] = "no_quote_window"
    else:
        adverse = (float(face["window_low"]) / plan.start_price - 1.0) * 100.0
        values["mae_pct"] = round(min(0.0, adverse), 4)
    if plan.kind in {"price_below", "price_above"}:
        if plan.threshold is None:
            missing["first_touch_invalidation"] = "invalidation_value_missing"
        elif face.get("first_touch") is not None:
            first = face["first_touch"]
            values["first_touch_invalidation"] = (
                first.isoformat() if isinstance(first, date) else str(first)
            )
            if face.get("days_to") is not None:
                values["days_to_invalidation"] = int(face["days_to"])
    return values, missing


def _earnings(face: dict[str, Any], *, market_level: bool) -> tuple[dict[str, Any], dict[str, str]]:
    """契约 §1 的财报窗口（**纯函数**：只读成交里带回来的两个字段，不查库）。

    ======================  =====================================================
    字段                    口径
    ======================  =====================================================
    ``earnings_in_window``  ``True`` = ``(快照日, due_on]`` 内至少一次财报披露；
                            ``False`` = 有财报数据但窗口内没有；
                            ``None`` = 该股**没有任何（带公告日的）财报记录**
                            （缺数据，不猜 —— 不渲染成"没有财报"）
    ``earnings_note``       人话；``False`` / ``None`` 时是空串（没有事件就没有提示）
    ======================  =====================================================

    PIT：窗口只由 ``ann_date <= due_on`` 的记录算出（``window_ann_dates`` 在 SQL 里
    已经按 ``(start_date, end_date]`` 过滤）。``all_rows``（该股全部带公告日的财报行数）
    **只**用于区分 ``None`` 与 ``False``：它不参与"窗口内有没有发生披露"的判定，
    所以不会把"事后才公告"的记录读成窗口内事件。

    "N 次披露"按 **distinct 公告日** 数（同一天公告多份报表 = 一次披露）。

    市场级判断（``symbol is None``）：快照没有标的，财报窗口不适用 →
    ``None`` + ``missing_reason["earnings"] = "market_level_no_symbol"``。
    """
    values: dict[str, Any] = {"earnings_in_window": None, "earnings_note": ""}
    missing: dict[str, str] = {}
    if market_level:
        missing["earnings"] = "market_level_no_symbol"
        return values, missing
    if int(face.get("all_rows") or 0) == 0:
        # 该股一行带公告日的财报都没有 → 缺数据（不是"没有披露"）
        missing["earnings"] = "no_financial_reports"
        return values, missing
    raw_dates = face.get("window_ann_dates") or []
    days = sorted({parsed for raw in raw_dates if (parsed := _as_date(raw)) is not None})
    if not days:
        # 有财报数据，只是都不在 (快照日, due_on] 里 —— 这是一个可核对的事实
        values["earnings_in_window"] = False
        return values, missing
    values["earnings_in_window"] = True
    values["earnings_note"] = EARNINGS_IN_WINDOW_NOTE.format(
        n=len(days), days="、".join(day.strftime("%m-%d") for day in days)
    )
    return values, missing


def _unlock(face: dict[str, Any], *, market_level: bool) -> tuple[dict[str, Any], dict[str, str]]:
    """契约 §S11.3 的解禁窗口（**纯函数**：只读持仓里带回来的三列，不查库）。

    ==========================  =====================================================
    字段                        口径
    ==========================  =====================================================
    ``unlock_in_window``        ``True`` = ``(快照日, due_on]`` 内有解禁且该**解禁日**合计
                                占总股本 ``>= UNLOCK_RISK_RATIO``（3%）；
                                ``False`` = 有解禁记录，但窗口内没有 ≥3% 的解禁；
                                ``None`` = 该股**没有任何解禁记录**（缺数据，不猜 ——
                                不渲染成"不会解禁"）
    ``unlock_note``             人话；``False`` / ``None`` 时是空串（没有事件就没有提示）
    ==========================  =====================================================

    PIT：``unlock_all_rows`` 与两个数组都只由 ``ann_date <= due_on`` 的记录算出
    （公告之后才可知，见 face SQL 的 LATERAL）。窗口过滤发生在 **``float_date``**
    （= 解禁日，事件发生的那天）上，与财报窗口的 ``ann_date`` 口径不同，这是刻意的。

    "N 次解禁"按 **distinct 解禁日** 数（同一天多个持有人 = 一次解禁事件）。

    ``None`` 只写**降级键** ``missing_reason["unlock"]``：它不阻塞复盘（阻塞键见
    spec §S9 的清单，只认 outcome/bench/invalidation），否则"没有解禁记录"的股票会
    永远评不了分 —— 而这类股票在真实数据里占多数，会把复盘功能整体废掉。
    """
    values: dict[str, Any] = {"unlock_in_window": None, "unlock_note": ""}
    missing: dict[str, str] = {}
    if market_level:
        missing["unlock"] = "market_level_no_symbol"
        return values, missing
    if int(face.get("unlock_all_rows") or 0) == 0:
        # 该股一行解禁记录都没有 → 缺数据（不是"不会解禁"）
        missing["unlock"] = "no_share_float_rows"
        return values, missing
    # SQL 用同一个 ORDER BY + FILTER 成对生成两个数组（见 face SQL 注释），故可按下标 zip。
    events: list[tuple[date, float]] = []
    for raw_day, ratio in zip(face.get("unlock_dates") or [], face.get("unlock_ratios") or []):
        day = _as_date(raw_day)
        if day is None or ratio is None:
            continue
        events.append((day, float(ratio)))
    big = [(day, ratio) for day, ratio in events if ratio >= UNLOCK_RISK_RATIO]
    if not big:
        # 有解禁数据，只是窗口内没有 ≥3% 的解禁 —— 这是一个可核对的事实，不是缺数
        values["unlock_in_window"] = False
        return values, missing
    values["unlock_in_window"] = True
    values["unlock_note"] = UNLOCK_IN_WINDOW_NOTE.format(
        n=len(big), days="；".join(f"{day:%m-%d}，占总股本 {ratio:.1f}%" for day, ratio in big)
    )
    return values, missing


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
        # ★ spec §S9.7.4 路径分析（终点对错之外，中途发生了什么）
        "mae_pct": None,
        "first_touch_invalidation": None,
        "touched_then_reversed": False,
        "days_to_invalidation": None,
        # ★ 契约 §1 财报窗口（三态：True 有 / False 有数据但不在窗口 / None 无数据）
        "earnings_in_window": None,
        "earnings_note": "",
        # ★ spec §S11.3 解禁窗口（同样三态；None 只写降级键，不阻塞复盘）
        "unlock_in_window": None,
        "unlock_note": "",
        # ★ spec §S9.7.3：基准到底是什么（人话 + 是否退化），与 bench_pct 同时下发
        "bench_basis_note": bench.note,
        "bench_fallback": bench.fallback,
        # ★ spec §S9.7.8：失效条件可观测（False = 该条不可复盘，与 reviewable 同一判据）
        "invalidation_observable": plan.observable,
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

    # ★ spec §S9.7.4：路径四指标（与 invalidation_hit 同一行情窗口，不新增查询）
    path_values, path_missing = _path_metrics(plan, face, market_level=market_level)
    result.update(path_values)
    missing.update(path_missing)

    # ★ 契约 §1：财报窗口（数据由标的 face 同一条批 SQL 带回，不新增查询）
    earnings_values, earnings_missing = _earnings(face, market_level=market_level)
    result.update(earnings_values)
    missing.update(earnings_missing)

    # ★ spec §S11.3：解禁窗口（数据与财报同一条批 SQL 带回，不新增查询）
    unlock_values, unlock_missing = _unlock(face, market_level=market_level)
    result.update(unlock_values)
    missing.update(unlock_missing)

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

    # ★ spec §S9.7.4「先触失效、到期反而盈利」：净收益（不是净超额）> 0。
    # 触达与结果缺一不可 —— 无法证明就不说它发生了（false，而不是猜一个 true）。
    if result["first_touch_invalidation"] is not None and result["net_outcome_pct"] is not None:
        result["touched_then_reversed"] = result["net_outcome_pct"] > 0
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
    有 symbol 的判断           标的的期末行情/区间极值 + **首次触达失效条件的交易日**
                               + **财报窗口 + 解禁窗口**（同一个 SQL 的多条 LATERAL，不按天查）
    要行业基准的判断           申万一级成员等权平均（一条批 LATERAL；只有 ``relative``）
    要沪深300 的判断            指数起止收盘（一条批 LATERAL，三种情形合并：
                                ``direction`` / ``market`` / ``relative`` 退化）
    ==========================  ================================================
    """
    plans = [_plan(entry) for entry in entries]
    if not plans:
        return {}
    symbol_plans = [plan for plan in plans if plan.entry.symbol]
    market_plans = [plan for plan in plans if not plan.entry.symbol]
    # 要行业基准的：``relative``（快照 bench_spec.kind = 行业）+ 快照里有申万一级归属。
    # ``direction`` 不再走行业（spec §S9.7.3 口径修正：direction → 沪深300）。
    industry_plans = [
        plan for plan in symbol_plans if plan.bench_kind == BENCH_KIND_INDUSTRY and plan.sector_code
    ]
    # 有 symbol 但基准不是行业（direction / market / relative 无行业）→ 沪深300
    industry_ids = {int(plan.entry.id) for plan in industry_plans}
    hs300_symbol_plans = [plan for plan in symbol_plans if int(plan.entry.id) not in industry_ids]

    faces = await _symbol_faces(db, symbol_plans)
    sector_benches = await _sector_benches(db, industry_plans)
    # 有申万归属**但**该行业的等权平均缺数 → 退化为沪深300（spec §S9.4/§S9.7.3）
    fallback_plans = [
        plan for plan in industry_plans if sector_benches.get(int(plan.entry.id)) is None
    ]
    index_faces = await _index_faces(
        db,
        [*market_plans, *hs300_symbol_plans, *fallback_plans],
        code=HS300_TS_CODE,
    )

    results: dict[int, dict[str, Any]] = {}
    for plan in plans:
        entry_id = int(plan.entry.id)
        market_level = not plan.entry.symbol
        if market_level:
            # 市场级：outcome 与 bench 都用沪深300。``fallback`` 只有 ``relative``
            # 无行业归属时才为真（其余情形沪深300 就是用户选的基准）。
            face = index_faces.get(entry_id) or _face_from_row(None)
            bench = _index_bench(face, fallback=plan.bench_fallback)
        else:
            face = faces.get(entry_id) or _face_from_row(None)
            if plan.bench_kind == BENCH_KIND_INDUSTRY and sector_benches.get(entry_id) is not None:
                bench = _Bench(
                    pct=sector_benches[entry_id],
                    reason=None,
                    fallback=False,
                    note=BENCH_NOTE_INDUSTRY,
                )
            else:
                # 显式 direction/market（hs300，fallback=False）或 relative 无行业归属
                degraded = plan.bench_kind == BENCH_KIND_INDUSTRY or plan.bench_fallback
                bench = _index_bench(
                    index_faces.get(entry_id) or _face_from_row(None), fallback=degraded
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
    ``symbol/kind/bet_type/position_size/confidence/thesis/invalidation/horizon_days``。
    ``invalidation`` 的 DB 形状由 schema 的 ``to_json()`` 产出（``{"kind":..., "value":...}``）；
    非三形态在 schema 层已经是 422（spec §S9.7.8），本函数**不做二次收口**（单一闸门）。

    ``bet_type``（spec §S9.7.3）同时落**列**（聚合用）与``snapshot.bench_spec``
    （不可变口径，评估时以它为准）：一处是查询用的结构，一处是"当时怎么定的"证据。
    ``position_size`` / ``confidence``（spec §S9.7.1/§S9.7.5）只落列：它们是**判断的
    属性**（风险单位 / 事前信心），不是市场状态，进快照会让"当时市场什么样"混进主观项。
    ``kind=pass`` ⇒ ``position_size`` 只能是 ``none``（spec §S9.7.1/§S9.7.6 的口径修正）：
    入参闸在 schema 层（非 ``none`` → 422），未传时默认值就是 ``none``——本函数不重复收口。
    """
    uid = _to_uuid(user_id)
    created_at = now or datetime.now(_SH)
    as_of = created_at.date()
    bet_type = payload.bet_type
    snapshot = await build_snapshot(db, symbol=payload.symbol, as_of=as_of, bet_type=bet_type)
    # ★ spec §S9.6.4：同类历史在**创建时**算一次并落进快照（失败也不阻断创建）。
    # 放在 build_snapshot 之外是刻意的：快照的"五个数据面 ≤5 条查询"是既有契约，
    # 而这一段多一条 L1 查询（本人历史），不该让它挤进那 5 条的预算里。
    try:
        snapshot["peer_history"] = await peer_history.peer_history(
            db,
            uid,
            snapshot=snapshot,
            symbol=payload.symbol,
            computed_at=created_at,
        )
    except Exception:  # noqa: BLE001 — 任何失败都降级，创建必须成功（spec §S9.6.4）
        snapshot["peer_history"] = peer_history.failure()
    entry = DecisionJournalEntry(
        user_id=uid,
        symbol=payload.symbol,
        kind=payload.kind,
        bet_type=bet_type,
        position_size=payload.position_size,
        confidence=payload.confidence,
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


def _jsonb_list(raw: Any) -> list[Any]:
    """把 ``json_agg`` 的返回值还原成列表（asyncpg 对聚合出的 json 给字符串）。"""
    if not isinstance(raw, str):
        return raw if isinstance(raw, list) else []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:  # 理论上不会：json_agg 的输出一定是合法 JSON
        return []
    return parsed if isinstance(parsed, list) else []


async def summary(
    db: AsyncSession, user_id: uuid.UUID | str, *, today: date | None = None
) -> dict[str, int]:
    """``{open, due, reviewed}`` 角标（spec §S12.2）：**1 条聚合查询**。

    派生规则与 :func:`list_entries` / :func:`status_counts` **逐字相同**（状态不落库，
    由 ``due_on`` 与 reviews 行现算）：``reviewed`` = 有复盘行；``open`` = 无复盘行且
    ``due_on > today``；``due`` = 无复盘行且 ``due_on <= today``。

    ``due`` **额外要求 ``reviewable``**（失效条件可观测，spec §S9.7.8），理由：

    1. 角标的用途是"点进去能做什么"。不可复盘的历史条目（自由文本失效条件）到期后
       **没有复盘入口**（``POST /journal/{id}/review`` 直接 422），把它算进"待复盘"
       等于给用户一个永远消不掉的角标（它永远不会变成 ``reviewed``）；
    2. 与前端"待复盘"的操作口径一致：列表里这类条目的按钮区渲染的是
       "无法自动判定、不提供归因"，不是"去复盘"。

    为什么仍是**一条**查询：``due`` 的过滤依赖 jsonb 里的 ``kind``/``value``，在 SQL 里
    重写一遍 :func:`invalidation_observable` 会变成"两套可观测性判据"（历史数据的
    边角形态一定对不齐）。所以同一条聚合里用 ``json_agg`` 把**到期未复盘条目的
    invalidation** 带回来，在 Python 里复用同一个纯函数计数——查询数与条目数无关。
    """
    uid = _to_uuid(user_id)
    today = today or today_sh()
    row = (
        await db.execute(
            select(
                func.count(DecisionJournalEntry.id)
                .filter(
                    DecisionJournalReview.id.is_(None),
                    DecisionJournalEntry.due_on > today,
                )
                .label("open_n"),
                func.count(DecisionJournalReview.id).label("reviewed_n"),
                func.coalesce(
                    func.json_agg(DecisionJournalEntry.invalidation).filter(
                        DecisionJournalReview.id.is_(None),
                        DecisionJournalEntry.due_on <= today,
                    ),
                    text("'[]'::json"),
                ).label("due_invalidations"),
            )
            .select_from(DecisionJournalEntry)
            .outerjoin(
                DecisionJournalReview,
                DecisionJournalReview.entry_id == DecisionJournalEntry.id,
            )
            .where(DecisionJournalEntry.user_id == uid)
        )
    ).one()
    due_n = sum(1 for value in _jsonb_list(row.due_invalidations) if invalidation_observable(value))
    return {"open": int(row.open_n or 0), "due": due_n, "reviewed": int(row.reviewed_n or 0)}


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
    """写入一次复盘（append-only）：不可复盘/未到期/已复盘/结果未就绪都**拒绝**，不静默兜底。

    拒绝顺序是有讲究的：**已复盘（409）优先于可复盘性**——历史状态已经存在时，"这条
    已经评过了"比"它本来就不该被评"更准确；而可复盘性（spec §S9.7.8，422）优先于到期
    判断，因为它是**条目自身的属性**，与"今天几号"无关。
    """
    if verdict not in VERDICTS:
        raise ValueError(f"verdict 越界：{verdict}（只接受 {list(VERDICTS)}）")
    today = (now or datetime.now(_SH)).date()
    row = await get_entry(db, user_id, entry_id, today=today)
    if row.review is not None:
        raise AlreadyReviewed(f"条目 {entry_id} 已复盘，不可再评")
    if not reviewable(row.entry):
        raise NotReviewable(
            f"条目 {entry_id} 不可复盘：失效条件不可观测（历史数据里的形态不是 "
            f"{' / '.join(OBSERVABLE_INVALIDATION_KINDS)}）。该条只展示结果，不接受归因。"
        )
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
    """统计（spec §S9.4 + §S9.7.1/§S9.7.2/§S9.7.5/§S9.7.6/§S9.7.7）：**仍只有两条聚合查询**。

    自洽约束（每一句都被 ``tests/test_journal_*.py`` 钉死）：

    - ``by_verdict`` / ``by_attribution`` 合计 = ``reviewed_n``；
    - ``by_kind`` / ``by_size`` 合计 = ``n``（都数**判断**，spec §S9.7.6/§S9.7.1）；
    - ``avg_excess_pct`` / ``avg_net_excess_pct`` **同时排除 ``kind=pass`` 与
      ``position_size=none``**（spec §S9.7.1/§S9.7.6：「我决定不动」与「只观察」都只有
      判断、没有可归因的收益；把它们算进平均会把"没做"混进"做了"的收益）；
    - ``avg_weighted_excess_pct`` 同样只收 ``pass 之外 + 非 none`` 的样本；
    - ``discipline_break_share`` 在 ``reviewed_n < 5`` 时为 ``null``（不是 0）；
    - 比例一律同时给 Wilson 95% 区间（``share_ci``，``n=0`` 时为 ``None``）；
    - ★ 裁定 2：``share_ci.win`` 的**分母排除 ``pass``**（pass = 不参与市场，
      它的"超额"没有意义；分母 = 已复盘且 ``kind != pass``）；
    - ★ ``calibration`` 的总样本 = "已复盘且填了 ``confidence``"的条数，
      ``< 20`` 时为 ``None`` + ``calibration_note``（不画半条曲线）；
    - ★ 契约 §2 ``consistency``：分子/分母都在**同一条聚合**里数（分母 = 已复盘且
      净超额可算，分子 = :func:`consistency_summary` 的判据），``n < 5`` 时
      ``share`` / ``ci_low`` / ``ci_high`` 为 ``null``（只给 ``n``），区间复用
      :func:`wilson_interval`（不另造一套）。

    为什么两个名字给**同一组计数**：spec 的验收判据是"``by_attribution`` 合计 =
    ``reviewed_n``（算术自洽）"，而前端类型（``JournalStats.by_verdict``）要的是
    "五枚举 → 条数"的纯数字映射（占比由前端用 ``count / reviewed_n`` 自己算）。
    两处都必须是**数值**，不能是 ``{n, share}`` 对象——否则前端把对象当子节点渲染会崩。

    ``stats`` 的**产物版本**是 :data:`JOURNAL_STATS_VERSION`（口径常量一变必须一起 bump）。

    查询设计（反 N+1 的延伸：与用户条数无关，**Phase B2 也没有变成 3 条**）：

    ① 一条聚合拿齐 ``n / reviewed_n`` + ``by_kind`` / ``by_size`` / ``by_verdict`` 计数 +
       ``share_ci.win`` 的分子分母 + 校准每档的 ``(n, hits)`` + 按仓位的 ``sum/count``
       （``FILTER`` 子句比十几次往返便宜，且保持 1 条）；
    ② 一条聚合按 ``kind`` 分组拿 ``count`` / ``sum(excess_pct)`` /
       ``percentile_cont(0.5)`` —— ``by_kind_median`` 与"排除 pass 的收益均值"都从它推导。

    中位数为什么必须单独分组：``percentile_cont`` **不可加**（不能把子组的中位数合成
    总中位数），所以它只能和 ``by_kind`` 的键同层；而 ``by_size`` / 校准 / verdict 分栏
    都是可加的计数，全放进第 ① 条。
    """
    uid = _to_uuid(user_id)
    # 净超额的"赢"判据：excess_pct - 12bp > 0（与 COST_ROUND_TRIP_PCT 同一个常量）
    cost = Decimal(str(COST_ROUND_TRIP_PCT))
    aggregates: list[Any] = [
        func.count(DecisionJournalEntry.id).label("n"),
        func.count(DecisionJournalReview.id).label("reviewed_n"),
        # 判断统计（合计 = n）：归档标签分栏
        *(
            func.count(DecisionJournalEntry.id)
            .filter(DecisionJournalEntry.kind == kind)
            .label(f"kind_{kind}")
            for kind in ENTRY_KINDS
        ),
        # ★ spec §S9.7.1 风险单位分栏（合计 = n；none = 只观察，仍是判断）
        *(
            func.count(DecisionJournalEntry.id)
            .filter(DecisionJournalEntry.position_size == size)
            .label(f"size_{size}")
            for size in POSITION_SIZES
        ),
        # 归因分栏（合计 = reviewed_n）
        *(
            func.count(DecisionJournalReview.id)
            .filter(DecisionJournalReview.verdict == verdict)
            .label(f"verdict_{verdict}")
            for verdict in VERDICTS
        ),
        # ★ spec §S9.7.5 校准：每档"已复盘且填了信心"的条数 + 净超额 > 0 的条数
        *(
            func.count(DecisionJournalReview.id)
            .filter(DecisionJournalEntry.confidence == confidence)
            .label(f"confidence_{confidence}_n")
            for confidence in CONFIDENCE_VALUES
        ),
        *(
            func.count(DecisionJournalReview.id)
            .filter(
                DecisionJournalEntry.confidence == confidence,
                DecisionJournalReview.excess_pct > cost,
            )
            .label(f"confidence_{confidence}_hits")
            for confidence in CONFIDENCE_VALUES
        ),
        # ★ 裁定 2：win 的分子与分母都排除 pass（分母放在同一查询里，免得再做减法）
        func.count(DecisionJournalReview.id)
        .filter(DecisionJournalEntry.kind != "pass")
        .label("win_denominator"),
        func.count(DecisionJournalReview.id)
        .filter(
            DecisionJournalEntry.kind != "pass",
            DecisionJournalReview.excess_pct > cost,
        )
        .label("win_n"),
        # ★ spec §S9.7.1 加权超额：按仓位档的 sum/count（权重在 Python 里乘，
        #   none 的权重是 0 → 不贡献分子也不贡献分母；pass 一律不进收益统计）
        *(
            func.sum(DecisionJournalReview.excess_pct)
            .filter(
                DecisionJournalEntry.position_size == size,
                DecisionJournalEntry.kind != "pass",
            )
            .label(f"size_{size}_excess_sum")
            for size in POSITION_SIZES
        ),
        *(
            func.count(DecisionJournalReview.excess_pct)
            .filter(
                DecisionJournalEntry.position_size == size,
                DecisionJournalEntry.kind != "pass",
            )
            .label(f"size_{size}_excess_n")
            for size in POSITION_SIZES
        ),
        # ★ 契约 §2 归因自洽：分母 = 已复盘**且净超额可算**的条数；分子按
        #   verdict 与净超额符号的一致性判定（判据与 `>` / `<` 在同一条聚合里，
        #   stats 仍然是 2 条查询，不为每条 entry 再查一次）。
        #   净超额 = excess_pct − 12bp，所以"净超额 > 0"就是"excess_pct > 0.12"
        #   （恰在成本线上不算赚，与 share_ci.win 同一口径）。
        func.count(DecisionJournalReview.id)
        .filter(DecisionJournalReview.excess_pct.is_not(None))
        .label("consistency_n"),
        func.count(DecisionJournalReview.id)
        .filter(
            or_(
                # 纪律问题与盈亏独立 → 恒自洽（契约 §2 逐字）
                DecisionJournalReview.verdict.in_(CONSISTENCY_ALWAYS_VERDICTS),
                # 「我错了」必须对应亏钱，才叫前后一致
                and_(
                    DecisionJournalReview.verdict.in_(CONSISTENCY_NEGATIVE_VERDICTS),
                    DecisionJournalReview.excess_pct < cost,
                ),
                # 「我对了」必须对应赚钱（且赚过成本线）
                and_(
                    DecisionJournalReview.verdict.in_(CONSISTENCY_POSITIVE_VERDICTS),
                    DecisionJournalReview.excess_pct > cost,
                ),
            )
        )
        .label("consistency_consistent"),
    ]
    total_row = (
        await db.execute(
            select(*aggregates)
            .select_from(DecisionJournalEntry)
            .outerjoin(
                DecisionJournalReview,
                DecisionJournalReview.entry_id == DecisionJournalEntry.id,
            )
            .where(DecisionJournalEntry.user_id == uid)
        )
    ).one()
    m = total_row._mapping
    n = int(m["n"] or 0)
    reviewed_n = int(m["reviewed_n"] or 0)
    by_kind = {kind: int(m[f"kind_{kind}"] or 0) for kind in ENTRY_KINDS}
    by_size = {size: int(m[f"size_{size}"] or 0) for size in POSITION_SIZES}
    counts: dict[str, int] = {verdict: int(m[f"verdict_{verdict}"] or 0) for verdict in VERDICTS}

    # 加权平均超额：Σ(w·excess) / Σ(w·n)，`none` 权重 0 自动退出分母
    weighted_sum = 0.0
    weighted_n = 0.0
    for size in POSITION_SIZES:
        weight = SIZE_WEIGHTS[size]
        excess_sum = m[f"size_{size}_excess_sum"]
        sample_n = int(m[f"size_{size}_excess_n"] or 0)
        if weight == 0.0 or sample_n == 0 or excess_sum is None:
            continue
        weighted_sum += weight * float(excess_sum)
        weighted_n += weight * sample_n

    grouped = (
        await db.execute(
            select(
                DecisionJournalEntry.kind,
                func.count(DecisionJournalReview.id),
                # ★ 中位毛超额（不可加，必须在本条分组里算）
                func.percentile_cont(0.5).within_group(DecisionJournalReview.excess_pct),
                # ★ 判断口径的样本：只排除 `pass`（它没有方向）。放在同一条分组查询里，
                #   stats 仍然是两条聚合（spec 契约 §6）。
                #   为什么**不**在这里排除 `position_size=none`：只观察的条目依然是
                #   "一个可被验证的判断"，把它踢出判断均值等于只奖励真金白银下过单的人；
                #   `none` 只在**加权收益**口径里因权重 0 而退出（见 SIZE_WEIGHTS）。
                func.count(DecisionJournalReview.id).label("revenue_n"),
                func.sum(DecisionJournalReview.excess_pct).label("revenue_sum"),
            )
            .join(DecisionJournalEntry, DecisionJournalEntry.id == DecisionJournalReview.entry_id)
            .where(DecisionJournalEntry.user_id == uid)
            .group_by(DecisionJournalEntry.kind)
        )
    ).all()
    # ★ spec §S9.7.6：每类的中位净超额（"pass 也有一栏"——它是判断统计的一部分；
    #   但**总体的**收益均值仍然排除 pass 与 none，见下面 revenue_n）
    by_kind_median: dict[str, dict[str, Any]] = {
        kind: {"count": 0, "median_net_excess_pct": None} for kind in ENTRY_KINDS
    }
    #: 判断口径的样本：只排除 ``pass``（我决定不动，没有方向可判）。
    #: ``position_size=none``（只观察）**仍计入**判断均值——它是判断而非交易；
    #: 只有"按仓位加权的收益均值"（``avg_weighted_excess_pct``）因权重 0 排除它。
    revenue_n = 0
    revenue_sum = 0.0
    for kind, count, median, kind_revenue_n, kind_revenue_sum in grouped:
        key = str(kind)
        group_n = int(count or 0)
        if key in by_kind_median:
            by_kind_median[key]["count"] = group_n
            if median is not None:
                # 中位净超额 = 中位毛超额 − 12bp（成本是常数，减在最后一步）
                by_kind_median[key]["median_net_excess_pct"] = round(
                    float(median) - COST_ROUND_TRIP_PCT, 4
                )
        if key == "pass":
            continue
        revenue_n += int(kind_revenue_n or 0)
        if kind_revenue_sum is not None:
            revenue_sum += float(kind_revenue_sum)

    calibration, calibration_note = calibration_summary(
        {
            confidence: (
                int(m[f"confidence_{confidence}_n"] or 0),
                int(m[f"confidence_{confidence}_hits"] or 0),
            )
            for confidence in CONFIDENCE_VALUES
        }
    )

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
        # ★ spec §S9.7.6：中位**净**超额（新增字段，既有的 by_kind 仍是纯条数）
        "by_kind_median": by_kind_median,
        # ★ spec §S9.7.1：风险单位分栏 + 加权超额（none 排除）
        "by_size": by_size,
        "avg_weighted_excess_pct": (
            None if weighted_n == 0.0 else round(weighted_sum / weighted_n, 4)
        ),
        # ★ spec §S9.7.5：校准曲线（总样本 < 20 → None + 原因）
        "calibration": calibration,
        "calibration_note": calibration_note,
        # ★ spec §S9.7.7：比例必须带区间（n=0 → None，不是 [0,1]）
        "share_ci": {
            "discipline_break": wilson_interval(counts["discipline_break"], reviewed_n),
            # 裁定 2：分母排除 pass（全是 pass 时 0/0 → None，而不是 100%）
            "win": wilson_interval(int(m["win_n"] or 0), int(m["win_denominator"] or 0)),
        },
        # ★ 契约 §2 归因自洽率（n < 5 → 只给 n，比例与区间都是 None）
        "consistency": consistency_summary(
            int(m["consistency_consistent"] or 0), int(m["consistency_n"] or 0)
        ),
        "net_cost_bps": COST_ROUND_TRIP_BPS,
        "multiple_comparison_note": MULTIPLE_COMPARISON_NOTE,
        # ★ spec §S9.7.1/§S9.7.6 收益统计的排除口径（前端原样展示，不改写）
        "exclusion_note": EXCLUSION_NOTE,
        # ★ spec §S9.7.9：前端直接拿它做「导出我的日志」，不自己拼路径
        "export_hint": EXPORT_HINT,
    }


# ── §S9.7.9 导出（只含本人：entries + snapshot + review） ────────────────────


async def export_rows(db: AsyncSession, user_id: uuid.UUID | str) -> list[JournalRow]:
    """导出用的读面：**本人全部**条目（不分页、不截断），一次查询 join 复盘。

    与 :func:`list_entries` 的差别只有一个：没有 ``LIST_LIMIT``。"导出"是用户对自己
    数据的完整取回（spec §S9.7.9），被列表页的显示上限截断就不再是导出。
    排序用 ``created_at`` **升序**（导出是给人按时间读的档案，最新在最后）。
    """
    uid = _to_uuid(user_id)
    today = today_sh()
    rows = (
        await db.execute(
            select(DecisionJournalEntry, DecisionJournalReview)
            .outerjoin(
                DecisionJournalReview, DecisionJournalReview.entry_id == DecisionJournalEntry.id
            )
            .where(DecisionJournalEntry.user_id == uid)
            .order_by(DecisionJournalEntry.created_at.asc(), DecisionJournalEntry.id.asc())
        )
    ).all()
    return [
        JournalRow(
            entry=entry, review=review, status=entry_status(entry, review is not None, today)
        )
        for entry, review in rows
    ]


def export_entry(row: JournalRow) -> dict[str, Any]:
    """一条判断的导出形状（JSON 与 CSV **共用**同一份投影，口径不会分叉）。

    包含三块：条目本身的不可变字段、**创建时的快照**（原样，不裁剪）、复盘（若已复盘）。
    ``net_excess_pct`` 在复盘里现算（复盘表只存毛超额；净 = 毛 − 12bp，与 evaluation 同式）。
    """
    entry = row.entry
    review = row.review
    return {
        "id": int(entry.id),
        "symbol": entry.symbol,
        "kind": entry.kind,
        "bet_type": entry.bet_type or "direction",
        "position_size": entry.position_size or "none",
        "confidence": entry.confidence,
        "thesis": entry.thesis,
        "invalidation": entry.invalidation or {},
        "horizon_days": entry.horizon_days,
        "created_at": entry.created_at.isoformat(),
        "due_on": entry.due_on.isoformat(),
        "status": row.status,
        "snapshot": entry.snapshot or {},
        "review": (
            None
            if review is None
            else {
                "id": int(review.id),
                "entry_id": int(review.entry_id),
                "reviewed_at": review.reviewed_at.isoformat(),
                "outcome_pct": float(review.outcome_pct),
                "bench_pct": float(review.bench_pct),
                "excess_pct": float(review.excess_pct),
                "net_excess_pct": round(float(review.excess_pct) - COST_ROUND_TRIP_PCT, 4),
                "invalidation_hit": bool(review.invalidation_hit),
                "verdict": review.verdict,
                "note": review.note,
            }
        ),
    }


def export_json(rows: Sequence[JournalRow], *, now: datetime | None = None) -> str:
    """导出为 JSON（自带 ``exported_at`` / ``count`` 元数据，条目顺序即入参顺序）。"""
    payload = {
        "format": "decision_journal.export.v1",
        "exported_at": (now or datetime.now(_SH)).isoformat(),
        "count": len(rows),
        "entries": [export_entry(row) for row in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _csv_number(value: Any) -> str:
    """小数统一 4 位（与库里的 ``Numeric(10,4)`` 同精度）；缺失是空串，不是 0。"""
    if value is None:
        return ""
    return f"{float(value):.4f}"


def _csv_row(item: dict[str, Any]) -> list[str]:
    """按 :data:`EXPORT_CSV_COLUMNS` 的**固定列序**取值（位置即契约，不按表头猜）。"""
    review = item["review"] or {}
    return [
        str(item["id"]),
        item["symbol"] or "",
        item["kind"],
        item["bet_type"],
        item["position_size"],
        "" if item["confidence"] is None else str(item["confidence"]),
        item["thesis"],
        # 失效条件是 jsonb：导出成一个字面 JSON 串（csv 模块负责转义引号/逗号/换行）
        json.dumps(item["invalidation"], ensure_ascii=False, sort_keys=True),
        str(item["horizon_days"]),
        item["created_at"],
        item["due_on"],
        item["status"],
        _csv_number(review.get("outcome_pct")),
        _csv_number(review.get("bench_pct")),
        _csv_number(review.get("excess_pct")),
        _csv_number(review.get("net_excess_pct")),
        str(review.get("verdict") or ""),
    ]


def export_csv(rows: Sequence[JournalRow]) -> bytes:
    """导出为 CSV（**UTF-8 BOM** + 固定列序，spec §S9.7.9）。

    为什么带 BOM：Excel 打开不带 BOM 的 UTF-8 会把中文 ``thesis`` 显示成乱码，而"导出
    我的日志"的第一使用场景就是 Excel。BOM 是**字节层**的前缀（``\ufeff`` 编成 UTF-8），
    不是表头的一部分 —— 解析方按 ``utf-8-sig`` 读即可。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_CSV_COLUMNS)
    for row in rows:
        writer.writerow(_csv_row(export_entry(row)))
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
