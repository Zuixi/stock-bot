---
status: draft
scope: 前端设计整改——一级导航形态与三层布局表达、工作页信息密度（去重复）、设计系统（字体/数字/量级色阶/对比度/动效/焦点）、响应式四档断点、文案与品牌一致性；来源为 2026-10-13 全站设计评审（1440/1280/390 三档实测截图）
touches: frontend/src/app/, frontend/src/pages/, frontend/src/features/nav/, frontend/src/features/market/, frontend/src/features/industry-research/, frontend/src/shared/ui/, frontend/src/shared/config/, frontend/src/app/router/, frontend/public/fonts/, frontend/scripts/, frontend/e2e/, docs/design/landing-market-theme.md, docs/Changelog.md, plans/index.md
updated: 2026-10-13
next-action: owner 裁定 §0 的 5 项（导航形态 / 移动端承诺等级 / 字体自托管预算 / 首页定位 / 数据版图新页位置）；通过后执行 Phase 0（基线冻结 + 先红后绿的门禁）
---

# 前端设计整改 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking。

**Goal:** 把当前"默认值堆叠出来的行情浏览器"变成**有排版主张、信息只出现一次、在 1280 上可用、在 390 上不坏**的投研工作台——不新增页面能力，只改表达。

**Why now（2026-10-13 实测，非推断）:** 评审在运行中的两套前端上取证——`:3000`（`.worktrees/research-platform-ia`，10 导航新 IA）与 `:80`（docker，5 导航旧版）。硬事实：

| 现象 | 实测 | 落点 |
|---|---|---|
| 一级导航溢出 | 1280 折叠 4 项、**1440 折叠 3 项**进 `...` | `.worktrees/research-platform-ia/frontend/src/app/layouts/MainLayout.tsx` |
| 左树恒空 | `MODULE_TREE: NavNode[] = []`，`ModuleSideNav` 整块不渲染 → "三层布局"只有两层 | 同上 `:69` |
| 数据版图钉在每个 Tab | 约 800px×11 行，5 个 Tab 各看一遍 | `frontend/src/pages/market/index.tsx:312` |
| 31 个行业渲染两遍 | `SwIndustryGrid`（:244）与 `IndustryClassification`（:251）同屏，信息等价 | 同上 |
| 瓦片文字不过 AA | accent 叠白 alpha 0.5→**2.10:1**、0.85→**3.81:1**（需 4.5）；6/31 瓦片命中白字分支 | `frontend/src/features/market/components/SwIndustryGrid.tsx:8-17` |
| 移动端指数卡崩 | 名称截成 `上证…`/`上…`（无法区分），代码 `CN 000001` 逐字竖排，单卡约 230px 高 | `GlobalIndexCardView.tsx` |
| 移动端横向溢出 | `/market/global` 390px 视口下 `scrollWidth = 749px` | `frontend/src/pages/market-global/index.tsx`（仅新 IA 分支） |
| 日期四种写法 | `10月9日` / `2026-10-12T15:00:00Z` / `1 天前` / `4 天前` | `pages/market-index/index.tsx` 等 |
| 品牌字标两种 | `Stock Bot`（壳/登录/<title>）vs `StockBot`（landing/页脚） | `MainLayout.tsx:116`、`login/index.tsx:82`、`landing/sections/LandingNav.tsx:36` |
| 动效/焦点 | 全站 0 处 `prefers-reduced-motion`；`.css` 里只有 1 处 `:focus-visible` | `frontend/src/**/*.css` |

**除"导航溢出"与"左树为空"两项只存在于 IA 分支外，上表其余项在 `main` 上同样成立**（已逐项核对 `main` 的 `market/index.tsx`、`SwIndustryGrid.tsx`、`landing/index.tsx`）。

**Architecture:** 五条互不阻塞的工作流——
1. **W1 导航与布局表达**：一层导航换左侧常驻栏 + 下发静态二级树（把"三层"补成三层）
2. **W2 信息去重与密度**：数据版图迁出工作台、删 31 行业重复渲染、Tab 副标题同源
3. **W3 设计系统**：字体/数字 token、量级色阶与对比度、日期口径、动效与焦点
4. **W4 响应式**：四档断点契约 + 三处硬伤修复
5. **W5 文案与打磨**：品牌字标、landing 收敛、编号装置、信号历史 diff

