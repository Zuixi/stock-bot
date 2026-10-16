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
    # 事件驱动（spec §S1.7 的产业资本增减持：一行 = 一次公告事件，不是等间隔序列）
    "event": "事件驱动",
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

# ── 全市场交易活跃度 / 杠杆 / 估值（spec §S1.9 + §S1.11）──────────────────
#
# 与 S1.8 同一个 scope 键（`industry_key='market'`）：S1.9 的 11 个 + S1.11 的 2 个
# （外加 ERP 的 PE 底座 `pe_csi300`）全部落进同一张 `industry_metrics`、走同一个
# `GET /api/v1/metrics/*` 读面。**没有第二张表、没有第二套读路。**
#
# 计算与落库在 `app/services/market_activity_service.py`；Step-0 实机结论见
# `docs/design/data-source.md` 附.7。
#
# v2（Ruling 59 fix round 1）与 v1 的差异：① `erp_10y` / `cn_treasury_yield_10y` 的
# **落库天**由「`daily_quotes` 交易日」改为**自身输入的可得性交集**（中债曲线日 ∩
# 沪深300 PE 日）—— ERP = 1/PE − 10Y 与 `daily_quotes` 无关，v1 因此被无关输入截断在
# 829 天（3.4 年 < spec §S1.11 的「≥5 年」），v2 起为 1259 天（5.2 年）；
# ② 自建换手率的增量缓存改为**口径感知**（只认当前口径的行），命中的 (key, day)
# **不再重写**——v1 的重跑会把库里的好值覆盖成 `source_empty`(NULL)（"有值/空值抖动"
# 的根因）；③ 落库前新增 PIT 守卫（缺 `published_at`/`caliber_version` 即响亮失败）。
# 两版的可比值**逐值相同**（公式未变），变的是覆盖窗口与写入纪律。
MARKET_ACTIVITY_CALIBER_VERSION = "s1.9s1.11-v2"

#: 两融口径的产地（TuShare `margin` = 交易所官方汇总，三所求和）。
MARGIN_SOURCE = "tushare_margin"
#: 全市场成交额的产地（本地聚合 `daily_quotes.amount`）。
MARKET_TURNOVER_AMOUNT_SOURCE = "local_calc"
#: 10Y 国债收益率的产地（中债官方曲线；备源东财镜像，两源同日逐值相同）。
TREASURY_YIELD_SOURCE = "chinabond_yield_curve"
#: 指数日度指标的产地（5 个直取指数换手率的来源）。
INDEX_DAILYBASIC_SOURCE = "tushare_index_dailybasic"
#: 自建指数换手率的产地（成分权重 × 成分股 daily_basic）。
SELF_BUILT_TURNOVER_SOURCE = "self_built_index_turnover"
#: ERP 的 PE 底座产地（沪深300 整体法 PE）。
ERP_PE_SOURCE = "tushare_index_dailybasic"

#: §S1.9 / §S1.11 的 `extra.missing_reason` 缺数语义。**缺数恒 NULL，绝不填 0**：
#: 0 元融券余额是"今天真的没有融券"这一事实断言，与"这天没有数据"完全相反。
MARKET_ACTIVITY_MISSING_REASONS: dict[str, str] = {
    "no_quote_rows": "该交易日 daily_quotes 无行（行情底座缺失）",
    "source_empty": "上游当日/该区间没有任何值（未发布、未采集或本地无该日行情）",
    "index_not_in_source": (
        "该指数不在 index_dailybasic 的覆盖范围（实测 北证50/科创综指 返回 0 行且不报错）"
    ),
    "no_weight_snapshot": (
        "该交易日之前没有任何 index_weight 月度快照（**是上游快照的起点，不是指数的成立日**："
        "科创综指自身日线可回溯到基日 2019-12-31，缺的只是成分权重快照）"
    ),
    "partial_weight_coverage": (
        "自建指数的权重覆盖率低于门槛（extra.weight_coverage < 0.95）—— 参与未达标，"
        "不拿缺失成分拼出一个换手率"
    ),
    "partial_member_coverage": "自建指数的当日成分行情覆盖率不足",
    "treasury_yield_missing": "该交易日 10Y 国债收益率缺数（中债曲线/东财镜像未发布）",
    "pe_missing": "该交易日沪深300 整体法 PE 缺数（index_dailybasic 未发布或未回补）",
    "zero_turnover_denominator": (
        "该交易日全市场成交额为 0（分母为 0）—— 融资买入额占比数学上无定义，落 NULL"
    ),
}

#: 换手率口径（spec §S1.9 硬性验收判据）：**自由流通股本**。
TURNOVER_CALIBER_NOTE = (
    "换手率口径 = **自由流通股本**：直取的 5 个指数取 index_dailybasic.turnover_rate_f"
    "（上游自身定义 = 成交量 / 自由流通股本，free_share），不做二次换算；自建的 2 个指数"
    "（北证50 / 科创综指）取成分股 daily_basic.turnover_rate_f 的权重加权平均"
    "（权重 = index_weight.weight）。总股本口径 turnover_rate **不落库**，"
    "只在 extra.turnover_rate_total 留痕供对账。"
)


def _turnover_def(
    key: str, name: str, code: str, *, source: str, tier: str, note: str
) -> MetricDef:
    """7 个指数换手率的共同形状（口径文案统一，避免 7 份会腐烂的复制）。"""
    return MetricDef(
        key=key,
        name=name,
        unit="%",
        freq="daily",
        tier=tier,
        sources=[source],
        group="activity",
        higher_is_better=None,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            f"{note}（{code}）。**换手率口径 = 自由流通股本**（详见同组其它指标与 "
            "docs/design/metric-dictionary.md）。" + TURNOVER_CALIBER_NOTE
        ),
    )


MARKET_ACTIVITY_METRICS: list[MetricDef] = [
    # ── ① 全市场成交额（spec §S1.9「交易活跃度」）──────────────────────
    MetricDef(
        key="market_turnover_amount",
        name="全市场成交额",
        unit="亿元",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_TURNOVER_AMOUNT_SOURCE],
        group="activity",
        higher_is_better=True,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            "当日 `daily_quotes.amount`（TuShare `daily.amount`，原生单位千元）全市场求和，"
            "落库换算为**亿元**。只统计 `amount IS NOT NULL` 的行；`extra.n_rows` 记录参与"
            "统计的标的家数。当日无行情行时**不落行**（不拿 0 伪造一天「全市场 0 元成交」）"
        ),
    ),
    # ── ② 7 个指数换手率（spec §S1.9 逐字列出；口径：自由流通股本）──────
    _turnover_def(
        "idx_turnover_ratio_sse",
        "上证指数换手率",
        "000001.SH",
        source=INDEX_DAILYBASIC_SOURCE,
        tier=TIER_OFFICIAL,
        note="上证指数（000001.SH）自由流通股本换手率，直取 index_dailybasic",
    ),
    _turnover_def(
        "idx_turnover_ratio_szse",
        "深证成指换手率",
        "399001.SZ",
        source=INDEX_DAILYBASIC_SOURCE,
        tier=TIER_OFFICIAL,
        note="深证成指（399001.SZ）自由流通股本换手率，直取 index_dailybasic",
    ),
    _turnover_def(
        "idx_turnover_ratio_bse50",
        "北证50换手率",
        "899050.BJ",
        source=SELF_BUILT_TURNOVER_SOURCE,
        tier=TIER_DERIVED,
        note=(
            "北证50（899050.BJ）自由流通股本换手率。**index_dailybasic 不覆盖该指数"
            "（返回 0 行且不报错）**，故自建：成分股 daily_basic.turnover_rate_f 按 "
            "index_weight 权重加权平均（权重取该日或之前最近一期月度快照，无前视偏差）"
        ),
    ),
    _turnover_def(
        "idx_turnover_ratio_star",
        "科创综指换手率",
        "000680.SH",
        source=SELF_BUILT_TURNOVER_SOURCE,
        tier=TIER_DERIVED,
        note=(
            "科创综指（000680.SH）自由流通股本换手率。**index_dailybasic 不覆盖该指数**，"
            "故与北证50同法自建（成分权重 × 成分股 daily_basic.turnover_rate_f）。"
            "⚠️ **序列起点 = 上游 index_weight 快照起点 2025-09-30**（实测 13 期）——"
            "这是**源侧限制、不是该指数的成立日**：该指数自身日线可回溯到基日 2019-12-31"
            "（TuShare `index_basic`：base_date=20191231、list_date=20250120；"
            "`index_daily` 实测自 2019-12-31 起 1639 行），缺的只是成分权重快照。"
            "故本 key 当前**短于 3 年**，属**可由上游补齐**的缺口（不是结构性不可能），"
            "缺头的每一天都带 missing_reason='no_weight_snapshot'"
        ),
    ),
    _turnover_def(
        "idx_turnover_ratio_chinext",
        "创业板指换手率",
        "399006.SZ",
        source=INDEX_DAILYBASIC_SOURCE,
        tier=TIER_OFFICIAL,
        note="创业板指（399006.SZ）自由流通股本换手率，直取 index_dailybasic",
    ),
    _turnover_def(
        "idx_turnover_ratio_sse50",
        "上证50换手率",
        "000016.SH",
        source=INDEX_DAILYBASIC_SOURCE,
        tier=TIER_OFFICIAL,
        note="上证50（000016.SH）自由流通股本换手率，直取 index_dailybasic",
    ),
    _turnover_def(
        "idx_turnover_ratio_csi300",
        "沪深300换手率",
        "000300.SH",
        source=INDEX_DAILYBASIC_SOURCE,
        tier=TIER_OFFICIAL,
        note="沪深300（000300.SH）自由流通股本换手率，直取 index_dailybasic",
    ),
    # ── ③ 杠杆资金三件套（spec §S1.9「杠杆资金」）──────────────────────
    MetricDef(
        key="margin_financing_balance",
        name="融资余额",
        unit="亿元",
        freq="daily",
        tier=TIER_OFFICIAL,
        sources=[MARGIN_SOURCE],
        group="leverage",
        higher_is_better=None,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            "融资余额（TuShare `margin.rzye`，原生单位元）**三所求和**（SSE + SZSE + BSE），"
            "落库换算为**亿元**。产地是交易所汇总口径（不是个股明细求和）；"
            "逐所拆分落 `extra.by_exchange_yuan` 留痕"
        ),
    ),
    MetricDef(
        key="margin_financing_buy_ratio",
        name="融资买入额占比",
        unit="%",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARGIN_SOURCE],
        group="leverage",
        higher_is_better=None,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            "融资买入额 / 全市场成交额 × 100。分子 = TuShare `margin.rzmre` 三所求和"
            "（原生单位元），分母 = 当日 `daily_quotes.amount` 全市场求和（亿元）。"
            "分母缺失或为 0 → 落 NULL（**不落 0、不落无穷**）；分子分母都落 `extra` 可复核"
        ),
    ),
    MetricDef(
        key="margin_short_balance",
        name="融券余额",
        unit="亿元",
        freq="daily",
        tier=TIER_OFFICIAL,
        sources=[MARGIN_SOURCE],
        group="leverage",
        higher_is_better=None,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            "融券余额（TuShare `margin.rqye`，原生单位元）**三所求和**，落库换算为**亿元**。"
            "融券余额为 0 与缺数严格区分：0 是事实（当天真的没有融券余额），"
            "缺数落 NULL + `extra.missing_reason`"
        ),
    ),
    # ── ④ 10Y 国债收益率（spec §S1.11 的无风险利率底座）────────────────
    MetricDef(
        key="cn_treasury_yield_10y",
        name="10年期国债收益率",
        unit="%",
        freq="daily",
        tier=TIER_OFFICIAL,
        sources=[TREASURY_YIELD_SOURCE],
        group="valuation",
        higher_is_better=None,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            "中债国债收益率曲线的 **10 年** 期限值（中国债券信息网官方口径，"
            "`yield.chinabond.com.cn` 的 `qxId=ycqx` 历史查询；AKShare "
            "`bond_china_yield` 逐年翻窗取数，窗口必须 < 1 年）。"
            "**落库天锚在本源自身的可得性上**（Ruling 59）：中债曲线有值的交易日就落行，"
            "**不**被 `daily_quotes`（本地行情底座）截断，故与 `erp_10y` 同窗口（≥5 年）。"
            "⚠️ TuShare `yc_cb` 在本 token 上**权限不足**（`抱歉，您没有接口(yc_cb)访问权限`），"
            "故走中债直取；备源东财 `bond_zh_us_rate` 的 `中国国债收益率10年` 列"
            "（2002 起全历史），两源同日逐值相同（见 data-source.md 附.7 的 XCHK）"
        ),
    ),
    # ── ⑤ ERP（spec §S1.11）──────────────────────────────────────────
    MetricDef(
        key="erp_10y",
        name="股债收益差（10年期）",
        unit="%",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[MARKET_TURNOVER_AMOUNT_SOURCE],
        group="valuation",
        higher_is_better=True,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            "`erp_10y = 1/PE × 100 − 10Y 国债收益率`（单位：百分点）。"
            "**PE 口径 = 整体法**（指数总市值 / 指数口径净利润），**指数 = 沪深300"
            "（000300.SH）**，取 TuShare `index_dailybasic.pe`（该列即整体法口径，"
            "不是成分中位数）。PE 与收益率都落 `extra`，可逐日手算复核。"
            "**落库天锚在自身两个输入的可得性交集上**（Ruling 59）：只看 10Y 中债曲线日"
            "与沪深300 PE 日，**不依赖 `daily_quotes`** —— 公式里根本没有它。"
            "缺 PE 或缺收益率的交易日落 NULL + `missing_reason`，不落 0。"
            "spec 允许「全A或沪深300」——实测 `index_dailybasic` **没有全A（中证全指）**，"
            "故取沪深300；PE 本身另落 `pe_csi300` 一行，便于独立查询"
        ),
    ),
    MetricDef(
        key="pe_csi300",
        name="沪深300市盈率（整体法）",
        unit="倍",
        freq="daily",
        tier=TIER_OFFICIAL,
        sources=[ERP_PE_SOURCE],
        group="valuation",
        higher_is_better=False,
        caliber_version=MARKET_ACTIVITY_CALIBER_VERSION,
        description=(
            "沪深300 整体法市盈率，直取 TuShare `index_dailybasic.pe`（`extra.caliber` = "
            "`aggregate_total_mv_over_net_profit`）。**它是 `erp_10y` 的分子底座**："
            "单独落一行，才能让 ERP 的 `1/PE` 这一半被独立查询与手算核对。"
            "`extra` 另带 `pe_ttm` / `pb` / `total_mv_wan` 便于对账"
        ),
    ),
]

