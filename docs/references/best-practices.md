# Best Practices

项目开发过程中沉淀的经验教训，按主题归档。每季度或大特性合入时建议通读一遍，删除过时项、合并重复项。

目录：
- [一、数据源与采集](#一数据源与采集)
- [二、数据库与性能](#二数据库与性能)
- [三、Docker 与部署](#三docker-与部署)
- [四、前端（React / antd / ECharts）](#四前端react--antd--echarts)
- [五、测试与 E2E](#五测试与-e2e)
- [六、架构与分层](#六架构与分层)
- [七、指标建模与规则引擎](#七指标建模与规则引擎)
- [八、工程流程与文档（元经验）](#八工程流程与文档元经验)

## 自检探测器映射表

完成前自检第二步：对 `git diff` 用「分类→关键词」映射 grep，命中即复核对应分类条目。命中本身不一定是错误，但必须确认没有重复踩雷。

| 分类 | 探测器关键词（`git diff | grep -i <key>`） |
|------|---------------------------------------------|
| 数据源与采集 | `source` · `registry` · `mock` · `trade_cal` · `ZoneInfo` · `Asia/Shanghai` · `to_thread` · `RUN_SCHEDULER` · `scheduler` · `worker` · `QUEUES` |
| 数据库与性能 | `DISTINCT ON` · `LATERAL` · `N+1` · `index(` · `ON CONFLICT` · `COALESCE` · `::date` · `SELECT` |
| Docker 与部署 | `Dockerfile` · `dockerignore` · `COPY --from` · `resolver` · `target: runtime` · `seed` · `service_completed_successfully` |
| 前端 | `antd` · `EChart`/`notMerge` · `toFixed` · `formatCap` · `unit` · `rowKey` · `CheckableTag` · `Segmented` · `Tooltip` |
| 测试与 E2E | `playwright` · `getByText` · `toContainText` · `toBeVisible` · `getByRole("radio")` · `strict` |
| 架构与分层 | `schema` · `repository` · `_dispatch_task` · `QUEUES` · `JWT` · `mTLS` · `whitelist` |
| 指标建模与规则引擎 | `metric_key` · `freq` · `period` · `rollup` · `report_version` · `calc_method` · `match` · `source` |
| 工程流程与文档 | `Alembic`/`revision` · `Changelog` · `AGENTS` · `README` · `best-practices` · `docker-compose` · 端口号 |

---

## 一、数据源与采集

- 外部数据源爬取服务应设计为独立的 scheduler 容器进程，通过 APScheduler 管理定时任务，避免耦合到 API 主进程中影响请求处理性能；反爬策略（UA 轮换、cookie 持久化、随机 jitter、指数退避）应在服务层统一封装，而非散落在各调用点。
- 启动期历史行情回补应先做覆盖度判定再按股票小并发分批抓取，只补缺口并在客户端统一限流，避免全量无差别拉取造成 IO 与外部 API 压力峰值。
- 数据回填路径采用"APScheduler 定时任务（自动）+ RabbitMQ Worker（手动触发）"双轨制：APScheduler 处理每日增量避免遗漏，Worker 队列支持手动任意时间回补；两者共用同一 `ingest_daily_*` Service 方法，保证逻辑一致性。
- 新增 Worker 时必须同步在 `app/core/mq.py` 的 `QUEUES` 字典注册 `queue_key` → `queue_name` 映射，否则 `BaseWorker.run()` 因 KeyError 启动失败；映射规则 `queue_name = "stock_bot." + queue_key`。新增 Scheduler 定时任务需在 `runner.py` 的 `create_scheduler()` 注册 `CronTrigger` 并在 `jobs.py` 实现处理函数，同时注册队列供 Worker 消费；新增 Worker 队列消息类型需同步补齐 Schema（`Fetch*Request`）+ Service（`trigger_fetch_*`）+ API 端点（`POST /tasks/fetch-*`），三者缺一则端到端不通。
- 接入第三方数据源必须"先实机验证、再写适配器"：公开文档的函数名/参数/返回形状常滞后甚至失效（本次生意社 `futures_spot_sys` 文档在、实跑已因页面改版抛 AttributeError），应把验证结论（日期、包版本、列名、窗口）固化进客户端注释与表驱动规格；同时用纯单测锁死"fetcher 写入的 source 名 ⊆ registry 声明"——错名会让源优先级裁决永远匹配不到真实行。
- 数据源策略演进：早期以"交易所 crawler → AKShare → yfinance"降级链路起步，后收敛为 API 稳定、覆盖面广的单一主源（**TuShare Pro**）做统一，避免多源降级链路的维护负担与数据口径不一致；批量拉取应按 trade_date 而非 ts_code 循环以减少请求次数（220 交易日 vs 5000+ 股票）。
- 首次启动应自动检测空库并后台异步拉取初始数据（不阻塞 API），保证服务可用性的同时逐步填充真实数据；静态分类数据应优先从本地文件解析入库（离线可用、避免限流），并用数据库表存储以支持 JOIN 聚合。
- 集成第三方同步 SDK（如 TuShare）时，应通过 `asyncio.to_thread` 包装并内置请求间隔节流与重试机制，同时将每次 API 原始响应以 JSONL 原子写入本地 `data/` 目录作为防丢失备份。
- 新增 TuShare 数据源时，遵循"client 方法 → ingest 方法 → model/迁移 → repo → service → worker → API 端点"的完整链路搭建，确保从爬取到展示每环节独立成模块，便于单点测试与问题定位。
- 官方转载源（如协会月度文章）解析应拆成"网络抓取壳 + 纯解析函数"两层：解析用真实页面快照 fixture 做离线单测锁定正文形状（含环比方向词归一、数据期优先取标题月份），抓取壳只做发现与容错（任何失败 log 后返回 None 不抛穿），并为列表页改版预留显式 URL 设置逃生通道。
- 逐项容错（per-item skip+log）的采集任务必须把每项错误摘要写进任务 result：否则接线类 bug（如 upsert 缺 db 参数）只留一条日志警告、任务仍报 completed，实跑"成功"零数据要到查库才发现。
- 演示/mock 数据源在源优先级列表中必须永远垫底，fetcher 实际写入的 source 名必须与 registry 声明一一对应，且真实源首次成功落库后应主动清除 mock 行——否则切换真实数据后旧演示行会继续压过真实值；排序与登记不变量用纯单测锁定。**清除演示数据必须连同派生行一起删**：派生计算只 upsert 不删除，仅删 mock 基础行时由 mock 算出的 derived 序列会存活并继续喂给规则引擎。
- 行业覆盖缺口的合并优先用三方分类接口交叉验证而非纯名称匹配：TuShare `index_member_all` 有 3000 行上限且不支持按股查询；东财 push2 接口 f127 字段与申万 2021 同名可直接映射（突发批量会被限流，push2delay 镜像 + 0.3s 间隔可绕），特例用同花顺 F10 双源核验；落库走幂等 custom tag 表而非改原始字段。
- 人工策展数据进 repo 用"overlay 种子文件 + 加性 ON CONFLICT"而非追加进自动再生成的种子（会被下次导出抹掉），并同步补 `.gitignore` 的 `data/*` 豁免与 `backend/.dockerignore` 的 `data/*` 豁免——漏 dockerignore 会导致镜像内文件缺失、加载器静默返回 0。
- 东财 push2/push2delay 主站可用性日内可变（实测当日 push2 上午可用、下午开始 TCP 拒连）——数据源客户端的域名 base 以"当天实测可达"为准切换并留时间戳注释，clist 类端点在 push2delay 同构镜像可用（字段/数值一致），避免盘中轮询任务对不可达域名反复重试静默失败；排查连通性时 host 与容器两侧都要 curl（代理/DNS 差异）。
- TuShare 部分接口（如 moneyflow_hsgt）全列返回字符串且缺值为 NaN/空串——数值归一（str→float|None）必须在映射层一次完成并对 NaN/空串显式兜底，让 repo/DB 只见规范类型；pg `ON CONFLICT DO UPDATE` 的 set_ 只列会变化的字段，首写后不变的字段（如 source）留在 values 中即可，避免无谓覆盖。
- 复权基准必须随最新因子滚动（qfq=当日因子/最新因子），且缓存 key 必须包含复权维度——否则 qfq 结果污染 raw 缓存；数据不完整时宁可不缓存，靠回补后的 delete_pattern 兜底。
- 事件类数据（解禁/回购）的读取窗口语义由业务性质决定而非对齐 ingest 窗口：解禁 `float_date` 是未来事件（缺省窗口须含 today+N 未来段），回购按 `ann_date` 回看——同表两种"默认窗口"方向相反时在 service docstring 写明因果，避免后人"统一"成今日窗口把未来解禁静默滤掉；另外 Postgres 唯一约束不判重 NULL（`ann_date` 可空的 DO NOTHING 去重存在旁路）属可接受偏差，须在 repo docstring 落字说明而非默默容忍。
- 复用网站数据先比对页面 HTML 里的实体代码（东财 BK 板块码）：代码一致即同源，排行页的扩展列（最大股/中单小单）多数在同端点 fields 里就有，无需另找接口。
- 东财 kline 类接口（fflow/daykline 等）返回 CSV 字符串行，数值必须显式 `float()`；容器内长连接池偶发被服务端断连（RemoteProtocolError），HTTP GET 加一次传输层重试即可消除偶发失败。
- "按需单只抓取"的数据如果在 UI 可见却无自动补数，会表现为"部分标的空白"的伪 bug；改为与行情一致的全市场自动回填：幂等 upsert + "已有≥1条报告版本即视为已补"的续跑判据 + 调度批处理（分批/续跑/失败单只隔离）三件套，新标的自动被后续批次覆盖。
- 定时任务的交易时段/工作日守卫必须显式 `ZoneInfo("Asia/Shanghai")` 且与 CronTrigger 使用同一时区：容器默认 UTC，`CronTrigger(timezone="Asia/Shanghai")` 会在正确的北京时间触发，但 job 内部再用 naive `datetime.now()`（UTC）判断（如 `_in_trading_hours()`）会永远为 False，任务全部被静默跳过，日志只有 executed successfully 没有业务结果；同理，"回填上一交易日"必须查 `trade_cal` 而不是 `weekday()` 推算，否则节假日后会产生永久缺口。
- 第三方行情先 curl 实测定字段与单位再写映射：东财 f62 是元、TuShare block_trade 是万元/万股、north_money 是万元、巨潮 announcementTime 是毫秒——单位/时间戳错一档，UI 就差四个数量级或 1970 年。
- 回补窗口类参数（months）必须从 API schema → worker payload → service → fetcher 全链贯通并各自设默认值，任何一层残留硬编码窗口（如 `df.tail(45)`）都会让上游参数静默失效；生成演示序列时"末点精确等于基准值"要靠生成器结构保证并用纯单测钉死，不能依赖抖动碰巧为零。

## 二、数据库与性能

- 列表页的批量金融数据展示应使用单次 JOIN 查询一次获取全部股票的行情/基本面字段，而非前端逐只股票 N+1 请求，避免首屏数据空白和 API 洪泛。
- 批量金融数据查询中 `DISTINCT ON (stock_id) ... ORDER BY stock_id, trade_date DESC` 会对**全表**做 Seq Scan + Sort（3.8M rows），与 WHERE 条件解耦导致单次查询 3.5s；应改用 `LATERAL (SELECT ... WHERE stock_id = s.id ORDER BY trade_date DESC LIMIT 1)` 将过滤条件推入子查询，利用 `(stock_id, trade_date)` 复合索引实现 O(1) 每股票查询，延迟从 3.5s 降至 ~20ms（150x+ 提升）。
- 财务表按 stock_id+metric_key 读时序、并按报告版本 join 时，用 `(stock_id, metric_key, report_version_id)` 覆盖索引一次满足过滤+排序+join，避免额外 sort 与 hash join；逐只查询在全市场规模下仍在个位数 ms，无需提前物化。
- pg `ON CONFLICT` 不处理同一 INSERT 语句内的自冲突（Postgres 约束检查逐行进行，同批两行撞同一唯一键直接报错）——无稳定业务键的表（如大宗交易 date+code+buyer+seller+price+volume 去重键）采集时先在 Python 端按约束键去重（保留末次）再 DO NOTHING；多源共存产生派生时先按 registry 源优先级逐 period 去重，否则同批重复键直接 CardinalityViolation 使整个 ingest 失败；映射层截断超长字符串列（如 reason String(160)）优先于让 DB 报错，比 DDL 放宽更可控。
- 手写 `UPDATE ... FROM (VALUES ...)` 派生表 SQL 时，未定型的日期字符串字面量会被 PostgreSQL 推断为 text 列，与实体表 date 列比较直接抛 `operator does not exist: date = text`——VALUES 行内必须显式 `'...'::date` 转型；此类 SQL 类型错误纯函数单测覆盖不到，接线任务必须以实机验证（docker 重建 + curl + psql 计数）闭环。
- UPSERT 覆盖"懒回补型"可空字段（如 adj_factor）时 SET 子句必须 `COALESCE(excluded.x, table.x)` 防 NULL 重灌抹掉历史回补值；回补的幂等判定口径必须与读取端可用性口径一致（按最新交易日行而非"任一行非空"），并为真实外呼加短 TTL 冷却 key 防数据未发布期间高频重拉——三者缺任一都会形成"不可用但永不修复"的跨日死锁。
- 核实表分区状态别照抄 catalog 列名：PostgreSQL 10+ 的 `pg_partitioned_table` 主键列是 `partrelid`（不是 `relid`），更稳的判据是 `pg_class.relkind = 'p'`；列名写错会抛 `column does not exist` 而非返回 0/1，容易把"查询失败"静默读成"未分区"——分区与否必须由实际 catalog 查询闭环，不能采信模型文件里"已外部分区"的注释。
- 自研 SQL seed 解析器（split-by-semicolon）的经典死法：「注释头 + 巨型 INSERT」脚本按分号切块后首块以 `--` 开头，"跳过注释块"逻辑会连 INSERT 一起吞掉——导入恒 0 行、不报错，只有核对目标表行数或读日志才能发现。防御 = 先剥离注释行再切分（纯函数化）+ 对畸形样例写解析单测；seed 导入类代码必须"导入后核对行数"而非只看退出码。另注意排障时先核对诊断前提：`grep -c` 零匹配（退出码 1）容易被误读成"已修复"。
- 公开可达端点的昂贵排序/聚合路径必须有服务端缓存（客户端 staleTime 不算防护），且**缓存 key 必须包含所有改变结果集的过滤维度**（exchange/category/keyword/排序/分页）——漏掉一个维度会让一次查询的行静默服务给另一组筛选（缓存污染），这类缺陷不报错、只在特定筛选组合下返回错数据；同时要核实端点是否真的把 cache 依赖传进了 service（`cache=None` 断言会让缓存永不生效，等于没做）。
- 按业务键（如 stock_id + trade_date）批量 UPDATE 的三种写法要选对：ORM `update(Model)` 带额外 WHERE 会走 ORM bulk-update 分支并要求参数含主键，executemany 在 asyncpg 下 `rowcount` 返回 -1。正确写法是 `sa.Values(...).data(rows)` + Core table 的 `UPDATE ... FROM (VALUES ...)` 单语句——rowcount 准确、无 N 次往返，也避开 ORM 身份映射同步限制。
- 涨跌幅/前收这类含公司行为的派生字段必须用数据源原生值（按 trade_date 重拉权威源），不得在库内用 `LAG(close)` 现算：除权日的参考前收是除权后价，窗口函数恰在除权日算错且无声。补历史数据要"重拉权威源 + 逐行对拍"，并把该理由写进函数 docstring 防后人"优化"。
- 给行情表新增字段时，落地点至少四处（模型、每个 ORM 构造点、repo 的 INSERT values、`on_conflict_do_update` 的 `set_`），漏任一处都不报错——INSERT 侧静默丢列、冲突侧静默留 NULL。用"mock 捕获落库对象 + 断言编译后 SQL 含 `excluded.<col>`"的单测锁死，比实机抽查行数更早暴露。
- SQLAlchemy `text()` 的绑定参数只能承载**值**，列名与排序方向（`ORDER BY :col :dir`）无法参数化——排序维度必须从文件内硬编码白名单 f-string 插值，并保证任何用户输入都在到达字符串前被白名单校验拦下（校验即天然防注入）；同时 Postgres `ORDER BY x DESC` 默认 NULLS FIRST，可空排序列不显式加 `IS NOT NULL` 会让"涨幅榜"以 NULL 行领跑，榜单类查询必须在 SQL 内过滤坏行，而不是留给前端补。
- 字段量纲注释必须与**存储单位**逐字一致：TuShare `amount` 存的是千元、`daily_basic` 市值是万元，注释写错单位比不写更危险——下游 mapper 会照错注释再乘错一档（10³ 级偏差），而透传单测只断言"原值透传"抓不到；不确定时标注来源口径（如"TuShare 原生千元，消费端 ×1000"）而非猜一个。
- 行业聚合的**聚合对象必须是"当日真有行情的标的"而非静态成员表**：`sw_industry_members` 上卷 L3→L2→L1 后必须 INNER JOIN 当日 `daily_quotes` 并 `pct_chg IS NOT NULL` 再 `count`/`avg`，否则停牌/无行情成员会稀释 `avg_pct_chg` 并虚增 `member_count`（`up_count`+`down_count` 还只覆盖有涨跌的，平盘成员计入 `member_count` 属正确）；join 键先实测覆盖率再定（本次 `members.symbol → stocks.symbol` = 95.4%，高于计划的 >90% 阈值），别照抄 brief 里未验证的键名；若 `count(DISTINCT symbol) == count(*)` 则无扇出，可放心聚合。

## 三、Docker 与部署

- Docker build 缓存不可信：`COPY . .` 步骤即便显示 `DONE`（非 CACHED），实际可能未检测到文件变更（OrbStack on macOS 已知问题）。每次 rebuild 后必须 `docker exec` 验证容器内文件内容，不可仅依赖构建输出。
- Docker 多阶段构建应将依赖安装与源码复制分层，利用层缓存加速重建；前端 Dockerfile 应保留完整构建路径的同时支持 `target: runtime` 跳过构建阶段以适配网络受限环境。
- 前端多阶段镜像的运行时阶段必须通过 `COPY --from=<builder>` 获取构建产物，避免误从构建上下文复制 `dist/` 导致镜像构建失败。
- Docker Compose 编排应使用 `service_completed_successfully` 条件让迁移容器在应用服务启动前完成数据库 schema 初始化，避免应用启动时表不存在。
- Redis 持久化卷在容器编排中应先通过一次性 init 步骤统一修正目录属主，再启动业务容器，避免 `MISCONF` 导致写缓存失败并放大为上层 500。
- Python 镜像构建应使用 `uv.lock` 的 frozen 导出流程并配置国内默认源（如 TUNA）与更长 HTTP 超时，同时保留 BuildKit 缓存挂载，避免解析/下载抖动导致构建超时。
- Docker 构建涉及种子文件时，需显式检查 `.dockerignore` 排除规则并为目标文件添加白名单（如 `!data/sw_seed.sql`），否则运行时会出现"容器内文件缺失"的隐蔽故障。
- Docker Compose 场景下 Nginx 反向代理上游应启用 Docker DNS 动态解析（`resolver 127.0.0.11` + 变量 `proxy_pass`），避免后端容器重建后因缓存旧 IP 导致持续 502。
- 对"体量大但更新频率低"的静态映射数据，推荐"首次解析源文件并自动导出 SQL 种子，后续部署优先导入 SQL"的策略，导入策略显式分层为"SQL 种子优先、源文件解析兜底"，并在启动日志打印实际命中路径，便于排查"文件存在但未生效"的环境问题。

## 四、前端（React / antd / ECharts）

- 多级联动多选控件应以上层选项动态约束下层候选集，并在上层变更时剔除失效下层值，保证提交数据始终满足父子层级关系；联动筛选状态应直接由当前上层已选值派生（而非间接缓存变量），确保禁用态、placeholder 与候选集在同一次渲染中保持一致。
- 当分类数据同时存在"官方成员映射"和"用户自定义标签"时，应在树统计、详情列表和兜底分类中统一按并集去重计算，防止展示与筛选结果不一致；行业树统计应与可展示股票集合保持同一口径（以 `stocks` 为准），为无法映射到有效层级的标的提供"其他"兜底分类，避免前端总量与分组总和不一致。
- 对会影响聚合视图的数据编辑操作，应成对执行"前端 query invalidation + 后端 Redis 聚合键清理"，避免页面在 TTL 期间显示过期计数。
- 公共类型/映射函数应集中在语义匹配的模块中（如 `BackendStockEnriched` 从 `swIndustry.ts` 移到 `stocks.ts`），避免使用者因模块名误导而重复实现；单股详情页 FundamentalCards 估值指标依赖 enriched 接口，新增详情接口时应复用同一 SQL 查询并统一缓存 key 前缀（如 `stock:enriched:`）以保证数据一致性。
- 封装组件的 prop 语义即契约（如 EChart 的 silent 必须真正关 animation+tooltip，不能用换 renderer 这类无副作用的近似实现），文案/标签类展示值应由后端 payload 下发而非前端维护重复映射表（只留纯展示常量如颜色）——否则后端语义演进时前端会静默回退或误标；前后端透传的 JSONB dict 键名要保持 snake_case 一致（前端读 camelCase 会静默不渲染，类型断言不报错）。
- 行业分级页面建议采用"层级选择状态 + 统一个股表格"的单一数据视图模式，避免一级/二级/三级分别维护重复表格逻辑导致状态不一致；涉及层级导航的页面用显式独立路由承载每一级职责，让"点击下钻"和"同页筛选"交互分离，同一层级列表内的点击交互统一为路由下钻并保持视觉反馈一致。
- 板块中心类页面推荐采用"主界面分组预览 + 详情页完整表格"的双层结构，既保证信息密度也降低首次加载认知成本。
- 前后端联调阶段应优先让页面消费真实后端字段并通过前端适配层兜底缺失指标，避免长期依赖 mock 造成接口契约漂移；迁移前端到真实后端接口时，先建立统一的 API 适配层集中做字段映射，再逐页面替换调用以减少回归风险；分层行业页面去 mock 时应先后端化层级树和分层股票查询，再让前端页面复用同一套树数据以保证路由与数据口径一致。
- Ant Design Table 的分页大小切换应由组件状态显式持久化（避免把 `pageSize` 写成固定常量），否则在排序/筛选触发重渲染后退回初始值；当页面展示服务端分页切片时，分页总数与最大页必须用后端 `total` 而非当前页 `data.length`，并由父组件统一驱动 `current/pageSize` 避免 UI 与请求状态漂移。
- ROE、营收同比、净利润同比等财务成长指标需 TuShare `fina_indicator`/`profit_data` 接口支持、后端入库后方可展示；在此之前 FundamentalCards 应提供降级展示策略或临时隐藏相关指标。
- echarts-for-react 默认 merge 模式下，用户交互过的组件状态（如 dataZoom 滚轮缩放窗口）不会被新 option 同名配置重置：数据全集切换的图表必须 `notMerge`（对齐 shared/ui/EChart 封装），且不要用固定 start/end 百分比裁剪初始视图——周期切换类交互的正确语义是"所选区间全量展示 + 每次切换重置缩放"。
- antd 栅格内卡片等高要"双保险"：内容侧 Typography `ellipsis`（描述 `tooltip:true`）消除换行撑高，布局侧 Col `display:flex` + Card `height:100%` 拉伸兜底；flex 行内文本省略号必须给文本容器 `minWidth:0`（flex item 默认 min-width:auto 不收缩），Tag/图标侧补 `flexShrink:0`。
- 重复图表组件的合并应先落纯函数层（计算/格式化/裁剪）并配 barrel 导出，且把类型签名当依赖契约先于组件实现冻结（任务 brief 的 Interfaces 块即签名源）——后续 UI 任务只依赖稳定签名，不再各自重复实现；brief 代码块可用 diff 逐字校验落地无漂移。
- 微服务架构中的身份认证应采用“Gateway 同源 BFF + HttpOnly Session Cookie + 短时非对称签名断言（Principal Assertion JWT）+ 下游本地 JWKS 验签”的零信任模式，彻底消除 XSS 窃取与未签名 Header 伪造越权风险。
- 重复图表组件的合并应先落纯函数层（计算/格式化/裁剪）并配 barrel 导出，且把类型签名当依赖契约先于组件实现冻结（任务 brief 的 Interfaces 块即签名源）——后续 UI 任务只依赖稳定签名；brief 代码块可用 diff 逐字校验落地无漂移。
- ECharts option builder 保持返回未注解的结构化对象（推断类型天然可赋给 `Record<string, unknown>`），formatter 一律收 `params: unknown` 再局部断言；当 `string` 参数要索引 `Partial<Record<字面量联合,…>>` 时直接把参数收窄为字面量联合类型（如 `MaKey`），比在索引处加 `as` 断言更不易漂移。
- antd 5.x 命名导出随小版本漂移：子组件（如 CheckableTag）可能只挂在主组件命名空间（`Tag.CheckableTag`）而非顶层导出，逐字移植参考代码时先对齐代码库内同组件既有用法再定 import 形态；此类 TS2305 还会连带制造"参数隐式 any"的次生报错，修掉根因即一并消除。
- 后端能力未就绪（如复权因子懒加载中）的前端控件应降级为"禁用+Tooltip 提示"而非条件隐藏：DOM 结构保持稳定让 E2E 能以长超时轮询等就绪，且禁用只锁视觉层——受控 state 与 queryKey 不变、value 固定为当前真实展示值，能力恢复后无缝启用且不发额外请求。
- 多源单位在映射层一次性归一（元），消费端只做展示分档：后端字段单位（TuShare total_mv 万元/amount 千元）与前端 formatCap 分档基准（元）错位时，在唯一 mapper 处换算并注释口径，表格列新增时核对 unit props——多消费点各自补偿是单位错误的标准成因。
- 数值型 tooltip 用"灰标签左 + 右对齐 tabular-nums 数值右"的两列式行布局（flex space-between + min-width），OHLC/涨跌随当日涨跌统一着色、量额中性；横排挤合无对齐基准，是主流行情软件与其余 tooltip 的主要视觉分界。
- 市场情绪类可视化的三件套是直方图 + 平衡条 + 参与度（成交额）：平衡条把千位数量级压成长度比例供前注意感知，连续梯度色阶（0%→灰、极端→深色）优于离散档位——但必须为近零浅色块切换深色文字保对比度。
- antd CheckableTag 选中态自带主题色实底，inline 彩色文字色会与之撞色（对比度 ~1.2:1）——彩色图例类控件用图内绝对定位文本行（线色文字 on 白底），不要用 CheckableTag 承载。
- 前端接真实数据源时 ECharts tooltip/label formatter 里的可空数值必须先判空再 `.toFixed`（板块热力图 `d.changePercent.toFixed(2)` 无守卫页面加载即抛 TypeError，属存量隐患）；替换"近似实现"组件前先 grep 全量引用确认只剩 barrel+单页两处，且轮询语义要区分 `refetchInterval`（盘中定时刷新）与 `staleTime`（仅去抖），漏配会把"60s 自动更新"做成假象。
- 计划 brief 给定的表格 rowKey 组合键先对活端点跑唯一性校验再落码：Tushare 明细类数据（解禁一股多持有人、大宗同日同股同价同买方多笔）在默认键上必撞 React duplicate key，复合键以「业务键 + 区分度最高且前端已展示的字段」补位（如 +holderName/+volume）而非引入未展示字段。
- 把为全市场设计的端点复用到个股维度时，客户端 filter 的覆盖边界要在 UI 上写明而非只靠空态：龙虎榜接口无 symbol 参数，个股卡拉 limit=50 最新日再前端过滤，本股不在当日榜即显示"暂无上榜记录"，footer 同步注明"全市场最新日筛选本股"，避免用户把覆盖范围导致的空态误读为数据缺失；另外计划 brief 末尾自带的防未用报错脚手架（hidden span + 死 import）按其收尾指令删除即可，落库前对"这段代码存在的理由"过一遍能直接清掉这类残留。
- 接三方行情先 curl 实测定字段与单位再写映射：东财 f62 是元、TuShare block_trade 是万元/万股、north_money 是万元、巨潮 announcementTime 是毫秒——单位/时间戳错一档，UI 就差四个数量级或 1970 年。
- 定时任务的交易时段/工作日守卫必须显式 ZoneInfo("Asia/Shanghai")：容器默认 UTC，naive datetime.now() 会让盘中任务在真实交易时段静默跳过、却在晚间时段放行——cron 触发正确而 job 体空转，日志只有 executed successfully 没有业务结果行。
108	- 复用网站数据先比对页面 HTML 里的实体代码（东财 BK 板块码）：代码一致即同源，排行页的扩展列（最大股/中单小单）多数在同端点 fields 里就有，无需另找接口。
109	- 东财 kline 类接口（fflow/daykline 等）返回 CSV 字符串行，数值必须显式 float()；容器内长连接池偶发被服务端断连（RemoteProtocolError），HTTP GET 加一次传输层重试即可消除偶发失败。
110	- 前端认证与统一请求层改造中，BFF HttpOnly 会话请求必须全局强制 `credentials: "include"`，非幂等操作需配合 single-flight CSRF Token 注入机制；同时在全局请求客户端中拦截 401 派发事件触发 QueryClient 缓存清理与状态重置，并通过 `skipAuth` 选项切断登录、注册及探针接口的 401 死循环。

- 市场情绪类可视化的三件套是直方图+平衡条+参与度（成交额）：平衡条把千位数量级压成长度比例供前注意感知，连续梯度色阶（0%→灰、极端→深色）优于离散档位——但必须为近零浅色块切换深色文字保对比度。
- 计划 brief 给定的表格 rowKey 组合键先对活端点跑唯一性校验再落码：Tushare 明细类数据（解禁一股多持有人、大宗同日同股同价同买方多笔）在默认键上必撞 React duplicate key，复合键以"业务键 + 区分度最高且前端已展示的字段"补位（如 +holderName/+volume）而非引入未展示字段。
- 把为全市场设计的端点复用到个股维度时，客户端 filter 的覆盖边界要在 UI 上写明而非只靠空态：龙虎榜接口无 symbol 参数，个股卡拉 limit=50 最新日再前端过滤，本股不在当日榜即显示"暂无上榜记录"，footer 同步注明"全市场最新日筛选本股"，避免用户把覆盖范围导致的空态误读为数据缺失。
- 公开宣传页（Landing）不得被后端状态拖垮：所有公开 API 消费都要「加载骨架 + 失败静默降级占位文案」双兜底，且登录态 CTA 在 isAuthReady 之前隐藏文字（保留按钮位），防止错标签闪现与布局跳动；登录态只读复用 auth store selector，不改 auth 模块。
- 页面级 Tab 化重组时，既有 ECharts 卡从裸 ReactECharts 迁到共享 EChart 封装要连「onEvents 透传」一起补齐（treemap 点击导航依赖它），且 option 构建纯函数必须把 colors 收为参数而非闭包静态色——否则明暗切换后图表仍是旧主题色；渐变/色阶端点从主题色推导（bgPanel→up/down）后，近零浅块的深色文字阈值类（nameDark）改挂 textPrimary/textSecondary 即可两种模式自然成立，无需按模式分支。
- 数据缺失的表达必须全组件统一：同一张卡片里价格缺失显示 `--`、涨跌幅却因 `?? 0` 兜底显示 `0.00%`，会被读成"平盘"——缺失与零值语义不同，任何指标渲染都要先判 null 再决定占位符；此类缺陷用 E2E mock 一条 null 数据即可稳定复现（先红后绿）。
- 分桶统计的两侧集合必须对称且穷尽：上涨桶漏掉 `0~1%`、下跌桶含 `0~-1%` 会让「上涨家数」静默少一桶（实测 954 vs 1903），且不会报错——用 E2E 断言「逐桶家数求和 == 上涨 + 下跌 == 全部分桶总和」把漏桶钉死；公开首页多区块重组时每个区块各自包 `SectionCard` 并各自降级（骨架/占位文案），用「一个端点 abort + 邻区正常」的用例锁定单点失败不牵连邻区。
- 按可空字段排序的榜单接口，服务端排序键若把 null 排在前面（`(is None, value)` 再 `reverse=True` 时 None 反成首位），首屏会被无数据行霸占——前端消费榜单类接口应按当前排序维度过滤不可排序行（多取一批再截断 top-N），且把 `--` 缺失值契约的 E2E 挪到不受该过滤影响的维度上断言，避免「修了置顶」却「删了契约覆盖」；空数组必须走不可用占位而非渲染成 0/0。
- UI 上的**口径标注必须与实际数据源同源联动**：数据源切换/回退时标注要一起切（申万一级请求失败回退证监会数据，就把标签同步改回「行业口径：证监会」），绝不静默错标；`retry: 0` 让回退即时而非指数退避期间口径悬空。反面是**客户端 workaround 要写清存在条件、并在服务端修好后退役**：服务端 SQL 已 `pct_chg IS NOT NULL` 后，前端重复的 null 过滤层应删除（只保留真缺失渲染 `--` 的展示契约），临时 API 封装须 grep 零引用再删——但删除会改变 E2E 的请求锚点（首页不再调 `/exchanges/*`），同步把「防假绿」断言换成新端点，别让旧断言在删除后仍靠巧合通过。
- 共享行组件的「缺失值占位」要显式开关而非默认填充：`DataRow` 有纯涨跌幅行（不传 `value`，省略数值槽才是对的），若把「`value===undefined` 一律渲染 `--`」写死进组件，这些行会凭空多出一个 `--`；正确做法是加可选 `valuePlaceholder` 让数值消费方显式 opt-in，同时**数值槽与单位一起判存在**（`hasValue && unit`），避免 `--` 后跟悬空的「亿元」。

## 五、测试与 E2E

- Playwright 浏览器测试在中文重文案页面上，`getByText`/正则会同时命中嵌套容器或相邻重复文案而触发 strict mode violation；断言应优先落在唯一容器上用 `toContainText`，或用 `.locator(".ant-tag").filter({ hasText })` 一类结构选择器限定作用域。
- SPA 内页断言同文案 Tag 时先等"目标页独有元素"挂载再取全局 locator：列表与详情页出现同名 Tag（如行业卡片与工作台头部的"周期阶段"）后，路由 URL 变更与 React 卸载旧页之间存在空窗，Playwright strict mode 多元素错误即时抛出不重试，仅 waitForURL 不足以防护。
- antd 5 Segmented 的 radio input（`.ant-segmented-item-input`）是零尺寸隐藏元素：Playwright 对 `getByRole("radio")` 做 toBeVisible/click 必失败，E2E 应操作可见的 `label.ant-segmented-item`（label 点击天然转发到 input），选中态改断言其 `ant-segmented-item-selected` 类。
- JSX 表达式间字面空格（`{d.key} {expr}`）在末表达式为空串时会留下尾部空格文本（如 "MA60 "），而 Playwright `getByText` 正则匹配不做首尾 trim——行尾锚定（`/^MA60$/`）必失败；此类断言应放宽为 `\s?` 或在组件侧条件拼接避免悬空空格，卡死时先抓 error-context 快照看实际 DOM 文本再改正则。
- 测试夹具按日历日生成序列时用"基准日 + timedelta(days=i)"而非手写 `date(y, m, d+1)`——月份天数溢出抛 ValueError 后若被测代码按设计静默兜底（per-item try/except），失败断言会指向兜底路径（spark 为空）而非夹具根因，排查方向被带偏。
- Worker 单测要脱离真库时，把 session 工厂暴露为模块级变量供 monkeypatch 成假 async context manager，且 NullSession 必须带 `async def commit()`——service 被 patch 后虽不触库，成功路径的 commit 照常执行，漏了会在断言前炸 AttributeError。
- 性能基准与单测必须 marker 隔离（`bench`）且**基线契约显式化**：合成输入的尺寸/seed 写成测试常量并注释"改动即失基线"，门禁按 median 相对退化而非绝对 ms；微基准（<1ms）rounds 多 median 稳，**大样本基准（>10ms/次）单次抖动可达 7-8%**——控制样本量让各基准处于同一量级（~1-5ms）比调阈值更治本；管道里验证 exit code 要看 `PIPESTATUS`，`cmd | tail` 后 `$?` 是 tail 的。
- **wall-clock 性能基线绑定硬件，入库基线不能跨机器门禁**：本机生成的 baseline.json 在 CI runner 上全部基准慢 30-50%，相对阈值门禁必假红。CI 硬门禁的标准做法是**同 runner A/B**（同一 job 内先 checkout base commit 跑一遍存临时基线、再 checkout head 对比），入库 baseline.json 只作本机开发参考。配套两个坑：Windows 侧创建的脚本无执行位（git mode 644），Linux CI 直接执行报 exit 126，须经解释器调用；A/B 产物写 $RUNNER_TEMP 而非 tracked 的基线文件，否则 PR 改基线时 `git checkout` 拒切。
- 测试模块里的跨目录资源查找（fixture 路径、数据文件）**不能在 import 期求值**：pytest 为读模块的 `pytestmark` 会先 import，再应用 `-m` 反选；若模块级上调 `Path(...)` 且目标不存在就抛异常，纯后端/CI 环境里默认 `uv run pytest` 会变成 collection ERROR 而非干净反选。把解析放进 test 或惰性 helper，缺失时 `pytest.skip("...not present")`——同类跨仓依赖（前端 fixture 等）必须允许"后端独立可收集"。抽共享 helper 时也要逐档对照被替换的旧实现，别在"等价重构"里静默丢掉边界分支（`≥1e12 → 万亿` 档）。
- 免登录公开页的「零 401」与「单源降级」要用真断言锁定，不能只做冒烟：收集全链路响应时须显式豁免登录态探测端点（匿名 `/auth/session` 返回 401 是"未登录"语义而非越权，与数据接口 401 性质不同），同时断言**确实发出了行情请求**以防"空集合平凡通过"的假绿；降级侧每块独立 query + 独立空/错态，abort 单个源后除故障列自身占位外，同卡另一列与所有邻区都必须仍可见。
- 前端 e2e 手写 mock 载荷只能锁住前端自己的假设：后端字段改名时后端单测/`tsc`/mock e2e 会全绿，而真机上页面渲染 `undefined`/`--`。解法是**mock 与后端契约同源**——把容器内实抓的真实响应落成 committed fixture，前端 mock 读它、后端再加一条读同一批 fixture 的 `@pytest.mark.e2e` 契约测试断言实际发出的 key 集（顶层 + 条目）与之完全一致，改名即转红；注意这仍**不证明活链路**（dev 代理指向旧镜像时 mock 是必需的），活链路验证要等分支部署，须如实披露。配套：同一模块级 Redis 池在 function-scoped event loop 下跨 test 复用会报 "attached to a different loop"，autouse dispose fixture 除 `engine.dispose()` 外还要 `await close_redis_pool()`。
- 多个 `@pytest.mark.e2e` 用例共用模块级 SQLAlchemy async engine 时，pytest-asyncio 的 function-scoped 事件循环会让上一用例遗留的池化连接在新循环里被复用，抛 `RuntimeError: Event loop is closed`（表现为随机某个用例失败，非断言失败）；在 autouse fixture 里 `await engine.dispose()` 按用例收尾即可，不必改全局 loop scope。

## 六、架构与分层

- 分类/标签等用户可编辑的多对多关系应独立建表并采用"先删后插"的全量替换策略，避免增量 diff 逻辑复杂化；合成分类节点（如"其他"）应复用现有字段自动分组，减少用户手动维护成本；自定义标签系统应与现有分类体系独立设计（独立建表 + 独立前端组件 + 专用聚合页），避免与分类逻辑耦合。
- 当已有页面解决过同类问题时，优先复用其基础设施（schema、service、SQL、前端映射函数）而非重新实现，只需新增一个端点。
- 读路径（GET）不得隐式写库：信号/派生类结果应在 ingest 时评估落表、查询时只读存储行，最多保留"空库引导补算一次"的兜底；写通道入口（人工/CSV batch）必须按白名单校验 source，防止伪造采集适配器专属来源污染源优先级裁决。
- MQ 任务派发必须"先提交任务行、后发消息"：消费者可能在生产者事务提交前收到消息，查询不到行会静默跳过状态更新，任务永久 pending。
- Worker 的 `process` 不要用 blanket try/except 把异常吞成返回 dict：`BaseWorker.handle_message` 只把"抛异常"翻译成 failed 任务行，正常返回一律标 completed/100——数据源故障是常态，失败语义必须靠异常传播；只有未知 job type 这类"没开工"的错误才适合直接返回 failed dict（触库前拦截）。
- 内容型功能（知识库/图谱/原则）应落"迁移内 seed + JSONB 内容表 + 读路径装配"而非硬编码前端：内容单点维护在 seed 模块供迁移与单测共用，前端组件零行业知识，Playwright 断言锚定 `.ant-card-head-title` 一类标题容器以规避徽章同文案混淆。
- 多分层微服务认证必须把 Gateway 当作性能/路由层而不是唯一安全边界：业务服务仍需独立校验 JWT 的签名、iss、aud、exp/nbf 并执行对象/属性/功能级授权；服务间另用 workload identity（优先 mTLS）鉴别调用方，禁止仅凭内网、Docker network 或可伪造的 `X-User-*` 请求头建立信任。用户 access token 只在其目标资源服务链路内转发，跨服务使用 audience 限定、scope 下缩的 token exchange；异步 MQ 消息不携带 bearer token，改由服务身份认证并在任务记录中保存最小化 actor/audit 元数据。
- 小型 Docker Compose 系统选 API Gateway 时，应先按当前的服务发现、认证与限流需求收敛运维面：优先选择能直接读取容器元数据且无需额外控制面依赖的方案，同时让 FastAPI 保留 JWT 签名、iss/aud/exp/nbf 与对象级授权校验，避免把网关误当成唯一安全边界。
- 同一份数据出现在产品多个页面时必须**单一同源**（同一 API/同一 service）：landing 曾走旧的 `/market/indices`（读 index_dailies 盘后日线）而市场页走 `/market/global-indices`（东财实时快照），"每 60 秒自动刷新"轮询的却是盘后库表，EOD vs realtime 口径差被用户当作数据错误上报。新增展示面时先审现有链路能否复用，口径差异要在 UI 上如实标注（如"盘后为准"），"实时"文案不得配非实时数据源；同源化时把两端测试断言（E2E mock 端点/载荷形状、单测注册表条数）一起同步，注册表扩容类断言优先锁"集合"而非只锁"个数"。
- 跨端点共享的取值（如"数据截至日"）应收敛为**单一实现**（`max(trade_date)` 只写一处，其余调用方薄委托），但缓存是调用方各自选择的路径而非实现本身：不要为了"统一"给所有调用方强加缓存，也不要让薄委托顺手改掉老调用方的行为。空源降级责任在**使用层**：需要兜底语义的端点捕获实现抛出的异常并返回自洽空载荷（公开首页块绝不能因空库 500），只有确需该值的调用方才让异常穿透。把 `datetime.date` 放进 JSON 序列化的 `CacheClient` 时必须存 `isoformat()` 字符串并在读出侧 `date.fromisoformat` 兜底——直接存 `date` 依赖 `json.dumps(default=str)` 的隐式转换，读回是 str 而类型标注说 date，迟早出隐性类型错。

## 七、指标建模与规则引擎

- 跨行业可复制的产品（投研工作台）应"一套资产服务所有行业"：指标单表（industry_key + nullable stock_id + metric_key + source + period）+ 代码级指标注册表（metric registry）+ 派生指标统一落表 + 源适配器隔离；接入新行业 = 配置 + 采集器，而非新表新页面。会随政策修订的参考锚点（如能繁正常保有量 4100→3900→3750）必须入库带生效日期，禁止硬编码。
- 纯函数规则引擎中所有"转多"判定分支（阶段复苏、左侧布局信号）都应显式要求正向证据在场（如盈亏口径任一非空），避免 None 缺失值在布尔短路中被静默当作"已确认"；并用无 DB 的纯单测把该不变量锁定为回归门。
- 同一指标表内并存多种频率时，"最新值"裁决必须先按 registry 注册频率过滤再比日期，且唯一约束必须把 freq 纳入冲突键/去重维度：否则月末归档行（period=月末）天然晚于日度行，会借未来日期压过当日数据；约束缺 freq 时同批 upsert 直接触发 PG "cannot affect row a second time"（事务硬失败），且月度行会覆写日度行 freq 导致 rollup 非幂等。日度→月度 rollup 行应作为独立 upsert 阶段先于依赖它的派生计算落库，并打 extra 标记区分来源；用"批内 (key, source, freq, period) 无重复 + 月末跨频合法共存"两条纯单测把不变量钉死。
- 财务三类数据必须按"原始事实/标准化事实/派生指标"三层分表，并以"报告版本父表（stock+end_date+report_type+comp_type+source+ann_date+update_flag）"承载多源与修订，禁止塞进 `(stock_id, trade_date)` 的日频宽表；派生指标必须带 `calc_method`（reported/calculated/derived）与 `quality_status`，缺失显示空而非 0，且 TuShare 已提供的权威值（如 or_yoy/netprofit_yoy）不应被自算值覆盖。
- 估值历史分位/通道只基于"有效正样本"（排除负 PE/PB 与缺失）计算，否则亏损期的负估值会被误判为极低估值；任何带时间属性的指标入库都要记录 ann_date/end_date/as_of，避免把修订后最新值回填历史造成前视偏差。

## 八、工程流程与文档（元经验）

- 调试数据空白问题时，优先直调后端 API 确认响应字段，再追代码。空字段可能来自三层中任意一层：后端未查 → schema 未定义 → 前端映射硬编码 undefined。
- Agent 指令文件（AGENTS.md/CLAUDE.md）必须保持单一事实来源：CLAUDE.md 用 symlink 或一行转发指向 AGENTS.md 而非拷贝；同类沉淀文档不可并存近似命名（`best-practice.md` vs `best-practices.md` 曾同时被更新导致经验分裂，本文件即两文件合并产物）；AGENTS.md 中的命令必须实跑验证后再写入（本次发现 ruff/mypy 需 `uv run --extra dev`、frontend eslint 需先 `npm install`）。
- 技术栈/README 这类"镜像型"文档极易与实现漂移（本项目 README 曾长期标注 SQLModel / Tailwind+shadcn，实际早已迁至 SQLAlchemy 2.0 async / Ant Design 5）：应把某一份文档定为唯一权威入口并纳入 PR 变更清单同步更新，其余文档只做链接跳转；同时每季度或大特性合入时对照一遍 compose / AGENTS / README 的端口、服务名、镜像名，避免"7 个服务 vs 实际 9 个、redis 6380"这类静默漂移。
- uv 的 `[project.optional-dependencies] dev`（ruff/mypy/pytest）默认不随 `uv sync` 安装：CI 与本地都必须显式 `uv sync --extra dev`（或 `uv run --extra dev`），否则 `uv run ruff/mypy` 报 "Failed to spawn"——这会让 Lint/TypeCheck 形同虚设并放行历史欠账；同理 pytest 若依赖真实运行 API，须标 `pytest.mark.e2e` 并在 CI 用 `-m "not e2e"` 避免测试 job 必挂。
- 多分支并行各自新增 Alembic 迁移、随后合并时，会产生两个 head 导致 `alembic upgrade head` 报 "Multiple head revisions"——在合并点新增一个 `down_revision=(链Ahead, 链Bhead)` 的空 merge 迁移（alembic merge <revA> <revB>）线性化两条链，否则 DB 迁移/CI Test job 必挂；此类 merge 迁移需 ruff-clean（去掉未用 import）。
- 认证微服务与安全凭据设计应采用"Argon2id 密码哈希 + Redis 滑动会话 / DB 快照持久化 + 短时 RS256 非对称断言签名 + 公钥 JWKS 规范分发"的完整分层，且会话与主业务库严格物理隔离以保障身份系统的独立性与高可用。
- 微服务拓扑演进中，API Gateway（如 Traefik）应作为唯一暴露的外部流量入口，下游业务 API 与前端容器必须收敛宿主机端口映射改为内网通信，并结合静态/动态中间件分层配置（Security Headers、Rate Limit、Compression）与 labels 声明式路由实现安全防护与任务防洪。
- 下游微服务践行零信任安全原则：用户身份与权限必须严格源自经过非对称签名（RS256）并经本地 JWKS 验签的断言载荷，任何未经签名的入站 `X-User-*` 请求头必须强制丢弃以杜绝伪造越权；同时前端跨节点/跨交易所 fallback 必须严格限定在 404 Not Found 状态码，避免将 401/403/500 等关键鉴权与系统错误静默吞没。
- 多用户业务数据归属设计应采用“模型与索引显式绑定 user_id + 路由层注入当前登录 Principal + 缓存键用户命名空间（`user:{user_id}:*`）隔离 + 前端 React Query 动态以 `user?.id` 门控并于登出时全量清理”的四层联动防护，彻底阻断横向越权与多端缓存串扰。
- 在微服务与 API Gateway 架构中，CI/CD 流水线应将微服务专属门禁（Lint/TypeCheck/Test）与统一网关烟雾测试（仅通过 Gateway:80 外部入口验证各路由分发连通性）结合，配合前端 E2E 隔离断言，形成从代码静态分析到黑盒流量路由的完整自动化质量屏障。
- 跨服务 JWT 契约（iss/aud/claims）绝不能靠口头约定：auth-service 与 backend 各自写一条交叉契约测试锁定同一 claim 结构与默认值（篡改即失败）；Traefik forwardAuth 永远以 GET 调用鉴权子请求且 trustForwardHeader=true 会透传客户端可伪造的 X-Forwarded-Method（可绕过 CSRF 方法判定），因此该开关必须为 false 让 Traefik 用真实原始方法覆写，同时用 strip-assertion（customRequestHeaders 置空=删除）在链首剥除客户端伪造的断言头。
- 认证接口安全评审修复应遵循"凭据最小暴露"原则：登录响应体绝不回传 session_id/csrf_token（只经 Set-Cookie 下发）、会话识别只认 HttpOnly Cookie 不留 Header 旁路（如 X-Session-Id）、登录/注册等匿名写接口也要强制 CSRF double-submit（先 GET /auth/csrf 再回显 Header）、内部端点用共享密钥（X-Internal-Token，非空即 hmac 常数时间强制）收口，并以"APP_ENV=production 必须 COOKIE_SECURE=true"类模型级校验 fail-fast 防生产误配。
- 基础设施安全收敛应遵循"默认不可达 + 会话寿命有界 + 日志不可信输入剔除"三原则：中间件凭据绝不使用 guest 类默认值且端口不映射宿主机（按需 docker exec 访问）；滑动续期会话必须叠加绝对过期上限（如 7 天）防止无限续命；审计日志只记录哈希后的会话标识（SHA-256），且仅当显式声明信任反代（trust_forwarded_for）时才解析 X-Forwarded-For，否则客户端可伪造该头污染审计 IP。
- 清理废弃 mock 文件前应先全局检索引用并在删除后执行一次完整构建回归，避免隐式动态依赖遗漏；类似的，实施计划 brief 末尾自带的防未用报错脚手架（hidden span + 死 import）按其收尾指令删除即可，落库前对"这段代码存在的理由"过一遍能直接清掉这类残留。
- 手写 Alembic 迁移的 revision ID 在多分支并行开发时是全局命名空间——先 `git log --all -S "<revision>"` 查重再落盘，活库 alembic_version 落在其他分支的 head 上时用临时隔离库验证迁移链而非硬闯活库。
- 计划 brief 说"创建"某文件前先确认它是否已存在：cninfo_client.py 已有 webapi 行情客户端（CnInfoClient/get_cninfo_client），追加公告检索客户端时新类名 + 新工厂与既有命名并存，沿用 brief 的同名工厂会静默 shadow 旧客户端把行情/指数采集换成公告协议；brief 里的"伪代码调用"以既有代码真实签名为准改写而非照抄（task_service 实际是 `trigger_*(db, req)` 包 `_dispatch_task(db, task_type, queue_key, payload)`，brief 草稿的 `dispatch_task(task_type=,routing_key=,payload=)` 并不存在）。
- 决策类文档落字必须"实测证据 + 删除范围 + 替代定位"三件套：计划里承诺的目标（如 SEO/静态可索引落地路径）可能与已上线配置（网关整站 `X-Robots-Tag: noindex`）直接冲突，此时先跑黑盒探针实测配置并以事实为准，再把决策连同原始 header 输出、被删除的目标清单、以及易被误删的相邻项定位（页脚署名是给用户的合规署名而非 SEO）一并写死——否则后人会按旧计划文本隐式复活已删目标，或把非 SEO 项当 SEO 一起删掉。
- 新产品模块（如行业投研工作台）落地前，先用单文件 HTML + CDN ECharts 做高保真交互原型验证信息架构与布局（结论先行、证据下钻、数据源权威性分级徽章），再迁移为 React 组件，可大幅降低前端返工成本；原型视觉应贴近真实技术栈（antd v5）而非另起炉灶。

---

> 历史说明：本文件系 2026-09-03 由 `docs/references/best-practice.md` 与 `docs/references/best-practices.md` 两文件合并而来（此前近似命名并存导致经验分裂），条目按主题归档；继续沿用"每次任务沉淀一句"的约定向对应分类追加。
