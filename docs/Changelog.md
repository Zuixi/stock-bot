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

## 2026-09-03 - Playwright 浏览器级 E2E（投研列表 + 猪智投工作台）
- frontend 新增 Playwright 套件（devDep `@playwright/test` + `npm run test:e2e` 脚本，chromium 本地安装）：`/research` 断言生猪养殖行业卡片、申万Ⅲ标签与"指标接入 x/x"覆盖度形状；`/research/pig` 经卡片点击导航后断言头部"周期阶段/当前信号"标签（信号 ∈ 买入|卖出|关注|空仓）、指标带生猪均价卡片数值非空、周期相位条四阶段（繁荣/衰退/萧条/复苏）且唯一"当前"高亮、仓位建议三段（核心底仓/波段仓位/现金储备）、≥2 个 EChart canvas、核心指标速览网格、行业知识库 Tab 的 P6 占位文案；断言全部锚定中文标签与 DOM 结构、不锚定实盘数值（数据为真实/混合源）；tsconfig include 未覆盖 e2e/ 目录，`tsc -b` 与 vite 构建不受影响（已验证）
- 涉及模块：frontend/e2e（research.spec.ts）、frontend（package.json / playwright.config.ts）

## 2026-09-03 - 能繁协会源接入
- 新增 `CaaaClient`（pig.caaa.cn 中国畜牧业协会猪业分会）：行业动态栏目列表页（`/html/pig_rd/pig_hydt/`，年份目录 403 但栏目页 200）倒序发现最新"全国生猪产品数据"月度文章，正文纯文本正则解析（无表格）"能繁母猪存栏XXXX万头，环比下降/上升X%"——`parse_sow_article`/`find_latest_data_article` 为纯函数可离线单测；数据期取标题月份的月末（"2026年3月份"→03-31，与正文"1季度末"一致），环比按方向词归一符号；任何失败 log warning 返回 None，绝不抛穿；`CAAA_SOW_ARTICLE_URL` 设置可指定文章直连兜底
- ingest 接线：`source=="akshare"` 时 `_fetch_caaa_sow_row`（可注入假 client）在 upsert 前并入 → sow_inventory 进入 covered_metrics，mock purge 覆盖能繁演示行；registry sow sources → `["stats_gov", "caaa", "mock"]`（统计局 CSV 通道仍最高优先）；row extra 落 article_url/mom_pct 溯源
- 实跑验证：live 探针返回 `{period: 2026-03-31, inventory: 3904.0 万头, mom_pct: -1.5%, article: /2026/0427/2467.html}`；新增 `tests/test_caaa_client.py` 18 项离线单测（真实文章快照 fixture + 符号归一 + 兜底链 + 容错 + 服务接线/registry 对齐）
- 涉及模块：backend/core/providers/caaa_client, backend/services/industry_metric_service, backend/services/industry_registry, backend/config, backend/tests

## 2026-09-03 - P5 标的分析核心（公司指标 / 头均市值 / companies 端点 / 对比表）
- **Registry**：新增公司级指标 group="company"（stock_id>0 落表）——`company.hogs_sold_monthly`(万头,monthly,manual)、`company.cost_complete`(元/kg,quarterly,manual)、派生 `mcap_per_head`(元/头,monthly,calc)；**修正 sw_l3_codes 110301→110702**（110301 实为林业Ⅲ，生猪养殖=110702，与 docs/references/sw/申万行业分类.md 及库内 SW seed 一致，companies 端点实测暴露）
- **派生**：`_annualize_hogs` 纯函数（≥12 个不同月 trailing-12M SUM；1-11 个月最新月×12 粗年化，extra.annualized 标记；同月多点取最新）→ `_compute_derived_metrics` 追加头均市值 = daily_basic 最新 total_mv(万元) ÷ 年化出栏(万头)，单位相消为元/头；历史分位暂缓（需 ≥1 年派生行积累）
- **修复（实跑暴露的存量缺陷）**：能繁环比派生在多源同 period 共存（caaa 真实行 + 重跑 mock 演示行）时产出重复 period 行，单批 ON CONFLICT 二次命中即 CardinalityViolation——按 registry 源优先级逐期去重后再算环比
- **API**：`GET /api/v1/industries/{key}/companies` —— sw_l3_codes 成分股（复用 market_service，抽取 `list_symbols_by_industry_codes` 泛化原 level3 查询）+ enriched 行情/估值（复用 `get_stocks_enriched_by_symbols`）+ 公司指标 latest（新增 repo：`latest_company_rows`/`get_company_metric_history`，`_pick_latest` 泛化为 `_pick_row` 供公司级复用）；列定义 registry 驱动下发（固定 代码/名称/最新价/总市值(亿)/PE(TTM)/PB + company 指标列）
- **前端**：`fetchIndustryCompanies` + `CompanyComparisonTable`（列由 payload columns 驱动、数值右对齐可排序、行点击跳 /stock/:symbol、antd Tabs 惰性挂载保证 Tab 激活才发请求）挂入工作台「行情调研追踪」Tab；market-industry-level3 页命中已产品化行业（sw_l3_codes 与当前二级/选中三级交集）时展示"进入投研工作台"banner（复用 industries 查询缓存）
- **测试**：新增 `tests/test_industry_companies.py` 10 项纯单测（年化出栏各分支/列下发/stock_id 透传/L3 码锁定）；e2e 追加 companies 全链路（tree 动态解析成分股 → 双猪股导入 6 个月出栏+成本 → 断言头均市值>0 与 registry 列）+ 未知行业 404；Playwright 追加对比表渲染跳转 + L3 页 banner 两用例；全量 99 backend + 4 Playwright 通过（含 docker 重建实跑）
- 涉及模块：backend/services/industry_registry, backend/services/industry_metric_service, backend/services/market_service, backend/repositories/industry_metric_repo, backend/api/v1/industries, backend/schemas/industry, frontend/shared/api, frontend/features/industry-research, frontend/pages/research-workbench, frontend/pages/market-industry-level3, backend/tests, frontend/e2e

