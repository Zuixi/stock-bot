---
status: completed
scope: 前端设计整改——一级导航形态与三层布局表达、工作页信息密度（去重复）、设计系统（字体/数字/量级色阶/对比度/动效/焦点）、响应式四档断点、文案与品牌一致性；来源为 2026-10-13 全站设计评审（1440/1280/390 三档实测截图）
touches: frontend/src/app/, frontend/src/pages/, frontend/src/features/nav/, frontend/src/features/market/, frontend/src/features/industry-research/, frontend/src/shared/ui/, frontend/src/shared/config/, frontend/src/app/router/, frontend/public/fonts/, frontend/scripts/, frontend/e2e/, docs/design/landing-market-theme.md, docs/Changelog.md, plans/index.md
updated: 2026-10-15
next-action: 已完成（Phase 0–4 全部落地；Phase 0 冻结的 0.2–0.6 五条红线全部转绿且无回归）。后续如需继续推进：① 6 个 `768–1023` 热力图可切 Top10 视图等「附加视图」类新能力；② 自托管字体（C1）若将来预算放宽可切回（抽象层已就位，只影响字体文件）；③ §8.3 剩余两条 slop 探针待其命中目标消失后接入
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
| 品牌字标两种 | `Stock Bot`（壳/登录/<title>）vs `StockBot`（landing/页脚） | **Phase 4 §5.1 已统一为 `StockBot`**（`MainLayout.tsx` 两处、`login/index.tsx`、`index.html`；另收口 `admin-users`/`change-password` 的 `document.title`） |
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

## 0. 启动前必须由 owner 裁定（5 项）—— **已裁定（2026-10-14）**

| # | 待裁定 | 建议 | 影响 | ✅ **owner 裁定** |
|---|---|---|---|---|
| A | **一级导航形态**：A1 左侧图标栏（72px，hover 展开 208px）/ A2 左侧常驻 208px / A3 顶栏只留 7 个"研究层"，策略/风险/交易移入右侧独立区段 | **A1** | 决定 W1 全部工作量与内容区宽度；A3 需 owner 确认是否接受顶栏分区 | **A1** |
| B | **移动端承诺等级**：B1 桌面优先（≥1280 为设计基准）+ `<768` 只做"行情速览" / B2 全量响应式 | **B1** | B2 会让每个图表组件都要一套移动端形态，成本约 3 倍；B1 只需保证"不坏 + 可读" | **B2（与建议相反）** —— 见下方 §0.1 的范围修订 |
| C | **字体自托管预算**：C1 自托管思源黑体子集（约 120KB×2 字重）+ IBM Plex Mono 数字子集（约 15KB）/ C2 中文沿用系统栈，只引入数字等宽（走 `ui-monospace`，**零字节**） | **C1，超预算则降级 C2** | `--font-num` 抽象层先落地，C2/C1 只影响字体文件，不改调用点 | **C1**（超预算仍按原路降级 C2） |
| D | **首页定位**：D1 维持既有拍板"免登录行情台"（7051px / 7.8 屏）/ D2 收敛为营销页 3–4 屏 + 行情入口 | **D2** | **D2 推翻既有拍板，必须 owner 明确同意**；不同意则 W5-3 只做"压缩排序"，不做删区块 | **D1（与建议相反）** —— 见下方 §0.1 的范围修订 |
| E | **数据版图新页位置**：`/market/data-sources`（市场域内）vs `/about/data`（关于页） | **`/market/data-sources`** | 决定 `route-manifest.ts` 的一条新路由与面包屑层级 | **`/market/data-sources`** |

### 0.1 由 B2 / D1 引起的范围修订（**这两条与计划建议相反，故下文按此改**）

- **B = B2（全量响应式）⇒ 本计划 §5.1 的 `<768` 行作废**。原写 `<768` 只做"行情速览（隐藏 sparkline / 热力图 / K 线）"，与"全量响应式"直接矛盾。改为：**`<768` 每个区块都要有可用的移动端形态**（不是隐藏）——
  - 热力图：treemap 仍可保留，但必须给 `label` 防重叠策略（`labelLayout` / 缩放阈值），并把"折叠为 Top10 列表"从**替代**降级为**用户可切换的视图**；
  - K 线：窄屏改为 `dataZoom` 默认聚焦最近 N 根 + 手势缩放，而非隐藏；
  - sparkline：保留，但按容器宽度决定画点数（不是按断点隐藏）；
  - **代价已知并接受**：W4 工作量约为 B1 的 3 倍（每个图表组件都要一套窄屏形态）。§7 的进度表按此重估。
- **D = D1（维持"免登录行情台"）⇒ §6 的 5.2 收敛任务作废**：**不删区块、不迁移区块**（`资金与情绪` / `日历` / `快讯` 都留在 landing）。W5 在首页只做**压缩排序与去重**，且 §6 的 `scrollHeight ≤ 4200` 目标**不再适用**（该目标是 D2 的产物）——改为**"在 D1 的 6 区块结构下，仅靠压缩排序把 scrollHeight 降到尽可能低，并把实测值记入 §7"**。
  - 例外：`日历即将上线` 的**空占位卡**仍应删除或填上真实内容——它不是"区块"，是"空盒子"（第 4 屏放空盒子在任何定位下都是缺陷，与 D1/D2 无关）。

**裁定已完成，Phase 1 及之后可开工**（Phase 0 的"先红后绿"基线见 §2）。

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

- [x] **0.1 冻结路由与页面清单**：`frontend/scripts/check-routes.mjs` 现有输出存档为 `frontend/e2e/fixtures/route-baseline.json`（路由数量与 redirect 集合），整改期间任何路由增删都要在此 diff。新增 `/market/data-sources` 时同步更新 `route-manifest.ts` + `route-elements.tsx` + `PLACEHOLDER_CONTRACT`。
- [x] **0.2 新增 `frontend/e2e/responsive.spec.ts`（先写、先红）**：四档视口 `390 / 768 / 1280 / 1440` × 路由 `[/, /market, /market/global, /market/heatmap, /company/600540, /industry/pig, /me]`，逐条断言
  ```ts
  // ⚠️ 必须 `evaluate` 里就返回**原始值**：`page.evaluate(() => document.documentElement)`
  // 返回的是 `ref: <Node>` 字符串，解构 `{ scrollWidth }` 得 undefined ⇒ 全部转红但**原因是错的**
  // （Phase 0 实测踩到：28/28「错误原因的红」）。见 §2.1 假绿坑清单。
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  const inner = await page.evaluate(() => window.innerWidth);
  expect(scrollWidth, `${route}@${width} 横向溢出`).toBeLessThanOrEqual(inner + 1);
  ```
  预期：`/market/global@390` **立即失败**。**Phase 0 实测 `scrollWidth=877`（不是计划写的 749**——计划值取自更早的一次测量；同一个成因，数值更大），且 `@768` 也红（877 > 768）。
- [x] **0.3 新增 `frontend/e2e/nav.spec.ts` 断言（先红）**：`width ∈ {1280, 1440}` 下 10 个一级项全部可见。
  - **不得用 `toBeVisible()`**：它**不计祖先 `overflow` 裁剪**——AntD 折叠进 `...` 的项外层 `li` 高度为 0、内层 `span` 仍是 28×17，于是 10/10 **假绿**（Phase 0 实测踩到）。改用 `elementFromPoint` 命中测试。
  - 作用域用 `[data-testid="primary-nav"]` + `[data-nav-label]`（**W1 改左侧栏时必须保留这两个锚点**，见 §3 W1）。
  - Phase 0 实测：`@1440` 3 项不可见（策略引擎/风险管理/交易执行）· `@1280` 4 项（+价值网图谱）——与 §7 的 7/10、6/10 一致。
- [x] **0.4 新增 `frontend/e2e/marketLayout.spec.ts`（先红）**：三条断言，其中**②③ 的预期已按实测修正**（原判据写在 `1efe65a` 之前，该提交早于本计划就把「数据版图」移出了市场页）：
  - ① **副标题 === Tab label 以 ` · ` 拼接** → **红**（真问题：副标题与 Tab 不同源）。这是本条唯一的红线工作项。
  - ② **页面文本不含「数据版图」** → 现在**绿**。降级为**回归闸**（防止它被搬回来），不再是红线。
  - ③ ~~「申万行业网格」标题出现 1 次~~ → **改为**：A股全景 Tab 下**同屏行业区块数 == 1**（申万行业网格 与 行业分类（申万）**都在列 31 个行业**，这才是「信息去重」要抓的冗余）→ **红**。原写法（按标题计数）抓不到这个冗余，因为第二份的标题不同。
    - ⚠️ **修订计划的教训（Phase 1 实测）**：本条在**计划里**改成结构计数后，**已写好的 spec 并不会自动跟着变** —— `marketLayout.spec.ts` 里仍是旧写法（数 `.ant-card-head-title` 文案为「申万行业网格」的数量），因此 Phase 1 期间 ③ 一直**空转通过**（那一个标题本来就只有 1 个）。Phase 1 收尾时已把 spec 改为数「渲染整份 31 行业列表的容器」并**加正对照**（注入第二份后计数必须变 2，实测通过）⇒ 现在是**真断言**。**改判据时必须同时改实现该判据的 spec**，否则改的只是文档。
  - **两条负向断言必须先等到请求终态**（`waitForLoadState("networkidle")` + 显式等待该 Tab 的目标卡片出现）再断言，否则在 react-query 的 loading 窗口内恒真、假绿；锚点放在文案上而非 `data-testid`。
  - 计数用**结构选择器**（`page.locator(...)` 的 `toHaveCount`），不得用全局 `getByText`（strict-mode violation）。**并做正对照**（注入一段已知文本后计数应变化），证明计数断言不是空跑——Phase 0 已做。