#: 本批次（§S1.9 + §S1.11）**spec 承诺**的全部 `metric_key`（13 个）。
#: `pe_csi300` **不在本元组里**：它是 `erp_10y` 的分子底座（同样落库、同样注册），
#: 但不是 spec 列的指标，故不计入本批次的覆盖率对账。
MARKET_ACTIVITY_METRIC_KEYS: tuple[str, ...] = (
    "market_turnover_amount",
    "idx_turnover_ratio_sse",
    "idx_turnover_ratio_szse",
    "idx_turnover_ratio_bse50",
    "idx_turnover_ratio_star",
    "idx_turnover_ratio_chinext",
    "idx_turnover_ratio_sse50",
    "idx_turnover_ratio_csi300",
    "margin_financing_balance",
    "margin_financing_buy_ratio",
    "margin_short_balance",
    "cn_treasury_yield_10y",
    "erp_10y",
)

#: registry 里登记的**全部** key（= spec 的 13 个 + PE 底座 1 个）。
MARKET_ACTIVITY_ALL_KEYS: tuple[str, ...] = tuple(m.key for m in MARKET_ACTIVITY_METRICS)


def _assert_market_activity_keys() -> None:
    """13 个 spec key 必须**恰好**是登记项去掉 PE 底座（防"少登记一个"静默通过）。"""
    declared = set(MARKET_ACTIVITY_METRIC_KEYS)
    registered = set(MARKET_ACTIVITY_ALL_KEYS)
    if registered - declared != {"pe_csi300"}:
        raise RuntimeError(
            "MARKET_ACTIVITY_METRICS 与 MARKET_ACTIVITY_METRIC_KEYS 不一致："
            f"只登记={sorted(registered - declared)} 只声明={sorted(declared - registered)}"
        )
    if len(MARKET_ACTIVITY_METRIC_KEYS) != 13:
        raise RuntimeError(
            "spec §S1.9+§S1.11 承诺 13 个指标（11 + 2），"
            f"声明了 {len(MARKET_ACTIVITY_METRIC_KEYS)} 个"
        )


_assert_market_activity_keys()

# ── 全市场波动率与衍生品（spec §S1.10）──────────────────────────────────
#
# spec 原话：「**这是情绪模块里性价比最高的一组**——数据质量高、有真实前瞻性」。
# 11 个指标（5 条 QVIX 中国波指 + 4 个品种股指期货升贴水 + IC/IM 贴水率）与 §S1.8 /
# §S1.9 / §S1.11 **同一个 scope 键、同一张 `industry_metrics`、同一个读面**。
#
# 计算与落库在 `app/services/volatility_derivatives_service.py`；
# Step-0 实机结论见 `docs/design/data-source.md` 附.8。
VOLATILITY_DERIVATIVES_CALIBER_VERSION = "s1.10-v1"

#: QVIX 中国波指的产地：**第三方 optbbs.com 编制**（AKShare 内置封装直读其静态 CSV）。
#: ⚠️ **不是交易所官方口径** —— 官方「上证50ETF波动率指数（iVIX）」自 2018-02-22 起由
#: 中证指数公司暂停发布，故 `source_tier` 记 `highfreq`（第三方参考）而非 `official`。
QVIX_SOURCE = "akshare_optbbs_qvix"
#: 升贴水的产地标记：TuShare `fut_daily`（期货腿，`<品种>.CFX` 逐日主力连续）
#: + `index_daily`（现货腿）两条腿相减得出。
#:
#: ⚠️ **必须与写进 `industry_metrics.source` 的字符串逐字相等**：`get_series` 用
#: `MetricDef.sources` 做**源过滤白名单**（`source = next(s for s in m.sources if s in
#: available)`），登记与数据脱节会让序列读面返回**空序列 + source_mismatch=True**
#: ——实测踩过：登记写成 `tushare_fut_daily` 而库里的 source 是
#: `tushare_fut_daily+index_daily`，`GET /metrics/series` 于是返回 0 个点。
#: 两条腿各自的产地明细落 `extra.futures_source` / `extra.spot_source`。
FUTURES_BASIS_SOURCE = "tushare_fut_daily+index_daily"
SPOT_INDEX_SOURCE = "tushare_index_daily"

#: §S1.10 的 `extra.missing_reason` 缺数语义。**缺数恒 NULL，绝不填 0**
#: （0 的波动率/0 点的基差是"市场毫无波动/期货与现货完全平水"这一事实断言，
#: 与「这天没有数据」完全相反 —— 与 §S1.9 的 `margin_short_balance` 同纪律）。
VOLATILITY_DERIVATIVES_MISSING_REASONS: dict[str, str] = {
    "source_not_updated": (
        "上游当日尚无值（**源侧发布滞后**：实测 QVIX 的 optbbs CSV 在 2026-10-13 仍停在 "
        "2026-09-30，比期货腿晚 3 个交易日）。这是「源还没更新」而不是「波动率为 0」"
    ),
    "qvix_before_option_listing": (
        "该交易日在对应 ETF 期权上市日之前 —— 没有期权就没有隐含波动率可算"
        "（上游用 NaN 行占位，本模块落 NULL + 本原因码，**不落 0**）"
    ),
    "spot_index_missing": "该交易日现货指数腿（TuShare index_daily）没有收盘价",
    "futures_leg_missing": "该交易日期货腿（TuShare fut_daily 主力连续）没有结算价",
    "basis_spot_zero": "现货指数收盘价为 0 —— 基差率数学上无定义，落 NULL（不落无穷）",
}

#: **升贴水的符号约定（唯一口径，前端不得自行翻转符号）**。
#:
#: spec §S1.10 的验收判据是「升贴水**负值**时 UI 明示"贴水"语义，避免正负号误读」——
#: 只有「基差 = 期货 − 现货」能让「负值 = 贴水」成立：`现货 − 期货` 会把贴水显示成
#: **正数**，正好是判据要防的那种误读。
BASIS_SIGN_CONVENTION = (
    "**升贴水符号约定（唯一口径）**：`基差 = 期货 − 现货`（期货价 − 现货指数价），"
    "**正值 = 升水（期货高于现货）、负值 = 贴水（期货低于现货）**。"
    "依据 spec §S1.10 的验收判据「升贴水负值时 UI 明示『贴水』语义」（`现货 − 期货` 会把"
    "贴水显示为正数）。手算核对：2026-10-12 `IF2612.CFX` 收盘 4233.60 − 沪深300 收盘 "
    "4287.98 = −54.38 ⇒ 贴水。**机器可读标记**：`sign_convention=futures_minus_spot`"
    "（与落库行 `extra.sign_convention` 同值），前端据此决定是否显示「贴水」徽标，"
    "**不得**自行翻转符号。"
)

#: 基差的自报口径（写进每个基差指标的 description）。
BASIS_CALIBER_NOTE = (
    "`value = 期货结算价 − 现货指数收盘价`（**结算价**取自 TuShare `fut_daily.settle`，"
    "中金所收盘后计算发布的当日官方标价 = 保证金/对冲成本的结算依据）。"
    "收盘价口径另落 `extra.basis_close`（= 期货 `close` − 现货收盘），供与行情商口径对拍。"
    "期货腿 = `<品种>.CFX` **逐日主力连续**（实测与当日 `fut_mapping` 合约的收盘价逐日"
    "完全相等 ⇒ 逐日主力、未复权、无前视偏差）；现货腿 = TuShare `index_daily`。"
    "`extra` 带 `contract`（当日真实合约）、`fut_settle`、`fut_close`、`spot_close`、"
    "`multiplier`、`spot_index`，可逐日手算复核。"
)

