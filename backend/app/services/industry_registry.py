"""Industry metric registry — 投研工作台的单一事实源.

指标定义 / 数据源分级 / 参考区间 / 展示分组 / 周期阶段 / 仓位模板全部在此声明，
API 与前端组件均由其驱动：新增一列、换一个预警阈值、接入一个新行业 = 改配置，
零前端改动、零新表。metric_key 命名与 docs/design/data-source.md 对齐。

registry 同时是**未登记 key 的拒绝面**：metric_key 不在此登记即被读/写路径拒绝
（`IndustryConfig.metric` 与 `find_metric` 都是白名单查询）。

`industry_key` 语义已泛化为通用 scope 键（spec §S1.2）：INDUSTRIES 既登记**行业**
scope（'pig' / 'broiler'），也登记**非行业** scope（'market' = 全市场情绪，
spec §S1.8，2026-10-12 落地）—— 两者落进同一张 `industry_metrics` 表并复用
`GET /api/v1/metrics/*` 读面，**没有第二张指标表、没有第二套读路**。

注意 `IndustryConfig` 是**行业 + 全市场共用**的容器：非行业 scope 的
`sw_l3_codes` / `phases` / `position_templates` 留空（'market' 即如此），
只有 `metrics` 与 `reference_points` 对它有意义。消费方若假定"每个 scope 都是行业"
（如数 `sw_l3_codes` 或取相位），必须先看该 scope 是否行业 scope。

**"是不是行业"有唯一判据**：:attr:`IndustryConfig.is_industry`（= `sw_l3_codes`
非空，即该 scope 挂了申万三级分类）。`INDUSTRIES` 的 key 数是**scope 数**（既有行业
也有全市场），不是行业数 —— 徽标的「行业分类（申万）」分母与
`GET /api/v1/industries`（"已产品化行业列表"）都必须用 `is_industry` 过滤，
否则全市场情绪会被当成一个"行业"计数/上架（Ruling 54 第 4 条）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# ── 数据源层级（UI 徽章 + 权威性裁决） ────────────────────────────────
TIER_OFFICIAL = "official"  # 官方基准：农业农村部/发改委/统计局/交易所
TIER_HIGHFREQ = "highfreq"  # 高频参考：Mysteel/涌益/卓创/生意社等
TIER_CALC = "calc"  # 测算：基于公开数据推算（如头均市值）
TIER_MANUAL = "manual"  # 人工录入：公告/纪要/年报
TIER_DERIVED = "derived"  # 派生：由基础指标计算（猪粮比等）

TIER_LABELS: dict[str, str] = {
    TIER_OFFICIAL: "官方基准",
    TIER_HIGHFREQ: "高频参考",
    TIER_CALC: "测算",
    TIER_MANUAL: "人工录入",
    TIER_DERIVED: "派生",
}

FREQ_LABELS: dict[str, str] = {
    "daily": "日度",
    "weekly": "周度",
    "monthly": "月度",
    "quarterly": "季度",
    "yearly": "年度",
}


@dataclass(frozen=True)
class WarnBand:
    """预警阈值带：value <= upper 属于该档（upper=None 表示最后开放档）。"""

    upper: float | None
    label: str
    severity: str = "warn"  # info | warn | danger


@dataclass(frozen=True)
class MetricDef:
    key: str
    name: str
    unit: str
    freq: str
    tier: str
    sources: list[str]  # ingest/查询源优先级（高→低）；mock 永远垫底，演示数据不得压过真实源
    group: str = "quick"  # strip | quick | supply | cost | company（公司级，stock_id>0）
    strip: bool = False  # 进入综合指标带
    spark: bool = False  # 指标带附迷你走势
    higher_is_better: bool | None = None  # 涨跌颜色语义；None=中性
    rollup_monthly: bool = False  # 日度指标按"每月最后一个日度值"补一条月度行（source 不变）
    warn_bands: list[WarnBand] = field(default_factory=list)
    # 通用 mock 基准值：generic mock builder 据此生成抖动序列（未配置则该指标跳过）
    mock_base: float | None = None
    # 口径版本登记位（spec §S1.2）：上游口径修订时在此声明当前口径。
    # None = 该指标尚无口径修订（读面按行的 caliber_version 回传，两者不一致即上游
    # 口径断点，由消费方提示）。本期只要求登记位存在，**不预填具体口径**。
    caliber_version: str | None = None
    description: str = ""

    def band_label(self, value: float) -> str | None:
        for band in sorted(self.warn_bands, key=lambda b: (b.upper is None, b.upper or 0)):
            if band.upper is None or value <= band.upper:
                return band.label
        return None


@dataclass(frozen=True)
class PhaseDef:
    key: str
    label: str
    desc: str


@dataclass(frozen=True)
class PositionSlice:
    name: str
    role: str
    desc: str
    pct: int
    color: str


@dataclass(frozen=True)
class ReferencePointDef:
    metric_key: str
    label: str
    value: float
    effective_from: date
    note: str = ""


@dataclass(frozen=True)
class IndustryConfig:
    """一个 scope 的登记项（现按行业登记；`key` 语义已泛化为通用 scope 键）."""

    key: str
    name: str
    description: str
    sw_l3_codes: list[str]
    metrics: list[MetricDef]
    phases: list[PhaseDef]
    position_templates: dict[str, list[PositionSlice]]
    reference_points: list[ReferencePointDef] = field(default_factory=list)
    # ── 行情面标的（P5）：TuShare fund_daily / cb_daily 逐代码回补 ──
    etf_codes: list[str] = field(default_factory=list)
    cb_codes: list[str] = field(default_factory=list)
    securities_names: dict[str, str] = field(default_factory=dict)  # code → 展示名

    def metric(self, key: str) -> MetricDef | None:
        return next((m for m in self.metrics if m.key == key), None)

    @property
    def is_industry(self) -> bool:
        """本 scope 是否**行业** scope（唯一判据：挂了非空申万三级分类）。

        为什么不用别的判据：`phases` / `position_templates` 在"已策展但尚未配相位"
        的行业上也可能为空，用它会把真行业误判成非行业；`sw_l3_codes` 是**申万行业
        归属**的最小充分证据（行业 scope 必有、全市场 scope 必无）。徽标
        「行业分类（申万）」的 `curated_count` 分母与 `GET /api/v1/industries`
        （"已产品化行业列表"）都用它，避免把 `market` 数成行业（Ruling 54 第 4 条）。
        """
        return bool(self.sw_l3_codes)

    @property
    def strip_metrics(self) -> list[MetricDef]:
        return [m for m in self.metrics if m.strip]

    @property
    def quick_metrics(self) -> list[MetricDef]:
        return [m for m in self.metrics if m.group == "quick"]

    def position_template(self, signal_type: str) -> list[PositionSlice]:
        return self.position_templates.get(signal_type, self.position_templates.get("关注", []))


# ── 因子层（spec §S2.1；不建表、不建 factor_defs） ────────────────────
#
# `MetricDef` 描述**一条序列**（id 元数据），`FactorDef` 描述**一次变换**
# （`(序列, window, params) → 序列` 的元数据）。两者是不同层次，故**分表登记**
# 而不是塞进 `IndustryConfig.metrics`——否则 `find_metric` 白名单会把因子函数
# 当成可读指标，`/api/v1/metrics/latest` 会去查一张永远没有它的表。
#
# 因子实现**只在** `app/services/indicators.py`（纯函数）；本处只登记元数据。
# 阈值/窗口若与实现分离，一律住这里（与 `cycle_engine` 的既有纪律一致：
# "阈值调整改 registry 而非逻辑文件"）。

GROUP_MOMENTUM = "momentum"  # 动量/变化
GROUP_TREND = "trend"  # 趋势/均线
GROUP_RISK = "risk"  # 风险/波动
GROUP_VALUATION = "valuation"  # 分位/估值位

FACTOR_GROUPS: dict[str, str] = {
    GROUP_MOMENTUM: "动量与变化",
    GROUP_TREND: "趋势与均线",
    GROUP_RISK: "风险与波动",
    GROUP_VALUATION: "分位与估值位",
}

# 物化状态：低成本的**不落库**（读时算 + Redis 缓存）；只有昂贵的（长窗口 /
# 跨全市场 / 逐标的每日）才物化。物化列名对齐 `app/models/feature.py`。
FACTOR_STORAGE_READ_TIME = "read_time"  # 读时算
FACTOR_STORAGE_MATERIALIZED = "materialized"  # 物化落 stock_features 既有列


@dataclass(frozen=True)
class FactorDef:
    """一个因子的登记项（`factor_key → {名称, 单位, 输入指标, 窗口, 展示分组}`）.

    `func` 是 `indicators.py` 里的纯函数名（**字符串**，不是函数对象）：
    registry 与实现**解耦**——registry 不被 import 循环绑住，函数改名在此一处改，
    而 `tests/test_indicators_registry.py` 机械校验该名字真的存在且可调用。

    `input_metrics` 是因子消费的 `metric_key`（可多个：如 `feed_meat_ratio` 之类
    有待接入）；空元组 = 因子只吃"数值序列"，不对上具体指标（分位/波动率/均线
    对任何序列都成立，绑死一个 metric_key 会把它降级成单指标实现）。
    `input_metrics` 里**非空**的键必须已在 `INDUSTRIES` 注册（`validate_factors` 校验）。

    `window` 是默认窗口（与 registry 其它定义一致：**口径住这里**，不写死在函数里）；
    `window_param` 命名该窗口在函数签名里的参数（`window` / `periods_per_year` /
    `long_window`…），`None` = 函数**无窗口参数**（全序列口径，如 `drawdown` /
    `max_drawdown` —— 它们在 `stock_feature_row` 里按调用方窗口裁剪）。

    `params` 登记**其余**口径参数（如 `periods_per_year=252`、`normalize=True`）：
    纯函数签名里的默认值就是函数级兜底，本字段是**因子级**口径（口径住 registry）。
    `window` **不得**与函数签名默认值矛盾（`test_declared_window_matches_function_default`
    机械校验）；`params` 同理（`test_declared_params_match_function_defaults`）。
    """

    key: str
    name: str
    unit: str  # "" = 无量纲（比值/分位/布尔）
    func: str  # indicators.py 中的纯函数名
    input_metrics: tuple[str, ...] = ()
    window: int | None = None
    window_param: str | None = "window"
    params: tuple[tuple[str, Any], ...] = ()  # 其余口径参数（名字, 值），不可变
    group: str = GROUP_TREND
    freq: str = "daily"  # 因子随输入序列的频率走；此处登记默认/典型频率
    storage: str = FACTOR_STORAGE_READ_TIME
    materialized_column: str | None = None  # 仅 storage=materialized 时非空
    higher_is_better: bool | None = None
    description: str = ""

    def param_dict(self) -> dict[str, Any]:
        """`params` 的字典视图（消费方按关键字展开调用纯函数时用）。"""
        return dict(self.params)


def _factor(
    key: str,
    name: str,
    unit: str,
    func: str,
    *,
    input_metrics: tuple[str, ...] = (),
    window: int | None = None,
    window_param: str | None = "window",
    params: tuple[tuple[str, Any], ...] = (),
    group: str = GROUP_TREND,
    freq: str = "daily",
    storage: str = FACTOR_STORAGE_READ_TIME,
    materialized_column: str | None = None,
    higher_is_better: bool | None = None,
    description: str = "",
) -> FactorDef:
    return FactorDef(
        key=key,
        name=name,
        unit=unit,
        func=func,
        input_metrics=input_metrics,
        window=window,
        window_param=window_param,
        params=params,
        group=group,
        freq=freq,
        storage=storage,
        materialized_column=materialized_column,
        higher_is_better=higher_is_better,
        description=description,
    )


#: 因子登记表 —— `factor_key → FactorDef`。键与 `metric_key` **共用同一命名空间**
#: 且不得撞名（`validate_factors` 校验）：撞名会让 `grep` 与前端映射双双失效。
FACTORS: dict[str, FactorDef] = {
    f.key: f
    for f in [
        # ── 读时算：低成本，不落库（Redis 缓存在读面） ──────────────────
        _factor(
            "pct_rank",
            "滚动分位",
            "",
            "percentile_rank",
            window=252,
            group=GROUP_VALUATION,
            higher_is_better=False,
            description=(
                "窗口内当前值的历史分位（0–1，越高越极端）。nearest-rank / 无插值，"
                "只用有效样本。低成本 → 读时算；长窗口（如 1260 日情绪分位）才物化进 "
                "stock_features.extra，不新增列"
            ),
        ),
        _factor(
            "zscore_20d",
            "20 日 z-score",
            "",
            "zscore",
            window=20,
            group=GROUP_VALUATION,
            higher_is_better=None,
            description="偏离 20 日均值多少个总体标准差；窗口内无波动 → NULL（不是 0）",
        ),
        _factor(
            "yoy",
            "同比",
            "",
            "yoy",
            window=12,
            window_param="periods_per_year",
            freq="monthly",
            group=GROUP_MOMENTUM,
            higher_is_better=True,
            description=(
                "与 12 期前比，返回比值（0.05 = +5%）。窗口即回看期数（月度序列=去年同月），"
                "落在函数参数 periods_per_year 上"
            ),
        ),
        _factor(
            "mom",
            "环比",
            "",
            "mom",
            window=1,
            window_param="lag",
            freq="monthly",
            group=GROUP_MOMENTUM,
            higher_is_better=True,
            description="与上期比，返回比值（0.05 = +5%）；基期为 0 → NULL，不算 inf",
        ),
        _factor(
            "ma_20d",
            "20 日均线",
            "",  # 单位随输入序列，registry 不另立量纲
            "moving_average",
            window=20,
            group=GROUP_TREND,
            description=(
                "简单移动平均；要求窗口内无缺失（与市场情绪 above_ma20_ratio 的"
                "窗口完备守卫同义）。单位随输入序列：本因子登记为无量纲是**有意**的——"
                "写死 '元/kg' 会让同一因子用在成交量上时单位变谎言"
            ),
        ),
        _factor(
            "volatility_60d",
            "60 日年化波动率",
            "",
            "annualized_volatility",
            window=60,
            params=(("periods_per_year", 252),),
            group=GROUP_RISK,
            higher_is_better=False,
            description="窗口内一阶收益率的总体标准差 × sqrt(252)，返回比值（0.28 = 28%）",
        ),
        _factor(
            "drawdown",
            "逐点回撤",
            "",
            "drawdown",
            window=None,
            window_param=None,
            group=GROUP_RISK,
            higher_is_better=False,
            description="(x − 历史最高) / 历史最高，范围 [-1, 0]，逐位对齐输入序列",
        ),
        _factor(
            "trend_slope_60d",
            "60 日趋势斜率",
            "",
            "trend_slope",
            window=60,
            group=GROUP_TREND,
            higher_is_better=True,
            description=(
                "窗口内 OLS 斜率 ÷ 窗口均值（无量纲，跨量级可比）；对应 stock_features.trend_slope"
            ),
        ),
        _factor(
            "ma_bullish",
            "均线多头排列",
            "",
            "ma_bullish",
            window=60,
            window_param="long_window",
            group=GROUP_TREND,
            higher_is_better=True,
            description="MA(short) > MA(long) 的布尔序列（严格大于；等于不算多头）",
        ),
        _factor(
            "consecutive_negative",
            "连续为负期数",
            "期",
            "count_consecutive_negative",
            window=None,
            window_param=None,
            group=GROUP_MOMENTUM,
            higher_is_better=False,
            description=(
                "尾部连续严格为负的期数。**复用 cycle_engine.count_consecutive_negative**"
                "（spec §S2.1 点名），本层不另写一份"
            ),
        ),
        # ── 物化：昂贵（逐标的 × 逐 asof_date），落 stock_features 既有列 ──
        _factor(
            "return_percentile",
            "收益分位",
            "",
            "percentile_rank",
            window=60,
            group=GROUP_VALUATION,
            storage=FACTOR_STORAGE_MATERIALIZED,
            materialized_column="return_percentile",
            higher_is_better=False,
            description=(
                "逐标的每日物化（跨全市场 × 长历史是昂贵侧）。**复用既有 "
                "stock_features.return_percentile 列**"
                "（uq_stock_features_key 冲突即更新，重跑幂等）"
            ),
        ),
        _factor(
            "max_drawdown",
            "最大回撤",
            "",
            "max_drawdown",
            window=None,
            window_param=None,
            group=GROUP_RISK,
            storage=FACTOR_STORAGE_MATERIALIZED,
            materialized_column="max_drawdown",
            higher_is_better=False,
            description=(
                "窗口内最大回撤（负数）。纯函数是全序列口径（window=None → 整条序列）；"
                "物化时按 stock_features.window_days 裁剪（见 indicators.stock_feature_row）。"
                "复用既有 stock_features.max_drawdown 列"
            ),
        ),
        _factor(
            "trend_slope",
            "趋势斜率",
            "",
            "trend_slope",
            window=60,
            group=GROUP_TREND,
            storage=FACTOR_STORAGE_MATERIALIZED,
            materialized_column="trend_slope",
            higher_is_better=True,
            description="窗口内归一化 OLS 斜率。复用既有 stock_features.trend_slope 列",
        ),
        _factor(
            "annual_volatility",
            "年化波动率",
            "",
            "annualized_volatility",
            window=60,
            group=GROUP_RISK,
            storage=FACTOR_STORAGE_MATERIALIZED,
            materialized_column="annual_volatility",
            higher_is_better=False,
            description="窗口内一阶收益率年化标准差。复用既有 stock_features.annual_volatility 列",
        ),
    ]
}


def get_factor(factor_key: str) -> FactorDef | None:
    """因子白名单查询（与 `find_metric` 同构）：未登记因子返回 None."""
    return FACTORS.get(factor_key)


def validate_factors() -> None:
    """登记自洽性校验（纯函数，无 IO；由 `tests/test_indicators_registry.py` 调用）。

    逐条查 registry 自身**可判定**的三件事——这些都是"抄错即静默失效"的类型：

    1. 因子键**不得与 metric_key 撞名**（共用命名空间）
    2. `input_metrics` 里的键必须**已在 INDUSTRIES 注册**（否则因子引用了
       不存在的指标，读时才发现）
    3. `group` 取值必须在 `FACTOR_GROUPS` 内；`storage` 取值必须是两个常量之一；
       `storage=materialized` ⟺ `materialized_column` 非空
    4. `window_param` 与 `params` 的名字**不得重复**（同一个参数两处给值 → 谁生效靠顺序，
       是口径漂移的入口）

    实现侧的名字/签名是否存在由单测校验（registry 不 import indicators，
    以免形成 import 环）。
    """
    metric_keys = {m.key for cfg in INDUSTRIES.values() for m in cfg.metrics}
    for key, f in FACTORS.items():
        if key != f.key:
            raise ValueError(f"FACTORS 键 {key!r} 与 FactorDef.key {f.key!r} 不一致")
        if key in metric_keys:
            raise ValueError(f"factor_key {key!r} 与已注册 metric_key 撞名（命名空间冲突）")
        for mk in f.input_metrics:
            if mk not in metric_keys:
                raise ValueError(f"因子 {key!r} 引用了未注册指标 {mk!r}")
        if f.group not in FACTOR_GROUPS:
            raise ValueError(f"因子 {key!r} 的展示分组 {f.group!r} 未登记")
        if f.storage not in (FACTOR_STORAGE_READ_TIME, FACTOR_STORAGE_MATERIALIZED):
            raise ValueError(f"因子 {key!r} 的 storage {f.storage!r} 非法")
        if (f.storage == FACTOR_STORAGE_MATERIALIZED) != bool(f.materialized_column):
            raise ValueError(
                f"因子 {key!r}：storage=materialized 必须给 materialized_column，反之亦然"
            )
        param_names = [name for name, _ in f.params]
        if len(param_names) != len(set(param_names)):
            raise ValueError(f"因子 {key!r} 的 params 里有重名参数: {param_names}")
        if f.window_param is not None and f.window_param in param_names:
            raise ValueError(
                f"因子 {key!r}：{f.window_param!r} 同时出现在 window_param 与 params 里"
            )


# ── 信号类型 ─────────────────────────────────────────────────────────
SIGNAL_EMPTY = "空仓"
SIGNAL_WATCH = "关注"
SIGNAL_BUY = "买入"
SIGNAL_SELL = "卖出"
SIGNAL_COLORS: dict[str, str] = {
    SIGNAL_BUY: "#ef4444",  # 红涨绿跌惯例：做多=红
    SIGNAL_SELL: "#22c55e",
    SIGNAL_WATCH: "#faad14",
    SIGNAL_EMPTY: "#8c8c8c",
}


def _position_slices(core: int, band: int, cash: int) -> list[PositionSlice]:
    return [
        PositionSlice(
            "核心底仓", "做周期 · 长持", "成本与现金流领先的头部企业，穿越周期持有", core, "#1677ff"
        ),
        PositionSlice(
            "波段仓位", "做波动 · 择时", "畜牧 ETF / 高 β 龙头，跟随信号加减", band, "#faad14"
        ),
        PositionSlice(
            "现金储备", "底部加仓弹药", "货基 / 国债逆回购，预留极端行情", cash, "#94a3b8"
        ),
    ]


# ── 生猪养殖（猪智投） ───────────────────────────────────────────────
PIG_METRICS: list[MetricDef] = [
    MetricDef(
        key="hog_price",
        name="生猪均价",
        unit="元/kg",
        freq="daily",
        tier=TIER_HIGHFREQ,
        sources=["akshare_soozhu", "mock"],
        group="quick",
        strip=True,
        spark=True,
        higher_is_better=True,
        rollup_monthly=True,
        description="全国生猪出栏均价；官方批发价为基准，本值用于跟踪边际变化",
    ),
    MetricDef(
        key="corn_price",
        name="玉米价格",
        unit="元/kg",
        freq="daily",
        tier=TIER_HIGHFREQ,
        sources=["akshare_soozhu", "mock"],
        group="quick",
        higher_is_better=True,
        rollup_monthly=True,
        description="饲料成本端主要原料",
    ),
    MetricDef(
        key="soybean_meal_price",
        name="豆粕价格",
        unit="元/kg",
        freq="daily",
        tier=TIER_HIGHFREQ,
        sources=["akshare_soozhu", "mock"],
        group="quick",
        higher_is_better=True,
        description="饲料成本端蛋白原料",
    ),
    MetricDef(
        key="pork_wholesale",
        name="猪肉批发价",
        unit="元/kg",
        freq="daily",
        tier=TIER_OFFICIAL,
        sources=["manual", "mock"],
        group="quick",
        higher_is_better=True,
        description="农业农村部全国农产品批发市场猪肉均价",
    ),
    MetricDef(
        key="piglet_price_15kg",
        name="仔猪价格（15kg）",
        unit="元/kg",
        freq="weekly",
        tier=TIER_HIGHFREQ,
        sources=["manual", "mock"],
        group="quick",
        higher_is_better=True,
        description="补栏情绪的先行指标",
    ),
    MetricDef(
        key="lh_future_main",
        name="生猪期货主力",
        unit="元/吨",
        freq="daily",
        tier=TIER_OFFICIAL,
        sources=["akshare_sina", "mock"],
        group="quick",
        strip=True,
        spark=True,
        higher_is_better=True,
        description="DCE 生猪期货主力连续，远月价格反映市场对未来供需的预期",
    ),
    MetricDef(
        key="hog_corn_ratio",
        name="猪粮比",
        unit="",
        freq="daily",
        tier=TIER_DERIVED,
        sources=["derived"],
        group="quick",
        strip=True,
        spark=True,
        warn_bands=[
            WarnBand(5.0, "一级预警", "danger"),
            WarnBand(6.0, "二级预警", "warn"),
            WarnBand(9.0, "正常", "info"),
            WarnBand(None, "过度上涨", "danger"),
        ],
        description="生猪价格/玉米价格，行业盈亏核心指标（自算口径，与发改委周度口径略有差异）",
    ),
    MetricDef(
        key="sow_inventory",
        name="能繁母猪存栏",
        unit="万头",
        freq="monthly",
        tier=TIER_OFFICIAL,
        sources=["stats_gov", "caaa", "xuantian", "mock"],
        group="supply",
        strip=True,
        spark=True,
        description="农业农村部月度环比 + 统计局季度末绝对数，产能最终基准（10 个月生产时滞）",
    ),
    MetricDef(
        key="pork_output",
        name="猪肉产量",
        unit="万吨",
        freq="quarterly",
        tier=TIER_OFFICIAL,
        sources=["xuantian", "mock"],
        group="supply",
        description="统计局季度/年度猪肉产量，供给端总量基准（玄田镜像）",
    ),
    MetricDef(
        key="hog_inventory",
        name="生猪存栏",
        unit="万头",
        freq="quarterly",
        tier=TIER_OFFICIAL,
        sources=["xuantian", "mock"],
        group="supply",
        description="统计局季度末生猪存栏绝对数，产能存量基准（玄田镜像）",
    ),
    MetricDef(
        key="hog_slaughter_quarterly",
        name="生猪出栏量",
        unit="万头",
        freq="quarterly",
        tier=TIER_OFFICIAL,
        sources=["xuantian", "mock"],
        group="supply",
        description="统计局季度生猪出栏量，供给流量指标（玄田镜像）",
    ),
    MetricDef(
        key="sow_inventory_mom",
        name="能繁存栏环比",
        unit="%",
        freq="monthly",
        tier=TIER_DERIVED,
        sources=["derived"],
        group="supply",
        higher_is_better=None,
        description="由能繁存栏序列计算，连续为负即产能去化",
    ),
    MetricDef(
        key="industry_cost_avg",
        name="行业平均完全成本",
        unit="元/kg",
        freq="monthly",
        tier=TIER_MANUAL,
        sources=["manual", "mock"],
        group="cost",
        description="协会调研/研报口径，季度更新后线性插值为月度",
    ),
    MetricDef(
        key="msy",
        name="MSY（行业均值）",
        unit="头/年",
        freq="yearly",
        tier=TIER_MANUAL,
        sources=["manual", "mock"],
        group="quick",
        higher_is_better=True,
        description="每头能繁母猪年提供出栏肥猪数",
    ),
    MetricDef(
        key="psy",
        name="PSY（行业均值）",
        unit="头",
        freq="yearly",
        tier=TIER_MANUAL,
        sources=["manual", "mock"],
        group="quick",
        higher_is_better=True,
        description="每头能繁母猪年提供断奶仔猪数",
    ),
    MetricDef(
        key="feed_meat_ratio",
        name="料肉比",
        unit="",
        freq="yearly",
        tier=TIER_MANUAL,
        sources=["manual", "mock"],
        group="quick",
        higher_is_better=False,
        description="消耗饲料/增重，越低效率越高",
    ),
    # ── 公司级指标（标的分析，P5）：stock_id>0 落表，companies 端点按此下发列 ──
    MetricDef(
        key="company.hogs_sold_monthly",
        name="月度出栏量",
        unit="万头",
        freq="monthly",
        tier=TIER_MANUAL,
        sources=["manual"],
        group="company",
        description="公司月度商品猪出栏量（销售简报/月度经营公告）",
    ),
    MetricDef(
        key="company.cost_complete",
        name="完全成本",
        unit="元/kg",
        freq="quarterly",
        tier=TIER_MANUAL,
        sources=["manual"],
        group="company",
        higher_is_better=False,
        description="公司养殖完全成本（季报/调研纪要口径）",
    ),
    MetricDef(
        key="mcap_per_head",
        name="头均市值",
        unit="元/头",
        freq="monthly",
        tier=TIER_CALC,
        sources=["derived"],
        group="company",
        higher_is_better=False,
        description="最新总市值 / 年化出栏量（测算）——生猪股跨周期估值锚；"
        "历史分位需积累派生行后开放",
    ),
]

PIG_INDUSTRY = IndustryConfig(
    key="pig",
    name="生猪养殖",
    description="猪智投 · 农林牧渔-养殖业-生猪养殖（申万Ⅲ级）",
    # 申万Ⅲ 生猪养殖 = 110702（docs/references/sw/申万行业分类.md · 2021新增，9 只成分股；
    # 110301 为林业Ⅲ —— 建表时误抄，2026-09-03 随 P5 companies 端点实测修正）
    sw_l3_codes=["110702"],
    metrics=PIG_METRICS,
    phases=[
        PhaseDef("prosperity", "繁荣", "猪价高位 · 产能扩张 · 二育活跃"),
        PhaseDef("recession", "衰退", "猪价下行 · 利润收窄 · 产能惯性增长"),
        PhaseDef("depression", "萧条", "全行业亏损 · 产能去化 · 政策托底"),
        PhaseDef("recovery", "复苏", "供给收缩 · 猪价回升 · 盈利修复"),
    ],
    position_templates={
        SIGNAL_EMPTY: _position_slices(0, 10, 90),
        SIGNAL_WATCH: _position_slices(50, 30, 20),
        SIGNAL_BUY: _position_slices(60, 30, 10),
        SIGNAL_SELL: _position_slices(20, 30, 50),
    },
    reference_points=[
        ReferencePointDef(
            "sow_inventory",
            "正常保有量",
            4100,
            date(2021, 1, 1),
            "《生猪产能调控实施方案（2021）》",
        ),
        ReferencePointDef(
            "sow_inventory",
            "正常保有量",
            3900,
            date(2024, 3, 1),
            "《生猪产能调控实施方案（2024年修订）》",
        ),
        ReferencePointDef(
            "sow_inventory",
            "正常保有量",
            3750,
            date(2026, 1, 1),
            "《生猪产能综合调控实施方案（2026年修订）》",
        ),
    ],
    # ── 行情面标的（2026-09-03 TuShare 实机核验）──────────────────────
    # ETF：159865.SZ 国泰中证畜牧养殖ETF（畜牧养殖 ETF 中规模/流动性最佳，
    # fund_basic 实测名"国泰中证畜牧养殖ETF"，上市 2021-03-08）。
    etf_codes=["159865.SZ"],
    # 可转债：cb_basic 按猪智投 9 只成分股正股名（stk_short_name）过滤、
    # 仅保留在市（delist_date 为空）的转债。已退市：希望转债 127015.SZ
    # （2026-01-05 摘牌）、正邦转债 128114.SZ（2024-01-31，正邦重整）。
    # 牧原转债 127045.SZ ← 牧原股份；温氏转债 123107.SZ ← 温氏股份；
    # 希望转2 127049.SZ ← 新希望。
    cb_codes=["127045.SZ", "123107.SZ", "127049.SZ"],
    securities_names={
        "159865.SZ": "国泰中证畜牧养殖ETF",
        "127045.SZ": "牧原转债",
        "123107.SZ": "温氏转债",
        "127049.SZ": "希望转2",
    },
)

# ── 白羽肉鸡（泛化验证演示行业：mock-only，零前端改动跑通 列表→工作台） ──
# 刻意保持最小：2 个 mock 价格指标 + 通用四阶段相位 + 通用仓位模板，
# 证明"接入新行业 = 写配置"的设计原则（无 fetcher / 无 scheduler / 无新表）。
BROILER_METRICS: list[MetricDef] = [
    MetricDef(
        key="chick_price",
        name="鸡苗价格",
        unit="元/羽",
        freq="daily",
        tier=TIER_HIGHFREQ,
        sources=["mock"],
        mock_base=2.9,
        group="quick",
        strip=True,
        spark=True,
        higher_is_better=True,
        description="白羽肉鸡苗出厂价，补栏情绪先行指标（演示数据源，未接真实源）",
    ),
    MetricDef(
        key="broiler_price",
        name="毛鸡价格",
        unit="元/kg",
        freq="daily",
        tier=TIER_HIGHFREQ,
        sources=["mock"],
        mock_base=7.4,
        group="quick",
        strip=True,
        spark=True,
        higher_is_better=True,
        description="白羽肉鸡毛鸡收购价，养殖盈亏核心指标（演示数据源，未接真实源）",
    ),
]

BROILER_INDUSTRY = IndustryConfig(
    key="broiler",
    name="白羽肉鸡",
    description="肉鸡投研 · 农林牧渔-养殖业-肉鸡养殖（申万Ⅲ级）· 泛化验证演示行业（mock 数据源）",
    # 申万Ⅲ 肉鸡养殖 = 110703（三源核验 2026-09-03：backend/data/sw_seed.sql +
    # docs/references/sw/申万行业分类.md + 库内 live tree，7 只成分股，与生猪 110702 不相交）
    sw_l3_codes=["110703"],
    metrics=BROILER_METRICS,
    # 复用通用四周期键位（与 cycle_engine 阶段 key 对齐），描述按肉鸡行业口径改写
    phases=[
        PhaseDef("prosperity", "繁荣", "鸡价高位 · 补栏积极 · 苗价强势"),
        PhaseDef("recession", "衰退", "鸡价下行 · 养殖利润收窄 · 出栏惯性增加"),
        PhaseDef("depression", "萧条", "全行业亏损 · 种蛋转商 · 产能去化"),
        PhaseDef("recovery", "复苏", "供给收缩 · 鸡价回升 · 盈利修复"),
    ],
    position_templates={
        SIGNAL_EMPTY: _position_slices(0, 10, 90),
        SIGNAL_WATCH: _position_slices(50, 30, 20),
        SIGNAL_BUY: _position_slices(60, 30, 10),
        SIGNAL_SELL: _position_slices(20, 30, 50),
    },
)

# ── 全市场情绪 scope：'market'（spec §S1.8 情绪/市场宽度与动量）────────────
#
# 这是 `industry_key` 泛化为通用 scope 键后的**第一个非行业 scope**（S1.2 Ruling 16
# 就是为它准备的）：6 个 S1.8 指标 + 2 个底层家数指标全部落在同一张 `industry_metrics`
# 表、走同一个 `GET /api/v1/metrics/*` 读面，**不为情绪另建表或另开读路**。
#
# 计算与落库在 `app/services/market_sentiment_service.py`；四徽章里的 `source` 恒为
# `local_calc`（与 `market_sentiment_daily.source` 同值 —— 产地标签与底座保持一致，
# 不另造一个只在读面出现的源名），`unit` / `freq` / `tier` 由本表登记。
MARKET_SENTIMENT_SOURCE = "local_calc"
#: 口径版本：**fix round 1 bump 到 s1.8-v2** —— v1 把 `pct_chg IS NULL` 的行按市场
#: 分布的 `None → 0~1%` 行为记成"上涨"（2025-09-23 落库 `market_advance_count=5382`），
#: v2 在分桶前剔除 NULL 行、加覆盖率门槛（`partial_pct_chg`）并改 `extra` 形状。
#: v1 的行与 v2 **不可混用**（ADR 0011 第 4 条：口径变更 bump + 留断点）。
MARKET_CALIBER_VERSION = "s1.8-v2"

#: `extra` 里带 `missing_reason` 的缺数语义。**缺数恒 NULL，绝不填 0**（spec §S1.8
#: 验收：涨停类指标的"缺数日"不能静默为 0）——0 在"涨跌停家数"上是"今天只有 0 家
#: 涨停"这一**事实断言**，与"这天没有数据"完全相反。
MARKET_METRIC_MISSING_REASONS: dict[str, str] = {
    "no_sentiment_day": "该交易日 market_sentiment_daily 无行（涨停事实底座未回补或未采集）",
    "source_null": "底座该字段为 NULL（上游口径下该日该值不可得）",
    "no_quote_rows": "该交易日 daily_quotes 无行（行情底座缺失）",
    "no_ma20_window": "无任何标的具备完整的 20 日窗口（上市不足 20 个交易日或 20 日窗口未取满）",
    "partial_pct_chg": (
        "当日取回行的可算 pct_chg 覆盖率低于门槛（extra.coverage < extra.min_coverage），"
        "涨跌家数与涨跌比整组落 NULL —— 分母被缺失数据掏空后，任何数字都不是该日的市场事实"
    ),
}

MARKET_METRICS: list[MetricDef] = [
    # ── ① 涨跌家数（同源：与市场模块 A股涨跌分布共用 market_snapshot_service 的一日取行）──
    MetricDef(
        key="market_advance_count",
        name="上涨家数",
        unit="家",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=True,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "全市场当日**有 pct_chg 的**（`pct_chg IS NOT NULL`）标的中，收盘涨跌幅落入"
            "市场分布上涨桶（`0~1%` / `1~3%` / `3~5%` / `>5%` / `涨停`）的家数。"
            "注意 `0~1%` 桶含 `pct_chg == 0` 的平盘，故本值 = 符号上涨（`pct_chg > 0`）"
            "**加上**平盘；两者之差落 `extra.flat_in_positive_bucket` 可复核。"
            "`pct_chg IS NULL`（无行情行/未采集）的家数**既不计涨也不计跌**，"
            "落 `extra.unavailable_pct_chg`；可算覆盖率 `extra.coverage` 低于门槛时"
            "本指标整组落 NULL（`missing_reason='partial_pct_chg'`），不拿缺失数据拼数字"
        ),
    ),
    MetricDef(
        key="market_decline_count",
        name="下跌家数",
        unit="家",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=False,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "全市场当日**有 pct_chg 的**标的中，收盘涨跌幅落入市场分布下跌桶"
            "（`0~-1%` / `-1~-3%` / `-3~-5%` / `-5~-7%` / `>-7%` / `跌停`）的家数"
            "（等价于 `pct_chg < 0`）；平盘（`pct_chg == 0`）在上涨侧的 `0~1%` 桶里，"
            "**不计入本值**。`pct_chg IS NULL` 的行**既不计涨也不计跌**，其家数落"
            "`extra.unavailable_pct_chg`；覆盖率低于门槛时整组落 NULL"
            "（`missing_reason='partial_pct_chg'`）"
        ),
    ),
    # ── ② 涨跌比 ─────────────────────────────────────────────────────────
    MetricDef(
        key="advance_decline_ratio",
        name="涨跌比",
        unit="",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=True,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "上涨家数 / 下跌家数（无量纲比值，口径同为市场分布桶之和，见上涨/下跌家数）。"
            "分母为 0（全市场无下跌）时落 NULL，不落 0 也不落无穷；`extra` 同时带"
            "上涨/下跌/平盘家数、不可用家数 `unavailable_pct_chg` 与可用覆盖率"
            "`coverage` 可复核；`pct_chg IS NULL` 的行不计涨也不计跌，覆盖率低于门槛时"
            "落 NULL（`missing_reason='partial_pct_chg'`，`value` 不落任何由缺失数据拼出的数字）"
        ),
    ),
    # ── ③ 涨跌停比（底座：market_sentiment_daily.zt_count / dt_count，直接读不重算）──
    MetricDef(
        key="limit_up_down_ratio",
        name="涨跌停比",
        unit="",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=True,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "涨停家数 / 跌停家数，直接读 market_sentiment_daily 的 zt_count / dt_count。"
            "跌停家数为 0 时落 NULL（不落无穷）"
        ),
    ),
    # ── ④ 突破 20 日均线占比（新算：全仓此前无 MA20）─────────────────────
    MetricDef(
        key="above_ma20_ratio",
        name="站上 20 日均线占比",
        unit="%",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=True,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "收盘价 > 该股截至当日的 20 日简单均线（含当日、前 19 个交易日）的标的占"
            "**具备完整 20 日窗口**标的的比例，算术均值取存储列 close。"
            "窗口不足 20 个交易日的标的整只剔除（不进分子也不进分母）——"
            "新股按字面口径永远是 0%，会把新上市高峰期误读成情绪冰点。"
            "窗口是「最近 20 **行**行情」而非「20 个连续交易日」，长停牌股的一个窗口"
            "可能横跨 60+ 个日历日，故 `extra` 记录当日最坏的"
            "`max_window_span_days`（窗口跨度）、`max_gap_days`（窗口内最大相邻间隔）"
            "与 `span_over_alert_days`（跨度超过 `span_alert_days`=40 天的合格标的家数）"
        ),
    ),
    # ── ⑤ 炸板率（底座：market_sentiment_daily.broken_rate，直接读不重算）──
    MetricDef(
        key="limit_up_break_ratio",
        name="炸板率",
        unit="%",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=False,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "炸板家数 / 触板家数，直接读 market_sentiment_daily.broken_rate 并"
            "×100 归一到百分数（库内该列为 0–1 小数）"
        ),
    ),
    # ── ⑥ 连板高度（底座：market_sentiment_daily.max_streak）────────────
    MetricDef(
        key="max_consecutive_limit_up",
        name="连板高度",
        unit="板",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=True,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "当日全市场最高连续涨停板数（截至该交易日），直接读 "
            "market_sentiment_daily.max_streak。定义：连续涨停的首日为 1 板，"
            "每多一个连续交易日 +1；当日未收盘涨停即归零。"
            "最高连板标的代码在该行 `extra.max_streak_symbol`（非本指标值）"
        ),
    ),
    # ── ⑦ 首板晋级率（底座：market_sentiment_daily.promo_1to2）───────────
    MetricDef(
        key="first_board_promotion_ratio",
        name="首板晋级率",
        unit="%",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_SENTIMENT_SOURCE],
        group="breadth",
        higher_is_better=True,
        caliber_version=MARKET_CALIBER_VERSION,
        description=(
            "昨日首板股中今日继续涨停的比例，直接读 market_sentiment_daily.promo_1to2 "
            "并×100 归一到百分数。定义：首板 = 截至昨日连板数恰为 1 的标的；"
            "分母（昨日首板家数 promo_1to2_n）在该行 `extra.denominator`，为 0 时落 NULL"
        ),
    ),
]

MARKET_SENTIMENT = IndustryConfig(
    key="market",
    name="全市场情绪",
    description=(
        "全市场 scope（非行业）：情绪/市场宽度与动量（spec §S1.8）。"
        "指标值与行业指标同表同读面，代码即配置"
    ),
    sw_l3_codes=[],  # 非行业 scope：不挂申万分类
    metrics=MARKET_METRICS,
    phases=[],  # 周期判定是行业工作台的产物，全市场 scope 不参与
    position_templates={},
)


INDUSTRIES: dict[str, IndustryConfig] = {
    PIG_INDUSTRY.key: PIG_INDUSTRY,
    BROILER_INDUSTRY.key: BROILER_INDUSTRY,
    MARKET_SENTIMENT.key: MARKET_SENTIMENT,
}


def get_industry(key: str) -> IndustryConfig | None:
    return INDUSTRIES.get(key)


def get_industry_scopes() -> list[IndustryConfig]:
    """`INDUSTRIES` 里 ``is_industry`` 为真的 scope（**行业** scope，顺序 = 登记序）。

    这是"行业数"的**唯一**分母：徽标的「行业分类（申万）」`curated_count` 与
    `GET /api/v1/industries`（"已产品化行业列表"）都必须用它，而不是
    ``len(INDUSTRIES)`` —— 后者数的是 scope 数，含全市场情绪 `market`（Ruling 54 第 4 条）。
    通用读面/因子校验要**全部** scope 时请直接用 `INDUSTRIES`。
    """
    return [cfg for cfg in INDUSTRIES.values() if cfg.is_industry]


def find_metric(metric_key: str) -> tuple[str, MetricDef] | None:
    """全局 metric_key 白名单查询：返回 (登记它的 scope key, 定义)，未登记返回 None.

    通用读面 ``/api/v1/metrics/latest`` 按 metric_key 跨 scope 取数时用它做**未登记
    key 拒绝**（spec §S1.2 验收：未登记 metric_key 被 registry 拒绝）。同一 metric_key
    若被多个 scope 登记（如未来 'market' 与行业共用日频指标），返回首个登记 scope ——
    定义（单位/频率/源优先级）必须一致，否则属于登记冲突。
    """
    for scope_key, cfg in INDUSTRIES.items():
        m = cfg.metric(metric_key)
        if m is not None:
            return scope_key, m
    return None
