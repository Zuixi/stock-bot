# 前端架构

> 当前态文档。历史上的空壳文件 `ARCHITERTURE.md`（2 行、拼写错误）已由本文件取代。

## 1. 技术栈

| 维度 | 选型 |
|---|---|
| 框架 | React 18 + TypeScript + Vite |
| UI | Ant Design 5 |
| 图表 | ECharts（`echarts-for-react`，统一封装在 `shared/ui/EChart.tsx`） |
| 数据获取 | TanStack React Query |
| 本地状态 | Zustand（持久化） |
| 路由 | react-router-dom（清单 `app/router/route-manifest.ts` 是唯一真相，`app/router/index.tsx` 由它生成 `<Route>`，页面全部 `lazy` + `Suspense`；见 §6） |
| 静态校验 | `tsc --noEmit` + `npm run check:design`（**不引入 eslint**，见 [ADR 0006](../../decisions/0006-no-eslint-tsc-as-static-check.md)） |
| 生产托管 | nginx（`frontend/Dockerfile` 的 runtime 阶段只打包 `dist/`） |

## 2. 目录结构（feature-sliced）

```
frontend/src/
├── app/            # 应用装配：router / layouts / theme（theme.ts + theme-context.tsx）
├── pages/<路由>/    # 路由级页面，只做编排（组合 features + shared），不含业务逻辑
├── features/<域>/   # 业务域：auth · concept · industry-research · market · nav · search · stock-detail · watchlist
├── shared/
│   ├── api/        # 每个域一个 api 模块（market.ts / marketData.ts / limitUp.ts / industryResearch.ts …）
│   ├── ui/         # UI 原语：EChart · StateWrapper · DegradedNotice · FreshnessNote · NumberText · ChangeText · DataRow · SectionCard
│   ├── types/      # 共享类型
│   └── config/
└── ../scripts/      # 门禁脚本（在 frontend/scripts/，不在 src/ 下）：check-design-tokens.mjs 设计令牌（涨跌色对比度 + theme.ts ↔ theme.css 一致）· check-routes.mjs 路由清单 ↔ IA 文档 + 本文件 §6
```

边界约定：

- `pages/` **不写业务逻辑**，只做布局与数据装配
- 跨域复用一律上提 `shared/`；某个域独有的组件留在 `features/<域>/`，禁止跨 feature 直接 import（走 `shared/`）
- 新 UI 原语落 `shared/ui/`，不要在各页面重复实现同类封装

## 3. 数据获取约定

- 统一走 React Query；query key 的拆分见下
- **缓存键必须含数据自身的判据维度**：`as_of` 一定要进键，否则修补/换日/回灌后旧 payload 会以"新日期的旧数据"形态被服务
- **同一端点不同消费节奏要拆 key**：一个端点同时服务"常驻跟美股（300s）"与"A 股指数卡（30s、休市停）"时，共用 query key 会让慢节奏拖慢快节奏（`refetchInterval` 是每个 observer 各起定时器但共享同一份 data，"休市不轮询"会名存实亡）
- 后端返回的**信封对象**（`{ items, asOf, ... }`）保持普通对象形状；**不要用"给数组挂属性"捎带元数据**（`[...arr]` / `.filter` / `structuredClone` / `JSON.parse` 都会静默丢标签，且会破坏 React Query 的结构共享）。统一封装见 `shared/api/marketEnvelope.ts`

## 4. 展示与单位

- 数值展示统一走 `shared/ui/NumberText.tsx` / `ChangeText.tsx` / `DeltaText.tsx`，不要在页面里手写 `toFixed`
- 涨跌色与主题令牌集中在 `app/theme.ts` 与 `theme.css`，两者必须一致（`check:design` 机械校验，含 `bg-page`/`bg-panel` 上的对比度 ≥ 4.5）
- 数据陈旧要**显式标注**：`FreshnessNote.tsx` + 后端返回的 `stale_days` / `source_status`；`as_of` 必须描述返回数据本身，不能借用元数据表的 ingest 时间戳

## 5. 降级与错误

| 组件 | 用途 |
|---|---|
| `StateWrapper.tsx` | 统一的 loading / empty / error 三态 |
| `DegradedNotice.tsx` | 上游降级或部分数据缺失时的显式提示（**不要静默空白**） |
| `ErrorBoundary.tsx` | 渲染异常兜底 |

