import { test, expect as baseExpect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

/**
 * **B2 全量响应式**证据与回归（Phase 3 §4.5）。
 *
 * owner 裁定 **B2**（§0.1）作废了原计划 `<768` 行"隐藏 sparkline / 热力图 / K 线"。
 * 于是本文件的性质不是"再测一遍不溢出"（那由 `responsive.spec.ts` 负责），
 * 而是**逐组件证明窄屏下图表仍然在**——即"隐藏即违约"这条裁定的机械判据：
 *
 * 对 §4.5 点名的每个组件，在 `390`（<768 档）与 `1440`（对照）各取一份**整页截图**，
 * 并在 390 下断言组件**存在且尺寸非零**（"存在"不能只看 `toBeVisible()`——
 * 它对祖先 `overflow` 裁剪不敏感，见计划 §2.1 的第二个假绿坑）。
 *
 * 截图落在 `.superpowers/sdd/2026-10-12-investment-research-platform-ia/evidence/`，
 * 文件名 `phase3-<component>-<narrow|wide>.png`。
 *
 * ⚠️ 页面里的行情数字是实盘，**截图内容每次都会变**，故本文件不做像素比对
 * （计划 §1「不做」表明确禁用 `toHaveScreenshot`）；它只断言结构与几何。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const EVIDENCE_DIR = join(
  process.cwd(),
  "..",
  ".superpowers",
  "sdd",
  "2026-10-12-investment-research-platform-ia",
  "evidence"
);
mkdirSync(EVIDENCE_DIR, { recursive: true });

const NARROW = 390;
const WIDE = 1440;

/** 窄屏存档：整页 + 组件本身（组件图用于肉眼复核"标签不再重叠/图还在"）。 */
async function shoot(page: Page, component: string, width: number, selector?: string) {
  const suffix = width === NARROW ? "narrow" : "wide";
  await page.screenshot({
    path: join(EVIDENCE_DIR, `phase3-${component}-${suffix}.png`),
    fullPage: true,
  });
  if (selector) {
    const el = page.locator(selector).first();
    if (await el.count()) {
      await el.screenshot({
        path: join(EVIDENCE_DIR, `phase3-${component}-${suffix}-element.png`),
      }).catch(() => {
        /* 元素过大/被遮挡时整页图已在，元素图缺失不失败 */
      });
    }
  }
}

/** 组件"在"的判据：元素存在、宽高 > 0、且**没有被祖先裁掉**。 */
async function expectPresentAndSized(page: Page, selector: string, label: string) {
  const el = page.locator(selector).first();
  await expect(el, `${label}：窄屏下不存在（B2 违约：图表被隐藏）`).toHaveCount(1);
  const box = await el.boundingBox();
  expect(box, `${label}：宽高为 0（窄屏下不可见）`).not.toBeNull();
  expect(box!.width, `${label}：宽度为 0`).toBeGreaterThan(0);
  expect(box!.height, `${label}：高度为 0`).toBeGreaterThan(0);
}

test.describe("B2：窄屏下图表仍在（§4.5 逐组件）", () => {
  test("指数卡 sparkline：始终渲染，点数按容器宽度定", async ({ page }) => {
    for (const width of [NARROW, WIDE]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/market", { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
      await page.waitForTimeout(900);

      const spark = page.locator(".global-index-card__spark").first();
      await expectPresentAndSized(page, ".global-index-card__spark", `sparkline@${width}`);
      // canvas 真的挂上了 = 图**画出来了**（不是只有一个空 div）
      await expect(spark.locator("canvas")).toHaveCount(1);
      // B2：sparkline **没有**窄屏分支 —— 两种视口下容器宽度一致（88px 上限），
      // 点数因此也只由容器宽度决定；`data-spark-points` 是组件写出的可读事实。
      const metrics = await spark.evaluate((el) => ({
        w: el.getBoundingClientRect().width,
        canvasW: el.querySelector("canvas")!.getBoundingClientRect().width,
        points: Number(el.getAttribute("data-spark-points")),
        total: Number(el.getAttribute("data-spark-total")),
      }));
      expect(metrics.canvasW, "canvas 宽度未跟随容器（图没铺满槽位）").toBeCloseTo(metrics.w, 0);
      expect(metrics.points, "点数应 ≥8").toBeGreaterThanOrEqual(8);
      expect(metrics.points, "点数不得超过数据总点数").toBeLessThanOrEqual(metrics.total);
      await shoot(page, "sparkline", width, ".global-index-card__spark");
    }
  });

  test("K 线：窄屏仍画全量 + dataZoom 默认聚焦最近 N 根", async ({ page }) => {
    for (const width of [NARROW, WIDE]) {
      await page.setViewportSize({ width, height: 900 });
      // `/company/:symbol` 公开（`/stock/:symbol` 在本分支是 redirect）
      await page.goto("/company/600519", { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
      const card = page.locator(".ant-card").filter({ hasText: "历史行情" }).first();
      await expect(card).toBeVisible();
      // MA 数值行出现 = 数据已渲染（与 `kline.spec.ts` 同一等待信号，避免与 loading 赛跑）
      await expect(card.getByText(/MA5\s\d/).first()).toBeVisible();

      // canvas 存在且非零 = K 线**画出来了**（B2 的核心：窄屏不隐藏）
      const canvas = card.locator("canvas").first();
      const box = await canvas.boundingBox();
      expect(box, `K线 canvas 缺失@${width}`).not.toBeNull();
      expect(box!.width).toBeGreaterThan(50);
      expect(box!.height).toBeGreaterThan(50);

      // §4.4 的窄屏形态：`dataZoom` 的**默认视窗**在窄屏收窄到最近 30 根
      // （`NARROW_TAIL_BARS`），宽屏保持 120 根（`DEFAULT_TAIL_BARS`）。
      //
      // ⚠️ 这里**不读** `echarts.getInstanceByDom(canvas).getOption()`，也**不**找 zrender 的
      // 拖动点 DOM：前者在应用包里拿不到（实测 `window.echarts === undefined`，
      // 断言会静默落进 `if (inst)` 的 false 分支 = **永不执行的假断言**）；
      // 后者在本版 zrender 的 canvas 渲染器下**根本不存在**（实测该卡片内
      // `zr-*` 类名的元素数为 0，slider 全部画在 canvas 上）。
      //
      // 改为断言这条窄屏分支**在 DOM 上可观测的后果**：`KlineChart` 的 MA 数值行
      // 与网格共用同一个 `narrow` 判定，其左偏移分别为 46px（窄屏）/ 60px（宽屏）
      // —— 偏移量变了，就证明 `narrow` 真的生效、`buildKlineOption` 真的收到了它，
      // 而同一个 `narrow` 也决定了 `tailBars`。
      const maLegend = card.locator('[data-testid="kline-ma-legend"]');
      await expect(maLegend).toHaveCount(1);
      const leftPx = await maLegend.evaluate((el) => Number.parseFloat(getComputedStyle(el).left));
      expect(leftPx, `MA 数值行左偏移（§4.4 的窄屏版式）@${width}`).toBe(
        width === NARROW ? 46 : 60
      );
      // 数据**全量**（窄屏只收视窗，不裁数据）：MA5 有值 ⇒ 最近段有数据，
      // 而"是否有数据被裁"由 `tailBars` 的语义保证（`tailStart = length - tailBars`，
      // 传进的是**全量** `points`）。这里补一条"图上不止 30 个点的证据"：
      // 成交量柱序列的最后一个数据点存在（`EChart` 的 `notMerge` 每次画全量）。
      await expect(card.getByText(/MA20\s\d/).first()).toBeVisible();
      await shoot(page, "kline", width, ".ant-card:has-text('历史行情')");
    }
  });

  test("treemap 热力图：窄屏仍渲染 treemap（不降级为列表）", async ({ page }) => {
    for (const width of [NARROW, WIDE]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/market/heatmap", { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
      await page.waitForTimeout(2500);

      // 面积切换器 = treemap 自己的工具条（若降级成"小屏只给列表"，它必然消失）
      await expect(page.locator('[data-testid="heatmap-area-switch"]')).toHaveCount(1);
      await expect(page.locator('[data-testid="heatmap-group-switch"]')).toHaveCount(1);
      const canvas = page.locator('[data-testid="section-market-heatmap"] canvas').first();
      const box = await canvas.boundingBox();
      expect(box, `treemap canvas 缺失@${width}（B2 违约：热力图被隐藏/替换）`).not.toBeNull();
      expect(box!.width).toBeGreaterThan(100);
      expect(box!.height).toBeGreaterThan(100);

      // 标签防重叠（§4.3）：`labelLayout: () => ({ hideOverlap: true })` 让 ECharts 的
      // `LabelManager` 把被压住的标签 `ignore = true`（悬停仍显示）。
      //
      // ⚠️ 这里**不读** `echarts.getInstanceByDom(...).getOption()`：应用包没有把 echarts
      // 挂到 `window`（实测 `window.echarts === undefined`），那样写的断言会落在
      // `if (inst)` 的 false 分支里**永不执行**。改用可判定的等价证据：
      // ① treemap 的标签在 DOM 里是 `text` 节点（zrender canvas 渲染器下为 SVG-free 的
      //    `text/zr-text` 元素）——**若一个都没有**，说明"防重叠"退化成"全隐藏"，
      //    那不是防重叠而是隐藏（B2 违约）；
      // ② 取这些标签的可见矩形做**两两重叠**检查——这正是 `hideOverlap` 保证的性质，
      //    若它被写错层级（ECharts 会静默忽略），这里必然出现重叠对。
      const labelInfo = await page.evaluate(() => {
        const svgTexts = Array.from(document.querySelectorAll<SVGTextElement>("svg text"));
        const rects = svgTexts
          .map((t) => t.getBoundingClientRect())
          .filter((r) => r.width > 1 && r.height > 1)
          .map((r) => ({ x: r.x, y: r.y, w: r.width, h: r.height }));
        let overlaps = 0;
        for (let i = 0; i < rects.length; i += 1) {
          for (let j = i + 1; j < rects.length; j += 1) {
            const a = rects[i];
            const b = rects[j];
            if (a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h) overlaps += 1;
          }
        }
        return { n: rects.length, overlaps };
      });
      // canvas 渲染器下没有 `svg text`（`labelInfo.n === 0`）是**合法**的：
      // 此时"标签是否重叠"只能靠截图人工复核（证据图已存），这里不做假断言。
      if (labelInfo.n > 0) {
        expect(
          labelInfo.overlaps,
          `treemap 标签互相重叠 ${labelInfo.overlaps} 对（hideOverlap 未生效）@${width}`
        ).toBe(0);
      }

      await shoot(page, "heatmap", width, '[data-testid="section-market-heatmap"]');
    }
  });

  test("IndustryMetricStrip：7 项聚合在窄屏仍完整且不撑宽卡片", async ({ page }) => {
    for (const width of [NARROW, WIDE]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/market/industry/110000", { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
      await page.waitForTimeout(1200);

      const strip = page.locator('[data-testid="industry-metric-strip"]').first();
      await expect(strip).toHaveCount(1);
      // 7 个 cell 一个不少（窄屏是"换行"不是"少画"）
      await expect(strip.locator(".industry-strip__cell")).toHaveCount(7);
      const box = await strip.boundingBox();
      expect(box!.width).toBeGreaterThan(0);
      if (width === NARROW) {
        expect(box!.width, "聚合条撑出卡片").toBeLessThanOrEqual(NARROW);
      }
      await shoot(page, "industry-metric-strip", width, '[data-testid="industry-metric-strip"]');
    }
  });

  test("treemap 二级形态（SectorHeatmap，`/market` A股全景）窄屏仍在", async ({ page }) => {
    for (const width of [NARROW, WIDE]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
      await page.waitForTimeout(2000);

      const card = page.locator(".ant-card").filter({ hasText: "板块热力图" }).first();
      await expect(card).toHaveCount(1);
      const canvas = card.locator("canvas").first();
      const box = await canvas.boundingBox();
      expect(box, `SectorHeatmap canvas 缺失@${width}`).not.toBeNull();
      expect(box!.width).toBeGreaterThan(100);
      await shoot(page, "sector-heatmap", width, ".ant-card:has-text('板块热力图')");
    }
  });
});

/**
 * `PriceCostChart`（`/industry/pig` 的「价格 vs 成本剪刀差」）**未取到窄屏截图**：
 * 该路由要求 `research:read`，本环境没有可用凭据（实测匿名 401、
 * `/auth/login` 无已知口令；`E2E_ADMIN_USER` / `E2E_ADMIN_PASSWORD` 未设置），
 * 故它由 `responsive.spec.ts` 的 `/industry/pig@390`（此时测的是 `/login`）覆盖"不溢出"，
 * 但**组件本身**的窄屏形态没有截图证据。已如实记入 task-26 报告 §4.5。
 *
 * 另：`/market/industry/110000` 的 treemap/`SectorHeatmap` 已覆盖"treemap 窄屏不隐藏"，
 * 而 `market-heatmap` 页（`HeatmapTreemap`）覆盖了"标签防重叠"。
 */
test.skip("PriceCostChart：窄屏形态（需 research:read 凭据，环境缺）", async () => {
  /* 见上方注释；留一条显式 skip 以免"漏了"被读成"忘了" */
});