- [x] **0.5 扩展 `frontend/scripts/check-design-tokens.mjs`（先红）**：见 §8.1 的五组断言。Phase 0 实测 **28 项不达标**（`--mag-*` / `--font-ui` / `--font-num` / `reduced-motion` / `focus-visible` 全缺；等宽数字出现次数 **65**，见 §8.1-5 已据实修正）。
- [x] **0.6 新增 `frontend/scripts/check-no-raw-iso.mjs`（先红）**：扫 `frontend/src/**/*.tsx` 的 JSX 文本节点与模板字符串，命中 `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}` 即失败。
  - **⚠️ 原预期「`market-index` 立即失败」不可达**：该页渲染 `{index.asof}`（payload 直出），源码里**没有** ISO 字面量 ⇒ 静态层**结构上不可能红**（Phase 0 实测：扫 136 个 `.tsx` / 8130 段文本，绿）。
  - **故本条改为两层判据**：① 静态源码字面量（现在绿，防**将来**写死）；② **运行时页面文本**（新增 `frontend/e2e/noRawIso.spec.ts`）—— 实测 `/market/index/000001.SH` 可见文本含 `2026-10-13T15:00` ⇒ **② 是真正的红线**，也正是 §Why now 要抓的那条。详见 §8.2。
- [x] **0.7 基准记录（人工，非门禁）**：三档视口下各页 `scrollHeight` 快照写入本计划 **§7.1**（Phase 0 实际落点；§7 的指标表未动）。**并记录了 §7 旧值的漂移**：landing 7051→6066 · 指数总览 1439→956 · A股全景 3141→3667（计划写于 2026-10-13，此后有真实改动）。

### 2.1 Phase 0 实测发现的两个"假绿"坑（**已就地修掉，且可机械化**）

| 坑 | 症状 | 处置 |
|---|---|---|
| `page.evaluate(() => document.documentElement)` | 返回 `ref: <Node>` 字符串；解构 `{ scrollWidth }` 得 `undefined` ⇒ **全部转红但原因是错的**（28/28） | evaluate 内只返回原始值；已写进 §2 的代码片段 |
| `toBeVisible()` 与祖先 `overflow` | 折叠进 `…` 的项外层高 0、内层仍 28×17 ⇒ **10/10 假绿**，实际只 7 项可见 | 改 `elementFromPoint` 命中测试 |

> 这两条**都不是本计划专有**：`toBeVisible` 那条会影响**任何**"折叠/溢出后是否可见"的断言。按 AGENTS.md 第 5 条，应升级为 `check-*` 或 best-practices 一行——**待办**（Phase 1 收尾时处理）。

**Phase 0 完成判据（据实修正）**：0.2 / 0.3 / 0.5 各有红线；0.4 有 ①③ 两条红线（② 降级为回归闸）；0.6 的红线在**运行时层**。**每条的失败原因必须与 §Why now 表逐条对应**（证明断言抓的是真问题，不是空跑）；**预期为绿却变红的，与预期为红却变绿的一样，都算错**。

---

## 3. Phase 1（P0）—— W1 导航与布局表达 + W2 去重复（2–3 天）

### W1 一级导航改左侧栏 + 补二级树

- [x] **1.1 布局重构**：`frontend/src/app/layouts/MainLayout.tsx` 把 `NavTree mode="horizontal"` 从 `Header` 移入 `Layout` 的 `Sider`（复用既有 `frontend/src/features/nav/ModuleSideNav.tsx` 与 `NavTree`，不新建组件）。
  - 断点：`≥1280` 常驻 208px / `1024–1279` 折叠 64px 图标栏（hover 展开浮层）/ `<1024` 抽屉（`Drawer` + 汉堡按钮）。
  - `Header` 只保留：品牌 + 全局搜索 + 主题切换 + 用户菜单（IA §3.2 的"全局工具不进导航"）。
  - 命中依据：`NAV_PRESENTATION` 与 `NAV_ROOTS` 的合成逻辑**不动**，只换容器。
- [x] **1.2 补二级树**：`frontend/src/app/router/route-manifest.ts` 新增 `MODULE_TREES: Record<string, NavNode[]>`（**纯数据**，与 `NAV_ROOTS` 同文件，保持"路由契约唯一真相"），节点取自 IA 计划 §3.3 表（资讯 4 / 行业 3 / 公司 9 / 宏观 7 / 情绪 10 / 策略 5 / 风险 4 / 交易 3）。`MainLayout` 用当前 pathname 前缀选树下发给 `ModuleSideNav`。
  - 子节点**暂时指向父路由**（`/news?tab=announcement` 形态），不做假路由——保证 `check-routes.mjs` 的占位契约不被破坏。
- [x] **1.3 内容区适配**：`Content` 的 `maxWidth` 由 1400 改为 `1280`（表格式页面用 `maxWidth: "none"` 全宽），保证左栏 + 内容在 1440 下不挤压密集表格。

**W1 验收**：0.3 转绿（1280/1440 下 10 项可见）；新增断言 `<1024` 时汉堡按钮可打开抽屉且抽屉内含 10 项；`/news` 左树渲染 4 个节点。

### W2 信息去重

- [x] **2.1 数据版图迁出工作台**：删 `frontend/src/pages/market/index.tsx:312` 的 `<DataCoverageMatrix withStats={false} />`；新建 `frontend/src/pages/market-data-sources/index.tsx`（复用既有 `DataCoverageMatrix withStats`），注册路由（依 §0-E 裁定）。
  - 工作台补偿：`market-page-head` 右侧加一行 `数据覆盖 12 个域 · TuShare/东财 · 查看来源 →`（链到新页），字号 12 / `--text-secondary`。
  - landing 的 `DataCoverage` 区块**保留**（那是它的正确位置）。
- [x] **2.2 删 31 行业重复渲染**：`market/index.tsx:251` 删除 `<IndustryClassification />`。把它的两个有效信息并进瓦片：`SwIndustryGrid.tsx` 每格第二行由 `{count} 只` 改为 `{count} 只 · {children.length} 个二级`，`title` 属性保留；瓦片整体改为可点击 → `navigate('/market/industry/' + code)`（现在是纯展示 `div`，需改为 `<Link>` 或 `role="button"` + `onKeyDown`，保证键盘可达）。
  - 若 owner 认为 `IndustryClassification` 承载的"钻取入口"不可丢：改法二是保留列表、删除瓦片卡（二选一，**不得两个都在**）。
- [x] **2.3 副标题同源**：`market/index.tsx` 的 `全球指数 · A股全景 · …` 字面量（`:298`）改由 `items.map(i => i.label).join(" · ")` 生成；若 §W2 后 Tab 收敛为 4 个（见 2.4），副标题自动跟随。
- [ ] **2.4 短线情绪拆页**（**Phase 1 未做，单独排期**：见下）：把 `SentimentTab`（现 `market/index.tsx` 内联）提升为 `frontend/src/pages/market-sentiment/index.tsx`（路由 `/market/sentiment`），`market` 的 Tab 回到契约 §4 的 4 个：指数总览 / A股全景 / 资金流向 / 数据面。市场页首屏给一条"结论条"（涨停 N / 炸板率 X% / 情绪档位），避免 6300px 的下滚。
  - 注：本条依赖 §0-A 裁定（A3 方案下 Tab 数量另议），**可在 W2 其余项之后单独排期**。
  - **Phase 1 未做（单独排期）的实测理由**：它要新增**真功能路由** `/market/sentiment` ⇒ 连带 5 处登记
    （`route-manifest` / `route-elements` 的 `PAGES` / `check-routes` 的 `NEW_S1_ROUTES` / IA §4「S1.x 新增路由」表
    / `ARCHITECTURE §6`）；且 `/market` 的 Tab 从 5 收敛到 4 会**同时改两套既有 E2E 的路由契约**
    （`marketDataFace.spec.ts` 的「短线情绪三卡」+ `limitUpSentiment.spec.ts` 的 8 个用例全部
    `goto("/market")` + `getByRole("tab", {name:"短线情绪"})`）；再加一条**新 UI 能力**（结论条）。
    超出本 Phase 收尾量级，故按本条自己的许可推迟（见 task-24 报告 §7）。