## 2026-09-03 - P5 ETF/可转债管道
- **表与迁移**：新增 `fund_etf_daily` / `cb_daily`（迁移 d5a6b7c8d9e0，链头自 c9d0e1f2a3b4），镜像 daily_quotes 数值口径（OHLC Numeric(12,4)、volume/amount 原样落库不换单位），UNIQUE(ts_code, trade_date) 支持幂等 upsert
- **TuShare 采集**：TuShareClient 新增 `fetch_fund_daily`/`fetch_cb_daily`/`fetch_cb_basic`（经 RateLimitedSyncProvider 节流）；`securities_service.ingest_industry_securities` 按 registry `etf_codes`/`cb_codes` 逐代码回补（单代码失败 log+跳过且错误摘要进任务 result），`map_daily_rows`/`build_code_series` 为纯函数可离线单测
- **Registry 标的（2026-09-03 实机核验）**：pig `etf_codes=["159865.SZ"]`（国泰中证畜牧养殖ETF，fund_basic 实测名）；`cb_codes=["127045.SZ","123107.SZ","127049.SZ"]`（cb_basic 按 9 只成分股正股名过滤、仅留在市 delist_date 为空：牧原转债/温氏转债/希望转2；已退市的希望转债 127015、正邦转债 128114 排除）；`securities_names` 下发 code→展示名
- **双轨任务**：QUEUES `securities.fetch` + `SecuritiesWorker` + `POST /api/v1/tasks/fetch-securities`（backfill_days 默认 365，ge=1 le=1825）+ APScheduler `securities_refresh_job` 工作日 17:10（industry_metrics 17:05 之后），调度走 10 天增量窗口、手动通道负责全年回补
- **API**：`GET /api/v1/industries/{key}/securities?type=etf|cb&limit=90` → `{type, codes:[{ts_code, name, latest, change_pct(close vs pre_close), series}]}`，未拉取时 series 空（前端空态引导）
- **前端**：「行情调研追踪」Tab 成分股对比表下新增"行业 ETF"（代码/名称/最新价/涨跌幅/成交量/近期走势 sparkline，复用 sparkOption+EChart）与"可转债"（registry 无在市转债时不渲染）两张紧凑表 +「拉取数据」按钮（触发任务后 3s 延迟刷新）
- **测试**：`tests/test_industry_securities.py` 11 项离线单测（registry 标的/名称覆盖、TuShare 行映射含脏行跳过、序列组装涨跌幅、冲突列常量=模型约束=迁移、db 透传回归锁定）；e2e 追加 fetch-securities 任务→securities 端点全链路（≥30 序列行 + cb 分支随 registry 源无关）+ 404/422；Playwright 追加 ETF 表用例；全量 111 backend（99 offline + 12 e2e）+ 5 Playwright 通过（docker 重建实跑：ETF 243 行、3 只转债 729 行入库）
- 涉及模块：backend/models/securities, backend/migrations, backend/core/providers/tushare_client, backend/services/industry_registry, backend/services/securities_service, backend/repositories/securities_repo, backend/core/mq, backend/workers/securities_worker, backend/scheduler, backend/services/task_service, backend/api/v1/tasks, backend/api/v1/industries, backend/schemas, frontend/shared/api, frontend/features/industry-research, frontend/pages/research-workbench, backend/tests, frontend/e2e

## 2026-09-03 - P6 行业知识库
- **表与迁移**：新增 `industry_knowledge`（迁移 e6f7a8b9c0d1，链头自 d5a6b7c8d9e0）：`industry_key/kind(org|principle|mindmap)/payload JSONB/sort`，同 kind 多行按 (kind, sort, id) 读序，索引 (industry_key, kind, sort)；纯内容管理，第二行业零表结构改动
- **迁移内 seed（内容即数据）**：内容单点维护于 `app/services/industry_knowledge_seed.py`，迁移与单测共用同一份（本仓库 alembic env 本就运行于 app 包内，env.py 已 import app.*，import app 内容模块与既有运行方式一致）；猪智投 14 机构（官方 5/协会 2/数据平台 6/期货 1，tier 对齐 SourceBadge 五级权威性）、数据权威性使用原则 5 条、思维导图 EChart tree（供给/需求/成本/政策/金融 五分支，叶子 ≤2 层深）；PRD 原文不在仓库，内容以 data-source.md §四（口径与坑）+ §六（参考链接）为基准整理
- **API**：`GET /api/v1/industries/{key}/knowledge` → `{org:[...], principle:{title,items}|null, mindmap:{name,children}|null}`；未知行业 404（与既有端点同语义）、已知行业无内容 → 空形状 200；脏行（形状不合法/非 dict payload）log 后跳过不打挂 Tab
- **前端**：`fetchIndustryKnowledge`（payload 透传无 mapper）+ `KnowledgeTab`（机构图谱四分组卡片：名称+SourceBadge+一句 desc；原则编号列表；思维导图 EChart tree orient LR 可折叠 420px）挂入工作台「行业知识库」Tab 替换占位，antd Tabs 惰性挂载首激活才请求
- **测试**：`tests/test_industry_knowledge.py` 7 项离线单测（种子形状：四分组/tier 合法/名称唯一/原则 ≥4/树可序列化 ≤2 层/sort 组序 + 装配纯函数空态与脏行容错）；e2e 追加 knowledge 聚合（org ≥12 四分组、原则 ≥4、思维导图 ≥4 分支）+ 未知行业 404；Playwright 知识库用例由占位断言升级为分组卡片/机构条目/原则/思维导图 canvas 断言（`.ant-card-head-title` 作用域规避 SourceBadge 同文案混淆）；全量 121 backend + 5 Playwright 通过（docker 重建实跑）
- 涉及模块：backend/models/industry_research, backend/migrations, backend/services/industry_knowledge_seed, backend/services/industry_knowledge_service, backend/repositories/industry_knowledge_repo, backend/api/v1/industries, backend/schemas/industry, frontend/shared/api/industryResearch, frontend/features/industry-research, frontend/pages/research-workbench, backend/tests, frontend/e2e

## 2026-09-03 - P6 收尾：research 列表信号化 + broiler 泛化验证 + 计划勾选
- **列表卡片信号化**：`GET /api/v1/industries` 每行业增 `phase / signal_type / signal_date`（list_industries 逐行业 latest_signal 查询，从未 ingest 的行业为 null）；`/research` 卡片增状态行——周期阶段 Tag（prosperity/recession/depression/recovery 四色与工作台同板，色板与文案映射抽取 `features/industry-research/constants.ts` 供列表/工作台共用）+ 当前信号加粗 Tag + 信号日期，保留指标接入覆盖度与数据截至
- **泛化验证（broiler 白羽肉鸡第二行业，零新页面）**：registry 增 `BROILER_INDUSTRY`（申万Ⅲ 110703 肉鸡养殖——三源核验：sw_seed.sql + 申万行业分类.md + 库内 live tree，7 只成分股与生猪 110702 不相交）；2 个 mock-only 指标 `chick_price` 鸡苗价格 / `broiler_price` 毛鸡价格（`MetricDef` 新增 `mock_base` 基准值字段）；复用通用四周期键位（描述按肉鸡口径改写）与 `_position_slices` 仓位模板；周期引擎 `evaluate_pig_cycle` 增可选 `cfg` 参数（驱动预警档与仓位模板，缺省仍 PIG_INDUSTRY，规则本体保持猪周期口径）
- **通用 mock builder**：`build_generic_mock_points(cfg)` 对所有配置 `mock_base` 的指标按注册频率（daily/weekly/月度对齐）生成 seeded 抖动序列，末点精确等于基准值；`build_industry_mock_points` 统一分发（pig 走原型对齐专用序列，其余走通用）——新演示行业无需再写 builder 模块；`source=mock` ingest 链路（worker payload industry_key=broiler）端到端跑通（90 行落库 + 信号评估）
- **测试**：新增 `tests/test_industry_generalization.py` 10 项离线单测（registry 双行业 sw 码不相交 / broiler mock-only / 通用 builder 无重复冲突键+确定性+末点=基准 / 引擎 cfg 注入）；e2e 追加 broiler ingest→列表→dashboard 全链路 + pig/broiler 成分股隔离断言；Playwright 追加列表状态行与"broiler 卡片零新页面进入工作台"两用例（并修一处竞态：列表卡片新增周期/信号 Tag 后，SPA 路由切换瞬间旧列表 DOM 触发严格模式多元素，现先等 Tab 栏挂载再断言头部标签）；全量 133 backend（117 offline + 16 e2e）+ 7 Playwright 通过（docker 重建实跑）
- **文档收尾**：`plans/industry-research-workbench.md` P1-P6 验收项按实际完成情况勾选留痕（未勾选项附原因：猪粮比独立图表、统计局 CSV 脚本、多源对比 Drawer；头均市值分位注明"分位待历史积累"）；`plans/2026-09-03-workbench-p3-p6-completion.md` 五阶段标记完成；data-source.md 补 `sow_inventory_mom` 派生行与 broiler mock 演示指标注记（metric_key 交叉引用对齐）
- 涉及模块：backend/services/industry_registry, backend/services/industry_mock_data, backend/services/industry_metric_service, backend/services/cycle_engine, backend/schemas/industry, frontend/pages/research, frontend/pages/research-workbench, frontend/features/industry-research, frontend/shared/api/industryResearch, backend/tests, frontend/e2e, plans, docs

