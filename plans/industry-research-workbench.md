# Plan: 行业投研工作台（Industry Research Workbench）

> Source PRD: [农林牧渔-养殖业-生猪养殖 v3.2 产品化 · 猪智投](../docs/农林牧渔-养殖业-生猪养殖v3.2-产品化-20260814%20-%20猪智投.md)
> 设计原型: [docs/design/prototype-pig-dashboard.html](../docs/design/prototype-pig-dashboard.html)
> 数据源调研: [docs/design/data-source.md](../docs/design/data-source.md)

> **进度备注**（2026-08-31）：P1-P4 已在本分支（feature/industry-research-p1-p4）实现。
> 外部 API 未实机验证的项（AKShare 接口名）按约定以 mock 数据源落地（`INDUSTRY_DATA_SOURCE=mock`，适配器已就绪）；
> P3 协会源抓取器接入与 P4 规则参数实机调优，待 docker compose 环境验证迁移 + ingest 后进行。
> **加固备注**（2026-09-02）：P1-P4 评审修复已落地（源优先级/rollup/months 贯通/引擎测试/读路径卫生），详见 `docs/Changelog.md` 2026-09-02 条目。

## 设计原则（组件化 / 模块化 / DRY）

1. **一套资产服务所有行业**：数据表、采集框架、API、前端组件全部行业无关；行业差异只存在于 metric registry 配置与 fetcher 适配器。接入第二个行业 = 写配置 + 写采集器，零新表、零新页面。
2. **单一读取面**：派生指标（猪粮比、头均市值）计算后统一写回指标表，所有消费方（看板、图表、规则引擎）无差别查询，不感知指标来源。
3. **优先复用既有轮子**：PagedResponse / deps 注入 / Alembic 迁移链 / QUEUES-Worker-Scheduler 双轨 / TuShare 限流重试逻辑 / 前端 enriched 双查询模式 / StateWrapper 等 UI 原语，一律复用或抽取泛化，禁止平行实现。
4. **组件 props 驱动、内容后端驱动**：前端组件不内置任何行业知识；看板区块结构、列定义、参考区间、预警阈值均由后端 payload / registry 下发。

## Architectural decisions（贯穿所有阶段的持久决策）

- **指标单表**：`industry_metrics(id, industry_key, stock_id NULL, metric_key, source, freq, period, value Numeric(18,4), unit, extra JSONB)`，`unique(industry_key, stock_id, metric_key, source, period)`，索引 `(industry_key, metric_key, period)`。行业级指标 `stock_id` 为 NULL，公司级（标的分析）带 `stock_id`——行业看板与公司对比共用一张表、一套 API。
- **指标注册表（代码即配置）**：后端 registry 模块定义 `metric_key → {名称, 单位, 频率, 数据源优先级, 源层级(official/highfreq/calc/derived), 参考区间, 展示分组}`，经 API 下发给前端渲染；`metric_key` 命名与 data-source.md 对齐。
- **政策锚点表**：`industry_reference_points(industry_key, metric_key, label, value, effective_from)`——正常保有量等随政策修订（4100→3900→3750），带生效日期，参考线随日期自动切换。
- **信号表**：`industry_signals(id, industry_key, signal_type(空仓/关注/买入/卖出), effective_date, reason, payload JSONB)`——信号历史可回测、可审计。
- **API 路由**（挂在 `api/v1/__init__.py` 既有装配处）：
  - `GET /api/v1/industries`（已产品化行业列表）
  - `GET /api/v1/industries/{key}/metrics/latest`（全部最新值，含 source/asof/层级）
  - `GET /api/v1/industries/{key}/metrics/{metric_key}/history?range=`（时序）
  - `GET /api/v1/industries/{key}/dashboard`（看板聚合：指标 + 周期阶段 + 信号 + 仓位，一次请求）
  - `POST /api/v1/industries/{key}/metrics:batch`（人工 / CSV 导入通道）
