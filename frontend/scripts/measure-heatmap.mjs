// S1.5 热力图性能基准（spec §S1.5：≥3000 标的 treemap 首屏渲染不得阻塞主线程）。
//
// 用法（frontend/ 下；需要 vite(:3000) + uvicorn(:8000) 都已起，dev 库）：
//   node scripts/measure-heatmap.mjs                      # 默认权威口径（3 次取中位）
//   node scripts/measure-heatmap.mjs --runs=3 --json=/tmp/after.json
//   node scripts/measure-heatmap.mjs --paint=all          # 诊断：一次画完（不分片）
//   node scripts/measure-heatmap.mjs --paint=chunked      # 诊断：旧分片着色
//   node scripts/measure-heatmap.mjs --url=http://127.0.0.1:3000/market/heatmap
//
// 测量什么、为什么：
//   1. `longtask`（PerformanceObserver）——规范判据的浏览器口径：>50ms 的主线程任务。
//      输出每次任务的时长，并统计 >50ms / >100ms 的个数（阈值论证见 task-9 报告）。
//   2. ECharts `setOption` 归因——treemap 布局在 setOption 内同步完成，是 O(n) 大头。
//      探针（scripts/lib/heatmap-perf-instrument.mjs）把 echarts.init 返回实例的
//      setOption 包一层计时，输出「次数 + 每次耗时 + 每次送的叶子数」。
//      **次数**是关键：旧实现每帧重排一次整棵树，次数 ≈ 叶数/800；
//      修好后应恒为 1 次/首屏（不含面积/分组切换）。
//   3. `buildTree` 纯函数耗时（组件内 dev 埋点 `window.__heatmapPhases`）。
//   4. 交互（切分组 / 切面积 / 点集合块下钻）的 click→settled 与交互期间长任务。
//
// 脚本自身不做断言，只输出数字 + 一份 JSON（--json），断言与结论写在 task-9 报告里。
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { chromium } from "playwright-core";
import {
  INSTRUMENT_SCRIPT,
  collectPageMetrics,
  resetObservations,
} from "./lib/heatmap-perf-instrument.mjs";

const args = new Map(
  process.argv.slice(2).map((a) => {
    const [k, ...rest] = a.replace(/^--/, "").split("=");
    return [k, rest.join("=") || "1"];
  })
);
const RUNS = Number(args.get("runs") ?? 3);
const PAINT = args.get("paint") ?? "";
// 诊断：把矩形预算调到远超数据量 → 等价于修复前的「不做聚合」路径，
// 用来复现 before 数字（配合页面里的 --paint=chunked 逐帧重排）。
const NOCAP = args.get("nocap") === "1";
const MIN_SHARE = args.get("min-share") ?? "";
const BASE = args.get("url") ?? "http://127.0.0.1:3000/market/heatmap";
// 诊断参数（不改生产默认）：paint=all 一次画完；anim=0 关入场动画
const DIAG = [
  PAINT && `paint=${PAINT}`,
  MIN_SHARE && `min-share=${MIN_SHARE}`,
]
  .filter(Boolean)
  .join("&");
const PAGE_URL = DIAG ? `${BASE}?${DIAG}` : BASE;
const JSON_OUT = args.get("json") ?? "";
const BACKEND = args.get("backend") ?? "http://127.0.0.1:8000";

const med = (arr) => {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
};

// 先跑聚合口径的机械校验（面积守恒/计数诚实/预算生效/drill-down 可逆）：
// 这些是确定性的，失败就是真错，不该等浏览器测量。
try {
  const guard = execFileSync(process.execPath, ["scripts/check-heatmap-aggregate.mjs"], {
    encoding: "utf8",
  });
  console.log(`[guard] 聚合口径校验通过\n${guard.trim().split("\n").slice(-2).join("\n")}`);
} catch (e) {
  console.error("[guard] 聚合口径校验失败：\n" + String(e.stdout || e.message));
  process.exit(1);
}

// 后端本身的口径（顺带记录，与前端渲染无关）
const t0 = Date.now();
const payload = await (await fetch(`${BACKEND}/api/v1/market/heatmap/treemap`)).json();
const backendMs = Date.now() - t0;
console.log(
  `[backend] GET treemap: ${backendMs}ms, count=${payload.count}, ` +
    `json=${JSON.stringify(payload).length}B, units=${payload.mv_unit}/${payload.amount_unit}, ` +
    `as_of=${payload.as_of}(${payload.as_of_quality})`
);

const browser = await chromium.launch();
const allRuns = [];

