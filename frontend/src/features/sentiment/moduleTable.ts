/**
 * `/sentiment` 情绪分析页的**唯一模块表**（module → metrics → 文案）。
 *
 * 为什么是一张数据表而不是 8 个手写组件：10 个模块的差别只有"哪些 metric_key、哪句口径注、
 * 有没有后端分位"。把它写成数据后，页面只有**一个渲染器**（`SentimentModulePanel`）——
 * 新增/挪动一个指标 = 改这一处，不会出现"8 个组件里 3 个忘了标 missing_reason"。
 *
 * 三条纪律：
 *  1. **口径文本不在这里新编**：每条 `SentimentCaliberNote.text` 都是
 *     `docs/design/metric-dictionary.md` / `docs/design/data-source.md` 里已裁定的原文
 *     （只做删减，不改写语义），并带 `source` 指回出处；
 *  2. **分位只标后端已提供的**：`percentileField` 只能取 `/api/v1/market/sentiment/trends`
 *     真正返回的三个字段（zt_count / broken_rate / promo_1to2），前端不计算任何分位；
 *  3. **未落地的模块就地说明**：`kind: "unbuilt"` 的行渲染成结论 + 出处，
 *     不留空图表、不留假占位。
 */
import type { SentimentPercentileField } from "@/shared/api/limitUp";

/** 一条口径注：稳定 id（e2e / 报告引用）+ 出处 + 已裁定的原文。 */
export interface SentimentCaliberNote {
  id: string;
  /** 出处（spec 规则号 / 字典章节），只作追溯，不参与渲染逻辑 */
  source: string;
  /** 口径原文（删减自已裁定文档，不新编） */
  text: string;
}

/** 一条指标行：值来自 `GET /api/v1/metrics/latest?scope_keys=market`。 */
export interface SentimentMetricRow {
  kind: "metric";
  metricKey: string;
  /** 后端 `name` 取不到时的兜底标签（两者应逐字一致） */
  label: string;
  /** 该行附后端已提供的分位 chip；无此字段 = 只显示值 + 窗口/新鲜度 */
  percentileField?: SentimentPercentileField;
  /** 口径注（渲染在该行正下方） */
  caliber?: SentimentCaliberNote;
}

/**
 * 一条"只有分位"的行：`zt_count` 不是 `industry_metrics` 里的注册指标
 * （它住在 `market_sentiment_daily`），但 `/market/sentiment/trends` 给了它的历史分位。
 * 单独成一种行类型，是为了不在前端为它捏一个 `metric_key`。
 */
export interface SentimentPercentileRow {
  kind: "percentile";
  field: SentimentPercentileField;
  label: string;
  /** 说明这个值的来源与"为什么它没有 metric_key" */
  note: string;
}

/** 一条"本模块未落地"的结论行（spec 允许/要求就地说明，而不是留占位）。 */
export interface SentimentUnbuiltRow {
  kind: "unbuilt";
  /** 决定它未落地的规则号（S1.14 / S2.2） */
  ruling: string;
  headline: string;
  detail: string;
}

export type SentimentRow = SentimentMetricRow | SentimentPercentileRow | SentimentUnbuiltRow;

export interface SentimentModule {
  key: string;
  /** 页面锚点 id = `sentiment-module-${key}` */
  title: string;
  /** 这个模块回答读者的哪个问题（模块头下的一句话） */
  question: string;
  /** 模块级说明（可选） */
  note?: string;
  /** `true` = 不是表格式面板，由页面另行渲染（当前只有综合指数 hero） */
  hero?: boolean;
  rows: SentimentRow[];
}

export interface SentimentGroup {
  key: string;
  label: string;
  modules: SentimentModule[];
}

// ── 口径注（逐条指回出处；不新编文本）─────────────────────────────────────