#: 贴水率的口径（写进 IC/IM 贴水率指标的 description）。
DISCOUNT_RATE_CALIBER_NOTE = (
    "`value = 基差 / 现货指数收盘价 × 100`，**符号与基差一致**（负值 = 贴水、正值 = 升水）"
    "—— **不翻转符号**：把它翻成「负数越大越贴水」之外的正数形态，正好是 spec §S1.10 要防的"
    "正负号误读。**不年化**（年化口径另落 `extra.annualized_pct` = 贴水率 × 365 / 距最后"
    "交易日天数，天数由 `fut_basic.delist_date` 与交易日相减得到，挂牌即已知 ⇒ 无前视）。"
    "专业用途：作为**对冲成本与中性策略拥挤度**的代理（spec §S1.10 点名）。"
)


def _qvix_def(key: str, name: str, series: str, note: str) -> MetricDef:
    """5 条 QVIX 中国波指的共同形状（口径文案统一，避免 5 份会腐烂的复制）。"""
    return MetricDef(
        key=key,
        name=name,
        unit="%",
        freq="daily",
        # ⚠️ 第三方编制（官方 iVIX 已自 2018-02-22 暂停发布）→ `highfreq` 而非 `official`。
        tier=TIER_HIGHFREQ,
        sources=[QVIX_SOURCE],
        group="volatility",
        # 波动率越高 = 市场越恐慌 → 颜色语义上"越高越差"（与 §S1.8 的 sentiment 同向）。
        higher_is_better=False,
        caliber_version=VOLATILITY_DERIVATIVES_CALIBER_VERSION,
        description=(
            f"{note}。**产地 = 第三方 optbbs.com 编制的 QVIX 中国波指**（AKShare "
            f"`index_option_*_qvix` 直读其静态 CSV，取 `close` 列），**不是交易所官方口径**："
            "官方「上证50ETF波动率指数（iVIX）」自 2018-02-22 起由中证指数公司暂停发布。"
            f"单位 = 波动率百分数（如 13.84 = 13.84% 年化隐含波动率）。"
            f"`extra.series='{series}'`、`extra.caliber='close'`、`extra.upstream` 记录产地。"
            "**缺数恒 NULL + `missing_reason`**（`source_not_updated` = 源侧发布滞后、"
            "`qvix_before_option_listing` = 该日早于期权上市日），**不落 0、不取最近值、"
            "不做插值**"
        ),
    )


def _basis_def(
    product: str, key: str, name: str, contract: str, spot_code: str, spot_name: str
) -> MetricDef:
    """4 个品种股指期货升贴水的共同形状（符号约定与口径文案统一）。"""
    return MetricDef(
        key=key,
        name=name,
        unit="点",
        freq="daily",
        # 由两个当日上游事实相减得出（期货结算价 − 现货收盘）→ 本地派生。
        tier=TIER_DERIVED,
        sources=[FUTURES_BASIS_SOURCE],
        group="derivatives",
        higher_is_better=None,  # 升水/贴水本身无"越好"方向（是套保成本，不是好坏）
        caliber_version=VOLATILITY_DERIVATIVES_CALIBER_VERSION,
        description=(
            f"{product} 股指期货主力连续的升贴水（{contract} 逐日主力连续 ← "
            f"{spot_name} `{spot_code}`）。{BASIS_SIGN_CONVENTION}{BASIS_CALIBER_NOTE}"
        ),
    )


def _discount_def(product: str, key: str, name: str, basis_key: str) -> MetricDef:
    """IC / IM 贴水率的共同形状（对冲成本与中性策略拥挤度的代理）。"""
    return MetricDef(
        key=key,
        name=name,
        unit="%",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[FUTURES_BASIS_SOURCE],
        group="derivatives",
        higher_is_better=None,
        caliber_version=VOLATILITY_DERIVATIVES_CALIBER_VERSION,
        description=(
            f"{product} 股指期货主力连续的**贴水率**（由 `{basis_key}` 与现货指数收盘价算出，"
            f"与基差同源同窗口）。{DISCOUNT_RATE_CALIBER_NOTE}{BASIS_SIGN_CONVENTION}"
        ),
    )


#: spec §S1.10 逐字列的 5 个期权波动率品种（**顺序 = spec 顺序**）。
#: （akshare series 键, metric_key, 名称, 期权上市日）
QVIX_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("50etf", "qvix_50etf", "50ETF期权隐含波动率", "2015-02-09"),
    ("300etf", "qvix_300etf", "300ETF期权隐含波动率", "2019-12-23"),
    ("500etf", "qvix_500etf", "500ETF期权隐含波动率", "2022-09-19"),
    ("cyb", "qvix_cyb_etf", "创业板ETF期权隐含波动率", "2022-09-19"),
    ("kcb", "qvix_kcb_etf", "科创50ETF期权隐含波动率", "2023-06-05"),
)

#: spec §S1.10 逐字列的 4 个股指期货品种（顺序 = spec 顺序）。
#: （品种, 连续合约代码, 基差 metric_key, 现货指数代码, 现货指数名）
FUTURES_BASIS_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("IF", "IF.CFX", "if_basis", "000300.SH", "沪深300"),
    ("IC", "IC.CFX", "ic_basis", "000905.SH", "中证500"),
    ("IH", "IH.CFX", "ih_basis", "000016.SH", "上证50"),
    ("IM", "IM.CFX", "im_basis", "000852.SH", "中证1000"),
)

#: **额外指标**（spec §S1.10 逐字点名）：IC/IM 贴水率。
#: spec 写作 `IC/IM 贴水率`——落地为**两个 key**（IC 与 IM 各一条独立序列），
#: 因为二者是**不同的标的、不同的贴水水平**（实测 2026-10-12 IC −1.509% vs IM −1.431%），
#: 合成一条"IC/IM 平均"会丢掉它们各自的对冲成本信息。品种同上（`extra` 可对账）。
DISCOUNT_RATE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("IC", "ic_discount_rate", "IC贴水率", "ic_basis"),
    ("IM", "im_discount_rate", "IM贴水率", "im_basis"),
)

VOLATILITY_DERIVATIVES_METRICS: list[MetricDef] = [
    *(
        _qvix_def(
            key,
            name,
            series,
            f"「{name}」= 该 ETF 期权组合的 QVIX 中国波指日频收盘值（spec §S1.10 点名的 "
            f"{name.replace('期权隐含波动率', '')}，对应期权上市日 {list_date}）",
        )
        for series, key, name, list_date in QVIX_SPECS
    ),
    *(
        _basis_def(product, key, f"{product}股指期货基差", contract, spot_code, spot_name)
        for product, contract, key, spot_code, spot_name in FUTURES_BASIS_SPECS
    ),
    *(
        _discount_def(product, key, name, basis_key)
        for product, key, name, basis_key in DISCOUNT_RATE_SPECS
    ),
]

#: §S1.10 **spec 承诺**的全部 `metric_key`：5 条 QVIX + 4 个品种升贴水 + 2 条贴水率 = 11 个。
VOLATILITY_DERIVATIVES_METRIC_KEYS: tuple[str, ...] = (
    *(key for _series, key, _name, _ld in QVIX_SPECS),
    *(key for _p, _c, key, _sc, _sn in FUTURES_BASIS_SPECS),
    *(key for _p, key, _n, _b in DISCOUNT_RATE_SPECS),
)

#: registry 里登记的**全部** key（本批次目前 = spec 的 11 个，无「底座类」附加 key）。
VOLATILITY_DERIVATIVES_ALL_KEYS: tuple[str, ...] = tuple(
    m.key for m in VOLATILITY_DERIVATIVES_METRICS
)


def _assert_volatility_derivatives_keys() -> None:
    """spec §S1.10 的 11 个 key 必须**恰好**是登记项 —— 防"少登记一个"静默通过。

    同时锁死"5 条 QVIX 覆盖 ≥3 个品种"这一 spec 判据：登记项里 QVIX 少于 3 个即当场失败
    （不是运行时才发现 UI 上少了两条线）。
    """
    declared = set(VOLATILITY_DERIVATIVES_METRIC_KEYS)
    registered = set(VOLATILITY_DERIVATIVES_ALL_KEYS)
    if declared != registered:
        raise RuntimeError(
            "VOLATILITY_DERIVATIVES_METRICS 与 _METRIC_KEYS 不一致："
            f"只登记={sorted(registered - declared)} 只声明={sorted(declared - registered)}"
        )
    if len(VOLATILITY_DERIVATIVES_METRIC_KEYS) != len(set(VOLATILITY_DERIVATIVES_METRIC_KEYS)):
        raise RuntimeError("§S1.10 的 metric_key 有重复")
    if len(VOLATILITY_DERIVATIVES_METRIC_KEYS) != 11:
        raise RuntimeError(
            "spec §S1.10 承诺 11 个指标（5 期权波动率 + 4 品种升贴水 + 2 条贴水率），"
            f"声明了 {len(VOLATILITY_DERIVATIVES_METRIC_KEYS)} 个"
        )
    if len(QVIX_SPECS) < 3:
        raise RuntimeError(f"spec §S1.10 要求期权波动率覆盖 ≥3 个品种，登记了 {len(QVIX_SPECS)} 个")
    if len(FUTURES_BASIS_SPECS) != 4:
        raise RuntimeError(
            f"spec §S1.10 要求 IF/IC/IH/IM 四品种升贴水，登记了 {len(FUTURES_BASIS_SPECS)} 个"
        )


_assert_volatility_derivatives_keys()

# ── 全市场资金流四件套（spec §S1.7）──────────────────────────────────────
#
# 四条腿的落地方式**逐条不同**（源现状不同，不是偷懒）：
#   · 两融 `margin_financing_balance` / `margin_short_balance` —— **引用 S1.9 的既有登记**，
#     **不重复登记**（重复登记会让 `find_metric` 的白名单出现两份可能漂移的定义）。
#   · 南向 `southbound_net_buy` —— 落库（`moneyflow_hsgt.south_money` 的**一阶差分**）。
#   · ETF 份额变化 `etf_share_change` —— 落库（六只宽基 ETF 的份额日变化之和）。
#   · 产业资本增减持 `industrial_capital_net_change` —— **只登记、不落行**：
#     原料是公告（S3.2），S3.2 未完成；spec 原话「标记为待接，**不得用假数据填充**」。
# 另有一条**同批次的登记**（不是四件套之一）：`northbound_holding_quarterly`
# （陆股通持仓（季度））—— 只落口径与滞后说明，ingest 与页面待后续批次。
#
# 计算与落库在 `app/services/capital_flows_service.py`；Step-0 实机结论见
# `docs/design/data-source.md` 附.9。
#
# 方法论禁令（Ruling 19）：**不得**把「香港中央結算持仓」称作"外资"（它是名义持有人
# **汇总账户**，持仓主体不可拆分）——故本批次一律用「陆股通持仓」。
#
# v2（口径断点修正）与 v1 的差异：v1 把 `south_money` **整段历史**都当"历史累计净买额"
# 去差分 —— 实测该列在 **2023-11-23 / 2023-11-24 之间换过语义**
# （此前 = 日频净买入/百万元，含负值；此后 = 历史累计净买额/亿元，恒正），
# 于是 v1 在断点前落的是**日频序列的二阶差分（纯噪声）**。v2：①南向只落断点及之后的
# 差分窗口（断点日自身落 NULL + `prev_connect_day_missing`，因为它的前值还是旧口径）；
# ②落库前加"断点后出现 ≤ 阈值 的累计值即响亮失败"的**口径回退守卫**。
# 两版的行**不可混用**（ADR 0011 第 4 条），故 v2 起 v1 的南向行必须重灌。
CAPITAL_FLOWS_CALIBER_VERSION = "s1.7-v2"

