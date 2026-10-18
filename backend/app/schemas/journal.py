"""决策日志的入参/响应契约（spec §S9.1 表单 · §S9.2 数据形状 · §S9.4 结果/归因）。

三条契约纪律：

1. **3 行必填**：``thesis``（≤200 字）· ``invalidation``（价格阈值或日期）·
   ``horizon_days ∈ {7,30,90}``；缺一 → 422，越界 → 422（不是默默纠正）。
2. **创建后不可改**：本模块**没有** Update 模型，路由层也没有 PATCH/PUT/DELETE
   （``tests/test_journal_api.py`` 用 grep 断言）。
3. **归因必选**：``verdict`` 是五枚举之一且**必填**；未选不得写入（422）。

``invalidation`` 用 pydantic 的**判别联合**而不是 ``dict``：三种形状的值语义完全不同
（价格是浮点、日期是 ISO 串），交给校验器比在服务里 ``if kind == ...`` 安全。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 与 ``app/models/journal.py`` 的 ``ENTRY_KINDS`` / ``HORIZON_CHOICES`` / ``VERDICTS``
#: 逐字一致（一致性由 ``tests/test_journal_api.py`` 对拍）。
JournalKind = Literal["watch", "buy", "sell", "pass", "hold"]
HorizonDays = Literal[7, 30, 90]
#: 赌的是什么（spec §S9.7.3）：决定基准口径，创建时选定、事后不可改。
#: 与 ``app/models/journal.py`` 的 ``BET_TYPES`` 逐字一致（测试对拍）。
BetType = Literal["direction", "relative", "market"]
Verdict = Literal[
    "thesis_wrong",
    "timing_wrong",
    "discipline_break",
    "right_for_wrong_reason",
    "luck",
]
EntryStatus = Literal["open", "due", "reviewed"]


class PriceInvalidation(BaseModel):
    """价格阈值：``price_below`` 跌到阈值（含）以下 / ``price_above`` 涨到阈值（含）以上。"""

    kind: Literal["price_below", "price_above"]
    value: float = Field(gt=0, description="价格阈值（元），必须 > 0")

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value}


class DateInvalidation(BaseModel):
    """日期阈值：只写"到哪天为止看结果"，到期即 ``invalidation_hit = true``。"""

    kind: Literal["date_only"]
    value: date = Field(description="日期（YYYY-MM-DD）")

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value.isoformat()}


Invalidation = Annotated[PriceInvalidation | DateInvalidation, Field(discriminator="kind")]


class JournalEntryCreate(BaseModel):
    """创建一条判断。**这 3 个字段必填**，其余可选（spec §S9.1）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    #: 留空 = 市场级判断（结果基准退化为沪深300）
    symbol: str | None = Field(default=None, max_length=16)
    #: 只作归档标签（``pass`` = "我决定不动"：计入判断统计、不计入收益统计）
    kind: JournalKind = "watch"
    #: 赌的是什么（spec §S9.7.3）：``direction`` 默认；``market`` = 市场级判断（基准沪深300）
    bet_type: BetType = "direction"
    thesis: str = Field(min_length=1, max_length=200)
    invalidation: Invalidation
    horizon_days: HorizonDays

    @field_validator("symbol")
    @classmethod
    def _blank_symbol_is_none(cls, value: str | None) -> str | None:
        return value or None


class JournalReviewCreate(BaseModel):
    """归因（**必选**）+ 可选 ``note``（≤500 字）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    verdict: Verdict
    note: str | None = Field(default=None, max_length=500)


class JournalReviewOut(BaseModel):
    """一次复盘（append-only）。``verdict`` 用 ``str``：历史行可能含未来新增枚举值。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    entry_id: int
    reviewed_at: datetime
    outcome_pct: float
    bench_pct: float
    excess_pct: float
    invalidation_hit: bool
    verdict: str
    note: str | None = None


class JournalEvaluationOut(BaseModel):
    """到期自动结果（**不写库**）：缺数时为 ``None`` 并在 ``missing_reason`` 说明原因。

    Phase A（spec §S9.7.2/§S9.7.3）**只加字段**：

    - ``net_outcome_pct`` = ``outcome_pct - 0.12``（12bp 往返成本，净口径默认展示）；
    - ``net_excess_pct`` = ``net_outcome_pct - bench_pct``；
    - ``bench_basis_note``：基准到底是什么（**人话**，后端下发，前端不硬编码）；
    - ``bench_fallback``：``True`` = 本来要行业基准但拿不到行业，已退化到沪深300。
    """

    due_on: date
    price_as_of: date
    outcome_pct: float | None = None
    bench_pct: float | None = None
    excess_pct: float | None = None
    #: ★ spec §S9.7.2：净口径（毛 − 12bp）。既有字段类型一个都没动
    net_outcome_pct: float | None = None
    net_excess_pct: float | None = None
    invalidation_hit: bool | None = None
    #: ★ spec §S9.7.3：基准的人话说明 + 是否退化（``bench_pct`` 的口径附注）
    bench_basis_note: str = ""
    bench_fallback: bool = False
    missing_reason: dict[str, str] = Field(default_factory=dict)