/** S1.9 · 换手率的自由流通股本口径 */
const NOTE_TURNOVER: SentimentCaliberNote = {
  id: "turnover",
  source: "S1.9 · metric-dictionary §4.2",
  text:
    "7 个换手率一律自由流通股本口径。直取指数的 5 条取 TuShare index_dailybasic.turnover_rate_f（上游自身定义 = 成交量 / 自由流通股本 free_share，不做二次换算）；北证50 与科创综指因 index_dailybasic 不覆盖（实测返回 0 行且不报错）改自建 = 成分股 daily_basic.turnover_rate_f 按 index_weight.weight 的权重加权平均。总股本口径 turnover_rate 不落库，只在 extra.turnover_rate_total 留痕供对账。",
};

/** S1.10 · 升贴水符号约定 */
const NOTE_BASIS_SIGN: SentimentCaliberNote = {
  id: "basis-sign",
  source: "S1.10 · metric-dictionary §4.2",
  text:
    "升贴水的符号约定（唯一口径，前端不得自行翻转符号）：基差 = 期货 − 现货；正值 = 升水（期货高于现货），负值 = 贴水（期货低于现货）。机器可读标记 sign_convention=futures_minus_spot。贴水率 = 基差 / 现货指数收盘价 × 100，符号与基差一致（负 = 贴水、正 = 升水）。",
};

/** S1.10 · QVIX 的产地与发布滞后 */
const NOTE_QVIX_LAG: SentimentCaliberNote = {
  id: "qvix-lag",
  source: "S1.10 · data-source 附.8",
  text:
    "QVIX 中国波指是第三方编制、不是交易所官方口径（官方的上证50ETF波动率指数 iVIX 已于 2018-02-22 起暂停发布），且源侧有 1–3 个交易日的发布滞后。这些日子落 NULL + missing_reason='source_not_updated'，不是落 0、也不是落最近值。",
};

/** S1.11 · PE 口径与所指数 */
const NOTE_PE: SentimentCaliberNote = {
  id: "pe-caliber",
  source: "S1.11 · metric-dictionary §4.2",
  text:
    "PE 口径与所指数：整体法（指数总市值 / 指数口径净利润，不是成分中位数）、指数 = 沪深300（000300.SH），取 TuShare index_dailybasic.pe。erp_10y = 1/PE × 100 − cn_treasury_yield_10y（单位：百分点）；PE 单独落 pe_csi300，1/PE 这一半可从库里独立复原。",
};

/** S1.12 · 频率与发布滞后 + 公募基金仓位的产地限制 */
const NOTE_FUND_LAG: SentimentCaliberNote = {
  id: "fund-lag",
  source: "S1.12 · metric-dictionary §4.2 / data-source 附.10",
  text:
    "公募基金仓位（public_fund_equity_position）：频率 = weekly（周度，按产地实测登记：455 行 / 2017-12-04…2026-09-30，相邻观测间隔 6–8 天占 90.3%）；发布滞后 = 约 15 个工作日（推算值，上游无发布字段）。产地 akshare.fund_stock_position_lg（乐咕乐股 type=pos_stock）是第三方编制（source_tier='calc'），不是官方口径；上游 JSON 只有 date / close / position，无报告期、无发布日期、无编制方法 ⇒ 口径不可复算，该观测日也不是报告期。仅背景展示，不进任何信号链路。",
};

/** S1.12 · new_fund_share 的下界口径 */
const NOTE_LOWER_BOUND: SentimentCaliberNote = {
  id: "lower-bound",
  source: "S1.12 · metric-dictionary §4.2（Ruling 64）",
  text:
    "new_fund_share 在有成员缺值的月份只是「有值成员之和」= 该月真实规模的**下界**，不是完整总额（实测 45 个月中有 42 个月如此，只有 3 个月成员齐全）；机读标记 extra.is_lower_bound=true 是必要非充分条件 —— 前端必须把「下界」标出来，不得让使用者读成完整口径。",
};

/** 跨模块 · 缺失纪律（不落 0） */
const NOTE_MISSING: SentimentCaliberNote = {
  id: "missing-none",
  source: "S0.4 / ADR 0014 · metric-dictionary §4.2",
  text:
    "缺数不静默为 0：缺失一律 value = NULL + extra.missing_reason + 覆盖率对账，绝不落 0（0 是「市场毫无波动 / 没有净申购」这类事实断言，与「没有数据」相反）。参与成分指标配置权重之和 < 0.20（insufficient_effective_weight）同样落 NULL。",
};