#: 南向 `south_money` 的**口径断点**（同日切换：日频净买入/百万元 → 历史累计净买额/亿元）。
#:
#: 实证（`scripts/spike_s17_capital_flows_sources.py` 的 PROBE 3c）：把每一天的
#: `south_money` 同时与东财「当日成交净买额×100」和「历史累计净买额×1e4」比较，
#: 2023-06-01…2023-11-23 共 114 日全部匹配**日频**（其中 37 日为负），
#: 2023-11-24 起共 61 日全部匹配**累计**（无一日 ≤0）。
#: ⇒ `southbound_net_buy` 的落库窗口下界就是这个日期（更早的整段**不落**）。
SOUTHBOUND_CUMULATIVE_FROM = date(2023, 11, 24)

#: ETF 份额的产地（TuShare `fund_share`，`fd_share` 原生单位**万份**）。
ETF_SHARE_SOURCE = "tushare_fund_share"
#: 南向资金的产地（TuShare `moneyflow_hsgt` 的 `south_money` = **历史累计净买额/亿元**）。
SOUTHBOUND_SOURCE = "tushare_moneyflow_hsgt"
#: 产业资本增减持的**计划**产地（公告采集，S3.2）。⚠️ S3.2 未完成 ⇒ 本 key **无行**。
INDUSTRIAL_CAPITAL_SOURCE = "s3.2_announcement_ingest"
#: 陆股通持仓（季度）的产地（TuShare `hk_hold`，含 `exchange ∈ {SH,SZ}` 的陆股通持股行）。
NORTHBOUND_HOLDING_SOURCE = "tushare_hk_hold"

#: §S1.7 的 `extra.missing_reason` 缺数语义。**缺数恒 NULL，绝不填 0**：
#: 0 份净申购是"今天真的没有净申购"这一事实断言，与"这天没有数据"完全相反。
CAPITAL_FLOWS_MISSING_REASONS: dict[str, str] = {
    "source_empty": "上游当日返回了行但没有值（或该指标自身输入缺当日值）",
    "partial_basket_coverage": (
        "宽基 ETF 篮子里有成员当日或前一日无份额行 —— 份额变化是**可加量**，"
        "缺成员时按覆盖率归一会得到一个假的「全市场净申购」，故整日落 NULL"
    ),
    "prev_value_missing": (
        "上一个交易日的累计值缺失 ⇒ 差分**跨了缺口**，不落数字（把跨多日的变化记成"
        "「一天」是静默错标；也不回退到更早的行）"
    ),
    "no_prev_fund_day": ("该基金在取数窗口内**没有前一个份额日**（序列起点）⇒ 变化量结构性不可得"),
    "prev_connect_day_missing": (
        "该交易日没有**前一个累计口径**的交易日的值可相减（口径断点当天、或上游缺行）——"
        "不回退到更早的行（那会把跨多日的累计变化记成「一天」，或把旧口径的值混算进来）"
    ),
    "upstream_dependency_pending": (
        "**待接**：原料来自公告采集（spec §S3.2），S3.2 未完成 ⇒ 本指标**没有任何行**"
        "（spec 原话：不得用假数据填充）"
    ),
    "not_ingested_page_pending": (
        "**只登记口径**：陆股通持仓（季度）本轮只落口径与滞后说明，ingest 与页面待后续批次"
    ),
}

#: ETF 份额变化的口径说明（写进 registry `description`，字典只转录 registry）。
ETF_BASKET_CALIBER = (
    "口径 = 六只宽基 ETF（沪深300 510300 · 上证50 510050 · 中证500 510500 · "
    "中证1000 512100 · 科创50 588000 · 创业板指 159915）的 `fd_share` **日变化之和**，"
    "单位**亿份**（上游原生单位万份，÷1e4）。篮子按「每个宽基指数族里**规模**"
    "（份额×收盘价）最大的一只」选定（实机排名与原文证据见 data-source.md 附.9）；"
    "⚠️ 已知收窄：沪深300 族的 510310 / 159919 未纳入。"
    "**为什么是「份额变化」而非份额水平**：净申购赎回才是「国家队」护盘痕迹。"
    "任一成员缺当日或前一日份额 → 整日 NULL + `missing_reason='partial_basket_coverage'`。"
)


def _capital_flow_def(
    key: str,
    name: str,
    unit: str,
    freq: str,
    tier: str,
    sources: list[str],
    group: str,
    note: str,
    *,
    higher_is_better: bool | None = None,
) -> MetricDef:
    """§S1.7 各指标的共同形状（口径文案统一点，避免复制会腐烂）。"""
    return MetricDef(
        key=key,
        name=name,
        unit=unit,
        freq=freq,
        tier=tier,
        sources=sources,
        group=group,
        higher_is_better=higher_is_better,
        caliber_version=CAPITAL_FLOWS_CALIBER_VERSION,
        description=note,
    )


CAPITAL_FLOWS_METRICS: list[MetricDef] = [
    # ── ① ETF 份额变化（spec §S1.7「宽基 ETF 份额变化」，日频）──────────
    _capital_flow_def(
        "etf_share_change",
        "宽基 ETF 份额变化",
        "亿份",
        "daily",
        TIER_DERIVED,
        [ETF_SHARE_SOURCE],
        "capital_flow",
        ETF_BASKET_CALIBER,
        higher_is_better=True,
    ),
    # ── ② 南向资金（spec §S1.7「南向资金」，日频）──────────────────────
    _capital_flow_def(
        "southbound_net_buy",
        "南向资金净买入",
        "亿元",
        "daily",
        TIER_DERIVED,
        [SOUTHBOUND_SOURCE],
        "capital_flow",
        (
            "口径 = `south_money(T) − south_money(前一个港股通交易日)`，单位**亿元**。"
            "**必须差分**：`south_money` 是**历史累计净买额**（与东财「历史累计净买额」"
            "逐值相等 650/650 日），直接落库就是把累计值冒充日频净买入；"
            "一阶差分与东财「当日成交净买额」逐值相等（649/649 日）——见 data-source.md 附.9。"
            f"⚠️ **口径断点**：该列在 {SOUTHBOUND_CUMULATIVE_FROM.isoformat()} **换过语义**"
            "（此前 = 日频净买入/百万元，含负值），故本指标**只落断点及之后**的窗口；"
            "断点日自身无累计前值 → NULL + `prev_connect_day_missing`。"
            "落库天 = 上游有行的日（港股通交易日是 A 股交易日的**子集**）。"
        ),
        higher_is_better=True,
    ),
    # ── ③ 产业资本增减持（spec §S1.7；**待接**，不落行）────────────────
    _capital_flow_def(
        "industrial_capital_net_change",
        "产业资本净增减持",
        "亿元",
        "event",
        TIER_MANUAL,
        [INDUSTRIAL_CAPITAL_SOURCE],
        "capital_flow",
        (
            "**待接（blocked-on-verification）**：原料是上市公司**公告**（spec §S3.2"
            "「原料/视图分离」的直接体现），而 S3.2 未完成 ⇒ 本指标**没有任何行**"
            "（spec 原话：标记为待接、不得用假数据填充）。"
            "已登记的目的：①让读面显式返回「已登记但无数据」而不是 404；"
            "②把「缺什么」写进口径字典。"
            "上游替代 `stk_holdertrade`（结构化增减持，实测单日 3000 行撞上限）"
            "**本轮不采用** —— 换原料会绕开公告面的 `publisher_tier` 与正文可回溯，属控制器裁定权。"
        ),
    ),
    # ── ④ 陆股通持仓（季度）（spec §S1.7「改按季度披露持股」；页面待建）──
    _capital_flow_def(
        "northbound_holding_quarterly",
        "陆股通持仓（季度）",
        "万股",
        "quarterly",
        TIER_OFFICIAL,
        [NORTHBOUND_HOLDING_SOURCE],
        "capital_flow",
        (
            "**陆股通持仓（季度）**：`hk_hold` 的 `exchange ∈ {SH,SZ}` 行（沪深股通持有"
            "A 股明细）。⚠️ **命名禁令（Ruling 19）**：香港中央結算（HKSCC）是**名义持有人"
            "汇总账户**，持仓主体不可拆分 ⇒ 只说「陆股通持仓」，**不得**称「外资」。"
            "**滞后说明（UI 必须明示）**：2024-08-19 起沪深港通持股**改为按季度披露**"
            "（北向每日净买入同期取消），故本指标落后当季最多约一个季度 + 源侧发布滞后；"
            "季度末的可得性实测**不规则**（12 个季度末里 10 个给出陆股通行：20240329 返回 0 行、"
            "20260930 只回港股通行），单次上限 4200 行。"
            "**本轮只落口径与滞后说明，不落任何数据**（`missing_reason='not_ingested_page_pending'`），"
            "ingest 与页面待后续批次。"
        ),
    ),
]

#: 四件套里**本任务实际落库**的两个 key（= `capital_flows_service.INGEST_METRIC_KEYS`）。
CAPITAL_FLOWS_METRIC_KEYS: tuple[str, ...] = ("etf_share_change", "southbound_net_buy")

#: 四件套里**引用既有登记**的两个 key（S1.9 已落）—— 本批次**不重复登记**。
CAPITAL_FLOWS_REUSED_KEYS: tuple[str, ...] = (
    "margin_financing_balance",
    "margin_short_balance",
)

#: **已登记但按 spec 待接、不落任何行**的 key。
CAPITAL_FLOWS_PENDING_KEYS: tuple[str, ...] = ("industrial_capital_net_change",)

#: registry 里登记的**全部**本批次 key（4 个）。
CAPITAL_FLOWS_ALL_KEYS: tuple[str, ...] = tuple(m.key for m in CAPITAL_FLOWS_METRICS)


