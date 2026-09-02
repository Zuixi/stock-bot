"""Industry metric registry — 投研工作台的单一事实源.

指标定义 / 数据源分级 / 参考区间 / 展示分组 / 周期阶段 / 仓位模板全部在此声明，
API 与前端组件均由其驱动：新增一列、换一个预警阈值、接入一个新行业 = 改配置，
零前端改动、零新表。metric_key 命名与 docs/design/data-source.md 对齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ── 数据源层级（UI 徽章 + 权威性裁决） ────────────────────────────────
TIER_OFFICIAL = "official"    # 官方基准：农业农村部/发改委/统计局/交易所
TIER_HIGHFREQ = "highfreq"    # 高频参考：Mysteel/涌益/卓创/生意社等
TIER_CALC = "calc"            # 测算：基于公开数据推算（如头均市值）
TIER_MANUAL = "manual"        # 人工录入：公告/纪要/年报
TIER_DERIVED = "derived"      # 派生：由基础指标计算（猪粮比等）

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
    sources: list[str]              # ingest/查询源优先级（高→低）；mock 永远垫底，演示数据不得压过真实源
    group: str = "quick"            # strip | quick | supply | cost
    strip: bool = False             # 进入综合指标带
    spark: bool = False             # 指标带附迷你走势
    higher_is_better: bool | None = None  # 涨跌颜色语义；None=中性
    warn_bands: list[WarnBand] = field(default_factory=list)
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
    key: str
    name: str
    description: str
    sw_l3_codes: list[str]
    metrics: list[MetricDef]
    phases: list[PhaseDef]
    position_templates: dict[str, list[PositionSlice]]
    reference_points: list[ReferencePointDef] = field(default_factory=list)

    def metric(self, key: str) -> MetricDef | None:
        return next((m for m in self.metrics if m.key == key), None)

    @property
    def strip_metrics(self) -> list[MetricDef]:
        return [m for m in self.metrics if m.strip]

    @property
    def quick_metrics(self) -> list[MetricDef]:
        return [m for m in self.metrics if m.group == "quick"]

    def position_template(self, signal_type: str) -> list[PositionSlice]:
        return self.position_templates.get(
            signal_type, self.position_templates.get("关注", [])
        )


# ── 信号类型 ─────────────────────────────────────────────────────────
SIGNAL_EMPTY = "空仓"
SIGNAL_WATCH = "关注"
SIGNAL_BUY = "买入"
SIGNAL_SELL = "卖出"
SIGNAL_COLORS: dict[str, str] = {
    SIGNAL_BUY: "#ef4444",     # 红涨绿跌惯例：做多=红
    SIGNAL_SELL: "#22c55e",
    SIGNAL_WATCH: "#faad14",
    SIGNAL_EMPTY: "#8c8c8c",
}


def _position_slices(core: int, band: int, cash: int) -> list[PositionSlice]:
    return [
        PositionSlice("核心底仓", "做周期 · 长持", "成本与现金流领先的头部企业，穿越周期持有", core, "#1677ff"),
        PositionSlice("波段仓位", "做波动 · 择时", "畜牧 ETF / 高 β 龙头，跟随信号加减", band, "#faad14"),
        PositionSlice("现金储备", "底部加仓弹药", "货基 / 国债逆回购，预留极端行情", cash, "#94a3b8"),
    ]


# ── 生猪养殖（猪智投） ───────────────────────────────────────────────
PIG_METRICS: list[MetricDef] = [
    MetricDef(
        key="hog_price", name="生猪均价", unit="元/kg", freq="daily",
        tier=TIER_HIGHFREQ, sources=["akshare_100ppi", "mock"],
        group="quick", strip=True, spark=True, higher_is_better=True,
        description="全国生猪出栏均价；官方批发价为基准，本值用于跟踪边际变化",
    ),
    MetricDef(
        key="corn_price", name="玉米价格", unit="元/kg", freq="daily",
        tier=TIER_HIGHFREQ, sources=["akshare_100ppi", "mock"],
        group="quick", higher_is_better=True,
        description="饲料成本端主要原料",
    ),
    MetricDef(
        key="soybean_meal_price", name="豆粕价格", unit="元/kg", freq="daily",
        tier=TIER_HIGHFREQ, sources=["akshare_100ppi", "mock"],
        group="quick", higher_is_better=True,
        description="饲料成本端蛋白原料",
    ),
    MetricDef(
        key="pork_wholesale", name="猪肉批发价", unit="元/kg", freq="daily",
        tier=TIER_OFFICIAL, sources=["manual", "mock"],
        group="quick", higher_is_better=True,
        description="农业农村部全国农产品批发市场猪肉均价",
    ),
    MetricDef(
        key="piglet_price_15kg", name="仔猪价格（15kg）", unit="元/kg", freq="weekly",
        tier=TIER_HIGHFREQ, sources=["manual", "mock"],
        group="quick", higher_is_better=True,
        description="补栏情绪的先行指标",
    ),
    MetricDef(
        key="lh_future_main", name="生猪期货主力", unit="元/吨", freq="daily",
        tier=TIER_OFFICIAL, sources=["akshare_sina", "mock"],
        group="quick", strip=True, spark=True, higher_is_better=True,
        description="DCE 生猪期货主力连续，远月价格反映市场对未来供需的预期",
    ),
    MetricDef(
        key="hog_corn_ratio", name="猪粮比", unit="", freq="daily",
        tier=TIER_DERIVED, sources=["derived"],
        group="quick", strip=True, spark=True,
        warn_bands=[
            WarnBand(5.0, "一级预警", "danger"),
            WarnBand(6.0, "二级预警", "warn"),
            WarnBand(9.0, "正常", "info"),
            WarnBand(None, "过度上涨", "danger"),
        ],
        description="生猪价格/玉米价格，行业盈亏核心指标（自算口径，与发改委周度口径略有差异）",
    ),
    MetricDef(
        key="sow_inventory", name="能繁母猪存栏", unit="万头", freq="monthly",
        tier=TIER_OFFICIAL, sources=["stats_gov", "mock"],
        group="supply", strip=True, spark=True,
        description="农业农村部月度环比 + 统计局季度末绝对数，产能最终基准（10 个月生产时滞）",
    ),
    MetricDef(
        key="sow_inventory_mom", name="能繁存栏环比", unit="%", freq="monthly",
        tier=TIER_DERIVED, sources=["derived"],
        group="supply", higher_is_better=None,
        description="由能繁存栏序列计算，连续为负即产能去化",
    ),
    MetricDef(
        key="industry_cost_avg", name="行业平均完全成本", unit="元/kg", freq="monthly",
        tier=TIER_MANUAL, sources=["manual", "mock"],
        group="cost",
        description="协会调研/研报口径，季度更新后线性插值为月度",
    ),
    MetricDef(
        key="msy", name="MSY（行业均值）", unit="头/年", freq="yearly",
        tier=TIER_MANUAL, sources=["manual", "mock"],
        group="quick", higher_is_better=True,
        description="每头能繁母猪年提供出栏肥猪数",
    ),
    MetricDef(
        key="psy", name="PSY（行业均值）", unit="头", freq="yearly",
        tier=TIER_MANUAL, sources=["manual", "mock"],
        group="quick", higher_is_better=True,
        description="每头能繁母猪年提供断奶仔猪数",
    ),
    MetricDef(
        key="feed_meat_ratio", name="料肉比", unit="", freq="yearly",
        tier=TIER_MANUAL, sources=["manual", "mock"],
        group="quick", higher_is_better=False,
        description="消耗饲料/增重，越低效率越高",
    ),
]

PIG_INDUSTRY = IndustryConfig(
    key="pig",
    name="生猪养殖",
    description="猪智投 · 农林牧渔-养殖业-生猪养殖（申万Ⅲ级）",
    sw_l3_codes=["110301"],
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
            "sow_inventory", "正常保有量", 4100, date(2021, 1, 1),
            "《生猪产能调控实施方案（2021）》",
        ),
        ReferencePointDef(
            "sow_inventory", "正常保有量", 3900, date(2024, 3, 1),
            "《生猪产能调控实施方案（2024年修订）》",
        ),
        ReferencePointDef(
            "sow_inventory", "正常保有量", 3750, date(2026, 1, 1),
            "《生猪产能综合调控实施方案（2026年修订）》",
        ),
    ],
)

INDUSTRIES: dict[str, IndustryConfig] = {PIG_INDUSTRY.key: PIG_INDUSTRY}


def get_industry(key: str) -> IndustryConfig | None:
    return INDUSTRIES.get(key)