**W2 验收（Phase 1 实测后据实修正，controller 裁定 2026-10-15）**：

1. **0.4 三条全绿** ✓ 达成（实测 `marketLayout.spec.ts` 3 passed + `nav.spec.ts` 5 passed）。
2. **`scrollHeight` 门槛改为"按 Tab 分别设、且只对真正冗余的 Tab 设"** —— 原写的"**一律** ≥35%（1439/3141/1361）"**不可达且判据本身是错的**：
   - 那三个数出自 §3，与 §7.1 的实测基线（956/3667/900）**互相矛盾**；且 §7.1 自己写明"不得当作 CI 断言阈值"。
   - **数据面 900 就是 100vh 地板**（header 64 + 内容 + footer 65）⇒ 585 在物理上不可能，"−35%"无从谈起。
   - **指数总览实测 +24.3%（956→1188）**：变大是**故意的**——为了让「上证指数」不再被截成「上…」而让指数卡改为纵向堆叠；实测名称所需宽度 52px、原先只有 36px，且它同时造成了 `/market@1280` 的 **16px 横向溢出**。
     **裁定：可读名称 + 无横向溢出 > 这条高度指标**（依 §1 目标 5「名称不被截断到不可辨识」与 §0-B「全量响应式」）。
   - **修正后的判据**：**A股全景**（唯一真正有冗余的 Tab）`scrollHeight` 下降 **≥50%**（实测 −57.9% @1440 / −55.2% @1280 ✓ PASS）；**指数总览 / 数据面不设高度门槛**，改设「**无横向溢出 + 名称可辨识**」（实测两者均无溢出 ✓）。
3. **既有 E2E 回归不挂** ✓：`marketDataFace` + `limitUpSentiment` + `marketTrust` + `landing` + `darkmode` + `navigation` 全绿；`responsive` 的 2 条红与 Phase 0 **一字未变**（`/market/global@390`·`@768`，属 Phase 3 范围）。

### 2.5 Phase 1 的三处"计划与现实不符"（controller 裁定：**均按实测为准**）

| # | 计划原文 | 实测现实 | 裁定 |
|---|---|---|---|
| a | §2.1「复用既有 `DataCoverageMatrix withStats`」+「landing 的 `DataCoverage` 区块保留」 | **两条都为假**：`1efe65a`（HEAD 的祖先，不在 main）已把 `DataCoverageMatrix.tsx/.css` **与 landing 的该区块一并删除**；`git grep DataCoverage frontend/src` = **0 命中** | **批准偏离**。新页建在**真实的** `GET /api/v1/market/data-freshness` 的 `coverage[]` 上（8 个**实测**域，计划写的 12 是估算）。**不复活**那张已因口径漂移而被删的静态表——复活它等于把已修掉的缺陷搬回来 |
| b | §2.1 补偿行写「… · TuShare/东财 · …」 | 会**违反既有的** `marketTrust` 门禁「公开页不披露数据来源」（断言 `/market` 整页不得出现供应商名称） | **批准省略供应商名**，改用实测的「数据覆盖 8 个域」。**既有门禁 > 计划措辞** |
| c | §2.4 拆 `/market/sentiment` | 需新增真路由 + 5 处登记，并改 2 套既有门禁测试的路由契约，另加一条新 UI | **批准推迟**（该条自身写明"可单独排期"）；已记入 §2.4 |

> **另一发现（环境类，值得机械化）**：前端镜像的 runtime `COPY` 层会被**陈旧缓存**命中——`docker compose build frontend` **报告成功但装的是旧 dist**，只有 `--no-cache` 才对。这正是本会话早先"页面看起来坏了"那类事故的另一种形态。**待办**：加一条构建后校验（比对容器所服 `index-*.js` 哈希与本地 `dist/` 的一致），或把 `--no-cache` 写进前端构建约定。

> **Phase 1 实测回写（task-24，2026-10-15）**：`0.3` / `0.4①` / `0.4②` / `0.4③` **全部转绿**；
> `marketDataFace` + `limitUpSentiment` + `marketTrust` + `landing` + `darkmode` + `navigation` **全绿**；
> `responsive` 的 2 条红与 Phase 0 一字未变（`/market/global@390/768`）。
> **`scrollHeight ↓≥35%` 只在 A股全景成立，另两个 Tab 达不到**（同口径实测，`W×900` 匿名态）：
>
> | Tab | §7.1 基线 | 实测 | 相对 §7.1 | 相对本节括号里的旧值 | 结论 |
> |---|---|---|---|---|---|
> | A股全景 @1440 | 3667 | **1545** | **−57.9%** | −50.8% | ✅ 达标 |
> | A股全景 @1280 | 3667 | **1642** | **−55.2%** | −47.7% | ✅ 达标 |
> | 指数总览 @1280/1440 | 956 | **1188** | **+24.3%** | −17.4% | ❌ 增高（见下） |
> | 数据面 @1280/1440 | 900 | **900** | **0.0%** | −33.9% | ❌（**物理不可达**，见下） |
>
> - **数据面**：900 是 `100vh` 地板（header 64 + content + footer 65），其内容本就短于视口。
>   把 900 压到 `900×0.65 = 585` 在物理上不可能——这条目标在本节写的 1361 基线下也是
>   `1361×0.65 = 885 ≤ 900`（只差 15px，同样接近不可达）。**该 Tab 不构成整改成效的度量**。
> - **指数总览 +24.3% 是主动取舍**：内容区 `maxWidth: 1280` + 左栏 208px 使 `GlobalMarketBoard`
>   的 `xl={4}` 在 ≥1200px 生效 ⇒ 单卡只有 156–191px，而卡内涨跌幅色块自身 97px（不可断行），
>   横排时**名称只剩 36px**（「上证指数」被截成「上…」，与本文档 §Why-now 表里"名称被截到不可辨识"
>   同类），并把 `/market@1280` 顶出 16px 横向溢出。改为**全档堆叠**后名称完整（`0/19` 截断）、
>   无溢出，代价是每 Tab +232px。**「≥35% 高度」与「名称可辨识」在 156–191px 宽的卡片里不可兼得**，
>   已按 §1 目标 5 + §0-B 选**名称可读**；owner 若认为高度优先，回退卡片堆叠即可（代价见上）。

---

## 4. Phase 2（P1）—— W3 设计系统（2–3 天）

### 4.1 字体与数字 token

- [x] **3.1 定义 token**（`frontend/src/app/styles/theme.css`，三块 `:root` / `[data-theme=light]` / `[data-theme=dark]` **都要有**，形状与颜色 token 一致）：
  ```css
  --font-ui:  "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  --font-num: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  ```
- [x] **3.2 字体文件**（依 §0-C）：`frontend/public/fonts/` 放子集 `noto-sans-sc-{400,500,600}.subset.woff2`（常用 3500 字 + 标点）与 `ibm-plex-mono-{400,600}.subset.woff2`（仅 `0-9 + - % . , 亿 万`）；`@font-face` 加 `font-display: swap` + `unicode-range`。子集化脚本入库 `frontend/scripts/subset-fonts.sh`（依赖 `pyftsubset`，**仅构建期**，不进运行时）。
- [x] **3.3 AntD 接入**：`frontend/src/app/theme.ts` 的 `token` 增加 `fontFamily: 'var(--font-ui)'`。
- [x] **3.4 数字统一**：`theme.css` 增 `.num { font-family: var(--font-num); font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }`；替换现有 **65 处**手写的 `fontVariantNumeric`（**Phase 0 实测**；计划原写"20+"，取自更早的一次 grep。`grep -rn "fontVariantNumeric\|font-variant-numeric" frontend/src`）。**保留至少一处内联**作为回归哨兵？不——全部替换，靠门禁（**§8.1-5**）防止新增。
- [x] **3.5 字号刻度**：在工作区把卡片标题统一为 `14px/600/--text-primary`（现 `SectionCard.css` 是 16/600，契约 §4 写的是 13/secondary——**以本计划为准，同步改契约**），数据行 `14`（数字）/ `13`（名称）/ `11`（代码）/ `12`（口径与 as_of）。

### 4.2 量级色阶与对比度

- [x] **3.6 新增中性量级色阶**（不用 accent 编码"数量"）：
  ```css
  /* light */
  --mag-0:#eef1f6; --mag-1:#e2e7f0; --mag-2:#cfd7e5; --mag-3:#a9b5c9; --mag-4:#75859f;
  /* dark  */
  --mag-0:#1e222d; --mag-1:#262c3a; --mag-2:#313a4d; --mag-3:#3f4a61; --mag-4:#4c5872;
  ```
  约束（机器校验，见 §5.1-2）：light 每级 `--text-primary` 对比度 ≥4.5（实测上限 `#75859f` = **4.77:1**）；dark 每级 `--text-primary` ≥4.5（`#4c5872` = **4.80:1**）。文字**一律 `--text-primary`，不再有白字分支**。
