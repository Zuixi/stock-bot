# 前端 e2e（Playwright）

> 先读 [`index.md`](./index.md#术语消歧务必区分)：本仓库有两个都叫 "e2e" 的东西。**本文只讲前端 Playwright**（`frontend/e2e/*.spec.ts`）。后端 `e2e` marker 见 [`backend.md`](./backend.md)。

## 现状

| 项 | 值 |
|---|---|
| 配置 | `frontend/playwright.config.ts`（`testDir: ./e2e`，`timeout 30s`，`retries 0`，`reporter: list`） |
| baseURL | `process.env.E2E_BASE_URL ?? http://localhost:3000` |
| 运行 | `cd frontend && npm run test:e2e` |
| **是否在 CI** | **不在**（`ci.yml` 无 Playwright job）—— 起栈依赖真实 DB 与登录态，故列为手动档 |
| 用例 | `auth` · `conceptBoard` · `darkmode` · `kline` · `landing` · `limitUpSentiment` · `marketDataFace` · `marketPolling` · `marketTrust` · `navigation` · `public-homepage` · `research` · `userIsolation` |

## 起栈前提

用例默认打 `:3000`（Vite dev server）。可按需指向其他环境：

```bash
cd frontend && npm run dev            # 终端 A
cd frontend && npm run test:e2e       # 终端 B

# 指向已在运行的栈（如 gateway:80 或预发布域）
E2E_BASE_URL=http://localhost npx playwright test
```

涉及登录态与用户隔离的用例（`auth` / `userIsolation`）需要可用的 gateway 与 auth 链路，`E2E_BASE_URL` 必须指到**经网关**的地址，而不是 Vite dev server 直连 `:8000`（直连没有身份上下文）。

## 断言规范

- 优先 role/text 语义选择器（`getByRole`、`getByText`），少用 CSS 类名 —— 设计令牌与类名会变
- **注意 strict mode**：`getByText("xx")` 命中多个节点会直接失败；同类文案（如"涨"字）在页面里常有多个，用 `getByRole("radio")` 或 scope 到容器
- 断言可见性用 `toBeVisible` / `toContainText`，不要用 `waitForTimeout` 硬等（并行任务共享真实数据，时间断言最容易 flaky）
- 与数据相关的断言要避开"当日实时值"，改用结构/存在性断言（实时数据在盘中会变）

## 何时该写 e2e、何时不该

| 场景 | 用 |
|---|---|
| 纯计算/格式化逻辑（单位、涨幅、格式化函数） | 后端或前端**单元测试**（`tsc` 覆盖类型，逻辑用断言） |
| API 契约与字段（含 `asOf` / `stale_days` / 信封形状） | 后端集成测试（更快也更稳） |
| 跨页导航、登录/登出闭环、真实交互（图表切换、表格排序、下钻） | **e2e** |
| 主题与对比度（涨跌色可读性） | `npm run check:design`（机械门禁）+ `darkmode` spec 兜底视觉 |

## 失败产物

Playwright 失败时会把截图与 trace 落在 `frontend/test-results/`（该目录已被 `.dockerignore` 排除，不会被塞进镜像）。排查顺序：先看截图确认页面实际状态，再看 trace 里的网络请求确认是数据问题还是渲染问题。

真实数据驱动的用例出现失败时，先判断是**代码回归**还是**数据状态**（上游停更、当日盘中、实验数据未回补）。历史上出现过"基线失败 3 个"其实是暗色主题白底这类既有问题，与本轮改动无关 —— 判断回归时先对齐基线，不要把所有失败都算作当前改动的账。
