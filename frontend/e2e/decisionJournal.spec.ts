import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * `/me?tab=journal`「决策日志」e2e（S9：spec §S9.1/§S9.4/§S9.5，计划 Task 5–6）。
 *
 * ## 为什么全部 mock
 *
 * 本文件的每条判据都只依赖**响应 → 渲染**这一段：表单三必填的禁用态、创建后条目出现、
 * `due` → 复盘 → 移到 `reviewed`、未选归因时确认按钮禁用、统计的「样本不足 vs 占比」、
 * 以及"已复盘条目没有编辑/删除入口"。把 `/api/v1/journal*` 用 `page.route` 钉住之后，
 * 判据不随当天日期变化，也不依赖后端新端点是否已上线、本机是否已有记录
 * （否则"今天恰好没到期条目"会让复盘那条假红，"恰好有 5 条历史"又会让样本不足那条假绿）。
 *
 * ## 只 mock session 是"假登录"——这里把页面消费的读面一起 mock
 *
 * `research.spec.ts` 的教训（`signalPanel.spec.ts` 文件头有完整版）：没真 cookie 时只
 * mock `/auth/session` 会让其它真接口 401 → 全局 `auth:unauthorized` 清态 → 页面反复重挂。
 * 故本文件把 `/auth/session`、`/auth/csrf` 与 journal 的全部读写面（列表/统计/创建/复盘）
 * **一起** mock，没有真接口参与。
 *
 * ## mock 是"有状态的"
 *
 * 复盘用例要断言"条目从待复盘**移到**已复盘"：如果 `POST /review` 之后 `GET ?status=`
 * 仍返回旧数据，移库那条就永远绿（或永远红）。所以这里用一个 per-test 的内存 store：
 * 创建往 `open` 追加、复盘把条目改成 `reviewed` 并挂上 review，列表按 `status` 过滤。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const USER = {
  id: "usr-e2e-journal",
  username: "journal_writer",
  email: "journal@example.com",
  display_name: "日志作者",
  status: "active",
  is_superuser: false,
  roles: ["reader"],
  permissions: ["stocks:read", "watchlist:manage"],
  created_at: "2026-10-01T00:00:00Z",
};

/** 本页可见文本里**一个都不许出现**的判断词（S8 词表中与本页文案相关的子集）。 */
// 下面这行必须**逐字**列出禁用词才有断言对象，故给它行级豁免（扫描器会打印豁免行，评审可见）
const FORBIDDEN = ["买入", "卖出", "推荐", "看好", "建议关注", "目标价", "机会", "布局"]; // compliance-allow: 断言清单本身必须包含禁用词

interface FixtureReview {
  id: number;
  entry_id: number;
  reviewed_at: string;
  outcome_pct: number | null;
  bench_pct: number | null;
  excess_pct: number | null;
  invalidation_hit: boolean | null;
  /** S9.7.2：净口径（毛 − 12bp）；Phase A 未上线时可缺省。 */
  net_outcome_pct?: number | null;
  net_excess_pct?: number | null;
  verdict: string;
  note: string | null;
}

/**
 * 自动结果（契约见 `shared/api/journal.ts` 的 `JournalEvaluation`）。
 *
 * S9.7 的四个字段刻意都是**可选**：Phase A 的后端可能只给毛口径，页面必须优雅退化，
 * 所以"净字段缺省"是这里的一等公民（否则 e2e 只能验证新后端，一遇到旧响应就白屏）。
 */
interface FixtureEvaluation {
  outcome_pct: number | null;
  bench_pct: number | null;
  excess_pct: number | null;
  invalidation_hit: boolean | null;
  net_outcome_pct?: number | null;
  net_excess_pct?: number | null;
  bench_basis_note?: string | null;
  bench_fallback?: boolean | null;
  /** S9.7.4 路径分析（Phase B 后端未上线时可缺省）。 */
  mae_pct?: number | null;
  first_touch_invalidation?: string | null;
  touched_then_reversed?: boolean | null;
  days_to_invalidation?: number | null;
  /** S9.7.8：失效条件是否可观测。 */
  invalidation_observable?: boolean | null;
}

interface FixtureEntry {
  id: number;
  symbol: string | null;
  kind: string;
  thesis: string;
  invalidation: { kind: string; value: number | string };
  horizon_days: number;
  snapshot_summary: string;
  created_at: string;
  due_on: string;
  status: "open" | "due" | "reviewed";
  /** S9.7.3：创建时固化的基准（Phase A 未上线时可缺省）。 */
  snapshot?: {
    bench_spec?: { bet_type?: string; fallback?: boolean } | null;
    bench_basis_note?: string | null;
  } | null;
  /** S9.7.3：赌注类型（Phase A 未上线时可缺省）。 */
  bet_type?: string | null;
  /** S9.7.1：仓位粗档（Phase B 未上线时可缺省；缺省时列表**不显示**标签）。 */
  position_size?: string | null;
  /** S9.7.5：事前信心 1–5（缺省 = 不参与校准）。 */
  confidence?: number | null;
  /** S9.7.8：`false` 时**不提供**复盘入口（缺省 = 可复盘）。 */
  reviewable?: boolean | null;
  /** 自动结果（到期后才有；契约见 `shared/api/journal.ts` 的 `JournalEvaluation`）。 */
  evaluation: FixtureEvaluation | null;
  review: FixtureReview | null;
}

interface FixtureStats {
  n: number;
  reviewed_n: number;
  by_verdict: Record<string, number>;
  avg_excess_pct: number | null;
  discipline_break_share: number | null;
  /** S9.7.6：五种归档标签各自条数（合计 = n）。 */
  by_kind?: Record<string, number> | null;
  /** S9.7.2：净口径平均超额。 */
  avg_net_excess_pct?: number | null;
  /** S9.7.7：比例 + Wilson 95% 区间。 */
  share_ci?: Record<string, { n: number; successes: number; ci_low: number; ci_high: number }> | null;
  net_cost_bps?: number | null;
  /** S9.7.7：固定标注（页面原样展示）。 */
  multiple_comparison_note?: string | null;
  /** S9.7.1：四种仓位粗档各自条数（Phase B 未上线时可缺省）。 */
  by_size?: Record<string, number> | null;
  /** S9.7.1：按 `size_weight` 加权的平均超额（`none` 排除）。 */
  avg_weighted_excess_pct?: number | null;
  /** S9.7.5：校准曲线（样本不足时为 `null`）。 */
  calibration?: {
    n: number;
    bins?: {
      confidence: number;
      p?: number | null;
      n: number;
      hit_rate?: number | null;
      sufficient?: boolean | null;
    }[];
    brier?: number | null;
  } | null;
  /** S9.7.5：样本不足的原因（总样本不足时只显示它）。 */
  calibration_note?: string | null;
  /** S9.7.9：导出端点（缺省时页面拼固定路径）。 */
  export_hint?: string | null;
}

/**
 * 一条**到期待复盘**的条目。thesis 刻意带换行：列表行只展示第一行
 * （`journal-entry__thesis` 的 `white-space: nowrap` + 首行截取），全文不铺开。
 */