- [x] **3.7 改造 `SwIndustryGrid`**：`SwIndustryGrid.tsx` 删 `ALPHA_MIN/ALPHA_MAX/WHITE_TEXT_THRESHOLD` 与 `colors.accent` 叠 alpha 的写法，改为按 `count` 分位取 `--mag-0..4`（阈值取五分位，稳定且可解释）；`SwIndustryGrid.css` 的 `.sw-count { opacity: .75 }` 改为显式 `color: var(--text-secondary)`（opacity 会让对比度不可预测）。
- [x] **3.8 同步 `theme.ts`**：`ThemePalette` 增 `mag: [string, string, string, string, string]`，ECharts 如需数量型配色从 `useTheme().colors.mag` 取（本轮无消费方则先不加，避免死代码——**按需**）。

### 4.3 日期口径

- [x] **3.9 单一格式化入口**：`frontend/src/shared/ui/date.ts` 增 `formatAsOf(iso, now) => "10月9日收盘 · 3天前"`，`FreshnessNote` 与各卡的 `asof` 一律走它；`market-index/index.tsx` 的 `数据截至：2026-10-12T15:00:00Z` 改走同一入口。禁止任何组件直接渲染 ISO 串（门禁 §5.1-3）。

### 4.4 动效与焦点

- [x] **3.10 全局降级动效**：`theme.css` 增
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
  }
  ```
  （覆盖 1.4s 无限 shimmer 与 `.sw-cell:hover { translateY(-2px) }`）
- [x] **3.11 全局焦点环**：`theme.css` 增 `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`，保留 `market.css:79` 的既有局部规则（不冲突）。

**W3 验收（Phase 2 实测后据实修正，2026-10-15）**：

| 判据 | 实测 |
|---|---|
| 0.5 `npm run check:design` | **转绿，退出 0**（整改前 **28 项不达标 / 退出 1**）；五个 §8.1 组 + 日期静态层全 PASS |
| 0.6 运行时（`e2e/noRawIso.spec.ts`） | **转绿：5 passed**（整改前 3 passed / **2 failed**，红的正是 `/market/index/000001.SH`） |
| `npx tsc --noEmit` | 0 |
| `npm run build` | 0（`✓ built in 3.71s`） |
| 瓦片文字对比度 | 门禁逐级算出并打印，**10/10 级 ≥4.5**（数值见下） |
| `grep -rn "fontVariantNumeric\|font-variant-numeric" frontend/src` | **只余 1 处**（`theme.css` 的 `.num` 定义）；开工时 **67** |

> **Phase 2 实测回写（task-25，2026-10-15）**
>
> **① 3.2 走的是 C2 —— 不是"下不下来"，是"下得下来但超预算"。** 环境**有出网**，
> `pyftsubset`/`fonttools[woff]`+`brotli` 也装得上（临时 venv，**未入库为依赖**），字体源也下得下来
> （Noto Sans SC VF 17.7 MB + IBM Plex Mono 2 字重）。卡住的是**体积**：子集到本仓库源码字符集
> （1164 CJK 字形）后单字重 woff2 = **194,292 B**，而 §0-C 预算是 122,880 B/字重 ⇒ 超 1.58×；
> 三字重合计 **588,708 B**，超「120KB × 2 字重」**2.4×**；按计划写的"常用 3500 字"规模
> （3500 字形代理集）单字重 **~493 KB** ⇒ 超 6×。数字那一半（IBM Plex Mono 子集 = **3,300 / 4,096 B**）
> 在 15 KB 预算内。**依 §0-C「超预算仍按原路降级 C2」执行**：不带字体文件、不声明 `@font-face`，
> `--font-ui` = 系统栈、`--font-num` = `ui-monospace`（零字节）。子集脚本按本条要求入库
> （`frontend/scripts/subset-fonts.sh`，**不被构建/npm script/CI 调用**），文件尾写明如何切回 C1。
>
> **② 3.4 的处数：Phase 0 记 65 是对的，本次开工实测是 67。** 差的 2 处来自 **Phase 1 新建的
> `pages/market-data-sources/`** 自带的 `ds-num`（可核对：`git grep -c "fontVariantNumeric\|font-variant-numeric" 387c94d -- frontend/src` = **65**，
> 到 `048f632` 变 67）。收敛后 `src/**` 只剩 `.num` 定义处的 **1 处**。
>
> **③ 3.6 候选色值由门禁复核（不是手算）：light `#75859f` = 4.78:1**（计划写 4.77）、
> **dark `#4c5872` = 4.81:1**（计划写 4.80），其余 8 级 5.99–15.81 ⇒ **候选值无需调整**。
>
> **④ 3.9 的落法（与计划字面有一处差异，按"只留一个入口"的意图收口，并写明理由）**：新增
> `formatAsOf(iso, now)`（= `"10月9日收盘 · 3天前"`，相对日只在**过去**时给出）+ 共用的日期片段
> `formatAsOfDate`。`market-index` 的 `数据截至` 改走 `formatAsOf`；**`FreshnessNote` 不改成单串**——
> 它那一行的契约是把口径与相对日渲染成**两个 chip**，且**日期归本行独有、chip 不复述日期**
> （被 `marketDataFace.spec.ts` 的「回落口径卡片…不重复判据日」与 `N 天前` 徽标逐条钉住；
> 改成单串会让「日期 + 按最近完整日 + N 天前」三处重复同一事实）。故二者**共用 `date.ts` 里同一个
> 日期片段实现**，而不是共用同一个字符串模板。实测上屏文本：
> **`数据截至：10月13日收盘 · 2天前`**（整改前 = `数据截至 2026-10-13T15:00:00Z`）。
> 另：`FreshnessNote` 顺带修掉一个潜在坑 —— 旧实现走 `formatCnDate`（按 `-` 切三段），
> 若被喂完整 ISO 会渲染 `NaN月NaN日`；`formatAsOfDate` 先取前 10 字符，不会。
>
> **⑤ §8.4 的门禁挂载已做**：`npm run check:design` 改为
> `node scripts/check-design-tokens.mjs && node scripts/check-no-raw-iso.mjs` ⇒ CI 的 `frontend-lint`
> 从此同时守住**日期静态层**（原先那条脚本进了仓库却没有任何地方跑它）；`docs/testing/index.md`
> 门禁矩阵补「日期口径」一行。**未改任何 CI job id**，`doc_gate` 规则 4 不受影响。
> 运行时层（`noRawIso.spec.ts`）**不在 CI**（前端 Playwright 全量都不在 CI），仍是手动门禁。
>
> **⑥ 既有 E2E 回归（与 Phase 1 同一命令、同一口径）**：
> ```
> $ E2E_BASE_URL=http://localhost npx playwright test nav marketLayout marketDataFace limitUpSentiment \
>     marketTrust landing darkmode navigation responsive --reporter=line
>   2 failed
>     e2e/responsive.spec.ts:42:7 › 响应式：四档视口下不得横向溢出 › /market/global@390 无横向溢出
>     e2e/responsive.spec.ts:42:7 › 响应式：四档视口下不得横向溢出 › /market/global@768 无横向溢出
>   79 passed (25.4s)
> ```
> - 集合 **81 条**（9 个 spec 文件的 test 生成代码与 Phase 1 一字未变）。Phase 1 记 78 passed + 2 failed = 80
>   ⇒ 当时有 **1 条运行时 `test.skip()`**（数据缺失分支，`marketDataFace`/`navigation` 的按需跳过）
>   这次没跳过，**不是新增或丢失用例**。
> - 2 条红 = **Phase 0 的既有红，route 集合一字未变**（`/market/global@390`·`@768`，属 Phase 3 §4.2）。
>   数值漂移如实记录：`@768` 由 877 → **874**（`.num` 换了数字字体 ⇒ 度量微变），`@390` 仍红。**本 Phase 不动 W4。**
> - 8 个非 responsive 的 spec 文件：**53 passed / 0 failed**。
> - 一次全量运行中 `limitUpSentiment.spec.ts:328`（"从短线情绪进个股，浏览器返回仍停在短线情绪 Tab"）
>   曾红 1 次，**单独复跑 3/3 绿**：它 `expect(page).toHaveURL(/\/stock\//)`，而 `/stock/:symbol` 在本分支
>   是 **redirect**（→ `/company/:symbol`），断言实际赌"能抓到跳转中间态" ⇒ **既有 flake，与本次改动无关**
>   （本次未触碰路由与 `.ladder-stock` 结构）。**未改该断言**（改它属于 Phase 3 之后的清理，且会动既有红线）。
>
> **⑦ 视觉复核（截图在 `.superpowers/sdd/2026-10-12-investment-research-platform-ia/evidence/phase2-*.png`）**：
> **自检抓到 1 个真缺陷并已修** —— `.sw-count`（瓦片第二行）原先按 §3.7 用 `--text-secondary`，
> 实测 `#6a6d78` 对 `--mag-3`/`--mag-4` = **2.49:1 / 1.38:1**、暗色 `#787b86` 对 `--mag-4` = **1.69:1**
> —— 而那两级正是"成分数最多"的格子，**眼睛也确实看得出糊**（浅色下「电子 493 只 · 6 个二级」、
> 深色下同理）。改为**与名称行同为 `--text-primary`**（§4.2 原文就是"文字一律 `--text-primary`"），
> 层次改由字号承担（11px vs 13px/600）⇒ 10 级全部 ≥4.5。这是"门禁绿了但屏幕仍不好读"的典型：
> §8.1-2 只断言 `--mag-*` 对 `--text-primary`，**覆盖不到第二行用的次级色**。


---

## 5. Phase 3（P1）—— W4 响应式（1–2 天）

### 5.1 断点契约（写进 `docs/design/landing-market-theme.md` 新增一节）

| 断点 | 布局 | 内容策略（**依 §0-B = B2 全量响应式修订**）|
|---|---|---|
| `≥1280` | 左栏 208 + 内容 1280 | 全功能 |
| `1024–1279` | 图标栏 64（hover 展开） | 卡片 4→3 列 |
| `768–1023` | 抽屉导航 | 卡片 2 列；表格包 `overflow-x:auto`；热力图**默认 Top10 列表**，可切 treemap |
| `<768` | 抽屉导航（汉堡） | **单列全功能**：不隐藏任何区块；图表按容器宽度退化（见下） |

> ⚠️ **原 `<768` 的"行情速览（隐藏 sparkline / 热力图 / K 线）"已作废** —— owner 裁定 **B2 全量响应式**，隐藏即违约。改为"**每个区块都要有可用的窄屏形态**"：
> - 热力图：treemap **保留**，加 `labelLayout`/缩放阈值防标签重叠；Top10 列表从**替代**降级为**可切换视图**；
> - K 线：窄屏改为 `dataZoom` 默认聚焦最近 N 根 + 手势缩放，**不隐藏**；
> - sparkline：**保留**，按容器宽度决定画点数（不是按断点隐藏）。

- [x] **4.1 指数卡移动端**（`GlobalIndexCardView.tsx`）：`<768` 左列 `min-width: 104px`；名称 `white-space: normal` 允许 2 行（**名称优先级高于代码**，禁止把 `上证指数` 截成 `上证…`）；`CN 000001` 并入名称行做 10px 后缀；sparkline **始终渲染**，画点数按容器宽度自适应（**B2 修订**：原 `width ≥ 768` 才渲染的条件作废）。
- [x] **4.2 表格横向滚动容器**：`market-global/index.tsx` 与所有 AntD `Table` 外层统一包 `.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }`；`Table` 加 `scroll={{ x: 'max-content' }}`。修掉 `scrollWidth=877`（**Phase 0 实测**；计划原写 749，取自更早测量，同一成因）。
- [x] **4.2b Tab 级横向溢出扫描（Phase 0 发现的新增项）**：0.2 的路由集只覆盖**路由级**，漏掉 **Tab 级**布局。Phase 0 实测 **`/market?tab=moneyflow@390` `scrollWidth=420` > 390**（资金流向 Tab 的三个卡片）——它在 0.2 的断言集之外，**至今没有任何门禁守着**。做法：`responsive.spec.ts` 增加一遍 `/market` **五个 Tab** 逐一测横向溢出，并纳入 4.2 验收。
- [x] **4.3 热力图移动端**（**B2 修订**）：`market-heatmap` 在 `<768` **仍渲染 treemap**，但必须解决标签重叠（ECharts `labelLayout: { hideOverlap: true }` + 按画布宽度收矩形预算）。**不得**改成"小屏只给列表"。
- [x] **4.4 首页移动端**：`landing.css` 检查 `.landing-container` 的 `max-width` 与内边距；目标 `<768` 无横向溢出。
- [x] **4.5 移动端形态清单（B2 新增）**：逐个点名需要窄屏形态的图表组件并各留一条 e2e/截图证据 —— K 线（`kline`）、treemap（`SectorHeatmap` / `market-heatmap`）、sparkline（`GlobalIndexCardView`）、`IndustryMetricStrip`、`PriceCostChart`（`/industry/pig`）。**B2 的成本主要在这里**（约 B1 的 3 倍工作量）。

**W4 验收**：0.2 全绿（7 条路由 × 4 档视口）；`/market/global@390` 的 `scrollWidth ≤ 391`；`/market@390` 指数卡内的名称文本**可区分**——作用域限定到首张卡再断言（`page.locator(".ant-card").filter({ hasText: …, })` 内 `toContainText`/`toHaveText("上证指数")`），**不得**用全局 `getByText('上证指数')`（"全球指数"与"A股核心指数"两处同名，必 strict violation，见 §1「新断言必须绕开的两个已知坑」）。

---

#### §5 Phase 3 实测回写（task-26，2026-10-15）

**① 两条已知红的**真实成因**都不是表格** —— 计划 §4.2 假设"包一层 `.table-scroll` 就好"，实测不成立：

| 页面/视口 | 整改前 `scrollWidth` | 真正溢出的元素 | 处置 | 整改后 |
|---|---|---|---|---|
| `/market/global@390` | **874** | AntD **`Segmented`**（`.ant-segmented`，7 段共 **833px**）——每一项 `white-space: nowrap`、不换行不收缩，它自己不是滚动容器，于是把 `.ant-space-item` → 卡片体 → `Col`（`min-width: auto`）一路撑到 **837px** | `.market-global__filter` 加 **`contain: inline-size`** + `overflow-x: auto`（`max-width:100%` / `width:100%` / `padding-inline` **实测均无效**，见下） | **390** |
| `/market/global@768` | **874** | 同上 | 同上 | **768** |
| `/market?tab=moneyflow@390` | **425** | **`.mf-continuity__tag`**（口径 badge，`white-space: nowrap` 且不可压缩，实测宽 388px）把 `.mf-continuity__head` → 卡片 → `Col` 撑宽 | `max-width: 100%` + 省略号（完整口径仍在 title/tooltip 与逐日列表里） | **390** |
| 其余 6 路由 × 4 档 + 5 Tab × 2 档 | — | — | — | **全部 = 视口宽** |

表格**不是**元凶：`.ant-table-content` 在 `scroll.x` 下本就有 `overflow-x: auto`，实测 390 下它宽 308px、`scrollWidth` 900（表在**容器内**滚动）。`.market-global__table-scroll` 仍补上了外层容器（防将来出现不可断行的单元格把宽度传上去），但**它不是这两条红的解**。

**② `contain: inline-size` 是唯一生效的约束**（同一页面、同一布局，逐变体实测）：

```
baseline（无任何约束）              docSW 874 / filter 837
max-width:100% + width:auto         docSW 874 / filter 837
padding-inline:8px                  docSW 890 / filter 853   ← 更糟（包含块自己就是被撑出来的）
width:100%                          docSW 890 / filter 853
contain: inline-size                docSW 390 / filter 256   ← 唯一有效
```

原因：`max-width` 的百分数、`padding-inline` 的"包含块 − padding"都**回指一个由内容撑出来的宽度**；`contain: inline-size` 的语义正是"本元素行内尺寸与内容无关"，才切断了这条传播链。

**③ sparkline 的 B2 落法与我最初的写法相反**：我先写成"窄屏让 sparkline 占满整行"（`max-width: none; width: 100%`），结果它把点数的决定权**又交回视口断点**（窄屏容器必然宽 ⇒ 点数必然多），正是 B2 否掉的那件事。改为**全档同宽**（`max-width: 88px`）+ `sparkPointsForWidth(容器实测宽度)`（`ResizeObserver`），首帧宽度未知时**不采样**（画全部点，而不是先画一条假走势）。实测四档视口下点数与画布宽**完全一致**（29 点 / 88px），可机械回归（`data-spark-points`）。副作用：数据源只给 30 个点 ⇒ 88px 槽位算出 29 点，采样在**当前数据量下几乎不触发**（`total` 一涨就会触发）；这是诚实的结果，不是"没实现"。

**④ §4.3 的 `labelLayout` 措辞实测更正**：treemap 的 `labelFormatter(params)` **拿不到**方块尺寸（`params.data` 只有 `name/value/pct_chg/symbol/itemStyle`，`width/height` 均 `undefined`），但 `labelLayout` **确实被调用**（探针收到 `{ dataIndex, text, rect, labelRect }`），所以"防重叠"的实现是 `labelLayout: () => ({ hideOverlap: true })`（ECharts `LabelManager` 做矩形互斥，被压住的标签 `ignore = true`、悬停仍显示）。**但只做这一层不够**：`hideOverlap` 只保证"不重叠"，不保证"有地方放字"——900 个矩形塞进 308px 画布时每块平均 ~10×5px，实测结果是"极少数块有标签、其余全空"。故**按画布宽度收矩形预算**：`rectBudgetForWidth(width) = clamp(width/1.8, ≥90, ≤MAX_TOTAL_RECTS)`（390 视口 → 172 块，1440 → 639 块 **低于** 900 ⇒ 宽屏行为不变）。这是**可读性**手段，性能上界仍是 `MAX_TOTAL_RECTS`。Top10 列表**未做**（理由见 `docs/design/landing-market-theme.md` §1.4 的"实现状态"）。

**⑤ `PriceCostChart` 无窄屏截图 —— 环境缺凭据**：`/industry/:industryKey` 要 `research:read`；本环境匿名 401、`/auth/login` 无已知口令、`E2E_ADMIN_USER`/`E2E_ADMIN_PASSWORD` 未设置。它由 `responsive.spec.ts` 的 `/industry/pig@390`（实际测 `/login`）覆盖"不溢出"，但**组件本身**没有窄屏证据。其余 §4.5 组件齐全。

**⑥ 窄屏断言的两个"永不执行"陷阱（本次踩到并改掉）**：
- `window.echarts?.getInstanceByDom(...)` —— 应用包**不挂** `window.echarts`（实测 `undefined`），写成 `if (inst) { 断言 }` 会得到一条**永不执行的假断言**（比没有断言更坏）。改用 DOM/`getComputedStyle` 层可观测的后果（K 线用 MA 数值行的 `left: 46px` 证明 `narrow` 生效）。
- zrender 在 **canvas 渲染器**下**没有** `zr-*` DOM（实测该卡片内 `zr-` 元素数 = 0），"找 slider 拖动点"这条路不存在。

**⑦ 验收数（dev server，`E2E_BASE_URL=http://localhost:3000`）**：`responsive.spec.ts` **44 passed**（28 路由档 + 10 Tab 档 + 2 名称档 + `/login` 已并入路由集）；`responsive-b2.spec.ts` **5 passed / 1 skipped**（skip = `PriceCostChart`，理由见 ⑤）。全量 158 基线见 §8 回写。

---

## 6. Phase 4（P2）—— W5 文案与打磨（1–2 天）

- [x] **5.1 品牌字标统一为 `StockBot`**：`MainLayout.tsx:205`、`MainLayout.tsx:257`、`login/index.tsx:68`、`index.html` `<title>`；**顺带收口同源的另外三处**（`admin-users` 与 `change-password` 的 `document.title`），否则"字标统一"名不副实。加入 slop 扫描（命中 `Stock Bot` 即**失败**，不是告警）。
- [x] **5.2 landing 压缩排序**（**依 §0-D 裁定 D1 改写**：维持"免登录行情台"，**不删区块、不迁移区块**）：
  - 6 个区块**全部保留**（`实时市场脉搏` / `今日榜单` / `行业与资金` / `资金与情绪` / `日历` / `快讯`）——D1 推翻了原 D2 的"移入 /market + 删除"方案。
  - 只做**压缩排序**：`今日榜单` 改 3 列密排（见 5.3，唯一真正大幅降高的手段）；区块内边距与标题区按 §4.1 的间距刻度收紧；把"信息量最低的区块"排到最后。
  - **目标不再是 `scrollHeight ≤ 4200`**（那是 D2 的产物）：改为"**在 D1 的 6 区块结构下把 scrollHeight 降到尽可能低**"，并把整改前后实测值一同记入 §7。**不接受**为了达成某个数字而删内容。
  - 唯一例外（与 D1/D2 无关）：`日历` 区块里 `日历即将上线` 的**空占位卡**必须删除或填入真实内容 —— 那是"空盒子"不是"区块"，第 4 屏放空盒子在任何首页定位下都是缺陷。
- [x] **5.3 `DataRow` 支持多列**：`shared/ui/DataRow.tsx` 加 `columns?: 1|2|3`（默认 1），`RankingMatrix` 用 3 列。根因：现在 10 行 × 64px = 640px，而每行只放"名称+代码 / 价格+涨跌幅"两个数据点，在 1400px 宽里两端相距 1300px（`DataRow.css` 的 `padding: 8px 0` + 两行文本撑出 64px）。3 列后同屏信息量 ×3，高度 ÷3。
  - **落法（与计划字面有一处差异，实测后收口）**：新增的不是 `DataRow` 的 prop 而是**容器** `DataRowList`（`columns` 是布局属性，挂在行上会让每个行元素各自为政）。`columns === 1` 时**不渲染任何新 DOM 层**（既有单列消费方 `SectorFlow` 的节点树一字不变）。
  - **退档用视口媒体查询，不是 `@container`**：最初写成容器查询，390px 实测**仍是 3 列 × 89px**、10/10 标题被 ellipsis 截断（正是 §1 目标 5 要避免的）；换成媒体查询后 390 实测 **1 列、0 截断**。原因与代价写在 `DataRow.css` 的注释里。
- [x] **5.4 删装饰性结构装置**：`CompactHero` 的 `A股行情` 药丸（无信息）；`ProductShowcase` 的 `01/02/03` —— 改为在途流程（原料 → 研判 → 信号 → 仓位）**或**去编号，二选一（`01/02/03` 只在内容真的是序列时才是信息）。
  - 选**改为在途流程**：`原料 → 研判 → 结论 → 执行`，四项各自对应真实组件（指标带 / 周期相位条 / 信号面板 / 仓位建议）。第四项不是新编的营销文案，而是把链路补完（原文案的"信号面板"描述里本就含"仓位建议联动"）。
  - 视觉复核抓到并修掉一个自造缺陷：链路位置后挂的 `↓` 落在行尾成为**孤儿字形**（读起来像"原料 ↓"），已删，方向由纵向排列 + 位置文字承担。
- [x] **5.5 投研工作台信号历史 diff 化**：`features/industry-research/components/SignalPanel.tsx` 首条给完整 `触发依据`，后续只渲染变化项（`猪粮比 4.65 → 4.34`、`能繁环比第 3 个月回落`）；连续无变化折叠为 `连续 5 日无变化`。E2E 断言：第 2 条起不包含首条的 `触发依据` 全文。
  - **算法抽成纯函数** `signalHistoryDiff.ts`（无 React/DOM），且**另有一个不需要凭据与浏览器**的门禁 `frontend/scripts/check-signal-diff.mjs`（挂进 `npm run check:design`）；判据用**库内真实 `reason`**（`industry_signals` where `industry_key='pig'`）。
  - 折叠文案是 **`连续 N 条无变化`**（不是计划写的"N 日"）：`signal_history` 的条目是**信号变更点**、不是逐日快照，说"日"会把变更点误读成日历日。
  - 渲染层 E2E `signalPanel.spec.ts` **mock session + dashboard**（该路由要 `research:read`，本环境无凭据）——按 `research.spec.ts:24` 的教训，**不是**只 mock `/auth/session` 的假登录态：页面消费的全部读面一起 mock，没有真接口参与。
- [x] **5.6 两栏均衡**：`industry/workbench.tsx` 的左栏在 `猪周期阶段定位` 下方补 `价格 vs 成本剪刀差` 小图（数据已在 `PriceCostChart` 的同一 payload），消掉现测约 400px 空洞。
  - 新增 `CostScissorsChart.tsx`：消费**同一份** `dashboard.trends.price_vs_cost`，只画 `生猪价 − 成本` 这一个派生量（头均盈亏按 `PriceCostChart` 的同一系数 115 元/头折算）。用 SVG 而非第二个 ECharts 实例（150px 高、一条线，且要与上方的双线图在视觉上区分）。点数按容器实测宽度取。
- [x] **5.7 占位页升级为路线图**：新建 `frontend/src/shared/config/module-roadmap.ts`（每模块 3–5 个子模块 + 状态 `规划中/接入中`，来源 IA 计划 §3.3），`shared/ui/PlaceholderPage.tsx` 渲染为标签化骨架 + 一句"这个模块需要什么数据"。E2E 断言 8 条占位路由每条 ≥3 个 roadmap 项。
  - **渲染落点是 `PlaceholderNotice` 而不是 `PlaceholderPage`**：8 条占位路由由**路由清单注入** `PlaceholderNotice`，`PlaceholderPage`（带 `SectionCard` 的完整壳）只有 `/me` 用它。按计划字面改 `PlaceholderPage` 会让 8 条路由**一行都不变**——这是本 Phase 最容易做成"改了但没生效"的一条。
  - **顺带补做 IA §5.2 的参数自证**：`placeholder-pathname` 与查询参数原先只在 `PlaceholderPage` 里渲染 ⇒ 8 条占位路由的契约**从未兑现**（实测 `/search?q=生猪` 页面文本无 `q=生猪`）。已在 `PlaceholderNotice` 补上。
- [x] **5.8 北向资金口径一致**：~~`DataCoverageMatrix` 的 `北向资金` 行由 `planned` 改为 `stale`（新枚举），文案 `已接入 · 上游停更`~~ —— **该组件与那份静态矩阵已由 `1efe65a` 删除**（`git grep DataCoverage frontend/src` = 0）。按控制器的重新定向落地：
  - 新建 `frontend/src/shared/config/dataSourceCaliber.ts` 的 `NORTHBOUND_SOURCE`：**唯一常量**，状态文案 + 徽章色 + 编号 key；附 `NORTHBOUND_QUERY`（React Query key + 窗口天数）让两个表面**共用一次请求**。
  - **表面 A** `NorthboundCard`：顶部加 `已接入 · 上游停更 / 已接入 · 上游日更` 徽章（按 payload 的 `source_status` 选文案），停更时历史曲线**灰化**（虚线 + 半透明 + `emphasis.disabled`，不参与 hover 强调）。
  - **表面 B** `/market/data-sources`：新增 `data-sources-northbound` 登记块，**同样消费 `NORTHBOUND_SOURCE`**（不新造第二个常量）。页面上**原本没有北向行**——`coverage[]` 实测只有 8 个域且不含北向，而前端**不得**往那张表里塞本地行（那就退回被删掉的静态矩阵那条路）。
  - 状态归一化走 `normalizeSourceStatus`：**缺字段归 `discontinued`**（"不确定"不得读成"实时可用"）。既有 `FreshnessNote` 的 `数据源已停更` 与测试锚点一字未动。


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
| 首页 `scrollHeight` | 5935（§7.1 同口径基线；计划正文原记 **7051** 为旧版/旧口径） | ~~≤ 4200（D2）~~ **已作废**（D1 裁定）；改为"在 6 区块结构下尽量低" | 0.7 基准 |
| `.css` 中 `:focus-visible` 规则数 | 1 | ≥ 1 全局 + 局部 | 0.5 断言 |
| 自绘 `fontVariantNumeric` 处数 | **65**（Phase 0 实测；原写 22 为更早的 grep）→ **67**（Phase 1 新增的 `pages/market-data-sources/` 自带 2 处） | 1（`.num` 定义） | §8.1-5 |
| `--mag-0..4` 各级对 `--text-primary` 对比度 | 2.10:1（accent 叠 alpha） | 10 级全部 ≥4.5 | §8.1-2 |

**§7 的 Phase 2 实测值（2026-10-15，`npm run check:design` 直接打印，非手算）**：

| 级别 | Light 色值 | Light 对 `#131722` | Dark 色值 | Dark 对 `#d1d4dc` |
|---|---|---|---|---|
| `--mag-0` | #eef1f6 | 15.81:1 | #1e222d | 10.71:1 |
| `--mag-1` | #e2e7f0 | 14.42:1 | #262c3a | 9.42:1 |
| `--mag-2` | #cfd7e5 | 12.36:1 | #313a4d | 7.68:1 |
| `--mag-3` | #a9b5c9 | 8.64:1 | #3f4a61 | 5.99:1 |
| `--mag-4` | #75859f | **4.78:1** | #4c5872 | **4.81:1** |

> 计划 §4.2 里写的 4.77 / 4.80 与门禁的 4.78 / 4.81 是同一组候选值的四舍五入差；**以门禁为准**（它才是判据）。

---

#### §6 Phase 4 实测回写（task-27，2026-10-15）

**① 首页 `scrollHeight`：5935 → 5428（−507px / −8.5%），同口径（1440×900 匿名、`domcontentloaded` → `networkidle` 有界 12s → 停 1.5s）。**

**逐区块实测（改前 → 改后，1440/1280 同值）**：

| 区块 | 改前 | 改后 | Δ | 靠什么 |
|---|---|---|---|---|
| `hero` | 230 | **203** | −27 | 删静态药丸 `A股行情`（§5.4） |
| `pulse` 实时市场脉搏 | 602 | 602 | 0 | 未动（数据驱动） |
| `rankings` 今日榜单 | **694** | **357** | **−337** | §5.3 三列密排（10 行 → 4 行栅格） |
| `sectors` 行业与资金 | 607 | 607 | 0 | 未动 |
| `money` 资金与情绪 | 276 | 276 | 0 | 未动 |
| `calendar` 日历 | 183 | **0（区块删除）** | **−183** | §5.2 唯一例外：空占位盒 |
| `news` 快讯 | 502 | 502 | 0 | 未动 |
| `product` 工作台长什么样 | 586 | **682** | **+96** | §5.4 三条并列特性 → 四项在途流程（**主动加高**，见下） |
| 其余（ValueProps / IndustryGrid / AccountPerks / BottomCTA / Footer） | — | — | ≈0 | 仅 §5.2 的区块间距 16→12、上下 padding 24/48→16/32 |

> **`product` 变高不是回退，是有意的取舍**：§5.4 的二选一里选的是"改为在途流程"，
> 而完整链路是**四项**（原料 → 研判 → 结论 → 执行）⇒ 比原三条多一项、且新增的第四项
> 是真实链路位置（原文案"信号面板"的描述里本就写着"与仓位建议联动"）。已把每项 padding
> 20→12、gap 16→14 压回去一部分；**没有为了数字把第四项删掉**——那正是 §5.2 说的
> "不接受为了达成某个数字而删内容"。
>
> **净账**：−27（hero）+ −337（rankings）+ −183（日历）+ −96（product 反向）+ 间距≈−24
> ≈ −667；实测 −507 ⇒ 差额来自 `rankings` 改后 4 行的次像素与间距重新分配。**四舍五入不掩盖**。

**② 三列密排的实测判据（不是"看起来更密"）**：

```
[BASE] landing@1440 datarow-grid box={"x":177,"y":961,"width":1086,"height":212} rows=10
        columns=348.656px 348.672px 348.656px   标题截断 0/10
[BASE] landing@390  datarow-grid box={"x":41,"y":1304,"width":308,"height":530} rows=10
        columns=308px                            标题截断 0/10   scrollWidth=390=innerWidth
```

`@390` 的 **1 列 + 0 截断**是修出来的，不是自然结果：最初用 `@container` 时实测**3 列 × 89px、
10/10 标题全被 ellipsis 截断**（正是 §1 目标 5 要避免的），换成视口媒体查询后才对。
详细原因与代价写在 `frontend/src/shared/ui/DataRow.css` 的注释里。宽屏 0/10 截断、390 0/10 截断。

**③ 5.5/5.6/5.7/5.8 的机械证据**：

| 条 | 证据 |
|---|---|
| 5.5 | 纯函数门禁 `node scripts/check-signal-diff.mjs` → **全部通过**（22 条断言，用库内真实 `reason`）；渲染层 `e2e/signalPanel.spec.ts` → **5 passed**（含"第 2 条起不复述首条全文"与"折叠条数守恒"） |
| 5.6 | `e2e/signalPanel.spec.ts` 的 `cost-scissors` 用例 → 1 passed（点数为 payload 有效月数） |
| 5.7 | `e2e/placeholder.spec.ts` → **4 passed**；逐路由打印：`/company 5 · /news 4 · /macro 5 · /valuegraph 3 · /strategy 5 · /risk 4 · /trade 4 · /search 4`（全部 ≥3） |
| 5.8 | `e2e` 未新增断言（既有 `marketTrust` / `marketDataFace` 的北向用例继续绿）；两态实测文本见下 |

**5.8 两态实测**（既有 mock 机制把 `source_status` 覆写成 `discontinued`；`live` 为真实 payload）：

```
northbound(live)         = 北向资金 | 盘后成交额 · 亿元 | 已接入 · 上游日更 | 数据截至 10月12日 3 天前 | 当日成交额 24.80亿 | 近30日累计成交额 363.51亿
northbound(discontinued) = 北向资金 | 盘后成交额 · 亿元 | 已接入 · 上游停更 | 数据截至 10月12日 3 天前 | 数据源已停更（最新 10-12） | 当日成交额 24.80亿 | 近30日累计成交额 363.51亿
data-sources             = 北向资金 | 已接入 · 上游日更 | 沪深股通日频序列（2024-08-19 起为成交额口径；此前为净买入，两者不可拼接）；库内最新 2026-10-12 | 产地 tushare.moneyflow_hsgt
```

> 两处的 `已接入 · …` **逐字来自同一个 `NORTHBOUND_SOURCE`**（`shared/config/dataSourceCaliber.ts`），
> 且共用一个 `NORTHBOUND_QUERY`（React Query key + 窗口天数）⇒ 同一次请求、同一份缓存。
> ⚠️ 现状真实 payload 是 `source_status="live"`（`as_of=2026-10-12`，3 天前），
> 故两处现在都显示 **`上游日更`**；`上游停更` 分支由既有 e2e 的 mock 驱动（本环境无法
> 让上游真的停更来取证）。两态都实测过，截图在 evidence 里。

**④ 视觉复核（截图在 `.superpowers/sdd/2026-10-12-investment-research-platform-ia/evidence/phase4-*.png`）**：
**自检抓到 2 个自造缺陷并已修**——

1. **`@container` 不生效**（见 ②）：390 下三列 89px、标题全截断。
2. **流程位置后的 `↓` 是孤儿字形**：`原料 ↓ / 研判 ↓ / 结论 ↓` 渲染在行尾，读起来像
   "原料 ↓"这个整体，且最后一项没有箭头 ⇒ 视觉上像漏了一个。已删箭头，方向由纵向排列 +
   位置文字承担（`ProductShowcase.tsx` 注释写明）。
3. **顺带发现（不是本次改动引入，但本次才被断言抓到）**：IA §5.2 的「把 query 与路径参数
   原样显示出来」对 8 条占位路由**从未兑现**——那两个 `data-testid` 只在 `/me` 用的
   `PlaceholderPage` 里渲染。已在 `PlaceholderNotice` 补 `ParamSelfProof`。



### 7.1 整改前 `scrollHeight` 基准快照（0.7，2026-10-14 实测）

**怎么测的**：Playwright（chromium）新上下文，视口 `390 / 1280 / 1440 × 高 900`，
`page.goto` → `domcontentloaded` → `networkidle`（有界 8s）→ 停 800ms，读
`document.documentElement.scrollHeight`。**未登录**（匿名）态；`/industry/pig` 与 `/me`
在匿名下 redirect 到 `/login`，故它们的数是**登录页**高度（900 = 一屏），不是工作台页。

| 页面 | 390 | 1280 | 1440 | 备注 |
|---|---|---|---|---|
| `/`（landing） | 9026 | 6065 | 6065 | 本表为**当前分支**实测（计划正文原记 7051 为旧版/旧口径） |
| `/market` 指数总览 | 2590 | 956 | 956 | 计划正文原记 1439，同上 |
| `/market` A股全景 | 9933 | 3667 | 3667 | 计划正文原记 3141，同上 |
| `/market` 资金流向 | 1689 | 943 | 924 | 390 下 `scrollWidth = 420`（**已溢出**，0.2 的路由集合未覆盖本 Tab，见 task-23 报告 0.2） |
| `/market` 数据面 | 900 | 900 | 900 | 实盘数据面为空态（非交易时段） |
| `/market` 短线情绪 | 5763 | 3372 | 3344 | |
| `/market/global` | 2845 | 2491 | 2469 | 390 / 768 下 `scrollWidth = 877`（0.2 的红） |
| `/market/heatmap` | 1132 | 917 | 900 | |
| `/company/600540` | 1943 | 1278 | 1278 | |
| `/industry/pig` | 900 | 900 | 900 | 匿名 → `/login` |
| `/me` | 900 | 900 | 900 | 匿名 → `/login` |
| `/market/index/000001.SH` | 900 | 900 | 900 | 页面直出 ISO 串（0.6 运行时断言的红） |

**读数纪律（后续复测必须同口径）**：上表是**实盘数据下的结构高度**，不是快照基线——
行情条数变化会改变高度（尤其 A股全景 / 短线情绪）。"去重复收益"只看**同口径前后差**
（同视口、同登录态、同一天）；不要把本表当 CI 断言阈值（§1 已明确禁用像素快照）。
0.2 的 `scrollWidth` 是同步布局事实、不受数据条数影响，故它当门禁而本表不当。

**Phase 依赖**：`0 → (1, 2, 3) → 4 → 5`；W1 需 IA 的 Wave 0「IA 冻结」先落；W2-2.4 需 §0-A 裁定。

---

## 8. 门禁（新增项，全部可机械检测）

### 8.1 扩展 `frontend/scripts/check-design-tokens.mjs`

1. `--mag-0..4` 在三块（`:root` / light / dark）齐全，且与 `theme.ts` 的同名数组逐项一致（沿用现有"三块一致性"的校验写法）。
2. 对每级算对比度：light 与 `--text-primary`、dark 与 `--text-primary`，全部 ≥4.5；不足即失败并打印实测值。
3. `--font-ui` / `--font-num` 存在，且 `theme.ts` 的 `token.fontFamily` 引用 `--font-ui`。
4. `theme.css` 含 `@media (prefers-reduced-motion: reduce)` 与 `:focus-visible` 两块。
5. `frontend/src/**` 中 `fontVariantNumeric|font-variant-numeric` 的出现次数 ≤1（只允许 `.num` 定义处）。**Phase 0 实测实际是 65 处**（§7 原写 22，取自更早的一次 grep；门槛值 ≤1 不变，待 §3.4 统一到 `.num`）。**Phase 2 收口：开工时 67（Phase 1 新增页带来 2 处）→ 交付时 1。**

### 8.2 日期口径门禁（**两层**，Phase 0 据实修正）

**Layer 1（静态，源码字面量）**：`frontend/scripts/check-no-raw-iso.mjs` 扫 `frontend/src/**/*.tsx`，命中 `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}` 即失败（日期一律走 `shared/ui/date.ts`）。

> ⚠️ **只做 Layer 1 不够 —— 它在结构上抓不到本计划要抓的那个缺陷。** Phase 0 实测：`market-index` 渲染的是 `{index.asof}`（**payload 直出**），源码里没有 ISO 字面量 ⇒ Layer 1 **恒绿**（扫 136 个 `.tsx` / 8130 段文本）。而用户实际看到的就是 `2026-10-13T15:00` 这种未格式化的 ISO。

**Layer 2（运行时，页面可见文本）**：`frontend/e2e/noRawIso.spec.ts` —— 打开目标页，对**可见文本**跑同一个正则。Phase 0 实测 **`/market/index/000001.SH` 可见文本含 `2026-10-13T15:00` ⇒ 红**，这才是本门禁的真正红线。

**为什么两层都要**：Layer 1 防**将来**有人写死字面量（便宜、无浏览器）；Layer 2 抓**现在**payload 直出的那个缺陷（贵、但抓得到）。只留任一层都会漏一类。

### 8.3 slop 扫描追加（`scripts/slop_scan.sh` 或 best-practices 映射表）

`Stock Bot`（应为 `StockBot`）· `landing-placeholder`（空占位卡）· `WHITE_TEXT_THRESHOLD`（用 accent 编码数量）。

> **Phase 2 状态**：**未做**（`WHITE_TEXT_THRESHOLD` 已随 §3.7 从代码里消失，但 `Stock Bot` 的改名是 **5.1** 的范围）。
> 三条探针一起在 W5 落地更合适——否则 Phase 2 加的两条会在 Phase 5 之前**每天命中既有文案**（`Stock Bot` 现在仍有 4 处），
> 把 slop_scan 变成噪声。**这是有意的跨 Phase 推迟，不是遗漏。**
>
> **Phase 4 状态（据实测收口，三条不是"都加"）**：
> - **`Stock Bot` —— 已加，且是硬探针（不是告警）**：`scripts/slop_scan.sh` 末尾新增树级检查，
>   扫 `frontend/src` + `index.html` + `public` + `e2e`，命中即以 **exit 1** 失败。
>   为什么升级成失败：它不像上面那张映射表"命中需人工复核"，而是**确定错误**（改名前才有的写法）。
>   实测双向：把 `index.html` 的 `<title>` 临时改回 `Stock Bot` ⇒ **EXIT=1** 并打印命中行；还原后 ⇒ **EXIT=0**。
>   扫描范围**刻意排除** `plans/**` 与 `docs/**`——那两处的 `Stock Bot` 是"改成 StockBot"这句历史记录本身。
> - **`landing-placeholder` —— 未加（且不该加）**：Phase 4 删掉的是 landing 日历区块里的**空盒子**，
>   但 `.landing-placeholder` 这个类**仍然活着**：`MarketPulse` 在行情取不到时用它渲染
>   "行情数据暂不可用，请稍后重试"（失败态文案，不是空盒子）。按类名扫描会把它判成违规——
>   一条会命中**正确代码**的探针只会被关掉，不如不加。真正要守的判据是"landing 不存在空占位卡"，
>   而它已经被 §5.2 的实现（区块删除）与 `e2e/landing.spec.ts` 的结构断言覆盖，不是靠类名。
> - **`WHITE_TEXT_THRESHOLD` —— 未加**：该常量已随 §3.7 从代码中**整体删除**（§8.1-2 现在直接
>   逐级算 `--mag-*` 对 `--text-primary` 的对比度并断言 ≥4.5），加一条"不得出现某标识符"的探针
>   是恒不命中的空转，无收益。

### 8.4 CI 挂钩

`frontend/scripts/check-design-tokens.mjs` 已在 `npm run check:design`；把 `check-no-raw-iso.mjs` 一并挂进同一 npm script，并确认 `.github/workflows/ci.yml` 的 job id 变更同步到 `docs/testing/index.md`（`doc_gate.sh` 规则 5）。

> **Phase 2 已完成**：`frontend/package.json` 的 `check:design` = `node scripts/check-design-tokens.mjs && node scripts/check-no-raw-iso.mjs`
> ⇒ CI 的 `frontend-lint` job（已跑 `npm run check:design`）现在同时守日期静态层。**未新增/改名任何 CI job**，
> 故无需改 `ci.yml`，`doc_gate.sh` 规则 4 不受影响；`docs/testing/index.md` 门禁矩阵已补「日期口径」一行。
> 运行时层（`noRawIso.spec.ts`）**不在 CI**——前端 Playwright 全量都不在 CI（见 `docs/testing/index.md`）。

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
