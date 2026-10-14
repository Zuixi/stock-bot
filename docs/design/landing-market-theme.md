# Landing 宣传页 + TradingView 风格市场页 + 暗色模式 设计契约

> 分支 `feature/landing-market`（基于 `feature/p7-auth-gateway`）。
> 本文档是三个实施阶段的统一契约：品牌、配色 Token、路由、组件归属。

## 0. 已拍板决策

- 品牌名：**StockBot**（猪智投作为首个行业案例露出）
- 已登录访问 `/`：**停留宣传页，CTA 换成「进入工作台」**（不自动跳转）
- 市场页视觉语言与 TradingView 一致；数据版图参考 cn.tradingview.com/data-coverage/
- 必须支持暗色模式切换，配色参考 TradingView

## 1. TradingView 配色 Token（全局 CSS 变量 + AntD token 双轨）

CSS 变量挂在 `html[data-theme='light'|'dark']` 上，`src/app/theme.ts` 消费同名 token 注入 AntD。

| Token | Light | Dark（TV 实测；涨跌色见下方 AA 说明） |
|---|---|---|
| `--bg-page` | #ffffff | #131722 |
| `--bg-panel` | #f8f9fd | #1e222d |
| `--bg-elevated` | #ffffff | #232837 |
| `--border` | #e0e3eb | #2a2e39 |
| `--text-primary` | #131722 | #d1d4dc |
| `--text-secondary` | #6a6d78 | #787b86 |
| `--accent` | #2962ff | #2962ff |
| `--up`（A股红涨） | #c62828 | #f2555a |
| `--down`（绿跌） | #0a7d5f | #0aa088 |
| `--hover` | rgba(41,98,255,.06) | rgba(41,98,255,.12) |

> 涨跌色按 WCAG AA 调整，判据为**两个真实表面**（`--bg-page` 与 `SectionCard` 的 `--bg-panel`）对比度均 ≥ 4.5：
> - 浅色原 `#f5222d`/`#22c55e` 对 `--bg-page` 仅 4.08:1 / 2.28:1 → `#c62828`（5.62/5.34）/`#0a7d5f`（5.11/4.85）。
> - 暗色原 TV 实测 `#f23645`/`#089981` 对 `--bg-page` 4.59/5.01 达标，但对 `--bg-panel` 仅 4.08/4.45 不达标 → `#f2555a`（5.30/4.71）/`#0aa088`（5.45/4.84）；此处以 AA 表面为准，不再等于 TV 实测暗色值。
>
> 门禁：`frontend && npm run check:design`（校验 `:root` 兜底块、light、dark 三块 × 两表面的对比度，及与 `theme.ts` 的一致性）。

暗色判定优先级：localStorage `stockbot-theme` > `prefers-color-scheme`。切换按钮（太阳/月亮 icon）放 **MainLayout Header 右侧 + Landing 导航右侧**。ECharts 统一走 `shared/ui/EChart` 封装，从 ThemeContext 读 axis/text/splitLine 颜色。

### 1.1 字体与数字 token（Phase 2 W3 / 计划 §4.1）

三个块（`:root` 兜底 / `light` / `dark`）**都必须**声明（与上面颜色 token 同规则），`app/theme.ts` 的 `token.fontFamily` 引用 `var(--font-ui)`：

| Token | 值 | 说明 |
|---|---|---|
| `--font-ui` | `"PingFang SC", "Microsoft YaHei", system-ui, sans-serif` | UI 字体族（C2 路径：中文沿用系统栈） |
| `--font-num` | `ui-monospace, SFMono-Regular, Menlo, monospace` | 数字字体族（零字节，走系统等宽） |

**字体自托管（C1）的实测结论：超预算，按 §0-C 降级 C2。** 2026-10-15 实测（`frontend/scripts/subset-fonts.sh` 可复现）：
Noto Sans SC 子集（本仓库源码字符集，1164 个 CJK 字形）woff2 单字重 **194 KB**（400/500/600 = 194,292 / 197,088 / 197,328 B），
对照预算「约 120KB × 2 字重 = 240 KB」**超预算 2.4×**；若按「常用 3500 字」规模（3500 字形代理集）单字重 **~493 KB**，超预算 6×。
IBM Plex Mono 数字子集（`0-9 + - % . , 亿 万`）**3,300 / 4,096 B**，在 15 KB 预算内。
故**不引入任何字体文件、不声明 `@font-face`**（不指向缺失文件）；`--font-ui`/`--font-num` 抽象层已落地，
将来 owner 放宽预算后只需改这两行 + 加 `@font-face`，**调用点一处不动**（切换步骤见 `scripts/subset-fonts.sh` 文件尾）。

**数字排版唯一入口是 `.num`**（`app/styles/theme.css`）：