const DUE_ENTRY: FixtureEntry = {
  id: 101,
  symbol: "600519",
  kind: "watch",
  thesis: "渠道库存回到常态\n第二行不该出现在列表行里",
  invalidation: { kind: "price_below", value: 1500 },
  horizon_days: 30,
  snapshot_summary: "价格 1588.00 元 · 当日 +1.20% · 情绪分位 62",
  created_at: "2026-09-16T01:00:00Z",
  due_on: "2026-10-16",
  status: "due",
  evaluation: { outcome_pct: -3.5, bench_pct: -1, excess_pct: -2.5, invalidation_hit: true },
  review: null,
};

/** 一条**已复盘**的条目：带归因标签、超额与备注（页面只读，没有编辑/删除入口）。 */
const REVIEWED_ENTRY: FixtureEntry = {
  id: 102,
  symbol: null,
  kind: "pass",
  thesis: "市场级：连续缩量，等下一次放量再说",
  invalidation: { kind: "date_only", value: "2026-10-10" },
  horizon_days: 7,
  snapshot_summary: "价格 — · 情绪分位 40",
  created_at: "2026-10-03T01:00:00Z",
  due_on: "2026-10-10",
  status: "reviewed",
  evaluation: { outcome_pct: 1.5, bench_pct: 0.7, excess_pct: 0.8, invalidation_hit: true },
  review: {
    id: 9001,
    entry_id: 102,
    reviewed_at: "2026-10-11T01:00:00Z",
    outcome_pct: 1.5,
    bench_pct: 0.7,
    excess_pct: 0.8,
    invalidation_hit: true,
    // 归因五选一：这里选「没按计划执行」，已复盘列表必须显示对应标签
    verdict: "discipline_break",
    note: "到期那天没有回来看",
  },
};

/** 样本不足（`reviewed_n < 5`）：只许出现「样本不足（<5 条）」，不许出现任何占比。 */
const STATS_INSUFFICIENT: FixtureStats = {
  n: 3,
  reviewed_n: 3,
  by_verdict: { thesis_wrong: 2, luck: 1 },
  avg_excess_pct: 0.5,
  discipline_break_share: 0.333,
};

/**
 * 净口径 + 基准说明的到期条目（S9.7.2/§S9.7.3）：净字段齐备，基准是行业等权。
 * 毛 −3.50% / 净 −3.62%（差 12bp）；超额毛 −2.50% / 净 −2.62%。
 */
const NET_DUE_ENTRY: FixtureEntry = {
  ...DUE_ENTRY,
  id: 103,
  bet_type: "direction",
  snapshot: {
    bench_spec: { bet_type: "direction", fallback: false },
    bench_basis_note: "基准=所属申万一级成分股等权平均收益（非指数行情）",
  },
  evaluation: {
    outcome_pct: -3.5,
    bench_pct: -1,
    excess_pct: -2.5,
    invalidation_hit: true,
    net_outcome_pct: -3.62,
    net_excess_pct: -2.62,
    bench_basis_note: "基准=所属申万一级成分股等权平均收益（非指数行情）",
    bench_fallback: false,
  },
};

/** 无行业归属 → 基准退化为沪深300（`bench_fallback = true`）的市场级条目。 */
const FALLBACK_DUE_ENTRY: FixtureEntry = {
  ...DUE_ENTRY,
  id: 104,
  symbol: null,
  bet_type: "market",
  thesis: "市场级：连续缩量，等下一次放量再说",
  snapshot: {
    bench_spec: { bet_type: "market", fallback: true },
    bench_basis_note: "基准=沪深300 指数收益",
  },
  evaluation: {
    outcome_pct: 2,
    bench_pct: 1.8,
    excess_pct: 0.2,
    invalidation_hit: false,
    net_outcome_pct: 1.88,
    net_excess_pct: 0.08,
    bench_basis_note: "基准=沪深300 指数收益",
    bench_fallback: true,
  },
};

/** 净口径已复盘条目（非 pass）：列表收益列默认净超额 2.18%，不是毛 2.30%。 */
const NET_REVIEWED_ENTRY: FixtureEntry = {
  ...REVIEWED_ENTRY,
  id: 105,
  symbol: "000001",
  kind: "watch",
  thesis: "净口径样例：到期结果按净成本展示",
  bet_type: "relative",
  evaluation: {
    outcome_pct: 3.5,
    bench_pct: 1.2,
    excess_pct: 2.3,
    invalidation_hit: false,
    net_outcome_pct: 3.38,
    net_excess_pct: 2.18,
    bench_basis_note: "基准=所属申万一级成分股等权平均收益（非指数行情）",
    bench_fallback: false,
  },
  review: {
    id: 9005,
    entry_id: 105,
    reviewed_at: "2026-10-11T01:00:00Z",
    outcome_pct: 3.5,
    bench_pct: 1.2,
    excess_pct: 2.3,
    invalidation_hit: false,
    net_outcome_pct: 3.38,
    net_excess_pct: 2.18,
    verdict: "right_for_wrong_reason",
    note: null,
  },
};

/** 只有毛口径的已复盘条目：净字段缺省（Phase A 未上线）时必须标注"毛口径"。 */
const GROSS_ONLY_REVIEWED_ENTRY: FixtureEntry = {
  ...REVIEWED_ENTRY,
  id: 106,
  symbol: "600036",
  kind: "watch",
  thesis: "净字段缺省时回退毛口径",
  evaluation: { outcome_pct: -1, bench_pct: 0.2, excess_pct: -1.2, invalidation_hit: true },
  review: {
    id: 9006,
    entry_id: 106,
    reviewed_at: "2026-10-11T01:00:00Z",
    outcome_pct: -1,
    bench_pct: 0.2,
    excess_pct: -1.2,
    invalidation_hit: true,
    verdict: "timing_wrong",
    note: null,
  },
};

/**
 * 不可复盘条目（spec §S9.7.8）：`reviewable = false` —— 失效条件不可观测，
 * 到期只显示结果、**没有**「复盘」按钮，改成「不可复盘（失效条件不可观测）」。
 */
const NOT_REVIEWABLE_ENTRY: FixtureEntry = {
  ...DUE_ENTRY,
  id: 107,
  symbol: "002714",
  thesis: "失效条件写的是一句话，没有价格也没有日期",
  reviewable: false,
  evaluation: {
    outcome_pct: -2.1,
    bench_pct: -0.4,
    excess_pct: -1.7,
    invalidation_hit: null,
    invalidation_observable: false,
  },
};

/**
 * 路径：**先触发失效、到期反而盈利**（spec §S9.7.4 的 `touched_then_reversed`）。
 * 弹窗要给"更像纪律问题"的提示，并且**不预选**归因。
 */