**Tech Stack:** 现有栈不变（React 18 + TypeScript + Vite + AntD 5 + ECharts + TanStack Query）；测试 Playwright 1.62 + `scripts/check-design-tokens.mjs` + `scripts/doc_gate.sh`。**零新增运行时依赖**（字体为静态资源，非依赖）。

**与既有计划的关系:** 本计划是 [`2026-10-12-investment-research-platform-ia.md`](./2026-10-12-investment-research-platform-ia.md)（10 导航 IA 主线）的**前端表达层整改**，不改变其 IA 决策（10 导航的**构成与顺序**一律不动）。W1 依赖 IA 的 Wave 0「IA 与路由表冻结」先落；其余工作流与之无耦合，可在 `main` 上独立推进。

---

## 0. 启动前必须由 owner 裁定（5 项）

| # | 待裁定 | 建议 | 影响 |
|---|---|---|---|
| A | **一级导航形态**：A1 左侧图标栏（72px，hover 展开 208px）/ A2 左侧常驻 208px / A3 顶栏只留 7 个"研究层"，策略/风险/交易移入右侧独立区段 | **A1** | 决定 W1 全部工作量与内容区宽度；A3 需 owner 确认是否接受顶栏分区 |
| B | **移动端承诺等级**：B1 桌面优先（≥1280 为设计基准）+ `<768` 只做"行情速览" / B2 全量响应式 | **B1** | B2 会让每个图表组件都要一套移动端形态，成本约 3 倍；B1 只需保证"不坏 + 可读" |
| C | **字体自托管预算**：C1 自托管思源黑体子集（约 120KB×2 字重）+ IBM Plex Mono 数字子集（约 15KB）/ C2 中文沿用系统栈，只引入数字等宽（走 `ui-monospace`，**零字节**） | **C1，超预算则降级 C2** | `--font-num` 抽象层先落地，C2/C1 只影响字体文件，不改调用点 |
| D | **首页定位**：D1 维持既有拍板"免登录行情台"（7051px / 7.8 屏）/ D2 收敛为营销页 3–4 屏 + 行情入口 | **D2** | **D2 推翻既有拍板，必须 owner 明确同意**；不同意则 W5-3 只做"压缩排序"，不做删区块 |
| E | **数据版图新页位置**：`/market/data-sources`（市场域内）vs `/about/data`（关于页） | **`/market/data-sources`** | 决定 `route-manifest.ts` 的一条新路由与面包屑层级 |

**裁定前不得开工 Phase 1 及之后**；Phase 0 可先行（只加不删）。

---

## 1. 目标与非目标

### 做
1. 一级导航在 **1280 与 1440 下 10 项全部可见**，并补上模块内二级树（IA §3.2 的三层布局真正落地）。
2. 同一个数字/同一块内容在全站**只出现一次**（数据版图、31 行业网格、副标题 vs Tab）。
3. 一套**显式 token**：字体族（UI / 数字分离）、量级色阶（不用 accent 编码数量）、日期口径、间距刻度、四档断点。
4. **对比度与可访问性达标**：所有自绘色块文字 ≥4.5:1；`prefers-reduced-motion` 与 `:focus-visible` 全局生效。
5. 移动端 `<768` **不横向溢出、名称不被截断到不可辨识**。

### 不做（明确边界）
| 不做 | 理由 |
|---|---|
| 改后端 / 加接口 | 本计划是表达层整改；数据形态问题归 IA 主线与 S1/S3 spec |
| 改 K 线、ECharts 主题、图表库 | 现有 `shared/ui/EChart` 已按主题取色，不在本次问题清单 |
| 像素级视觉回归快照（`toHaveScreenshot`） | 数据是实盘/混合源，会天天漂；**改用结构断言 + 尺寸断言**（见 §5） |