## 2026-09-03 - 个股/指数详情 K 线周期切换修复
- **问题**：个股详情（及指数详情）K 线图切换 1月/3月/6月/1年 周期"显示不对、似未生效"——任何周期都只渲染所选区间尾部 40%（dataZoom 固定 start:60），且 `ReactECharts` 默认 merge 模式下用户滚轮缩放状态粘滞，切周期后可见窗口不重置；后端 start/end 过滤与缓存 key 均正常（已实测 30d=22 行 / 365d=242 行）
- **修复**：`KLineChart` 与 `IndexKLineChart` 去掉 dataZoom 固定 `start:60/end:100`（周期切换后默认展示全量区间，滚轮缩放留给用户主动操作），并加 `notMerge`（与 `shared/ui/EChart` 封装既有约定对齐），确保切周期时完整重放 option、重置缩放状态
- **验证**：docker 重建前端后浏览器实测——滚轮缩放至窄窗口再切"1年"，视图与干净的全年视图逐字节一致（修复前该场景窗口冻结）；1月=整月 22 根、1年=全年 200+ 根全量渲染
- 涉及模块：frontend/features/stock-detail, frontend/pages/index-detail

## 2026-09-03 - 前端交互修复
- **面包屑可点击**：投研工作台（/research/pig、/research/broiler 共用组件）面包屑首项"投研"由纯文本改为 react-router `<Link to="/research">`（antd Breadcrumb item title 直接承载 Link，末项"工作台"保持当前页无链接约定）；/research 列表页无面包屑，不涉及
- **行业卡片等高 + 描述截断**：/research 两张行业卡片在部分宽度不等高（broiler 描述换行 2 行撑高 246/268px）——描述改 `Typography.Text` `ellipsis={{ tooltip: true }}` 单行省略（hover 出全文）消除换行差；再以 Col `display:flex` + Card `height:100%` 拉伸兜底（窄宽度 Tag 换行等场景仍等高）；卡片头部 h4 名称加 `minWidth:0` + `ellipsis={{ rows:1 }}`、申万 Tag/箭头 `flexShrink:0`，超长行业名同样省略号截断
- **测试**：Playwright 新增两用例——/research 两 `.ant-card` boundingBox 等高（≤1px 子像素容差）；/research/pig 点面包屑"投研"→ waitForURL `**/research` 且生猪养殖卡片可见；9/9 通过（docker 重建前端实跑；另 1280/480 双宽度实测 diff=0，ellipsis computed style 核验）
- 涉及模块：frontend/pages/research, frontend/pages/research-workbench, frontend/e2e

## 2026-09-03 - K线共享组件纯函数层（K线组件升级 Task 1）
- 新建 `frontend/src/shared/ui/kline/`（`klineMath.ts` + barrel `index.ts`）：MA5/10/20/60 定义与滑动平均（暖窗前为 null）、同年 MM-DD/跨年首日 YYYY-MM-DD 轴标签、日期区间裁剪 `cropToRange`、成交量/成交额格式化 `fmtVolume`/`fmtAmount`，并 re-export 复权三类型；`shared/types` 的 `KLinePoint` 增 `amount?: number`，文件末尾追加 `AdjustMode` / `KlineResult` / `KlineFetcher`——为个股/指数两处重复 K 线图合并为共享组件打底，类型签名作为后续任务的依赖契约冻结（plans/2026-09-03-kline-component-upgrade.md Task 1）
- 涉及模块：frontend/shared/ui/kline, frontend/shared/types

## 2026-09-03 - K线 option builder（K线组件升级 Task 2）
- 新建 `frontend/src/shared/ui/kline/klineOption.ts` 并入 barrel：`buildKlineOption(KlineOptionInput)` 消费 Task 1 纯函数产出完整 ECharts option——candlestick 主图 + visibleMas 过滤的 MA 折线叠加 + 成交量副图（涨红跌绿）、结构化 tooltip（DOM formatter 返回 HTML：OHLC/涨跌幅/量额/MA 行，按前收着色）、最新收盘价虚线 markLine、inside+slider 双 dataZoom、跨年轴标签；一次构建失败修复：tooltip 内 `maLine` 参数 `string` 收窄为 `MaKey`（string 不能索引 `Partial<Record<MaKey,…>>`）（plans/2026-09-03-kline-component-upgrade.md Task 2）
- 涉及模块：frontend/shared/ui/kline

## 2026-09-03 - K线共享容器组件 KlineChart（K线组件升级 Task 3）
- 新建 `frontend/src/shared/ui/kline/KlineChart.tsx` 并入 barrel：`KlineChartProps` 五字段契约（title/queryKey/fetcher/showAdjust/defaultRange）——React Query 按 `["kline", queryKey, range, adjust]` 取数并加 130 日历日 MA warm-up 缓冲，本地按 `rangeCutoff` 裁剪可见区间（K 线与 MA 同口径对齐）；工具栏 MA CheckableTag 开关（选中按均线色着色）/ 复权 Segmented（showAdjust 时展示，默认 qfq）/ 周期 Segmented（1/3/6月/1年）/ 重置缩放按钮——直用 ReactECharts + `notMerge` + `lazyUpdate` 并以实例 ref `dispatchAction(dataZoom)`，不走未透传 ref 的 EChart 封装；loading/empty 固定 400px 占位；一次构建失败修复：antd 5.24 顶层无 `CheckableTag` 命名导出，改 `Tag.CheckableTag`（对齐 market-industry-level3 既有用法），其连带的 onChange 隐式 any 次生报错随之消除（plans/2026-09-03-kline-component-upgrade.md Task 3）
- 涉及模块：frontend/shared/ui/kline

