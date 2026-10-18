"""决策日志的入参/响应契约（spec §S9.1 表单 · §S9.2 数据形状 · §S9.4 结果/归因）。

三条契约纪律：

1. **3 行必填**：``thesis``（≤200 字）· ``invalidation``（价格阈值或日期）·
   ``horizon_days ∈ {7,30,90}``；缺一 → 422，越界 → 422（不是默默纠正）。
2. **创建后不可改**：本模块**没有** Update 模型，路由层也没有 PATCH/PUT/DELETE
   （``tests/test_journal_api.py`` 用 grep 断言）。
3. **归因必选**：``verdict`` 是五枚举之一且**必填**；未选不得写入（422）。
4. **可证伪闸**（spec §S9.7.8）：``invalidation`` 只接受**可观测**三形态
   （``price_below`` / ``price_above`` / ``date_only``）。自由文本（"逻辑破坏"）在
   入参层就是 422 + 指导文案（``INVALIDATION_GUIDANCE``），不给"先建后判不可复盘"
   的模糊地带；历史行里已有的不可识别形态读取时标 ``reviewable = false``。

``invalidation`` 用 pydantic 的**判别联合**而不是 ``dict``：三种形状的值语义完全不同
（价格是浮点、日期是 ISO 串），交给校验器比在服务里 ``if kind == ...`` 安全。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 与 ``app/models/journal.py`` 的 ``ENTRY_KINDS`` / ``HORIZON_CHOICES`` / ``VERDICTS``
#: 逐字一致（一致性由 ``tests/test_journal_api.py`` 对拍）。
JournalKind = Literal["watch", "buy", "sell", "pass", "hold"]
HorizonDays = Literal[7, 30, 90]
#: 赌的是什么（spec §S9.7.3）：决定基准口径，创建时选定、事后不可改。
#: 与 ``app/models/journal.py`` 的 ``BET_TYPES`` 逐字一致（测试对拍）。
BetType = Literal["direction", "relative", "market"]
#: 风险单位分档（spec §S9.7.1）：``none`` = 只观察不下单（默认）。
#: 与 ``app/models/journal.py`` 的 ``POSITION_SIZES`` 逐字一致（测试对拍）。
PositionSize = Literal["none", "light", "normal", "heavy"]
#: 可观测的失效条件形态（spec §S9.7.8）：**唯一白名单**。
#: 与 ``app/models/journal.py`` 的 ``OBSERVABLE_INVALIDATION_KINDS`` 逐字一致（测试对拍）。
INVALIDATION_KINDS: tuple[str, ...] = ("price_below", "price_above", "date_only")
#: 自由文本失效条件的 422 指导文案（spec §S9.7.8 逐字意图）：告诉用户**怎么改**，
#: 而不是只说"校验失败"。后端下发，前端不另写一份。
INVALIDATION_GUIDANCE = (
    "失效条件必须是可观测的形态：price_below / price_above / date_only。"
    "请给一个价格或日期，或改用备注（note）写理由。"
)
#: 「我决定不动」与仓位互斥的 422 文案（spec §S9.7.1/§S9.7.6 的口径修正）。
PASS_POSITION_GUIDANCE = "我决定不动 = 只观察，不能同时选仓位"

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
    #: 风险单位（spec §S9.7.1）：``none`` 默认 = 只观察不下单；越界 422
    position_size: PositionSize = "none"
    #: 事前信心（spec §S9.7.5）：1..5 的整数；**不填 = 不参与校准**（不是 3 分）
    confidence: int | None = Field(default=None, ge=1, le=5)
    thesis: str = Field(min_length=1, max_length=200)
    invalidation: Invalidation
    horizon_days: HorizonDays

    @field_validator("symbol")
    @classmethod
    def _blank_symbol_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("invalidation", mode="before")
    @classmethod
    def _observable_invalidation_only(cls, value: Any) -> Any:
        """可证伪闸（spec §S9.7.8）：非三形态 → 422 + **指导文案**。

        为什么在联合校验**之前**拦：判别联合对 ``{"kind": "logic_broken"}`` 只会报
        "union_tag_invalid"（用户看不懂），而 spec 要求 422 时给出"请给一个价格或日期，
        或改用备注写理由"。判据只看 ``kind``（形状/值域仍交给下面的联合校验）。
        """
        kind = value.get("kind") if isinstance(value, dict) else getattr(value, "kind", None)
        if kind not in INVALIDATION_KINDS:
            raise ValueError(f"{INVALIDATION_GUIDANCE}（收到 kind={kind!r}）")
        return value

    @model_validator(mode="after")
    def _pass_means_no_position(self) -> JournalEntryCreate:
        """``kind=pass`` ⇒ ``position_size`` 强制 ``none``（spec §S9.7.1/§S9.7.6 修正）。

        口径理由：``pass`` = "我决定不动"（只观察），它没有可归因的收益；再让它带一个
        仓位（轻仓/标准/重仓）就是把"没做"混进"做了"的收益统计。既然 ``position_size``
        的默认值已经是 ``none``，"未传"天然满足"自动置 none"，这里只需要拦"传了非 none"。
        """
        if self.kind == "pass" and self.position_size != "none":
            raise ValueError(PASS_POSITION_GUIDANCE)
        return self


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
    - ``invalidation_observable``（spec §S9.7.8）：失效条件能否被行情/日期客观判定。
      三形态 = ``True``；历史行里的不可识别形态 = ``False``（该条不可复盘）。

    Phase B2（spec §S9.7.4 路径分析）**再加四个字段**（全部可选、缺省 = 不适用/缺数）：

    - ``mae_pct``：入场到到期期间的最大不利偏移（%），**≤ 0**（没有不利偏移就是 0）；
    - ``first_touch_invalidation``：首次触达失效条件的交易日；未触达/``date_only`` 为 ``None``；
    - ``touched_then_reversed``：先触达失效条件、到期净收益反而 > 0。缺证据一律 ``False``；
    - ``days_to_invalidation``：入场日之后到首次触达日的**交易日数**；未触达为 ``None``。

    深度优化（契约 §1 `.superpowers/sdd/journal-deep/contract.md`）**再加两字段**：

    - ``earnings_in_window``：``(快照日, due_on]`` 内有没有财报披露日。**三态**：
      ``True`` 有（结果可能被财报事件主导）/ ``False`` 有财报数据但窗口内没有 /
      ``None`` 该股没有任何带公告日的财报记录（缺数据，不猜）。PIT：只用
      ``ann_date <= due_on`` 的记录判断；
    - ``earnings_note``：人话（"窗口内含 1 次财报披露（10-15），结果可能被财报事件主导"）；
      ``False`` / ``None`` 时是**空串**（没有事件就没有提示）。

    spec §S11.3 解禁窗口（与财报窗口同构）**再加两字段**：

    - ``unlock_in_window``：``(快照日, due_on]`` 内有没有**占流通/总股本 ≥3%** 的解禁日。
      三态：``True`` 有 / ``False`` 有解禁记录但窗口内没有（或都 <3%）/ ``None`` 该股没有
      PIT 可见的解禁记录（缺数据，不猜"不会解禁"）；
    - ``unlock_note``：人话（"窗口内含 1 次解禁（11-02，占总股本 4.2%），结果可能被解禁
      冲击主导"）；``False`` / ``None`` 时是空串。
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
    #: ★ spec §S9.7.4 路径四指标（只加，缺省 = 不适用/缺数）
    mae_pct: float | None = None
    first_touch_invalidation: date | None = None
    touched_then_reversed: bool = False
    days_to_invalidation: int | None = None
    #: ★ 契约 §1 财报窗口：``True`` 窗口内有披露 / ``False`` 有数据但不在窗口 /
    #: ``None`` 无财报数据（缺数据，不猜）。三态**不可合并**：``None`` 不是"没有财报"。
    earnings_in_window: bool | None = None
    #: ★ 契约 §1 财报窗口的人话说明（``False`` / ``None`` 时为空串）
    earnings_note: str = ""
    #: ★ spec §S11.3 解禁窗口（三态；``None`` 只写降级键，不阻塞复盘）
    unlock_in_window: bool | None = None
    #: ★ spec §S11.3 解禁窗口的人话说明（``False`` / ``None`` 时为空串）
    unlock_note: str = ""
    #: ★ spec §S9.7.3：基准的人话说明 + 是否退化（``bench_pct`` 的口径附注）
    bench_basis_note: str = ""
    bench_fallback: bool = False
    #: ★ spec §S9.7.8：失效条件可观测 → ``True``；不可观测 → ``False``（≠ 结果算不出来）
    invalidation_observable: bool = True
    missing_reason: dict[str, str] = Field(default_factory=dict)


class JournalEntryOut(BaseModel):
    """一条判断（``snapshot`` 是**创建时**的不可变快照，原样下发）。

    Phase B1（spec §S9.7.1/§S9.7.5/§S9.7.8）**只加字段**：

    - ``position_size`` / ``confidence``：创建时选定的风险单位与事前信心（原样回读）；
    - ``reviewable``：失效条件可观测 = ``True``；``False`` 时前端**不提供**复盘入口
      （端点也会拒绝：不可复核的判断不该被"归因"）。
    """

    id: int
    symbol: str | None
    kind: str
    #: ★ spec §S9.7.3：``direction`` / ``relative`` / ``market``（同时也在 snapshot.bench_spec）
    bet_type: str = "direction"
    #: ★ spec §S9.7.1：``none`` / ``light`` / ``normal`` / ``heavy``（历史行由迁移回填 ``none``）
    position_size: str = "none"
    #: ★ spec §S9.7.5：1..5；``None`` = 当时没填（不参与校准）
    confidence: int | None = None
    #: ★ spec §S9.7.8：``False`` = 失效条件不可观测，不提供复盘入口
    reviewable: bool = True
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
    #: ★ spec §S9.6.4：**创建那一刻**的同类历史（同快照里那份，原样下发）。
    #: 老行（s9.r1/r2）没有它 → ``None``（"当时没有这段信息"，不补算）。
    peer_history: dict[str, Any] | None = None


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


class JournalCalibrationBinOut(BaseModel):
    """校准曲线的一档（spec §S9.7.5）：``n = 0`` 的档**不出现**（不是假数据点）。

    ``p`` 是 :data:`app.services.journal_service.CALIBRATION_P` 里的**固定**先验
    （``{1:0.2, 2:0.35, 3:0.5, 4:0.65, 5:0.8}``），不是按历史结果拟合出来的。
    """

    confidence: int
    #: 该档的固定先验概率（后端算，前端不重算）
    p: float
    #: 该档"已复盘且填了信心"的条数
    n: int
    #: 实际命中率 = ``净超额 > 0`` 的比例（**不是毛收益**）
    hit_rate: float
    #: ``n >= 10`` 才 ``True``（该档样本不足 → 前端写"样本不足"）
    sufficient: bool


class JournalCalibrationOut(BaseModel):
    """校准（spec §S9.7.5）：总样本 ``n < 20`` 时服务层给 ``None`` + ``calibration_note``。"""

    n: int
    bins: list[JournalCalibrationBinOut] = Field(default_factory=list)
    #: ``mean((p - I[net_excess>0])**2)``；无样本时为 ``None``
    brier: float | None = None


class JournalKindMedianOut(BaseModel):
    """一类归档标签的净超额中位（spec §S9.7.6）。

    ``count`` 是**中位数的样本数**（已复盘的条数），与 ``by_kind`` 的"全部条数"
    不是同一件事——``by_kind`` 的既有形状（纯数字）保持不变。
    """

    count: int
    median_net_excess_pct: float | None = None


class JournalConsistencyOut(BaseModel):
    """归因自洽率（契约 §2 `.superpowers/sdd/journal-deep/contract.md`）。

    自洽 = "我归类的原因"与"净超额的符号"方向一致：

    - ``thesis_wrong`` / ``timing_wrong`` → 自洽当且仅当 ``net_excess_pct < 0``；
    - ``discipline_break`` → **恒自洽**（纪律问题与盈亏独立）；
    - ``right_for_wrong_reason`` / ``luck`` → 自洽当且仅当 ``net_excess_pct > 0``。

    ``n`` 是**分母**：已复盘且净超额可算的条数。``n < 5`` 时 ``share`` / ``ci_low`` /
    ``ci_high`` 一律 ``None``（只给 ``n``）—— 4 条样本上的百分比会被读成"我很一致"。
    区间复用 :func:`app.services.journal_service.wilson_interval`（spec §S9.7.7），
    不另造一套算法。``note`` 是契约 §2 的**逐字**免责文案，前端原样展示、不改写。
    """

    n: int
    consistent: int
    #: ``consistent / n``；``n < 5`` 时为 ``None``（不是 0）
    share: float | None = None
    #: Wilson 95% 区间（``n < 5`` 时为 ``None``）
    ci_low: float | None = None
    ci_high: float | None = None
    #: 契约 §2 逐字（不自洽 ≠ 说谎）
    note: str


class JournalStatsOut(BaseModel):
    """统计（spec §S9.4）：``by_attribution`` 合计恒等于 ``reviewed_n``。

    Phase A 新增（**既有字段类型一律不变**）：

    - ``by_kind``：五个 ``kind`` 各自的条数（合计 = ``n``；``pass`` 在这里有独立分栏）；
    - ``avg_net_excess_pct``：**净口径**平均超额（毛 − 12bp，且**排除 pass 与「只观察」**）；
    - ``share_ci``：``discipline_break`` 与 ``win`` 两个比例的 Wilson 95% 区间；
    - ``net_cost_bps`` / ``multiple_comparison_note``：口径常量，前端原样展示。

    Phase B2 新增（spec §S9.7.1/§S9.7.5/§S9.7.9，**只加字段**）：

    - ``by_size``：四个仓位档各自的条数（合计 = ``n``）；
    - ``avg_weighted_excess_pct``：按 ``size_weight`` 加权（``none`` 排除，权重 0）；
    - ``by_kind_median``：每类的 ``{count, median_net_excess_pct}``；
    - ``calibration`` / ``calibration_note``：校准曲线（总样本 < 20 → ``None`` + 原因）；
    - ``export_hint``：导出地址，前端直接用（spec §S9.7.9）；
    - ``exclusion_note``：收益统计的排除口径（``pass`` 与「只观察」都不计入），
      前端**原样展示**，不改写（spec §S9.7.1/§S9.7.6 口径修正）。

    深度优化（契约 §2）**再加一个字段**：``consistency``（归因自洽率，见
    :class:`JournalConsistencyOut`）。它只加字段，既有字段的类型一个都没动。
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
    #: ★ 毛口径平均超额（排除 ``pass``）。默认展示用 ``avg_net_excess_pct``
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
    #: ★ spec §S9.7.1 仓位分栏（四枚举 → 条数，合计 = ``n``）
    by_size: dict[str, int] = Field(default_factory=dict)
    #: ★ spec §S9.7.1 按仓位加权超额（``none`` 排除；无样本时 ``None``）
    avg_weighted_excess_pct: float | None = None
    #: ★ spec §S9.7.6 每类净超额中位（``by_kind`` 的既有纯数字形状不变）
    by_kind_median: dict[str, JournalKindMedianOut] = Field(default_factory=dict)
    #: ★ spec §S9.7.5 校准（总样本 < 20 → ``None``）
    calibration: JournalCalibrationOut | None = None
    #: ★ 校准样本不足时的原因（够样本时为 ``None``）
    calibration_note: str | None = None
    #: ★ spec §S9.7.9 导出地址（前端直接用，不写死路径）
    export_hint: str = ""
    #: ★ spec §S9.7.1/§S9.7.6 收益统计的排除口径（``pass`` 与「只观察」都不计入）
    exclusion_note: str = ""
    #: ★ 契约 §2 归因自洽率（``n < 5`` 时只给 ``n``，比例与区间为 ``None``）
    consistency: JournalConsistencyOut | None = None