```css
.num { font-family: var(--font-num); font-variant-numeric: tabular-nums; font-feature-settings: "tnum" 1; }
```

原先全站有 **67 处**手写的 `fontVariantNumeric` / `font-variant-numeric`（各处内联 style + 各 `.css` 规则各写一遍），
Phase 2 全部收敛到 `.num`，并由 `npm run check:design` 的 §8.1-5 **机械断言该声明在 `src/**` 里 ≤1 次**（只有定义处）。

**字号刻度**（同一份契约，工作区）：

| 位置 | 字号 / 字重 / 颜色 |
|---|---|
| 卡片标题（`.section-card__title`） | **14px / 600 / `--text-primary`** |
| 数据行数字（`.datarow__price`） | 14px（走 `.num`） |
| 数据行名称（`.datarow__title`） | 13px |
| 数据行代码（`.datarow__ticker`） | 11px / `--text-secondary` |
| 口径与 `as_of`（`.section-card__asof`） | 12px / `--text-secondary` |

### 1.2 量级色阶 `--mag-0..4`（数量编码，Phase 2 W3 / 计划 §4.2）

**不用 accent 叠 alpha 编码"数量"**：原实现 `colors.accent` + 白 alpha 0.5→2.10:1、0.85→3.81:1（需 4.5），
且有 6/31 格命中「白字分支」——颜色本身在编码数量，文字颜色还随数量跳变。改为中性灰阶 + 恒定文字色：

