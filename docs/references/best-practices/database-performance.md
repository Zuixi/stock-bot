# Best Practices — 数据库与性能

> 2026-09-23 从 [`../best-practices.md`](../best-practices.md) 拆分（内容原样搬移，未改写）。
> 检索方式（探测器映射表）与**写入门槛**见 [`../best-practices.md`](../best-practices.md)。

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
- 盘后一次性派生落库（如情绪周期）在编排层快照之上再判跳过时，必须把 `is_partial`（当日行情行数不足）放在空候选 `no_limit_up_rows` 之前：编排层 get_snapshot 在候选窗口为空时会先报 no_limit_up_rows，而部分 ingest 才是更本质的跳过原因，只按 degraded_reason 判会把部分行情日错记成 no_limit_up_rows 或直接 `k["zt_count"]` KeyError。
- 手写 Alembic 迁移里的 `DROP INDEX CONCURRENTLY` 必须在 `op.get_context()` 的 `autocommit_block()` 内执行（CONCURRENTLY 不允许在事务块内跑，普通 `op.execute` 会在迁移事务里直接报错），而它真正生效的前提是 `migrations/env.py` 用 `async with engine.connect()` 而非 `engine.begin()`——后者把整个 MigrationContext 包在外层事务里，`autocommit_block()` 一进去就断言失败；改 `env.py` 是影响全链的改动，改完必须用「空库从零 `alembic upgrade head`」验证一遍，不能只跑增量。
- 建表/映射时「只挑当前用得到的字段」是最隐蔽的数据债：上游免费返回、且未来可能用于排序/过滤/聚合的数值字段（本次即 TuShare `daily` 的 `pct_chg`/`pre_close`）不落库，就只能查询时现算——**派生值不是存储列，任何索引都撑不住 `ORDER BY <派生列> DESC LIMIT N`**。凡上游免费给出的字段，建表时就一并入库（成本≈0）；已丢弃的按「补列 → 补映射 → 历史回填」三步偿还，**不要用预计算快照表绕过**（快照与明细会口径漂移，且违背 DRY）。