def _assert_capital_flows_keys() -> None:
    """§S1.7 的登记面必须自洽，且"两融引用不重做"必须**机械可证**。

    - 登记项恰好 = 2 个落库 key + 1 个待接 key + 1 个陆股通持仓 key；
    - 四件套里被引用的两个两融 key **必须在 S1.9 的登记面里**（否则"引用"就成了空话）；
    - 陆股通持仓的**频率标识必须是 `quarterly`**（spec 判据：「频率标识为季度」）；
    - 待接 key 的频率必须是 `event`（spec 表格：事件驱动）。
    """
    registered = set(CAPITAL_FLOWS_ALL_KEYS)
    declared = set(CAPITAL_FLOWS_METRIC_KEYS) | set(CAPITAL_FLOWS_PENDING_KEYS)
    if registered - declared != {"northbound_holding_quarterly"}:
        raise RuntimeError(
            "CAPITAL_FLOWS_METRICS 与声明的 key 集不一致："
            f"只登记={sorted(registered - declared)} 只声明={sorted(declared - registered)}"
        )
    if len(CAPITAL_FLOWS_ALL_KEYS) != len(registered):
        raise RuntimeError("§S1.7 的 metric_key 有重复")
    missing_reused = [k for k in CAPITAL_FLOWS_REUSED_KEYS if k not in MARKET_ACTIVITY_ALL_KEYS]
    if missing_reused:
        raise RuntimeError(
            "§S1.7 声称「两融引用 S1.9 的既有登记」，但 S1.9 的登记面里没有："
            f"{missing_reused} —— 引用落空（要么 S1.9 没落，要么 key 拼错）"
        )
    by_key = {m.key: m for m in CAPITAL_FLOWS_METRICS}
    if by_key["northbound_holding_quarterly"].freq != "quarterly":
        raise RuntimeError("spec §S1.7 判据要求陆股通持仓的频率标识为「季度」")
    for key in CAPITAL_FLOWS_PENDING_KEYS:
        if by_key[key].freq != "event":
            raise RuntimeError(f"spec §S1.7 要求 {key} 为事件驱动（freq='event'）")


_assert_capital_flows_keys()

# ══════════════════════════════════════════════════════════════════════════
# §S1.12 情绪 · 投资者行为（增量资金的入场行为）
# ══════════════════════════════════════════════════════════════════════════
#
# spec §S1.12 的契约表是**四条腿**，处置逐条不同（与 §S1.7 同样的"四条腿四条路"）：
#
# | 原图口径 | 处置（spec 明文） | 本批次 |
# |---|---|---|
# | 基金申购赎回 → ETF 份额变化 | 采用，**复用 S1.7** | **不重做**：S1.7 已落（引用） |
# | 新增散户开户数 | 「待 S0.3 实机验证；不可得则删除，不用估算值填充」 | **不可得 ⇒ 不存在** |
# | 新基金发行规模 → 新成立基金份额 | 采用 | **落库** `new_fund_share`（月频） |
# | 公募基金仓位 | 「低频参考值 → **仅背景展示，不进信号链路**」 | 落库，`freq='weekly'`（周度） |
# | 主动基金申赎 | **不做**（不公开） | — |
#
# **Step 0 实机结论（2026-10-13，探针 `scripts/spike_s112_investor_behavior_sources.py`）**：
#
# - **新增散户开户数 = 不可得**。候选源东财 `stock_account_statistics_em`
#   （``RPT_STOCK_OPEN_DATA``）实机可得但**最新月停在 2023-08**（101 行 = 该接口单页
#   上限，`count` 字段亦为 101），距观测日已停滞约 3 年；中国结算（chinaclear.cn）月报
#   是唯一权威产地而 AKShare **未封装**（无对应 `fetch_*` 方法）⇒ **无权威第二源**。
#   ⇒ 按 spec 的「不可得则删除」**不注册 `metric_key`**：本 registry 里**不存在**
#   开户数键（因此读面不会回骨架、UI 不会出现占位空图），也没有任何估算值填充。
# - **新成立基金份额 = 可得**：东财 `FundNewIssue.aspx`（AKShare `fund_new_found_em`）
#   实机 7012 行（逐页取三页合计同为 7012 ⇒ **无分页丢行**），`成立日期`
#   2023-01-01…2026-09-30 **完整**（更早只剩 2013/2018 两行孤例 + 2019–2022 无数据）。
#   取值列 = **`募集份额`**（单位**亿元**）：该表**没有**独立的「成立份额/发行份额」列，
#   而本表只含**新成立**基金，故"发行份额 == 成立份额 == 成立时的募集份额"。**不估不补**。
# - **公募基金仓位 = 可得但口径与 spec 的假设不符**：乐咕乐股
#   `fund_stock_position_lg`（`type=pos_stock`、`category=总仓位`）实机 455 行 /
#   2017-12-04…2026-09-30，相邻观测间隔 **6–8 天占 90.3% ⇒ 实测是周度**（**不是**最初
#   假设的季报），上游 JSON 只有 `date/close/position` 三个字段（**无** 报告期/发布日期/
#   编制方法）⇒「股票投资占基金总资产比」这一口径**无法从产地复算**。故本 key
#   一律 `source_tier='calc'`（测算/第三方编制，**不是** `official`）且文案显式写
#   「低频参考值、滞后 ~15 工作日」，并**禁止进任何信号链路**（判据 2 的 grep）。
#   **Ruling 64（2026-10-13）**：`freq` 按**实测周度**登记 `weekly` —— 机读字段
#   **不得**与实测不符；spec §S1.12 已同步修正（原「季报」前提对该产地就是错的）。
#
# **频率与发布滞后（spec §S1.12 判据 1：必须在 UI 明示）** 落在两处、都是可机读字段：
# `MetricDef.freq`（机器读）+ `MetricDef.description`（人读；含滞后的具体天数与口径）。
# 前端的 `FREQ_LABELS` 徽章直接渲染 `freq`，描述进指标说明。
# ⚠️ **`freq` 必须是实测频率**（Ruling 64）：机读字段与实测不符就是一句机器可读的假话 ——
# 仓位的产地实测是**周度**，故登记 `weekly`；导入期断言强制它在 `FREQ_LABELS` 里有标签。
#
# **A/B/C 归类（ADR 0014）**：两个落库指标都是 **B 类**（只用当期点内事实、可回算、
# 允许回补）。**无 A 类**（不消费盘中明细）、**无 C 类**（不用成分快照回算）。

#: §S1.12 的口径版本（ADR 0011 第 4 条：口径修订即 bump，两版的行不可混用）。
INVESTOR_BEHAVIOR_CALIBER_VERSION = "s1.12-v1"

#: 新成立基金份额的产地（东财 `FundNewIssue.aspx`，AKShare `fund_new_found_em` 封装）。
NEW_FUND_SHARE_SOURCE = "akshare_fund_new_found_em"
#: 公募基金仓位的产地（乐咕乐股 `legulegu.com/api/stockdata/fund-position`，
#: AKShare `fund_stock_position_lg` 封装）—— **第三方编制**，不是交易所官方口径。
PUBLIC_FUND_POSITION_SOURCE = "akshare_legulegu_fund_position"

#: §S1.12 的 `extra.missing_reason` 缺数语义。**缺数恒 NULL，绝不填 0**：
#: 0 亿元的新成立基金份额是"这个月真的没有基金成立"这一事实断言，
#: 与"这个月的数据还没到"完全相反。
INVESTOR_BEHAVIOR_MISSING_REASONS: dict[str, str] = {
    "source_empty": "上游返回了该周期的行但值全部为空（有行 ≠ 有值）",
    "upstream_no_rows": "该周期上游**没有任何行**（如该月无基金成立 / 序列起点之前）",
}

#: 开户数**不可得**的处置依据（写进字典，让"为什么没有这个 key"可查）。
#: ⚠️ 这里是**注释与文档**，**不是**一个待注册的 `MetricDef` —— spec 判据 3 要求
#: "本项在 UI 不存在"，所以 `INVESTOR_BEHAVIOR_METRICS` 里**没有**它。
ACCOUNT_STATS_UNAVAILABLE_NOTE = (
    "新增散户开户数 = **不可得**（Step 0 实机：东财 `stock_account_statistics_em` 最新月 "
    "2023-08、101 行 = 单页上限；中国结算月报是唯一权威产地而 AKShare 未封装 ⇒ 无第二源）。"
    "按 spec §S1.12「不可得则删除，不用估算值填充」⇒ **不注册 metric_key**，UI 无此项。"
)


def _investor_behavior_def(
    key: str,
    name: str,
    unit: str,
    freq: str,
    tier: str,
    sources: list[str],
    note: str,
    *,
    higher_is_better: bool | None = None,
) -> MetricDef:
    """§S1.12 各指标的共同形状（口径文案与版本统一点，避免复制会腐烂）。"""
    return MetricDef(
        key=key,
        name=name,
        unit=unit,
        freq=freq,
        tier=tier,
        sources=sources,
        group="capital_flow",
        higher_is_better=higher_is_better,
        caliber_version=INVESTOR_BEHAVIOR_CALIBER_VERSION,
        description=note,
    )


INVESTOR_BEHAVIOR_METRICS: list[MetricDef] = [
    # ── ① 新成立基金份额（spec：新基金发行规模 → 新成立基金份额；月频）──────
    _investor_behavior_def(
        "new_fund_share",
        "新成立基金募集份额",
        "亿元",
        "monthly",
        TIER_DERIVED,
        [NEW_FUND_SHARE_SOURCE],
        (
            "**频率：月频**（按 `成立日期` 归月，`period` = 该月 1 日）。"
            "**发布滞后：约 4 个日历日**（`published_at` = 该月**最后一个成立日** + 4 天，"
            "该偏移是沿用 probe 实机观测（成立日与源侧可见约有数日差）的**保守取值**，"
            "精确更新时刻未验证——见 data-source.md 附.10 的未验证余项）。"
            "口径 = 该月**全部新成立基金**的 `募集份额` 之和，单位**亿元**"
            "（上游 `fund_new_found_em.募集份额`；东财页面亦标亿元）。"
            "**为什么取「募集份额」**：该表只有这一列有份额语义（无独立的「成立份额」/"
            "「发行份额」列），而表内全是**新成立**基金 ⇒ 发行份额 == 成立份额 == 募集份额。"
            "⚠️ **A/C 双计是已知的口径特征**：同一基金的 A、C 两个代码**各有独立份额与"
            "成立日**，故按行求和会把一次募集计两次；本指标按**行业惯例**（新发基金规模按"
            "份额类别分别统计）保留双计，**不**在代码里「顺手除以 2」——"
            "该选择与「不纳入 2019–2022」（产地无数据）一并登记为已知口径边界。"
            "缺数恒 NULL：该月**全部**基金的 `募集份额` 都为空（一位有值的都没有）→ 整月 NULL + "
            "`missing_reason='source_empty'`（`extra.n_null` 记个数），**不填 0**。"
            "⚠️ **下界口径（使用者必读）**：若该月**只有部分**基金的 `募集份额` 缺失"
            "（实测字段级缺值约 4.8%、均匀散布），本值 = **「有值成员之和」即该月真实规模的"
            "下界**，`extra.is_lower_bound=true`（并附 `n_funds` / `n_missing_share` / "
            "`missing_note`）——**读的人不得把部分成员的和当成全月完整总额**；"
            "缺的份额**未被当成 0**。实测 45 个月里有 **42** 个月属于这种下界口径。"
        ),
    ),
    # ── ② 公募基金仓位（spec：仅作背景展示，不进信号链路）──────────────────
    _investor_behavior_def(
        "public_fund_equity_position",
        "公募基金仓位（股票型基金·总仓位，背景参考）",
        "%",
        "weekly",
        TIER_CALC,
        [PUBLIC_FUND_POSITION_SOURCE],
        (
            "**⚠️ 仅作背景展示，不得当情绪指标、不得进任何信号链路**"
            "（低频参考值；`extra.signal_chain='forbidden'`）。"
            "本 key 在代码里**没有任何消费者**（唯一的登记消费者是读面与字典）；"
            "S2.2 落地 `signal_rules.py` 后必须保持 0 命中（判据 2 的可复跑 grep）。"
            "**频率：`weekly`（周度）**——**按产地实测登记**（455 行 / 2017-12-04…2026-09-30，"
            "相邻观测间隔 6–8 天占 90.3%）；机读字段不得与实测不符（Ruling 64）。"
            "**发布滞后：约 15 个工作日**"
            "（`published_at` = 观测日 + 15 个工作日，跳过周末、不查节假日日历；"
            "**推算值**、产地无发布字段，见 data-source.md 附.10.4）。"
            "**口径（产地原始说明，未经证实）**：乐咕乐股「基金仓位 · 股票型基金 · 总仓位」，"
            "AKShare `fund_stock_position_lg`（`type=pos_stock`）；值 = 股票型基金持股市值"
            "占其基金总资产的百分比，**第三方编制**（故 `source_tier='calc'`，**不是**官方口径）。"
            "⚠️ **口径不可复算（登记为限制，不掩盖）**：上游 JSON 只有 `date/close/position` "
            "三列，**无**报告期/发布日期/编制方法，故「股票投资占基金总资产比」这一描述"
            "**无法从产地复算**，`published_at` 只能按「滞后 ~15 工作日」推算。"
            "⚠️ 该观测日**不是**报告期（产地不给报告期）——任何下游**不得**把它当「报告期」。"
            "缺数恒 NULL（上游某观测日无值 → `missing_reason='source_empty'`），**不填 0**。"
        ),
    ),
]