## 2026-09-03 - 个股/指数详情接入共享 KlineChart（K线组件升级 Task 4）
- 个股详情页与指数详情页切换至共享 `KlineChart` 容器（MA chips 显隐/周期与复权 Segmented/结构化 tooltip/重置缩放），删除 `features/stock-detail/components/KLineChart.tsx` 与 `pages/index-detail/IndexKLineChart.tsx` 两份旧图表（净 -136 行）；`fetchKlineBySymbol` 增第三参 `adjust` 透传（后端 P2 就绪前 FastAPI 忽略该 query 参数，属预期过渡）并返回 `KlineResult`（`adjustAvailable` 按响应 `adjust_available !== false` 推导），`fetchIndexKline` 返回 `KlineResult`（指数无复权恒 true），两个 fetcher 均映射 `amount`（成交额）；新增 `frontend/e2e/kline.spec.ts` 3 用例——MA chips 可见可切换、tooltip 涨跌幅/成交量/成交额、周期切换选中态跟随、指数页无复权控件；e2e 首跑失败修复：antd 5.24 Segmented 的 radio input 为零尺寸隐藏元素，控件断言从 `getByRole("radio")` 改为可见 `label.ant-segmented-item`（选中态断言 `ant-segmented-item-selected` 类）（plans/2026-09-03-kline-component-upgrade.md Task 4）
- 涉及模块：frontend/shared/api/quotes, frontend/shared/api/market, frontend/pages/stock-detail, frontend/pages/index-detail, frontend/features/stock-detail/components, frontend/e2e

## 2026-09-03 - K线复权懒加载回补接线（K线组件升级 Task 7）
- `quotes/daily` 端点增 `adjust` query 参数（`Literal["raw","qfq"]`，默认 raw）透传 service；响应 `adjust_available=false` 时由 FastAPI BackgroundTasks 触发 `quote_service.backfill_adj_factor(exchange, symbol)`——幂等（`has_adj_factor` 已有即 skip）、单股全历史拉取 TuShare adj_factor 批量 UPDATE 入库、`delete_pattern("quote:kline:{exchange}:{symbol}:*")` 失效该股 K线缓存，异常兜底 log 不影响响应；修复实机验证暴露的 `update_adj_factors` SQL 缺陷（VALUES 派生表未定型日期字面量被 PG 推断为 text，与 date 列比较抛 `operator does not exist: date = text`——补 `::date` 显式转型）；新增 2 个 service 级单测（fake cache 记录 set 调用：qfq 因子不完整不写缓存、raw 正常缓存）
- **验证**：pytest 19 failed / 125 passed（19 个全为基线 httpx.ConnectError 环境性失败，与 HEAD 失败集 diff 为空，零回归）；docker 重建后实机——600519 首次 qfq `available: False rows: 23`，25s 后二次 `available: True close[0]: 1358.98`，日志 `[adj_factor backfill] Shanghai_Stocks.600519 updated=808` 一条且三次请求不重触（幂等生效），库内 `adj_factor IS NOT NULL` 共 808 行；除权窗口（2026-06-22..30，因子 8.4464→8.6463）raw 与 qfq 数值分化（1241.41→1212.71，基准日及之后不动）
- 涉及模块：backend/services/quote_service, backend/api/v1/stocks, backend/repositories/quote_repo, backend/tests

## 2026-09-03 - K线复权开关前端完整接入（K线组件升级 Task 8）
- 共享 `KlineChart` 的复权 Segmented 接入 `adjustAvailable` 禁用态：`data.adjustAvailable` 为假（含首帧 loading）时降级为 Tooltip（"复权数据后台拉取中，稍后自动可用"）包裹的 `disabled` Segmented 且 value 固定 "raw"（表示当前展示即 raw 数据），`adjust` 状态保持 "qfq"、queryKey 不变不发额外请求；因子就绪后受控值恢复 "qfq" 无缝启用。`frontend/e2e/kline.spec.ts` 新增用例"数据就绪后可切换前复权/不复权"——按 Task 4 沉淀以 `label.ant-segmented-item` 文案定位 + `ant-segmented-item-selected` 类断言改写 brief 原始的 `getByRole("radio")`/`toBeChecked`（antd 5.24 radio input 零尺寸隐藏必失败），两处 30s 超时为后端懒加载回补留等待余量。验证：`npm run build` 通过（chunk 警告为既有基线）；docker 重建后 playwright 全量 16 passed / 4.7s（含既有 spec 零回归；600519 实况 `adjust_available: True`）
- 涉及模块：frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - K线组件升级整期收官（Task 0-9，P1 组件统一 + P2 复权链路 + P3 复权开关）
- **整期成果**：个股/指数两份重复 K 线图合并为共享 `shared/ui/kline/KlineChart`（P1：MA5/10/20/60 显隐、结构化 tooltip、inside+slider 缩放与重置、最新价 markLine、跨年轴标签）；后端打通 adj_factor 懒加载链路（P2：`GET /quotes/daily?adjust=qfq|raw` + `adjust_available` 标记 + BackgroundTasks 幂等单股回补 + `delete_pattern` 缓存失效）；前端复权开关完整接入（P3：因子未就绪禁用+Tooltip 降级，就绪后无缝启用）。净删除两份旧图表组件，e2e 扩至 16 用例
- **关键架构决策**：① 共享组件契约先冻结——`KlineFetcher = (days, adjust) => Promise<KlineResult>` 作为 Task 1 类型签名发布，shared 层不 import 业务 API，靠 fetcher 回调注入实现依赖倒置，个股/指数页各传一份；② 复权三重缓存防护——缓存 key 追加 `:{adjust}` 维度、qfq 因子不完整不写缓存、回补完成后 `delete_pattern("quote:kline:{exchange}:{symbol}:*")` 兜底，杜绝 qfq 结果污染 raw 缓存与回补后读到陈旧数据；③ `::date` 转型根因——手写 `UPDATE ... FROM (VALUES ...)` 派生表日期字面量被 PG 推断为 text 抛 `date = text`，此类 SQL 类型错误纯函数单测覆盖不到，接线任务以实机验证闭环
- **收官回归**：backend pytest 19 failed / 125 passed（19 个全为基线 httpx.ConnectError 环境性失败，与 Task 7 记录的失败集一致，零回归）；frontend `npm run build` 通过；playwright 16/16 passed
- 涉及模块：frontend/shared/ui/kline, frontend/shared/types, frontend/shared/api, frontend/pages/stock-detail, frontend/pages/index-detail, backend/services/quote_service, backend/api/v1/stocks, backend/repositories/quote_repo, backend/core/providers, frontend/e2e

