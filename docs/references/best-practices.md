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
- 新增 Worker 队列消息类型时，需要同步新增：Schema（`Fetch*Request`）、Service 方法（`trigger_fetch_*`）、API 端点（`POST /tasks/fetch-*`）—— 三者缺一则端到端不通。
- 定时任务的"时间窗判断"必须与 APScheduler 的 CronTrigger 使用同一时区：容器默认 UTC 时，`CronTrigger(timezone="Asia/Shanghai")` 会在正确的北京时间触发，但 job 内部再用 `datetime.now()`（UTC）判断 `_in_trading_hours()` 会永远为 False，导致任务全部被静默跳过；同理，"回填上一交易日"必须查 `trade_cal` 而不是 `weekday()` 推算，否则节假日后会产生永久缺口。
- 新产品模块（如行业投研工作台）落地前，先用单文件 HTML + CDN ECharts 做高保真交互原型验证信息架构与布局（结论先行、证据下钻、数据源权威性分级徽章），再迁移为 React 组件，可大幅降低前端返工成本；原型视觉应贴近真实技术栈（antd v5）而非另起炉灶。
- 跨行业可复制的产品（投研工作台）应"一套资产服务所有行业"：指标单表（industry_key + nullable stock_id + metric_key + source + period）+ 代码级指标注册表（metric registry）+ 派生指标统一落表 + 源适配器隔离；接入新行业 = 配置 + 采集器，而非新表新页面。会随政策修订的参考锚点（如能繁正常保有量 4100→3900→3750）必须入库带生效日期，禁止硬编码。
- 纯函数规则引擎中所有"转多"判定分支（阶段复苏、左侧布局信号）都应显式要求正向证据在场（如盈亏口径任一非空），避免 None 缺失值在布尔短路中被静默当作"已确认"；并用无 DB 的纯单测把该不变量锁定为回归门。
- 演示/mock 数据源在源优先级列表中必须永远垫底，fetcher 实际写入的 source 名必须与 registry 声明一一对应，且真实源首次成功落库后应主动清除 mock 行——否则切换真实数据后旧演示行会继续压过真实值；排序与登记不变量用纯单测锁定。
- 同一指标表内并存多种频率时，"最新值"裁决必须先按 registry 注册频率过滤再比日期（DISTINCT ON 也要把 freq 纳入去重维度）——否则月末归档行（period=月末）天然晚于日度行，会借未来日期压过当日数据；日度→月度 rollup 行应作为独立 upsert 阶段先于依赖它的派生计算落库，并打 extra 标记区分来源。
- 同表并存多种频率的指标，唯一约束必须把 freq 纳入冲突键：月末日期可同时承载日度观测与月度归档行，约束缺 freq 时同批 upsert 直接触发 PG "cannot affect row a second time"（事务硬失败），且月度行会覆写日度行 freq 导致 rollup 非幂等——用"批内 (key, source, freq, period) 无重复 + 月末跨频合法共存"两条纯单测把不变量钉死。
- 回补窗口类参数（months）必须从 API schema → worker payload → service → fetcher 全链贯通并各自设默认值，任何一层残留硬编码窗口（如 `df.tail(45)`）都会让上游参数静默失效；生成演示序列时"末点精确等于基准值"（日度末点==月度最新值）要靠生成器结构保证并用纯单测钉死，不能依赖抖动碰巧为零。
- 读路径（GET）不得隐式写库：信号/派生类结果应在 ingest 时评估落表、查询时只读存储行，最多保留"空库引导补算一次"的兜底；写通道入口（人工/CSV batch）必须按白名单校验 source，防止伪造采集适配器专属来源污染源优先级裁决。
- 封装组件的 prop 语义即契约（如 EChart 的 silent 必须真正关 animation+tooltip，不能用换 renderer 这类无副作用的近似实现），文案/标签类展示值应由后端 payload 下发而非前端维护重复映射表（只留纯展示常量如颜色）——否则后端语义演进（新枚举值、切换真实数据源）时前端会静默回退或误标。
- 数据源切换的"清除演示数据"必须连同派生行一起删：派生计算只 upsert 不删除，仅删 mock 基础行时由 mock 算出的 derived 序列会存活并继续喂给规则引擎，且前后端透传的 JSONB dict 键名要保持 snake_case 一致（前端读 camelCase 会静默不渲染，类型断言不报错）。
- MQ 任务派发必须"先提交任务行、后发消息"：消费者可能在生产者事务提交前收到消息，查询不到行会静默跳过状态更新，任务永久 pending。
- 接入第三方数据源必须"先实机验证、再写适配器"：公开文档的函数名/参数/返回形状常滞后甚至失效（本次生意社 `futures_spot_sys` 文档在、实跑已因页面改版抛 AttributeError），应把验证结论（日期、包版本、列名、窗口）固化进客户端注释与表驱动规格，并用纯单测锁死"fetcher 写入的 source 名 ⊆ registry 声明"——错名会让源优先级裁决永远匹配不到真实行。
- Agent 指令文件（AGENTS.md/CLAUDE.md）必须保持单一事实来源：CLAUDE.md 用 symlink 或一行转发指向 AGENTS.md 而非拷贝；同类沉淀文档不可并存近似命名（best-practice.md vs best-practices.md 曾同时被更新导致经验分裂）；AGENTS.md 中的命令必须实跑验证后再写入（本次发现 ruff/mypy 需 `uv run --extra dev`、frontend eslint 需先 `npm install`）。