const REVERSED_DUE_ENTRY: FixtureEntry = {
  ...NET_DUE_ENTRY,
  id: 108,
  thesis: "先跌破失效线，到期反而收涨",
  evaluation: {
    ...(NET_DUE_ENTRY.evaluation as FixtureEvaluation),
    mae_pct: -12.4,
    first_touch_invalidation: "2026-10-14",
    touched_then_reversed: true,
    days_to_invalidation: 2,
    invalidation_observable: true,
  },
};

/** 样本够（`reviewed_n >= 5`）：五类条数 + 占比 + 平均超额都可见。 */
const STATS_ENOUGH: FixtureStats = {
  n: 10,
  reviewed_n: 8,
  by_verdict: { thesis_wrong: 2, timing_wrong: 1, discipline_break: 3, right_for_wrong_reason: 1, luck: 1 },
  avg_excess_pct: 0.42,
  discipline_break_share: 0.375,
};

/**
 * 比例带 Wilson 区间（spec §S9.7.7）：`share_ci.win = {4/6, [30%, 90%]}` 必须原样写成
 * `4/6（95% 区间 30%–90%）`；`by_kind` 与 `multiple_comparison_note` 也一起给。
 */
const STATS_WITH_CI: FixtureStats = {
  n: 6,
  reviewed_n: 6,
  by_verdict: { thesis_wrong: 2, timing_wrong: 1, discipline_break: 2, right_for_wrong_reason: 1, luck: 0 },
  avg_excess_pct: -2.4,
  discipline_break_share: 0.333,
  by_kind: { watch: 3, buy: 2, sell: 0, pass: 1, hold: 0 },
  avg_net_excess_pct: -2.52,
  share_ci: {
    discipline_break: { n: 6, successes: 3, ci_low: 0.19, ci_high: 0.81 },
    win: { n: 6, successes: 4, ci_low: 0.3, ci_high: 0.9 },
  },
  net_cost_bps: 12,
  multiple_comparison_note: "未做多重比较校正：按 45 个桶检索时总会有桶看起来显著",
};

/** `reviewed_n < 5` 但后端仍给了 `share_ci`：页面只许写"样本不足"，不给区间也不给百分比。 */
const STATS_SMALL_WITH_CI: FixtureStats = {
  n: 4,
  reviewed_n: 4,
  by_verdict: { thesis_wrong: 2, luck: 2 },
  avg_excess_pct: 0.5,
  discipline_break_share: 0.25,
  share_ci: {
    discipline_break: { n: 4, successes: 1, ci_low: 0.05, ci_high: 0.72 },
    win: { n: 4, successes: 2, ci_low: 0.15, ci_high: 0.85 },
  },
};

/**
 * Phase B 统计：`by_size` + 加权超额 + 校准曲线（spec §S9.7.1/§S9.7.5）+ `export_hint`。
 *
 * 校准两档刻意一档不够、一档够：`信心 1`（n=4）只许写"样本不足"，
 * `信心 3`（n=12）才给命中率 58.0% —— 同屏对照，避免"每档都写样本不足"这种假绿。
 */
const STATS_WITH_SIZE_AND_CALIBRATION: FixtureStats = {
  n: 26,
  reviewed_n: 22,
  by_verdict: {
    thesis_wrong: 6,
    timing_wrong: 4,
    discipline_break: 6,
    right_for_wrong_reason: 3,
    luck: 3,
  },
  avg_excess_pct: -1.7,
  discipline_break_share: 0.27,
  by_kind: { watch: 10, buy: 4, sell: 2, pass: 8, hold: 2 },
  avg_net_excess_pct: -1.82,
  net_cost_bps: 12,
  by_size: { none: 2, light: 1, normal: 3, heavy: 0 },
  avg_weighted_excess_pct: -1.8,
  calibration: {
    n: 22,
    bins: [
      { confidence: 1, p: 0.2, n: 4, hit_rate: 0.25, sufficient: false },
      { confidence: 3, p: 0.5, n: 12, hit_rate: 0.58, sufficient: true },
    ],
    brier: 0.231,
  },
  export_hint: "/api/v1/journal/export?format=csv&scope=mine",
};

/** 校准总样本不足：后端给 `calibration: null` + 原因，页面**只显示**原因，不画曲线。 */
const STATS_CALIBRATION_INSUFFICIENT: FixtureStats = {
  ...STATS_WITH_CI,
  calibration: null,
  calibration_note: "样本不足（<20 条已复盘且填了信心）",
};

interface JournalStore {
  entries: FixtureEntry[];
  stats: FixtureStats;
  /** 断言用：最后一次「记一笔」的请求载荷（表单到底发了什么）。 */
  lastCreate: Record<string, unknown> | null;
}

/**
 * journal 全读写的 mock（`/auth/session` + `/auth/csrf` + 列表/统计/创建/复盘）。
 *
 * 只注册**一条** `**\/api/v1/journal**` 路由、在 handler 里按 pathname + method 分派：
 * Playwright 的路由匹配是"后注册的先命中"，多条正则容易因为顺序把 `/journal/stats`
 * 吞进 `/journal` 那条（`stats` 就被当成列表响应，页面白屏），所以这里不给自己留这个坑。
 */
async function mockJournal(page: Page, store: JournalStore) {
  await page.route("**/auth/session", (route) => route.fulfill({ status: 200, json: USER }));
  await page.route("**/auth/csrf", (route) =>
    route.fulfill({ status: 200, json: { csrf_token: "csrf-token-test" } })
  );

  await page.route("**/api/v1/journal**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/\/+$/, "");
    const method = request.method();

    if (path.endsWith("/stats")) {
      return route.fulfill({ status: 200, json: store.stats });
    }

    if (method === "POST" && path.endsWith("/review")) {
      const id = Number(path.split("/").slice(-2)[0]);
      const body = JSON.parse(request.postData() ?? "{}");
      const entry = store.entries.find((item) => item.id === id);
      const review: FixtureReview = {
        id: 9000 + id,
        entry_id: id,
        reviewed_at: "2026-10-18T09:00:00Z",
        outcome_pct: entry?.evaluation?.outcome_pct ?? null,
        bench_pct: entry?.evaluation?.bench_pct ?? null,
        excess_pct: entry?.evaluation?.excess_pct ?? null,
        invalidation_hit: entry?.evaluation?.invalidation_hit ?? null,
        // 复盘响应与 `evaluation` 同形：净口径字段一并带过去（S9.7.2）
        net_outcome_pct: entry?.evaluation?.net_outcome_pct ?? null,
        net_excess_pct: entry?.evaluation?.net_excess_pct ?? null,
        verdict: body.verdict,
        note: body.note ?? null,
      };
      if (entry) {
        // 真实后端是 append-only + `unique(entry_id)`：这里同样"一次复盘就移库"。
        entry.status = "reviewed";
        entry.review = review;
      }
      return route.fulfill({ status: 201, json: review });
    }

    if (method === "POST" && path === "/api/v1/journal") {
      const body = JSON.parse(request.postData() ?? "{}");
      store.lastCreate = body;
      const created: FixtureEntry = {
        id: 500,
        symbol: body.symbol ?? null,
        kind: body.kind ?? "watch",
        thesis: body.thesis,
        invalidation: body.invalidation,
        horizon_days: body.horizon_days,
        // 快照是**后端**在创建时落的（spec §S9.3），前端只显示摘要。
        snapshot_summary: "价格 1588.00 元 · 当日 +0.40% · 情绪分位 55",
        created_at: "2026-10-18T09:00:00Z",
        due_on: "2026-11-17",
        status: "open",
        // 创建响应里的 `bet_type` 跟着请求走；快照由后端固化（Phase A 未上线时缺省）
        bet_type: body.bet_type ?? null,
        // S9.7.1/§S9.7.5：仓位与信心跟着请求走（后端 Phase B 落列后同形）
        position_size: body.position_size ?? null,
        confidence: body.confidence ?? null,
        // 新条目默认可复盘（spec §S9.7.8 的闸只对"失效条件不可观测"置 false）
        reviewable: true,
        evaluation: null,
        review: null,
      };
      store.entries.unshift(created);
      return route.fulfill({ status: 201, json: created });
    }

    if (method === "GET" && path === "/api/v1/journal") {
      const status = url.searchParams.get("status");
      const items = store.entries.filter((item) => !status || item.status === status);
      return route.fulfill({ status: 200, json: { items } });
    }

    return route.fulfill({
      status: 404,
      json: { code: "NOT_FOUND", message: `e2e mock 未覆盖：${method} ${path}`, trace_id: "e2e" },
    });
  });
}