| Token | Light | Dark | Light 对 `--text-primary`(#131722) | Dark 对 `--text-primary`(#d1d4dc) |
|---|---|---|---|---|
| `--mag-0`（最少） | #eef1f6 | #1e222d | 15.81:1 | 10.71:1 |
| `--mag-1` | #e2e7f0 | #262c3a | 14.42:1 | 9.42:1 |
| `--mag-2` | #cfd7e5 | #313a4d | 12.36:1 | 7.68:1 |
| `--mag-3` | #a9b5c9 | #3f4a61 | 8.64:1 | 5.99:1 |
| `--mag-4`（最多） | #75859f | #4c5872 | 4.78:1 | 4.81:1 |

- 三块（`:root` / `light` / `dark`）齐全，且与 `app/theme.ts` 的 `THEME_COLORS[mode].mag` **逐项一致**（门禁 §8.1-1）。
- 每级对同级 `--text-primary` 的对比度 **≥4.5**（门禁 §8.1-2，实测值即上表）；文字**一律 `--text-primary`，无白字分支**。
- 消费方（本轮唯一）：`features/market/components/SwIndustryGrid`——按**官方成分数**的 20/40/60/80 分位阈值落 5 档
  （阈值法：同一 count 必得同一档；分档口径见该组件文件头的 `QUANTILES`）。ECharts 若需数量型配色从 `useTheme().colors.mag` 取。

### 1.3 动效与焦点（Phase 2 W3 / 计划 §4.4）

- **全局降级动效**：`theme.css` 的 `@media (prefers-reduced-motion: reduce)` 把 `animation-duration` / `transition-duration`
  压到 `0.01ms` 且 `animation-iteration-count: 1`（覆盖 `.sw-skeleton` 的 1.4s 无限 shimmer 与 `.sw-cell:hover` 的 `translateY(-2px)`）。
- **全局焦点环**：`theme.css` 的 `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`；
  局部规则（如 `market.css` 的表格行）可继续覆盖，本规则只保证"任何可聚焦元素都有可见焦点"。
- 两者都由 `npm run check:design` 的 §8.1-4 机械断言存在。

## 2. 路由变更

```text
/                  → Landing 宣传页（公开，独立布局，不套 MainLayout）
                     已登录：导航与 Hero CTA 显示「进入工作台」→ /market
/login /register   → 现有（不动）
/market /research… → RequireAuth + MainLayout（现状不动）
```

## 3. Landing 页区块（9 区块，见 docs/design/landing-page-ux.md 草案）

文案定稿：H1「把一个行业，研究透。」；信任行「申万 31 个一级行业 · 5,500+ 只个股 · 多源交叉验证」。
实时脉搏卡调公开 API：`/api/v1/market/indices` + `/api/v1/market/distribution`（60s staleTime；实现期修订：指数卡按市场状态轮询——A 股盘中 30s、全球指数常驻 300s）。
行业网格调 `/api/v1/market/sw-industry/tree`（公开）。
组件落 `src/pages/landing/`（sections/*.tsx），品牌色只用 accent/neutral，不引入新色。

## 4. 市场页 TradingView 化（/market 重构为卡片阵列 + 顶部分类 Tab）

TV 市场页骨架 = 顶部分类 Tab + 地区/主题分组卡片，卡内 ticker 行（名称 · 价格右对齐 · 涨跌幅红绿块）。我们映射为四组：

```text
Tab: 指数总览 | A股全景 | 资金流向 | 数据面
指数总览: 全球指数卡（亚/美分组, sparkline 可后置）+ A股核心指数卡
A股全景 : 涨跌分布卡 + 板块热力图卡 + 申万行业网格卡（TV tile 风）
资金流向: 大盘四档卡 + 板块资金流卡 + 北向卡
数据面 : 龙虎榜/大宗/解禁/回购 Tab 表（现状迁移）
```

卡片规范：`--bg-panel` 底、1px `--border`、8px 圆角、标题 **14px/600 `--text-primary`**、ticker 行 14px。
（**2026-10-15 修正**：原文写「标题 13px secondary」，而实现是 16/600 —— 两处都过期。以计划
[`2026-10-13-frontend-design-remediation.md`](../../plans/2026-10-13-frontend-design-remediation.md) §3.5 为准，
字号刻度见本文档 §1.1。）
涨跌幅渲染统一 `ChangeText`（红涨绿跌、右对齐、+/- 号）。现有组件保留逻辑、只换皮 + 重组布局。

## 5. 数据覆盖（data-coverage 风格）矩阵 —— 已下线

> **2026-10-12：该区块整体移除。** Landing 第 6 区块与 `/market` 页脚上方的两份精简矩阵、组件本体
> （`DataCoverageMatrix.tsx/.css`）与相关 e2e 均已删除：矩阵是静态宣传文案，与真实数据可用性长期漂移
> （北向停更、逐域上线状态靠手改常量维护），且挂在市场页 Tabs 下方导致每个 Tab 都重复出现。
> 真实数据新鲜度已由**后端** `/market/data-freshness` 的 `coverage`（逐域 `have_days`/`complete_days`/`cover_pct`）
> 承担——那是从库里算出来的实时状态，不是手写常量。
>
> 下面 11 行表与统计带仅作设计存档，**代码中已无对应实现**，请勿据它判断某域是否可用。

> **2026-10-14：公开页不披露数据来源**（来源构成属竞争资产，owner 裁定）。矩阵虽已移除，
> 该规则适用于全部免登录页面：页脚不再有「数据来源：TuShare · 东方财富 · 巨潮资讯网 · 上海证券交易所」
> 署名，也在清除各处点名供应商的口径标签（如「东财口径／东财板块／成分截至（东财）」）。
> 下表「来源」列因此仅为**内部口径留档**，任何公开页都不得渲染。

| 数据域 | 内容 | 频率 | 来源 | 级别 |
|---|---|---|---|---|
| A股行情 | 沪深北 5,500+ 只日线 OHLCV | 日度（盘后） | TuShare | 免费 |
| 估值指标 | PE/PB/换手/市值/量比 | 日度 | TuShare | 免费 |
| 全球指数 | 上证/深证/创业板/恒生/日经/KOSPI/标普/纳指等 | 实时快照+日度 | 东财/AKShare | 免费 |
| 行业体系 | 申万 31 L1/全层级分类+成分 | 静态+季度 | 申万 2021 版 | 免费 |
| 概念板块成分 | 板块列表 + 成分股 | 每日 18:20 | 东财 | 免费 |
| 资金流向 | 大盘四档/板块/个股主力净流入 | 盘中+盘后 | 东财 | 免费 |
| 北向资金 | 沪深港通成交额（2024-08-19 起） | — | — | 规划中 |
| 龙虎榜/大宗/解禁/回购 | 每日榜单 | 盘后 | 东财 | 免费 |
| ETF/可转债 | 日线行情 | 日度 | TuShare | 免费 |
| 财务三表 | 利润/资产负债/现金流+衍生指标 | 季度 | TuShare/巨潮 | 免费 |
| 行业产能指标 | 生猪价格/能繁存栏/猪粮比等（多源分级） | 日度/月度 | 统计局/协会/生意社/期货 | 免费 |

统计带（TV 风）：**10 已上线数据域 · 5,500+ 标的 · 4 级数据权威分级 · 已上线数据全部免费**。（历史设计，已随区块下线。）
> ⚠️ **口径更正（2026-10-13，Ruling 62/63）**：本存档表内「北向资金」一行原写「净流入」，但该列自 **2024-08-19** 起已换成**成交额**口径（断点前才是净买入；行级 `amount_kind='turnover'`）。矩阵已下线，此事实以 [data-source.md 附.9](./data-source.md) 为准。


## 6. 验收（实现完成后必须全绿）

1. `npm run build` + `npx tsc --noEmit` 通过
2. 暗色切换：点按钮生效、刷新后保持、landing 与 market 全部区块无残留白底
3. 已登录 `/` 显示「进入工作台」，未登录显示「免费开始」
4. Playwright E2E 新增 `landing.spec.ts` + `darkmode.spec.ts` 全过；既有 spec 回归不挂