## 2026-09-03 - qfq 跨日死锁修复（K线组件升级最终审查 C1+I1）
- **问题（C1）**：每日 ingest 以 `adj_factor=None` upsert 新日期行情且 ON CONFLICT SET 无条件覆盖——重灌既有日期会抹掉已回补因子；回补幂等判定 `has_adj_factor`（任一行非空即 skip）与 `get_kline` 可用性口径（区间全部行非空）错位，且 skip 在缓存失效之前 return → 次日起 qfq 永久 `adjust_available=false` 死锁
- **修复**：① `upsert_quotes` SET 子句 `adj_factor` 改 `COALESCE(excluded.adj_factor, daily_quotes.adj_factor)`（NULL 不覆盖既有因子）；② 幂等判定换 `latest_adj_factor_present`（最新交易日行有因子才 skip，跨日新增 NULL 行可增量再触发）并删除口径错位的 `has_adj_factor`；③ 真实外呼后写 300s Redis 冷却 key `quote:adj-factor:backfill-cd:{exchange}:{symbol}`（skip 不冷却、失败也冷却防失败风暴），API 层 `add_task` 前以 `cache.exists` 守卫，key 模板提为 quote_service 模块常量两处共用
- **问题（I1）**：复权禁用态 Tooltip 承诺"稍后自动可用"但 5min staleTime 内不会自动重取——`useQuery` 加 `refetchInterval`（`adjustAvailable === false` 时 10s 轮询，就绪即停）
- **验证**：`tests/test_kline_adjust.py` 8/8（新增冷却 key 契约测试）；全量 pytest 126 passed / 19 基线环境性失败零回归；docker 重建后跨日自愈模拟——置 NULL 最新行 → qfq `adjust_available:False` 触发回补（updated=808，冷却 key TTL≈300）→ 20s 后 `available:True` 库内因子恢复；冷却期内重复请求不重触；容器内重灌 09-01 既有日期（因子 None）COALESCE 保住原值；frontend build + playwright 16/16
- 涉及模块：backend/repositories/quote_repo, backend/services/quote_service, backend/api/v1/stocks, frontend/shared/ui/kline, backend/tests

## 2026-09-03 - K线P4 图表区重构（频率Tab + 图内MA行 + 去日期轴）
- 共享 `KlineChart` 按主流行情终端风格重构：频率 Segmented（日K/周K/月K）替换范围选择，周/月K 纯前端聚合（`aggregateDaily`：open=组首、close=组末、high/low=极值、volume/amount=求和；freq 不进 queryKey、fetcher 固定 3650 天=库内全量，切换零请求）；MA 数值行从 Card extra 的 CheckableTag 移入图表左上角绝对定位 span 行（选中=线色带最新值、未选=灰 #9ca3af、可点击切换，修复 CheckableTag 对比度问题）；主图/成交量两轴 `axisLabel.show=false` 去掉成交量区日期轴，x data 用原始 ISO 日期串由 tooltip 直接消费；dataZoom 改 startValue/endValue 按末尾 `DEFAULT_TAIL_BARS=120` 根定初始视图（TradingView 式：数据全量、视图局部、slider 漫游），grid2 由 66%/13% 调至 68%/16%；删除 `MA_WARMUP_CALENDAR_DAYS/cropToRange/buildAxisLabels` 导出与 `defaultRange` prop（两个页面调用点无引用，零破坏）。验证：npm run build 通过；docker 重建后 playwright 全量 16 passed / 4.7s（首跑 15/16——MA60 off 态 span 文本为 "MA60 "，JSX 保留尾部空格致 `^MA60$` 正则不命中，改 `\s?$` 后全绿）
- 涉及模块：frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - K线P4 UI迭代收官（行情终端风格 + 个股头部8项网格）
- **MA对比度根因与图内数值行方案**：MA 开关原用 antd CheckableTag 承载，其选中态自带主题色实底，与 inline 均线色文字撞色（对比度 ~1.2:1）——改为图表左上角绝对定位文本行（选中=线色文字带最新值、未选=灰 #9ca3af、可点击切换），线色文字落在白底图区对比度天然达标（教训沉淀 best-practices）
- **频率Tab语义与前端聚合**：日K/周K/月K 是同一份日行情的"重新分桶"而非不同数据窗口——周/月K 由 `aggregateDaily` 纯前端聚合（open=组首/close=组末/high-low=极值/量额=求和），freq 不进 queryKey、fetcher 固定 3650 天拉库内全量，切换零请求零延迟；替代原"1/3/6月/1年"范围选择
- **头部8项**：个股头部升级 `Descriptions column={4}` 两行 8 项网格（今开/最高/最低/昨收/成交量/成交额/换手率/总市值），后端 enriched 查询补选最新行情行 open/high/low 三列（模式照既有 prev_close 同行取法）
- **默认120根**：初始视图改 dataZoom startValue/endValue 按末尾 `DEFAULT_TAIL_BARS=120` 根定位（TradingView 式：数据全量、视图局部、slider 漫游），成交量区去日期轴
- **收官回归**：backend pytest 126 passed / 19 failed（全为基线 httpx.ConnectError 环境性失败，零回归）；frontend `npm run build` 通过；playwright 17/17 passed（含新增头部8项网格与频率Tab/图内MA行用例）
- 涉及模块：frontend/shared/ui/kline, frontend/features/stock-detail, backend/services/market_service, backend/schemas/stock, frontend/e2e

## 2026-09-03 - 市值/成交额单位口径统一（单位归一 Task 1）
- **问题**：全站市值/成交额显示差 1e4/1e3 倍——后端 TuShare 口径 total_mv/circ_mv 为万元、amount 为千元，前端 formatCap 按元分档（≥1e12 万亿/≥1e8 亿/≥1e4 万），mapBackendStockEnriched 原样透传未换算，导致 600519 总市值显示为"1.62亿"量级错误
- **修复**：
  - mapBackendStockEnriched 单点换算为元（amount ×1e3、total_mv/circ_mv ×1e4，判空在前避免 null 归零），volume 保持手口径不换算
  - StockTable 成交额列 NumberText 补 `unit="cap"`（WatchlistTable 核对无缺）
  - KlineChart 死代码清理：删未使用 DEFAULT_TAIL_BARS import、MA 行冗余 `!isLoading &&` 守卫