/** 打开 journal tab 的最小语境（空记录 + 样本不足统计）。 */
async function openJournal(page: Page, store?: Partial<JournalStore>): Promise<JournalStore> {
  // 每条 fixture 都**浅拷贝**一份：mock 的复盘会就地把条目改成 `reviewed`，
  // 直接改模块级常量会让**后面的用例**看到被上一轮改过的 `DUE_ENTRY`
  // （表现为"这条本该 due 的却出现在已复盘里"这种跨用例的隐性顺序依赖）。
  const entries = (store?.entries ?? [DUE_ENTRY, REVIEWED_ENTRY]).map((entry) => ({ ...entry }));
  const state: JournalStore = {
    stats: STATS_INSUFFICIENT,
    lastCreate: null,
    ...store,
    entries,
  };
  await mockJournal(page, state);
  await page.goto("/me?tab=journal");
  await expect(page.getByTestId("journal-panel")).toBeVisible();
  return state;
}

/**
 * 表单里选观察期限。
 *
 * 不能对 `getByRole("radio")` 直接 `check()`：AntD 的 `Radio.Button` 把真正的 `<input>`
 * 渲染成 `opacity:0` 的覆盖层，Playwright 判它不可见（`element is not visible`），
 * 于是"选期限"这一步会一直重试到超时。用户实际点的是可见的 label 外壳，故这里也点外壳。
 */
async function pickHorizon(page: Page, label: string) {
  await page.locator(".ant-radio-button-wrapper", { hasText: label }).first().click();
}

/** 表单里选失效条件（AntD Select：点开下拉再点选项）。 */
async function pickInvalidationKind(page: Page, label: string) {
  await page.getByTestId("journal-inv-kind").click();
  await page.locator(".ant-select-item-option", { hasText: label }).first().click();
}

/**
 * 表单里选「赌的是什么」（S9.7.3）。
 *
 * 与期限同理：AntD 的 `Radio.Button` 把真 `<input>` 渲染成透明覆盖层，`check()` 会
 * 判不可见；用户点的是可见的 label 外壳。范围限定在 `journal-bet-type` 里，
 * 避免和「方向 → 所属行业…」那句 hint 撞文本。
 */
async function pickBetType(page: Page, label: string) {
  await page
    .getByTestId("journal-bet-type")
    .locator(".ant-radio-button-wrapper", { hasText: label })
    .first()
    .click();
}

/**
 * 表单里选仓位（S9.7.1 的四选一，AntD `Radio.Button`）。
 * 与期限/赌注同理：真 `<input>` 是透明覆盖层，点可见的 label 外壳。
 */
async function pickPositionSize(page: Page, label: string) {
  await page
    .getByTestId("journal-position-size")
    .locator(".ant-radio-button-wrapper", { hasText: label })
    .first()
    .click();
}

/** 表单里选信心档（S9.7.5，AntD Select：点开下拉再点选项）。 */
async function pickConfidence(page: Page, label: string) {
  await page.getByTestId("journal-confidence").click();
  await page.locator(".ant-select-item-option", { hasText: label }).first().click();
}