for (let i = 1; i <= RUNS; i++) {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  await page.route("**/api/v1/market/heatmap/**", (route) =>
    route.continue({ url: route.request().url().replace(":3000", ":8000") })
  );
  await page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: '{"code":"AUTH_UNAUTHORIZED"}',
    })
  );
  await page.addInitScript(INSTRUMENT_SCRIPT);

  const tStart = Date.now();
  await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".section-card canvas", { timeout: 30000 });
  const tCanvas = Date.now() - tStart;
  await page.waitForFunction(
    () => /\d{3,}/.test(document.querySelector('[data-testid="heatmap-count"]')?.textContent || ""),
    { timeout: 30000 }
  );
  // 等两帧，让首屏期间的 setOption/长任务都落进观察窗口
  await page.evaluate(
    () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  );
  const tReady = Date.now() - tStart;
  const m = await collectPageMetrics(page);
  const aggNote = await page
    .locator('[data-testid="heatmap-aggregate-note"]')
    .textContent()
    .catch(() => null);
  const run = { canvas: tCanvas, ready: tReady, aggNote, ...m };
  allRuns.push(run);

  console.log(
    `[run${i}] goto→canvas ${run.canvas}ms, goto→ready ${run.ready}ms, ` +
      `longTasks(${run.longTasks.length})=${JSON.stringify(run.longTasks)}, longest=${run.longest}ms, ` +
      `>50ms=${run.over50}, >100ms=${run.over100}`
  );
  console.log(
    `        setOption x${run.setOptionCount} total=${run.setOptionTotalMs}ms ` +
      `each=${JSON.stringify(run.setOptionMs)} leaves=${JSON.stringify(run.setOptionLeaves)} ` +
      `phases=${JSON.stringify(run.phases.map((p) => `${p.name}:${p.ms}ms`))}`
  );
  console.log(`        aggregate-note: ${JSON.stringify(aggNote)}`);
  await ctx.close();
}

const summary = {
  runs: allRuns,
  median: {
    canvas: med(allRuns.map((r) => r.canvas)),
    ready: med(allRuns.map((r) => r.ready)),
    longest: med(allRuns.map((r) => r.longest)),
    over50: med(allRuns.map((r) => r.over50)),
    over100: med(allRuns.map((r) => r.over100)),
    setOptionCount: med(allRuns.map((r) => r.setOptionCount)),
    setOptionTotalMs: med(allRuns.map((r) => r.setOptionTotalMs)),
  },
};
console.log(
  `[median] canvas ${summary.median.canvas}ms; ready ${summary.median.ready}ms; ` +
    `longest task ${summary.median.longest}ms; tasks>50ms ${summary.median.over50}; ` +
    `tasks>100ms ${summary.median.over100}; setOption x${summary.median.setOptionCount} ` +
    `(${summary.median.setOptionTotalMs}ms)`
);