### 新断言必须绕开的两个已知坑（`docs/references/best-practices/testing.md`）
1. **`getByText` 会撞 strict mode**：中文重文案页面里同名文本常同时存在于嵌套容器、图例与 SVG `<title>`。所有新断言一律落在**唯一容器**上用 `toContainText`，或 `locator(".选择器").filter({ hasText })` 限定作用域（例：断言指数卡名称不能写 `getByText('上证指数')`——它在"全球指数"与"A股核心指数"两处同时存在，必然 strict violation）。
2. **负向断言在 loading/retry 窗口内恒真**：`toHaveCount(0)` / `not.toContainText` 在 react-query 未 settle 时会**假绿**。W2 的"页面不含《数据版图》"这类断言必须先等到请求终态（`waitForLoadState("networkidle")` + 显式等待目标卡片出现）再断言，且把锚点放在**文案**而非仅 `data-testid` 上。
| 引入新组件库 / 设计系统依赖 | 现有 AntD + CSS 变量已够；新增依赖的收益为负 |
| 重画 10 个模块的真页面 | 占位页只升级为"路线图"，真页面归 IA Wave 1+ |
| 新增第三个主题（如"高对比"） | 无消费方；先把两个主题做对 |
| 改 IA 的 10 个导航构成 / 顺序 / 权限三态 | 已拍板，见 IA 计划 §3 与 ADR 0008 |

---

## 2. Phase 0 —— 基线冻结与"先红后绿"门禁（0.5 天）

> 原则：**每一条整改都必须先有一条会失败的断言**，否则改完没人知道有没有回退。

- [ ] **0.1 冻结路由与页面清单**：`frontend/scripts/check-routes.mjs` 现有输出存档为 `frontend/e2e/fixtures/route-baseline.json`（路由数量与 redirect 集合），整改期间任何路由增删都要在此 diff。新增 `/market/data-sources` 时同步更新 `route-manifest.ts` + `route-elements.tsx` + `PLACEHOLDER_CONTRACT`。
- [ ] **0.2 新增 `frontend/e2e/responsive.spec.ts`（先写、先红）**：四档视口 `390 / 768 / 1280 / 1440` × 路由 `[/, /market, /market/global, /market/heatmap, /company/600540, /industry/pig, /me]`，逐条断言
  ```ts
  const { scrollWidth } = await page.evaluate(() => document.documentElement);
  const inner = await page.evaluate(() => window.innerWidth);
  expect(scrollWidth, `${route}@${width} 横向溢出`).toBeLessThanOrEqual(inner + 1);
  ```
  预期：`/market/global@390` **立即失败**（现测 749 > 390）。
- [ ] **0.3 新增 `frontend/e2e/nav.spec.ts` 断言（先红）**：`width ∈ {1280, 1440}` 下顶栏/侧栏的 10 个一级项 `toBeVisible()`。预期：`@1440` **立即失败**（3 项在 `...` 内）。
- [ ] **0.4 新增 `frontend/e2e/marketLayout.spec.ts`（先红）**：`/market` 的每个 Tab 下断言页面文本**不含**「数据版图」；断言「申万行业网格」标题在页面内出现 **1** 次；断言 `market-page-head` 的副标题文本 === Tab label 以 ` · ` 拼接。预期：三条**全部立即失败**。
  - **两条负向断言必须先等到请求终态**（`waitForLoadState("networkidle")` + 显式等待该 Tab 的目标卡片出现）再断言，否则在 react-query 的 loading 窗口内恒真、假绿；锚点放在文案上而非 `data-testid`。
  - 「出现 1 次」用结构选择器计数（如 `page.locator(".section-card__title", { hasText: "申万行业网格" })` 的 `toHaveCount(1)`），不得用全局 `getByText`。
- [ ] **0.5 扩展 `frontend/scripts/check-design-tokens.mjs`（先红）**：见 §5.1 的四组断言。预期：`--mag-*` / `--font-ui` / `--font-num` / `reduced-motion` / `focus-visible` 缺失 → 立即失败。
- [ ] **0.6 新增 `frontend/scripts/check-no-raw-iso.mjs`（先红）**：扫 `frontend/src/**/*.tsx` 的 JSX 文本节点与模板字符串，命中 `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}` 即失败。预期：`market-index` 立即失败。
- [ ] **0.7 基准记录（人工，非门禁）**：三档视口下各页 `scrollHeight` 快照写入本计划 §7 的"整改前"列，用于量化"去重复"收益。

