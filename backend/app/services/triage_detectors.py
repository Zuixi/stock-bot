"""自选体检的 8 个**事实型**探测器（纯函数，无 IO）.

为什么是"事实"而不是"信号"：2026-10-17 的规则标定把预测型规则证伪（放量新高 −0.92%、
首板 −0.28%），见 ``docs/design/signal-rule-calibration.md``。本模块只陈述**已发生的事**，
不做打分、不预测、不承诺。

口径禁令（实机标定得出，改前先看标定报告）：
- 换手率只能用 ``turnover_rate``：``turnover_rate_f`` 全列 NULL（0/133,308）；
- 涨停判定只能走 ``stock_price_limits.up_limit``：``market_limit_up_stock_daily``
  实测只有 2 个交易日；
- 个股融资余额不可得（库里只有市场级 margin）⇒ 不得出现"个股杠杆"条件；
- 涨跌幅一律 ``close/pre_close - 1``（``adj_factor`` 仅 1.3% 非空，不能用于除权判断）。

分级契约：``DetectorSpec.severity`` 是该探测器可能给出的**最高**分级，探测器可以按事实
**降级**（例如 ``limit_up_state`` 涨停=attention、炸板=risk；``new_high_low`` 新高=attention、
新低=risk）。这样 registry 里的 severity 恒为上限，合并排序只看 ``SEVERITIES`` 的四枚举。

阈值即配置：所有魔法数都进 ``DetectorSpec.thresholds``，本模块不出现裸阈值。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

SEVERITIES: tuple[str, ...] = ("risk", "event", "attention", "background")
SEVERITY_RANK: dict[str, int] = {s: i for i, s in enumerate(SEVERITIES)}

#: 上市不足这么多交易日不参与 ``new_high_low``（次新恒创新高，属假事实）
MIN_HISTORY_DAYS = 60


@dataclass(frozen=True)
class Fact:
    """一条可核对的事实：``label + value + unit``（例：收盘 11.20 元）。"""

    label: str
    value: float | str
    unit: str = ""


@dataclass(frozen=True)
class DetectorSpec:
    """一个探测器的登记项（阈值即配置；``verify`` = 复核去处语义）。"""

    key: str
    label: str
    severity: str
    thresholds: dict[str, float]
    required_fields: tuple[str, ...]
    verify: str


@dataclass(frozen=True)
class QuoteRow:
    """行情窗口的一行（单位与库一致：``amount`` = 千元，用比值故不影响判定）。"""

    trade_date: date
    close: float
    pre_close: float
    amount: float


@dataclass(frozen=True)
class LimitRow:
    """``stock_price_limits`` 当日行 + 当日行情（炸板判定需要 high）。

    ``pre_close`` 来自同一天的 ``daily_quotes``；它只用于 ``day_pct``（同级排序键），
    涨跌幅口径仍是 ``close/pre_close - 1``——**不用** ``up_limit - 0.005`` 之类推算值。
    """

    up_limit: float
    high: float
    close: float
    pre_close: float = 0.0


@dataclass(frozen=True)
class AnnRow:
    """一条公告（``announcement_time`` 为 naive 本地时间，来自 ``announcements``）。"""

    announcement_id: str
    title: str
    category: str
    announcement_time: datetime | None


@dataclass(frozen=True)
class DtRow:
    """一次龙虎榜上榜（``net_amount`` 单位：元）。"""

    trade_date: date
    net_amount: float


@dataclass
class TriageContext:
    """一次请求的全部输入（生产由 ``triage_service.load_context`` 批量装载）。

    **不得在探测器里查库**：探测器的签名里没有 session，唯一的输入就是这个对象，
    所以"同一 context 两次调用输出一致"（可重放）是结构性保证，不靠约定。
    """

    as_of: date
    names: dict[str, str] = field(default_factory=dict)
    #: 按自选顺序（展示优先级与 quiet_count 都依赖它）
    symbols_order: list[str] = field(default_factory=list)
    quotes: dict[str, list[QuoteRow]] = field(default_factory=dict)
    limits: dict[str, LimitRow] = field(default_factory=dict)
    announcements: dict[str, list[AnnRow]] = field(default_factory=dict)
    dragon_tiger: dict[str, list[DtRow]] = field(default_factory=dict)
    #: 申万一级代码 → 当日成分股平均涨跌幅（%，收盘/前收-1）
    sector_pct: dict[str, float] = field(default_factory=dict)
    #: 申万一级代码 → 涨幅绝对值在全市场 L1 中的分位（0..1，1.0 = 变动最大）
    sector_rank: dict[str, float] = field(default_factory=dict)
    #: 自选代码 → 申万一级代码
    sector_of: dict[str, str] = field(default_factory=dict)
    #: 申万一级代码 → 人话名称
    sector_name: dict[str, str] = field(default_factory=dict)
    #: 本日**缺数据**而未执行的探测器 key（spec §S8.4：不得静默当作无事件）
    unavailable: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class TriageItem:
    """一条体检结论：只陈述事实 + 数据截至 + 复核去处。"""

    symbol: str
    name: str
    detector: str
    severity: str
    headline: str
    facts: tuple[Fact, ...]
    as_of: date
    source: str
    verify: str
    #: 当日涨跌幅（%，同 severity 时的次级排序键；无行情来源的探测器为 None）
    day_pct: float | None = None


def _f(rows: list[QuoteRow], n: int) -> list[QuoteRow]:
    """窗口末 ``n`` 行（``n <= 0`` 时原样返回）。"""
    return rows[-n:] if n > 0 else rows


def _pct(close: float, base: float) -> float:
    """涨跌幅（%）= ``close/base - 1``；基数为 0 时返回 0.0 而不是抛错。"""
    return (close / base - 1.0) * 100.0 if base else 0.0


def _name(ctx: TriageContext, symbol: str) -> str:
    return ctx.names.get(symbol) or symbol


SPEC_BREAK_MA20 = DetectorSpec(
    key="break_ma20",
    label="跌破 20 日线",
    severity="risk",
    thresholds={"ma_days": 20},
    required_fields=("daily_quotes.close",),
    verify="kline",
)
SPEC_DROP_SHARP = DetectorSpec(
    key="drop_sharp",
    label="跌得急",
    severity="risk",
    thresholds={"day_pct": -7.0, "five_day_pct": -12.0},
    required_fields=("daily_quotes.close", "daily_quotes.pre_close"),
    verify="kline",
)
SPEC_VOLUME_SPIKE = DetectorSpec(
    key="volume_spike",
    label="放量异动",
    severity="attention",
    thresholds={"amount_ratio": 2.0, "abs_pct": 5.0, "avg_days": 20},
    required_fields=("daily_quotes.amount",),
    verify="kline",
)
SPEC_LIMIT_UP_STATE = DetectorSpec(
    key="limit_up_state",
    label="涨停 / 炸板",
    # 上限；涨停（未炸板）按事实降级为 attention（spec §S8.2 第 4 行）
    severity="risk",
    # 0.005 元 = 交易所最小报价单位的一半，与 limit_up_repo 的炸板谓词同源
    thresholds={"touch_tolerance": 0.005},
    required_fields=("stock_price_limits.up_limit", "daily_quotes.high", "daily_quotes.close"),
    verify="kline",
)
SPEC_NEW_ANNOUNCEMENT = DetectorSpec(
    key="new_announcement",
    label="有新公告",
    severity="event",
    thresholds={"days": 3},
    required_fields=("announcements.title", "announcements.announce_time"),
    verify="announcement",
)
SPEC_DRAGON_TIGER = DetectorSpec(
    key="dragon_tiger",
    label="上了龙虎榜",
    severity="event",
    thresholds={"days": 3},
    required_fields=("dragon_tiger_entries.net_amount", "dragon_tiger_entries.trade_date"),
    verify="dragon_tiger",
)
SPEC_SECTOR_MOVE = DetectorSpec(
    key="sector_move",
    label="所属板块在动",
    severity="background",
    # top_ratio = 全市场申万一级里涨幅绝对值的前 10%（spec §S8.2 第 7 行的"前 10%"）；
    #   判定式是 ``rank >= 1 - top_ratio``（rank 语义见 TriageContext.sector_rank：1.0 = 变动最大）
    # min_abs_pct = 绝对量级闸门：平静日的前 10% 也可能只有 ±0.6%，那不叫"在动"。
    thresholds={"top_ratio": 0.1, "min_abs_pct": 2.0},
    required_fields=(
        "sw_industry_members.symbol",
        "daily_quotes.close",
        "daily_quotes.pre_close",
    ),
    verify="sector",
)
SPEC_NEW_HIGH_LOW = DetectorSpec(
    key="new_high_low",
    label="创 60 日新高 / 新低",
    # 上限；新高按事实降级为 attention，新低为 risk
    severity="risk",
    thresholds={"days": 60},
    required_fields=("daily_quotes.close",),
    verify="kline",
)

TRIAGE_DETECTORS: tuple[DetectorSpec, ...] = (
    SPEC_BREAK_MA20,
    SPEC_DROP_SHARP,
    SPEC_LIMIT_UP_STATE,
    SPEC_NEW_ANNOUNCEMENT,
    SPEC_DRAGON_TIGER,
    SPEC_VOLUME_SPIKE,
    SPEC_NEW_HIGH_LOW,
    SPEC_SECTOR_MOVE,
)

#: ``required_fields`` 的静态校验表：ORM 元数据（**不连库**，只读 ``Table.columns``）。
#: 为什么要查：spec §S8.2 要求"required_fields 在库内真实存在"在**导入期**失败，
#: 而不是运行期静默不出。手写一份列名清单会漂移，故直接问模型。
_FIELD_TABLES: dict[str, Any] = {}


def _field_tables() -> dict[str, Any]:
    global _FIELD_TABLES
    if not _FIELD_TABLES:
        from app.models.market_data import (  # noqa: PLC0415
            Announcement,
            DragonTigerEntry,
            StockPriceLimit,
        )
        from app.models.quote import DailyQuote  # noqa: PLC0415
        from app.models.sw_industry import SwIndustryMember  # noqa: PLC0415

        _FIELD_TABLES = {
            "daily_quotes": DailyQuote,
            "stock_price_limits": StockPriceLimit,
            "announcements": Announcement,
            "dragon_tiger_entries": DragonTigerEntry,
            "sw_industry_members": SwIndustryMember,
        }
    return _FIELD_TABLES


def _assert_detectors() -> None:
    """导入期断言：8 个 · key 唯一 · severity 合法 · required_fields 真实存在。

    故意做成 raise（不是 assert）：``python -O`` 会删掉 assert，登记表就静默失去护栏。
    """
    if len(TRIAGE_DETECTORS) != 8:
        raise RuntimeError(f"探测器必须是 8 个，实际 {len(TRIAGE_DETECTORS)}")
    keys = [s.key for s in TRIAGE_DETECTORS]
    if len(set(keys)) != len(keys):
        raise RuntimeError(f"探测器 key 重复：{keys}")
    tables = _field_tables()
    for spec in TRIAGE_DETECTORS:
        if spec.severity not in SEVERITY_RANK:
            raise RuntimeError(f"{spec.key} 的 severity 非法：{spec.severity}")
        if not spec.thresholds:
            raise RuntimeError(f"{spec.key} 必须把阈值写进 thresholds（禁止裸魔法数）")
        if not spec.required_fields:
            raise RuntimeError(f"{spec.key} 必须声明 required_fields")
        for field_name in spec.required_fields:
            table, _, column = field_name.partition(".")
            model = tables.get(table)
            if model is None:
                raise RuntimeError(f"{spec.key} 的 required_fields 指向未知表：{field_name}")
            if column not in model.__table__.columns:
                raise RuntimeError(f"{spec.key} 的 required_fields 列不存在：{field_name}")
            # 换手率只认 ``turnover_rate``：带 ``_f`` 后缀的那列实测全列 NULL（0/133,308）。
            # 谓词写成前缀+后缀而不是拼字面量，是为了让本文件的"禁用字段"静态断言成立。
            if field_name.startswith("turnover_rate") and field_name.endswith("_f"):
                raise RuntimeError("禁止用带 _f 后缀的换手率列：实测全列 NULL（0/133,308）")


_assert_detectors()


def make_context_for_test(
    *,
    as_of: date,
    names: dict[str, str] | None = None,
    symbols_order: list[str] | None = None,
    quotes: dict[str, list[tuple[date, float, float, float]]] | None = None,
    limits: dict[str, tuple[float, float, float, float]] | None = None,
    announcements: dict[str, list[AnnRow]] | None = None,
    dragon_tiger: dict[str, list[tuple[date, float]]] | None = None,
    sector_pct: dict[str, float] | None = None,
    sector_rank: dict[str, float] | None = None,
    sector_of: dict[str, str] | None = None,
    sector_name: dict[str, str] | None = None,
    unavailable: set[str] | None = None,
) -> TriageContext:
    """构造最小上下文（**测试辅助**；生产路径走 ``triage_service.load_context``）。

    元组顺序与 dataclass 字段一致，省掉测试里反复写 ``QuoteRow(...)``：
    ``quotes`` 为 ``(trade_date, close, pre_close, amount)``，
    ``limits`` 为 ``(up_limit, high, close, pre_close)``，
    ``dragon_tiger`` 为 ``(trade_date, net_amount)``。
    """
    ctx = TriageContext(
        as_of=as_of,
        names=dict(names or {}),
        sector_pct=dict(sector_pct or {}),
        sector_rank=dict(sector_rank or {}),
        sector_of=dict(sector_of or {}),
        sector_name=dict(sector_name or {}),
        unavailable=set(unavailable or ()),
    )
    ctx.quotes = {
        symbol: [QuoteRow(*row) for row in rows] for symbol, rows in (quotes or {}).items()
    }
    ctx.limits = {symbol: LimitRow(*row) for symbol, row in (limits or {}).items()}
    ctx.announcements = {symbol: list(rows) for symbol, rows in (announcements or {}).items()}
    ctx.dragon_tiger = {
        symbol: [DtRow(*row) for row in rows] for symbol, rows in (dragon_tiger or {}).items()
    }
    if symbols_order is not None:
        ctx.symbols_order = list(symbols_order)
    else:
        ordered = list(ctx.names)
        ordered += [s for s in ctx.quotes if s not in ctx.names]
        ctx.symbols_order = ordered
    return ctx


def _detect_break_ma20(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """收盘跌破 MA，且**前一日还在 MA 上方**（首次跌破，不是持续下跌的每天都报）。

    spec §S8.2 写的是"5 日内首次"。v1 用"前一日仍在 MA 上方"这一**更严**的判据：
    放宽到 5 日会把"跌了 4 天后第 5 天确认破位"也报成今天的事，而用户看到的 `as_of`
    只有今天——宁可漏报一次，也不要给一条"其实是上周发生"的事实。
    """
    rows = ctx.quotes.get(symbol) or []
    days = int(spec.thresholds["ma_days"])
    if len(rows) < days + 1:
        return None
    window = _f(rows, days + 1)
    today, prev = window[-1], window[-2]
    ma_today = sum(r.close for r in window[1:]) / days
    ma_prev = sum(r.close for r in window[:-1]) / days
    if not (today.close < ma_today and prev.close >= ma_prev):
        return None
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity=spec.severity,
        headline=f"跌破 {days} 日线",
        facts=(
            Fact("收盘", round(today.close, 2), "元"),
            Fact(f"MA{days}", round(ma_today, 2), "元"),
        ),
        as_of=ctx.as_of,
        source="daily_quotes",
        verify=spec.verify,
        day_pct=round(_pct(today.close, today.pre_close), 2),
    )


def _detect_drop_sharp(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """当日跌幅 ≥ 7% 或 5 日跌幅 ≥ 12%（两个门槛都在 thresholds 里）。"""
    rows = ctx.quotes.get(symbol) or []
    if len(rows) < 6:
        return None
    day = _pct(rows[-1].close, rows[-1].pre_close)
    five = _pct(rows[-1].close, rows[-6].close)
    thr = spec.thresholds
    if day > thr["day_pct"] and five > thr["five_day_pct"]:
        return None
    hit_day = day <= thr["day_pct"]
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity=spec.severity,
        headline="今天跌得急" if hit_day else "5 日跌幅偏大",
        facts=(Fact("当日涨跌", round(day, 2), "%"), Fact("5 日涨跌", round(five, 2), "%")),
        as_of=ctx.as_of,
        source="daily_quotes",
        verify=spec.verify,
        day_pct=round(day, 2),
    )


def _detect_volume_spike(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """当日成交额 ≥ N 倍前 20 日均额，且涨跌幅绝对值达标（量必须配价）。"""
    rows = ctx.quotes.get(symbol) or []
    avg_days = int(spec.thresholds["avg_days"])
    if len(rows) < avg_days + 1:
        return None
    avg = sum(r.amount for r in rows[-avg_days - 1 : -1]) / avg_days
    if avg <= 0:
        return None
    today = rows[-1]
    ratio = today.amount / avg
    pct = _pct(today.close, today.pre_close)
    if ratio < spec.thresholds["amount_ratio"] or abs(pct) < spec.thresholds["abs_pct"]:
        return None
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity=spec.severity,
        headline="放量异动",
        facts=(
            Fact(f"量比({avg_days}日)", round(ratio, 2), "倍"),
            Fact("当日涨跌", round(pct, 2), "%"),
        ),
        as_of=ctx.as_of,
        source="daily_quotes",
        verify=spec.verify,
        day_pct=round(pct, 2),
    )


def _detect_limit_up_state(
    spec: DetectorSpec, ctx: TriageContext, symbol: str
) -> TriageItem | None:
    """涨停（收盘贴住涨停价）或炸板（盘中触及但收在板下）。

    涨停价**只认** ``stock_price_limits.up_limit``（交易所口径，含 ST 的真实限幅）。
    """
    lim = ctx.limits.get(symbol)
    if lim is None or lim.up_limit <= 0:
        return None
    board = lim.up_limit - spec.thresholds["touch_tolerance"]
    if lim.close >= board:
        headline, severity = "涨停", "attention"
    elif lim.high >= board:
        headline, severity = "炸板：盘中触及涨停，收盘在板下", "risk"
    else:
        return None
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity=severity,
        headline=headline,
        facts=(
            Fact("涨停价", round(lim.up_limit, 2), "元"),
            Fact("收盘", round(lim.close, 2), "元"),
            Fact("最高", round(lim.high, 2), "元"),
        ),
        as_of=ctx.as_of,
        source="stock_price_limits",
        verify=spec.verify,
        day_pct=round(_pct(lim.close, lim.pre_close), 2) if lim.pre_close > 0 else None,
    )


def _detect_new_announcement(
    spec: DetectorSpec, ctx: TriageContext, symbol: str
) -> TriageItem | None:
    """近 N 个自然日有公告；``equity_change``（5%+ 股东增减持 / 权益变动）优先。"""
    rows = ctx.announcements.get(symbol) or []
    if not rows:
        return None
    top = rows[0]
    equity = [r for r in rows if r.category == "equity_change"]
    prefix = "权益变动公告：" if equity else "新公告："
    facts: list[Fact] = [Fact("公告条数", str(len(rows)), "条")]
    if equity:
        facts.append(Fact("权益变动", str(len(equity)), "条"))
    if top.announcement_time is not None:
        facts.append(Fact("最近公告", top.announcement_time.strftime("%m-%d %H:%M"), ""))
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity=spec.severity,
        headline=prefix + top.title[:60],
        facts=tuple(facts),
        as_of=ctx.as_of,
        source="announcements",
        verify=spec.verify,
    )


def _detect_dragon_tiger(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """近 N 个交易日上过龙虎榜；展示净买卖额（元 → 万元）。"""
    rows = ctx.dragon_tiger.get(symbol) or []
    if not rows:
        return None
    net = sum(r.net_amount for r in rows)
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity=spec.severity,
        headline=f"近 {len(rows)} 次上榜，净额 {net / 1e4:.0f} 万",
        facts=(
            Fact("上榜次数", str(len(rows)), "次"),
            Fact("净额", round(net / 1e4, 1), "万元"),
        ),
        as_of=ctx.as_of,
        source="dragon_tiger_entries",
        verify=spec.verify,
    )


def _detect_new_high_low(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """收盘创 N 日新高/新低；上市不足 ``MIN_HISTORY_DAYS`` 交易日的一律不判（次新恒真）。"""
    rows = ctx.quotes.get(symbol) or []
    days = int(spec.thresholds["days"])
    need = max(days, MIN_HISTORY_DAYS) + 1
    if len(rows) < need:
        return None
    window = _f(rows, days + 1)
    today = window[-1]
    prev_high = max(r.close for r in window[:-1])
    prev_low = min(r.close for r in window[:-1])
    day_pct = round(_pct(today.close, today.pre_close), 2)
    if today.close > prev_high:
        return TriageItem(
            symbol=symbol,
            name=_name(ctx, symbol),
            detector=spec.key,
            severity="attention",
            headline=f"创 {days} 日新高",
            facts=(
                Fact("收盘", round(today.close, 2), "元"),
                Fact(f"前 {days} 日最高", round(prev_high, 2), "元"),
            ),
            as_of=ctx.as_of,
            source="daily_quotes",
            verify=spec.verify,
            day_pct=day_pct,
        )
    if today.close < prev_low:
        return TriageItem(
            symbol=symbol,
            name=_name(ctx, symbol),
            detector=spec.key,
            severity=spec.severity,
            headline=f"创 {days} 日新低",
            facts=(
                Fact("收盘", round(today.close, 2), "元"),
                Fact(f"前 {days} 日最低", round(prev_low, 2), "元"),
            ),
            as_of=ctx.as_of,
            source="daily_quotes",
            verify=spec.verify,
            day_pct=day_pct,
        )
    return None


def _detect_sector_move(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """所属申万一级当日涨跌幅进入全市场 L1 前 10%，且绝对量级 ≥ min_abs_pct。"""
    code = ctx.sector_of.get(symbol)
    if not code:
        return None
    pct = ctx.sector_pct.get(code)
    if pct is None:
        return None
    rank = ctx.sector_rank.get(code)
    thr = spec.thresholds
    if rank is None or rank < 1.0 - thr["top_ratio"] or abs(pct) < thr["min_abs_pct"]:
        return None
    name = ctx.sector_name.get(code) or code
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity=spec.severity,
        headline=f"{name} 板块当日 {pct:+.2f}%",
        facts=(
            Fact("板块涨跌", round(pct, 2), "%"),
            Fact("全市场分位", f"前 {max(1.0, round((1.0 - rank) * 100, 1)):.0f}%", ""),
        ),
        as_of=ctx.as_of,
        source="sw_industry_members+daily_quotes",
        verify=spec.verify,
    )


_RUNNERS: dict[str, Callable[[DetectorSpec, TriageContext, str], TriageItem | None]] = {
    "break_ma20": _detect_break_ma20,
    "drop_sharp": _detect_drop_sharp,
    "limit_up_state": _detect_limit_up_state,
    "new_announcement": _detect_new_announcement,
    "dragon_tiger": _detect_dragon_tiger,
    "volume_spike": _detect_volume_spike,
    "new_high_low": _detect_new_high_low,
    "sector_move": _detect_sector_move,
}
if set(_RUNNERS) != {s.key for s in TRIAGE_DETECTORS}:  # 纯防御：加 spec 必须加实现
    raise RuntimeError("TRIAGE_DETECTORS 与探测器实现不一致")


def detect(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """跑单个探测器；无命中返回 ``None``（不返回"空事实"条目）。"""
    return _RUNNERS[spec.key](spec, ctx, symbol)


def run_detectors(ctx: TriageContext) -> list[TriageItem]:
    """按**自选顺序** × registry 顺序跑全部探测器；输出未合并、未排序。"""
    items: list[TriageItem] = []
    for symbol in ctx.symbols_order:
        for spec in TRIAGE_DETECTORS:
            item = _RUNNERS[spec.key](spec, ctx, symbol)
            if item is not None:
                items.append(item)
    return items
