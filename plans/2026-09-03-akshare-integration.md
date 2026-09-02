# AKShare 真实数据接入 + Playwright 浏览器验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** 将已实机验证的 AKShare/搜猪网接口接入行业指标 ingest，无法覆盖的指标保留 mock；新增 Playwright 浏览器级 E2E 验证工作台 UI。

**Architecture:** 数据面三处改动——AkShareClient 换成验证过的四个接口、fetcher 按指标适配（可注入 client 供单测）、mock 清除策略从"整行业清除"改为"按已覆盖指标清除"。验证面新增 frontend Playwright 套件（依赖运行中的 docker 栈）。

**Spec:** 本文件 §数据源测绘（2026-09-03 实机验证，akshare 1.18.94）。

## 数据源测绘（实机验证结果 — 绑定）

| metric_key | 接入 | 接口（已验证） | 形状 | 频率/窗口 |
|---|---|---|---|---|
| hog_price | ✅ soozhu | `ak.spot_hog_year_trend_soozhu()` | 200×2，`日期/价格`，元/kg | 日度，当年起（逐年累积） |
| corn_price | ✅ soozhu | `ak.spot_corn_price_soozhu()` | 15×2，`日期/价格`，元/kg | 日度，每次 15 天（逐日累积） |
| soybean_meal_price | ✅ soozhu | `ak.spot_soybean_price_soozhu()` | 15×2，`日期/价格`，元/kg | 同上 |
| lh_future_main | ✅ sina | `ak.futures_zh_daily_sina(symbol="LH0")` 取 `close` | 1370×8（2021-01-08→今） | 日度全历史 |
| pork_wholesale | ❌ mock | 农业农村部无 AKShare 接口（总指数≠猪肉专项） | — | — |
| piglet_price_15kg | ❌ mock | `spot_hog_three_way_soozhu` 口径疑似"元/头"，与 registry"15kg 元/kg"不符 | 15×2（~260） | — |
| sow_inventory | ❌ mock | L2 协会/统计局抓取器属 P3，未建 | — | — |
| industry_cost_avg / msy / psy / feed_meat_ratio | ❌ mock | L3 人工维护，走既有 batch 导入通道 | — | — |
| （参考）hog_price 长历史备选 | 后置 | `ak.index_hog_spot_price()` 周度指数 585 期（2015→今），含成交均价 | — | 不接入，留档 |

已证伪：`futures_spot_sys`（生意社现期图，即原 fetcher 猜测的 `spot_price_qh` 真名）对生猪/玉米均抛 `AttributeError: 'NoneType' object has no attribute 'find_all'`（上游页面改版）——100ppi 路径废弃。

## 设计决策

1. **source 命名**：hog/corn/soybean 的 source 从 `akshare_100ppi` 改为 **`akshare_soozhu`**（registry sources 同步，mock 仍垫底）；lh_future_main 维持 `akshare_sina`。
2. **清除策略修订（修订 C2 裁定）**：真实源 ingest 改为**只清除本次已覆盖指标的 mock/derived 行**——hog/corn/soybean/lh_future_main 走真实，sow/cost/仔猪等未覆盖指标**保留 mock**（"无法补齐的继续 mock"）。当 hog_price 与 corn_price 同时被覆盖时，连同清除 `hog_corn_ratio` 的 derived 行（其输入已真实化，重算即真实值）。
3. **可测性**：`_fetch_akshare_rows(cfg, months, client=None)` 允许注入假 client，fixture DataFrame 单测；数值健全性护栏（现货 0<v<100 元/kg、期货 0<v<100000 元/吨）。
4. **部署开关**：代码默认 `INDUSTRY_DATA_SOURCE=mock` 不变；`backend/.env` 写 `INDUSTRY_DATA_SOURCE=akshare`（本地栈真实化）。akshare 进 pyproject 运行依赖（`uv add akshare`）。
5. **E2E 源断言放宽**：`test_industry_e2e.py` 允许 hog_price source ∈ {akshare_soozhu, mock}，保持对栈当前配置无感。

## Global Constraints

- 后端测试：`cd backend && uv run pytest tests/test_industry_fetchers.py tests/test_industry_source_priority.py ... -q`；纯单测不触网（akshare 调用全部 mock 注入）。
- E2E：pytest `-m e2e` 与 Playwright 均依赖运行中的 docker 栈（http://localhost:3000）。
- 不改 docker-compose / Dockerfile（.env 属部署配置可改）；中文注释；conventional commits。

---

### Task 1: 后端真实源接入（AkShareClient + fetcher + 按指标清除 + 测试 + .env）

**Files:**
- Modify: `backend/app/core/providers/akshare_client.py`（四个验证过的接口方法，删除 100ppi/TODO(api-verify)）
- Modify: `backend/app/services/industry_metric_service.py`（_fetch_akshare_rows 重写、purge 按指标、PURGE 逻辑更新）
- Modify: `backend/app/services/industry_registry.py`（sources: akshare_soozhu）
- Modify: `backend/app/repositories/industry_metric_repo.py`（delete_rows_by_source 加 metric_keys 过滤）
- Modify: `backend/pyproject.toml` + `uv.lock`（uv add akshare）、`backend/.env`（INDUSTRY_DATA_SOURCE=akshare）
- Modify: `backend/tests/test_industry_source_priority.py`（PURGE 断言更新）、`backend/tests/test_industry_e2e.py`（源断言放宽）
- Create: `backend/tests/test_industry_fetchers.py`（假 client fixture 单测）

要点：fetcher 表驱动 `(metric_key, source, client_method, date_col, val_col, value_max)`；ingest 返回 dict 增 `"covered_metrics"`；`_covered_purge_keys(covered)` 纯函数（含 hog+corn→ratio 联动）+ 单测；AkShareClient 方法带"已验证 2026-09-03, akshare 1.18.94"注释与窗口说明。

### Task 2: Playwright 浏览器 E2E（frontend）

**Files:**
- Create: `frontend/playwright.config.ts`、`frontend/e2e/research.spec.ts`
- Modify: `frontend/package.json`（devDep @playwright/test + script `test:e2e`）

要点：baseURL http://localhost:3000；用例：/research 行业卡片与覆盖度；/research/pig 标题标签（周期阶段/当前信号）、指标带含"生猪均价"且数值非空、相位条 4 阶段且 1 个 active、信号面板当前信号、仓位条 3 段、≥2 个 canvas（EChart）、核心指标速览非空、知识库/调研 Tab 占位文案；断言用角色/文本定位（中文标签），不依赖具体数值。chromium 安装失败时用 `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/`。`tsc -b` 构建不得受 e2e 目录影响（确认 tsconfig include 范围）。

### Task 3: 实机验证 + docs（controller 执行）

重建镜像 → .env 已切 akshare → 重启栈 → 触发 ingest（验证 soozhu/sina 真实落库、未覆盖指标留 mock、看板 SourceBadge 混合展示）→ pytest 全套 + Playwright 全绿 → Changelog/best-practices → 提交。