#: §S1.12 中**由本批次实际落库**的 key（= `investor_behavior_service.INGEST_METRIC_KEYS`）。
INVESTOR_BEHAVIOR_METRIC_KEYS: tuple[str, ...] = (
    "new_fund_share",
    "public_fund_equity_position",
)

#: §S1.12 中**引用既有登记、不重做**的 key（§S1.7 已落，见
#: :data:`CAPITAL_FLOWS_METRIC_KEYS`）—— spec 原话「ETF 份额变化（复用 S1.7）」。
INVESTOR_BEHAVIOR_REUSED_KEYS: tuple[str, ...] = ("etf_share_change",)

#: §S1.12 中**判为不可得、故不注册**的项（spec：「不可得则删除，不用估算值填充」）。
#: 这里是**文字记录**而非 key —— 让"为什么 UI 里没有开户数"可查（判据 3 的证据面）。
INVESTOR_BEHAVIOR_DROPPED_ITEMS: dict[str, str] = {
    "新增散户开户数": ACCOUNT_STATS_UNAVAILABLE_NOTE,
}

#: registry 里登记的**全部**§S1.12 key（2 个；被判不可得的开户数**不在其中**）。
INVESTOR_BEHAVIOR_ALL_KEYS: tuple[str, ...] = tuple(m.key for m in INVESTOR_BEHAVIOR_METRICS)


def _assert_investor_behavior_keys() -> None:
    """§S1.12 的登记面必须自洽，且三条验收判据里可机械化的两条必须**当场**成立。

    - 登记项恰好 = 2 个落库 key；
    - 「ETF 份额变化复用 S1.7」必须**机械可证**：`etf_share_change` 确实在 §S1.7 的
      登记面里（否则"复用"就成了空话，与 §S1.7 断言两融引用的做法同构）；
    - **判据 1（频率与滞后必须在 UI 可明示）**：两个 key 的 `freq` 必须是
      `FREQ_LABELS` 里**有标签**的取值（前端徽章靠它），且 `description` 里必须同时
      出现「频率」与「滞后」两个词（滞后值住在描述里，**不得**只活在服务代码里）。
      ⚠️ `freq` 还必须是**实测频率**（Ruling 64）：机读字段与实测不符 = 一句机器可读的
      假话 —— 本断言的作用是**挡住描述/频率漂移**，故**只增不减**，不得为了让某个值过关
      而放宽（`weekly` 已是产地实测的频率）。
    - **判据 3（开户数不可得则 UI 不存在）**：`new_investor_accounts` /
      `retail_investor_accounts` 这类键**必须不在**登记面里（写死断言，防"后来有人
      顺手补一个空壳 key 当占位"），且"不可得"的结论必须在 `INVESTOR_BEHAVIOR_DROPPED_ITEMS`
      里有记录（防"删了但没记为什么"）。
    """
    registered = set(INVESTOR_BEHAVIOR_ALL_KEYS)
    if registered != set(INVESTOR_BEHAVIOR_METRIC_KEYS):
        raise RuntimeError(
            "INVESTOR_BEHAVIOR_METRICS 与声明的 key 集不一致："
            f"只登记={sorted(registered - set(INVESTOR_BEHAVIOR_METRIC_KEYS))} "
            f"只声明={sorted(set(INVESTOR_BEHAVIOR_METRIC_KEYS) - registered)}"
        )
    if len(INVESTOR_BEHAVIOR_ALL_KEYS) != len(registered):
        raise RuntimeError("§S1.12 的 metric_key 有重复")

    missing_reused = [k for k in INVESTOR_BEHAVIOR_REUSED_KEYS if k not in CAPITAL_FLOWS_ALL_KEYS]
    if missing_reused:
        raise RuntimeError(
            "§S1.12 声称「ETF 份额变化复用 S1.7」，但 §S1.7 的登记面里没有："
            f"{missing_reused} —— 引用落空（要么 S1.7 没落，要么 key 拼错）"
        )

    # 判据 3：开户数**必须不存在**（占位空壳 key 也算违反——spec 要的是"UI 不存在"）。
    forbidden = {
        "new_investor_accounts",
        "retail_investor_accounts",
        "investor_account_openings",
        "stock_account_statistics",
    }
    leaked = sorted(forbidden & registered)
    if leaked:
        raise RuntimeError(
            "spec §S1.12 判据 3：开户数实测不可得 ⇒ 本项**不得**出现在 UI/registry；"
            f"但登记面里出现了 {leaked}（占位空图同样违反判据，不得用估算值填充）"
        )
    if not INVESTOR_BEHAVIOR_DROPPED_ITEMS:
        raise RuntimeError("开户数被判不可得却没有任何文字记录 —— 「删除」必须留下为什么")

    # 判据 1：频率必须可渲染（FREQ_LABELS 有标签），描述必须同时写明频率与滞后。
    for metric in INVESTOR_BEHAVIOR_METRICS:
        if metric.freq not in FREQ_LABELS:
            raise RuntimeError(
                f"§S1.12 判据 1：{metric.key} 的 freq={metric.freq!r} 没有 UI 标签"
                f"（FREQ_LABELS={sorted(FREQ_LABELS)}）—— 前端无法明示频率"
            )
        for token in ("频率", "滞后"):
            if token not in metric.description:
                raise RuntimeError(
                    f"§S1.12 判据 1：{metric.key} 的 description 里没有「{token}」—— "
                    "频率/发布滞后必须能在 UI 明示（不得只活在服务代码里）"
                )


_assert_investor_behavior_keys()

# ── 全市场情绪**综合指数**（spec §S1.13）────────────────────────────────
#
# scope='market' 里**唯一的合成指标**：不消费任何新产地，只把上面已登记的成分指标做
# **滚动分位标准化**后**加权合成**。权重 / 分位窗口 / 最小样本量 / 缺失处置 / 版本号
# **全部住在本节**（registry 是唯一事实源），由
# `app/services/market_sentiment_composite_service.py` 读取并落库，同时在
# `docs/design/metric-dictionary.md` §4.2「市场情绪综合指数（spec §S1.13）」小节
# **逐字转录公开**。改权重 = 改本节 + bump :data:`COMPOSITE_CALIBER_VERSION` + 同步字典，
# 由 :func:`_assert_composite_spec`（导入期）挡住自相矛盾的配置。
COMPOSITE_METRIC_KEY = "market_sentiment_composite"

#: 合成指数的 `source`：与 scope 内其它本地派生指标同值（产地标签与底座一致）。
COMPOSITE_SOURCE = "local_calc"

#: **权重与方法版本**（spec：「变更时带版本号」+ ADR 0011 口径断点）。
#: 同一字符串写进 `MetricDef.caliber_version` 与**每一行**的 `caliber_version` ——
#: 可复算性的前提是"能指名要复现哪一版权重与方法"（ADR 0014 的 B 类要求）。
#: v1 的内容 = :data:`COMPOSITE_CONSTITUENTS` 的权重表 + 252/126 分位口径 +
#: `drop_and_renormalise` 缺失处置。
COMPOSITE_CALIBER_VERSION = "s1.13-v1"

#: 成分分位标准化的**滚动窗口**（单位 = **该成分自身的观测行数**，日频成分即 ≈252 个
#: 交易日）。与 `FACTORS['pct_rank']` 的默认窗口 252 同值 —— 同一个分位口径，不另造。
COMPOSITE_PERCENTILE_WINDOW = 252
#: 窗口内**有效样本**（非 NULL 观测）的下限；不足即该成分当日**不参与**合成
#: （不是"按 0 算"、不是"取最近值"、更不插值）。126 = 半个窗口。
COMPOSITE_PERCENTILE_MIN_SAMPLES = 126
#: 参与成分数下限：低于它当日的合成值落 NULL（1 个成分的"加权分位"不是合成指数）。
COMPOSITE_MIN_PARTICIPANTS = 2
#: 生效权重之和下限：防止"只剩一两个零碎小权重腿"时仍报出一个名为合成值的数字。
#: ⚠️ 与 :data:`COMPOSITE_PUBLISH_MIN_WEIGHT` **是两件事**，不得合并：
#: 本常量是**方法下限**（低于它连"算"都不算，`compose_series` 直接落 NULL）；
#: 后者是**发布闸门**（算得出数字，但腿太薄时**不作为值发布**，见下一条）。
COMPOSITE_MIN_EFFECTIVE_WEIGHT = 0.20

#: **发布闸门**：生效权重之和低于它 ⇒ 该日**不发布值**（`value=NULL` +
#: `extra.missing_reason='below_publish_weight'`），但行仍在、参与集与生效权重仍落
#: `extra`（可复算性与"缺数可见"两条都不丢）。
#:
#: 为什么在 0.20 之上再加一道 0.80（2026-10-16 产品裁决：用户口径 = 「只展示各腿齐备到
#: T-1 的值」）：实测 2026-10-12 各腿可得日不齐（期货/基差腿到 10-12、行情锚定的腿只到
#: 10-09），union 日历于是长出**3/11 条腿、生效权重 0.23** 的一行；它过了 0.20 的**方法**
#: 下限，就被当成"当天的市场情绪综合指数"发布 —— 可它与前一日（10 条腿、0.88）
#: **不是同一个东西**，读者会把 3 条腿的值直接与 10 条腿的值对比。
#: 0.80 的取值理由：11 条腿里**最重的单腿是 0.12**（`qvix_50etf`），故 0.80 蕴含
#: "至少 7 条腿参与"；而常态下只有 QVIX 一族缺值时是 **0.88**（10 条腿），仍在闸门之上
#: —— 闸门只挡"结构性缺腿"，不误伤"单源滞后"。
#: 改这个数 = 改发布口径 → 同步 :data:`COMPOSITE_MISSING_REASONS` 与字典 §4.2。
COMPOSITE_PUBLISH_MIN_WEIGHT = 0.80

