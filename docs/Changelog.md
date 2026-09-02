# Project Changelog
项目所有重大更新必须记录在这里，补充在文档最后面，采用如下形式：
```markdown
## {{日期}} - {{更新模块}}
- 一句话总结更新的内容
- 涉及模块有哪些，不需要列出具体文件，只需要列出模块名
```

## 2026-05-09 - 数据回填管道完善（APScheduler 定时任务 + 手动触发 API）
- **问题**：每日增量数据回填未纳入 APScheduler，导致系统只依赖启动时一次性回补；缺失手动触发 daily_basic 回填的 API 端点
- **修复**：
  - 新增 APScheduler 定时任务 `daily_quotes_backfill_job`（16:30）和 `daily_basic_backfill_job`（16:45），工作日 16:30-16:45 执行昨日数据回填
  - 新增 `POST /api/v1/tasks/fetch-daily-basic` 端点，可手动触发 daily_basic 全量回填
  - 新增 `FetchDailyBasicRequest` Schema + `trigger_fetch_daily_basic()` Service 方法
  - `DailyBasicWorker` 已支持队列消费，只需发送对应 payload 即可触发
- 涉及模块：backend/scheduler/jobs, backend/scheduler/runner, backend/api/v1/tasks, backend/services/task_service, backend/schemas/task

## 2026-05-09 - 股票详情页估值指标数据缺失修复
- **问题**：`FundamentalCards`（估值目标/成长与盈利卡片）全为空白，根因是 `fetchStockBySymbol()` 调用基础 `/stocks/{symbol}` 接口返回 `StockOut`（无金融字段），且 `roe/revenueGrowth/profitGrowth` 后端未入库
- **修复**：
  - 后端新增 `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/enriched` 端点，返回 `StockEnrichedOut`（含 latest_price/pe_ttm/pb/total_mv/circ_mv）
  - 后端 `get_stock_enriched()` service 复用 `get_stocks_enriched_by_symbols()` SQL，TTL 300s
  - 前端新增 `fetchStockEnrichedBySymbol()` 并切换 stock-detail 页至 enriched 接口
  - `roe/revenueGrowth/profitGrowth` 字段 Phase 2 待接入 TuShare `fina_indicator`/`profit_data`
- 涉及模块：backend/services/stock_service, backend/api/v1/stocks, frontend/shared/api/stocks, frontend/pages/stock-detail

## 2026-05-08 - 申万分类详情页金融数据空白修复（含完整数据链路闭环）
- **问题**：申万行业详情页个股表格金融列全空（最新价/涨跌幅/成交额/市值/PE），根因有三层：
  1. 前端 `mapBackendStock` 把金融字段硬编码 `undefined`
  2. SW 行业 API 只返回 `StockOut`（纯元数据），不含行情/基本面
  3. `daily_basic_indicators` 表为 0 行 → PE/市值无数据源
- **修复**：
  - **数据展示**：新增 `StockEnrichedOut` Schema + 批量 enriched API（DISTINCT ON JOIN stocks + daily_quotes + daily_basic_indicators 一次查询），前端 `fetchSwLevel*Stocks` 切至 `/enriched` 端点
  - **懒加载**：前端采用双 `useQuery` 渐进式渲染（basic 秒出骨架 → enriched 异步填金融列），避免加载卡顿
  - **数据源**：手动回补 `daily_basic_indicators` 近 1 年数据（242 个交易日，覆盖 5400+ 只股票，69 万行），验证 TuShare `daily_basic` API 全链路可用
  - **启动加速**：`data_init` 中 `daily_basic` 回补改为与 `daily_quotes` 并行执行（`asyncio.create_task` + `gather`），内部 3 并发（`Semaphore(3)`），从"排队数小时等不到"→"启动 3 分钟跑完"
  - **Worker 修复**：`QUEUES` 字典补全 `daily_basic.fetch` 映射，终止 Worker crash loop
- 涉及模块：backend/schemas/stock, backend/services/market_service, backend/services/data_init, backend/api/v1/market, backend/core/mq, backend/workers/daily_basic_worker, frontend/shared/api/swIndustry, frontend/pages/market-industry-level2, frontend/pages/market-industry-level3

## 2026-04-20 - 申万行业分类
- 基于本地 XLS/XLSX 文件实现申万三级行业分类与成分股联动，替代原 TuShare API 内存缓存方案
- 涉及模块：backend/models、backend/services、backend/api、frontend/features/market、frontend/pages/market-industry-*