test.describe("决策日志（/me?tab=journal）", () => {
  test("表单三必填：缺项时提交按钮禁用，且提示逐项收敛", async ({ page }) => {
    await openJournal(page);

    const submit = page.getByTestId("journal-submit");
    const missing = page.getByTestId("journal-missing");

    // ① 空表单：三项都缺，按钮禁用（不是点了才报错）
    await expect(submit).toBeDisabled();
    await expect(missing).toContainText("判断依据");
    await expect(missing).toContainText("失效条件");
    await expect(missing).toContainText("观察期限");

    // ② 只写 thesis：判断依据从缺项里消失，另外两项仍在，按钮仍禁用
    await page.getByTestId("journal-thesis").fill("渠道库存回到常态，价格低于成本线就说明看错了");
    await expect(missing).not.toContainText("判断依据");
    await expect(missing).toContainText("失效条件");
    await expect(missing).toContainText("观察期限");
    await expect(submit).toBeDisabled();

    // ③ 选了失效条件的**类型**但没填数值：仍算缺（"选了类型"≠"填了值"）
    await pickInvalidationKind(page, "跌破某价格");
    await expect(missing).toContainText("失效条件");
    await expect(submit).toBeDisabled();

    // ③b 换成"只看日期"：数值输入换成日期输入，日期没填同样算缺（三选一都走同一条判据）
    await pickInvalidationKind(page, "只看日期（到期即算触发）");
    await expect(page.getByTestId("journal-inv-date")).toBeVisible();
    await expect(page.getByTestId("journal-inv-price")).toHaveCount(0);
    await expect(missing).toContainText("失效条件");

    // 换回价格阈值，继续后面的步骤
    await pickInvalidationKind(page, "跌破某价格");

    // ④ 填上数值：失效条件满足；只剩观察期限
    await page.getByTestId("journal-inv-price").fill("1500");
    await expect(missing).not.toContainText("失效条件");
    await expect(missing).toContainText("观察期限");
    await expect(submit).toBeDisabled();

    // ⑤ 选期限：三项齐全 → 按钮可用，缺项提示消失
    await pickHorizon(page, "30 天");
    await expect(submit).toBeEnabled();
    await expect(page.getByTestId("journal-missing")).toHaveCount(0);
  });

  test("填满后提交：条目立刻出现在「未到期」，且请求带齐三必填", async ({ page }) => {
    const store = await openJournal(page, { entries: [] });

    // 空记录：三个分组各自有空态，不打架
    await expect(page.getByTestId("journal-due-item")).toHaveCount(0);
    await expect(page.getByTestId("journal-reviewed-item")).toHaveCount(0);
    await expect(page.getByText(/还没有到期的记录/)).toBeVisible();

    await page.getByTestId("journal-thesis").fill("渠道库存回到常态");
    await pickInvalidationKind(page, "跌破某价格");
    await page.getByTestId("journal-inv-price").fill("1500");
    await pickHorizon(page, "30 天");
    await page.getByTestId("journal-symbol").fill("600519");
    await page.getByTestId("journal-submit").click();

    // 创建后立刻可见：走的是"后端定状态 → 前端重取列表"的同一条路径（不是前端手塞）
    await expect(page.getByTestId("journal-open-item")).toHaveCount(1);
    const row = page.getByTestId("journal-open-item").first();
    await expect(row).toContainText("渠道库存回到常态");
    await expect(row).toContainText("600519");
    // 到期日走 `shared/ui/date.ts`，不直出 ISO 机器串
    await expect(row).toContainText("到期 11月17日");
    await expect(row).toContainText("快照：");
    await expect(row).not.toContainText(/T\d{2}:\d{2}/);

    // 「记一笔」发出的载荷逐字符合契约：三个必填都在，失效条件是 {kind, value}
    expect(store.lastCreate).toMatchObject({
      symbol: "600519",
      kind: "watch",
      // S9.7.3：默认「方向」，创建时随载荷提交
      bet_type: "direction",
      thesis: "渠道库存回到常态",
      invalidation: { kind: "price_below", value: 1500 },
      horizon_days: 30,
    });

    // 表单单字段被清空（同一笔不该被重复提交两次）
    await expect(page.getByTestId("journal-thesis")).toHaveValue("");
  });

  test("复盘弹窗：自动结果只读，未选归因时「确认复盘」禁用", async ({ page }) => {
    await openJournal(page);

    // 顶部角标 = due 条数
    await expect(page.getByTestId("journal-due-count")).toHaveText("待复盘 1 条");

    await page.getByTestId("journal-review-open").first().click();
    const modal = page.getByTestId("journal-review-modal");
    await expect(modal).toBeVisible();

    // 只读区逐字给出后端算的四个数（保留符号；触发状态是文字，不只靠颜色）
    const result = page.getByTestId("journal-review-result");
    await expect(result).toContainText("结果");
    await expect(result).toContainText("-3.50%");
    await expect(result).toContainText("-1.00%");
    await expect(result).toContainText("-2.50%");
    await expect(result).toContainText("已触发");
    // 到期日走格式化入口
    await expect(modal).toContainText("到期日 10月16日");

    const confirm = page.getByRole("button", { name: "确认复盘" });
    // 未选归因：确认禁用 + 明说"还没选归因"（不是点了才报错）
    await expect(confirm).toBeDisabled();
    await expect(page.getByTestId("journal-review-need-verdict")).toBeVisible();

    // 归因是五选一：只出现这五个选项（多一个都说明枚举被扩了）
    const radios = modal.getByRole("radio");
    await expect(radios).toHaveCount(5);
  });

  test("选归因后提交：条目从待复盘移到已复盘（归因标签与备注可见）", async ({ page }) => {
    await openJournal(page, { entries: [DUE_ENTRY] });

    await expect(page.getByTestId("journal-due-item")).toHaveCount(1);
    await expect(page.getByTestId("journal-reviewed-item")).toHaveCount(0);

    await page.getByTestId("journal-review-open").first().click();
    const modal = page.getByTestId("journal-review-modal");
    await expect(modal).toBeVisible();

    // 选「没按计划执行」→ 确认可用
    await modal.getByText("没按计划执行").click();
    const confirm = page.getByRole("button", { name: "确认复盘" });
    await expect(confirm).toBeEnabled();

    // 备注是可选自由文本，原样保存
    await page.getByTestId("journal-review-note").fill("到期那天没有回来看");
    await confirm.click();

    // 移库：due 空、reviewed 有该条
    await expect(page.getByTestId("journal-review-modal")).toHaveCount(0);
    await expect(page.getByTestId("journal-due-item")).toHaveCount(0);
    await expect(page.getByTestId("journal-reviewed-item")).toHaveCount(1);

    const reviewed = page.getByTestId("journal-reviewed-item").first();
    await expect(reviewed).toContainText("600519");
    await expect(reviewed.getByTestId("journal-reviewed-verdict")).toHaveText("没按计划执行");
    await expect(reviewed.getByTestId("journal-reviewed-excess")).toContainText("-2.50%");
    await expect(reviewed).toContainText("你的备注：到期那天没有回来看");
    // 顶部角标归零（N 是 due 条数）
    await expect(page.getByTestId("journal-due-count")).toHaveText("待复盘 0 条");
  });

  test("统计：reviewed_n < 5 只显示「样本不足（<5 条）」，不给占比", async ({ page }) => {
    await openJournal(page, { stats: STATS_INSUFFICIENT });

    const card = page.getByTestId("journal-stats");
    await expect(card).toBeVisible();
    await expect(page.getByTestId("journal-stats-insufficient")).toHaveText("样本不足（<5 条）");
    // 三条样本里 2/3 是 66.7%——这类数字看起来像结论，必须不出现
    await expect(page.getByTestId("journal-stats-breakdown")).toHaveCount(0);
    await expect(card).not.toContainText("66.7%");
    await expect(card).not.toContainText("平均超额");
    // 总条数/已复盘条数是事实，仍然要显示
    await expect(card).toContainText("共 3 条 · 已复盘 3 条");
  });

  test("统计：reviewed_n >= 5 显示五类占比与平均超额", async ({ page }) => {
    await openJournal(page, { stats: STATS_ENOUGH });

    await expect(page.getByTestId("journal-stats-insufficient")).toHaveCount(0);
    const breakdown = page.getByTestId("journal-stats-breakdown");
    await expect(breakdown).toBeVisible();

    // 五类归因各一行（标签逐字）
    for (const label of ["理由错了", "时机错了", "没按计划执行", "结果对、理由不成立", "运气成分"]) {
      await expect(breakdown).toContainText(label);
    }
    // 占比：3/8 = 37.5%、2/8 = 25.0%（保留一位小数，不是丢掉的小数）
    await expect(breakdown).toContainText("37.5%");
    await expect(breakdown).toContainText("25.0%");
    // 平均超额与"没按计划执行"占比（后端给 0–1 的小数，页面换成 %）
    const card = page.getByTestId("journal-stats");
    await expect(card).toContainText("平均超额");
    await expect(card).toContainText("+0.42%");
    await expect(card).toContainText("没按计划执行占比");
    await expect(card).toContainText("37.5%");
  });

  test("pass 一键：点「我决定不动」把 kind 设为 pass，收益列显示「—（不计收益）」", async ({
    page,
  }) => {
    const store = await openJournal(page, { entries: [] });

    // 一键按钮**不跳过** 3 个必填：空表单时按钮可点，但提交仍禁用
    await page.getByTestId("journal-pass").click();
    await expect(page.getByTestId("journal-pass-hint")).toContainText("我决定不动");
    await expect(page.getByTestId("journal-submit")).toBeDisabled();
    await expect(page.getByTestId("journal-missing")).toContainText("判断依据");

    await page.getByTestId("journal-thesis").fill("没有把握，先记下不动的理由");
    await pickInvalidationKind(page, "跌破某价格");
    await page.getByTestId("journal-inv-price").fill("1500");
    await pickHorizon(page, "30 天");
    await page.getByTestId("journal-submit").click();

    await expect(page.getByTestId("journal-open-item")).toHaveCount(1);
    const row = page.getByTestId("journal-open-item").first();
    // pass 计入判断、不计入收益：收益列写"不计收益"，**不能**是 0.00%
    await expect(row.getByTestId("journal-entry-outcome")).toHaveText("收益 —（不计收益）");
    await expect(row).not.toContainText("0.00%");

    expect(store.lastCreate).toMatchObject({ kind: "pass", bet_type: "direction" });
  });

  test("净口径：列表与复盘弹窗默认净，并标「毛/净（成本 12bp）」", async ({ page }) => {
    await openJournal(page, {
      entries: [NET_DUE_ENTRY, NET_REVIEWED_ENTRY, GROSS_ONLY_REVIEWED_ENTRY],
    });

    // 列表默认净超额（+2.18%），毛 +2.30% 不单独上屏；口径小字跟着数值走
    const reviewed = page.getByTestId("journal-reviewed-item").first();
    await expect(reviewed.getByTestId("journal-reviewed-excess")).toContainText("+2.18%");
    await expect(reviewed.getByTestId("journal-entry-outcome")).toContainText(
      "毛/净（成本 12bp）"
    );
    await expect(reviewed).not.toContainText("+2.30%");

    // 净字段缺省（Phase A 未上线）：回退毛口径并明确标注，不假装是净
    const grossOnly = page.getByTestId("journal-reviewed-item").nth(1);
    await expect(grossOnly.getByTestId("journal-reviewed-excess")).toContainText("-1.20%");
    await expect(grossOnly.getByTestId("journal-entry-outcome")).toContainText(
      "净字段缺失，显示毛口径"
    );

    // 复盘弹窗：结果/超额主值是净，毛只作括注
    await page.getByTestId("journal-review-open").first().click();
    const modal = page.getByTestId("journal-review-modal");
    await expect(modal.getByTestId("journal-review-outcome")).toHaveText("-3.62%");
    await expect(modal.getByTestId("journal-review-excess")).toHaveText("-2.62%");
    await expect(modal.getByTestId("journal-review-result")).toContainText("毛 -3.50%");
    await expect(modal.getByTestId("journal-review-result")).toContainText("毛 -2.50%");
    await expect(modal.getByTestId("journal-review-basis")).toContainText("毛/净（成本 12bp）");
    await expect(modal.getByTestId("journal-review-basis")).toContainText("净口径");
  });

  test("统计：比例带 95% 区间，底部原样展示多重比较标注", async ({ page }) => {
    await openJournal(page, { stats: STATS_WITH_CI });
    const card = page.getByTestId("journal-stats");

    // 4/6 + Wilson 区间，逐字
    await expect(card.getByTestId("journal-stats-win")).toContainText("4/6（95% 区间 30%–90%）");
    // 没按计划执行占比同样带区间（3/6 → 19%–81%）
    await expect(card.getByTestId("journal-stats-discipline")).toContainText(
      "3/6（95% 区间 19%–81%）"
    );
    // 平均超额默认净口径（-2.52%，不是毛 -2.40%）
    await expect(card).toContainText("平均超额（净）");
    await expect(card).toContainText("-2.52%");
    await expect(card).not.toContainText("-2.40%");

    // by_kind 分栏：pass 与其它标签一样列条数，并说明不计收益
    const byKind = card.getByTestId("journal-stats-by-kind");
    await expect(byKind).toContainText("放弃");
    await expect(byKind).toContainText("我决定不动");
    await expect(byKind).toContainText("3");

    // 底部固定标注：后端给的原话，前端不改写
    await expect(card.getByTestId("journal-stats-multiple-note")).toHaveText(
      "未做多重比较校正：按 45 个桶检索时总会有桶看起来显著"
    );
  });

  test("统计：n<5 即使后端给了区间也不显示百分比", async ({ page }) => {
    await openJournal(page, { stats: STATS_SMALL_WITH_CI });
    const card = page.getByTestId("journal-stats");

    await expect(card.getByTestId("journal-stats-insufficient")).toHaveText("样本不足（<5 条）");
    await expect(card).not.toContainText("95% 区间");
    await expect(card).not.toContainText("%");
    await expect(card.getByTestId("journal-stats-breakdown")).toHaveCount(0);
  });

  test("基准：bet_type 三选一（默认「方向」）与 bench_fallback 提示", async ({ page }) => {
    const store = await openJournal(page, { entries: [FALLBACK_DUE_ENTRY] });

    // 三选一，默认「方向」
    const betGroup = page.getByTestId("journal-bet-type");
    await expect(betGroup.getByRole("radio")).toHaveCount(3);
    await expect(betGroup.locator(".ant-radio-button-wrapper-checked")).toHaveText("方向");
    await expect(betGroup).toContainText("跑赢同行");
    await expect(betGroup).toContainText("市场级");

    // 选「跑赢同行」后，创建载荷带 bet_type
    await page.getByTestId("journal-thesis").fill("同行扩产更慢，想验证能不能跑赢同行");
    await pickInvalidationKind(page, "跌破某价格");
    await page.getByTestId("journal-inv-price").fill("12.5");
    await pickHorizon(page, "7 天");
    await pickBetType(page, "跑赢同行");
    await page.getByTestId("journal-submit").click();
    // 先等创建真正落库（请求被 mock 接住并返回），再断言载荷——点击返回 ≠ 请求已发出
    await expect(page.getByTestId("journal-open-item")).toHaveCount(1);
    expect(store.lastCreate).toMatchObject({ bet_type: "relative" });

    // 复盘弹窗：展示 bench_basis_note；bench_fallback=true 时补一句退化提示
    await page.getByTestId("journal-review-open").first().click();
    const modal = page.getByTestId("journal-review-modal");
    await expect(modal.getByTestId("journal-bench-basis-note")).toHaveText(
      "基准=沪深300 指数收益"
    );
    await expect(modal.getByTestId("journal-bench-fallback")).toContainText(
      "无行业归属，基准退化为沪深300"
    );
  });

  test("仓位与信心：默认「只观察」，提交后列表行显示仓位标签、载荷带四选一与 1–5", async ({
    page,
  }) => {
    const store = await openJournal(page, { entries: [] });

    const sizeGroup = page.getByTestId("journal-position-size");
    // 四选一：多一个都说明枚举被扩了
    await expect(sizeGroup.getByRole("radio")).toHaveCount(4);
    // 默认「只观察」（spec §S9.7.1 的默认值，不是空白）
    await expect(sizeGroup.locator(".ant-radio-button-wrapper-checked")).toHaveText("只观察");
    // 信心可留空：默认没有值，占位文案说清"留空 = 不参与校准"
    await expect(page.getByTestId("journal-confidence")).toContainText("留空 = 不参与校准");

    // 第一笔：仓位/信心全默认 → 载荷 position_size=none，且**不带** confidence 键
    await page.getByTestId("journal-thesis").fill("只记判断，不下单");
    await pickInvalidationKind(page, "跌破某价格");
    await page.getByTestId("journal-inv-price").fill("1500");
    await pickHorizon(page, "30 天");
    await page.getByTestId("journal-submit").click();
    await expect(page.getByTestId("journal-open-item")).toHaveCount(1);
    expect(store.lastCreate).toMatchObject({ position_size: "none" });
    expect(store.lastCreate).not.toHaveProperty("confidence");
    await expect(
      page.getByTestId("journal-open-item").first().getByTestId("journal-entry-size")
    ).toHaveText("仓位：只观察");

    // 第二笔：轻仓 + 信心 4 → 载荷逐字，列表行显示「仓位：轻仓」
    await page.getByTestId("journal-thesis").fill("轻仓试一下，怕看错");
    await pickInvalidationKind(page, "跌破某价格");
    await page.getByTestId("journal-inv-price").fill("12.5");
    await pickHorizon(page, "7 天");
    await pickPositionSize(page, "轻仓");
    await pickConfidence(page, "4 分");
    await page.getByTestId("journal-submit").click();
    await expect(page.getByTestId("journal-open-item")).toHaveCount(2);
    expect(store.lastCreate).toMatchObject({ position_size: "light", confidence: 4 });
    await expect(
      page.getByTestId("journal-open-item").first().getByTestId("journal-entry-size")
    ).toHaveText("仓位：轻仓");
  });

  test("不可复盘：reviewable=false 的条目没有「复盘」按钮，改为文字说明", async ({ page }) => {
    await openJournal(page, { entries: [NOT_REVIEWABLE_ENTRY, DUE_ENTRY] });

    await expect(page.getByTestId("journal-due-item")).toHaveCount(2);
    // 两条到期里只有可复盘的那条有按钮
    await expect(page.getByTestId("journal-review-open")).toHaveCount(1);

    const blocked = page.getByTestId("journal-due-item").first();
    await expect(blocked).toContainText("失效条件写的是一句话");
    await expect(blocked.getByTestId("journal-not-reviewable")).toHaveText(
      "不可复盘（失效条件不可观测）"
    );
    // 不是"禁用的按钮"，而是**没有**按钮（禁用会暗示以后还能点）
    await expect(blocked.locator("button")).toHaveCount(0);

    // 另一条照旧能复盘（AntD 会给两个汉字的按钮插空格，故用正则而不是逐字 "复盘"）
    await expect(
      page.getByTestId("journal-due-item").nth(1).getByTestId("journal-review-open")
    ).toHaveText(/复\s*盘/);
  });

  test("路径提示：先触失效后反弹时在归因上方提示「更像纪律问题」，且不预选归因", async ({
    page,
  }) => {
    await openJournal(page, { entries: [REVERSED_DUE_ENTRY, DUE_ENTRY] });

    await page.getByTestId("journal-review-open").first().click();
    const modal = page.getByTestId("journal-review-modal");
    await expect(modal).toBeVisible();

    // 路径三格：最大不利偏移 / 首次触达日 / 距入场交易日数（逐字）
    const pathBox = modal.getByTestId("journal-review-path");
    await expect(pathBox.getByTestId("journal-review-mae")).toHaveText("-12.40%");
    await expect(pathBox.getByTestId("journal-review-first-touch")).toHaveText("10月14日");
    await expect(pathBox.getByTestId("journal-review-days-to")).toHaveText("第 2 个交易日");

    // 提示文案逐字（"纪律"两个字是提示的重点）
    const hint = modal.getByTestId("journal-path-hint");
    await expect(hint).toContainText("你先触发了失效条件，之后才反弹——这更像纪律问题");
    // 位置在「归因」选择**上方**：按 DOM 文本顺序断言（`innerText` 跟着文档顺序，
    // 比拿 boundingBox 比 y 稳——弹窗有入场动画、长内容还会滚动）。
    const modalText = await modal.innerText();
    const hintAt = modalText.indexOf("你先触发了失效条件");
    const verdictAt = modalText.indexOf("归因（必选");
    expect(hintAt).toBeGreaterThan(-1);
    expect(verdictAt).toBeGreaterThan(-1);
    expect(hintAt).toBeLessThan(verdictAt);

    // 提示不是强制选择：没有预选归因，确认仍禁用
    await expect(modal.locator("input[type=radio]:checked")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "确认复盘" })).toBeDisabled();

    // 没有"先触失效"的条目不给这条提示
    // （AntD 会给两个汉字的按钮插空格：可访问名是「取 消」，故用正则）
    await page.getByRole("button", { name: /取\s*消/ }).click();
    await expect(modal).toHaveCount(0);
    await page.getByTestId("journal-review-open").nth(1).click();
    await expect(page.getByTestId("journal-review-modal")).toBeVisible();
    await expect(page.getByTestId("journal-path-hint")).toHaveCount(0);
  });

  test("统计：by_size 分栏 + 加权超额，校准每档 n>=10 才给命中率", async ({ page }) => {
    await openJournal(page, { stats: STATS_WITH_SIZE_AND_CALIBRATION });
    const card = page.getByTestId("journal-stats");

    // 仓位分栏：四个档各自条数（纯条数事实，不是比例）
    const bySize = card.getByTestId("journal-stats-by-size");
    await expect(bySize).toBeVisible();
    for (const label of ["只观察", "轻仓", "标准", "重仓"]) {
      await expect(bySize).toContainText(label);
    }
    await expect(bySize).toContainText("只记判断");

    // 加权超额用后端给的 -1.80%（不是净平均 -1.82%）
    const weighted = card.getByTestId("journal-stats-weighted-excess");
    await expect(weighted).toContainText("加权超额");
    await expect(weighted).toContainText("-1.80%");

    // 校准卡：n<10 的档只写"样本不足"，n>=10 的档才给命中率
    const calibration = page.getByTestId("journal-calibration");
    await expect(calibration).toBeVisible();
    await expect(calibration).toContainText("你的信心与实际结果的对照");
    const bins = calibration.getByTestId("journal-calibration-bin");
    await expect(bins).toHaveCount(2);
    await expect(bins.nth(0)).toContainText("信心 1");
    await expect(bins.nth(0)).toContainText("样本不足（n=4）");
    await expect(bins.nth(1)).toContainText("信心 3");
    await expect(bins.nth(1)).toContainText("58.0%");
    await expect(bins.nth(1)).toContainText("n=12");
    await expect(calibration.getByTestId("journal-calibration-foot")).toContainText(
      "共 22 条已复盘且填了信心 · Brier 0.231"
    );

    // 导出链接优先用后端给的 `export_hint`
    await expect(page.getByTestId("journal-export")).toHaveAttribute(
      "href",
      "/api/v1/journal/export?format=csv&scope=mine"
    );
  });

  test("校准：总样本不足时只显示 calibration_note，不画曲线", async ({ page }) => {
    await openJournal(page, { stats: STATS_CALIBRATION_INSUFFICIENT });

    const calibration = page.getByTestId("journal-calibration");
    await expect(calibration).toBeVisible();
    await expect(calibration.getByTestId("journal-calibration-note")).toHaveText(
      "样本不足（<20 条已复盘且填了信心）"
    );
    // 没有曲线、没有命中率、没有百分比
    await expect(calibration.getByTestId("journal-calibration-bin")).toHaveCount(0);
    await expect(calibration).not.toContainText("命中率");
    await expect(calibration).not.toContainText("%");
  });

  test("导出与隐私：页底「导出我的日志」与不提供单条删除的原文告知", async ({ page }) => {
    const store = await openJournal(page, { stats: STATS_INSUFFICIENT });

    const link = page.getByTestId("journal-export");
    await expect(link).toHaveText("导出我的日志");
    // 后端没给 export_hint（Phase B 未上线）→ 拼契约里的固定端点
    await expect(link).toHaveAttribute("href", "/api/v1/journal/export?format=csv");

    // 隐私告知原文，逐字
    await expect(page.getByTestId("journal-privacy")).toHaveText(
      "日志不提供单条删除，以保证复盘记录不可篡改；如需清除请删除账号"
    );

    // 后端给了 export_hint 时优先用它
    store.stats = STATS_WITH_SIZE_AND_CALIBRATION;
    await page.reload();
    await expect(page.getByTestId("journal-panel")).toBeVisible();
    await expect(page.getByTestId("journal-export")).toHaveAttribute(
      "href",
      "/api/v1/journal/export?format=csv&scope=mine"
    );
  });

  test("Phase B 字段缺省（后端未上线）：不画仓位分栏/校准卡，也不补 0", async ({ page }) => {
    await openJournal(page, { stats: STATS_ENOUGH, entries: [DUE_ENTRY, REVIEWED_ENTRY] });

    // 旧统计照旧（五类占比 + 平均超额），Phase B 的块一个都不画
    await expect(page.getByTestId("journal-stats-breakdown")).toBeVisible();
    await expect(page.getByTestId("journal-stats-by-size")).toHaveCount(0);
    await expect(page.getByTestId("journal-stats-weighted-excess")).toHaveCount(0);
    await expect(page.getByTestId("journal-calibration")).toHaveCount(0);

    // 列表行不显示仓位标签（缺字段 ≠ 只观察）；到期条目照旧有复盘按钮
    await expect(page.getByTestId("journal-entry-size")).toHaveCount(0);
    await expect(page.getByTestId("journal-review-open")).toHaveCount(1);
  });

  test("已复盘条目没有编辑/删除入口（append-only）", async ({ page }) => {
    // 只放已复盘条目：这样"没有任何复盘按钮"是确定判据（有 due 条目时本来就该有一个）
    await openJournal(page, { entries: [REVIEWED_ENTRY] });

    const panel = page.getByTestId("journal-panel");
    await expect(page.getByTestId("journal-reviewed-item")).toHaveCount(1);

    // 三个入口都不许有：编辑 / 删除 / 再评一次
    await expect(panel.getByRole("button", { name: "编辑" })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "删除" })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: "复盘" })).toHaveCount(0);
    await expect(panel).not.toContainText("编辑");
    // 「删除」只允许出现在页脚的隐私告知原文里（spec §S9.7.9 要求逐字告知"不提供单条删除"），
    // 所以这一条**按条目行**断言：已复盘条目自己没有删除入口。
    const reviewedRow = page.getByTestId("journal-reviewed-item").first();
    await expect(reviewedRow).not.toContainText("删除");

    // 已复盘条目只有"看"：归因标签 + 备注都在，且没有操作按钮
    const reviewed = page.getByTestId("journal-reviewed-item").first();
    await expect(reviewed.getByTestId("journal-reviewed-verdict")).toBeVisible();
    await expect(reviewed.locator("button")).toHaveCount(0);
  });

  test("合规：页脚固定免责可见，可见文本零判断词", async ({ page }) => {
    await openJournal(page);

    // spec §S9.5 的固定免责，逐字
    await expect(page.locator(".journal__disclaimer")).toHaveText(
      "决策日志记录的是你自己的判断，不构成投资建议；结果统计不代表未来表现。"
    );

    const text = await page.locator("body").innerText();
    const hits = FORBIDDEN.filter((word) => text.includes(word));
    expect(hits, `页面可见文本出现禁用词：${hits.join("、")}`).toEqual([]);
  });

  test("防回归：/me 的其余 tab 不受影响（triage 仍是默认 tab）", async ({ page }) => {
    // triage 读面单独 mock：本条只验 tab 结构没被 journal 抢走
    await page.route("**/auth/session", (route) => route.fulfill({ status: 200, json: USER }));
    await page.route("**/auth/csrf", (route) =>
      route.fulfill({ status: 200, json: { csrf_token: "csrf-token-test" } })
    );
    await page.route("**/api/v1/watchlists/triage", (route) =>
      route.fulfill({
        status: 200,
        json: {
          as_of: "2026-10-17",
          state: "ok",
          quiet_count: 0,
          folded: 0,
          items: [],
          unavailable: [],
          unavailable_keys: [],
          disclaimer: "本产品只展示已发生的数据事实，不构成投资建议；历史数据不代表未来表现。",
        },
      })
    );
    // 先注册通用的 journal 读面，再注册 `/stats`（Playwright 后注册的优先命中）：
    // 反过来的话 `/stats` 会被通用那条吞掉，统计卡读到 `{items: []}` 而字段全 undefined。
    await page.route("**/api/v1/journal**", (route) =>
      route.fulfill({ status: 200, json: { items: [] } })
    );
    await page.route("**/api/v1/journal/stats", (route) =>
      route.fulfill({ status: 200, json: STATS_INSUFFICIENT })
    );
    // 自选面板会读 `/api/v1/watchlists`：不 mock 就会打真接口（401 → 全局清态 → 页面重挂）
    await page.route("**/api/v1/watchlists", (route) => route.fulfill({ status: 200, json: [] }));

    await page.goto("/me");
    await expect(page.getByRole("tab", { name: /今日体检/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    // 新 tab 存在且可点，且不新增路由（仍是 /me 上的 query）
    const journalTab = page.getByRole("tab", { name: /决策日志/ });
    await expect(journalTab).toBeVisible();
    await journalTab.click();
    await expect(page).toHaveURL(/\/me\?tab=journal$/);
    await expect(page.getByTestId("journal-panel")).toBeVisible();

    // 切回自选 tab：URL 契约不变
    await page.getByRole("tab", { name: /自选/ }).click();
    await expect(page).toHaveURL(/\/me\?tab=watchlist$/);
  });
});
