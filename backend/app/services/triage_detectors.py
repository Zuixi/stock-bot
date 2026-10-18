"""自选体检的 9 个**事实型**探测器（纯函数，无 IO）.

为什么是"事实"而不是"信号"：2026-10-17 的规则标定把预测型规则证伪（放量新高 −0.92%、
首板 −0.28%），见 ``docs/design/signal-rule-calibration.md``。本模块只陈述**已发生的事**，
不做打分、不预测、不承诺。

口径禁令（实机标定得出，改前先看标定报告）：
- 换手率只能用 ``turnover_rate``：``turnover_rate_f`` 全列 NULL（0/133,308）；
- 涨停判定只能走 ``stock_price_limits.up_limit``：``market_limit_up_stock_daily``
  实测只有 2 个交易日；
- 个股融资余额不可得（库里只有市场级 margin）⇒ 不得出现"个股杠杆"条件；
- 涨跌幅一律 ``close/pre_close - 1``（``adj_factor`` 仅 1.3% 非空，不能用于除权判断）；
- 解禁只能走 ``share_floats``（``float_date`` / ``float_share`` / ``float_ratio``）：
  该股**没有行**时一律不输出（不猜"没有解禁"），文案只写"解禁/可流通"
  （解禁只是"可流通"，与股东会不会卖是两件事，spec §S11.2 命名禁令）。

分级契约：``DetectorSpec.severity`` 是该探测器可能给出的**最高**分级，探测器可以按事实
**降级**（例如 ``limit_up_state`` 涨停=attention、炸板=risk；``new_high_low`` 新高=attention、
新低=risk）。这样 registry 里的 severity 恒为上限，合并排序只看 ``SEVERITIES`` 的四枚举。

阈值即配置：所有魔法数都进 ``DetectorSpec.thresholds``，本模块不出现裸阈值。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
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
    """一个探测器的登记项（阈值即配置；``verify`` = 复核去处语义）。

    **来源字段（provenance）是卡片契约的一部分**：``rule_text`` / ``version`` /
    ``source_tables`` / ``update_note`` / ``failure_modes`` 会经 ``with_provenance``
    填进 ``TriageItem``，再由 ``GET /api/v1/watchlists/triage/rules`` 原样下发。
    它们不是注释——``_assert_detectors`` 在导入期就拒绝空值，因为"规则从哪来、
    什么时候能算、什么时候会给出误导性事实"缺一条，用户就无法核对卡片上的数字。
    """

    key: str
    label: str
    severity: str
    thresholds: dict[str, float]
    required_fields: tuple[str, ...]
    verify: str
    #: 规则版本（阈值/判据变更必须升版）：历史卡片靠它解释当时的口径
    version: str
    #: 人话阈值（逐字上屏，前端不再自己拼文案）
    rule_text: str
    #: 一句话定义（这条规则在陈述什么事实）
    definition: str
    #: 判定依赖的表（第一项 = 卡片 ``source_table``；顺序即主次）
    source_tables: tuple[str, ...]
    #: 什么时候能算（更新时机）；写"盘后 X 起"，不写"实时"这类做不到的话
    update_note: str
    #: 该规则会给出**误导性事实**的具体场景（≥3 条，按规则各自相关）
    failure_modes: tuple[str, ...]


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


@dataclass(frozen=True)
class UnlockRow:
    """一条限售解禁计划（``share_floats``；**``float_share`` 单位是「股」**、``float_ratio`` %）。

    单位是实机核出来的（spec §S11.5 写"万股"、模型注释也写"万股"，但源值是「股」）：
    301387 单日 63,999,208 与其 ``daily_basic.total_share`` 10,730.19 万股之比 = 59.64%，
    与源 ``float_ratio`` 59.6441 逐位吻合；按"万股"读会大 10,000 倍。
    卡片一律换成 **万股** 上屏（``/10000``），口径不给读的人留坑。

    ``float_ratio`` 的分母是**总股本**（不是流通股本）：上例 59.64% 只对得上
    ``total_share``，对流通股本是 150%（301387 当时流通股仅 4,268 万股）。

    ``share_floats`` 是**持有人级**明细：同一解禁日可能有几十至几千行（每个股东/资管产品
    一行）。``triage_service`` 在 SQL 里已按 ``(ts_code, float_date)`` 合计成一行（把一次
    请求的行数从"上万"压到"≤30 天"），探测器仍按 ``float_date`` 再合计一次 —— 于是
    "多持有人 = 一次解禁事件"这条口径不依赖装载层，纯函数单独调用也成立。
    """

    float_date: date
    float_share: float | None = None
    float_ratio: float | None = None


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
    #: 自选代码 → 未来窗口内的解禁计划（按解禁日升序；**没有行 = 没有数据**，不是"没有解禁"）
    unlocks: dict[str, list[UnlockRow]] = field(default_factory=dict)
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
    """一条体检结论：只陈述事实 + 数据截至 + 复核去处 + **规则来源**。

    ``rule_text`` / ``rule_version`` / ``source_table`` 不在这里写死：探测器只负责
    "命中/不命中"，来源由 ``with_provenance`` 从 ``DetectorSpec`` 补（``triage_service``
    组合前调用），保证卡片文案与 registry 单向同源、不会两处漂移。
    """

    symbol: str
    name: str
    detector: str
    severity: str
    headline: str
    facts: tuple[Fact, ...]
    as_of: date
    source: str
    verify: str
    #: 人话阈值（来自 ``spec.rule_text``；空串只可能出现在未经 ``with_provenance`` 的构造里）
    rule_text: str = ""
    #: 规则版本（来自 ``spec.version``）
    rule_version: str = ""
    #: 主要来源表（来自 ``spec.source_tables[0]``）
    source_table: str = ""
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
    version="r1",
    rule_text="今日收盘跌破 20 日均线，且昨日收盘仍在 20 日均线上方（首次跌破才报）",
    definition="用 MA20 判「刚跌破」：只报首次跌破那一天，不重复报持续下跌的每一天。",
    source_tables=("daily_quotes",),
    update_note="盘后日线入库后即可计算（需最近 21 个交易日收盘）",
    failure_modes=(
        "停牌：当日无行情行时沿用停牌前最后一个交易日的数据，卡片仍标今天，看起来像今天跌破",
        "跳空低开：缺口一天跨过 MA20，前一日「还在均线上方」的状态无法反映跳空幅度，"
        "跌破发生在开盘一瞬间",
        "除权除息：当日跌幅的分母已按除权调整，但历史收盘未复权，除权日前后 MA20 本身会跳变",
        "次新（上市不足 21 个交易日）：没有 MA20，规则静默不判，看起来像「没跌破」",
    ),
)
SPEC_DROP_SHARP = DetectorSpec(
    key="drop_sharp",
    label="跌得急",
    severity="risk",
    thresholds={"day_pct": -7.0, "five_day_pct": -12.0},
    required_fields=("daily_quotes.close", "daily_quotes.pre_close"),
    verify="kline",
    version="r1",
    rule_text="当日跌幅 ≥ 7%，或 5 日跌幅 ≥ 12%",
    definition="陈述已发生的跌幅是否越过阈值，不判断跌完没有、也不给方向。",
    source_tables=("daily_quotes",),
    update_note="盘后日线入库后即可计算；5 日跌幅取最近 6 个交易日收盘",
    failure_modes=(
        "除权除息：当日跌幅分母已按除权调整故不会误报，但 5 日跌幅用的是未复权的历史收盘，"
        "除权日会凭空多出一段跌幅",
        "停牌复牌：复牌首日的前收是停牌前价格，一天内可能补跌越过阈值，读起来像「今天跌得急」",
        "一字跌停：跌幅被涨跌停板限制截断，连续一字跌停时 5 日跌幅会低估真实累计跌幅",
        "停牌：当日无行情行时沿用停牌前最后一个交易日的数据，卡片仍标今天",
        "次新（上市不足 6 个交易日）：没有 5 日可比收盘，规则静默不判",
    ),
)
SPEC_VOLUME_SPIKE = DetectorSpec(
    key="volume_spike",
    label="放量异动",
    severity="attention",
    thresholds={"amount_ratio": 2.0, "abs_pct": 5.0, "avg_days": 20},
    required_fields=("daily_quotes.amount",),
    verify="kline",
    version="r1",
    rule_text="当日成交额 ≥ 前 20 个交易日均额的 2 倍，且当日涨跌幅绝对值 ≥ 5%",
    definition="用成交额相对自身近期均值的倍数描述「量突然放大」，同时要求价格也有异动。",
    source_tables=("daily_quotes",),
    update_note="盘后成交额入库后即可计算（amount 单位千元，判定用比值故不影响）",
    failure_modes=(
        "停牌复牌：均额按有行情的交易日算，复牌首日放量容易被算成「2 倍异动」，而那只是积压成交",
        "一字板：封板日成交额可能被压到极低而非放大，规则不触发，用户会误以为「板上没有异动」",
        "停牌：当日无行情行时沿用停牌前最后一个交易日的数据，卡片仍标今天",
        "成交额缺失或为 0：均额为 0 时直接不判（避免除零），等于当天漏检且不给提示",
        "次新（上市不足 21 个交易日）：均额样本不足，规则静默不判",
    ),
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
    version="r1",
    rule_text="收盘 ≥ 涨停价 − 0.005 元记为涨停；盘中最高 ≥ 涨停价 − 0.005 元而收盘在板下记为炸板",
    definition="用交易所口径的涨停价描述「封住涨停」或「盘中触板后打开」这一事实。",
    source_tables=("stock_price_limits", "daily_quotes"),
    update_note="盘后 stk_limit 与日线同时入库后计算（涨停价只认交易所口径的 up_limit）",
    failure_modes=(
        "ST 股：真实限幅 5%，用名称/代码前缀推 10% 会判错；本规则只认 up_limit 故不受影响，"
        "但 up_limit 缺失当天整条不判",
        "一字板：全天封死且成交极少，事实是「涨停」，但读到的人容易把它当成可成交的价格",
        "次新股：上市初期无涨跌停或限幅不同，up_limit 可能为空，规则静默不判",
        "除权除息：涨停价按除权后的前收计算，除权日涨停价会跳变，与历史涨停价不可直接比较",
        "停牌：停牌日没有行情行，既不报涨停也不报炸板，看起来像「今天正常」",
    ),
)
SPEC_NEW_ANNOUNCEMENT = DetectorSpec(
    key="new_announcement",
    label="有新公告",
    severity="event",
    thresholds={"days": 3},
    required_fields=("announcements.title", "announcements.announce_time"),
    verify="announcement",
    version="r1",
    rule_text="近 3 个自然日有公告；含权益变动（equity_change）时优先展示",
    definition="只陈述「最近三天公司发了公告」这一事实，不解读公告内容的好坏。",
    source_tables=("announcements",),
    update_note="公告入库后即可计算（announce_time 为本地时间，按近 3 个自然日窗口取）",
    failure_modes=(
        "公告时间与交易日错位：盘后或周末公告会在下一个交易日被报出，读起来像「今天发的」",
        "公告分类只有 report/event 两类，分类缺失时权益变动会退化成普通新公告",
        "同一事项多条（更正/补充/摘要）会按条数重复计入，条数不等于事项数量",
        "公告源覆盖有限（实测仅近 1–2 个月），窗口外的历史日不判，等于静默漏报",
    ),
)
SPEC_DRAGON_TIGER = DetectorSpec(
    key="dragon_tiger",
    label="上了龙虎榜",
    severity="event",
    thresholds={"days": 3},
    required_fields=("dragon_tiger_entries.net_amount", "dragon_tiger_entries.trade_date"),
    verify="dragon_tiger",
    version="r1",
    rule_text="近 3 个交易日上过龙虎榜；展示上榜次数与净买卖额",
    definition="陈述「最近三个交易日出现在龙虎榜」这一事实，不解读资金意图。",
    source_tables=("dragon_tiger_entries",),
    update_note="龙虎榜盘后入库后计算；近 3 个交易日取行情里实际出现过的日期",
    failure_modes=(
        "上榜不等于全市场资金：net_amount 只是龙虎榜前五席位的净额，不是全市场主力资金",
        "同一股票同一天因多个上榜原因各有一行，净额会被重复累加，上榜次数也会重复计数",
        "未上榜不等于没有大额进出：龙虎榜只覆盖达到披露标准的个股",
        "数据源覆盖有限（实测仅近 1–2 个月），窗口外的历史日不判，等于静默漏报",
    ),
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
    version="r1",
    rule_text="所属申万一级当日涨跌幅进入全市场一级行业前 10%，且绝对涨跌幅 ≥ 2%",
    definition="用申万一级行业当日平均涨跌幅的全市场分位描述「所在板块在动」。",
    source_tables=("sw_industry_members", "daily_quotes"),
    update_note="盘后日线入库后按当前成分快照聚合；成分归属不做 PIT 还原（v1 已知口径）",
    failure_modes=(
        "成分归属用当前快照（非 PIT）：历史日回放会把今天的行业归属套到过去，"
        "改过行业的股票会被算进错的板块",
        "停牌成分股：板块均值是当日有行情成分股的算术平均，大面积停牌时均值失真",
        "涨跌停一字板：板块涨幅被涨跌停板截断，实际情绪强于数字所显示的程度",
        "次新/新上市：申万归属可能缺失，规则静默不判，看起来像「板块没动」",
        "分位只有 31 个一级行业：前 10% 实际是第 28–31 名，边界日第 28 名与第 27 名的差异可能极小",
    ),
)
SPEC_NEW_HIGH_LOW = DetectorSpec(
    key="new_high_low",
    label="创 60 日新高 / 新低",
    # 上限；新高按事实降级为 attention，新低为 risk
    severity="risk",
    thresholds={"days": 60},
    required_fields=("daily_quotes.close",),
    verify="kline",
    version="r1",
    rule_text=(
        "今日收盘 > 前 60 个交易日最高收盘 = 创 60 日新高；"
        "今日收盘 < 前 60 个交易日最低收盘 = 创 60 日新低"
    ),
    definition="用收盘价与自身近 60 个交易日区间比较，描述是否创区间新高/新低。",
    source_tables=("daily_quotes",),
    update_note="盘后日线入库后即可计算；上市不足 60 个交易日一律不判",
    failure_modes=(
        "次新（上市不足 60 个交易日）：窗口里没有 60 日历史，一律不判，看起来像「没创新高」",
        "除权除息：历史收盘未复权，除权后的收盘与除权前不可比，除权日附近会假报「创 60 日新低」",
        "停牌：60 日窗口按有行情的交易日算，长期停牌股复牌后比较的其实是更早的价格；"
        "当日停牌还会沿用旧数据并标今天",
        "一字板：封板日收盘即新高是真实事实，但没有成交，读到的人容易把它当成可成交的价",
        "上市首日：没有前 60 个交易日收盘，静默不判",
    ),
)
SPEC_UNLOCK_SOON = DetectorSpec(
    key="unlock_soon",
    label="30 天内解禁",
    # 上限；1%–3% 按事实降级为 attention（spec §S11.2）
    severity="risk",
    thresholds={"days": 30, "min_ratio": 1.0, "risk_ratio": 3.0},
    required_fields=(
        "share_floats.float_date",
        "share_floats.float_share",
        "share_floats.float_ratio",
    ),
    verify="kline",
    version="r1",
    rule_text=(
        "未来 30 个自然日内有解禁，且当日合计占总股本 ≥ 1%；≥ 3% 记 risk，1%–3% 记 attention"
    ),
    definition="陈述「未来 30 天将有可流通股份解禁」这一事实与规模（占总股本几成），"
    "不判断解禁后会不会抛售。",
    source_tables=("share_floats",),
    update_note=(
        "解禁计划入库后即可计算（按 float_date 取未来 30 个自然日；解禁日=当天不算「未来」）"
    ),
    failure_modes=(
        "解禁日 = 当天（as_of 是最近完整交易日）不报：那天已经可流通，不属于「未来 30 天」",
        "该股在 share_floats 里没有任何行时一律不输出：无记录 ≠ 没有解禁（覆盖不足时静默）",
        "float_ratio 缺失的解禁行不参与判定（无从核对「占比多少」），只有股数的记录会被漏掉",
        "占比的分母是总股本（源口径），不是流通股本：小盘股解禁 3% 总股本可能已是流通盘的几倍",
        "同一笔解禁被多次公告（修订）时读面按最新公告取一次；若上游把新增的一笔发成同持有人同类型，会被并成一条",
        "同一天多个持有人按 float_date 合计：看得到那天的总量，看不到单个股东的规模",
        "解禁只表示股份可流通，不等于股东会抛售；卡片上的规模数字不是未来的实际抛压",
    ),
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
    SPEC_UNLOCK_SOON,
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
            ShareFloat,
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
            "share_floats": ShareFloat,
        }
    return _FIELD_TABLES


def _assert_detectors() -> None:
    """导入期断言：9 个 · key 唯一 · severity 合法 · 来源字段齐全 · required_fields 真实存在。

    故意做成 raise（不是 assert）：``python -O`` 会删掉 assert，登记表就静默失去护栏。
    """
    if len(TRIAGE_DETECTORS) != 9:
        raise RuntimeError(f"探测器必须是 9 个，实际 {len(TRIAGE_DETECTORS)}")
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
        # 来源字段缺一条，卡片就无法回答"规则是什么/从哪来/什么时候能算/何时会误导"。
        # 校验的是**文本非空**（strip 后），因为空串比缺字段更隐蔽：前端会渲染成空白行。
        for attr in ("version", "rule_text", "definition", "update_note"):
            if not getattr(spec, attr).strip():
                raise RuntimeError(f"{spec.key} 的来源字段 {attr} 不得为空（provenance 契约）")
        if not spec.source_tables:
            raise RuntimeError(f"{spec.key} 必须声明 source_tables（至少一张表）")
        if len(spec.failure_modes) < 3:
            raise RuntimeError(f"{spec.key} 的 failure_modes 不足 3 条，无法讲清何时误导")
        if any(not mode.strip() for mode in spec.failure_modes):
            raise RuntimeError(f"{spec.key} 的 failure_modes 含空串")
        if len(set(spec.failure_modes)) != len(spec.failure_modes):
            raise RuntimeError(f"{spec.key} 的 failure_modes 有重复项")
        for table in spec.source_tables:
            if table not in tables:
                raise RuntimeError(f"{spec.key} 的 source_tables 指向未知表：{table}")
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

#: ``detector`` → spec 的查表（``with_provenance`` 用；导入期断言已保证 key 唯一）
SPEC_BY_KEY: dict[str, DetectorSpec] = {spec.key: spec for spec in TRIAGE_DETECTORS}


def with_provenance(item: TriageItem) -> TriageItem:
    """把规则来源三字段补进卡片（``triage_service`` 在组合/排序**之前**调用）。

    为什么不在 9 个探测器里各写一遍：同一份 ``rule_text`` 写 9 次必然漂移；这里从
    ``SPEC_BY_KEY`` 单向取值，探测器只负责判定，来源只有一个真相。
    """
    spec = SPEC_BY_KEY[item.detector]
    return replace(
        item,
        rule_text=spec.rule_text,
        rule_version=spec.version,
        source_table=spec.source_tables[0],
    )


def unavailable_labels(keys: Iterable[str]) -> list[str]:
    """把探测器 key 换成**上屏用**的人话 label（registry 顺序、去重）。

    为什么必须有这一层：``key`` 是机器串（``sector_move``），直接下发会让 `TriagePanel`
    把内部标识当文案渲染（实测过：用户看到 "sector_move"）。契约因此把 ``unavailable``
    定义为 label 列表，机器串只在 ``unavailable_keys``（排查用、**禁止上屏**）里出现。

    **未知 key 一律不进 label**：宁可少一行提示，也不把未登记的内部串漏到屏幕上
    （信息不会丢——它仍在 ``unavailable_keys`` 里，且 ``count`` 用它算）。
    """
    asked = set(keys)
    return [spec.label for spec in TRIAGE_DETECTORS if spec.key in asked]


def make_context_for_test(
    *,
    as_of: date,
    names: dict[str, str] | None = None,
    symbols_order: list[str] | None = None,
    quotes: dict[str, list[tuple[date, float, float, float]]] | None = None,
    limits: dict[str, tuple[float, float, float, float]] | None = None,
    announcements: dict[str, list[AnnRow]] | None = None,
    dragon_tiger: dict[str, list[tuple[date, float]]] | None = None,
    unlocks: dict[str, list[tuple[date, float | None, float | None]]] | None = None,
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
    ``dragon_tiger`` 为 ``(trade_date, net_amount)``，
    ``unlocks`` 为 ``(float_date, float_share, float_ratio)``（**股** / %，见 ``UnlockRow``）。
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
    ctx.unlocks = {
        symbol: [UnlockRow(*row) for row in rows] for symbol, rows in (unlocks or {}).items()
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


def _detect_unlock_soon(spec: DetectorSpec, ctx: TriageContext, symbol: str) -> TriageItem | None:
    """未来 N 个自然日内有解禁且合计**占总股本** ≥ ``min_ratio``（≥ ``risk_ratio`` 升为 risk）。

    三个"不猜"（spec §S11.2）：
    - 该股**没有 share_floats 行** → 返回 ``None``（缺数据，不是"没有解禁"）；
    - 解禁日 = ``ctx.as_of`` 当天 → **不算"未来"**（那天已经可流通；``as_of`` 是最近完整
      交易日，用户看到卡片时该解禁已经发生）；
    - 某一天**所有行**的 ``float_ratio`` 都缺失 → 无法核对"占比多少"，该日不判。
    """
    rows = ctx.unlocks.get(symbol) or []
    if not rows:
        return None
    days = int(spec.thresholds["days"])
    window_end = ctx.as_of + timedelta(days=days)
    # 按解禁日合计（同一日多个持有人/多笔 = 一次解禁事件）。装载层已按 (ts_code, float_date)
    # 聚合过一遍（把上万行压成 ≤N 天），这里再聚合一次：纯函数被单独调用时口径也一致。
    share_of: dict[date, float] = {}
    ratio_of: dict[date, float] = {}
    has_ratio: set[date] = set()
    for row in rows:
        if not (ctx.as_of < row.float_date <= window_end):
            continue
        day = row.float_date
        share_of[day] = share_of.get(day, 0.0) + (row.float_share or 0.0)
        if row.float_ratio is not None:
            ratio_of[day] = ratio_of.get(day, 0.0) + row.float_ratio
            has_ratio.add(day)
    # sorted → 同一天只有一个元素；跨天取"最近的一次"，排序键必须确定（可重放）
    hits = [
        (day, share_of[day], ratio_of[day])
        for day in sorted(has_ratio)
        if ratio_of[day] >= spec.thresholds["min_ratio"]
    ]
    if not hits:
        return None
    # 分级取窗口内**最高**一档，文案说那一档里**最近**的一次（用户先要知道最快到来的大额解禁）
    risk_hits = [hit for hit in hits if hit[2] >= spec.thresholds["risk_ratio"]]
    day, share, ratio = (risk_hits or hits)[0]
    # 源 ``float_share`` 是「股」，卡片按 spec §S11.2 的「万股」上屏 → /10000
    share_wan = share / 10_000.0
    facts: list[Fact] = [
        Fact("解禁股数", round(share_wan, 2), "万股"),
        Fact("占总股本比", round(ratio, 2), "%"),
        Fact("解禁日", day.strftime("%m-%d"), ""),
    ]
    if len(hits) > 1:
        facts.append(Fact(f"{days} 天内解禁", str(len(hits)), "次"))
    return TriageItem(
        symbol=symbol,
        name=_name(ctx, symbol),
        detector=spec.key,
        severity="risk" if risk_hits else "attention",
        headline=(
            f"{days} 天内解禁 {share_wan:,.0f} 万股（占总股本 {ratio:.1f}%），解禁日 {day:%m-%d}"
        ),
        facts=tuple(facts),
        as_of=ctx.as_of,
        source="share_floats",
        verify=spec.verify,
        day_pct=None,
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
    "unlock_soon": _detect_unlock_soon,
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