## 2026-04-20 - 申万导入链路优化
- 新增“SQL 种子优先、XLS 解析兜底”的双路径导入机制：首次解析后自动导出 `sw_seed.sql`，后续部署可直接导入 SQL，显著降低启动导入耗时
- 涉及模块：backend/services、backend/config、docker-compose、backend/.dockerignore、backend/data

## 2026-04-21 - 分类市场分页修复
- 修复分类市场表格切换“条/页”后因固定 pageSize 被重渲染覆盖导致显示条数不变化的问题，并将分页状态改为受控持久化。
- 涉及模块：frontend/features/market、docs

## 2026-04-21 - 分类市场总数口径修复
- 修复分类市场“总条数固定 300”的问题：新增跨交易所分页接口并改为服务端分页，前端分页总数改用后端 `total`，确保 20/50/100 条切换与页码显示一致。
- 涉及模块：backend/api、frontend/shared/api、frontend/pages/market-category、frontend/features/market、docs

## 2026-04-21 - 申万分类缺失兜底修复
- 修复申万行业树总数与股票总数不一致问题：行业树计数统一按 `stocks` 口径统计，并新增 `OTHER(其他)` 一级分类承接未映射到有效三级行业的股票。
- 涉及模块：backend/services/market、frontend/pages/market-industry-*、docs

## 2026-04-21 - 前端代理 502 修复
- 修复 frontend 经 Nginx 代理后端接口偶发全量 502 的问题：启用 Docker DNS 动态解析，避免 backend 容器重建后 frontend 继续使用过期 upstream IP。
- 涉及模块：frontend/nginx、docker-compose、docs

## 2026-04-22 - SSE 交易所指数实时数据采集
- 整合 SSE 官方 JSONP 接口爬虫到 backend 服务：新建 `sse_index_snapshots` 表存储盘中快照，异步 httpx 爬取服务含完整反爬策略（UA 轮换/cookie 持久化/随机 jitter/指数退避），APScheduler 定时调度（交易时段 9:30-15:00 每 10min + 15:30 收盘补采），支持历史时间戳回填 4/1-4/21 数据，前端 MarketOverview 自动合并 SSE 实时数据优先展示
- 涉及模块：backend/models、backend/repositories、backend/services、backend/schemas、backend/api、backend/scheduler（新增）、docker-compose、frontend/shared/types、frontend/shared/api、frontend/features/market

## 2026-04-22 - 三年日K启动补齐
- 启动初始化由“固定近30交易日”升级为“按股票检查近3年覆盖并仅回补缺口”，采用小并发分批逐股拉取 TuShare 日线，兼顾补齐效率与服务启动稳定性。
- 涉及模块：backend/services、backend/repositories、backend/core/providers、backend/tests、docs

## 2026-04-22 - 股票自定义申万分类标签
- 新增 stock_custom_sw_tags 表支持每只股票自定义多个 SW 二级/三级行业标签；"其他"一级行业按股票自带 industry 字段自动分组为二级子分类；股票详情页新增分类标签展示与编辑功能。
- 涉及模块：backend/models、backend/services、backend/api、backend/migrations、frontend/shared/api、frontend/shared/types、frontend/features/stock-detail、frontend/pages/market-industry-level2、frontend/pages/stock-detail

## 2026-04-23 - 申万“其他”二级下钻交互统一
- 修复申万“其他”一级分类下二级卡片点击行为与其余分类不一致的问题，统一为路由下钻到独立详情页，避免在一级页内混合展示子分组个股。
- 涉及模块：frontend/pages/market-industry-level2、docs

## 2026-04-23 - 自定义申万三级联动修正
- 修正股票详情“编辑自定义申万分类”弹窗联动失效问题：三级下拉严格按已选二级实时过滤，未选二级时禁用三级下拉并提示“请先选择二级行业”。
- 涉及模块：frontend/features/stock-detail、docs

## 2026-04-23 - 自定义申万标签与行业详情联动修复
- 修复行业详情页未包含股票自定义申万二/三级标签的问题：行业树统计、层级个股列表与 OTHER 兜底口径统一合并官方成分与自定义标签。
- 涉及模块：backend/services/market、docs

## 2026-04-23 - 自定义标签变更即时刷新修复
- 修复修改股票自定义申万标签后行业计数和个股列表不即时刷新问题：前端统一失效申万树/层级查询缓存，后端同步清理 `market:sw-tree` Redis 缓存。
- 涉及模块：frontend/features/stock-detail、backend/api、docs