#: 成分缺失的处置（**写死、公开、可机读**）：剔除该成分，对剩余成分的权重**重新归一化**
#: —— 绝不静默用 0、绝不插值、绝不前向填充。当天的实际参与集与生效权重落
#: `extra.participants` / `extra.effective_weights`（可复算性的前提）。
COMPOSITE_RENORMALIZATION = "drop_and_renormalise"

#: 综合指数缺数的原因码（与 §S1.8/§S1.9 同一条纪律：**缺数恒 NULL + 原因码，不落 0**）。
COMPOSITE_MISSING_REASONS: dict[str, str] = {
    "insufficient_participants": (
        "当日具备有效分位的成分数低于下限（extra.n_participants < extra.min_participants）"
        "—— 成分太少时「加权分位」不再是合成指数，落 NULL 不落数字"
    ),
    "insufficient_effective_weight": (
        "当日参与成分的配置权重之和低于下限（extra.effective_weight_sum < "
        "extra.min_effective_weight）—— 少数零碎腿的合成不具代表性，落 NULL"
    ),
    "below_publish_weight": (
        "当日生效权重之和过了方法下限但**未过发布闸门**（extra.min_effective_weight <= "
        "extra.effective_weight_sum < extra.publish_min_weight）—— 值算得出，但腿太薄，"
        "与齐备日的值不可比，故**不作为值发布**（`value=NULL`）。参与集/权重/分位仍在 "
        "`extra` 里，可复核；成因通常是各腿的**源侧可得日不齐**（如行情锚定的腿停在 T-1、"
        "而期货腿已到 T），由 `derived_metrics_service` 的收敛器在下一轮按 `target` 重算覆盖"
    ),
}


@dataclass(frozen=True)
class CompositeConstituent:
    """综合指数的一个成分：`metric_key` + 权重 + 方向 + 归属组 + 纳入理由.

    `direction` 是**风险偏好方向**（+1 = 原始值越高情绪越乐观；-1 = 反向，先取相反数
    再算分位）。它把"这条腿怎么读"写死成机读字段，而不是留在某处的注释里。
    """

    metric_key: str
    weight: float
    direction: int  # +1 / -1
    group: str
    rationale: str


#: 成分的**归属组权重**（组内权重之和 == 组权重；`_assert_composite_spec` 机械校验）。
#: 组 = 该成分出自哪一批 spec（§S1.8 / §S1.9 / §S1.10）—— 让"综合指数覆盖了哪几类情绪"
#: 可查，并防止某一类（如涨停族）因为 key 多而自动占大权重。
COMPOSITE_GROUP_WEIGHTS: dict[str, float] = {
    "volatility_derivatives": 0.35,  # spec §S1.10 波动率与衍生品
    "activity_leverage": 0.25,  # spec §S1.9 交易活跃度与杠杆资金
    "breadth_momentum": 0.40,  # spec §S1.8 市场宽度与动量
}

#: **v1 成分表**（11 条腿，权重之和 == 1.00）。纳入判据（逐条可查，见字典 §4.2）：
#: ① 日频（`freq='daily'`）—— 日频合成里纳入月/周频成分必然要前向填充 = 制造数据；
#: ② 风险偏好方向无歧义（`direction` 能写死）；③ 同组内不重复计同一信号（冗余腿剔除）；
#: ④ 该 key **未**被标注禁止进信号链路（`signal_chain='forbidden'` 一律排除）。
COMPOSITE_CONSTITUENTS: tuple[CompositeConstituent, ...] = (
    # ── 组 1：波动率与衍生品（§S1.10，0.35；三条腿：50ETF 隐含波动率 + IF/IC/IM 基差）──
    CompositeConstituent(
        "qvix_50etf",
        0.12,
        -1,
        "volatility_derivatives",
        "50ETF 期权隐含波动率 = 恐慌的直接价格；越高情绪越差（反向）。历史最长（2015-02 起）",
    ),
    CompositeConstituent(
        "if_basis",
        0.09,
        1,
        "volatility_derivatives",
        "IF（沪深300）期货基差 = 升贴水；升水 = 多头/对冲拥挤偏乐观，最深历史（2010-04 起）",
    ),
    CompositeConstituent(
        "ic_basis",
        0.07,
        1,
        "volatility_derivatives",
        "IC（中证500）期货基差；中小盘风险偏好更敏感（2015-04 起）",
    ),
    CompositeConstituent(
        "im_basis",
        0.07,
        1,
        "volatility_derivatives",
        "IM（中证1000）期货基差；小盘/投机情绪腿（2022-07 起），也是 2026-10 缺 QVIX 时的兜底腿",
    ),
    # ── 组 2：交易活跃度与杠杆资金（§S1.9，0.25）──
    CompositeConstituent(
        "idx_turnover_ratio_csi300",
        0.09,
        1,
        "activity_leverage",
        "沪深300 换手率（自由流通股本口径）= 参与度；比成交额更可比（成交额含长期抬升趋势）",
    ),
    CompositeConstituent(
        "margin_financing_balance",
        0.08,
        1,
        "activity_leverage",
        "融资余额 = 杠杆多头的存量意愿（交易所三所汇总，2023-05 起）",
    ),
    CompositeConstituent(
        "margin_financing_buy_ratio",
        0.08,
        1,
        "activity_leverage",
        "融资买入额占全市场成交额比 = 当日杠杆买入强度（快变量，与存量互补）",
    ),
    # ── 组 3：市场宽度与动量（§S1.8，0.40；2025-09 起，故只在近期参与）──
    CompositeConstituent(
        "above_ma20_ratio",
        0.12,
        1,
        "breadth_momentum",
        "站上 20 日均线占比 = 宽度（历史最长、无缺值的 §S1.8 腿）",
    ),
    CompositeConstituent(
        "advance_decline_ratio",
        0.10,
        1,
        "breadth_momentum",
        "涨跌比 = 当日方向性宽度；家数（分子分母）不再单独纳入以免重复计权",
    ),
    CompositeConstituent(
        "limit_up_break_ratio",
        0.09,
        -1,
        "breadth_momentum",
        "炸板率 = 涨停接力失败率；越高情绪越差（反向）",
    ),
    CompositeConstituent(
        "max_consecutive_limit_up",
        0.09,
        1,
        "breadth_momentum",
        "连板高度 = 投机热度（涨停族里与炸板率互补的另一面）",
    ),
)

#: 明令**不得**成为成分的 key（机读的"禁令挡板"）。`public_fund_equity_position` 是
#: spec §S1.12 写死的"仅作背景展示、不进信号链路"，把它当成分等于绕过该禁令。
COMPOSITE_FORBIDDEN_CONSTITUENT_KEYS: tuple[str, ...] = ("public_fund_equity_position",)

#: 被**刻意排除**、且理由必须公开可查的 key（不注册成成分，但留痕"为什么没有它"）。
#: 这里是**短因由**（机读、可查）；展开的逐条论证见字典 §4.2「市场情绪综合指数」小节。
COMPOSITE_EXCLUDED_KEYS: dict[str, str] = {
    "public_fund_equity_position": "spec §S1.12 明令不进信号链路；且实测周度",
    "new_fund_share": "月频；日频合成必须前向填充 = 制造数据",
    "etf_share_change": "方向不唯一（抄底/追涨两读皆通）",
    "southbound_net_buy": "方向有争议（离开 A 股 / 全球风险偏好上升）",
    "margin_short_balance": "与融资余额同属杠杆存量组且量级极小",
    "market_turnover_amount": "与沪深300换手率同组且含长期抬升趋势",
    "pe_csi300": "估值状态（慢变量），无唯一情绪方向",
    "erp_10y": "估值锚（慢变量），「便宜」与「恐慌」两读皆通",
    "cn_treasury_yield_10y": "无风险利率是宏观状态量，不是情绪",
    "ih_basis": "与 if_basis 同族（大盘）且历史更短",
    "ic_discount_rate": "由 ic_basis 确定性派生（同一信号）",
    "im_discount_rate": "由 im_basis 确定性派生（同一信号）",
    "qvix_300etf": "与 qvix_50etf 同族且历史更短",
    "qvix_500etf": "与 qvix_50etf 同族且历史更短",
    "qvix_cyb_etf": "与 qvix_50etf 同族且历史更短",
    "qvix_kcb_etf": "与 qvix_50etf 同族且历史更短",
    "market_advance_count": "是涨跌比的分子（重复计数）",
    "market_decline_count": "是涨跌比的分母（重复计数）",
    "limit_up_down_ratio": "与连板高度同属涨停族且历史更短",
    "first_board_promotion_ratio": "与炸板率同属接力成败信号",
    "idx_turnover_ratio_sse": "换手率族只取沪深300 一条代表",
    "idx_turnover_ratio_szse": "换手率族只取沪深300 一条代表",
    "idx_turnover_ratio_sse50": "换手率族只取沪深300 一条代表",
    "idx_turnover_ratio_bse50": "换手率族只取沪深300 一条代表",
    "idx_turnover_ratio_star": "换手率族只取一条代表；且仅有 244 天有值",
    "idx_turnover_ratio_chinext": "换手率族只取沪深300 一条代表",
    "industrial_capital_net_change": "事件驱动（一行=一次公告），非等间隔日频序列",
    "northbound_holding_quarterly": "季度；日频合成必然前向填充",
}

#: `INDUSTRIES['market']` 的**全部已登记指标**（合并顺序 = 登记顺序；S1.8 → S1.9/S1.11
#: → S1.10 → S1.7 → S1.12）。:data:`COMPOSITE_METRICS` 追加在最后（§S1.13 是消费方）。
MARKET_SENTIMENT_METRICS_ALL: list[MetricDef] = [
    *MARKET_METRICS,
    *MARKET_ACTIVITY_METRICS,
    *VOLATILITY_DERIVATIVES_METRICS,
    *CAPITAL_FLOWS_METRICS,
    *INVESTOR_BEHAVIOR_METRICS,
]