**Phase 0 完成判据**：0.2–0.6 五条断言**全部处于红色**，且每条的失败原因与 §Why now 表逐条对应（证明断言抓的是真问题，不是空跑）。

---

## 3. Phase 1（P0）—— W1 导航与布局表达 + W2 去重复（2–3 天）

### W1 一级导航改左侧栏 + 补二级树

- [ ] **1.1 布局重构**：`frontend/src/app/layouts/MainLayout.tsx` 把 `NavTree mode="horizontal"` 从 `Header` 移入 `Layout` 的 `Sider`（复用既有 `frontend/src/features/nav/ModuleSideNav.tsx` 与 `NavTree`，不新建组件）。
  - 断点：`≥1280` 常驻 208px / `1024–1279` 折叠 64px 图标栏（hover 展开浮层）/ `<1024` 抽屉（`Drawer` + 汉堡按钮）。
  - `Header` 只保留：品牌 + 全局搜索 + 主题切换 + 用户菜单（IA §3.2 的"全局工具不进导航"）。
  - 命中依据：`NAV_PRESENTATION` 与 `NAV_ROOTS` 的合成逻辑**不动**，只换容器。
- [ ] **1.2 补二级树**：`frontend/src/app/router/route-manifest.ts` 新增 `MODULE_TREES: Record<string, NavNode[]>`（**纯数据**，与 `NAV_ROOTS` 同文件，保持"路由契约唯一真相"），节点取自 IA 计划 §3.3 表（资讯 4 / 行业 3 / 公司 9 / 宏观 7 / 情绪 10 / 策略 5 / 风险 4 / 交易 3）。`MainLayout` 用当前 pathname 前缀选树下发给 `ModuleSideNav`。
  - 子节点**暂时指向父路由**（`/news?tab=announcement` 形态），不做假路由——保证 `check-routes.mjs` 的占位契约不被破坏。
- [ ] **1.3 内容区适配**：`Content` 的 `maxWidth` 由 1400 改为 `1280`（表格式页面用 `maxWidth: "none"` 全宽），保证左栏 + 内容在 1440 下不挤压密集表格。

**W1 验收**：0.3 转绿（1280/1440 下 10 项可见）；新增断言 `<1024` 时汉堡按钮可打开抽屉且抽屉内含 10 项；`/news` 左树渲染 4 个节点。

### W2 信息去重

- [ ] **2.1 数据版图迁出工作台**：删 `frontend/src/pages/market/index.tsx:312` 的 `<DataCoverageMatrix withStats={false} />`；新建 `frontend/src/pages/market-data-sources/index.tsx`（复用既有 `DataCoverageMatrix withStats`），注册路由（依 §0-E 裁定）。
  - 工作台补偿：`market-page-head` 右侧加一行 `数据覆盖 12 个域 · TuShare/东财 · 查看来源 →`（链到新页），字号 12 / `--text-secondary`。
  - landing 的 `DataCoverage` 区块**保留**（那是它的正确位置）。
- [ ] **2.2 删 31 行业重复渲染**：`market/index.tsx:251` 删除 `<IndustryClassification />`。把它的两个有效信息并进瓦片：`SwIndustryGrid.tsx` 每格第二行由 `{count} 只` 改为 `{count} 只 · {children.length} 个二级`，`title` 属性保留；瓦片整体改为可点击 → `navigate('/market/industry/' + code)`（现在是纯展示 `div`，需改为 `<Link>` 或 `role="button"` + `onKeyDown`，保证键盘可达）。
  - 若 owner 认为 `IndustryClassification` 承载的"钻取入口"不可丢：改法二是保留列表、删除瓦片卡（二选一，**不得两个都在**）。
- [ ] **2.3 副标题同源**：`market/index.tsx` 的 `全球指数 · A股全景 · …` 字面量（`:298`）改由 `items.map(i => i.label).join(" · ")` 生成；若 §W2 后 Tab 收敛为 4 个（见 2.4），副标题自动跟随。
- [ ] **2.4 短线情绪拆页**：把 `SentimentTab`（现 `market/index.tsx` 内联）提升为 `frontend/src/pages/market-sentiment/index.tsx`（路由 `/market/sentiment`），`market` 的 Tab 回到契约 §4 的 4 个：指数总览 / A股全景 / 资金流向 / 数据面。市场页首屏给一条"结论条"（涨停 N / 炸板率 X% / 情绪档位），避免 6300px 的下滚。
  - 注：本条依赖 §0-A 裁定（A3 方案下 Tab 数量另议），**可在 W2 其余项之后单独排期**。