## 2026-04-23 - 用户自定义股票标签功能
- 新增用户自定义标签系统：每只股票可添加任意文字标签，独立于申万分类体系；新增标签列表页以 Card 网格展示所有标签及股票数量，点击可下钻查看该标签下的股票列表。
- 涉及模块：backend/models、backend/services、backend/api、backend/migrations、frontend/shared/api、frontend/shared/types、frontend/features/stock-detail、frontend/pages/tags、frontend/pages/tags-detail、frontend/app/router、frontend/app/layouts、docs

## 2026-05-09 - /market/category 路由股票列表金融数据空白修复
- 根因：`/api/v1/exchanges/stocks` 端点返回 `StockOut`（仅 `stocks` 表字段），前端 `mapBackendStock()` 把所有行情字段硬编码为 `undefined`。修复：新增 `/exchanges/stocks/enriched` 端点返回 `StockEnrichedOut`（JOIN daily_quotes + daily_basic_indicators），前端 CategoryPage 采用 dual useQuery progressive loading（基础查询先渲染骨架，enriched 查询异步填充金融列），复用 `market-industry-level2/3` 页已有的 enriched 基础设施。
- 涉及模块：backend/services/stock_service、backend/api/v1/stocks、frontend/shared/api/stocks、frontend/shared/api/swIndustry、frontend/pages/market-category

## 2026-05-09 - 股票列表 enriched 查询性能修复（DISTINCT ON → LATERAL JOIN）
- **问题**：SW 行业详情页/market category 页的 enriched 查询（最新价/涨跌幅/PE/市值）耗时 3.5s，比 basic 查询（27ms）慢 130 倍，导致表格金融列晚 2-3s 才出现
- **根因**：`_GET_ENRICHED_SQL` 使用 `DISTINCT ON (stock_id)` 子查询对 `daily_quotes`（3.8M rows）和 `daily_basic_indicators`（1.4M rows）做**全表 Seq Scan + Sort**，WHERE 条件未推入子查询，3.8M 行排序后才能与目标 stocks JOIN
- **修复**：将 3 个 `DISTINCT ON` 子查询改为 `LATERAL (SELECT ... WHERE stock_id = s.id ORDER BY trade_date DESC LIMIT 1)`，利用已有的 `idx_daily_quotes_stock_date` 和 `idx_daily_basic_stock_date` 复合索引，每个 stock 仅做 1 次 Index Scan Backward
- **效果**：enriched 查询延迟 3.5s → 23ms（152x 提升），全过程无需新增索引
- 涉及模块：backend/services/market_service

## 2026-05-09 - 表格排序双组件 Bug 修复（全局排序 + 默认数据显示）
- **问题 1 (StockTable)**: `sorter: true` 用字符串比较覆盖父组件数值排序 → 排序错乱；分页数据只排序当前页
- **问题 2 (WatchlistTable)**: 用 `fetchStockBySymbol`（basic 端点）→ 金融字段全 `undefined` → 排序无效
- **问题 3 (market-category 默认不显示)**: 默认 `sortBy="symbol"` 时向后端传 `sort_by=symbol` → basic 和 enriched 返回不同页股票 → merge 失败 → 金融列空白
- **修复**:
  - StockTable 改为 controlled sorter：`sortBy`/`sortOrder` props 控制排序指示器，父组件 `applySort` 负责实际排序
  - WatchlistTable 切换 `fetchStockEnrichedBySymbol`
  - market-category：默认 symbol 排序不传 sort 参数（basic/enriched 同页），金融排序切 enriched-only 模式
  - 后端 enriched 端点加 `sort_by`/`sort_order` → 全量 enrich + sort + paginate（2312 股 ~194ms）
- 涉及模块：frontend/features/market/StockTable, frontend/features/watchlist/WatchlistTable, frontend/pages/market-category, frontend/pages/market-industry-level2/3, frontend/pages/tags-detail, backend/api/v1/stocks, backend/services/stock_service, backend/schemas/stock