#: 综合指数是**派生**指标（无新产地），单位 = 分（0–100 的加权分位）。
#: ⚠️ 名称只用「市场情绪综合指数」（命名禁令见 spec §S1.13；本指标与 CNN 那个美股七项
#: 口径的同名指数**没有任何关系**，也**不得**借用其名称）。
COMPOSITE_METRICS: list[MetricDef] = [
    MetricDef(
        key=COMPOSITE_METRIC_KEY,
        name="市场情绪综合指数",
        unit="分",
        freq="daily",
        tier=TIER_DERIVED,
        sources=[COMPOSITE_SOURCE],
        group="composite",
        higher_is_better=True,
        caliber_version=COMPOSITE_CALIBER_VERSION,
        description=(
            "**自建**合成指标（spec §S1.13）：把 11 个已登记的日频情绪成分做**滚动分位"
            "标准化**（窗口 252 个成分观测行、最小有效样本 126）后按**公开权重表**加权求和，"
            "值域 0–100（50 ≈ 历史中位）。成分的「风险偏好方向」写死在 registry"
            "（direction=+1/-1，反向腿先取相反数再算分位）。**权重、窗口、缺失处置与"
            "版本号全部公开在 docs/design/metric-dictionary.md §4.2「市场情绪综合指数」**"
            f"（本节只给指针；方法版本 = {COMPOSITE_CALIBER_VERSION}）。"
            "**成分缺失的处置**：当日无有效分位的成分被**剔除**，剩余成分的权重**重新"
            "归一化**（drop_and_renormalise）—— **绝不**静默用 0、**绝不**插值或前向填充；"
            "当天的实际参与集 extra.participants、生效权重 extra.effective_weights 与"
            "逐成分明细 extra.constituents 落库，这是**可复算**的前提。"
            "参与成分数 < 2 或生效权重之和 < 0.20 时落 NULL + missing_reason"
            "（insufficient_participants / insufficient_effective_weight），**不落 0**。"
            "**PIT**：published_at = 当日**全部**成分行 published_at 的最大值"
            "（extra.pit_basis=max_constituent_published_at）—— 合成值不可能早于当天最晚"
            "定型的成分，**不回退 period**；成分行缺 published_at 时**响亮失败**。"
            "**允许进信号链路**（spec Ruling 65：被禁入的是第三方机构编制的情绪指数；"
            "本指标自建、算法公开、可逐日复算）。相关读面 = 通用指标读面"
            "（industry_key='market'），**没有第二张表、没有平行读路**。"
        ),
    ),
]


def _assert_composite_spec() -> None:
    """§S1.13 的合成配置必须**导入期自洽**（权重表是公开契约，错一位就静默改口径）。

    逐条（都能机械判定）：
    1. key 不重复、权重 > 0、direction ∈ {+1,−1}、group 已声明；
    2. 成分权重之和 == 1.00、每组权重之和 == 声明的组权重、组权重之和 == 1.00（容差 1e-9）；
    3. 每个成分**必须**是已登记的市场 scope 指标且 `freq == 'daily'`（日频纪律）；
    4. 分位口径自洽：`0 < min_samples <= window`；
    5. 参与下限自洽：`min_participants >= 2`、`0 < min_effective_weight <= 1`；
    6. **禁令挡板**：`COMPOSITE_FORBIDDEN_CONSTITUENT_KEYS` 里的 key 不得出现为成分；
    7. 综合指数自身不得成为自己的成分；被排除项必须有文字理由（不得"删了但没记"）；
    8. 综合指数的登记项与本节的口径常量**同值**，且描述里必须给出字典指针
       （spec：「权重与方法必须公开可查」—— 指针缺失 = 公开面断链）。
    """
    keys = [c.metric_key for c in COMPOSITE_CONSTITUENTS]
    if len(set(keys)) != len(keys):
        dup = sorted({k for k in keys if keys.count(k) > 1})
        raise RuntimeError(f"§S1.13 成分表有重复 key：{dup}")
    for c in COMPOSITE_CONSTITUENTS:
        if c.weight <= 0:
            raise RuntimeError(f"§S1.13 成分 {c.metric_key} 的权重必须为正（得到 {c.weight}）")
        if c.direction not in (1, -1):
            raise RuntimeError(
                f"§S1.13 成分 {c.metric_key} 的 direction 必须是 +1/-1（得到 {c.direction}）"
                " —— 风险偏好方向必须写死，不得留在注释里"
            )
        if c.group not in COMPOSITE_GROUP_WEIGHTS:
            raise RuntimeError(
                f"§S1.13 成分 {c.metric_key} 的 group={c.group!r} 未在 "
                f"COMPOSITE_GROUP_WEIGHTS 声明（{sorted(COMPOSITE_GROUP_WEIGHTS)}）"
            )
    total = sum(c.weight for c in COMPOSITE_CONSTITUENTS)
    if abs(total - 1.0) > 1e-9:
        raise RuntimeError(f"§S1.13 成分权重之和必须为 1.00（得到 {total}）")
    group_total = sum(COMPOSITE_GROUP_WEIGHTS.values())
    if abs(group_total - 1.0) > 1e-9:
        raise RuntimeError(f"§S1.13 组权重之和必须为 1.00（得到 {group_total}）")
    for group, declared in COMPOSITE_GROUP_WEIGHTS.items():
        actual = sum(c.weight for c in COMPOSITE_CONSTITUENTS if c.group == group)
        if abs(actual - declared) > 1e-9:
            raise RuntimeError(
                f"§S1.13 组 {group} 的成分权重之和 {actual} != 声明的组权重 {declared}"
            )
    registered = {m.key: m for m in MARKET_SENTIMENT_METRICS_ALL}
    for c in COMPOSITE_CONSTITUENTS:
        metric = registered.get(c.metric_key)
        if metric is None:
            raise RuntimeError(f"§S1.13 成分 {c.metric_key} 未在本 scope 登记 —— 引用落空")
        if metric.freq != "daily":
            raise RuntimeError(
                f"§S1.13 成分 {c.metric_key} 的 freq={metric.freq!r} ≠ 'daily'："
                "日频合成纳入低频成分必然要前向填充 = 制造数据"
            )
    if not 0 < COMPOSITE_PERCENTILE_MIN_SAMPLES <= COMPOSITE_PERCENTILE_WINDOW:
        raise RuntimeError("§S1.13 分位口径不自洽：须 0 < min_samples <= window")
    if COMPOSITE_MIN_PARTICIPANTS < 2:
        raise RuntimeError("§S1.13 参与成分下限必须 >= 2（1 个成分的加权分位不是合成指数）")
    if not 0 < COMPOSITE_MIN_EFFECTIVE_WEIGHT <= 1:
        raise RuntimeError("§S1.13 生效权重下限必须落在 (0, 1]")
    # 发布闸门与方法下限的关系必须写死：闸门**松于或等于**方法下限时它形同虚设
    # （永远不会有一条"算得出但不发布"的日子），那是配置错误而不是"更宽松的口径"。
    if not COMPOSITE_MIN_EFFECTIVE_WEIGHT < COMPOSITE_PUBLISH_MIN_WEIGHT <= 1:
        raise RuntimeError(
            "§S1.13 发布闸门必须严格高于方法下限且不超过 1：须 "
            "min_effective_weight < publish_min_weight <= 1（得到 "
            f"{COMPOSITE_MIN_EFFECTIVE_WEIGHT} / {COMPOSITE_PUBLISH_MIN_WEIGHT}）"
        )
    forbidden = sorted(set(keys) & set(COMPOSITE_FORBIDDEN_CONSTITUENT_KEYS))
    if forbidden:
        raise RuntimeError(
            f"§S1.13 禁令被绕过：{forbidden} 被标注为不得进信号链路，却出现在成分表里"
        )
    if COMPOSITE_METRIC_KEY in keys:
        raise RuntimeError("§S1.13 综合指数不得成为自己的成分（自我引用 = 无定义的递归）")
    if not COMPOSITE_EXCLUDED_KEYS:
        raise RuntimeError("§S1.13 有 key 未纳入成分却没有留下理由 —— 「不纳入」也要可查")
    if len(COMPOSITE_METRICS) != 1:
        raise RuntimeError("§S1.13 只登记一个合成指标（多一个就是另一条口径）")
    reg = COMPOSITE_METRICS[0]
    if reg.key != COMPOSITE_METRIC_KEY or reg.name != "市场情绪综合指数":
        raise RuntimeError(
            f"§S1.13 登记项与常量不一致：key={reg.key!r} name={reg.name!r}"
            "（名称只允许「市场情绪综合指数」）"
        )
    if reg.caliber_version != COMPOSITE_CALIBER_VERSION:
        raise RuntimeError(
            f"§S1.13 登记项的 caliber_version={reg.caliber_version!r} 与 "
            f"COMPOSITE_CALIBER_VERSION={COMPOSITE_CALIBER_VERSION!r} 不一致 —— "
            "版本号必须与权重表同一处、同一值"
        )
    if reg.freq != "daily":
        raise RuntimeError("§S1.13 综合指数是日频序列")
    if "docs/design/metric-dictionary.md" not in reg.description:
        raise RuntimeError(
            "§S1.13：综合指数的 description 必须给出方法文档指针"
            "（docs/design/metric-dictionary.md）—— 权重/窗口/归一化规则要公开可查"
        )
    for token in ("分位", "归一化", "published_at"):
        if token not in reg.description:
            raise RuntimeError(f"§S1.13：综合指数 description 缺关键词「{token}」")


_assert_composite_spec()

#: `INDUSTRIES['market']` **只有一个** `IndustryConfig`（scope 键必须唯一）：
#: §S1.8 的 8 个、§S1.9/S1.11 的 14 个与本批次 §S1.10 的 11 个 key **合并登记在同一个
#: scope** 下 —— 它们本来就落在同一张 `industry_metrics`、走同一个读面，
#: `metrics` 只是白名单来源。
#: `MARKET_SENTIMENT` 这个名字保留（既有测试断言 `INDUSTRIES["market"] is MARKET_SENTIMENT`），
#: 语义从"全市场情绪"扩展为"全市场（情绪 + 活跃度 + 估值 + 波动率与衍生品）"。
MARKET_SENTIMENT = IndustryConfig(
    key="market",
    name="全市场情绪 · 活跃度 · 估值 · 波动率 · 资金流 · 投资者行为 · 综合指数",
    description=(
        "全市场 scope（非行业）：情绪/市场宽度与动量（spec §S1.8）"
        " + 交易活跃度与杠杆资金（spec §S1.9） + 估值与风险溢价（spec §S1.11）"
        " + 波动率与衍生品（spec §S1.10）"
        " + 资金流四件套（spec §S1.7：宽基 ETF 份额变化 / 南向净买入 / "
        "两融（引用 §S1.9）/ 产业资本增减持（待接 S3.2）"
        " + 陆股通持仓（季度）只登记口径）"
        " + 投资者行为（spec §S1.12：新成立基金募集份额（月频，部分成员缺值时为**下界**）/ "
        "公募基金仓位（实测**周度**、口径不可复算，仅背景展示且不进信号链路）；"
        "新增散户开户数实测不可得 ⇒ 本 scope **不登记**该项，UI 无此项）"
        " + 市场情绪综合指数（spec §S1.13：由上面 11 个日频成分滚动分位加权合成，"
        "权重表与缺失归一化规则公开在 docs/design/metric-dictionary.md §4.2；"
        "自建、算法公开、可复算 ⇒ 允许进信号链路）。"
        "指标值与行业指标同表同读面，代码即配置"
    ),
    sw_l3_codes=[],  # 非行业 scope：不挂申万分类
    metrics=[*MARKET_SENTIMENT_METRICS_ALL, *COMPOSITE_METRICS],
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
