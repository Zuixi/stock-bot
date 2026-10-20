"""监管异动雷达：读时计算（零新表）+ Redis 短缓存.

为什么读时算：`daily_quotes` 本身就是历史，异动 = 对历史窗口的确定性投影；落一张
`regulatory_anomaly_daily` 只会多一份需要自愈的派生数据（S8 已用同一取舍）。

候选集 = 今日涨停/跌停 ∪ 连板梯队（10 日内 ≥2 板或昨日涨停）∪ 3 日偏离 ≥ 阈值 80%
的股票。为什么必须带梯队：`600825` 这类停牌复牌股今天只涨 2.77%、3 日窗口因停牌
缺行而算不出来，但它仍是「15 天 11 板」的高位股，明日再涨 7.8% 就会触发。
候选股的 35 日序列单独再查一次，用于 10/30 日累计与「10 日 N 次」事件计数。
换手率口径**不进候选集**：它是与价格口径并列的独立条件，走 `turnover_only[]` 旁路
（近 3 日日均换手 ≥ 前 5 日 30 倍且累计 ≥ 20%），页面单独一行展示。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from app.core.database import async_session_factory
from app.repositories import regulatory_repo
from app.services import market_day_service
from app.services import regulatory_anomaly as calc

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

logger = logging.getLogger(__name__)

#: 候选股 10/30 日累计与事件计数需要的最长窗口。
LOOKBACK_DAYS = 35
#: 全市场扫描只取最近 13 个交易日（10 日历史 + 2 天窗口 + 1 天容错）。
RECENT_DAYS = 13
#: 异动历史默认回看天数。
HISTORY_DAYS = 10
#: 单日历史最多返回的条数（防止极端行情把响应撑爆，页面仍能正常渲染）。
HISTORY_CAP = 80
CACHE_TTL = 120
#: 交易所最小报价单位的一半：与 `limit_up_repo` 的涨停/炸板谓词同源。
TOUCH_TOLERANCE = 0.005

_CACHE_PREFIX = "market:regulatory:radar:v2"

#: `?date=` 只允许最近这么多个交易日（公开接口的最小成本闸门：限窗后键空间有上界）。
DATE_WINDOW_TRADING_DAYS = 250
_DATE_WINDOW_CACHE_KEY = "market:regulatory:min-date:v1"
_DATE_WINDOW_CACHE_TTL = 3600

CAVEATS: tuple[str, ...] = (
    "偏离值 = 个股日涨跌幅 − 基准指数日涨跌幅；累计 = 窗口内逐日偏离值求和，不是复利收益率。",
    "ST 判定取当前证券名称快照，历史回放可能与当日风险警示状态不一致（名称会摘帽/戴帽）。",
    "价格口径为主；换手率口径只做「仅换手率达标」旁路：连续 3 日日均换手率 ≥ 前 5 日的 "
    "30 倍且 3 日累计 ≥ 20%。换手率数据未更新到 as_of 时该旁路为空"
    "（turnover_available=false），不代表没有换手率异动。",
    "明日触发价假设基准指数明日不涨不跌；基准实际涨跌会等额移动触发价。",
    "停牌日不参与窗口；3 日窗口内缺任何一天行情则不判定 3 日偏离（不拿停牌前旧价格硬算），"
    "但「明日触发价」仍可用最近 2 个有行情的交易日计算。",
    "北交所按「连续 3 个有成交交易日」口径；沪深按连续 3 个交易日口径。",
    "连板数与「N 天 M 板」来自 market_limit_up_stock_daily（按日更新）；历史日期缺行时显示 --，"
    "梯队补位也只在该表有行时生效。",
    "历史触发日按**今日**板块/ST 阈值筛选（没有历史名称表）；摘帽/戴帽前后，历史触发日的"
    "成员可能有偏差。",
)


def _rule_payload() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for board, rule in calc.RULES.items():
        exchanges = [ex for (ex, b), _ in calc.BENCHMARKS.items() if b == board]
        for ex in exchanges:
            code, label = calc.BENCHMARKS[(ex, board)]
            out.append(
                {
                    "board": board,
                    "exchange": ex,
                    "threshold_pct": rule.threshold_pct,
                    "st_threshold_pct": rule.st_threshold_pct,
                    "price_limit_pct": rule.price_limit_pct,
                    "st_price_limit_pct": rule.st_price_limit_pct,
                    "benchmark_code": code,
                    "benchmark_name": label,
                }
            )
    return out


def _stale_days(as_of: date | None) -> int | None:
    """自然日陈旧度 ``今天(上海) - as_of``；无判据日 → ``None``。

    与 `market_data_service._stale_days` 同一口径（全站 `FreshnessNote` 的契约）。
    **派生量不是缓存内容**：跨零点后会冻结成旧值，故缓存命中路径必须按**当下**重算——
    与 `as_of_quality` 同一处理纪律（见 `get_radar`）。
    """
    return None if as_of is None else (market_day_service.now_sh().date() - as_of).days


def _empty(as_of: date | None, quality: str, reason: str | None) -> dict[str, Any]:
    return {
        "stale_days": _stale_days(as_of),
        "as_of": as_of,
        "as_of_quality": quality,
        "degraded_reason": reason,
        "summary": {
            "up_triggered": 0,
            "up_near": 0,
            "down_triggered": 0,
            "down_near": 0,
            "severe": 0,
            "limit_up": 0,
            "limit_down": 0,
        },
        "items": [],
        "history": [],
        "turnover_only": [],
        "turnover_available": False,
        "turnover_as_of": None,
        "thresholds": _rule_payload(),
        "caveats": list(CAVEATS),
    }


def _limit_pct(limits: dict[str, float] | None, direction: str, fallback: float) -> float:
    """当日限幅（%）：优先交易所限价（ST 的真实限幅），缺失时用板块固定值兜底。"""
    if not limits:
        return fallback
    pre = limits.get("pre_close") or 0.0
    if pre <= 0:
        return fallback
    if direction == "up":
        up = limits.get("up_limit") or 0.0
        return (up / pre - 1.0) * 100.0 if up > 0 else fallback
    down = limits.get("down_limit") or 0.0
    return (1.0 - down / pre) * 100.0 if down > 0 else fallback


def _devs_by_position(
    rows: list[dict[str, Any]],
    day_pos: dict[date, int],
    index_pct: dict[date, float],
    size: int,
) -> tuple[list[float | None], set[int]]:
    """把个股行情投影到「交易日位置」数组上；缺行的位置是 None.

    同时返回**有个股行情**的位置集合：`None` 有两种成因 —— 个股当天没行情（停牌）与
    基准指数缺日，只有分开才知道该怪谁（`dev3=None` 的 tooltip 原本一律写「停牌」，
    而实际可能是指数日线没入库，页面于是把「算不出来」读成「没有异动」）。
    """
    devs: list[float | None] = [None] * size
    filled: set[int] = set()
    for row in rows:
        pos = day_pos.get(row["trade_date"])
        if pos is None:
            continue
        filled.add(pos)
        stock_pct = calc.pct_change(float(row["close"]), float(row["pre_close"] or 0.0))
        devs[pos] = calc.daily_deviation(stock_pct, index_pct.get(row["trade_date"]))
    return devs, filled


def _is_ladder(fact: dict[str, Any] | None) -> bool:
    """连板梯队：昨日涨停，或近 10 日内 ≥2 板（``market_limit_up_stock_daily`` 口径）。"""
    if not fact:
        return False
    return bool(fact.get("prev_is_lu")) or (fact.get("boards_in_window") or 0) >= 2


async def _load_series(
    db: AsyncSession, start: date, end: date, stock_ids: list[int] | None = None
) -> dict[int, list[dict[str, Any]]]:
    rows = await regulatory_repo.load_quotes(db, start, end, stock_ids)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["stock_id"]), []).append(row)
    return grouped


async def _build(db: AsyncSession, as_of: date, history_days: int) -> dict[str, Any]:
    days = await regulatory_repo.list_market_days(db, as_of, LOOKBACK_DAYS)
    if not days:
        return _empty(as_of, "partial", "no_quotes")
    if days[-1] != as_of:
        return _empty(as_of, "partial", "not_a_trade_day")

    day_pos = {d: i for i, d in enumerate(days)}
    as_of_pos = len(days) - 1
    recent_start = days[max(0, len(days) - RECENT_DAYS)]

    codes = sorted({code for code, _ in calc.BENCHMARKS.values()})
    index_pct_all = await regulatory_repo.load_index_pct(
        db, codes, days[0] - timedelta(days=15), as_of
    )
    limits = await regulatory_repo.load_price_limits(db, as_of)
    facts = await regulatory_repo.load_limit_up_facts(db, as_of)

    #: 个股近 3 日行情齐备、但偏离算不出的基准指数（这些板的 3 日口径根本不可用）
    bench_missing: set[str] = set()
    last3 = range(len(days) - calc.WINDOW_DAYS, len(days))
    history: dict[date, list[dict[str, Any]]] = {d: [] for d in days[-history_days:]}
    history_floor = len(days) - history_days
    candidates: set[int] = set()

    def consume(
        stock_id: int,
        head: dict[str, Any],
        rule: calc.ResolvedRule,
        devs: list[float | None],
        filled: set[int],
        close: float,
    ) -> None:
        """单只股票的收尾工作：候选判定 + 异动历史.

        抽成函数是因为行情现在是**流式**消费的（见下方）：每换一只股票就结算上一只，
        全市场近 13 日行情不再整包驻留（2026-10-21 实测整包 84MB 峰值 / 512MB 容器）。
        """
        dev3 = calc.window_sum(devs, calc.WINDOW_DAYS)
        # 个股 3 日行情均在、却仍算不出偏离 ⇒ 缺的是基准指数（不是停牌）。
        # 不标记的后果：整块板子每一行都显「--（窗口缺行·停牌）」且 KPI 归零，
        # 读者得到两个错结论：「今天没人接近异动线」+「这些票都停牌」。
        if dev3 is None and all(p in filled for p in last3):
            bench_missing.add(rule.benchmark_code)
        direction = "up" if (dev3 is None or dev3 >= 0) else "down"
        status = calc.status_of(dev3, rule.threshold_pct, direction=direction)

        lim = limits.get(stock_id)
        is_lu = bool(lim and lim["up_limit"] > 0 and close >= lim["up_limit"] - TOUCH_TOLERANCE)
        is_ld = bool(lim and lim["down_limit"] > 0 and close <= lim["down_limit"] + TOUCH_TOLERANCE)
        # **今天没有行情（停牌）不得进候选**：`close` 是停牌前最后一笔，`day_pct`/`is_lu`/
        # 明日触发价都会把旧价格当今天的价格。梯队补位只补「窗口缺行但今天在交易」的票。
        # 历史仍可记：那是过去确实发生过的日子。
        if as_of_pos in filled and (
            status != "watch" or is_lu or is_ld or _is_ladder(facts.get(stock_id))
        ):
            candidates.add(stock_id)

        # ── 近 N 日异动历史：只记「首次越过阈值」的那一天 ──────────────────
        for pos in range(max(2, history_floor), len(days)):
            bucket = history.get(days[pos])
            if bucket is None or len(bucket) >= HISTORY_CAP:
                continue
            value = calc.window_sum(devs[pos - 2 : pos + 1], calc.WINDOW_DAYS)
            if value is None:
                continue
            direction_h = "up" if value >= 0 else "down"
            if abs(value) < rule.threshold_pct:
                continue
            prev = calc.window_sum(devs[pos - 3 : pos], calc.WINDOW_DAYS) if pos >= 3 else None
            if prev is not None and (
                prev >= rule.threshold_pct if direction_h == "up" else prev <= -rule.threshold_pct
            ):
                continue
            bucket.append(
                {
                    "symbol": str(head.get("symbol") or ""),
                    "name": str(head.get("name") or ""),
                    "board": rule.board,
                    "direction": direction_h,
                    "dev3_pct": round(value, 2),
                    # 不回传阈值：这里的 `rule` 取的是**今日** ST 快照，拿它标注过去的
                    # 触发日就是拿今天的口径读历史（摘帽/戴帽后 12% 与 20% 会错位）。
                    # 不知道当时阈值就别印，比印一个会漂移的数字诚实。
                }
            )

    # ── 单趟流式扫全市场（ORDER BY stock_id ⇒ 换股即结算，常驻内存与股数无关）──
    seen = 0
    cur_id: int | None = None
    head: dict[str, Any] = {}
    rule = calc.resolve_rule(None, None, "")
    devs: list[float | None] = [None] * len(days)
    filled: set[int] = set()
    last_close = 0.0
    async for row in regulatory_repo.stream_quotes(db, recent_start, as_of):
        seen += 1
        stock_id = int(row["stock_id"])
        if stock_id != cur_id:
            if cur_id is not None:
                consume(cur_id, head, rule, devs, filled, last_close)
            cur_id = stock_id
            head = dict(row)
            rule = calc.resolve_rule(head.get("exchange"), head.get("market"), head.get("name"))
            devs = [None] * len(days)
            filled = set()
        pos = day_pos.get(row["trade_date"])
        last_close = float(row["close"])
        if pos is not None:
            filled.add(pos)
            idx = index_pct_all.get(rule.benchmark_code, {})
            devs[pos] = calc.daily_deviation(
                calc.pct_change(last_close, float(row["pre_close"] or 0.0)),
                idx.get(row["trade_date"]),
            )
    if cur_id is not None:
        consume(cur_id, head, rule, devs, filled, last_close)
    if seen == 0:
        return _empty(as_of, "partial", "no_quotes")

    # ── 候选股：补齐 35 日序列，算事件 / 严重 / 明日触发 ────────────────────
    ids = sorted(candidates)
    long_series = await _load_series(db, days[0], as_of, ids) if ids else {}
    items: list[dict[str, Any]] = []
    for stock_id in ids:
        rows = long_series.get(stock_id) or []
        if not rows:
            continue
        head = rows[0]
        name = str(head.get("name") or "")
        rule = calc.resolve_rule(head.get("exchange"), head.get("market"), name)
        idx = index_pct_all.get(rule.benchmark_code, {})
        devs, _ = _devs_by_position(rows, day_pos, idx, len(days))
        dev3 = calc.window_sum(devs, calc.WINDOW_DAYS)
        fact = facts.get(stock_id)
        lim = limits.get(stock_id)
        close = float(rows[-1]["close"])
        is_lu = bool(lim and lim["up_limit"] > 0 and close >= lim["up_limit"] - TOUCH_TOLERANCE)
        is_ld = bool(lim and lim["down_limit"] > 0 and close <= lim["down_limit"] + TOUCH_TOLERANCE)
        if dev3 is not None:
            direction = "up" if dev3 >= 0 else "down"
        elif is_ld:
            direction = "down"
        else:
            direction = "up"
        status = calc.status_of(dev3, rule.threshold_pct, direction=direction)
        # 梯队股但今日既没板、3 日偏离也够不着「临近」：留在观察档（用户要看的是梯队全景）
        if status == "watch" and not (is_lu or is_ld or _is_ladder(fact)):
            continue

        events = calc.find_events(devs, rule.threshold_pct, direction=direction)
        events_10d = calc.count_events(events, len(devs), calc.SEVERE_COUNT_DAYS)
        flags = calc.severe_flags(devs, events, direction=direction)
        trigger = calc.next_day_trigger(
            close,
            devs,
            rule.threshold_pct,
            _limit_pct(lim, direction, rule.price_limit_pct),
            direction=direction,
        )
        # 已达线的票明日再触发是确定性事件（持平也算），给一个「触发价」只会误导
        if status == "triggered":
            trigger = None
        day_pct = calc.pct_change(close, float(rows[-1]["pre_close"] or 0.0))
        items.append(
            {
                "symbol": str(head.get("symbol") or ""),
                "name": name,
                "board": rule.board,
                "is_st": rule.is_st,
                "direction": direction,
                "status": status,
                "close": round(close, 2),
                "day_pct": round(day_pct, 2) if day_pct is not None else None,
                "threshold_pct": rule.threshold_pct,
                "dev3_pct": round(dev3, 2) if dev3 is not None else None,
                "dev10_pct": _round_or_none(calc.window_sum(devs, 10)),
                "dev30_pct": _round_or_none(calc.window_sum(devs, 30)),
                "events_10d": events_10d,
                "severe_flags": list(flags),
                "streak": (fact or {}).get("streak"),
                "days_span": (fact or {}).get("days_span"),
                "boards_in_window": (fact or {}).get("boards_in_window"),
                "limit_up": is_lu,
                "limit_down": is_ld,
                "benchmark_code": rule.benchmark_code,
                "benchmark_name": rule.benchmark_name,
                "next_trigger": (
                    {
                        "price": trigger.price,
                        "needed_pct": trigger.needed_pct,
                        "partial_pct": trigger.partial_pct,
                        "limit_pct": round(trigger.limit_pct, 2),
                        "feasible": trigger.feasible,
                    }
                    if trigger is not None
                    else None
                ),
            }
        )

    items.sort(key=_item_sort_key)

    # ── 换手率口径旁路：只列「价格未达线、但换手率单独达标」的票 ──────────────
    # 交易所的换手率触发条件与 3 日偏离是**并列**的独立条件；把它塞进 items 会让
    # 「一个数字 = 全部认定条件」的误读重现。故单独数组 + 页面一行提示。
    turnover_as_of = await regulatory_repo.turnover_data_as_of(db, as_of)
    turnover_available = turnover_as_of == as_of
    turnover_only: list[dict[str, Any]] = []
    if turnover_available:
        seen_symbols = {item["symbol"] for item in items}
        for hit in await regulatory_repo.load_turnover_only_hits(db, as_of):
            symbol = str(hit["symbol"])
            if symbol in seen_symbols:
                continue
            name = str(hit["name"] or "")
            close = float(hit["close"])
            day_pct = calc.pct_change(close, float(hit["pre_close"] or 0.0))
            turnover_only.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "board": str(hit["market"] or "主板"),
                    "is_st": calc.is_st_name(name),
                    "close": round(close, 2),
                    "day_pct": round(day_pct, 2) if day_pct is not None else None,
                    "turnover_3d_pct": round(float(hit["sum3"]), 2),
                    "turnover_3d_avg_pct": round(float(hit["avg3"]), 2),
                    "turnover_prev5_avg_pct": round(float(hit["avg5"]), 2),
                    "turnover_ratio": round(float(hit["ratio"]), 1),
                }
            )
        turnover_only = turnover_only[:50]  # 极端日兜底：旁路是一行提示，不是第二张全市场表

    history_out = [
        {"trade_date": d, "items": sorted(h, key=lambda x: (-abs(x["dev3_pct"]), x["symbol"]))}
        for d, h in sorted(history.items(), reverse=True)
        if h
    ]
    summary = {
        "up_triggered": sum(
            1 for i in items if i["direction"] == "up" and i["status"] == "triggered"
        ),
        "up_near": sum(1 for i in items if i["direction"] == "up" and i["status"] == "near"),
        "down_triggered": sum(
            1 for i in items if i["direction"] == "down" and i["status"] == "triggered"
        ),
        "down_near": sum(1 for i in items if i["direction"] == "down" and i["status"] == "near"),
        "severe": sum(1 for i in items if i["severe_flags"]),
        "limit_up": sum(1 for i in items if i["limit_up"]),
        "limit_down": sum(1 for i in items if i["limit_down"]),
    }
    return {
        "as_of": as_of,
        "as_of_quality": "complete",
        # 基准指数缺日优先：它把整块板子的 status 全部退化成 watch，比缺涨跌停价更能骗人
        "degraded_reason": (
            "benchmark_missing" if bench_missing else (None if limits else "price_limits_missing")
        ),
        "summary": summary,
        "items": items,
        "history": history_out,
        "turnover_only": turnover_only,
        "turnover_available": turnover_available,
        "turnover_as_of": turnover_as_of,
        "thresholds": _rule_payload(),
        "caveats": list(CAVEATS),
    }


def _round_or_none(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


_STATUS_RANK = {"triggered": 0, "near": 1, "watch": 2}


def _item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    # 涨向在前；已达线 → 临近 → 观察；同档偏离越大越靠前；连板数只做次级键（缺失排最后）。
    streak = item["streak"]
    dev3 = item["dev3_pct"]
    return (
        0 if item["direction"] == "up" else 1,
        _STATUS_RANK.get(item["status"], 3),
        -(abs(dev3) if dev3 is not None else -1.0),
        -(streak if streak is not None else -1),
        item["symbol"],
    )


class _BuildLock:
    """一把单飞锁 + **引用计数**（谁还在等/用）。

    为什么不能 `finally: _BUILD_LOCKS.pop(key)`：A 算完弹键时，B 还握着同一把锁在等；
    此时来了 C，`setdefault` 会新建一把锁 ⇒ B 与 C 同时冷构，单飞失效。引用计数在
    **最后一个使用者**退出时才回收键，既保证单飞，又不让 `?date=` 的键空间无界累积。
    """

    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.refs = 0


#: 单飞锁：同一 `(as_of, history_days)` 的冷请求串行化。
#: 为何需要：一次冷构 2.5s（实测）、峰值 12MB Python / 缓存 TTL 120s。缓存过期瞬间
#: N 个并发请求会各算一遍（N × 12MB + N × 全市场扫库），这是 512MB 容器里最现实的 OOM 入口。
#: 锁在**同一个事件循环**内（uvicorn 单 worker 多协程），无需线程安全。
_BUILD_LOCKS: dict[str, _BuildLock] = {}

#: 全局限流：**单飞锁键可被 `?date=` 无限拉开**（公开接口、无鉴权），
#: 100 个不同日期 = 100 个不同的键 = 100 个并发冷构（≈100 × 12MB）。单飞对此无效，
#: 真正兜住内存的是这个进程级信号量。上限 2 ≈ 24MB 峰值增量。
_MAX_CONCURRENT_BUILDS = 2
_BUILD_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_BUILDS)


def _with_request_semantics(payload: dict[str, Any], as_of: date, quality: str) -> dict[str, Any]:
    """盖上**本次请求**的两个派生量（缓存里那份是按写入时刻算的）。"""
    out = dict(payload)
    # `as_of_quality` 是本次请求的语义（缺省=最近完整日 / 显式=指定日期），不属于库内事实
    out["as_of_quality"] = quality
    # 陈旧度同理：跨零点会冻结成旧值（全站 `FreshnessNote` 的「N 天前」靠它）
    out["stale_days"] = _stale_days(as_of)
    return out


async def ensure_within_window(cache: CacheClient | None, as_of: date | None) -> None:
    """校验显式 ``?date=`` 落在最近 ``DATE_WINDOW_TRADING_DAYS`` 个交易日内.

    为什么在 API 层调而不是塞进 ``get_radar``：这是**请求参数校验**，不是读面逻辑；
    服务层测试仍可以直接构造任意日期测口径。下界用 ``daily_quotes`` 的真实交易日序列
    算（倒序 250 个不同日期的最小值），不靠工作日推算。
    """
    if as_of is None:
        return
    boundary: date | None = None
    if cache is not None:
        cached = await cache.get(_DATE_WINDOW_CACHE_KEY)
        if isinstance(cached, date):
            boundary = cached
        elif isinstance(cached, str):
            try:
                boundary = date.fromisoformat(cached)
            except ValueError:
                boundary = None
    if boundary is None:
        async with async_session_factory() as db:
            boundary = await regulatory_repo.oldest_recent_trade_day(db, DATE_WINDOW_TRADING_DAYS)
        if cache is not None and boundary is not None:
            await cache.set(
                _DATE_WINDOW_CACHE_KEY, boundary.isoformat(), ttl=_DATE_WINDOW_CACHE_TTL
            )
    if boundary is not None and as_of < boundary:
        raise ValueError(
            f"date 超出窗口：仅允许最近 {DATE_WINDOW_TRADING_DAYS} 个交易日"
            f"（最早 {boundary.isoformat()}）"
        )


async def _resolve_as_of(cache: CacheClient | None, as_of: date | None) -> tuple[date, str] | None:
    """判据日与本次请求的口径；库内没有任何行情 → ``None``。

    **用一次短命 session**：判据日解析（可能是共享组件的缓存未命中，实测 1.5s）
    不应该占着连接等到构建结束——否则并发用户会把池子（pool_size 10）抽完。
    """
    if as_of is not None:
        return as_of, "requested"
    async with async_session_factory() as db:
        resolved = await market_day_service.resolve_latest_complete_day(db, cache=cache)
    return None if resolved is None else (resolved.day, resolved.quality)


async def get_radar(
    cache: CacheClient | None,
    as_of: date | None = None,
    history_days: int = HISTORY_DAYS,
) -> dict[str, Any]:
    """监管异动雷达读面；``as_of`` 缺省 = 最近完整交易日。

    并发三层防线（多用户同时打这个公开接口时）：
    1. **缓存**（120s）——命中即返回，零 DB；
    2. **单飞锁**——同一 `(as_of, history_days)` 只算一次；
    3. **全局信号量**——键可被 `?date=` 拉开，真正兜住内存的是它；
    且等锁/等信号量期间**不持有 DB 会话**（否则 waiters 各自占一条连接）。
    """
    # 未来日期直接拒绝：它不可能是交易日，且 `?date=` 是公开参数——不拦会让未来键也进缓存/构建。
    if as_of is not None and as_of > market_day_service.now_sh().date():
        return _empty(as_of, "requested", "not_a_trade_day")
    resolved = await _resolve_as_of(cache, as_of)
    if resolved is None:
        return _empty(None, "partial", "no_quotes")
    day, quality = resolved

    key = f"{_CACHE_PREFIX}:{day.isoformat()}:{history_days}"
    if cache is not None:
        cached = await cache.get(key)
        if cached:
            return _with_request_semantics(cached, day, quality)

    entry = _BUILD_LOCKS.get(key)
    if entry is None:
        entry = _BuildLock()
        _BUILD_LOCKS[key] = entry
    entry.refs += 1
    try:
        async with entry.lock:
            # 双检：等锁期间别的协程可能已经把结果写进缓存
            if cache is not None:
                cached = await cache.get(key)
                if cached:
                    return _with_request_semantics(cached, day, quality)
            started = time.perf_counter()
            async with _BUILD_SEMAPHORE:
                async with async_session_factory() as db:
                    payload = await _build(db, day, history_days)
            if cache is not None:
                await cache.set(key, payload, ttl=CACHE_TTL)
            logger.info(
                "regulatory radar built as_of=%s history_days=%d items=%d history=%d "
                "degraded=%s elapsed_ms=%.0f",
                day.isoformat(),
                history_days,
                len(payload.get("items") or []),
                len(payload.get("history") or []),
                payload.get("degraded_reason"),
                (time.perf_counter() - started) * 1000.0,
            )
            return _with_request_semantics(payload, day, quality)
    finally:
        entry.refs -= 1
        if entry.refs == 0:
            _BUILD_LOCKS.pop(key, None)
