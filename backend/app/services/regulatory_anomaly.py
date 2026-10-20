"""A 股「交易异常波动 / 严重异常波动」的纯计算口径（无 DB、无网络、无时钟）.

监管口径（截至 2026-10 的交易所规则，原文出处见 docs/design/regulatory-anomaly.md）：

- **一般异常波动**：连续 3 个交易日内日收盘价涨跌幅偏离值累计达到阈值。阈值按板块：
  沪深主板 ±20%；主板风险警示股（ST/*ST）±12%；创业板 / 科创板 ±30%；北交所 ±40%
  （北交所口径是「连续 3 个**有成交**交易日」）。
- **严重异常波动**：连续 10 个交易日内 3 次同向异常波动；或连续 10 个交易日偏离值
  累计达到 +100%（-50%）；或连续 30 个交易日累计达到 +200%（-70%）。
- **偏离值** = 个股日涨跌幅 − 对应基准指数日涨跌幅；**累计** = 窗口内逐日偏离值
  **求和**（不是复合收益率）。交易所公告里的「连续三个交易日累计偏离 32.4%」正是
  逐日偏离值之和，因此本模块用求和，不用 ``close/pre_close`` 的复利差。
- **基准指数**：沪市主板→上证综指（000001.SH）；科创板→科创50（000688.SH）；
  深市主板→深证成指（399001.SZ）；创业板→创业板指（399006.SZ）；北交所→北证50
  （899050.BJ）。交易所规则原文写「对应分类指数」，本实现取上述指数并在页面披露。

**v1 明确不实现**：换手率口径（连续 3 日日均换手率 / 前 5 日日均 ≥ 30 倍，且累计
换手率 ≥ 20%）。它与价格口径是**独立触发条件**，需要流通股本口径的换手率；本模块
只回答「按收盘价能不能算出异动」，页面上必须显式写「仅价格口径」。

口径禁地（改前先读 docs/design/regulatory-anomaly.md §已知失效场景）：
- ST 判定只能看 ``stocks.name`` 前缀。名称是**当日快照**，历史回放会把今天摘帽/戴帽
  的状态套到过去；本模块只接收调用方传入的 ``is_st``，不自己查库。
- 停牌：窗口内任意一天没有行情（或与前一交易日不连续）就**不判**（返回 None），
  绝不把停牌前的旧价格当成今天的价格。
- 次新：上市不满 3 个交易日不判；不满 10/30 个交易日的，10/30 日累计类严重异动不判。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

#: 「临近」= 达到阈值的这个比例（80%）。只用于分级提示，不是监管口径。
NEAR_RATIO = 0.8

#: 异常波动窗口（连续 N 个交易日）。
WINDOW_DAYS = 3
#: 严重异常波动「N 日内 M 次」的口径。
SEVERE_COUNT_DAYS = 10
SEVERE_COUNT_TIMES = 3
#: 严重异常波动的累计偏离阈值（%）。
SEVERE_10D_PCT = 100.0
SEVERE_10D_DOWN_PCT = -50.0
SEVERE_30D_PCT = 200.0
SEVERE_30D_DOWN_PCT = -70.0

#: ST/*ST（含历史遗留 SST / S*ST）名称前缀。
_ST_RE = re.compile(r"^S?\*?ST")


@dataclass(frozen=True)
class BoardRule:
    """一个板块的基础规则（不含交易所维度；主板沪/深的基准指数不同，见 ``BENCHMARKS``）。"""

    board: str
    threshold_pct: float
    st_threshold_pct: float | None
    price_limit_pct: float
    st_price_limit_pct: float | None


RULES: dict[str, BoardRule] = {
    "主板": BoardRule("主板", 20.0, 12.0, 10.0, 5.0),
    "创业板": BoardRule("创业板", 30.0, None, 20.0, None),
    "科创板": BoardRule("科创板", 30.0, None, 20.0, None),
    "北交所": BoardRule("北交所", 40.0, None, 30.0, None),
}

#: (交易所, 板块) → (基准指数代码, 人话名)。交易所取 ``stocks.exchange`` 的原值。
BENCHMARKS: dict[tuple[str, str], tuple[str, str]] = {
    ("Shanghai_Stocks", "主板"): ("000001.SH", "上证综指"),
    ("Shanghai_Stocks", "科创板"): ("000688.SH", "科创50"),
    ("Shenzen_Stocks", "主板"): ("399001.SZ", "深证成指"),
    ("Shenzen_Stocks", "创业板"): ("399006.SZ", "创业板指"),
    ("Beijing_Stocks", "北交所"): ("899050.BJ", "北证50"),
}

#: 交易所 → 主板默认基准（板块缺失时的兜底，避免把沪市股票拿深证成指去算）。
_MAIN_BENCHMARK: dict[str, tuple[str, str]] = {
    "Shanghai_Stocks": ("000001.SH", "上证综指"),
    "Shenzen_Stocks": ("399001.SZ", "深证成指"),
    "Beijing_Stocks": ("899050.BJ", "北证50"),
}

DIRECTIONS: tuple[str, ...] = ("up", "down")


@dataclass(frozen=True)
class ResolvedRule:
    """某只股票当日适用的完整规则（阈值 + 涨跌幅限制 + 基准指数）。"""

    board: str
    is_st: bool
    threshold_pct: float
    price_limit_pct: float
    benchmark_code: str
    benchmark_name: str


@dataclass(frozen=True)
class DeviationEvent:
    """一次「连续 3 日偏离累计越过阈值」事件（窗口下标记，供 10 日计数/严重判定）。"""

    start: int
    end: int
    value: float


@dataclass(frozen=True)
class NextTrigger:
    """明日再走多少会触发（3 日窗口 = 最近 2 个已知交易日 + 明日）。"""

    price: float
    needed_pct: float
    partial_pct: float
    limit_pct: float
    feasible: bool


def is_st_name(name: str | None) -> bool:
    """名称前缀判风险警示股（``ST`` / ``*ST`` / 历史 ``SST`` / ``S*ST``）。"""
    return bool(name) and bool(_ST_RE.match(str(name).strip()))


def board_rule(board: str | None) -> BoardRule:
    """板块规则；未知板块按主板处理（库里 ``stocks.market`` 四值齐全，兜底只为不崩）。"""
    return RULES.get(board or "", RULES["主板"])


def benchmark_for(exchange: str | None, board: str | None) -> tuple[str, str]:
    """(交易所, 板块) → 基准指数；板块未知时按该所主板指数兜底。"""
    hit = BENCHMARKS.get((exchange or "", board or ""))
    if hit is not None:
        return hit
    return _MAIN_BENCHMARK.get(exchange or "", ("000001.SH", "上证综指"))


def resolve_rule(exchange: str | None, board: str | None, name: str | None) -> ResolvedRule:
    """把「交易所 + 板块 + 名称」解析成当日适用的规则。

    ST 只在**主板**改变阈值/限幅（主板风险警示股 5% 限幅、±12% 阈值）；
    创业板/科创板的风险警示股仍是 20% 限幅、±30% 阈值，北交所同理不降档。
    """
    rule = board_rule(board)
    st = is_st_name(name)
    if rule.board != "主板":
        st = False
    threshold = (
        rule.st_threshold_pct if (st and rule.st_threshold_pct is not None) else rule.threshold_pct
    )
    limit = (
        rule.st_price_limit_pct
        if (st and rule.st_price_limit_pct is not None)
        else rule.price_limit_pct
    )
    code, label = benchmark_for(exchange, rule.board if board in RULES else "主板")
    return ResolvedRule(
        board=rule.board,
        is_st=st,
        threshold_pct=float(threshold),
        price_limit_pct=float(limit),
        benchmark_code=code,
        benchmark_name=label,
    )


def pct_change(close: float, pre_close: float) -> float | None:
    """涨跌幅（%） = ``close/pre_close - 1``；前收缺失/为 0 返回 None（不拿 0 充数）。"""
    if not pre_close or pre_close <= 0:
        return None
    return (close / pre_close - 1.0) * 100.0


def daily_deviation(stock_pct: float | None, index_pct: float | None) -> float | None:
    """单日偏离值 = 个股涨跌幅 − 基准指数涨跌幅；任一缺失则 None。"""
    if stock_pct is None or index_pct is None:
        return None
    return stock_pct - index_pct


def window_sum(values: Sequence[float | None], window: int = WINDOW_DAYS) -> float | None:
    """窗口内偏离值累计（求和）；窗口不足或含缺失 → None（不跳过缺失日硬算）。"""
    if window <= 0 or len(values) < window:
        return None
    tail = values[-window:]
    if any(v is None for v in tail):
        return None
    return float(sum(v for v in tail if v is not None))


def status_of(dev: float | None, threshold_pct: float, *, direction: str = "up") -> str:
    """分级：``triggered``（已达线）/ ``near``（≥80% 阈值）/ ``watch``（观察）。

    ``direction="down"`` 时比较负向。已触发优先于临近。
    """
    if dev is None:
        return "watch"
    signed = dev if direction == "up" else -dev
    if signed >= threshold_pct:
        return "triggered"
    if signed >= threshold_pct * NEAR_RATIO:
        return "near"
    return "watch"


def find_events(
    devs: Sequence[float | None],
    threshold_pct: float,
    *,
    direction: str = "up",
    window: int = WINDOW_DAYS,
    lookback: int | None = None,
) -> list[DeviationEvent]:
    """按「不重叠窗口」找异动事件：一次触发后，下一个窗口从触发窗口之后开始。

    为什么不是「每个越线窗口都算一次」：连续 6 个 10% 板会让 4 个重叠窗口都越线，
    那会把「1 次异动」数成 4 次，严重异常波动的「10 日 3 次」会在 5 连板就误报。
    交易所按**公告次数**计，公告后新的 3 日窗口才可能再次触发，故用不重叠窗口近似。
    """
    n = len(devs)
    if n < window:
        return []
    sign = 1.0 if direction == "up" else -1.0
    events: list[DeviationEvent] = []
    last_end = -1
    for end in range(window - 1, n):
        start = end - window + 1
        if start <= last_end:
            continue
        value = window_sum(devs[: end + 1], window)
        if value is None:
            continue
        if sign * value < threshold_pct:
            continue
        if lookback is not None and end < n - lookback:
            continue
        events.append(DeviationEvent(start=start, end=end, value=value))
        last_end = end
    return events


def count_events(events: Sequence[DeviationEvent], n: int, days: int) -> int:
    """最近 ``days`` 个交易日（按序列下标）内的事件数。"""
    if n <= 0:
        return 0
    floor = n - days
    return sum(1 for e in events if e.end >= floor)


def severe_flags(
    devs: Sequence[float | None],
    events: Sequence[DeviationEvent],
    *,
    direction: str = "up",
) -> tuple[str, ...]:
    """严重异常波动命中项（顺序固定，前端不再自己拼）。

    - 「10日3次」：最近 10 个交易日内 3 次同向异动；
    - 「10日累计+100% / -50%」：最近 10 个交易日偏离值累计；
    - 「30日累计+200% / -70%」：最近 30 个交易日偏离值累计。

    10/30 日累计样本不足时**不判**（不拿短窗口冒充）。
    """
    n = len(devs)
    flags: list[str] = []
    if count_events(events, n, SEVERE_COUNT_DAYS) >= SEVERE_COUNT_TIMES:
        flags.append(f"{SEVERE_COUNT_DAYS}日{SEVERE_COUNT_TIMES}次")
    ten = window_sum(devs, 10) if 10 <= n else None
    if ten is not None:
        hit = ten >= SEVERE_10D_PCT if direction == "up" else ten <= SEVERE_10D_DOWN_PCT
        if hit:
            label = (
                f"10日累计+{SEVERE_10D_PCT:.0f}%"
                if direction == "up"
                else f"10日累计{SEVERE_10D_DOWN_PCT:.0f}%"
            )
            flags.append(label)
    thirty = window_sum(devs, 30) if 30 <= n else None
    if thirty is not None:
        hit = thirty >= SEVERE_30D_PCT if direction == "up" else thirty <= SEVERE_30D_DOWN_PCT
        if hit:
            label = (
                f"30日累计+{SEVERE_30D_PCT:.0f}%"
                if direction == "up"
                else f"30日累计{SEVERE_30D_DOWN_PCT:.0f}%"
            )
            flags.append(label)
    return tuple(flags)


def _round_trigger(price: float, direction: str) -> float:
    """按报价单位取整：涨向向上取到分（「≥ 触发价」），跌向向下取到分。"""
    tick = Decimal("0.01")
    raw = Decimal(str(price))
    rounded = raw.quantize(tick, rounding=ROUND_CEILING if direction == "up" else ROUND_FLOOR)
    return float(rounded)


def next_day_trigger(
    close: float,
    devs: Sequence[float | None],
    threshold_pct: float,
    price_limit_pct: float,
    *,
    direction: str = "up",
) -> NextTrigger | None:
    """明日触发价：3 日窗口 = 最近 2 个已知交易日 + 明日，**假设基准指数明日不涨不跌**。

    明天需要的个股涨跌幅 ``needed = ±threshold − 最近两日偏离合计``；触发价 =
    ``close × (1 + needed/100)``。``needed`` 超过明日涨跌幅限制 → ``feasible=False``
    （明日涨停也触发不了，页面显示「—」而不是一个不可达的价格）。
    """
    if len(devs) < 2:
        return None
    tail = devs[-2:]
    if any(v is None for v in tail):
        return None
    partial = float(sum(v for v in tail if v is not None))
    sign = 1.0 if direction == "up" else -1.0
    needed = sign * threshold_pct - partial
    price = close * (1.0 + needed / 100.0)
    feasible = abs(needed) <= price_limit_pct + 1e-9 and price > 0
    return NextTrigger(
        price=_round_trigger(price, direction),
        needed_pct=round(needed, 2),
        partial_pct=round(partial, 2),
        limit_pct=price_limit_pct,
        feasible=feasible,
    )