- **验证**：e2e 头部用例追加形状断言（600519 总市值含"万亿"、成交额含"亿"），17 用例全绿；实机 curl 推演总市值 1.62万亿 / 成交额 26.34亿
- 涉及模块：frontend/shared/api/stocks, frontend/features/market, frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - K线 tooltip 列式布局（P5 小迭代）
- **问题**：tooltip 三行挤排（"开：x 高：x 低：x 收：x"横排）无数字对齐、无涨跌额，与主流行情终端差距明显
- **修复**：`klineOption.ts` formatter 重写为参考图 1 的两列式逐行布局（灰标签左 + 右对齐 tabular-nums 语义色数值右）：日期标题行 → 开盘/收盘/最高/最低/涨跌额/涨跌幅（随当日涨跌着色，涨跌额带正负号，首日中性）→ 成交量/成交额（中性）→ MA 尾行（线色，悬停根的值）；容器 min-width:150px 保证列对齐
- **验证**：e2e tooltip 用例强化（开盘/收盘/涨跌额标签断言），全量 17/17 绿；浏览器实测两列对齐与着色规则符合预期
- 涉及模块：frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - 市场页"其他"行业分类合并清零（数据运营）
- **问题**：申万成员表仅覆盖 74% 股票，1439 只（多为次新股）落入"其他"，按 TuShare 行业名分 83 组，其中 29 组与现行申万 L2/L3 精确重名、54 组近似名，视觉上"重合却未合并"
- **处理**：A 精确重名 321 只按同名映射 + B 近似名 52 组 1017 只按语义映射表 + C 电气设备 99 只按东财行业接口三方归类（同花顺双源核验特例）+ 2 只银行逐股，共 1439 行写入 stock_custom_sw_tags（幂等）；"其他"清零，L1 组 32→31
- **验证**：API 实测电子 524/电力设备 351/医药生物 498/银行 42 与推演一致；树缓存失效后浏览器即时生效
- 涉及模块：数据层 stock_custom_sw_tags（无代码变更）

## 2026-09-03 - 行业分类种子同步 — custom_tags overlay 种子与启动加载
- **问题**：1439 只"其他"合并数据只存在于运行库，repo 种子未同步——全新部署会退回"其他"83 组状态；且 `sw_seed.sql` 为自动再生成文件（仅覆盖申万官方两表），手改会被抹掉
- **修复**：新增 `backend/data/sw_custom_tags_seed.sql` overlay 种子（1439 行，INSERT ON CONFLICT DO NOTHING 加性语义，保留用户自建标签）+ `sw_industry_service` 新增 `import_custom_tags_from_sql()` 并接入 `import_all()` 两条路径（SQL 种子/XLS 引导），启动初始化自动加载；`.gitignore`/`backend/.dockerignore` 补豁免使种子进 git 与镜像
- **验证**：种子在活库幂等重跑（INSERT 0 0）；api 重建后容器内 loader 实测返回 1439；mypy 零新增（基线 5 项既有）
- 涉及模块：backend/data, backend/app/services/sw_industry_service, backend/.dockerignore, .gitignore

## 2026-09-03 - 种子一致性审计 + overlay 加载修复
- **审计**：临时库灌入 repo 种子与活库逐行 diff——sw_industry_classes 511 / sw_industry_members 4430 / stock_custom_sw_tags 1439 三表完全一致，全新 docker 部署分类数据与当前显示一致
- **发现并修复**：data_init 的 overlay 加载被 is_sw_data_loaded 门控——先于 overlay 的老部署升级后 SW 表已有数据、跳过导入、1439 行永不生效；改为加性幂等的 overlay 每次 startup 无条件尝试（日志可观测）
- 涉及模块：backend/app/services/data_init

## 2026-09-03 - 市场数据面 Task 1 — 7 张数据表模型与迁移
- 新增市场数据面数据层：板块资金流快照 / 龙虎榜 / 北向资金 / 大宗交易 / 限售解禁 / 股票回购 / 公告快讯 7 个 ORM 模型（`app/models/market_data.py`，注册到 models `__init__`）+ 手写 Alembic 迁移 `9d4e7a2c8b1f`（down_revision `e6f7a8b9c0d1`），含唯一约束去重键与 10 个查询索引
- 涉及模块：backend/app/models, backend/app/migrations/versions

## 2026-09-03 - 市场数据面 Task 4 — GET /market/global-indices 全球指数卡片
- 新增 `market_data_service.get_global_index_cards(cache)`：东财 push2delay 实时快照（`_em_code` 对齐 secid→code，60s Redis 共享缓存 `market:global-indices`）+ `index_dailies` 近 30 日 spark + 实时缺失时 EOD 兜底（全球指数行 pre_close=NULL，用相邻收盘逐日差值算涨跌额/幅）→ `GlobalIndexCardOut`（`app/schemas/market_data.py`）经 `app/api/v1/market_data.py` 挂到 `/api/v1/market/global-indices`（子路由不带 prefix、include 时挂 `/market`，与 market.router 共存）；活体验证 9 卡全 realtime、spark=30、二次请求命中缓存
- 涉及模块：backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/api/v1/__init__

## 2026-09-03 - 市场数据面 Task 5 — 板块资金流盘中采集与读取端点
- 新增 `market_data_repo`（`upsert_sector_moneyflow` 幂等 upsert 按约束 `uq_sector_moneyflow_dim_code_date`、显式 `updated_at: func.now()` 因 pg on_conflict 不走 ORM onupdate；`list_sector_moneyflow` 按 main_net_inflow DESC NULLS LAST）；`market_data_service.ingest_sector_moneyflow` 拉 industry/concept 两维当日快照（`dict[str,int]` 行数契约），`get_sector_moneyflow` 走 Redis `market:sector-moneyflow:{dim}` TTL 60s；`SectorMoneyflowOut` + `GET /api/v1/market/sector-moneyflow?dimension=&limit=`；scheduler 新增 `sector_moneyflow_poll`（mon-fri 9-15 每 5 分钟，job 内 `_is_workday`/`_in_trading_hours` 守卫）+ CLI `_main` 分支 `sector_moneyflow`；东财 clist 端点 base 由 push2 切至 push2delay（push2 当日开始拒连，delay 域同构可用）
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner, backend/app/core/providers/eastmoney_client