## 2026-08-30 - 猪智投·投资看板高保真原型（投研产品化第一步）
- **背景**：基于《农林牧渔-养殖业-生猪养殖 v3.2 产品化》PRD，设计行业投研工作台的「投资看板」页面并产出可交互原型
- **产出**：单文件 HTML 原型（antd v5 视觉体系 + ECharts，mock 数据 37 个月），包含：工作台状态栏与四 Tab 架构、综合指标带（含官方基准/高频参考/测算三级数据源徽章 + 迷你走势）、猪周期四阶段相位波（当前萧条磨底）、交易信号时间线、价格 vs 成本对比图、能繁存栏趋势图、仓位管理建议（50/30/20 堆叠条）、10 项核心指标速览
- **设计约定**：结论先行（周期阶段/信号/仓位置顶）、证据下钻（图表验证）、页面通用组件化以便泛化到其他行业；下一阶段按此布局迁移为 React 页面（/research/:industryKey）
- 涉及模块：docs/design（原型）、frontend（后续接入）

## 2026-08-31 - 行业投研工作台实施计划 + 数据源调研 + AGENTS.md 修正
- **产出**：
  - `plans/industry-research-workbench.md`：6 阶段 tracer-bullet 实施计划（骨架贯通→图表→官方产能→规则引擎→标的分析→知识库+泛化验证），核心架构决策：指标单表 industry_metrics（行业/公司级共用）、metric registry 代码即配置、政策锚点带生效日期、采集双轨复用、前端组件行业无关
  - `docs/design/data-source.md`：猪智投数据源四层调研（L1 免费自动 AKShare 价格类 / L2 官方半自动产能 / L3 人工 batch 导入 / L4 付费墙后置），含 metric_key 命名、派生指标定义与口径坑（能繁双口径、保有量锚修订史）
  - 根 `AGENTS.md`：修正前端技术栈过时描述（Tailwind+shadcn → 实际 antd v5），补充后端分层/双轨采集与关键文档索引；`frontend/AGENTS.md` 修正 best-practice.md 断链
- 涉及模块：docs/design, plans, AGENTS.md, frontend/AGENTS.md

## 2026-09-02 - 猪周期规则引擎复苏分支加固 + 纯函数测试套件
- 复苏分支与萧条期左侧"关注"信号新增盈亏平衡确认（price≥cost 或 ratio≥6 任一口径非空），杜绝关键指标全缺失时仅凭能繁去化误判复苏/发左侧信号；新建 21 项无 DB 纯单测锁定行为
- 涉及模块：backend/services/cycle_engine, backend/tests

## 2026-09-02 - 行业指标源优先级修复（mock 永远垫底 + 真实源落库后清除演示数据）
- registry 各指标 sources 重排为真实源优先、mock 永远垫底，并补登记 akshare_sina（此前 AKShare 期货写入的源名从未注册、mock 排首位导致切换真实源后看板仍裁决出 mock 行）；`_pick_latest` 对未登记源兜底改为取最新 period（确定性）；真实源成功落库后清除该行业全部 mock 行并在 ingest 返回中回报 `purged_mock`；新增 4 项纯单测锁定排序与裁决不变量
- 涉及模块：backend/services/industry_registry, backend/services/industry_metric_service, backend/repositories/industry_metric_repo, backend/tests

## 2026-09-02 - 行业指标日度→月度 rollup + latest 按注册频率裁决
- 日度指标（hog_price/corn_price）ingest 时按"每月最后一个非空日度值"补写月度行（period=月末、source 不变、extra 标记 rollup），月度趋势图不再因真实源只写日度行而空白；`latest_rows_by_metric` DISTINCT ON 增加 freq 维度（每 metric×source 最多 daily/monthly 两行），`_pick_latest` 先按 registry 注册频率过滤再走源优先级，月末日期的月度行不再压过当日日度行；新增 4 项纯单测
- 涉及模块：backend/services/industry_registry, backend/services/industry_metric_service, backend/repositories/industry_metric_repo, backend/tests

## 2026-09-02 - industry_metrics 唯一约束纳入 freq（勘误）
- 更正：上一条 rollup 记录遗漏了已知撞键问题——唯一约束 (industry_key, stock_id, metric_key, source, period) 不含 freq 时，月末同时承载日度观测与月度归档行（mock 批、rollup+猪粮比派生批均会出现），同批 upsert 触发 PG "ON CONFLICT DO UPDATE command cannot affect row a second time" 中断事务，且月度行会覆写日度行的 freq 致 rollup 非幂等；现将 freq 纳入唯一约束（新迁移 c9d0e1f2a3b4，模型/repo 冲突列同步），跨频月末共存合法、撞键与非幂等一并消除，新增 2 项纯单测锁定批级无重复键 + 月末跨频共存不变量
- 涉及模块：backend/migrations, backend/models, backend/repositories, backend/services/industry_mock_data, backend/tests