**W2 验收**：0.4 转绿（三 Tab 下无「数据版图」、31 行业只出现一次、副标题与 Tab 同源）；`/market` 页面 `scrollHeight` 下降 ≥35%（整改前：指数总览 1439 / A股全景 3141 / 数据面 1361）；E2E 既有 `marketDataFace.spec.ts`、`limitUpSentiment.spec.ts` 回归不挂（后者若有 DOM 假设需同步更新 selector）。

---

## 4. Phase 2（P1）—— W3 设计系统（2–3 天）

### 4.1 字体与数字 token

- [ ] **3.1 定义 token**（`frontend/src/app/styles/theme.css`，三块 `:root` / `[data-theme=light]` / `[data-theme=dark]` **都要有**，形状与颜色 token 一致）：
  ```css
  --font-ui:  "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --font-num: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  ```
- [ ] **3.2 字体文件**（依 §0-C）：`frontend/public/fonts/` 放子集 `noto-sans-sc-{400,500,600}.subset.woff2`（常用 3500 字 + 标点）与 `ibm-plex-mono-{400,600}.subset.woff2`（仅 `0-9 + - % . , 亿 万`）；`@font-face` 加 `font-display: swap` + `unicode-range`。子集化脚本入库 `frontend/scripts/subset-fonts.sh`（依赖 `pyftsubset`，**仅构建期**，不进运行时）。
- [ ] **3.3 AntD 接入**：`frontend/src/app/theme.ts` 的 `token` 增加 `fontFamily: 'var(--font-ui)'`。
- [ ] **3.4 数字统一**：`theme.css` 增 `.num { font-family: var(--font-num); font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }`；替换现有 20+ 处手写的 `fontVariantNumeric`（`grep -rn "fontVariantNumeric\|font-variant-numeric" frontend/src`）。**保留至少一处内联**作为回归哨兵？不——全部替换，靠门禁（§5.1-4）防止新增。
- [ ] **3.5 字号刻度**：在工作区把卡片标题统一为 `14px/600/--text-primary`（现 `SectionCard.css` 是 16/600，契约 §4 写的是 13/secondary——**以本计划为准，同步改契约**），数据行 `14`（数字）/ `13`（名称）/ `11`（代码）/ `12`（口径与 as_of）。

### 4.2 量级色阶与对比度

- [ ] **3.6 新增中性量级色阶**（不用 accent 编码"数量"）：
  ```css
  /* light */
  --mag-0:#eef1f6; --mag-1:#e2e7f0; --mag-2:#cfd7e5; --mag-3:#a9b5c9; --mag-4:#75859f;
  /* dark  */
  --mag-0:#1e222d; --mag-1:#262c3a; --mag-2:#313a4d; --mag-3:#3f4a61; --mag-4:#4c5872;
  ```
  约束（机器校验，见 §5.1-2）：light 每级 `--text-primary` 对比度 ≥4.5（实测上限 `#75859f` = **4.77:1**）；dark 每级 `--text-primary` ≥4.5（`#4c5872` = **4.80:1**）。文字**一律 `--text-primary`，不再有白字分支**。
- [ ] **3.7 改造 `SwIndustryGrid`**：`SwIndustryGrid.tsx` 删 `ALPHA_MIN/ALPHA_MAX/WHITE_TEXT_THRESHOLD` 与 `colors.accent` 叠 alpha 的写法，改为按 `count` 分位取 `--mag-0..4`（阈值取五分位，稳定且可解释）；`SwIndustryGrid.css` 的 `.sw-count { opacity: .75 }` 改为显式 `color: var(--text-secondary)`（opacity 会让对比度不可预测）。
- [ ] **3.8 同步 `theme.ts`**：`ThemePalette` 增 `mag: [string, string, string, string, string]`，ECharts 如需数量型配色从 `useTheme().colors.mag` 取（本轮无消费方则先不加，避免死代码——**按需**）。

### 4.3 日期口径

