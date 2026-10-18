"""自选体检的响应契约（**只读**，无写模型；零新表）。

字段与前端 ``frontend/src/shared/api/triage.ts`` 的 ``TriageResponse`` 一一对应：
``disclaimer`` / ``unavailable`` 都由后端给，前端不硬编码（避免合规文案两处漂移）。

**两条契约纪律**（集成收尾时补的，防"机器串上屏"与"三种空态撞车"）：

1. ``unavailable`` 是**人话 label**（「所属板块在动」），机器 key 只在 ``unavailable_keys``；
2. ``state`` 显式区分第四态（``no_watchlist`` / ``no_data`` / ``ok``）——前端不得再靠
   ``items=[] & quiet_count=0`` 猜"用户没自选"，那会把"今天数据没就绪"渲染成"去添加自选"。
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

#: 分级只用这四枚举（spec §S8.5）：risk / event / attention / background。
#: 与 ``triage_detectors.SEVERITIES`` 的一致性由 ``tests/test_watchlist_triage_api.py``
#: 机械校验（两处漂移 = 响应里冒出第五种分级，前端 `??` 兜底会把它静默画成"背景"）。
TriageSeverity = Literal["risk", "event", "attention", "background"]

#: 页面状态：ok = 有自选且有最近完整交易日；no_data = 有自选但拿不到完整交易日；
#: no_watchlist = 用户一只自选都没有。与 ``triage_service.STATES`` 的一致性有测试对拍
#: （漏一个前端就会落进"猜"的分支，把"没数据"画成"没自选"）。
TriageState = Literal["ok", "no_data", "no_watchlist"]


class FactOut(BaseModel):
    """一条事实：``label`` + ``value`` + ``unit``（例：收盘 / 11.20 / 元）。"""

    label: str
    value: float | str
    unit: str = ""


class TriageItemOut(BaseModel):
    """一条体检结论：只陈述事实 + 数据截至 + 复核去处 + **规则来源**。

    ``rule_text`` / ``rule_version`` / ``source_table`` 是 provenance 契约的三字段
    （见 ``.superpowers/sdd/triage-provenance/contract.md``）：卡片右上角据此显示
    "依据哪条规则（哪个版本）、数据来自哪张表"，口径改版后历史卡片仍可解释。
    """

    symbol: str
    name: str
    detector: str
    severity: TriageSeverity
    headline: str
    facts: list[FactOut]
    as_of: date
    source: str
    verify: str
    #: 人话阈值（与 ``GET /watchlists/triage/rules`` 的 ``rule_text`` 逐字一致）
    rule_text: str
    #: 规则版本（如 ``r1``）：阈值/判据变更时必须升版
    rule_version: str
    #: 主要来源表（如 ``daily_quotes``）
    source_table: str


class TriageOut(BaseModel):
    """``GET /api/v1/watchlists/triage`` 的完整载荷。"""

    as_of: date
    #: 页面状态（见 ``TriageState``）：前端据此选文案，**不**从 items/quiet_count 反推
    state: TriageState
    #: 今日无命中的自选只数（``state='ok'`` 且 ``items=[]`` 且 ``quiet_count>0`` = 全静默日）
    quiet_count: int
    #: 超出单页上限被折叠的条数（>0 时前端提示"另有 N 项未展示"）
    folded: int
    items: list[TriageItemOut]
    #: 缺数据而未执行的探测器 —— **人话 label**（spec §S8.4：不得静默当作无事件）。
    #: 上屏只许用这个字段；它可能是空列表，但绝不含机器 key。
    unavailable: list[str]
    #: 与 ``unavailable`` 一一对应的探测器 key（**排查用**：日志/工单里定位是哪条检查没跑）。
    #: 前端**禁止**渲染本字段——key 形如 ``sector_move``，上屏就是机器串。
    unavailable_keys: list[str]
    disclaimer: str


class TriageStatsOut(BaseModel):
    """一条规则的标定统计（``scripts/build_triage_rule_stats.py`` 生成，静态 JSON 读出）。

    字段名逐字照抄 provenance 契约：``tN_excess_med`` = 各信号日「信号组中位收益 −
    当日全市场中位收益」的跨日中位数（%），``t5_p05`` = 信号组 5 日收益的 5% 分位（%）。
    **只放事实统计，不放预测/评分**（合规扫描面覆盖 ``watchlist_triage.py``）。
    """

    as_of: date
    window_months: int
    signals: int
    days: int
    per_day: float
    t1_excess_med: float | None
    t5_excess_med: float | None
    t20_excess_med: float | None
    t5_p05: float | None
    #: 口径说明（超额怎么算、收益怎么复权、样本限制）
    basis: str


class TriageRuleOut(BaseModel):
    """一条探测器的来源 + 统计（``GET /api/v1/watchlists/triage/rules`` 的数组项）。

    ``stats=null`` 是**合法且必须**的表达（统计产物缺失/源表覆盖不足/窗口内无信号），
    此时 ``stats_note`` 必须写原因——静默给一个空对象会让前端把"没算"画成"没影响"。
    """

    key: str
    label: str
    severity: TriageSeverity
    version: str
    rule_text: str
    definition: str
    thresholds: dict[str, float]
    source_tables: list[str]
    update_note: str
    failure_modes: list[str]
    stats: TriageStatsOut | None = None
    stats_note: str | None = None


class TriageRulesOut(BaseModel):
    """``GET /api/v1/watchlists/triage/rules`` 的完整载荷（固定 8 条，registry 顺序）。"""

    rules: list[TriageRuleOut]