以下条目合并自 docs/references/best-practice.md（2026-09-03，两文件合一）：

- Python 需要使用 Type Hinting / Type Checking / Annotations / Decorators 等技术手段，来提高代码的健壮性和可读性，多使用 Compose 而不是 Extension。
- 行业分级页面建议采用“层级选择状态 + 统一个股表格”的单一数据视图模式，避免一级/二级/三级分别维护重复表格逻辑导致状态不一致。
- 涉及层级导航的页面应使用显式独立路由承载每一级职责，让“点击下钻”和“同页筛选”交互分离以降低用户认知负担。
- 板块中心类页面推荐采用“主界面分组预览 + 详情页完整表格”的双层结构，既保证信息密度也降低首次加载认知成本。
- 后端多源行情抓取建议采用“先交易所 crawler、再 AKShare、最后 yfinance”的降级链路并内置随机退避与限速以降低反爬触发概率。
- 前后端联调阶段应优先让页面消费真实后端字段并通过前端适配层兜底缺失指标，避免长期依赖 mock 造成接口契约漂移。
- 迁移前端到真实后端接口时，应先建立统一的 API 适配层并集中做字段映射，再逐页面替换调用以减少回归风险。
- 分层行业页面去 mock 时应先后端化层级树和分层股票查询，再让前端页面复用同一套树数据以保证路由与数据口径一致。
- 清理废弃 mock 文件前应先全局检索引用并在删除后执行一次完整构建回归，避免隐式动态依赖遗漏。
- 集成第三方同步 SDK（如 TuShare）时，应通过 asyncio.to_thread 包装并内置请求间隔节流与重试机制，同时将每次 API 原始响应以 JSONL 原子写入本地 data/ 目录作为防丢失备份。
- Docker 多阶段构建应将依赖安装与源码复制分层，利用层缓存加速重建；前端 Dockerfile 应保留完整构建路径的同时支持 `target: runtime` 跳过构建阶段以适配网络受限环境。
- 前端多阶段镜像的运行时阶段必须通过 `COPY --from=<builder>` 获取构建产物，避免误从构建上下文复制 `dist/` 导致镜像构建失败。
- Docker Compose 编排应使用 `service_completed_successfully` 条件让迁移容器在应用服务启动前完成数据库 schema 初始化，避免应用启动时表不存在。
- 统一数据源应选择 API 稳定、覆盖面广的单一供应商（如 TuShare Pro），避免多源降级链路的维护负担和数据口径不一致；批量拉取应按 trade_date 而非 ts_code 循环以减少请求次数（220 交易日 vs 5000+ 股票）。
- 首次启动应自动检测空库并后台异步拉取初始数据（不阻塞 API），保证服务可用性的同时逐步填充真实数据。
- Redis 持久化卷在容器编排中应先通过一次性 init 步骤统一修正目录属主，再启动业务容器，避免 `MISCONF` 导致写缓存失败并放大为上层 500。
- 静态分类数据（如行业分类）应优先从本地文件解析入库而非依赖外部 API 实时拉取，既保证离线可用又避免 API 限流问题；同步使用数据库表存储以支持 JOIN 聚合查询。
- Python 镜像构建应使用 `uv.lock` 的 frozen 导出流程并配置国内默认源（如 TUNA）与更长 HTTP 超时，同时保留 BuildKit 缓存挂载，避免解析/下载抖动导致构建超时。
- 对“体量大但更新频率低”的静态映射数据，推荐采用“首次解析源文件并自动导出 SQL 种子，后续部署优先导入 SQL”的策略，兼顾数据可追溯性与启动性能。
- 导入策略建议显式分层为“SQL 种子优先、源文件解析兜底”，并在启动日志中打印实际命中路径，便于排查“文件存在但未生效”的环境问题。
- Docker 构建涉及种子文件时，需显式检查 `.dockerignore` 排除规则并为目标文件添加白名单（如 `!data/sw_seed.sql`），否则运行时会出现“容器内文件缺失”的隐蔽故障。
- Ant Design Table 的分页大小切换应由组件状态显式持久化（避免把 `pageSize` 写成固定常量），否则在排序/筛选触发重渲染后会回退到初始值并造成“条数切换无效”。
- 当页面展示的是服务端分页切片数据时，分页总数与最大页计算必须使用后端 `total` 而非当前页 `data.length`，并由父组件统一驱动 `current/pageSize` 避免 UI 与请求状态漂移。
- 行业树统计应与可展示股票集合保持同一口径（以 `stocks` 为准），并为无法映射到有效行业层级的标的提供“其他”兜底分类，避免前端总量与分组总和不一致。
- Docker Compose 场景下 Nginx 反向代理上游应启用 Docker DNS 动态解析（`resolver 127.0.0.11` + 变量 `proxy_pass`），避免后端容器重建后因缓存旧 IP 导致持续 502。
- 同一层级列表中的点击交互应统一为路由下钻并保持视觉反馈一致，避免仅对特殊分组采用页内筛选造成信息区块语义错位。
- 新增 TuShare 数据源时，应遵循“client 方法 → ingest 方法 → model/迁移 → repo → service → worker → API 端点”的完整链路搭建模式，确保从爬取到展示的每个环节都有独立模块，便于单点测试和问题定位。
- 自定义标签系统应与现有分类体系独立设计：后端独立建表存储，前端独立组件管理，并通过专用页面展示聚合视图，避免与已有分类逻辑耦合。
- 单股详情页 FundamentalCards 估值指标（marketCap/circulatingCap/PE/PB）依赖 enriched 接口，列表接口返回的 StockOut 不包含这些字段；新增详情接口时应复用同一 SQL 查询并统一缓存 key 前缀（如 `stock:enriched:`）以保证数据一致性。
- ROE（净资产收益率）、营收同比、净利润同比等财务成长指标需 TuShare `fina_indicator` / `profit_data` 接口支持，后端需新增数据入库链路后方可展示；在此之前 FundamentalCards 应提供降级展示策略或临时隐藏相关指标。
- Playwright 浏览器测试在中文重文案页面上，`getByText`/正则会同时命中嵌套容器或相邻重复文案而触发 strict mode violation；断言应优先落在唯一容器上用 `toContainText`，或用 `.locator(".ant-tag").filter({ hasText })` 一类结构选择器限定作用域。
- 官方转载源（如协会月度文章）解析应拆成"网络抓取壳 + 纯解析函数"两层：解析用真实页面快照 fixture 做离线单测锁定正文形状（含环比方向词归一、数据期优先取标题月份），抓取壳只做发现与容错（任何失败 log 后返回 None 不抛穿），并为列表页改版预留显式 URL 设置逃生通道。
- 批量 `INSERT ... ON CONFLICT DO UPDATE` 前必须保证单批内冲突键唯一：多源共存（真实源+mock 演示）的历史序列做派生时，先按 registry 源优先级逐 period 去重，否则同批重复键直接 CardinalityViolation 使整个 ingest 任务失败。
- 逐项容错（per-item skip+log）的采集任务必须把每项错误摘要写进任务 result：否则接线类 bug（如 upsert 缺 db 参数）只留一条日志警告、任务仍报 completed，实跑"成功"零数据要到查库才发现。
内容型功能（知识库/图谱/原则）应落"迁移内 seed + JSONB 内容表 + 读路径装配"而非硬编码前端：内容单点维护在 seed 模块供迁移与单测共用，前端组件零行业知识、Playwright 断言锚定 `.ant-card-head-title` 一类标题容器以规避徽章同文案混淆。
SPA 内页断言同文案 Tag 时先等"目标页独有元素"挂载再取全局 locator：列表与详情页出现同名 Tag（如行业卡片与工作台头部的"周期阶段"）后，路由 URL 变更与 React 卸载旧页之间存在空窗，Playwright strict mode 多元素错误即时抛出不重试，仅 waitForURL 不足以防护。
- echarts-for-react 默认 merge 模式下，用户交互过的组件状态（如 dataZoom 滚轮缩放窗口）不会被新 option 同名配置重置：数据全集切换的图表必须 `notMerge`（对齐 shared/ui/EChart 封装），且不要用固定 start/end 百分比裁剪初始视图——周期切换类交互的正确语义是"所选区间全量展示 + 每次切换重置缩放"。
- antd 栅格内卡片等高要"双保险"：内容侧 Typography `ellipsis`（描述 `tooltip:true`）消除换行撑高，布局侧 Col `display:flex` + Card `height:100%` 拉伸兜底；flex 行内文本省略号必须给文本容器 `minWidth:0`（flex item 默认 min-width:auto 不收缩），Tag/图标侧补 `flexShrink:0`。
- 重复图表组件的合并应先落纯函数层（计算/格式化/裁剪）并配 barrel 导出，且把类型签名当依赖契约先于组件实现冻结（任务 brief 的 Interfaces 块即签名源）——后续 UI 任务只依赖稳定签名，不再各自重复实现；brief 代码块可用 diff 逐字校验落地无漂移。
- ECharts option builder 保持返回未注解的结构化对象（推断类型天然可赋给 `Record<string, unknown>`），formatter 一律收 `params: unknown` 再局部断言；当 `string` 参数要索引 `Partial<Record<字面量联合,…>>` 时直接把参数收窄为字面量联合类型（如 `MaKey`），比在索引处加 `as` 断言更不易漂移。
- antd 5.x 的命名导出随小版本漂移：子组件（如 CheckableTag）可能只挂在主组件命名空间（`Tag.CheckableTag`）而非顶层导出，逐字移植参考代码时先对齐代码库内同组件既有用法再定 import 形态；此类 TS2305 还会连带制造"参数隐式 any"的次生报错，修掉根因即一并消除，无需逐个补类型。
- antd 5 Segmented 的 radio input（`.ant-segmented-item-input`）是零尺寸隐藏元素：Playwright 对 `getByRole("radio")` 做 toBeVisible/click 必失败，E2E 应操作可见的 `label.ant-segmented-item`（label 点击天然转发到 input），选中态改断言其 `ant-segmented-item-selected` 类。
- 手写 `UPDATE ... FROM (VALUES ...)` 派生表 SQL 时，未定型的日期字符串字面量会被 PostgreSQL 推断为 text 列，与实体表 date 列比较直接抛 `operator does not exist: date = text`——VALUES 行内必须显式 `'...'::date` 转型；此类 SQL 类型错误纯函数单测覆盖不到，接线任务必须以实机验证（docker 重建 + curl + psql 计数）闭环。
- 后端能力未就绪（如复权因子懒加载中）的前端控件应降级为"禁用+Tooltip 提示"而非条件隐藏：DOM 结构保持稳定让 E2E 能以长超时轮询等就绪（禁用态也渲染完整选项结构），且禁用只锁视觉层——受控 state 与 queryKey 不变、value 固定为当前真实展示值，能力恢复后无缝启用且不发额外请求。
- 复权基准必须随最新因子滚动（qfq=当日因子/最新因子），且缓存 key 必须包含复权维度——否则 qfq 结果污染 raw 缓存；数据不完整时宁可不缓存，靠回补后的 delete_pattern 兜底。
- UPSERT 覆盖"懒回补型"可空字段（如 adj_factor）时 SET 子句必须 COALESCE(excluded.x, table.x) 防 NULL 重灌抹掉历史回补值；回补的幂等判定口径必须与读取端可用性口径一致（按最新交易日行而非"任一行非空"），并为真实外呼加短 TTL 冷却 key 防数据未发布期间高频重拉——三者缺任一都会形成"不可用但永不修复"的跨日死锁。
- JSX 表达式间字面空格（`{d.key} {expr}`）在末表达式为空串时会留下尾部空格文本（如 "MA60 "），而 Playwright `getByText` 正则匹配不做首尾 trim——行尾锚定（`/^MA60$/`）必失败；此类断言应放宽为 `\s?` 或在组件侧条件拼接避免悬空空格，卡死时先抓 error-context 快照看实际 DOM 文本再改正则。
- antd CheckableTag 选中态自带主题色实底，inline 彩色文字色会与之撞色（对比度~1.2:1）——彩色图例类控件用图内绝对定位文本行（线色文字 on 白底），不要用 CheckableTag 承载。