## 6. 路由一览

**权威来源**：[`frontend/src/app/router/route-manifest.ts`](../../../frontend/src/app/router/route-manifest.ts)（纯数据清单，**唯一真相**）。`app/router/index.tsx` 由清单**生成** `<Route>`，`app/router/route-elements.tsx` 承载懒加载注册表 / `RequireAuth` / 占位通告注入。下表与清单的一致性由 `npm run check:routes` 机械核对（路径集合双向 + `kind` + 组件与页面目录 + redirect 去向 + 权限口径），CI `frontend-lint` 与 `scripts/self_review.sh` 快检都会跑它。

| 路径 | kind | 组件 / 页面目录 / 去向 | 鉴权 | 说明 |
|---|---|---|---|---|
| `/` | page | `pages/landing`（`LandingPage`） | 公开 | 宣传页，独立布局（不套 `MainLayout`） |
| （无 path 的 `main` 外壳） | layout | `MainLayout` | — | 唯一 layout 路由；下表 `parent: "main"` 的行都嵌在它下面（顶栏/页脚由它渲染） |
| `/login` | page | `pages/login`（`LoginPage`） | 公开 | 登录页 |
| `/market` | page | `pages/market`（`MarketPage`） | 公开 | 行情台首页 |
| `/market/global` | page | `pages/market-global`（`MarketGlobalPage`） | 公开 | 全球市场与跨市场标的（S1.3）：按市场分组、五要素齐备；大宗商品/外汇只放「数据源待接入」占位 |
| `/market/industry` | page | `pages/market-category`（`CategoryPage`） | 公开 | 申万分类浏览（`/market/category` 的 redirect 目标） |
| `/market/industry/:level1Code` | page | `pages/market-industry-level2`（`IndustryLevel2Page`） | 公开 | 申万一级下钻 |
| `/market/industry/:level1Code/:level2Code` | page | `pages/market-industry-level3`（`IndustryLevel3Page`） | 公开 | 申万二级下钻 |
| `/market/industry/:level1Code/:level2Code/:level3Code` | page | `pages/market-industry-level4`（`IndustryLevel4Page`） | 公开 | 申万末级：某三级行业的成分股（**SCD2** 区间还原）+ 该节点 7 项行情聚合（S1.4） |
| `/market/heatmap` | page | `pages/market-heatmap`（`MarketHeatmapPage`） | 公开 | 市场/个股热力图（S1.5）：treemap、四分组（全市场/申万一级/概念/自选）、面积=市值或成交额可切换、颜色=涨跌幅；批量读路 `/api/v1/market/heatmap/treemap`；**不是任何 redirect 的目标**（Ruling 30） |
| `/market/rankings/:key` | page | `pages/market-rankings`（`MarketRankingsPage`） | 公开 | A股排行 7 榜（S1.6）：市值 / 股息率 / 月度涨幅 / 月度跌幅 / 半年利润 / 年利润 / ROE，各 100 强；口径三件事（交易日 / 财务期 / 样本范围）由后端 payload 下发；整行下钻 `/company/:symbol`。**不在 IA §4 表内**（S1.6 新增子路由，与 `/market/global`、`/market/heatmap` 同类） |
| `/market/index/:tsCode` | page | `pages/market-index`（`MarketIndexPage`） | 公开 | 指数详情（`/index/:tsCode` 的 redirect 目标） |
| `/market/hot-sectors/:category` | page | `pages/market-hot-sectors`（`MarketHotSectorsPage`） | 公开 | 热门板块与情绪；保留（Ruling 30 从 redirect 退回） |
| `/market/concept/:boardCode` | page | `pages/market-concept`（`ConceptBoardPage`） | 公开 | 概念板块；保留（Ruling 30 从 redirect 退回） |
| `/company` | page（占位） | `pages/company`（`CompanyPage`） | 公开 | 公司分析总览；占位（9 张公司卡片未落地） |
| `/company/:symbol` | page | `pages/stock-detail`（`StockDetailPage`） | 公开 | 个股详情（`/stock/:symbol` 的 redirect 目标） |
| `/industry` | page | `pages/industry`（`IndustryPage`） | `research:read` | 行业分析看板（`/research` 的 redirect 目标） |
| `/industry/:industryKey` | page | `pages/industry/workbench`（`IndustryWorkbenchPage`） | `research:read` | 行业投研工作台（`/research/:industryKey` 的 redirect 目标） |
| `/news` | page（占位） | `pages/news`（`NewsPage`） | 待定（当前无门禁） | 资讯；占位，权限口径待模块落地 |
| `/macro` | page（占位） | `pages/macro`（`MacroPage`） | 待定（当前无门禁） | 宏观分析；占位 |
| `/sentiment` | page | `pages/sentiment`（`SentimentPage`） | 需登录 | 情绪分析的深度读出台（S1.8–S1.13）：综合指数 hero（读新端点 `/api/v1/market/sentiment/composite`，明示「N/11 条腿 + 生效权重」）+ 9 个模块面板（唯一模块表 `features/sentiment/moduleTable.ts` + 唯一渲染器 `SentimentModulePanel`）；⑧ 散户舆情与 ⑩ 情绪预警与预判按 spec 就地说明未落地。**权限**：IA §1 原列「待定」，本页消费受保护的 `/api/v1/metrics/*`，故按最小一致口径取「需登录」（沿用既有 `RequireAuth`，不新增 scope） |
| `/valuegraph` | page（占位） | `pages/valuegraph`（`ValueGraphPage`） | 待定（当前无门禁） | 价值网图谱；占位 |
| `/strategy` | page（占位） | `pages/strategy`（`StrategyPage`） | 待定（当前无门禁） | 策略引擎；占位 |
| `/risk` | page（占位） | `pages/risk`（`RiskPage`） | 待定（当前无门禁） | 风险管理；占位 |
| `/trade` | page（占位） | `pages/trade`（`TradePage`） | 待定（当前无门禁） | 交易执行；占位 |
| `/search` | page（占位） | `pages/search`（`SearchPage`） | 待定（当前无门禁） | 顶栏全局搜索目标页（`?q=`）；占位，非 redirect |
| `/me` | page | `pages/me`（`MePage`） | 需登录 | 我的：`?tab=watchlist`（自选）/ `?tab=tags&name=`（标签） |
| `/market/category` | redirect | → `/market/industry` | 公开 | 旧路由（`<Navigate replace>`），下同 |
| `/index/:tsCode` | redirect | → `/market/index/:tsCode` | 公开 | 旧路由 |
| `/stock/:symbol` | redirect | → `/company/:symbol` | 公开 | 旧路由 |
| `/research` | redirect | → `/industry` | 公开 | 旧路由；目标路由自身兑现 `research:read` |
| `/research/:industryKey` | redirect | → `/industry/:industryKey` | 公开 | 旧路由；目标路由自身兑现 `research:read` |
| `/watchlist` | redirect | → `/me?tab=watchlist` | 公开 | 旧路由；目标路由自身兑现「需登录」 |
| `/tags` | redirect | → `/me?tab=tags` | 公开 | 旧路由；目标路由自身兑现「需登录」 |
| `/tags/:tagName` | redirect | → `/me?tab=tags&name=:tagName` | 公开 | 旧路由；`tagName` 由 `RedirectTo` 转义后拼进 query |

以 [`route-manifest.ts`](../../../frontend/src/app/router/route-manifest.ts) 为准；上表与它不一致时以代码为准并修正本表。十条一级导航的可见性三态（口径见 [ADR 0008](../../decisions/0008-research-read-requires-permission.md) 与 [`design/ia-investment-research-platform.md`](../../design/ia-investment-research-platform.md) §1）由清单的 `requiresAuth` / `permission` 字段承载，本表不新增任何 scope。

## 7. 与网关的关系

生产/本地经 gateway 访问（`/`→frontend、`/api`→api、`/auth`→auth-service）。前端**不接触任何 token**：凭据在 HttpOnly Cookie 中，非幂等请求需带 CSRF 令牌（见 [`../../decisions/0002-split-auth-service-and-forward-auth.md`](../../decisions/0002-split-auth-service-and-forward-auth.md) 与 [`../authentication-and-gateway.md`](../authentication-and-gateway.md)）。

直连 `:8000` 不走网关 ⇒ 没有身份上下文，需要登录态的功能必须在网关下验证。