- **采集双轨**：复用既有模式——`QUEUES` 注册 `"industry_metrics.fetch"` + 新 Worker 注册进 `workers/runner.py` 列表 + APScheduler CronTrigger 注册进 `scheduler/runner.py:create_scheduler`；job 与 worker 共用同一 service ingest 方法。
- **采集基建 DRY 抽取**：从 `TuShareClient` 抽取限流/重试/`asyncio.to_thread` 包装为通用基类（`RateLimitedSyncProvider`），TuShare 与 AkShare 客户端共同继承；每个数据源一个薄 fetcher 适配器 + fixture 单测，上游接口变动影响面隔离。
- **前端路由**：`/research`（行业列表）、`/research/:industryKey`（工作台，四 Tab：投资看板 / 行业知识库 / 行情调研追踪 / 交易管理）；组件落 `features/industry-research/`，新 UI 原语落 `shared/ui/`；图表统一走新 `shared/ui/EChart` 封装。
- **分期对应数据源分层**：P1-2 = L1 免费价格类；P3 = L2 官方产能；P4 = 规则引擎（零外部源）；P5 = L3 人工库 + Tushare 复用；L4 付费源不在本计划内（后置评估）。

---

## Phase 1: 骨架贯通 — 价格类指标端到端（tracer bullet）

**User stories**: 投资看板 · 综合（行业头均市值除外）、核心指标速览中的价格项

### What to build

一条最薄的端到端竖切：Alembic 迁移建 `industry_metrics` + `industry_reference_points`；registry 登记 pig 首批价格类指标（`hog_price` / `corn_price` / `soybean_meal_price` / `pork_wholesale` / `lh_future_main`）；`AkShareClient`（生意社历史序列）+ 限流基类抽取重构 `TuShareClient`；ingest service（拉取→清洗→upsert，幂等）；派生猪粮比落表；QUEUES / Worker / Scheduler 三处注册；三个读端点 + batch upsert 端点；前端 `/research` 列表页 + 工作台骨架（四 Tab，后三个占位）+ 综合指标带（`IndicatorStrip` / `IndicatorCard` / `SourceBadge`）接真实 API。

### Acceptance criteria

- [ ] `docker compose up` 后 migrate 服务自动应用迁移，两张表存在
- [ ] 手动触发采集任务可回补生意社 ≥3 年历史，重跑幂等（无重复行）
- [ ] `GET /metrics/latest` 返回含 source / asof / 层级徽章的最新值；猪粮比以 `derived` 源出现
- [ ] 打开 `/research/pig` 指标带显示真实数据，源徽章由 registry 驱动
- [ ] `TuShareClient` 与 `AkShareClient` 共用同一限流/重试基类，无重复实现
- [ ] batch upsert 端点可通过 curl 导入人工数据并幂等

## Phase 2: 走势图表 — history API + 通用图表组件

**User stories**: 生猪价格 vs 行业成本走势（先用价格序列落地）、核心指标速览

### What to build

`/metrics/{key}/history` 端点（range 参数 + Redis 缓存）；`shared/ui/EChart` 统一封装（loading / resize / 主题 / tooltip 风格），看板两张图先落地已有价格序列（生猪价格走势、玉米/豆粕成本对照、猪粮比历史）；`reference_points` 接入参考线/参考带（带生效日期）；核心指标速览网格（`IndicatorGrid`，10 项，预警标签由 registry 参考区间计算）。

### Acceptance criteria

- [ ] history 端点返回升序序列，Redis 命中后二次请求明显变快
- [ ] `EChart` 被 ≥2 个图表复用，新增图表不手写 `echarts.init` 样板
- [ ] 猪粮比图显示预警参考带，阈值改 registry 配置即生效，前端零改动
- [ ] 速览网格预警标签（如"二级预警"）由阈值计算得出，非硬编码文案

## Phase 3: 官方产能数据 — 能繁存栏 + 多源分级

**User stories**: 能繁母猪存栏趋势、数据权威性使用原则（多源交叉验证）

### What to build

协会源 fetcher（pig.caaa.cn 月度"全国生猪产品数据"HTML 解析 + fixture 单测 + 月度 cron）；统计局季度绝对数走 CSV 导入脚本 → batch upsert；能繁指标双 source 存储（月度环比序列 / 季度末绝对数序列），latest 端点按 registry 源优先级返回；能繁趋势柱状图 + 正常保有量参考线（3750/2026 修订，`effective_from` 驱动切换）；指标卡多源并列展示（Drawer 展开对比曲线）。