/** S1.13 · 综合指数的权重/方法与「N/11 个成分指标」披露（契约字段名仍为 `legs` / `leg_count` / `total_legs`） */
export const NOTE_COMPOSITE: SentimentCaliberNote = {
  id: "composite-weights",
  source: "S1.13 · metric-dictionary §4.2（Ruling 65/66）",
  text:
    "综合指数口径：11 个日频情绪成分指标各自做滚动分位标准化（窗口 252 行、最小有效样本 126；反向的成分指标先取相反数），再按公开权重表加权求和 × 100：value = 100 × Σ w'_i × p_i，w'_i = w_i / Σ_(当日参与集) w_j。权重表与方法公开可查（唯一事实源 industry_registry.COMPOSITE_*，转录见 docs/design/metric-dictionary.md §4.2）；换权重 / 换窗口 / 换缺失规则 = bump caliber_version。各成分指标最新日不同 ⇒ 最新交易日的参与成分指标数会显著变少（实测 2026-10-12 仅 3/11 个成分指标、生效权重 0.23），不得把 3 个成分指标合成的值读成与 10 个等权。",
};

// ── 模块 ──────────────────────────────────────────────────────────────────

/** ⑨ 综合情绪指数：不是表格面板，而是整页唯一的 hero 卡（由 CompositeHero 渲染）。 */
export const COMPOSITE_MODULE: SentimentModule = {
  key: "composite",
  title: "市场情绪综合指数",
  question: "今天的情绪整体处在历史的什么位置？这个值有多薄？",
  hero: true,
  rows: [],
};

/** ⑩ 情绪预警与预判：依赖 S2.2 信号引擎。 */
const ALERT_MODULE: SentimentModule = {
  key: "alert",
  title: "情绪预警与预判",
  question: "情绪什么时候会越界？越界之后有没有信号与提醒？",
  rows: [
    {
      kind: "unbuilt",
      ruling: "S2.2",
      headline: "本模块未落地：依赖 S2.2 信号引擎",
      detail:
        "spec 的验收路径是「情绪历史分位 >90% / <10% → 规则 → 信号 → 提醒 → 站内可查」，其中规则引擎与信号表（signals / alerts）属于 S2.2，尚未建成。本页不画没有数据来源的预警线，也不做假占位。",
    },
  ],
};

/** ④ 市场宽度与动量（S1.8）。 */
const BREADTH_MODULE: SentimentModule = {
  key: "breadth",
  title: "市场宽度与动量",
  question: "赚钱效应是扩散还是收缩？涨停接力还接得住吗？",
  rows: [
    { kind: "metric", metricKey: "market_advance_count", label: "上涨家数" },
    { kind: "metric", metricKey: "market_decline_count", label: "下跌家数" },
    { kind: "metric", metricKey: "advance_decline_ratio", label: "涨跌比" },
    { kind: "metric", metricKey: "limit_up_down_ratio", label: "涨跌停比" },
    { kind: "metric", metricKey: "above_ma20_ratio", label: "站上 20 日均线占比" },
    {
      kind: "metric",
      metricKey: "limit_up_break_ratio",
      label: "炸板率",
      percentileField: "broken_rate",
    },
    { kind: "metric", metricKey: "max_consecutive_limit_up", label: "连板高度" },
    {
      kind: "metric",
      metricKey: "first_board_promotion_ratio",
      label: "首板晋级率",
      percentileField: "promo_1to2",
    },
    {
      kind: "percentile",
      field: "zt_count",
      label: "涨停家数",
      note:
        "该值住在 market_sentiment_daily、不是 industry_metrics 的注册 metric_key，故这里只给它的历史分位（值随分位窗口内的末点给出），不为它捏一个 key。",
    },
  ],
};

