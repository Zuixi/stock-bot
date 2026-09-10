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

## 2. 路由变更

```text
/                  → Landing 宣传页（公开，独立布局，不套 MainLayout）
                     已登录：导航与 Hero CTA 显示「进入工作台」→ /market
/login /register   → 现有（不动）
/market /research… → RequireAuth + MainLayout（现状不动）
```

## 3. Landing 页区块（9 区块，见 docs/design/landing-page-ux.md 草案）

文案定稿：H1「把一个行业，研究透。」；信任行「申万 31 个一级行业 · 5,500+ 只个股 · 多源交叉验证」。
实时脉搏卡调公开 API：`/api/v1/market/indices` + `/api/v1/market/distribution`（60s 轮询）。
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

卡片规范：`--bg-panel` 底、1px `--border`、8px 圆角、标题 13px secondary、ticker 行 14px。
涨跌幅渲染统一 `ChangeText`（红涨绿跌、右对齐、+/- 号）。现有组件保留逻辑、只换皮 + 重组布局。

## 5. 数据覆盖（data-coverage 风格）矩阵

Landing 第 6 区块 + `/market` 页脚上方各放一份精简矩阵。全部免费，来源与频率如实标注：

| 数据域 | 内容 | 频率 | 来源 | 级别 |
|---|---|---|---|---|
| A股行情 | 沪深北 5,500+ 只日线 OHLCV | 日度（盘后） | TuShare | 免费 |
| 估值指标 | PE/PB/换手/市值/量比 | 日度 | TuShare | 免费 |
| 全球指数 | 上证/深证/创业板/恒生/日经/KOSPI/标普/纳指等 | 实时快照+日度 | 东财/AKShare | 免费 |
| 行业体系 | 申万 31 L1/全层级分类+成分 | 静态+季度 | 申万 2021 版 | 免费 |
| 资金流向 | 大盘四档/板块/个股主力净流入 | 盘中+盘后 | 东财 | 免费 |
| 北向资金 | 沪深港通净流入 | 盘后 | 东财 | 免费 |
| 龙虎榜/大宗/解禁/回购 | 每日榜单 | 盘后 | 东财 | 免费 |
| ETF/可转债 | 日线行情 | 日度 | TuShare | 免费 |
| 财务三表 | 利润/资产负债/现金流+衍生指标 | 季度 | TuShare/巨潮 | 免费 |
| 行业产能指标 | 生猪价格/能繁存栏/猪粮比等（多源分级） | 日度/月度 | 统计局/协会/生意社/期货 | 免费 |

统计带（TV 风）：**10 大数据域 · 5,500+ 标的 · 4 级数据权威分级 · 全部免费**。

## 6. 验收（实现完成后必须全绿）

1. `npm run build` + `npx tsc --noEmit` 通过
2. 暗色切换：点按钮生效、刷新后保持、landing 与 market 全部区块无残留白底
3. 已登录 `/` 显示「进入工作台」，未登录显示「免费开始」
4. Playwright E2E 新增 `landing.spec.ts` + `darkmode.spec.ts` 全过；既有 spec 回归不挂