### Acceptance criteria

- [ ] `sow_inventory` 含两个 source，官方源按优先级胜出
- [ ] 参考线随 `effective_from` 自动切换（造一条未来生效的测试数据验证）
- [ ] CSV 脚本幂等回补 2018 至今约 100 个月度点
- [ ] 协会源解析有 fixture 单测；单源抓取失败不影响其他采集任务
- [ ] 指标卡点击可看多源对比，源徽章区分官方基准/高频参考

## Phase 4: 规则引擎 — 周期判定 + 交易信号 + 仓位建议

**User stories**: 猪周期阶段定位、交易信号面板、仓位管理建议

### What to build

`industry_signals` 表迁移；声明式规则 registry（输入指标快照 → 输出周期阶段/信号/仓位比例，纯函数）；dashboard 聚合端点一次返回看板全部状态；信号生成 job（指标 ingest 完成后触发，变更写入历史）；前端 `CyclePhaseStrip`（四阶段相位条）、`SignalPanel`（当前信号 + 历史时间线）、`PositionAdviceBar`（仓位堆叠条）。

### Acceptance criteria

- [ ] 规则引擎纯函数单测覆盖（给定指标快照 → 期望阶段/信号/仓位）
- [ ] `dashboard` 端点一次返回 metrics / cycle_phase / signal / positions 四块，前端单次 React Query 拉齐
- [ ] 信号变更写入历史表，时间线倒序渲染，含变更理由
- [ ] `/research/pig` 看板信息结构与原型 prototype-pig-dashboard.html 一致
- [ ] 规则参数（阈值/权重）在 registry 可调，改配置不改代码

## Phase 5: 标的分析 — 公司指标 + 现有行情打通

**User stories**: 公司核心指标对比、生猪行业 ETF、可转债

### What to build

`industry_metrics` 启用 `stock_id`：出栏量 / 完全成本 / 负债率等 L3 人工指标走 batch 导入；行情 / 市值 / PE 经 `stock_id` 关联既有 enriched 链路 join 展示（不重复采集）；头均市值 + 历史分位派生落表；`CompanyComparisonTable`（列由 registry 定义、综合评分可排序、行点击跳 `/stock/:symbol`）；ETF / 可转债表（Tushare 现有接口）；双向导航：申万 L3 页投研 banner + stock-detail 行业入口。

### Acceptance criteria

- [ ] 同一张 `industry_metrics` 服务行业级与公司级查询，API 无 per-company 特例分支
- [ ] 对比表新增一列 = registry 加一条定义，前端零改动
- [ ] 行点击跳转既有 `/stock/:symbol` 详情页
- [ ] 头均市值随出栏量导入自动重算
- [ ] 申万 L3"生猪养殖"页出现"进入投研工作台"banner

## Phase 6: 知识库 + 泛化验证收尾

**User stories**: 行业知识库（思维导图、利益相关机构图谱、数据权威性原则）、多行业复制

### What to build

`industry_knowledge` 内容表（机构 / 图谱关系 / 使用原则，JSONB，纯内容管理 + 简单 CRUD）；知识库 Tab 渲染机构图谱（分组卡片 + 权威性徽章）与思维导图（EChart tree）；`/research` 列表页完善（行业卡片：周期阶段 / 当前信号 / 指标覆盖度）；**泛化验证**：以最小配置（2-3 个价格指标）新增一个演示行业，确认零前端新页面跑通 列表 → 工作台 全链路；文档收尾（勾选计划、更新 Changelog）。

### Acceptance criteria

- [ ] 知识库 Tab 渲染机构图谱与数据权威性原则（官方 / 协会 / 平台 / 期货分组）
- [ ] 新增演示行业仅需：registry 配置 + 一个 fetcher + scheduler 注册，全程无前端改动
- [ ] `data-source.md` 与 registry 的 `metric_key` 命名一致（交叉检查）
- [ ] 各阶段验收项在本文档勾选留痕，Changelog 记录