// ---------- 交互测量：切分组 / 切面积 / 集合块下钻 ----------
async function interaction() {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  await page.route("**/api/v1/market/heatmap/**", (route) =>
    route.continue({ url: route.request().url().replace(":3000", ":8000") })
  );
  await page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: '{"code":"AUTH_UNAUTHORIZED"}',
    })
  );
  await page.addInitScript(INSTRUMENT_SCRIPT);
  await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".section-card canvas", { timeout: 30000 });
  await page.waitForTimeout(2000);

  const steps = [];
  async function step(name, action, settleMs = 700) {
    await resetObservations(page);
    const t = Date.now();
    await action();
    await page.waitForTimeout(settleMs);
    const elapsed = Date.now() - t;
    const mm = await collectPageMetrics(page);
    steps.push({
      name,
      elapsed,
      longest: mm.longest,
      over50: mm.over50,
      setOptionCount: mm.setOptionCount,
      setOptionMs: mm.setOptionMs,
      setOptionLeaves: mm.setOptionLeaves,
      buildTreeMs: mm.phases.map((p) => p.ms),
    });
    console.log(
      `[interact] ${name}: click→settled ${elapsed}ms (含 ${settleMs}ms 等待), ` +
        `longest=${mm.longest}ms, >50ms=${mm.over50}, setOption x${mm.setOptionCount} ` +
        `${JSON.stringify(mm.setOptionMs)} leaves=${JSON.stringify(mm.setOptionLeaves)} ` +
        `buildTree=${JSON.stringify(mm.phases.map((p) => p.ms))}`
    );
    return mm;
  }

  await step("切分组 全市场→申万一级", () =>
    page.click('[data-testid="heatmap-group-switch"] .ant-radio-button-wrapper:nth-child(2)')
  );
  await step("切分组 →概念板块", () =>
    page.click('[data-testid="heatmap-group-switch"] .ant-radio-button-wrapper:nth-child(3)')
  );
  await step("切面积 市值→成交额", () =>
    page.click('[data-testid="heatmap-area-switch"] .ant-radio-button-wrapper:nth-child(2)')
  );
  await step("切回 全市场 + 市值", async () => {
    await page.click('[data-testid="heatmap-group-switch"] .ant-radio-button-wrapper:nth-child(1)');
    await page.click('[data-testid="heatmap-area-switch"] .ant-radio-button-wrapper:nth-child(1)');
  });

  // 集合块下钻：用真实鼠标点击聚合块的像素中心（坐标由应用内 dev 探针给出，
  // 不用内部 API 伪造事件）。验证「聚合不是死数据」——点击后该组应展开为全量。
  const aggNoteBefore = await page
    .locator('[data-testid="heatmap-aggregate-note"]')
    .textContent()
    .catch(() => null);
  const tile = await page.evaluate(() => {
    try {
      return window.__heatmapFindAggregateTile?.() ?? null;
    } catch (e) {
      return { error: String(e) };
    }
  });
  console.log(`[drill] aggregate tile: ${JSON.stringify(tile)}`);
  if (!tile || tile.error || !tile.w) {
    console.log(
      "[drill] SKIP: 未能定位聚合块的屏幕矩形（getLayout() 与命中测试坐标系不重合，见报告「已知问题」）"
    );
  }
  let drillAfter = null;
  if (tile && !tile.error && tile.w) {
    await page.evaluate(({ x, y, w, h }) => {
      const mark = document.createElement("div");
      mark.style.cssText = `position:fixed;left:${x - w / 2}px;top:${y - h / 2}px;width:${w}px;height:${h}px;border:2px solid magenta;z-index:99999;pointer-events:none`;
      mark.dataset.testid = "aggregate-tile-mark";
      document.body.appendChild(mark);
    }, tile);
    await page.screenshot({ path: "/tmp/heatmap-aggregate-tile-marked.png" });
    const pathBefore = new URL(page.url()).pathname;
    await step("点击聚合块下钻", async () => {
      await page.mouse.click(tile.x, tile.y);
    });
    const pathAfter = new URL(page.url()).pathname;
    if (pathAfter !== pathBefore) {
      // 命中测试与 getLayout() 坐标不重合 → 点到个股块并跳转（见 task-9 报告 §5.4）
      drillAfter = `SKIP: 坐标点击命中了个股块并跳转到 ${pathAfter}（坐标系不重合，非下钻失败）`;
    } else {
      drillAfter = await page
        .locator('[data-testid="heatmap-aggregate-note"]')
        .textContent()
        .catch(() => null);
    }
  }
  console.log(`[drill] note before: ${JSON.stringify(aggNoteBefore)}`);
  console.log(`[drill] note after drill-down: ${JSON.stringify(drillAfter)}`);
  await step("面积/分组收起后稳定态", async () => {}, 200);

  const legend = await page
    .locator('[data-testid="heatmap-legend"]')
    .textContent()
    .catch(() => null);
  const count = await page
    .locator('[data-testid="heatmap-count"]')
    .textContent()
    .catch(() => null);
  console.log(`[legend] ${JSON.stringify(legend)} | [count] ${count}`);
  await page.screenshot({ path: "/tmp/heatmap-perf-final.png" });
  await ctx.close();
  return { steps, drillTile: tile, drillNoteBefore: aggNoteBefore, drillNoteAfter: drillAfter, legend, count };
}


// ---------- 各分组「首屏」（直接以 ?group= 进入）----------
// 判据是首屏不阻塞，所以每个分组维度都要各自量一次（分组切换是交互，另有上面一项）。
async function firstPaintByGroup(groupKey) {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();
  await page.route("**/api/v1/market/heatmap/**", (route) =>
    route.continue({ url: route.request().url().replace(":3000", ":8000") })
  );
  await page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: '{"code":"AUTH_UNAUTHORIZED"}',
    })
  );
  await page.addInitScript(INSTRUMENT_SCRIPT);
  const url = `${BASE}?group=${groupKey}${DIAG ? `&${DIAG}` : ""}`;
  const t0 = Date.now();
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".section-card canvas", { timeout: 30000 });
  const canvas = Date.now() - t0;
  await page.waitForFunction(
    () => /\d{3,}/.test(document.querySelector('[data-testid="heatmap-count"]')?.textContent || ""),
    { timeout: 30000 }
  );
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const ready = Date.now() - t0;
  const m = await collectPageMetrics(page);
  const note = await page
    .locator('[data-testid="heatmap-aggregate-note"]')
    .textContent()
    .catch(() => null);
  await ctx.close();
  const out = { group: groupKey, canvas, ready, longest: m.longest, over50: m.over50, over100: m.over100, rects: m.setOptionLeaves.at(-1) ?? null, note };
  console.log(
    `[first-paint group=${groupKey}] canvas ${canvas}ms, ready ${ready}ms, longest=${m.longest}ms, ` +
      `>50ms=${m.over50}, >100ms=${m.over100}, rects=${out.rects}, note=${JSON.stringify(note)}`
  );
  return out;
}

const byGroup = [];
for (const g of ["all", "sw_l1", "concept"]) byGroup.push(await firstPaintByGroup(g));

const inter = await interaction();
await browser.close();

const result = {
  diag: DIAG || "production-default",
  pageUrl: PAGE_URL,
  ...summary,
  firstPaintByGroup: byGroup,
  interaction: inter,
};
if (JSON_OUT) {
  writeFileSync(JSON_OUT, JSON.stringify(result, null, 2));
  console.log(`[json] wrote ${JSON_OUT}`);
}
console.log("[done]");