class JournalEntryOut(BaseModel):
    """一条判断（``snapshot`` 是**创建时**的不可变快照，原样下发）。"""

    id: int
    symbol: str | None
    kind: str
    #: ★ spec §S9.7.3：``direction`` / ``relative`` / ``market``（同时也在 snapshot.bench_spec）
    bet_type: str = "direction"
    thesis: str
    invalidation: dict[str, Any]
    horizon_days: int
    snapshot: dict[str, Any]
    created_at: datetime
    due_on: date
    status: str
    #: 列表行的一行快照摘要（后端拼的人话串；缺数只写有的那几段）
    snapshot_summary: str = ""
    review: JournalReviewOut | None = None
    #: 只有单条详情（``GET /journal/{id}``）会算：列表不逐条算，避免 N+1
    evaluation: JournalEvaluationOut | None = None


class JournalListOut(BaseModel):
    """列表 + 三态计数（角标用 ``snapshot_summary.due``，与截断无关）。"""

    items: list[JournalEntryOut]
    snapshot_summary: dict[str, int]
    #: 本次结果是否被 ``LIST_LIMIT`` 截断（截断不改变 ``snapshot_summary`` 的口径）
    truncated: bool
    status: str | None
    #: 固定免责（spec §S9.5 逐字）：由后端下发，前端不硬编码
    disclaimer: str


class WilsonIntervalOut(BaseModel):
    """一个比例的 **Wilson 95% 区间**（spec §S9.7.7）：点估计永远和区间一起出现。

    ``n = 0`` 时服务层返回 ``None``（整个对象缺省），不是 ``successes=0`` 的零区间。
    """

    n: int
    successes: int
    ci_low: float
    ci_high: float


class JournalStatsOut(BaseModel):
    """统计（spec §S9.4）：``by_attribution`` 合计恒等于 ``reviewed_n``。

    Phase A 新增（**既有字段类型一律不变**）：

    - ``by_kind``：五个 ``kind`` 各自的条数（合计 = ``n``；``pass`` 在这里有独立分栏）；
    - ``avg_net_excess_pct``：**净口径**平均超额（毛 − 12bp，且**排除 pass**）；
    - ``share_ci``：``discipline_break`` 与 ``win`` 两个比例的 Wilson 95% 区间；
    - ``net_cost_bps`` / ``multiple_comparison_note``：口径常量，前端原样展示。
    """

    n: int
    reviewed_n: int
    pending_n: int
    #: 五枚举 → 条数（**数值**；占比由前端用 ``count / reviewed_n`` 自己算）
    by_verdict: dict[str, int]
    #: 同一组计数的另一个名字（spec 的"合计 = reviewed_n"断言用它）
    by_attribution: dict[str, int]
    #: ★ ``kind`` 五枚举 → 条数（合计 = ``n``，spec §S9.7.6）
    by_kind: dict[str, int] = Field(default_factory=dict)
    #: 毛口径平均超额（排除 ``pass``）。默认展示用 ``avg_net_excess_pct``
    avg_excess_pct: float | None = None
    #: ★ 净口径平均超额（= 毛 − 12bp，排除 ``pass``）
    avg_net_excess_pct: float | None = None
    #: ``reviewed_n < 5`` 时为 ``None``（**不是 0**：0 意味着"样本够且没人破纪律"）
    discipline_break_share: float | None = None
    min_reviewed_for_share: int
    sample_sufficient: bool
    #: ★ 比例 → Wilson 95% 区间（``n = 0`` 时为 ``None``）
    share_ci: dict[str, WilsonIntervalOut | None] = Field(default_factory=dict)
    #: ★ 单边往返成本 bps（前端文案「已扣 12bp 往返成本」用它，不写死 12）
    net_cost_bps: int = 0
    #: ★ 固定标注（spec §S9.7.7 逐字）：未做多重比较校正
    multiple_comparison_note: str = ""
