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
| 路由 | react-router-dom（`app/router/index.tsx`，页面全部 `lazy` + `Suspense`） |
| 静态校验 | `tsc --noEmit` + `npm run check:design`（**不引入 eslint**，见 [ADR 0006](../../decisions/0006-no-eslint-tsc-as-static-check.md)） |
| 生产托管 | nginx（`frontend/Dockerfile` 的 runtime 阶段只打包 `dist/`） |

## 2. 目录结构（feature-sliced）

```
frontend/src/
├── app/            # 应用装配：router / layouts / theme（theme.ts + theme-context.tsx）
├── pages/<路由>/    # 路由级页面，只做编排（组合 features + shared），不含业务逻辑
├── features/<域>/   # 业务域：auth · concept · industry-research · market · search · stock-detail · watchlist
├── shared/
│   ├── api/        # 每个域一个 api 模块（market.ts / marketData.ts / limitUp.ts / industryResearch.ts …）
│   ├── ui/         # UI 原语：EChart · StateWrapper · DegradedNotice · FreshnessNote · NumberText · ChangeText · DataRow · SectionCard
│   ├── types/      # 共享类型
│   └── config/
└── scripts/check-design-tokens.mjs   # 设计令牌门禁（涨跌色对比度 + theme.ts ↔ theme.css 一致）
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

| 路径 | 页面 | 说明 |
|---|---|---|
| `/` | `pages/landing` | 宣传页，公开、独立布局（不套 `MainLayout`） |
| `/login` | `pages/login` | 登录 |
| `/market` | `pages/market` | 行情台首页 |
| `/market/category` | `pages/market-category` | 申万分类浏览 |
| `/market/industry/:level1Code` · `/market/industry/:level1Code/:level2Code` | `pages/market-industry-level2` · `market-industry-level3` | 申万一级/二级下钻 |
| `/market/hot-sectors/:category` | `pages/market-hot-sectors` | 热门板块与情绪 |
| `/market/concept/:boardCode` | `pages/market-concept` | 概念板块 |
| `/index/:tsCode` | `pages/index-detail` | 指数详情 |
| `/stock/:symbol` | `pages/stock-detail` | 个股详情 |
| `/watchlist` | `pages/watchlist` | 自选股（需登录） |
| `/tags` · `/tags/:tagName` | `pages/tags` · `tags-detail` | 自定义标签 |
| `/research` · `/research/:industryKey` | `pages/research` · `research-workbench` | 行业投研工作台 |

以 `app/router/index.tsx` 为准；上表与它不一致时以代码为准并修正本表。

## 7. 与网关的关系

生产/本地经 gateway 访问（`/`→frontend、`/api`→api、`/auth`→auth-service）。前端**不接触任何 token**：凭据在 HttpOnly Cookie 中，非幂等请求需带 CSRF 令牌（见 [`../../decisions/0002-split-auth-service-and-forward-auth.md`](../../decisions/0002-split-auth-service-and-forward-auth.md) 与 [`../authentication-and-gateway.md`](../authentication-and-gateway.md)）。

直连 `:8000` 不走网关 ⇒ 没有身份上下文，需要登录态的功能必须在网关下验证。