## 2026-09-03 - 市场数据面 Task 6 — 北向资金盘后净流入序列
- 新增 `market_data_repo.upsert_northbound`（幂等 upsert 按约束 `uq_northbound_date`，只刷 `net_amount`、source 首写不变）与 `list_northbound`（近 N 日按 trade_date 升序）；`market_data_service._map_hsgt_rows` 将 moneyflow_hsgt 全字符串列归一为 float|None（万元，NaN/空串兜底），`ingest_northbound` 近 30 日窗口采集，`get_northbound_series` 走 Redis `market:northbound:{days}` TTL 300s；`NorthboundPointOut` + `GET /api/v1/market/northbound?days=`；scheduler 新增 `northbound_daily`（mon-fri 16:10 盘后）+ CLI `_main` 分支 `northbound`；活体验证 23 个交易日入库、二次 ingest 幂等、端点升序返回且命中缓存
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 7 — 龙虎榜 + 大宗交易采集与读取
- 新增 `market_data_repo.upsert_dragon_tiger`（按约束 `uq_dragon_tiger_date_code_reason` DO UPDATE 全部行情列，同批 (date,code,reason) 重复先去重）、`upsert_block_trades`（按约束 `uq_block_trades_dedupe` DO NOTHING——行无稳定业务键）、`max_dragon_tiger_date`/`max_block_trade_date`（读取端点 date 缺省值）、`list_dragon_tiger`（net_amount DESC NULLS LAST）、`list_block_trades`（amount DESC NULLS LAST + `split_part(ts_code,'.',1)` LEFT JOIN stocks 取股票名、symbol 过滤）；`market_data_service` 新增 `_map_top_list_rows`（reason 超 160 字符映射层截断防 DB 报错）、`_map_block_trade_rows`、`_dedupe_block_trade_rows`（同批去重键保留末次，ON CONFLICT 不处理语句内自冲突）、`ingest_dragon_tiger`/`ingest_block_trades`（None=今日上海日期）与 `get_dragon_tiger`/`get_block_trades`（Redis `market:dragon-tiger:{date}` / `market:block-trades:{date}:{symbol}` TTL 300s，缓存全量 100 行按请求切片）；`DragonTigerOut`/`BlockTradeOut` + `GET /api/v1/market/dragon-tiger?date=&limit=`、`GET /api/v1/market/block-trades?date=&symbol=&limit=`（非法 ISO date 返 400）；scheduler 新增 `dragon_tiger_daily`（mon-fri 18:00）、`block_trade_daily`（mon-fri 17:00）+ CLI 分支 `dragon_tiger [yyyymmdd]`、`block_trades [yyyymmdd]`；活体验证 20260902 龙虎榜 77 行/大宗 78 行入库、二次采集幂等（77/0）、两端点返回含 symbol/name 及正确单位（dragon 金额元，大宗 price 元/volume 万股/amount 万元）
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 8 — 限售解禁 + 股票回购采集与读取
- 新增 `market_data_repo.upsert_share_floats`（约束 `uq_share_floats_dedupe` DO NOTHING；ann_date 可 NULL，Postgres 唯一约束不判重 NULL——NULL ann_date 行可能重复入库，属可接受偏差）、`list_share_floats`（float_date BETWEEN 窗口 DESC NULLS LAST + `split_part` LEFT JOIN stocks 取名、symbol 过滤）、`upsert_repurchases`（约束 `uq_stock_repurchases_dedupe` DO UPDATE end_date/exp_date/vol/amount/high_limit/low_limit——进度会修订，同批 (ann_date,ts_code,proc) 先 Python 去重）、`list_repurchases`（ann_date DESC）；`market_data_service` 新增 `_d_opt`（可空日期 NaN/None→None）、`_map_share_float_rows`/`_map_repurchase_rows`（float_share 万股、float_ratio %、vol 股、amount 元全程不换算；proc 映射层截断 String(16)）、`_dedupe_repurchase_rows`、`ingest_share_floats`/`ingest_repurchases`（近 N 日窗口 %Y%m%d，默认 7）与 `get_share_floats`/`get_repurchases`（缺省窗口：解禁 today-30d→today+90d 因解禁是未来事件必须含未来日期、回购 today-30d→today；Redis `market:share-floats:{start}:{end}:{symbol-or-all}` / `market:repurchases:...` TTL 300s，缓存全量 100 行按请求切片）；`ShareFloatOut`/`RepurchaseOut` + `GET /api/v1/market/share-floats?start=&end=&symbol=&limit=`、`GET /api/v1/market/repurchases?...`（非法 ISO 日期返 400）；scheduler 新增 `share_float_daily`（mon-fri 17:30）、`repurchase_daily`（mon-fri 17:40）+ CLI 分支 `share_floats [days]`、`repurchases [days]`；活体验证 解禁 469 行/回购 312 行入库、二次采集幂等（469/0、312/312 DO UPDATE 计 matched）、两端点返回 join name 与正确单位、symbol=002120 过滤与未来 float_date（解禁 09-04）默认窗口即含
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 9 — 巨潮公告采集与快讯端点
- 新增 `CninfoClient`（cninfo hisAnnouncement/query 免 token 公告检索，追加进既有 `cninfo_client.py` 与 webapi 行情 `CnInfoClient` 共存；单例工厂命名 `get_announcement_client` 避开既有 `get_cninfo_client`）：财报/重大事项两类目分页拉取，`announcementTime` 毫秒转上海时区 naive wall-clock datetime（fromtimestamp tz=Asia/Shanghai 后去 tzinfo，避免容器 TZ 未设落 UTC 语义致时间 +8h 漂移）、标题剥 `<em>` 高亮标签、PDF 前缀 static.cninfo.com.cn、column=szse 覆盖沪深；`announcement_service.ingest_announcements` 近 3 日窗口两类目各拉一次 + announcement_id 内存去重 + repo `upsert_announcements`（`uq_announcements_cninfo_id` DO NOTHING）、`get_announcements`（Redis `market:announcements:{symbol|all}` TTL 300s，缓存整页 100 行按请求切片防 limit 进缓存键，announce_time ISO str）；`AnnouncementOut` + `GET /api/v1/market/announcements?symbol=&limit=`；scheduler 新增 `announcements_poll`（每日 8-22 点每 10 分钟——公告含非交易日发布，无 workday/交易时段守卫）+ CLI 分支 `announcements [days]`；活体验证 report 6/event 150 行入库、二次采集幂等（6/0、150/0）、端点标题无 em 标签、pdf_url HEAD 200
- 涉及模块：backend/app/core/providers/cninfo_client, backend/app/services/announcement_service, backend/app/repositories/market_data_repo, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 10 — market_data.fetch 队列 Worker + 手动触发端点
- 新增 `MarketDataWorker`（`app/workers/market_data_worker.py`，queue_key `market_data.fetch`）：按 payload `type` 分发 9 类采集（global_index_daily/backfill_global_index/sector_moneyflow/northbound/dragon_tiger/block_trades/share_floats/repurchases/announcements），参数顶层与嵌套 params 均可（`{**payload, **params}` 合并），`_run` 内开 session 且成功路径 commit、未知 type 返 `{"status":"failed"}` 不抛异常；`QUEUES` 注册 `stock_bot.market_data.fetch`、runner 实例化为第 6 个 worker；`POST /api/v1/tasks/fetch-market-data`（`MarketDataFetchRequest`：`type` Literal 9 选 1 + `params: dict|None`）→ `task_service.trigger_fetch_market_data`（复用 `_dispatch_task`）→ 202 `TaskOut`；单测 monkeypatch service 函数 + 模块级 `async_session_factory`（NullSession 假上下文）2 例通过；活体验证 northbound 202→worker `upserted=22`→status completed、非法 type 422（Literal 网关层拦截）、worker 容器无崩溃
- 涉及模块：backend/app/core/mq, backend/app/workers/market_data_worker, backend/app/workers/runner, backend/app/schemas/task, backend/app/services/task_service, backend/app/api/v1/tasks
- Review fix 1：`process` 去掉 blanket try/except——service 异常向上传播由 BaseWorker 标记任务 failed（数据源故障不再伪装成 completed），未知 type 在触库前经 `_KNOWN_TYPES`（schema Literal `get_args` 单一事实源）返回 failed dict，`_run` 的 else 降级为防御性分支；新增异常传播单测（共 3 例）+ process docstring 补 9 类 payload keys