## 2026-09-02 - months 回补窗口贯通 + mock 序列 off-by-one 修复
- `ingest_industry_metrics` 此前收了 `months=37` 却从未使用：AKShare fetcher 硬编码 `df.tail(45)` 致多年回补不可能，现已全链贯通 API schema（`FetchIndustryMetricsRequest.months`，1..120 默认 37）→ worker payload 透传 → ingest → `_fetch_akshare_rows`（tail 换算 `max(45, months*31)`）；mock 分支 `build_pig_mock_points(months=...)` 越界钳制到 1..37 而非报错；同时修复 `_wobble_series` off-by-one（旧实现返回 n+1 点、调用方取前 n 点致日度末点带抖动，"日度末点==月度最新值"不变量破裂），新增 3 项纯单测锁定精确长度/窗口/跨频末点一致
- 涉及模块：backend/services/industry_metric_service, backend/services/industry_mock_data, backend/workers/industry_metrics_worker, backend/schemas/task, backend/tests

## 2026-09-02 - 读路径卫生与契约修正（行业工作台 backend 小修合集）
- `get_dashboard` 不再在每次缓存未命中时写库（原每次 GET 都调 `evaluate_and_store_signal` 落信号行）：改为读 `repo.latest_signal`，仅空库无信号行时补算一次引导；batch 导入抽出纯函数 `_prepare_batch_rows` 并引入 source 白名单 `IMPORT_ALLOWED_SOURCES = {"manual", "stats_gov"}`（采集适配器专属 source 不得经人工通道伪造），修复恒真的 source_tier 三元式，响应补 `derived_upserted` + `skipped_invalid_source`；history 端点查询参数 `months` 更名为 `limit`（实为行数上限，默认 500，1..5000）；`get_latest_metrics`/`get_metric_history`/`list_industries` 服务与路由去掉未用的 `cache` 参数（dashboard 保留真实缓存）；`DashboardOut` 新增 `data_source`（取 `settings.industry_data_source`，前端 Task 6 消费）；新增 3 项纯单测
- 涉及模块：backend/services/industry_metric_service, backend/api/v1/industries, backend/schemas/industry, backend/tests

## 2026-09-02 - 前端修正（EChart silent / 演示标签动态化 / 相位文案去本地化）
- 三项修正对应后端 Task 5 的 `DashboardOut.data_source` 契约：① `EChart` 封装的 `silent` prop 落实文档语义（真正关闭 animation + tooltip，旧实现仅切 canvas renderer 属无效近似）；② 工作台页"演示数据源：mock"标签改为仅在 `dashboard.dataSource === "mock"` 时条件渲染，切换真实源后不再误标演示；③ 删除前端本地 `PHASE_LABELS` 映射，周期阶段文案改从后端下发 `cycle.phases[].label` 派生（`PHASE_COLORS` 保留——纯展示常量）；`industryResearch.ts` 的 `BackendDashboard`/`Dashboard`/`mapDashboard` 同步补 `data_source`/`dataSource` 字段
- 涉及模块：frontend/shared/ui/EChart, frontend/shared/api/industryResearch, frontend/pages/research-workbench

## 2026-09-02 - 终审修复（mock purge 覆盖 derived 行 + basis 键名对齐）
- mock→真实源切换的清除范围扩展：`delete_mock_rows` 泛化为 `delete_rows_by_source(db, industry_key, sources)`，清除集 `PURGE_SOURCES = {"mock", "derived"}`（派生计算只 upsert 不删除，旧实现漏删 derived 行会让 mock 算出的能繁环比/猪粮比序列存活并继续喂给周期引擎，空库下可误报复苏/买入）；ingest 返回键 `purged_mock` 相应更名为 `purged`（worker 透传 dict、scheduler 日志不依赖该键），新增纯单测锁定清除集；前端 `CyclePhaseStrip` 的 basis 读取键 `sowConsecutiveDecline` 修正为后端 snake_case 的 `sow_consecutive_decline`（此前"连续 N 个月回落"证据行静默不渲染）；顺带移除 upsert 冲突 SET 中 freq 的无效自赋值（freq 已在冲突键内）
- 涉及模块：backend/repositories/industry_metric_repo, backend/services/industry_metric_service, backend/tests, frontend/features/industry-research