/** ① 交易活跃度（S1.9）。 */
const ACTIVITY_MODULE: SentimentModule = {
  key: "activity",
  title: "交易活跃度",
  question: "钱有没有进场？7 个指数的参与度谁高谁低？",
  rows: [
    { kind: "metric", metricKey: "market_turnover_amount", label: "全市场成交额" },
    { kind: "metric", metricKey: "idx_turnover_ratio_sse", label: "上证指数换手率" },
    { kind: "metric", metricKey: "idx_turnover_ratio_szse", label: "深证成指换手率" },
    { kind: "metric", metricKey: "idx_turnover_ratio_bse50", label: "北证50换手率" },
    { kind: "metric", metricKey: "idx_turnover_ratio_star", label: "科创综指换手率" },
    { kind: "metric", metricKey: "idx_turnover_ratio_chinext", label: "创业板指换手率" },
    { kind: "metric", metricKey: "idx_turnover_ratio_sse50", label: "上证50换手率" },
    {
      kind: "metric",
      metricKey: "idx_turnover_ratio_csi300",
      label: "沪深300换手率",
      caliber: NOTE_TURNOVER,
    },
  ],
};

/** ② 杠杆资金（S1.9）。 */
const LEVERAGE_MODULE: SentimentModule = {
  key: "leverage",
  title: "杠杆资金",
  question: "杠杆多头在加还是在撤？",
  rows: [
    { kind: "metric", metricKey: "margin_financing_balance", label: "融资余额" },
    { kind: "metric", metricKey: "margin_financing_buy_ratio", label: "融资买入额占比" },
    { kind: "metric", metricKey: "margin_short_balance", label: "融券余额" },
  ],
};

/** ⑤ 波动率与衍生品（S1.10）。 */
const VOLATILITY_MODULE: SentimentModule = {
  key: "volatility",
  title: "波动率与衍生品",
  question: "期权市场在给多高的恐慌定价？期指是升水还是贴水？",
  rows: [
    { kind: "metric", metricKey: "qvix_50etf", label: "50ETF期权隐含波动率", caliber: NOTE_QVIX_LAG },
    { kind: "metric", metricKey: "qvix_300etf", label: "300ETF期权隐含波动率" },
    { kind: "metric", metricKey: "qvix_500etf", label: "500ETF期权隐含波动率" },
    { kind: "metric", metricKey: "qvix_cyb_etf", label: "创业板ETF期权隐含波动率" },
    { kind: "metric", metricKey: "qvix_kcb_etf", label: "科创50ETF期权隐含波动率" },
    { kind: "metric", metricKey: "if_basis", label: "IF股指期货基差", caliber: NOTE_BASIS_SIGN },
    { kind: "metric", metricKey: "ic_basis", label: "IC股指期货基差" },
    { kind: "metric", metricKey: "ih_basis", label: "IH股指期货基差" },
    { kind: "metric", metricKey: "im_basis", label: "IM股指期货基差" },
    { kind: "metric", metricKey: "ic_discount_rate", label: "IC贴水率" },
    { kind: "metric", metricKey: "im_discount_rate", label: "IM贴水率" },
  ],
};

/** ⑥ 资金流向（S1.7）。 */
const CAPITAL_FLOW_MODULE: SentimentModule = {
  key: "capital-flow",
  title: "资金流向",
  question: "增量资金从哪个成分指标进来？哪几个成分指标今天根本没有数字？",
  rows: [
    { kind: "metric", metricKey: "etf_share_change", label: "宽基 ETF 份额变化" },
    { kind: "metric", metricKey: "southbound_net_buy", label: "南向资金净买入" },
    {
      kind: "metric",
      metricKey: "industrial_capital_net_change",
      label: "产业资本净增减持",
      caliber: NOTE_MISSING,
    },
    { kind: "metric", metricKey: "northbound_holding_quarterly", label: "陆股通持仓（季度）" },
  ],
};

/** ⑦ 估值与风险溢价（S1.11）。 */
const VALUATION_MODULE: SentimentModule = {
  key: "valuation",
  title: "估值与风险溢价",
  question: "股票相对债券便宜到什么程度？",
  rows: [
    { kind: "metric", metricKey: "erp_10y", label: "股债收益差（10年期）", caliber: NOTE_PE },
    { kind: "metric", metricKey: "pe_csi300", label: "沪深300市盈率（整体法）" },
    { kind: "metric", metricKey: "cn_treasury_yield_10y", label: "10年期国债收益率" },
  ],
};

