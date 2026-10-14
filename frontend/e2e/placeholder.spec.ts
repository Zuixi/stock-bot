import { test, expect as baseExpect, type Page } from "@playwright/test";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { mkdirSync } from "node:fs";

/**
 * **占位路由的路线图骨架**（Phase 4 §5.7）。
 *
 * ## 这一条在守什么
 *
 * 8 条占位路由原先只渲染一句「X 模块尚未落地，页面结构已就位」——零信息量。§5.7 把它们
 * 升级为"路线图骨架"：子模块（名称 + `规划中/接入中`）+ "这个模块需要什么数据"。
 *
 * 判据不是"页面上有路线图"（那太弱：只渲染 1 项也过），而是**每条占位路由 ≥3 个 roadmap 项**。
 *
 * ## 为什么路由集合要**从清单读**，而不是在测试里手抄 8 个路径
 *
 * `check-routes.mjs` 的 `PLACEHOLDER_CONTRACT` 断言"占位集合 == 契约（8 条，成员逐条一致）"。
 * 如果本文件再手抄一遍那 8 个路径，就出现**第三份清单**：将来某条路由摘掉占位、或新增一条
 * 占位路由，`check-routes` 会红，而本文件可能**根本不测**新成员（测试还是绿的）——那正是
 * 计划 §2.1「假绿」的形态。故这里直接 import 清单，取 `isPlaceholder` 的路由全集。
 *
 * ## 为什么还要断 `data-roadmap-count`（结构计数）而不只数 item 元素
 *
 * 两者都要：
 * - 数 `[data-roadmap-item]` = **用户真的看得到几项**；
 * - 断 `data-roadmap-count` 与之一致 = 防"数据里 5 项、只渲染了 1 项"这类渲染层丢项
 *   （只数数据不算数，只数 DOM 又不知道数据里有几项）。
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

/** 占位路由全集（**从清单读**，不在测试里手抄） */
async function placeholderPaths(): Promise<string[]> {
  const url = pathToFileURL(join(process.cwd(), "src/app/router/route-manifest.ts")).href;
  const manifest = (await import(url)) as {
    getPlaceholderRoutes: () => Array<{ path: string }>;
  };
  return manifest.getPlaceholderRoutes().map((r) => r.path);
}

/** 该模块在 `/market/data-sources` 之外**不依赖任何后端数据**：占位页是纯静态渲染 */
async function openPlaceholder(page: Page, path: string) {
  await page.goto(path, { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("placeholder-route-params")).toBeVisible();
}

test("占位路由集合非空（防清单读空导致整条用例空跑）", async () => {
  const paths = await placeholderPaths();
  expect(paths.length, "占位路由为 0 ⇒ 下面的断言会全部空转").toBeGreaterThan(0);
  // 与 check-routes 的契约条数同源；这里只做"没读空"的下界，成员一致性归 check-routes
  expect(paths.length).toBeGreaterThanOrEqual(3);
  console.log(`[placeholder] 占位路由 ${paths.length} 条：${paths.join("、")}`);
});

test("每条占位路由都渲染 ≥3 个 roadmap 项（含状态与所需数据）", async ({ page }) => {
  const paths = await placeholderPaths();
  const failures: string[] = [];

  for (const path of paths) {
    await openPlaceholder(page, path);

    const roadmap = page.getByTestId("placeholder-roadmap");
    const roadmapCount = await roadmap.count();
    if (roadmapCount !== 1) {
      failures.push(`${path}: placeholder-roadmap 数量 = ${roadmapCount}（应为 1）`);
      continue;
    }

    const items = roadmap.locator("[data-roadmap-item]");
    const n = await items.count();
    if (n < 3) {
      failures.push(`${path}: roadmap 项 = ${n}（应 ≥3）`);
      continue;
    }

    // 数据侧计数必须与 DOM 侧一致（防"数据 5 项、渲染 1 项"）
    const declared = Number(await roadmap.locator(".placeholder-roadmap__grid").getAttribute("data-roadmap-count"));
    if (declared !== n) {
      failures.push(`${path}: data-roadmap-count=${declared} 与渲染项数 ${n} 不一致`);
    }

    // 每一项必须**有名称、有状态、有"缺什么"**——只有标题没内容的骨架等于没升级
    for (let i = 0; i < n; i += 1) {
      const item = items.nth(i);
      const name = (await item.getAttribute("data-roadmap-item")) ?? "";
      const status = (await item.getAttribute("data-roadmap-status")) ?? "";
      if (!name.trim()) failures.push(`${path}: 第 ${i + 1} 项缺少 data-roadmap-item`);
      if (!["规划中", "接入中"].includes(status)) {
        failures.push(`${path}: 第 ${i + 1} 项状态非法（"${status}"）`);
      }
      const needs = (await item.locator(".placeholder-roadmap__needs").innerText()).trim();
      if (needs.length < 4) failures.push(`${path}: 第 ${i + 1} 项缺少"卡在什么数据上"`);
    }

    // "这个模块需要什么数据"必须真的上屏（不是只写在数据里）
    const requires = (await roadmap.locator(".placeholder-roadmap__requires").innerText()).trim();
    if (requires.length < 10) failures.push(`${path}: "这个模块需要什么数据"过短（"${requires}"）`);

    console.log(`[placeholder] ${path} → ${n} 项（${declared}），requires=${requires.slice(0, 40)}…`);
  }

  expect(failures, `以下占位路由不满足 §5.7 的路线图判据：\n  ${failures.join("\n  ")}`).toEqual([]);
});

test("占位路由仍保留参数自证（§5.7 未破坏既有 no-404 契约）", async ({ page }) => {
  await page.goto("/search?q=%E7%94%9F%E7%8C%AA", { waitUntil: "domcontentloaded" });
  await expect(page.getByTestId("placeholder-route-params")).toBeVisible();
  // ⚠️ 这两个 testid 原先**只在** `shared/ui/PlaceholderPage`（带 SectionCard 的完整占位页，
  // 只有 `/me` 用它）里实现 ⇒ 8 条占位路由走 `PlaceholderNotice` 时，IA §5.2 的
  // 「把 query 与路径参数原样显示出来」**并未兑现**（实测：`/search?q=生猪` 页面文本里
  // 没有 `q=生猪`，`placeholder-pathname` 也找不到）。§5.7 把这个能力补到占位路由自己的
  // 渲染路径上（`PlaceholderNotice` 的 `ParamSelfProof`），故这两条断言现在才有意义。
  await expect(page.getByTestId("placeholder-pathname")).toHaveText("/search");
  const body = await page.locator("body").innerText();
  expect(body, "query 参数被吞掉（占位页必须原样展示 ?q=）").toContain("q=生猪");
  // 路线图骨架与参数自证同屏
  await expect(page.getByTestId("placeholder-roadmap")).toHaveCount(1);
});

test("视觉存档：/strategy 的路线图骨架（1440 与 390）", async ({ page }) => {
  for (const width of [1440, 390] as const) {
    await page.setViewportSize({ width, height: 900 });
    await openPlaceholder(page, "/strategy");
    await page.screenshot({
      path: join(EVIDENCE_DIR, `phase4-placeholder-strategy-${width}.png`),
      fullPage: true,
    });
    // 路线图骨架不得横向溢出（§5.1 的断点契约同样适用于占位页）
    const sw = await page.evaluate(() => document.documentElement.scrollWidth);
    const iw = await page.evaluate(() => window.innerWidth);
    expect(sw, `/strategy@${width} 横向溢出`).toBeLessThanOrEqual(iw + 1);
  }
});