## 2026-09-03 - 任务派发竞态修复 + 行业工作台 E2E 测试套件
- **问题**：task_service 先发 MQ 消息、请求结束才提交任务行，worker 提前消费时 `update_task_status` 查无此行静默跳过，任务永远停在 pending（docker 实测复现，影响所有 worker 队列）
- **修复**：抽取 `_dispatch_task` 助手统一 5 个 trigger —— 先 commit 任务行、后 publish；publish 失败标记 failed 防孤儿 pending；worker 侧任务行缺失时输出告警
- **E2E**：新增 `tests/test_industry_e2e.py`（pytest marker `e2e`，需 docker 栈，离线 `-m "not e2e"` 跳过）8 项：任务生命周期/连续触发竞态回归、latest 频率裁决、dashboard 契约、history limit+月末双频、batch 白名单、ingest 幂等、前端烟雾；docker 环境全链路验证通过（迁移链 c9d0e1f2a3b4、mock ingest 408 行、月末 daily/monthly 共存、62/62 测试）
- 涉及模块：backend/services/task_service, backend/workers/base_worker, backend/tests

## 2026-09-03 - AKShare 真实数据源接入（搜猪网/新浪期货实机验证）
- AkShareClient 重写为四个实机验证接口（2026-09-03 · akshare 1.18.94）：搜猪网 `spot_hog_year_trend_soozhu`（生猪当年均价 ~200 行）/`spot_corn_price_soozhu`/`spot_soybean_price_soozhu`（各 15 行，元/kg 日度，长历史靠逐日滚动累积）+ 新浪 `futures_zh_daily_sina(LH0)`（全历史日行情，元/吨）；删除已证伪的生意社 `spot_price_qh` 路径（100ppi 页面改版上游抛 AttributeError），全部 TODO(api-verify) 清零
- fetcher 改表驱动 `_AKSHARE_SPECS`（metric_key/source/client 方法/日期列/数值列/护栏上限），`_fetch_akshare_rows(cfg, months, client=None)` 支持注入假 client 做纯单测；补上 LH 循环缺失的未来日期守卫，新增数值健全性护栏（现货 0<v<100、期货 0<v<100000，越界跳行告警）
- source 命名：hog/corn/soybean 从 `akshare_100ppi` 改为 `akshare_soozhu`（registry sources 同步，mock 仍垫底）
- mock 清除策略修订（修订 C2 裁定）：从"整行业清除"改为"按已覆盖指标清除"——新增纯函数 `_covered_purge_keys(covered)`（hog_price 与 corn_price 同时覆盖→连同清除 hog_corn_ratio 的 derived 旧行，重算即真实值），`delete_rows_by_source` 增加 `metric_keys` 可选过滤，未覆盖指标（能繁/成本/仔猪等）保留 mock 演示数据；ingest 返回新增 `covered_metrics`，移除 `PURGE_SOURCES` 常量
- akshare 进 pyproject 运行依赖（`uv add akshare`，锁定 1.18.94）；`backend/.env` 写入 `INDUSTRY_DATA_SOURCE=akshare`（本地栈真实化，代码默认 mock 不变）；新增 `tests/test_industry_fetchers.py`（不触网：假 client fixture 单测字段映射/未来日期剔除/护栏/单指标隔离/months 窗口/覆盖清除键/规格-registry 对齐）
- 涉及模块：backend/core/providers/akshare_client, backend/services/industry_registry, backend/services/industry_metric_service, backend/repositories/industry_metric_repo, backend/tests

## 2026-09-03 - Agent 指令文档治理（AGENTS.md/CLAUDE.md 单一事实来源）
- 合并 best-practice.md 与 best-practices.md 为后者（30 条去重合并），删除单数版并修正全部引用；根 CLAUDE.md 改为转发 AGENTS.md（backend/crons 的 CLAUDE.md 本就是 symlink）；重写 backend/AGENTS.md 对齐实际目录结构（修正 SQLModel→SQLAlchemy 2.0、不存在的顶层组件）；根 AGENTS.md 新增"常用命令"小节（uv/npm/docker compose，命令均已实跑验证）并标注根目录 src/ 为早期 CLI 遗留；重写 crons/AGENTS.md 澄清与 app/scheduler、app/workers 的分工；修复 frontend/AGENTS.md 与 docs/references/index.md 的断链
- 涉及模块：AGENTS.md（root/backend/frontend/crons）、CLAUDE.md、docs/references/best-practices.md、docs/references/index.md