/** ③ 投资者行为（S1.12）。 */
const INVESTOR_MODULE: SentimentModule = {
  key: "investor",
  title: "投资者行为",
  question: "增量资金的入场行为：新基金发了多少？公募仓位在哪一档？",
  note: "ETF 份额变化复用「资金流向」模块的同一个 metric_key（etf_share_change），此处不重复列示。",
  rows: [
    { kind: "metric", metricKey: "new_fund_share", label: "新成立基金募集份额", caliber: NOTE_LOWER_BOUND },
    {
      kind: "metric",
      metricKey: "public_fund_equity_position",
      label: "公募基金仓位（股票型基金·总仓位，背景参考）",
      caliber: NOTE_FUND_LAG,
    },
  ],
};

/** ⑧ 散户舆情（S1.14，已 scoped out）。 */
const RETAIL_MODULE: SentimentModule = {
  key: "retail",
  title: "散户舆情",
  question: "散户在关注什么？",
  rows: [
    {
      kind: "unbuilt",
      ruling: "S1.14",
      headline: "本模块按 spec 标 superseded：无稳定数据源",
      detail:
        "媒体平台股市搜索热度没有稳定 API，需要爬虫 + 反爬。spec §S1.14 的判据写明「若源不可得 → 本 spec 直接标 superseded，不在 UI 留占位」，故这里只保留结论：没有占位图表，也没有空态数字。",
    },
  ],
};

/** 分组 = 读者的提问顺序（先看结果，再拆市场内部 / 价格之外 / 参与者）。 */
export const SENTIMENT_GROUPS: SentimentGroup[] = [
  { key: "result", label: "结果", modules: [ALERT_MODULE] },
  { key: "internal", label: "市场内部", modules: [BREADTH_MODULE, ACTIVITY_MODULE, LEVERAGE_MODULE] },
  { key: "beyond-price", label: "价格之外", modules: [VOLATILITY_MODULE, CAPITAL_FLOW_MODULE, VALUATION_MODULE] },
  { key: "participants", label: "参与者", modules: [INVESTOR_MODULE, RETAIL_MODULE] },
];

/** 左侧粘性模块索引：与 `SENTIMENT_GROUPS` 同源（唯一真相），hero 作为「结果」组的第一项。 */
export const MODULE_INDEX: Array<{
  key: string;
  label: string;
  items: Array<{ key: string; title: string; anchor: string }>;
}> = SENTIMENT_GROUPS.map((group) => ({
  key: group.key,
  label: group.label,
  items: [
    ...(group.key === "result"
      ? [{ key: COMPOSITE_MODULE.key, title: COMPOSITE_MODULE.title, anchor: `sentiment-module-${COMPOSITE_MODULE.key}` }]
      : []),
    ...group.modules
      .filter((m) => !m.hero)
      .map((m) => ({ key: m.key, title: m.title, anchor: `sentiment-module-${m.key}` })),
  ],
}));

/** 页面渲染器要消费的全部模块（hero 除外，它由 CompositeHero 渲染）。 */
export const PANEL_MODULES: SentimentModule[] = SENTIMENT_GROUPS.flatMap((g) =>
  g.modules.filter((m) => !m.hero),
);

/** 页面需要向后端索取的 metric_key 全集（由模块表派生，防"表里有、请求里漏了"）。 */
export const SENTIMENT_METRIC_KEYS: string[] = Array.from(
  new Set(
    PANEL_MODULES.flatMap((m) =>
      m.rows.filter((r): r is SentimentMetricRow => r.kind === "metric").map((r) => r.metricKey),
    ),
  ),
);

/** 页面需要向 `/market/sentiment/trends` 取分位的字段全集。 */
export const SENTIMENT_PERCENTILE_FIELDS: SentimentPercentileField[] = Array.from(
  new Set(
    PANEL_MODULES.flatMap((m) =>
      m.rows.flatMap((r) =>
        r.kind === "metric" && r.percentileField
          ? [r.percentileField]
          : r.kind === "percentile"
            ? [r.field]
            : [],
      ),
    ),
  ),
);