## 2026-09-03 - 市场数据面 Task 13 — 板块主力资金流卡 + 北向折线卡替换近似实现
- 新增 `features/market/components/format.ts`（fmtYi/fmtSignedYi/fmtWanGu/fmtYiGu/fmtNorthYi 五个单位换算格式化器，null/undefined 统一返 "—"）；新增 `SectorMoneyflowCard`（行业/概念 Segmented 切换，`["sector-moneyflow", dimension]` staleTime+refetchInterval 双 60s 盘中轮询，TOP10 主力净流入横向柱图正红负绿，tooltip 带板块涨跌幅/主力净占比，非交易时段无数据展示"暂无资金流数据"空态）；新增 `NorthboundCard`（`["northbound", 30]` staleTime 5min，30 日净流入折线+零轴虚线，当日/近30日累计红绿着色，万元→亿换算）；市场页 Row2 由 CapitalFlowChart+HotSectors 换为 SectorMoneyflowCard+NorthboundCard，Row3 HotSectors 全宽（span=24）；删除近似实现 `CapitalFlowChart.tsx`（grep 确认仅 barrel+市场页引用）；`npm run build` 通过，活栈 `/market` 200，资金流卡 60s 自动刷新经 Network 请求复现（northbound 仅首载一次，符合无 refetchInterval 语义）
- 涉及模块：frontend/features/market/components, frontend/pages/market

## 2026-09-03 - 市场数据面 Task 14 — 数据面 Tab 区块（龙虎榜/大宗/解禁/回购/公告快讯）
- 新增 `MarketDataBoard`（antd Tabs 懒挂载 5 pane，首激活才发 useQuery）；`dataFace/` 五组件：`DragonTigerTable`（涨跌幅/净买额 ±红绿+金额加粗，行点击跳个股）、`BlockTradeTable`（成交额万元→亿新增 `fmtWanYi`，买/卖营业部 ellipsis）、`ShareFloatTable`（解禁数量亿股、占总股本%、类型）、`RepurchaseTable`（进度 Tag 实施=blue/完成=green/其他=default、金额元→亿、数量股→万股内联换算）、`AnnouncementFeed`（List 时间+分类 Tag+证简称+PDF 新窗链接，30 条滚动）；统一表格规范 size=small/pagination=false/scroll.y=320/空态文案；顺手清理 SectorMoneyflowCard 遗留的死导入 `fmtYi`；活数据校准两处 rowKey 防撞键（解禁同股多持有人追加 holderName、大宗同股同价同买方追加 volume）；`npm run build` 通过，活栈 `/market` 五 Tab 实数据切换验证通过（大宗 9034.54万→0.90亿换算核对）
- 涉及模块：frontend/features/market/components, frontend/pages/market

## 2026-09-03 - 市场数据面 Task 15 — 个股详情页相关数据卡（公告/龙虎榜/大宗/解禁/回购）
- 新增 `features/stock-detail/components/RelatedEvents.tsx`（Card title="相关数据" + Segmented 五视图：公告默认 Tab（时间+标题 PDF 新窗链接）、龙虎榜（fetchDragonTiger(50) 全市场最新日客户端 filter 本股，净买额 ±红绿）、大宗（fmtWanGu/fmtWanYi）、解禁（fmtYiGu/占比%）、回购（进度+fmtYi 金额）；每 Tab 独立 queryKey 且 enabled 门控首激活才请求，staleTime 5min，统一 size=small/pagination=false/空态文案，footer 注明"龙虎榜为全市场最新日筛选本股"数据语义）；接线 `pages/stock-detail/index.tsx`（K线/基础信息 Row 之后、末尾免责声明 Divider 之前插入全宽 Row，symbol 取 `stock.symbol` 规避 useParams 可空类型）；删除 brief 末尾防未用报错的脚手架残留（hidden span + useNavigate import）；`npm run build` 通过，活栈 `/stock/600519` 公告默认 Tab + 五 Tab 空态切换验证、`/stock/002536` 龙虎榜实数据（+4.59亿 红色）验证，零 console 错误
- 涉及模块：frontend/features/stock-detail/components, frontend/pages/stock-detail
- Review fix 1：RelatedEvents 大宗/解禁 rowKey 对齐 Task 14 dataFace 实证修复——大宗 `${tradeDate}-${buyer}-${price}` 追加 `-volume`（同股同日同价同买方多笔撞键）、解禁 `${floatDate}-${shareType}` 追加 `-holderName`（同股同日同类型多持有人撞键），照抄 brief 键前先核对既有同数据组件的 rowKey 教训

## 2026-09-03 - 市场数据面收尾 — e2e 用例与文档（Task 16，P1-P10 完成）
- 计划级总结——2026-09-03 市场数据面：全球市场指数区块（亚洲/美洲 Tab、徽章卡+30日sparkline）、板块主力资金流盘中轮询卡、北向资金折线卡、数据面五类榜单（龙虎榜/大宗/解禁/回购/公告快讯，TuShare+东财+巨潮）、个股相关数据卡；新增 7 表与 `market_data.fetch` 队列。
- 新增 `frontend/e2e/marketDataFace.spec.ts` 5 用例（全球市场 Tab 与指数卡/全球指数详情/板块资金流行业概念切换/数据面 Tab 表格/个股相关数据卡），数据缺失处按惯例 test.skip 守卫；全量 e2e 22 通过；`docs/design/data-source.md` 补「市场数据面数据源」小节（实测端点/字段/单位/调度限频）。
- 涉及模块：frontend/e2e, docs/design/data-source.md, docs/references/best-practices.md

## 2026-09-03 - 市场数据面评审修复
- 修复 RepurchaseTable rowKey 冲突（同日多进度回购行：补 proc 维度），并移除 market.ts 中无消费方的 `fetchSseLatestSnapshots`。
- 2026-09-04 市场数据面缺陷修复：调度守卫时区改用 Asia/Shanghai（容器 UTC 下盘中资金流轮询与 SSE 快照从未真正触发）；龙虎榜/大宗采集改补漏模式（自表内最新交易日起逐日拉齐缺失交易日，当日未发布自动重试）并补回 09-03 缺口；巨潮公告分页上限 5→10 页（重大事项类 3 天窗口实测 189 条，5 页截断 39 条）。
- 2026-09-04 资金流对齐东财数据中心板块资金页：新增地域维度（fs=m:90+t:1）与主力净流入最大股（f128/f140/f136），全链路入库到前端悬停展示；龙虎榜/大宗补漏模式顺带在空表上自动回补 9 个交易日历史。