- [ ] **3.9 单一格式化入口**：`frontend/src/shared/ui/date.ts` 增 `formatAsOf(iso, now) => "10月9日收盘 · 3天前"`，`FreshnessNote` 与各卡的 `asof` 一律走它；`market-index/index.tsx` 的 `数据截至：2026-10-12T15:00:00Z` 改走同一入口。禁止任何组件直接渲染 ISO 串（门禁 §5.1-3）。

### 4.4 动效与焦点

- [ ] **3.10 全局降级动效**：`theme.css` 增
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
  }
  ```
  （覆盖 1.4s 无限 shimmer 与 `.sw-cell:hover { translateY(-2px) }`）
- [ ] **3.11 全局焦点环**：`theme.css` 增 `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`，保留 `market.css:79` 的既有局部规则（不冲突）。

**W3 验收**：0.5、0.6 转绿；`npm run check:design` 通过；`npx tsc --noEmit` 通过；暗色/浅色两套下瓦片文字对比度实测 ≥4.5（门禁自动算）；`grep -rn "fontVariantNumeric" frontend/src` 只余 `.num` 的定义处。

---

## 5. Phase 3（P1）—— W4 响应式（1–2 天）

### 5.1 断点契约（写进 `docs/design/landing-market-theme.md` 新增一节）

| 断点 | 布局 | 内容策略 |
|---|---|---|
| `≥1280` | 左栏 208 + 内容 1280 | 全功能 |
| `1024–1279` | 图标栏 64（hover 展开） | 卡片 4→3 列 |
| `768–1023` | 抽屉导航 | 卡片 2 列；表格包 `overflow-x:auto`；热力图折叠为 Top10 列表 |
| `<768` | 抽屉导航（汉堡） | **行情速览**：单列；隐藏 sparkline / 热力图 / K 线；只读 |

- [ ] **4.1 指数卡移动端**（`GlobalIndexCardView.tsx`）：`<768` 左列 `min-width: 104px`；名称 `white-space: normal` 允许 2 行（**名称优先级高于代码**，禁止把 `上证指数` 截成 `上证…`）；`CN 000001` 并入名称行做 10px 后缀；`index.spark.length > 2 && width ≥ 768` 才渲染 sparkline。
- [ ] **4.2 表格横向滚动容器**：`market-global/index.tsx` 与所有 AntD `Table` 外层统一包 `.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }`；`Table` 加 `scroll={{ x: 'max-content' }}`。修掉 `scrollWidth=749`。
- [ ] **4.3 热力图移动端**：`market-heatmap` 在 `<768` 不渲染 treemap（ECharts treemap 在小屏必然标签重叠），改渲染同数据的 Top10 列表（复用 `SwL3LimitUpBoard` 的行样式）。
- [ ] **4.4 首页移动端**：`landing.css` 检查 `.landing-container` 的 `max-width` 与内边距；目标 `<768` 无横向溢出（当前 `scrollWidth=392` vs 390，1–2px 溢出同样判失败）。

**W4 验收**：0.2 全绿（7 条路由 × 4 档视口）；`/market/global@390` 的 `scrollWidth ≤ 391`；`/market@390` 指数卡内的名称文本**可区分**——作用域限定到首张卡再断言（`page.locator(".ant-card").filter({ hasText: "3,797.24" }).first()` 内 `toContainText("上证指数")`），**不得**用全局 `getByText('上证指数')`（"全球指数"与"A股核心指数"两处同名，必 strict violation，见 §1「新断言必须绕开的两个已知坑」）。

---

## 6. Phase 4（P2）—— W5 文案与打磨（1–2 天）

- [ ] **5.1 品牌字标统一为 `StockBot`**：`MainLayout.tsx:116`、`MainLayout.tsx:139`、`login/index.tsx:82`、`index.html` `<title>`。加入 slop 扫描（命中 `Stock Bot` 即告警）。
- [ ] **5.2 landing 收敛**（依 §0-D 裁定 D2）：
  - 行情台 6 区块 → 保留 `实时市场脉搏` + `今日榜单`（**改 3 列密排**，见 5.3）+ `行业与资金`；`资金与情绪` / `日历` / `快讯` 移入 `/market`（`日历即将上线` 的空卡**直接删除**——第 4 屏放一个空盒子是最差的位置）。
  - 目标 `scrollHeight ≤ 4200`（约 4.7 屏；整改前 7051）。
- [ ] **5.3 `DataRow` 支持多列**：`shared/ui/DataRow.tsx` 加 `columns?: 1|2|3`（默认 1），`RankingMatrix` 用 3 列。根因：现在 10 行 × 64px = 640px，而每行只放"名称+代码 / 价格+涨跌幅"两个数据点，在 1400px 宽里两端相距 1300px（`DataRow.css` 的 `padding: 8px 0` + 两行文本撑出 64px）。3 列后同屏信息量 ×3，高度 ÷3。
- [ ] **5.4 删装饰性结构装置**：`CompactHero` 的 `A股行情` 药丸（无信息）；`ProductShowcase` 的 `01/02/03` —— 改为在途流程（原料 → 研判 → 信号 → 仓位）**或**去编号，二选一（`01/02/03` 只在内容真的是序列时才是信息）。
- [ ] **5.5 投研工作台信号历史 diff 化**：`features/industry-research/components/SignalPanel.tsx` 首条给完整 `触发依据`，后续只渲染变化项（`猪粮比 4.65 → 4.34`、`能繁环比第 3 个月回落`）；连续无变化折叠为 `连续 5 日无变化`。E2E 断言：第 2 条起不包含首条的 `触发依据` 全文。
- [ ] **5.6 两栏均衡**：`industry/workbench.tsx` 的左栏在 `猪周期阶段定位` 下方补 `价格 vs 成本剪刀差` 小图（数据已在 `PriceCostChart` 的同一 payload），消掉现测约 400px 空洞。
- [ ] **5.7 占位页升级为路线图**：新建 `frontend/src/shared/config/module-roadmap.ts`（每模块 3–5 个子模块 + 状态 `规划中/接入中`，来源 IA 计划 §3.3），`shared/ui/PlaceholderPage.tsx` 渲染为标签化骨架 + 一句"这个模块需要什么数据"。E2E 断言 8 条占位路由每条 ≥3 个 roadmap 项。
- [ ] **5.8 北向资金口径一致**：`DataCoverageMatrix` 的 `北向资金` 行由 `planned` 改为 `stale`（新枚举），文案 `已接入 · 上游停更`；`NorthboundCard` 顶部加同一徽章并灰化历史曲线。两处措辞由同一常量导出。

---

## 7. 量化目标与进度

| 指标 | 整改前 | 目标 | 度量 |
|---|---|---|---|
| 一级导航 1440 可见项 | 7 / 10 | 10 / 10 | 0.3 断言 |
| 一级导航 1280 可见项 | 6 / 10 | 10 / 10 | 0.3 断言 |
| `/market` 指数总览 `scrollHeight` | 1439 | ≤ 700 | 0.7 基准 |
| `/market` A股全景 `scrollHeight` | 3141 | ≤ 1900 | 0.7 基准 |
| `/market/global` 390px `scrollWidth` | 749 | ≤ 391 | 0.2 断言 |
| 瓦片文字最小对比度 | 2.10:1 | ≥ 4.5:1 | `check:design` |
| 首页 `scrollHeight` | 7051 | ≤ 4200（D2） | 0.7 基准 |
| `.css` 中 `:focus-visible` 规则数 | 1 | ≥ 1 全局 + 局部 | 0.5 断言 |
| 自绘 `fontVariantNumeric` 处数 | 22 | 1（`.num` 定义） | §5.1-4 |

**Phase 依赖**：`0 → (1, 2, 3) → 4 → 5`；W1 需 IA 的 Wave 0「IA 冻结」先落；W2-2.4 需 §0-A 裁定。

---

## 8. 门禁（新增项，全部可机械检测）

### 8.1 扩展 `frontend/scripts/check-design-tokens.mjs`

1. `--mag-0..4` 在三块（`:root` / light / dark）齐全，且与 `theme.ts` 的同名数组逐项一致（沿用现有"三块一致性"的校验写法）。
2. 对每级算对比度：light 与 `--text-primary`、dark 与 `--text-primary`，全部 ≥4.5；不足即失败并打印实测值。
3. `--font-ui` / `--font-num` 存在，且 `theme.ts` 的 `token.fontFamily` 引用 `--font-ui`。
4. `theme.css` 含 `@media (prefers-reduced-motion: reduce)` 与 `:focus-visible` 两块。
5. `frontend/src/**` 中 `fontVariantNumeric|font-variant-numeric` 的出现次数 ≤1（只允许 `.num` 定义处）。

### 8.2 新增 `frontend/scripts/check-no-raw-iso.mjs`

扫 `frontend/src/**/*.tsx`，命中 `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}` 即失败（日期一律走 `shared/ui/date.ts`）。

### 8.3 slop 扫描追加（`scripts/slop_scan.sh` 或 best-practices 映射表）

`Stock Bot`（应为 `StockBot`）· `landing-placeholder`（空占位卡）· `WHITE_TEXT_THRESHOLD`（用 accent 编码数量）。

### 8.4 CI 挂钩

`frontend/scripts/check-design-tokens.mjs` 已在 `npm run check:design`；把 `check-no-raw-iso.mjs` 一并挂进同一 npm script，并确认 `.github/workflows/ci.yml` 的 job id 变更同步到 `docs/testing/index.md`（`doc_gate.sh` 规则 5）。

---

## 9. 风险与回滚

| 风险 | 概率 | 影响 | 处置 |
|---|---|---|---|
| 左栏改造与 IA 分支的路由清单改动冲突 | 中 | 合并冲突 | W1 在 IA 的 Wave 0「IA 冻结」之后开工；先 rebase 到含冻结提交的分支 |
| 自托管字体子集体积超预算 / 缺字 | 中 | 首屏变慢或豆腐块 | §0-C 的 C2 降级路径（只用 `--font-num`，中文沿用系统栈）；子集脚本入库可复现，缺字由 CI 的字符集断言兜（可选） |
| 删 `IndustryClassification` 被判定为功能回退 | 低 | 需返工 | 2.2 给了"保留列表删瓦片"的二选一；删前用一次 `grep -rn "IndustryClassification"` 确认无测试锚定（现有 e2e 未锚定该类名） |
| 数据版图新页无人访问，信任装置弱化 | 中 | 产品判断 | 工作台补偿一行入口 + landing 保留完整矩阵；§0-E 由 owner 定位置 |
| 移动端"行情速览"被理解为功能缩水 | 中 | 预期管理 | §0-B 提前拍板，并在页面显式提示"移动端为速览，完整功能请用桌面端"（而不是静默隐藏） |
| 实盘数据波动导致断言不稳 | 中 | 门禁 flaky | 所有新断言只锚**结构/尺寸/文本存在性**，不锚数值；已明确禁用像素快照 |

**回滚**：每条工作流独立可回滚（W1 单文件为主、W2 为删除+新增一页、W3 为 token 与两个组件、W4 为 CSS 与两个组件、W5 为展示层）。按 Phase 提交，任一 Phase 出问题只 revert 该 Phase。

---

## 10. 验收清单（全部完成才算 done）

- [ ] `cd frontend && npx tsc --noEmit` 通过
- [ ] `cd frontend && npm run build` 通过
- [ ] `cd frontend && npm run check:design` 通过（含 §8.1 五组新断言）
- [ ] `cd frontend && node scripts/check-no-raw-iso.mjs` 通过
- [ ] `cd frontend && node scripts/check-routes.mjs` 通过（含新增 `/market/data-sources`、`/market/sentiment` 的登记）
- [ ] `cd frontend && npx playwright test` 全绿（含 `responsive.spec.ts` / `nav.spec.ts` / `marketLayout.spec.ts` / `placeholder.spec.ts` 四个新文件，以及 `darkmode.spec.ts` 的暗色无浅底块扩展）
- [ ] `bash scripts/self_review.sh --full` 通过
- [ ] `bash scripts/doc_gate.sh` 通过
- [ ] `docs/design/landing-market-theme.md` 已补 §断点契约与 §量级色阶（与本计划 §4.1/§5.1 同源）
- [ ] `docs/Changelog.md` 已补记本次整改
- [ ] 本文件头部 `status` 改为 `completed`，`plans/index.md` 同步
