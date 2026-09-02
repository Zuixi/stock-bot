# Best Practices

项目开发过程中沉淀的经验教训。

---

- 外部数据源爬取服务应设计为独立的 scheduler 容器进程，通过 APScheduler 管理定时任务，避免耦合到 API 主进程中影响请求处理性能；反爬策略（UA 轮换、cookie 持久化、随机 jitter、指数退避）应在服务层统一封装，而非散落在各调用点。
- 启动期历史行情回补应先做覆盖度判定再按股票小并发分批抓取，只补缺口并在客户端统一限流，避免全量无差别拉取造成 IO 与外部 API 压力峰值。
- 分类/标签等用户可编辑的多对多关系应独立建表并采用"先删后插"的全量替换策略，避免增量 diff 逻辑复杂化；合成分类节点（如"其他"）应复用现有字段自动分组，减少用户手动维护成本。
- 新增 Worker 时必须同步在 `app/core/mq.py` 的 `QUEUES` 字典中注册对应的 `queue_key` → `queue_name` 映射，否则 `BaseWorker.run()` 会因 KeyError 启动失败。映射规则：`queue_name = "stock_bot." + queue_key`。
- 多级联动多选控件应以上层选项动态约束下层候选集，并在上层变更时剔除失效下层值，保证提交数据始终满足父子层级关系。
- 联动筛选状态应直接由当前上层已选值派生（而非间接缓存变量），确保禁用态、placeholder 与候选集在同一次渲染中保持一致。
- 当分类数据同时存在“官方成员映射”和“用户自定义标签”时，应在树统计、详情列表和兜底分类中统一按并集去重计算，防止展示与筛选结果不一致。
- 对会影响聚合视图的数据编辑操作，应成对执行“前端 query invalidation + 后端 Redis 聚合键清理”，避免页面在 TTL 期间显示过期计数。
- 列表页的批量金融数据展示应使用单次 JOIN 查询（`DISTINCT ON` + 子查询）一次获取全部股票的行情/基本面字段，而非前端逐只股票 N+1 请求，避免首屏数据空白和 API 洪泛。
- 当已有页面解决过同类问题时，优先复用其基础设施（schema、service、SQL、前端映射函数）而非重新实现。本次 `market-industry-level2` 的 `StockEnrichedOut` + `get_stocks_enriched_by_symbols()` + `mapBackendStockEnriched()` 整套链路可直接复用，只需新增一个端点。
- Docker build 缓存不可信：`COPY . .` 步骤即便显示 `DONE`（非 CACHED），实际可能未检测到文件变更（OrbStack on macOS 已知问题）。每次 rebuild 后必须 `docker exec` 验证容器内文件内容，不可仅依赖构建输出。
- 调试数据空白问题时，优先直调后端 API 确认响应字段，再追代码。空字段可能来自三层中任意一层：后端未查 → schema 未定义 → 前端映射硬编码 undefined。本次三层全中。
- 公共类型/映射函数应集中在语义匹配的模块中（如 `BackendStockEnriched` 从 `swIndustry.ts` 移到 `stocks.ts`），避免使用者因模块名误导而重复实现。
- 数据回填路径应采用"APScheduler 定时任务（自动）+ RabbitMQ Worker（手动触发）"双轨制：APScheduler 处理每日增量避免遗漏，Worker 队列支持手动任意时间回补；两者共用同一 `ingest_daily_*` Service 方法，保证逻辑一致性。
- 批量金融数据查询中 `DISTINCT ON (stock_id) ... FROM daily_quotes ORDER BY stock_id, trade_date DESC` 会对**全表**做 Seq Scan + Sort（3.8M rows），与 WHERE 条件解耦导致单次查询 3.5s。应使用 `LATERAL (SELECT ... WHERE stock_id = s.id ORDER BY trade_date DESC LIMIT 1)` 将过滤条件推入子查询，利用 `(stock_id, trade_date)` 复合索引实现 O(1) 每股票查询，延迟从 3.5s 降至 ~20ms（150x+ 提升）。
- 新增 Scheduler 定时任务需要在 `runner.py` 的 `create_scheduler()` 中注册 `CronTrigger` 并在 `jobs.py` 中实现处理函数；同时在 `mq.py` 的 `QUEUES` 字典注册队列名，使 Worker 能消费消息。
- 新增 Worker 队列消息类型时，需要同步新增：Schema（`Fetch*Request`）、Service 方法（`trigger_fetch_*`）、API 端点（`POST /tasks/fetch-*`）—— 三者缺一则端到端不通。- 定时任务的"时间窗判断"必须与 APScheduler 的 CronTrigger 使用同一时区：容器默认 UTC 时，`CronTrigger(timezone="Asia/Shanghai")` 会在正确的北京时间触发，但 job 内部再用 `datetime.now()`（UTC）判断 `_in_trading_hours()` 会永远为 False，导致任务全部被静默跳过；同理，"回填上一交易日"必须查 `trade_cal` 而不是 `weekday()` 推算，否则节假日后会产生永久缺口。
- 新产品模块（如行业投研工作台）落地前，先用单文件 HTML + CDN ECharts 做高保真交互原型验证信息架构与布局（结论先行、证据下钻、数据源权威性分级徽章），再迁移为 React 组件，可大幅降低前端返工成本；原型视觉应贴近真实技术栈（antd v5）而非另起炉灶。
- 跨行业可复制的产品（投研工作台）应"一套资产服务所有行业"：指标单表（industry_key + nullable stock_id + metric_key + source + period）+ 代码级指标注册表（metric registry）+ 派生指标统一落表 + 源适配器隔离；接入新行业 = 配置 + 采集器，而非新表新页面。会随政策修订的参考锚点（如能繁正常保有量 4100→3900→3750）必须入库带生效日期，禁止硬编码。
- 纯函数规则引擎中所有"转多"判定分支（阶段复苏、左侧布局信号）都应显式要求正向证据在场（如盈亏口径任一非空），避免 None 缺失值在布尔短路中被静默当作"已确认"；并用无 DB 的纯单测把该不变量锁定为回归门。
- 演示/mock 数据源在源优先级列表中必须永远垫底，fetcher 实际写入的 source 名必须与 registry 声明一一对应，且真实源首次成功落库后应主动清除 mock 行——否则切换真实数据后旧演示行会继续压过真实值；排序与登记不变量用纯单测锁定。
