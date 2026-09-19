## 2026-09-18 - 概念板块 Phase 1 落地：三张表 + 东财成分采集（504 板 / 71928 成分）+ 每日 18:20 调度
- **交付（T1–T5，分支 `feat/concept-boards`）**：① 三张表 `concept_boards` / `concept_members` / `concept_member_changes`（迁移 `d5b7c9e1a3f2`，成分表以 `symbol` 为业务键、`stock_id` 可空解析列、无 FK、无多余 `board_code` 索引）；② 东财客户端 `fetch_concept_boards` / `fetch_concept_members`（`pz` 服务端上限 100 → 翻页 + 去重 + `_MAX_PAGES` 守卫）；③ `concept_repo`（`diff_members` 纯函数 + `upsert_members` 整板应用 + 本地聚合 `_AGG_SQL` + `member_history_stats`）；④ `concept_service.ingest_concept_members`（逐板失败隔离 + 完整性闸门）；⑤ `concept_members_refresh_job`（mon-fri 18:20 Asia/Shanghai，避开 17:45 对账/18:00 龙虎榜）+ `MarketDataJobType` 新增 `concept_members` 手动触发（复用 `market_data.fetch` 队列，**不新增 worker 容器**）
- **实机首跑（dev DB）**：`boards=504 / members_upserted=71928 / added=71928 / removed=0 / failed_boards=0 / partial_boards=0 / unresolved=110 / dropped_members=0`；二次跑幂等（0/0）。`unresolved=110`（≈95 B 股 + 约 15 只最新上市不在 `stocks`）正是“名录滞后”唯一暴露面
- **两处设计缺陷在评审中被实测撞出来并修正**：
  - **完整性闸门必须作用在清洗后的行上**：原来先用原始行数比 `member_total`、再过滤脏行（缺 `f14`）→ 脏行会让**仍在板上的成分**被写成 `change_type='remove'`，而 `concept_member_changes` 是追加式永久历史（幻影记录不可撤销），且脏行会抬高计数、把“少抓到的页”掩盖过去 → 改为 `_valid_members` 先于闸门，脏行使该板计为 `partial` 并整块跳过
  - **翻页护栏的页数上限必须由真实最大分页数推导**：`_MAX_PAGES=20`（20×100=2000）< `BK0596 融资融券` 的 3870 成分 → 该板永远拿不到成分，且因读路径 inner join `concept_members` 而在**所有概念视图中完全不可见**（只汄1 条 WARNING + `partial_boards=1`）→ 提到 60（6000 覆盖最大板；小盘仍然空页即收敛），复跑后 `partial_boards` 1→0、`BK0596` 入库 3870 行
- **降级语义订正**：`misfire_grace_time=None` 只在“宿主睡眠且进程存活”时补跑；scheduler 用默认 MemoryJobStore，**进程重启会忘掉错过的触发点**（原计划把它当验收项，已改）
- **测试**：默认门禁 302 passed（新增 `diff_members` 纯函数、SQLite 内存的 `_AGG_SQL` 口径守卫 —— 使 `unresolved/priced/up-flat-down/avg_pct/is_active/tiebreak` 不再只靠 `-m e2e`；ingest 隔离与闸门；调度注册与 worker 分发）；真库 `-m e2e` 覆盖计划形状与聚合计数；ruff/mypy 绿
- **待办**：Phase 2 读路径（概念列表/详情/成分/个股反查/次新股）与 Phase 3 UI；活库 `alembic_version` 仍停在另一分支的 `e1f2a3b4c5d6`（本分支三张表已用等价 DDL 手工建入，合并后需按正常迁移链校对）
- 涉及模块：backend/app/{models/concept.py, migrations/versions/d5b7c9e1a3f2_add_concept_tables.py, core/providers/eastmoney_client.py, repositories/concept_repo.py, services/concept_service.py, scheduler/{jobs,runner}.py, schemas/task.py, workers/market_data_worker.py}, backend/tests/×6, plans/2026-09-18-concept-boards-and-new-stocks.md, docs/references/best-practices.md

## 2026-09-18 - 概念板块计划 T0 执行：名录解冻（5579）+ 66 只新股行情/限价/基本面补齐（数据运维，无代码改动）
- **名录解冻**：`stocks` 冻结在 2026-05-08（66 只 5-08 后上市新股无行）→ 逐交易所 `ingest_stock_universe` 重建：**5579 只**（+66）、`max(list_date)=2026-09-17`、`max(asof)=2026-09-18`、近一年上市 **162 只**（与东财概念板 `BK0501` 成分数完全一致）
- **历史行情回填**：66 只完全无行情 + 19 只覆盖不足 → `_ensure_trailing_three_year_daily_quotes` 85 只、**upserted 15272、failed 0**；验证“无任何行情”从 66 → **0**，且首行 = 上市日（如 `601091` C沈鼓 只有 2026-09-17 一行）；09-17 行情行数 5487 → **5553**
- **发现真缺陷（限价域漏新股，已绕过并修复数据）**：`ingest_stock_price_limits` 的补漏判据是“**该日无任何行**”，而 20 个期望日都有旧股票的行 → 任务报 `ok/fetched=0` 却把 66 只新股的限价**永久漏掉**（实测新股限价行 = 0，最新日“有行情无当日限价”恰为 66），直接让次新股的“未开板/涨停”不可判定。用显式 `trade_date` 逐日强制重拉 20 日 → **upserted 111120**，新股限价行 0 → **1140**，最新日缺限价 66 → **0**
- **派生重算**：09-17 的情结行在缺 66 只的条件下算过 → `persist_snapshot(as_of=2026-09-17)` 重算（同时失效 calendar 读缓存）：**涨停 50 / 跌停 2 / 炋板 23 / 最高板 5**；`/market/data-freshness` 四域全 ok（`universe 5579` vs `quotes_symbols_latest 5553`）
- **待办**：把限价域的存在性判据纳入对账体系（行数量级判据，与 quotes/basic 同口径），否则每新增一批股票就重现一次
- 涉及模块：docs/{Changelog,references/best-practices}.md（仅文档）

## 2026-09-18 - 概念板块成分 + 次新股追踪：实施计划入库（未实施，含 UI/UX 与解耦边界）
- **计划**：`plans/2026-09-18-concept-boards-and-new-stocks.md`。决策拍板：数据源**东财为主**（同花顺兜底**明确不做**：实测容器内 `ak.stock_board_concept_name_ths()` → `OSError: Error loading shared library libstdc++.so.6`，`python3.13-alpine` 装不了 `py_mini_racer` 的 V8）；历史口径**只做当日/向前**（不回算历史成分）；次新股口径=**东财概念板块 `BK0501` 成分**，不自建时间窗
- **实测依据（写进计划，勿再猜）**：东财概念板块共 **504** 个（`fs=m:90+t:3+f:!50`，`pz` 服务端硬上限 100、`fid=f12` 翻页稳定）；成分端点 `fs=b:BK0501` → `total=162`、**全部上市于 2025-09-19~2026-09-17**（与 TuShare 口径「近 1 年上市 162 只」完全一致）；首日新股 `stk_limit` 返回哨兵 `up_limit=99999.999/down_limit=0.01` 且 TuShare `pct_chg=373.8%` 与东财 `f3=177.74%` **基准不同**（禁止混源拼曲线）
- **现状定性**：概念不是没有而是「半截」——快照表已有资金流/涨跌家数，但 `get_hot_boards("concept")` 硬编码 `return []`（前端 tab 永远空白）、**完全没有成分股成员表**、且快照只拉 `pz=100` 不分页 → 至少 250 个板块永不落库且每行涨跌幅是「最后一次进 Top100 那一刻」的值
- **设计要点**：3 张新表（`concept_boards`/`concept_members`/`concept_member_changes`，成分表以 `symbol` 为业务键、`stock_id` 可空 → 名录滞后不再静默丢数，以 `unresolved_count` 暴露）；涨跌家数**本地聚合**消除样本偏差、资金流沿用东财快照且分字段不混算；读路径复用 `limit_up_service.get_snapshot()`（板内梯队与梯队卡同源）与 `market_service.get_stocks_enriched_by_symbols`（前端零改映射直接复用 `StockTable`）；采集复用 `market_data.fetch` 队列（不新增 worker 容器），每日 18:20，逐板失败隔离且失败**绝不表现为成分清空**；既有代码只改 4 个 seam
- **UI 触点**（含字段/交互/降级/空态设计）：热门板块概念 tab（后端委托后前端零改动）→ 概念详情页 `/market/concept/:boardCode`（头部口径条 + 4 KPI + 板内梯队 + 成分股表）→ 个股详情「所属概念」标签（失败即零占位）→ 短线情绪 tab「次新股情绪」卡（含「高于首日开盘」替代口径声明，因发行价未采集）→ 数据版图/资金流样本注脚
- **状态**：T0（解冻 `stocks` 名录，运维动作）+ T1–T16 待实施，T17 可选（成分变更历史入口，本期不做但差分表照写）
- 涉及模块：plans/2026-09-18-concept-boards-and-new-stocks.md（新增）, docs/references/best-practices.md

## 2026-09-17 - CI 修复：单测漏包 `_get_tushare()` seam（本机 .env 掩盖 → 本地绿 CI 全红）
- **现象**：push 后 CI `Test (backend)` 红，8 failed / 266 passed，全部是 `ValueError: TuShare token is required`；**上一版 main 的 CI 同样红（7 failed）**，只是没被注意到
- **根因**：`reconcile_market_data` 开头 `client = client or _get_tushare()` 比所有协作函数都早执行，而 `test_reconciliation_service.py` 的 `_seams` 只 patch 了 `expected_trade_dates`/`_row_counts`/`_refetch_*`，没包 client 构造点。本机 `backend/.env` 有真实 `TUSHARE_TOKEN` → 本地永远绿；CI 无该变量 → 必炸（经典「本地凭证掩盖 CI 缺口」）
- **修复**：fixture 补 `monkeypatch.setattr(rc, "_get_tushare", lambda: _CLIENT)`，并在 `_expected` 里断言 `client is _CLIENT`（钉住「注入的 client 必须透传」，避免只堵默认分支）；用 `TUSHARE_TOKEN= uv run pytest` 先复现失败再转绿（8 failed → 11 passed）
- **防御**：`scripts/self_review.sh --full` 的 pytest 改为 `TUSHARE_TOKEN= uv run pytest ...`——本地自检与 CI 同口径，同类“本地有凭证”的假阳性以后在门禁里就会被拦住
- 涉及模块：backend/tests/test_reconciliation_service.py, scripts/self_review.sh, docs/{Changelog,references/best-practices}.md

## 2026-09-17 - 本机数据卷恢复 + 全栈经 gateway 起全 + 垃圾清理（数据运维，无业务代码改动）
- **事故与恢复**：`docker compose up -d api` 触发 compose 连带重建 postgres —— main 里 `73f40d7` 把卷 pin 成 `stock_bot_wt_p7_postgres_data`，而本机真实数据一直在项目前缀卷 `stock-bot_postgres_data`（4.2G），于是新集群 initdb、`data_init` 见空 `stocks` 表→全量重播 3 年行情（烧额度）。旧库完好（`pg_controldata` + 临时实例对账：5513 stocks / 4,347,642 quotes / max(trade_date)=2026-09-16）。修法：新增本机专属 `docker-compose.override.yml`（已 gitignore）把 `postgres_data` pin 回 `stock-bot_postgres_data`，`docker compose down && up -d` 后数据回来
- **全栈经 gateway 起全**：之前只跑 api/worker/scheduler/frontend，本次补齐 `gateway(traefik) + auth-service + forward-auth + auth-db`，11 服务全 healthy；经 `:80` 实测 SPA 200 / 匿名 API 200（forward-auth 无 session 时旁路）/ `/auth/session` 401 / `/.well-known/jwks.json` 200
- **清理**：构建缓存 18.23GB→2.3MB；19 个匿名空库卷（≈880MB，历史 postgres 镜像 VOLUME 残留）+ 本项目 3 个孤立卷（`stock_bot_wt_p7_postgres_data` 事故重播卷 324M、`stock-bot_redis_data`、`stock-bot_rabbitmq_data`）+ `stock-bot-task6-frontend` 旧实验镜像 + 3 个一次性容器全部删除；本地卷 31→9 且 reclaimable 归 0
- **E2E**：经 gateway（`E2E_BASE_URL=http://localhost`）跑全套 63 例 → 60 passed；3 例失败（darkmode/limitUpSentiment/userIsolation）已用干净 main 前端重建做基线对账，**同样失败** → 存量/数据漂移问题，与本改动无关。`kline.spec` 5/5 通过，页面实际渲染 `数据截至 9月16日`
- 涉及模块：.gitignore, docker-compose.override.yml(未入库), backend/*, frontend/*, docs/references/best-practices.md

## 2026-09-17 - 个股页"数据截至"口径修复 + 名录冻结根治（拣选 fix/stock-asof-universe 内容重放，非 rebase）
- **口径修复**：个股头部"数据截至"原本渲染 `stocks.asof`（名录 ingest 时间戳），实测被冻在 `2026-05-08T15:09:16.546850Z` 而行情已到 09-16——差 4 个月的虚假时效标注。enriched 响应新增 `latest_quote_date`（最新行情 `daily_quotes.trade_date`），前端改绑并复用 `@/shared/ui/date` 的 `formatCnDate`（与 SectionCard/RankingMatrix/SectorFlow 同款「9月16日」），e2e 正则同步
- **名录冻结根治**（这才是真损失）：日线采集用 `stocks` 表把 `ts_code → stock_id`，映射不到的行 `skipped += 1; continue` **静默丢弃**。universe ingest 只在「空库首启」或手动 `/tasks/fetch-universe` 跑过，从不自动刷新 → 对账原始 JSONL 与 DB 得 **65 只次新股的行情从未落库**（`001232.SZ`…`603407.SH`，详情页 404），且每日 fetched 5550 / upserted 5485 的差额随上市递增。新增 `universe_refresh_job`（每周六 09:00 Asia/Shanghai，逐交易所失败隔离，与 `UniverseWorker` 共用 `ingest_stock_universe`，交易所清单复用 `models.stock.ExchangeName` 单一事实源而非另立常量）
- **可观测性补刀**（原分支缺）：`skipped` 拆出 `unknown_ts_codes` 并升级为 warning（含 sample），`_build_stock_id_map` 为空时由 warning 升 error 并带丢弃行数——「名录滞后」不再只体现为一个数字；`daily_basic` 同源路径同步
- **对账盲区修复**：`threshold = universe * 0.8` 的分母取自被冻结的 `stocks` 表，1.2% 缺失（65 只）永远过阈值。`/market/data-freshness` 新增 `quotes_symbols_latest`（最新期望日 daily_quotes 实际行数）与 `universe` 并排暴露，名录滞后在巡检端点可见
- 涉及模块：backend/app/{scheduler/{jobs,runner}.py,services/{tushare_ingest,reconciliation_service}.py,schemas/{stock,reconciliation}.py}, frontend/src/{shared/{api/stocks,types/index,ui/date}.*,features/stock-detail/components/StockHeader.tsx}, frontend/e2e/kline.spec.ts, backend/tests

## 2026-09-17 - 对账式自愈数据面落地：misfire 硬化 + reconciliation_service + 新鲜度端点（Phase 1+2 全部实施并实机验收通过）
- **L1 调度硬化**：`create_scheduler` 注入 `job_defaults={misfire_grace_time: None, coalesce: True, max_instances: 1}`（宿主挂起醒来补跑、堆积合并）；盘中五任务（SSE 快照×3/资金流轮询/公告轮询）例外 `grace=300` 防醒来堆积无意义快照。scheduler 注册 **18→21 任务**（+Startup reconcile/+Post-chain 17:45/+Weekend catch-up 10:00）
- **L2/L3 对账收敛器**（新 `reconciliation_service.py`）：trade_cal 期望集（截止昨日 T-1 语义，不可用降级工作日启发式）× 行数阈值（≥0.8×stocks 全市场数）判完整，`missing ∪ partial` 逐日重拉（upsert 幂等解 partial 死锁）；sentiment 仅在 quotes+limits 完备日补；底座补数先 commit 再派生（get_snapshot 独立会话可见性）。16:30/16:45 job 重写为单域对账薄封装，`_fetch_yesterday_*` 与 exists-skip 语义删除；worker `market_data.fetch` 队列新增 `reconcile` 类型
- **L4 缓存失效内聚**：`persist_snapshot` upsert 成功后 `delete_pattern("market:limit-up:calendar:*")`——任何调用方不再依赖"记得清缓存"（skipped 路径不失效）
- **L5 可观测**：`GET /market/data-freshness` 只读巡检（apply=False 复用对账判据），每域报 latest/missing/partial/status
- **L6 已拍板划掉**：后续部署服务器，开发机不做宿主改造
- **实机验收（superpowers 全流程 TDD）**：删 9/15 四域数据 → scheduler 重启 +2min 启动对账**自动补回**（quotes/basic 5546、limits 5560 行）；**首跑还发现了无人知晓的历史盲区**——情绪表 9/03-9/11 七天从未落库，一并补齐，日历趋势线 1→10 点且缓存经失效路径自动更新（无手动 DEL）；freshness 端点四域全 ok。测试 268 passed（新增 24 个：配置/对账/失效/端点/触发），ruff check+format ✓、mypy ✓
- 涉及模块：backend/app/scheduler/{runner,jobs}.py, backend/app/services/{reconciliation_service 新增,limit_up_service}.py, backend/app/schemas/{task,reconciliation 新增}.py, backend/app/api/v1/market_data.py, backend/app/workers/market_data_worker.py, backend/tests/×5 新增, plans/2026-09-17-data-sync-self-healing.md, docs/{Changelog,references/best-practices}.md

## 2026-09-17 - 数据定时同步根治方案设计：对账式自愈数据面（计划入库，未实施）
- **动机**：三次同构事故（9/10-9/14 partial 死锁、9/15-9/16 宿主挂起任务全丢）+ 一次缓存固化，共同模式是**正确性寄托在"调度触发"这个最脆弱环节**——开发机宿主睡眠是常态，方案必须让完整性由对账层保证
- **方案**（plans/2026-09-17-data-sync-self-healing.md，L1-L6 分层）：L1 `job_defaults={misfire_grace_time: None, coalesce: True}` 硬化（盘中四任务例外 300s）；L2 新增 `reconciliation_service.reconcile_market_data` 对账收敛器（trade_cal 期望集 × 行数阈值判据，触发三处冗余：启动+2min / 17:45 cron / worker 手动）；L3 删"只补 T-1+exists-skip"改窗口补漏（结构性解 partial 死锁）；L4 calendar 缓存失效内聚进 `persist_snapshot`；L5 `/market/data-freshness` 只读巡检端点；L6 宿主环境（关 Resource Saver）降级为非依赖项
- **关键设计决策**：完整性以**行数量级**（≥0.8×stocks 活跃数）而非存在性判定；派生表写入与读缓存失效同一服务方法内完成；数据时点维持 T-1 语义（当日拉取另立任务）
- **状态**：设计已定稿待评审，Phase 1（硬化+缓存失效）→ Phase 2（对账器+语义重写）→ Phase 3（扩域+UI 角标）分期实施，尚未动代码
- 涉及模块：plans/2026-09-17-data-sync-self-healing.md（新增）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-17 - 手动补齐 9/15、9/16 行情与情绪数据 + 清除日历缓存固化（数据运维，无代码改动）
- **现象与根因**：情绪页 `as_of` 停在 9/14——9/16 16:21 主栈启动后宿主**再次挂起**（"Up 4 minutes" + scheduler 日志停在启动行后零输出），9/16 的 16:30/16:50/17:15 盘后任务链又一次没跑，叠加 9/15 缺口，两个交易日全靠手动补；Changelog 9/16 条「今晚任务链自动补齐」的预期落空，验证了 grace/coalesce 未落地前宿主挂起必复现
- **手动补数（worker 容器内按依赖链执行）**：`ingest_daily_quotes`+`ingest_daily_basic`（9/15、9/16 各 upsert 5546 行）→ `ingest_stock_price_limits`（按 daily_quotes 缺失日自动补漏，两日共 11120 行）→ `persist_snapshot(as_of=…)` 逐日落库（9/15：涨停 32/跌停 34/最高 5 板；9/16：涨停 91/跌停 4/最高 6 板）
- **新发现缓存固化坑**：`get_calendar` 的 Redis 键 `market:limit-up:calendar:{days}` 把"只有 9/14"的旧结果集固化（非空即缓存），补数后日历端点仍只返回 1 点——`DEL market:limit-up:calendar:*` 后恢复 3 点，趋势线与环比随之可用；沉淀进 best-practices（补数验证必须 DB + API 两端核对）
- **顺手修复**：best-practices.md 三条条目残留历史编辑混入的行号前缀脏数据（`108\t-`/`109\t-`/`110\t-`），已清理
- **遗留**：待办 A（misfire_grace_time/coalesce + 补漏窗口）与 C（关 Docker Desktop Resource Saver）仍未落地，宿主挂起即复现，9/17 盘后任务能否自动跑取决于宿主是否活跃
- 涉及模块：数据运维（补数/清缓存）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-16 - 部署源切换到 main：docker 卷名锚定 + 清理 9/15 partial 脏数据
- **部署切换（用户拍板：只保留 main 分支 docker 栈，验证一律 kill 旧服务后重新部署）**：`stock_bot_wt_p7` 旧栈 down（保留卷）→ 主 worktree `docker compose up -d --build`（main 代码）。新 scheduler 注册 **18 个任务，含旧栈缺失的 `Price limits daily`（16:50）与 `Market sentiment daily`（17:15）**；前端为含短线情绪/申万榜重设计的 dist
- **卷名锚定（防空库）**：运行数据在 `stock_bot_wt_p7_*` 四个卷里，而主 worktree 默认 project=`stock_bot` 会新建空卷——docker-compose.yml volumes 段显式 `name:` 锚定既有四卷（postgres/auth/redis/rabbitmq），数据零丢失；注释写明原因
- **3002 dev server 彻底下线**：TaskStop 两次留下 vite 孤儿均按 `taskkill //F //T` 补杀；3000/3001/3002 全清，此后验证一律走 `localhost:80` docker 栈
- **9/15 partial 脏数据清除**：重启后 `as_of` 卡在 9/15 且 `is_partial=true`——根因是 backend 启动的"3 年日线覆盖"任务从 TuShare 单股接口拉到 9/15 已生成的仅 2 行（数据源侧当日未出全）写入库；而 16:30 回补 job 用"存在即跳过"判据会被这 2 行挡住形成死锁。已 `DELETE` 该 2 行，`as_of` 回到完整态 9/14，今晚 16:30/16:50/17:15 任务链自动补齐 9/15 全量（含限价与情绪快照）
- **已知误伤（无害）**：清理时把 9/15 完整的 5546 行 `daily_basic_indicators` 一并删除（TuShare daily_basic 出数早于 daily，属好数据被误判 partial）——今天 16:45 任务检测缺失会自动重拉全量
- **遗留风险**：APScheduler `misfire_grace_time` 仍为默认 1s（方案 A 未落地），宿主睡眠/Resource Saver 期间任务仍会被丢弃——今天 16:50/17:15 任务能否执行取决于宿主是否活跃；建议尽快落地 grace 配置 + 关闭 Docker Desktop Resource Saver
- 涉及模块：docker-compose.yml（volumes 锚定）, 部署运维（栈切换/脏数据清理）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-16 - 回补失败根因诊断：宿主挂起 + misfire_grace_time=1s 全量丢弃
- **现象**：情绪接口 `as_of` 停在 9/14，9/15 数据从未回补；历史上 9/10、9/11、9/14 均需手动补
- **取证**：scheduler 容器 26h 日志 **0 次 "Running job"**，每个任务槽位只有 `Run time of job ... was missed by ...`（迟到 17s~3m47s，第一个就是 9/15 07:00 晚 3m47s）；RestartCount=0、无 OOM；RabbitMQ 全队列 0 消息且消费者健在（消息从未发布）
- **反证**：容器内 asyncio `sleep(5)` 实测三连 5.00s 零漂移（宿主活跃时定时器正常）→ 排除任务/代码阻塞，锁定**宿主睡眠或 Docker Desktop Resource Saver 把容器进程成段挂起**，醒来必超 APScheduler 默认 `misfire_grace_time=1s` → 到点任务全量静默丢弃
- **放大器**：`_fetch_yesterday_daily_quotes/_daily_basic` 只补 T-1、存在即跳过、无补漏窗口；运行栈是 wt_p7 旧镜像，调度器里根本没有 price_limits/sentiment 任务
- 排障提示：scheduler 容器日志时间戳为 **UTC**（北京 = UTC+8），"15:41 静默"实为北京 23:41 后夜间无任务
- 教训沉淀：docs/references/best-practices.md（misfire_grace_time + 容器挂起 + 补漏窗口 + UTC 日志）
- 涉及模块：诊断（无代码改动）, docs/references/best-practices.md

## 2026-09-15 - 服务管理规则固化 + 申万三级最高板热度榜重设计 + 昨日涨停卡口径澄清
- **服务管理规则（用户要求）**：Agent 起长驻服务前必须清掉旧实例——实测 wt_landing worktree 的 vite 在 3000/3001 各挂一个、上轮 TaskStop 只杀 npm 父进程留下 vite 孤儿继续占 3002；本次 `taskkill //F //T` 全部清掉并固化规则进 AGENTS.md 新增「服务管理约定」（netstat 查占用 → Get-CimInstance 确认身份 → `//T` 杀进程树 → 收尾不留孤儿/保留须报端口与 PID）
- **申万三级最高板重设计**（`SwL3LimitUpBoard` 重写）：block Segmented 塞 21 个一级行业把标签截成单字（不可用），换 CheckableTag wrap + 家数徽标；新增「仅看 ≥2 板」开关；表格换自绘热度榜行——板高 4 格热度色阶（与梯队同语言）+ 涨停家数占比条 + 板高 desc→家数 desc→l3Code 确定性排序；行点击跳龙头个股与 `.sector-limit-up` 根类契约保留
- **昨日涨停卡口径澄清**：卡头「数据截至」从涨停日（as_of_prev，显示 9月11日）改为表现日（as_of，9月14日），卡内首行新增「统计 9月11日 涨停股在 9月14日 的表现 · 行情 T+1 回补」——旧标法被用户误读为"数据落后一个交易日"，实为双日口径表达问题；当日表现等 T+1 回补后自然生成
- **契约同步**：limit-up-sentiment.md「UI 呈现契约」补 SwL3 与 as_of 口径两节；e2e 补「仅看 ≥2 板」与口径行断言
- 验证：`npm run build` ✔、vite dev(3002) 实测：CheckableTag 过滤（电子→3 行）、叠加仅连板（→1 行）、还原 38 行、明暗双主题
- 涉及模块：AGENTS.md（服务管理约定）, frontend/src/features/market/components/{SwL3LimitUpBoard.tsx,SwL3LimitUpBoard.css,YesterdayLimitUp.tsx}, frontend/src/pages/market/index.tsx, frontend/e2e/limitUpSentiment.spec.ts, docs/design/limit-up-sentiment.md, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-15 - 短线情绪 Tab 重设计：hero 风 KPI 温度计 + 连板梯队分档列（修复比值显示 bug）
- **修数据展示 bug**：`broken_rate`/`promo_1to2`/`promo_2to3` 后端是 0-1 比值，旧 UI 直接 `toFixed(2)%` 把 40.62% 显示成 "0.41%"、晋级率 "0.21%"/"0.75%"——重设计后统一 ×100 渲染（40.62% / 21.21% / 75.00%），契约已写入 limit-up-sentiment.md「UI 呈现契约」
- **情绪温度计 hero 化**（新 `SentimentThermometer` 替代 `SentimentHeader` datarow 版式）：涨停/炸板/跌停大数字瓦片 + 多空力量条（家数占比）+ 6 个二级指标瓦片（新露出 `yzt_avg_open_premium` 今开溢价均值）+ 涨停/跌停趋势线（纯 SVG）；环比 chip 与趋势线接通既有但从未消费的 `/market/sentiment/calendar` 端点（30 日），无前值只藏 chip、日历滞后 as_of 时以实时 KPI 补趋势末点、<2 点显示"累积中"占位
- **连板梯队分档列**（`LimitUpLadder` 重写）：每档一卡（板高热度色阶 + 家数占比条 + 家数 chip）替代平铺 Tag 流；档内按封板时间升序、行可点跳个股页；新露出 `seal_fund` 封单额与 `break_count` 炸板次数（仅 >0 时警示显示）；首板默认 12 家折叠 + "展开全部"；汇总 pill（空间板/连板/首板家数）
- **配套**：温度计卡头补 `数据截至` 行；`e2e/limitUpSentiment.spec.ts` 同步新结构（`.thermo` 根类 + 比值 ×100 断言 + calendar 路由 mock），内容契约（`--`/「缺少 N 个交易日」/降级文案）保持不变
- 验证：`npm run build`（tsc+vite）✔、`npm run check:design` 8/8 PASS、vite dev + chrome-devtools 实测明暗双主题/390px 移动端/环比 chip 数值逐项核对
- 涉及模块：frontend/src/features/market/components（SentimentThermometer 新增、LimitUpLadder 重写、SentimentHeader 删除）, frontend/src/pages/market/index.tsx, frontend/e2e/limitUpSentiment.spec.ts, docs/design/limit-up-sentiment.md, docs/references/best-practices.md

## 2026-09-15 - 数据回填：补齐日线缺口 + 权威 pct_chg 全量回填（数据运维，无代码改动）
- **发现**：`daily_quotes` 缺 2026-09-10 / 09-11 / 09-14 三个交易日（09-11 仅 1 行、09-14 仅 3 行），而 `daily_basic_indicators` 同期**有** 5548 行/日——两条回补链路覆盖不一致，缺口因此极难察觉；下游连板梯队只是把 `as_of_prev` 悄悄滑回 09-09，不报错
- **补齐日线**：按交易日显式重拉 `ingest_daily_quotes`（09-10 / 09-11 / 09-14，各 upsert 5548 行，原生 `pct_chg`/`pre_close` 一并落库），日线缺口归零
- **权威 pct_chg 全量回填**：`scripts/backfill_pct_chg.py --years 3` → **725 个交易日 / 3,881,294 行**；3 年窗口 `pct_chg` 覆盖率 **99.867%**（NULL 0.133% ≈ 停牌/新股），最新 3 个交易日 NULL 数为 0，满足计划 Task 2.3 的「< 1%」判据
- **限价补漏**：`ingest_stock_price_limits` 自动识别 20 个缺失交易日并补全 → **111,064 行**（2026-08-18 ~ 2026-09-14）
- **情绪快照**：`persist_snapshot` 落库 2026-09-14（涨停 57 / 跌停 18 / 炸板 39 / 破板率 0.4062 / 昨涨停今均 +3.2131% / max_streak 4 = `000993` 闽东电力）
- **结果**：`/market/{rankings,distribution,limit-up-ladder,yesterday-limit-up,sector-limit-up}` 全部返回真实数据，`degraded_reason` 由 `price_limits_missing` 变为 `null`；`/market/rankings` 的 `as_of` 从 09-09 前进到 **2026-09-14**
- **遗留（建议另立修复）**：`_fetch_yesterday_daily_quotes` 只拉「上一个工作日」且命中即跳过，没有补漏窗口——调度器停摆一天就会留下永久空洞，而限价链路有补漏、日线没有（已沉淀进 best-practices 一）
- 涉及模块：数据运维（无代码改动）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-14 - 行尾归一化入库（.gitattributes）+ 双端检出与工作区属主修复
- **问题**：Windows 侧检出工作区为 CRLF、index 存 LF，WSL 侧未设 `core.autocrlf` → `git status` 一次性报 380 个文件「整体改写」（+76296/-76278），实际改动只有 2 个文件；同时仓库文件属主为 root，`.git` 不可写导致任何 commit/pull 必然失败
- **修复**：新增仓库根 `.gitattributes`（`* text=auto eol=lf`，二进制由 `text=auto` 自动跳过），gitattributes 优先于 `core.autocrlf`，双端不再需要各自的本地配置；工作区重新检出为 LF，`scripts/*.sh` 从此可在 WSL 直接 `bash` 执行（此前报 `set: pipefail: invalid option`）
- **renormalize**：`git add --renormalize .` 归一化了唯一以 CRLF 入库的 blob —— `backend/tests/fixtures/caaa_article.html`（461 行）；该 fixture 由 `Path.read_text()` 读取，行尾对其断言无影响
- **副产品**：4 个 worktree 的 `.git` 由 Windows 绝对路径（`F:/...`）改为相对路径（`gitdir: ../stock_bot/.git/worktrees/<name>`），WSL 侧恢复可用（原先 `git worktree list` 全部标 `prunable`）
- **踩坑**：批量重写工作区后 `git status` 仍报几百个 phantom 修改而 `git diff` 为空——index 缓存的 stat（size/ino）停在旧值，补一次 `git add -A` 重新 stat 才归零
- 验证：`git status --porcelain` 仅剩预期改动；`git diff --stat origin/main` 收敛到 .gitattributes + fixture 归一化 + Changelog/best-practices 四个文件
- 涉及模块：.gitattributes（新增）, backend/tests/fixtures/caaa_article.html, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-14 - CI docker-smoke 根因修复：网关缺健康检查导致 `--wait` 提前返回
- **现象**：`Docker Compose Smoke Test` 全容器 Healthy，但经网关的**所有**路径都返回 Traefik 自己的 404（body 恰 19 字节 `404 page not found`，含 `PathPrefix(/` 的前端首页）；第一个失败还被 `curl -f ... | head -1` 吞掉退出码，由第二个 curl 以 exit 22 中断 job。
- **根因**：`gateway` 服务**没有 healthcheck**（compose 里 7 个服务有、它没有），所以 `docker compose up -d --build --wait` 在 Traefik 容器刚 Running 时就返回，而 Traefik 还需一个节拍去订阅 Docker 事件、把容器 labels 变成 router；这段窗口内路由表为空 → 一切 404。CI 日志实证：诊断步骤（+约 1.4s）后 `/api/rawdata` 已列出全部 `*@docker` router，健康检查 5/5 首次尝试即 200 —— 即窗口真实存在，且只有「立刻断言」才会踩中。
- **修复（治根而非加 sleep）**：给 `gateway` 加健康检查，断言**路由表已加载**（`wget -qO- http://127.0.0.1:8080/api/rawdata | grep -q 'frontend@docker'`），让 `up --wait` 等待真正的就绪条件；同时把 smoke 步骤的 `curl -f` 改成**条件式轮询**（30×2s，打印首次尝试与失败响应体）作为纵深防御，并新增 `Diagnose gateway routing (always)` / `Gateway logs on failure` 两步，把网关版本、容器状态、rawdata 路由表与日志留在现场（网关 dashboard 只在容器内可达，故用 `compose exec`）。
- 验证：`docker compose config -q` ✔；CI `Docker Compose Smoke Test` 由 failure → success（含后续 `Auth login closed-loop smoke`）。
- 涉及模块：docker-compose.yml（gateway healthcheck）, .github/workflows/ci.yml（docker-smoke 三步骤）, docs/build.md（启动流程补网关层就绪条件）, docs/references/best-practices.md

## 2026-09-14 - 连板梯队 PR#9 CI 修复：ruff format 本地门禁与 CI 口径对齐
- **CI `Lint (backend)` 转红**：CI 的后端 lint job 跑的是 `ruff check app/ tests/` **外加** `ruff format --check app/ tests/`（`.github/workflows/ci.yml:24-25`），本特性各任务的本地自检只跑 `ruff check`，于是 16 个改动文件从没被 format 过——本地全绿、CI 必红。修复：`uv run ruff format app/ tests/` 格式化本特性全部改动文件（16 files reformatted，其中 0 个是既有文件，说明全部由本 PR 引入）。
- **补门禁防复发**：`scripts/self_review.sh` 的 ruff 步骤增加 `ruff format --check $PY`（与 CI 同口径、同样只看改动文件），并在脚本头注释与失败提示里写明「跑 `uv run ruff format <files>`」。自检现在能提前抓到这类问题，而不必等 CI。
- 验证：`ruff format --check app/ tests/` → 194 files already formatted；`ruff check app/ tests/` → All checks passed；`uv run pytest -q` 246 passed / 30 deselected；`mypy app` 150 files clean；`bash scripts/self_review.sh` 新 format 步骤 ✔
- 涉及模块：backend/app/**/*.py（本特性 16 个文件格式化）, scripts/self_review.sh, docs/references/best-practices.md

## 2026-09-14 - 连板梯队 终审修复：SQLite 合成夹具把 gaps-and-islands 不变量钉进默认门禁 + 修 spec 自相矛盾（仅测试/文档）
- **streak 不变量默认门禁守卫**（终审 #1/#2）：新增 `backend/tests/test_limit_up_window_sql.py`（无 `-m e2e`、无 Postgres），在 SQLite 内存库建最小 schema（daily_quotes/stock_price_limits/stocks/sw_industry_members/sw_industry_classes），**直接 import `limit_up_repo._WINDOW_SQL`**（不复制 SQL 文本）跑合成夹具：股 1 is_lu 序列 [T, F, T, T, F]（其中 F 日有行情无限价行 → 走 COALESCE），钉死 false 岛后 true 岛必须从 1 重启（D3/D4 streak_upto=1/2）与缺限价行日 `is_lu`/`touched` 为布尔假而非 NULL；股 2 覆盖零跑不抬升涨停 streak；同文件搬入无扇出（(stock_id,trade_date) 唯一）与整窗（>2 个不同 trade_date）断言。变异验证：临时把 `PARTITION BY i.stock_id, i.is_lu, i.grp` 去掉 `is_lu` → D3/D4 变 2/3（红）；临时去掉两个 `COALESCE(..., false)` → `is_lu=None`（红）；还原后全绿且 `git diff` 对 `limit_up_repo.py` 为空。`test_limit_up_repo.py` 只留真库计划形状守卫（`-m e2e`），docstring 注明无扇出/整窗已上移默认门禁
- **spec 自相矛盾**（终审 #3）：`docs/design/limit-up-sentiment.md:118` 把已作废的「34ms / 895 行」改为「16 交易日整窗 2,411 行 / ~48ms，见 §六」，与 §六实测口径一致
- 验证：`uv run pytest -q` 246 passed / 30 deselected（新增 4 例默认门禁测试）；`uv run pytest tests/test_limit_up_repo.py -v -m e2e` 1 passed；`ruff check` 改动文件、`mypy app`、`bash scripts/self_review.sh` 全绿
- 涉及模块：backend/tests/test_limit_up_window_sql.py（新增）, backend/tests/test_limit_up_repo.py, docs/design/limit-up-sentiment.md, docs/references/best-practices.md

## 2026-09-14 - 连板梯队与市场情绪：本地 K 线自算主干 + 限价权威表 + 降级契约
- **权威限价**：新增 `stock_price_limits`（TuShare `stk_limit` 原值，`Numeric(12,4)`，唯一键 `(trade_date, stock_id)`）+ 按交易日补漏 ingest（16:50）。**`fetch_stk_limit` 必须显式传 `fields`**：`pre_close` 是 TuShare「默认显示=N」字段，不传只回 `[trade_date, ts_code, up_limit, down_limit]`（2026-09-14 实测），整列 NULL 且不报错；传 `fields` 后 5,637/5,637 非空。**实测否决了名称启发式**：2026-09-08 权威判 75 只涨停、名称 ST/前缀启发式判 83 只，10 处分歧里 9 只是 ST 名称股真实限幅为 10%（`ST晨鸣` up_limit=2.13 / pre_close=1.94）；前收一律用 `stk_limit.pre_close`（交易所口径、含除权），用 LAG 当日前收会把涨停数算成 142
- **本地自算主干**：`limit_up_repo` 单条窗口 SQL（候选 CTE 先收敛再回查，实测 16 交易日整窗 2,411 行 / ~48ms；限价表驱动的自然 join 顺序 620ms）+ `limit_up_calculator` 纯函数（梯队 / `N天M板` / 交集式晋级率 / 申万 L3 最高板 / 情绪 KPI）。候选集必须 `DISTINCT`——两日并集重复会让涨停数 75→94 并造出假 8 连板。三条已锁死的坑：①候选集必须 `DISTINCT`——两日并集重复会让涨停数 75→94 并造出假 8 连板（新增扇出守卫测试）；②窗口必须回整段交易日（只回 as_of/as_of_prev 会把 `boards_in_window` 锁在 2、`missing_days` 恒为 14）；③`streaked` 必须 `PARTITION BY stock_id, is_lu, grp`（`grp = rn_all - rn_by_val` 只保证组内常量、不保证组间唯一，实测窗口内 4 行 is_lu 的 streak 被虚高）
- **涨跌幅分母**：昨日涨停股的今日涨幅一律取 **as_of 当日行**的 `pre_close`（= 昨收）。取 as_of_prev 行的 `pre_close` 会变成「今日 ÷ 前前日」的 2 日收益：实测 2026-09-08 从真值 2.8225% 变成 13.9488%（95 只），5 倍偏差且不报错
- **申万 L3 细分最高板**：复用既有 L3→L2→L1 `parent_code` 两跳链，龙头按 `streak DESC, amount DESC, symbol ASC` 确定性裁决，未映射标的进 `unclassified_count` 兜底桶 + 回传 `sw_coverage`（实测黄金日 70/75 = 0.9333）
- **降级契约**：`source` **只用** `local_calc`（东财 `hybk` 是东财板块口径、填不进申万 L3，Web 不产出完整 payload）；限价缺失/当日行数 < 4000（正常 5,490）时返回空 payload + `degraded_reason`（`price_limits_missing` / `partial_day` / `no_quotes` / `no_limit_up_rows` / `insufficient_trade_days`），**不猜比例**；读端点不写库，唯一外呼是东财增强（失败静默）
- **Web 增强**：东财涨停池（`push2ex/getTopicZTPool`，`date` 必填、客户端 10s 超时）按 `as_of` 取数注入封板时间/封单资金/炸板次数，失败只 log 不改主路径状态；本地路径这三字段为 `null`，前端渲染 `--`。`push2delay` 对该端点返回空，故硬编码 `push2ex`
- **情绪周期**：`market_sentiment_daily`（纯派生缓存，可重建）盘后 17:15 落库 + `GET /market/sentiment/calendar`
- **验证**：TDD 先红后绿——新增单测 29 例（`test_limit_up_ingest.py` 3 / `test_limit_up_calculator.py` 纯函数 12（含 off-by-one、停牌 policy A、pre_close 分母）/ `test_limit_up_service.py` 降级契约与快照 9 / `test_limit_up_web.py` 映射、失败隔离、降级不外呼 5），另有 `@pytest.mark.e2e` 的 `test_limit_up_repo.py` 扇出/窗口完整性/计划守卫 2 例。黄金日 2026-09-08 全链路口径一致（梯队 4板×3/3板×3/2板×13/首板×56，涨停 75 / 跌停 1 / 炸板 39，昨日涨停池 95 只今日均值 +2.8225% / 今开溢价 +3.11%，`sw_coverage` 0.9333）。`uv run pytest -q` 242 passed / 31 deselected（基线 213/29，净增 29 单测）；`mypy app`、`npx tsc -b`、`npm run check:design`、`bash scripts/self_review.sh` 全绿；本特性 e2e `limitUpSentiment.spec.ts` 3/3 通过。既有问题（非本特性引入，如实记录）：`uv run --extra dev ruff check .` 全量 27~28 个错误全部在 `scripts/`、`crons/SSE/` 等既有文件，本特性改动文件 ruff 全绿；前端 `npm run lint` 不可用（仓库无 eslint 二进制/配置，2026-09-11 已记录）；全量 e2e 本机 42 passed / 16 failed / 5 skipped，成因为环境缺 gateway 容器（`docker ps` 无 traefik，`frontend/vite.config.ts` 的 `/api` 代理指向 `localhost:80`），凡未自带 mock 的用例一律 500，历史基线（网关在跑时）曾为 3 项既有失败
- 涉及模块：backend/app/{models/market_data.py,repositories/{limit_up_repo,stock_repo}.py,services/{limit_up_calculator,limit_up_service,market_data_service,tushare_ingest}.py,core/providers/{eastmoney_client,tushare_client}.py,schemas/{limit_up,task}.py,api/v1/market_data.py,workers/market_data_worker.py,scheduler/{jobs,runner}.py,migrations/versions/{a7c1f0b2d3e4_*,b8d2e1c3f4a5_*}}, backend/tests/test_limit_up_{ingest,repo,calculator,service,web}.py, frontend/src/{shared/api/limitUp.ts,features/market/components/{SentimentHeader,LimitUpLadder,SwL3LimitUpBoard,YesterdayLimitUp}.tsx,pages/market/index.tsx}, frontend/e2e/limitUpSentiment.spec.ts, docs/design/limit-up-sentiment.md

## 2026-09-14 - 连板梯队 Task 4 补钉：钉死严格 no_limit_up_rows 路径与降级快照不写缓存（仅测试）
- 控制器规则第三轮只加测试、不动生产代码。新增两条契约测试：①`test_no_limit_up_rows_when_limits_present_but_window_empty` 钉死 round 1 恢复的严格判据精确触发条件（限价存在 + 窗口空 + zt_count=75 ⇒ `degraded_reason=="no_limit_up_rows"`、`echelons==[]`、`kpis=={}`）；②`test_degraded_snapshots_are_not_cached_but_complete_is` 用最小 `_RecordingCache`（get 恒 miss、记录 set）断言 `price_limits_missing` 与 `no_limit_up_rows` 两条降级路径 `cache.set` 未被调用，并补正例控制（完整日快照必被 set、key 含 as_of+lookback 两维、ttl==SNAPSHOT_TTL）。另修正 `_lu_row` docstring「16 键」→「18 键」并说明口径（18=行键数，16=窗口交易日数）。两条新测试均做变异验证确认能真失败（改回旧判据 / 让降级也 set 均转红）
- 验证：`uv run pytest tests/test_limit_up_service.py -v` 6 passed；`uv run pytest -q` 234 passed / 31 deselected；`ruff check` 改动文件、`mypy app`、`bash scripts/self_review.sh` 全绿
- 涉及模块：backend/tests/test_limit_up_service.py, docs/references/best-practices.md

## 2026-09-14 - 连板梯队 Task 4 评审修复：恢复严格 no_limit_up_rows 判据并修正不真实 fixture
- 评审发现 `limit_up_service.get_snapshot` 曾把空候选窗口判据放宽为 `not rows and zt_count == 0` 以迁就不真实测试 fixture（空 window + 非零 zt_count），会产出「表头有 zt/zb、梯队/板块/昨日全空、无 degraded_reason 且被缓存」的半成品 payload。修复方向按控制器裁定：**恢复严格判据 `if not rows: → no_limit_up_rows`（改测试而非改生产语义）**，新增 `_lu_row` 构造器让两个 fixture 给出与 T2 `fetch_limit_up_window` 同形状（16 键）的非空窗口，并在完整日用例追加 `assert snap["echelons"]` 回归护栏（完整日必须真产出梯队）；断言值不变。真库 2026-09-08 复验仍与黄金日一致（4板×3/3板×3/2板×13/首板×56，zt=75/dt=1/zb=39，sw_coverage 0.9333，空间板 4/4）
- 验证：`uv run pytest -q` 232 passed / 31 deselected；`ruff check` 改动文件、`mypy app`、`bash scripts/self_review.sh` 全绿
- 涉及模块：backend/app/services/limit_up_service.py, backend/tests/test_limit_up_service.py, docs/references/best-practices.md

## 2026-09-14 - 连板梯队与市场情绪：实施计划审计修订（未落代码）
- **审计发现并修入计划**（`plans/2026-09-14-limit-up-sentiment.md` + `docs/design/limit-up-sentiment.md` 同步）：①`stk_limit` 的 `pre_close` 是 TuShare「默认显示=N」列，`fetch_stk_limit` 不传 `fields` 则整列 NULL（实测 5,637 行无 `pre_close`）；②窗口 SQL 末尾多了一句只回 `as_of`/`as_of_prev` 的 `WHERE`，会让 `boards_in_window≤2`、`missing_days` 恒为 14（16 交易日窗口整窗 2,411 行 vs 只回两天 302 行）；③gaps-and-islands 的 `grp = rn_all - rn_by_val` 只保证组内常量、不保证组间唯一，`streak_upto` 的 `PARTITION BY` 必须补 `is_lu`（实测窗口内 4 行 is_lu 被虚高）；④昨日涨停溢价的分母必须取 **as_of 当日行**的 `pre_close`（取前一日行 = 2 日收益，实测 2.8225% → 13.9488%）；⑤`source="web"` 完整 payload 与盘中增强触发条件（`as_of == 今日`）为死代码，已收窄契约：`source` 只用 `local_calc`，Web 仅作增强字段（东财 `hybk` ≠ 申万 L3）；⑥校实并修正计划里的错误指向：`latest_quote_date` 等函数在 `limit_up_repo` 而非 `market_data_repo`、`api/v1/market_data.py` 没有 `_iso()`、`SectionCard` 没有 `asof` 属性、个股页路由是 `/stock/:symbol`、窗口查询实际走 `idx_daily_quotes_stock_date`（非 uq_）、`sw_coverage` 实测 0.9333；⑦补齐自检步骤（`bash scripts/self_review.sh`）与缺失的测试用例（pre_close 字段契约、窗口完整性、pre_close 分母、空候选日不落库）
- 验证：对真库/真接口逐条实跱（TuShare `stk_limit` 2026-09-08 全量 + 东财 `push2ex/getTopicZTPool` 20260807/08/14 + PostgreSQL 16 交易日窗口 EXPLAIN ANALYZE），黄金日梯队 {4板×3, 3板×3, 2板×13, 首板×56} 与广度 75/1/39/5490 可复现；`bash scripts/self_review.sh` ✔
- **复审（第二遍，逐条重验上表）：审计结论成立**——真库跑双版本 SQL 确认 `grp` 撞组使 4 行 `is_lu` 的 streak 虚高（600127 @08-27/09-01 报 3 实为 1），且不影响 09-08 黄金日（所以只会被 `promotion_rate` 静默带坏）；东财池 `date` 语义验证：`date=20260908` 回 73 行、与本地涨停集合 73/73 重合且 `lbc` **100% 一致**，而 `date=20260904` 只重合 6 行且 0 一致 → 端点在按请求日期取数，但 `qdate` 恒为当天、不可作校验（仅服务最近约 20 个交易日，20260825 有行 / 20260820 及更早均 0 行，故对更早的 `as_of` 增强静默无操作属预期）；另修 4 处残留：前端 `source` TS union 未随契约收窄、Task 7 漏登 `shared/ui/{SectionCard,date}.tsx` 与 `format.ts`、`formatCnDate` 的或/或未决、spec 里 “20260707/20260708 可查” 实测为 0 行（应为 20260907/20260908）
- 涉及模块：plans/2026-09-14-limit-up-sentiment.md, docs/design/limit-up-sentiment.md, docs/references/best-practices.md

## 2026-09-11 - 首页公开化 Phase 3 终审修复：登录态导航/口径诚实/DRY/内链/Section 视觉收口（F1–F16）
- **F1 登录态导航**：`LandingNav` 原无条件渲染「登录」按钮，与已登录的 `CtaButton`「进入工作台」并存；改为 `isAuthReady` 后按 `isAuthenticated` 二选一（已登录渲染 `UserMenu`），并在 e2e 加「已登录不得出现登录按钮」断言（先证红：`toHaveCount(0)` 收到 1）
- **F2 公开文案不得宣称已死数据源**：`DataCoverageMatrix` 北向资金行标「规划中」（来源/频率 `—`、徽章中性灰），统计带 `10 大数据域` → `9 已上线数据域`、`100% 全部免费` → `已上线数据免费`；同步 `docs/design/landing-market-theme.md` §5
- **F3 快讯故障 ≠ 无新闻**（口径诚实，与脉搏块同批）：`AnnouncementFeed` 增 `isError` 分支独立错误占位「公告快讯暂不可用，请稍后重试」，不再落回 `暂无公告快讯`；e2e abort `/market/announcements` 断言错误占位（先证红）
- **F4 as_of/口径分家**：`SectorFlow` 申万口径补「· 数据截至 9月9日」（复用抽到 `format.ts#formatCnDate` 的格式化与 `section-card__asof` 样式）；`MarketPulse` 把「实时 + 每 60 秒刷新」限定到指数条 caption，分布/家数改「当日 …（T+1）」措辞
- **F5–F7 类型/DRY/内链**：`market.ts` `total_amount` 改 `number | null` 对齐后端 `float | None`；新增 `features/market/components/coreIndices.ts`（`CORE_TS_CODES` + `pickCoreIndices(list, count, isEligibleRest)`，其余补位过滤由调用方传入），宣传页指数条与核心指数卡共用；`SectionCard`/`DataRow` 内链改 `react-router` `<Link>`，消除整页刷新
- **F8/F9 区块视觉/占位**：`MarketMoneyflowCard` 抽出 `MarketMoneyflowContent`，`MoneySentiment` 在 `SectionCard` 内直接渲染纯内容 + 同构子标题（不再卡中卡，`/market` 用法不变）；日历块永久骨架屏改显式文案「日历即将上线」
- **F10–F16 收尾**：`CompactHero` 注释如实说明徽章为静态字符串（本页无时间源）；`get_latest_trade_date` 非 str/非法缓存值不再 `cast` 穿透，改为回退 DB；榜单 quote/turnover SQL 补 `stock_id ASC` 确定性 tiebreak（新增 3 条子串断言）；`RankingMatrix` 删除无样式死类 `ranking__asof`，改用有规则的 `section-card__asof`；`SectorFlow` 区分网络故障与合法空载荷占位；`test_market_contract._load` 全注解 `dict[str, Any]`；`landing.spec.ts` 固定 `waitForTimeout(800)` 改 network-idle + 稳定 URL
- **验证**：E2E(3010) 57 passed / 3 failed（3 项为 research×2 + userIsolation×1 既有失败，本分支未触碰该两文件）；`uv run pytest -q` 213 passed / 29 deselected（+4 新单测）；`npx tsc -b`、`npm run check:design` 12/12、`bash scripts/self_review.sh` 全绿（`npm run lint` 因 node_modules 缺 eslint 二进制不可用，非本次改动引入）
- 涉及模块：frontend/src/pages/landing/{index.tsx,landing.css,sections/{LandingNav,MarketPulse,CompactHero}.tsx}, frontend/src/features/market/components/{DataCoverageMatrix.{tsx,css},AnnouncementFeed,MarketMoneyflowCard,MoneySentiment.{tsx,css},RankingMatrix,SectorFlow,CoreIndexCards,coreIndices,format,index}.ts, frontend/src/shared/{ui/{SectionCard.{tsx,css},DataRow.tsx},api/market.ts}, frontend/e2e/{landing,public-homepage}.spec.ts, backend/app/services/market_service.py, backend/tests/{test_latest_trade_date,test_rankings,test_market_contract}.py, docs/design/landing-market-theme.md

## 2026-09-11 - 首页公开化 Phase 3 复审修复二：契约测试惰性解析 fixture + 恢复万亿元档
- **契约测试不再在 import 期算路径**：`test_market_contract.py` 原在模块级 `FIXTURES_DIR = _fixtures_dir()`，纯后端检出（无 `frontend/`）时 `_fixtures_dir()` 抛 `RuntimeError`——pytest 为读 `pytestmark` 会先 import 模块，早于 `-m` 反选，导致默认 `uv run pytest` 变成 collection ERROR。改为 `_load()` 内惰性解析，fixture 目录/文件缺失时 `pytest.skip(...)`；实测把 `fixtures/` 改名隐藏后 `uv run pytest -q` 仍 209 passed / 29 deselected 且无 collection error，`-m e2e` 为 2 skipped；fixtures 就位时容器内 2 passed
- **恢复 `fmtAmountParts` 的 `≥1e12 元 → 万亿元` 档**：前一轮把局部 `formatAmount` 抽成共享 helper 时漏了该档，当前数据不可达但属共享能力回退；已按原实现补回
- 验证：`uv run pytest -q` 209/29（fixtures 有/无均无 collection error）、契约测试容器内 2 passed、`npx tsc -b`、`npm run check:design` 12/12、E2E(3010) 32/32、`self_review` ✔
- 涉及模块：backend/tests/test_market_contract.py, frontend/src/features/market/components/format.ts, docs/references/best-practices.md

## 2026-09-11 - 首页公开化 Phase 3 复审修复：榜单/申万契约快照锁 + 行业广度条 + 缺失值收口
- **契约漂移防护**：把容器内实抓的 `/market/rankings`（四类）与 `/market/sw-industry/performance` 真实响应落为 `frontend/e2e/fixtures/rankings.sample.json` / `swPerformance.sample.json`；前端 e2e mock 改为读同一批文件（不再手写载荷），并新增 `backend/tests/test_market_contract.py`（`@pytest.mark.e2e`，DB 实连）断言两端点实际发出的顶层/条目 key 集与 fixtures 完全一致——后端字段改名会先让契约测试转红，而不是 mock 静默漂移后页面渲染 undefined。**仍不证明活链路**（3010 dev 代理指向旧镜像，故 mock 是必需的），活链路验证待分支部署
- **Task 3.2 广度条补齐**（计划 Step 2）：新增共享 `features/market/components/BreadthBar`（上涨红/下跌绿按 up_count:down_count 比例，4px 行内条，复用 `--up`/`--down`），从该目录 `index.ts` 导出，SectorFlow 申万行业行在 `DataRow` 下方消费
- **缺失值收口**：`SwPerformanceItemOut.total_amount` 改 `float | None`（`sum(amount)` 可空，原非可选会在匿名端点触发 Pydantic 500）；`DataRow` 增可选 `valuePlaceholder`，数值缺失渲染 `--`（纯涨跌幅行不传、行为不变），RankingMatrix 三 Tab 与 SectorFlow 成交额槽位消费；`amount` 千元换算抽到 `features/market/components/format.ts#fmtAmountParts` 供两处复用；总成交额作为行业行 value 显示（千元→元→亿元）
- **口径诚实**：SectorFlow 成员数小字改为「当日有行情 N只」（`member_count` 是当日 `pct_chg` 非空的成员数，非静态成分总数）
- **验证**：`uv run pytest -q` 209 passed / 29 deselected（新增 total_amount 可空单测）；契约测试容器内 2 passed（新增 `close_redis_pool()` 到 autouse dispose，避免模块级 Redis 池跨 event loop）；`npx tsc -b`、`npm run check:design`、`self_review` 全绿；E2E(3010) 32/32
- 涉及模块：backend/app/schemas/sw_performance.py, backend/tests/{test_sw_performance,test_market_contract}.py, frontend/e2e/fixtures/*, frontend/e2e/public-homepage.spec.ts, frontend/src/features/market/components/{BreadthBar.tsx,BreadthBar.css,SectorFlow.tsx,SectorFlow.css,RankingMatrix.tsx,format.ts,index.ts}, frontend/src/shared/ui/DataRow.tsx

## 2026-09-11 - 首页公开化 Phase 3（Task 2.7 / 3.1 / 3.2）：榜单切专用端点 + 申万一级行业聚合 + 板块区申万口径
- **Task 2.7 榜单**：`features/market/components/RankingMatrix` 从临时 enriched 排序路径切到 `GET /market/rankings`（新增 `shared/api/market.ts#fetchRankings`/`RankingType`/`RankingResponse`）；Tab 扩为四项（涨幅/跌幅/成交额/换手率，后端五种类型中 `volume` 不上首页）；头部按 `as_of` 显示「数据截至 9月9日」；`daily_quotes.amount` 千元口径沿用既有 mapper 约定（×1000→元 再 /1e8→亿元）不做「修正」；**移除 D2 遗留的客户端 null 行过滤**（Ruling T：新 SQL 已 `pct_chg IS NOT NULL`，客户端为重复层），保留 `DeltaText` 真缺失渲染 `--` 契约；确认零引用后删除临时 `fetchStockRanking` 及其 `RankingRow`/`RankingSortBy` 类型
- **Task 3.1 申万一级行情聚合**：新增 `GET /api/v1/market/sw-industry/performance?limit=31`（`app/schemas/sw_performance.py` + `market_service.get_sw_industry_performance` + `_SW_PERF_SQL`）：以 `sw_industry_members.symbol → stocks.symbol` 为 join 键，经 `sw_industry_classes` 两跳 `parent_code` 链把 L3 成员上卷到 L1（L1=31/L2=134/L3=346，全 L3 可解析）；聚合前 JOIN `daily_quotes` 于最新交易日并 `pct_chg IS NOT NULL`，故 `member_count`/`up_count`/`down_count` **只计当日真有行情的股票**、`avg_pct_chg` 为这些股票的真实均值；`total_amount`（`sum(amount)`）为 TuShare 千元、透传原值（注释据实标注）；匿名端点走 `CacheClient`（key `market:sw-performance`，`_MARKET_CACHE_TTL`）且空库降级为空 payload（不缓存）；`as_of` 复用 `get_latest_trade_date`
- **Task 3.2 板块区切申万**：`SectorFlow` 左列改 `fetchSwPerformance`（行版式 = 行业名 + `member_count` 小字 `35只` + `DeltaText(avg_pct_chg)`），口径标注改为「行业口径：申万一级」并删除「申万版即将上线」；**保留 CSRC 回退**——申万请求失败时回退 `/market/sectors` 且标注同步切回「行业口径：证监会」，标注永远与所展示的数据源一致，绝不静默错标（`retry: 0` 使回退即时）
- **验证**：TDD 先红后绿——Task 2.7 三条新断言先红（换手率 Tab/数据截至/不剔除 null）后 4 passed；Task 3.1 ImportError 先红后 7 passed；Task 3.2 申万标注断言先红后 4 passed；容器内以本分支代码 + ASGI 连真库实跑新端点，`limit=5` 200（as_of=2026-09-09，与原生 SQL 逐值一致）、默认 31 条且降序、`limit=0/99` 422，Redis `market:sw-performance` TTL≈277s；真库 SQL 实测 31 个 L1 组、4,058 个当日有行情成员且无 symbol 扇出
- 测试：`uv run pytest -q` 208 passed / 27 deselected；`uv run --extra dev mypy app` 143 files 无问题；前端 `npx tsc -b`、`npm run check:design`、`bash scripts/self_review.sh` 全绿；E2E（3010）landing 7 + darkmode 3 + public-homepage 22 = 32/32
- 涉及模块：backend/app/services/market_service.py, backend/app/schemas/sw_performance.py, backend/app/api/v1/market.py, backend/tests/test_sw_performance.py, frontend/src/shared/api/market.ts, frontend/src/shared/api/stocks.ts, frontend/src/features/market/components/{RankingMatrix,SectorFlow}.tsx, frontend/e2e/public-homepage.spec.ts

## 2026-09-11 - 首页公开化 Phase 2（Task 2.4–2.6）：共享交易日解析 + 公开 `/market/rankings` 榜单 + 索引计划锁
- **Task 2.4**：`market_service.get_latest_trade_date(db, cache=None)`（Redis key `market:latest_trade_date`，TTL 300s）为"数据截至日"的单一实现：`_latest_trade_date` 改为其无缓存薄委托（`max(trade_date)` 只写一处），既有四处 dashboard 读取器（distribution/sectors/capital-flow/hot-boards）复用同一实现但**行为不变、仍为直读无缓存**，rankings 走 300s 缓存路径；缓存按 Ruling Q 存 `as_of.isoformat()`、读出 `date.fromisoformat` 兜底（`CacheClient` 是 JSON 序列化，`date` 不可直接 JSON 化）；纯函数 `last_weekday` 为 `is_latest_trading_day` 的 Phase-2 启发式（节假日不处理，Phase 4 换 `trade_calendar` 同签名）
- **Task 2.5**：新增 `app/schemas/ranking.py`（`RankingItemOut` / `RankingResponseOut`，含 `as_of` + `is_latest_trading_day`；`amount` 注释据实标为 TuShare 原生**千元**、透传原值）与 `GET /api/v1/market/rankings?type=gainers|losers|amount|turnover_rate|volume&limit=`；五种类型经文件内硬编码 `_RANKING_ORDER` 白名单 f-string 拼 ORDER BY（Ruling O：bind 参数不能承载标识符/方向，删 `:rank_col_expr`，无用户输入进串；Pydantic `Literal` 边界校验 + service 侧 membership 复检双闸）；`daily_quotes` 四种保留 `pct_chg IS NOT NULL`（Ruling P：DESC 默认 NULLS FIRST，否则涨幅榜以 NULL 行领跑），`turnover_rate` 走 `daily_basic_indicators`；top-N CTE 排好后外层再 ORDER BY，保证补字段 JOIN 不重排；缓存优先 + `_MARKET_CACHE_TTL`（Ruling S）；**空库降级**：rankings 层捕获 `ValueError` 返回空 payload（`as_of`=最近工作日、`is_latest_trading_day=false`、`items=[]`，不缓存），公开首页块不再 500，而 `get_latest_trade_date` 对确需日期的调用方仍抛 `ValueError`
- **Task 2.6**：新增 `tests/test_rankings_index.py`（`@pytest.mark.e2e`，默认 addopts 排除），锁三条 Task 2.1 索引的计划不回退：gainers→`idx_daily_quotes_date_pct`、amount→`idx_daily_quotes_date_amount`、turnover_rate→`idx_daily_basic_date_turnover`，断言命中期望索引节点且计划无 `Sort`
- **验证**：TDD 先红后绿（Task 2.4 纯函数 ImportError→5 passed；Task 2.5 schema ModuleNotFoundError→7 passed）；容器内以本分支代码 + ASGI transport 连真库实跑，5 种类型 200 且返回真实行，非法 `type` 与 `limit=0` 均 422；Task 2.6 三例 e2e 容器内 3 passed，实抓计划分别为三种 Index Scan
- 测试：`uv run pytest -q` 201 passed / 27 deselected；`uv run --extra dev mypy app` 142 files 无问题
- 涉及模块：backend/app/services/market_service.py, backend/app/schemas/ranking.py, backend/app/api/v1/market.py, backend/tests/test_latest_trade_date.py, backend/tests/test_rankings.py, backend/tests/test_rankings_index.py

## 2026-09-11 - 首页公开化 Phase 2（Task 2.1–2.3）：日线落库原生 pct_chg/pre_close + 权威回填
- **Task 2.1**：Alembic 迁移 `cf4b8e317fe5`（`5a1b2c3d4e5f` →）为 `daily_quotes` 增可空 `pre_close Numeric(12,4)` / `pct_chg Numeric(8,4)`，并建 `idx_daily_quotes_date_pct(trade_date,pct_chg)`、`idx_daily_quotes_date_amount(trade_date,amount)`、`idx_daily_basic_date_turnover(trade_date,turnover_rate)`；实查 `pg_partitioned_table` 无 `daily_quotes` 行（未分区），故用普通复合索引、不加分区/合并分支；已 `alembic upgrade head`，`heads` 单行
- **Task 2.2**：`DailyQuote` 模型 + `tushare_ingest` 两处日线构造（按 trade_date 全市场、按股票区间）+ `quote_repo.upsert_quotes` 的 VALUES 与 `on_conflict_do_update.set_` 全部补 `pre_close`/`pct_chg`（落地点四处缺一即静默丢字段）
- **Task 2.3**：新增 `scripts/backfill_pct_chg.py`（`--years`/`--limit`/`--start-date`/`--end-date`，幂等）与 `quote_repo.update_pct_chg_for_date`（`sa.Values` + Core `UPDATE ... FROM (VALUES ...)` 单语句，仅 UPDATE 不 INSERT）；**只回填 TuShare 原生值，禁止 `LAG(close)` 现算**（除权日参考前收需为除权后价），理由写入 docstring
- **验证**：TDD 先红（`AttributeError: 'DailyQuote' object has no attribute 'pct_chg'`）后绿；`--limit 4`（2026-09-04…09-09）实机回填 22,196 行，重复运行同值（幂等），与 TuShare 原生逐行对拍 22,196 行 0 不一致（抽样 000001.SZ/300750.SZ/600000.SH 值全等）；全量 3 年约 750 次 `fetch_daily` 由运维另跑（见 Task 2.1–2.3 报告）
- 测试：新增 `tests/test_daily_ingest_pct_chg.py` 3 例（映射落库、upsert 冲突补列、回填仅 UPDATE 无 LAG）；`uv run pytest -q` 185 passed / 24 deselected
- **D4 复审修复**：三个排行索引补进 ORM `__table_args__`（`models/quote.py`、`models/daily_basic.py`），使 `alembic check` 不再对它们报 `remove_index`（输出中其余 13 项为既有漂移，不在本次范围）；upsert 测试改为按 `ON CONFLICT` 切分断言 INSERT 列清单——原整体字符串断言被 SET 的 `excluded.*` 覆盖，删掉 VALUES 字典条目仍会假绿；新增第二处 ingest（`ingest_daily_quotes_for_stock`）回归测试。测试共 4 例，两处断言均以变异测试验证会转红
- 涉及模块：backend/app/migrations/versions/cf4b8e317fe5_add_pct_chg_to_daily_quotes_and_ranking_.py, backend/app/models/quote.py, backend/app/models/daily_basic.py, backend/app/services/tushare_ingest.py, backend/app/repositories/quote_repo.py, backend/scripts/backfill_pct_chg.py, backend/tests/test_daily_ingest_pct_chg.py

## 2026-09-11 - 首页公开化 Phase 1 收口：板块/资金/快讯三块 + 零 401 与降级门禁（Task 1.7–1.10）
- **Task 1.7 板块区**：新增 `features/market/components/SectorFlow`（左列 `/market/sectors` 证监会口径、右列 `/market/capital-flow` 近似口径双向条）；`/market/sector-moneyflow` 实测返回 `[]` 无法支撑资金列，改走可用源并在 UI 标注「近似口径：涨/跌股成交额估算，非主力净流入」；两列独立查询独立降级
- **Task 1.8 资金区**：新增 `MoneySentiment` 复用 `MarketMoneyflowCard`（`/market/market-moneyflow`）；**北向卡整卡移除**——`northbound_daily` 无数据行（非仅滞后），不以本地序列替补
- **Task 1.9 快讯区**：新增 `MarketNewsFeed`（财报公告/重大事项两 Tab，各 10 条），复用并导出 `dataFace/AnnouncementFeed`（增 category/limit/timeMode 可选 props + `format.fmtRelativeTime`）；零新数据源（TuShare 新闻接口积分门槛），不做伪新闻流
- **Task 1.10 收口**：页脚加「数据来源：TuShare · 东方财富 · 巨潮资讯网 · 上海证券交易所」；免登录行情文案纠偏（移除行业树「注册后查看」、账号能力区「注册即解锁全部投研能力」与注册档「猪周期工作台完整能力」，仅个性化——自选/标签/同步/提醒——保留在注册档）；新增 e2e 硬门禁——匿名全链路除 `/auth/session` 探测外零 401/403（并断言确发出行情请求防假绿）、单源/全源 abort 不白屏
- 测试：public-homepage 8 → 20，合计 30/30（landing 7 + darkmode 3）全绿
- 涉及模块：frontend/src/features/market/components/{SectorFlow, MoneySentiment, MarketNewsFeed, dataFace/AnnouncementFeed, format.ts}, frontend/src/pages/landing, frontend/e2e/public-homepage.spec.ts

## 2026-09-11 - 首页公开化 Phase 1（D2 复审修复）：榜单剔除 null 行 + 脉搏区表面收口
- **榜单**：涨幅/跌幅榜（及成交额榜）按当前排序维度剔除 null 行再截断 top-10（多取 20 条），修掉后端 desc 把无行情次新股排到榜首、涨幅榜首屏两行 `--` 的问题；`--` 缺失值契约改在不受过滤影响的成交额榜断言（Task 2.7 切 `/market/rankings` 后 SQL 已 `pct_chg IS NOT NULL`，此守卫为保险）
- **脉搏区**：分布返回 `[]` 不再渲染成 `上涨 0 · 下跌 0`，改走「今日涨跌分布暂不可用」占位；分布柱家数补千分位与汇总行统一；`MarketPulse`/`index.tsx` 改直连模块路径，避免 barrel 把 ECharts 视图拖进匿名首页 chunk
- **清理**：删除已无引用的 `.landing-hero-secondary-cta`；`LandingNav` aria-label 改为「行情台区块导航」（`darkmode.spec.ts` 同步）
- 测试：分桶完整性用例显式断言「上涨+下跌 == 逐桶求和 == 常量」；新增空分布占位用例；18/18 全绿
- 涉及模块：frontend/src/features/market/components/RankingMatrix.tsx, DistributionBars.tsx, frontend/src/pages/landing, frontend/e2e

## 2026-09-11 - 首页公开化 Phase 1：免登录行情台 IA 重排（Task 1.4–1.6）
- **背景**：产品决策「免登录行情为主」——行情区块是首页主内容，营销区压缩到行情台之后；导航锚点从「功能/数据/行业」改为「脉搏/榜单/板块/资金/快讯」（`#pulse/#rankings/#sectors/#money/#news`）
- **Task 1.4**：`Hero` 压缩为 `CompactHero`（保留 H1/信任行/CTA，实测高 232px ≤ 240px 目标，大图与次 CTA 移除），`index.tsx` 区块序列改为 紧凑 Hero → 脉搏/榜单/板块/资金/日历/快讯 → ValueProps/ProductShowcase → DataCoverage/IndustryGrid/AccountPerks/BottomCTA；未填充行情区块以 `<SectionCard>` + AntD `Skeleton` 显式骨架挂载
- **Task 1.5**：新增 `features/market/components/DistributionBars`（横向双向柱，与 `/market` DistributionChart 同 11 桶口径），`MarketPulse` 包进 `<SectionCard id="pulse">` 并加 `.index-ticker` 锚；**修复分桶漏桶**：`UP_RANGES` 补 `0~1%` 使两侧对称穷尽（实测上涨由 954 → 1,903，不再静默丢掉 949 只），失败降级文案去掉「注册后」改为「行情数据暂不可用，请稍后重试」
- **Task 1.6**：新增 `shared/api/stocks.ts#fetchStockRanking`（走 Task 1.3 已缓存的 `/api/v1/exchanges/stocks/enriched` 排序端点）与 `features/market/components/RankingMatrix`（涨幅/跌幅/成交额三 Tab；换手率榜后端未支持，Phase 2 Task 2.7 补）；缺失涨跌幅经 `DeltaText` 渲染 `--`
- **测试**：`public-homepage.spec.ts` 扩至 7 例（含「上涨+下跌 == 11 桶总和」「null 渲染 `--` 不渲染 0.00%」「脉搏区失败不影响榜单区」）；`landing.spec.ts` 分桶期望随口径修正 134 → 234；landing 7 + darkmode 3 + public-homepage 7 全绿
- 涉及模块：frontend/src/pages/landing, frontend/src/features/market/components, frontend/src/shared/api/stocks.ts, frontend/e2e

## 2026-09-11 - 首页公开化 Phase 1（D7 修订）：暗色涨跌色达标卡片表面，门禁统一
- **问题**：`SectionCard` 内 `DeltaText`/`DataRow` 实际渲染在 `--bg-panel #1e222d`；暗色 `--up #f23645`(4.08:1) / `--down #089981`(4.45:1) 对卡片面不达 WCAG AA（对 `--bg-page #131722` 的 4.59/5.01 达标掩盖了该缺陷）。原 D7「暗色已达标故不动」是在错误表面（`--bg-page`）上测得，予以推翻
- **修复**：暗色 `--up #f2555a`（bg-page 5.30 / bg-panel 4.71）、`--down #0aa088`（bg-page 5.45 / bg-panel 4.84），同步 `:root[data-theme="dark"]` 与 `theme.ts` `THEME_COLORS.dark`；`:root` 兜底块保持浅色不变
- **门禁**：移除 `check-design-tokens.mjs` 内的暗色例外，`check:design` 对 fallback/light/dark 三块 × `--bg-page`/`--bg-panel` 两表面统一断言 ≥ 4.5，12 项全 PASS、exit 0
- 涉及模块：frontend/src/app/styles/theme.css, frontend/src/app/theme.ts, frontend/scripts/check-design-tokens.mjs, docs/design/landing-market-theme.md

## 2026-09-11 - 首页公开化 Phase 1（D1 复核补漏）：设计令牌门禁接入 CI + 覆盖卡片表面
- **问题**：`npm run check:design` 只存在于 package.json，无任何自动化路径调用，改色破坏 AA 或 `theme.ts`/`theme.css` 失同步时 CI/lint/build 全绿；且原脚本只对 `--bg-page` 校验，漏掉 DataRow/DeltaText 实际所在的 `SectionCard` 表面（`--bg-panel`）
- **修复**：CI `frontend-lint` job 在 `npm ci` 后新增 `npm run check:design` 硬门禁步骤；脚本对 light/fallback 同时校验 `--bg-page` 与 `--bg-panel` 对比度 ≥ 4.5
- **发现（已由 D7 修订销项）**：暗色 `--up #f23645`(4.08:1) / `--down #089981`(4.45:1) 对 `--bg-panel #1e222d` 不达 AA；本条目当时未改暗色、暂只校验 `--bg-page`，随后按 D7 修订改为暗色换值并统一两表面校验（见上一条）
- 涉及模块：.github/workflows/ci.yml, frontend/scripts/check-design-tokens.mjs, docs/references/best-practices.md

## 2026-09-11 - 首页公开化 Phase 1：enriched 排序路径加 Redis 缓存（公开流量防护）
- **问题**：`/api/v1/exchanges/stocks/enriched` 公开可达，带 `sort_by` 时每次请求都走「全市场查询 + LATERAL + Python 排序」且完全无缓存，匿名流量可打满 DB
- **修复**：`list_stocks_enriched` 排序分支前置 Redis 缓存（key `stocks:enriched:sort:{exchange}:{category}:{keyword}:{sort_by}:{sort_order}:{offset}:{page_size}`，TTL 60s），命中直接反序列化返回，不再触库；key 含全部过滤维度，避免不同筛选互串
- **连带修复**：端点原本以 `cache=None` 调用（注释称缓存参数未被使用），导致缓存无法生效；改为注入 `CacheDep` 传入真实 `CacheClient`，让防护真正短路
- 涉及模块：backend/app/services/stock_service.py, backend/app/api/v1/stocks.py, backend/tests/test_stock_sort_cache.py

## 2026-09-11 - 首页公开化 Phase 1：共享 UI 原语 DeltaText / DataRow / SectionCard
- 新增 `frontend/src/shared/ui/` 三个行情区块基础件：`DeltaText({value, suffix?})`（正 `+`/负 `−`/零无符号/缺失 `--`，消费 `--up`/`--down` CSS 变量随主题切换）、`DataRow({logo?, title, ticker?, value?, unit?, href?, delta?})`、`SectionCard({id?, title, moreHref?, moreText?, children})`；签名由后续所有行情区块消费，不得改动
- 三个组件与类型经 `shared/ui/index.ts` 桶文件导出；新增 `frontend/e2e/public-homepage.spec.ts` 最小冒烟（`/` 返回 200 且 H1 可见）
- 涉及模块：frontend/src/shared/ui, frontend/e2e

## 2026-09-11 - 首页公开化 Phase 1：涨跌色 WCAG AA 修复 + 设计令牌门禁
- **问题**：浅色模式 `--up #f5222d` / `--down #22c55e` 对 `--bg-page #ffffff` 对比度仅 4.08:1 / 2.28:1，均低于 WCAG AA 的 4.5:1
- **修复**：浅色涨跌色改为 `--up #c62828`（5.62:1）/ `--down #0a7d5f`（5.11:1），同步三处声明（`:root` 首帧兜底块、`:root[data-theme="light"]`、`theme.ts` `THEME_COLORS.light`）；`flat`/`hover` 不动。暗色当轮未动，后经 D7 修订换值（见上）——原判据只测了 `--bg-page`，未覆盖 `SectionCard` 的 `--bg-panel`
- **门禁**：新增 `frontend/scripts/check-design-tokens.mjs` 与 `npm run check:design`，校验三块 `--up`/`--down` 对比度 ≥ 4.5 且 `theme.ts` 同名值一致；任何改色先跑它
- 涉及模块：frontend/src/app/styles/theme.css, frontend/src/app/theme.ts, frontend/scripts, docs/design/landing-market-theme.md

## 2026-09-11 - 首页公开化 Phase 0：noindex / SEO 决策记录
- **核实**：`curl -sI` 实测 `http://127.0.0.1:80/`（frontend 路由，200）与 `/api/v1/market/distribution`（HEAD 405 / GET 200）均返回 `X-Robots-Tag: noindex, nofollow, nosnippet, noarchive`；四条 router（frontend/api/auth/api-tasks）均挂载 `sec-headers@file`，中间件定义在 `gateway/dynamic/middlewares.yml:35-45`
- **决策**：维持整站 `noindex`（理由：公开再分发行情数据有条款约束 + Vite SPA 难索引、投入产出不成比例）；本计划删除一切 SEO /「静态可索引落地路径」目标；页脚数据来源署名（Task 13）保留，定位为面向用户的合规署名而非 SEO
- **复核**：`api-ratelimit` 的 `burst(50) < average(100)` 仅记录为「待用户确认是否有意为之」，未改网关配置
- 涉及模块：plans/2026-09-11-public-market-homepage（§0.5.1、风险 ⑦）, docs/references/best-practices（八）

## 2026-09-11 - 首页公开化 Phase 0：数据语义核实（北向/指数覆盖/分区状态）
- **问题**：公开行情首页的三个前置事实未定，直接决定北向模块形态、指数条数据源与 Phase 2 索引方案
- **核实**：
  - 北向 `northbound_daily` 实测**全表 0 行**（非仅近 30 天断流），`/market/northbound` 返回 `[]`；`market_moneyflow_daily`、`sector_moneyflow_snapshots` 同为 0 行
  - `index_dailies` 含 6 个指数：`000001.SH/000016.SH/000300.SH/399001.SZ/399006.SZ/899050.BJ`，`last_date` 均 `2026-09-10`；`GLOBAL_INDICES` 已含沪深300/上证50/北证50，与 `market_service._TARGET_INDICES` 对齐，**无需修 job**
  - `daily_quotes` 实测**未分区**（`pg_class.relkind='r'`，`pg_partitioned_table` 计 0），Phase 2 用普通复合索引
- 风险表 ②③④ 已销项并附原始查询输出，Task 11/14 的处置已明确
- 涉及模块：plans/2026-09-11-public-market-homepage, docs/references/best-practices

## 2026-09-11 - 公开行情台首页实施计划（含免登录现状核实与数据面缺口盘点）
- 新增 `plans/2026-09-11-public-market-homepage.md`：6 阶段 tracer-bullet 计划 + 前置 spike，目标为首页在未登录状态即可作为完整行情台（指数/榜单/板块/日历/资讯），登录只解锁个性化（自选/标签/提醒）
- **免登录现状核实**（结论：已基本成立，非鉴权改造项目）：网关 `forward-auth` 对无 session cookie 请求匿名放行（`return Response(status_code=200)`）；路由层仅 `/watchlist`、`/tags` 包 `RequireAuth`；后端行情类端点（exchanges/stocks/financials/market/market-data/clusters/industries-GET）均未挂 `CurrentUserDep`
- **数据面缺口盘点**（决定成本的三项）：
  - 榜单无高性能路径——`stocks/enriched?sort_by=` 实为「拉全市场 10k + LATERAL + Python sort」（约 +70ms/2300 只），且不支持换手率/成交量排序
  - `pct_chg` 被丢弃——TuShare `daily` 原生返回 `pct_chg`/`pre_close`，但 `DailyQuote` 模型无此列且 `tushare_ingest.py` 未映射，导致涨跌幅只能查询时现算、无索引可支撑（修复成本极低）
  - 日历（财报/分红/IPO/宏观）与财经新闻**均无数据源**；交易日历无持久化表，调度守卫 `weekday()<5` 导致节假日空跑
- **设计面实测**：现有涨跌色浅色模式不达 WCAG AA——涨 `#f5222d` 4.08:1、跌 `#22c55e` 仅 2.28:1（暗色 4.59/5.01 达标），给出重取取值与对比度回归测试要求
- 设计参考：TradingView 首页 chrome-devtools 实测（248 CSS 变量具名调色板、`--v-rhythm-*` 四档响应式节奏刻度、`data-theme` 单属性换肤、模块模板×资产类别矩阵式 IA）作为**内部设计参考**记入附录 A
- **计划修订 + 任务级细化（同日二轮，评审后）**：
  - 定位拍板：首页**以免登录行情为主体**，营销区压缩为尾部一段（Spec D1 重写）；涨跌色修复从 Phase 5 提前到 Phase 1（独立可访问性缺陷）
  - 评审发现 3 处硬伤并已修入计划：① Phase 1 临时榜单路径（`stock_service.list_stocks_enriched` 排序分支）**完全无缓存**而公开暴露 → 新增 Task 1.3 Redis TTL 缓存；② 网关 `sec-headers` 对整站打 `X-Robots-Tag: noindex`，SEO 目标与之矛盾 → 降级为 Phase 0 决策项（默认维持 noindex）；③ Phase 2 验收曾引用**从未实施的 Tier 2 API 基准** → 改为 EXPLAIN 索引断言测试（Task 2.6）
  - 评审补充 6 项已并入：申万口径（CSRC 临时 + Phase 3 申万聚合端点）、北向数据语义核实（2024 披露口径变化）、`pct_chg` 回填改权威源重拉（禁 `LAG(close)` 现算——除权日会错）、防护清单（§0.5）、e2e 具体化（`public-homepage.spec.ts` 零 401 + 降级用例）、页脚数据来源署名
  - 新增 `plans/2026-09-11-public-market-homepage-tasks.md`：superpowers 任务级计划，Phase 0–3 共 17 个 Task，逐步 TDD（含测试与实现代码、EXPLAIN 断言、e2e 用例）；Phase 4（日历）/5（资讯增强）待 Phase 0 spike 结论后另立计划
- 涉及模块：plans、docs/Changelog、docs/references/best-practices

## 2026-09-09 - API Gateway 选型调研
- 比较 Docker Compose 场景下 NGINX、Traefik、Kong/APISIX 的动态路由、OIDC/JWT、限流、可观测性、配置复杂度与 Kubernetes 演进适配性，并结合当前 stock_bot 架构给出 Gateway 选择建议。
- 涉及模块：架构调研、部署、认证、可观测性

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
208	- 涉及模块：backend/models/securities, backend/migrations, backend/core/providers/tushare_client, backend/services/industry_registry, backend/services/securities_service, backend/repositories/securities_repo, backend/core/mq, backend/workers/securities_worker, backend/scheduler, backend/services/task_service, backend/api/v1/tasks, backend/api/v1/industries, backend/schemas, frontend/shared/api, frontend/features/industry-research, frontend/pages/research-workbench, backend/tests, frontend/e2e
209	
210	## 2026-09-09 - 前端统一请求层、认证状态机与路由守卫 (Stage 2)
211	- 重构 `shared/api/client.ts` 通用底层请求（强制 `credentials: include`、CSRF 单飞并发获取与自动注入、结构化 `ApiError` 统一解析、401 拦截事件广播），新建 `shared/api/auth.ts` 契约客户端，实现基于 Zustand + React Query 的 `features/auth` 登录状态机与 `RequireAuth` 路由守卫，集成 `/login` 登录/注册表单与导航栏 `UserMenu`。
212	- 涉及模块：frontend/shared/api, frontend/features/auth, frontend/pages/login, frontend/app/router, frontend/app/layouts


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
- 涉及模块：backend/repositories/quote_repo, backend/services/quote_service, backend/api/v1/stocks, frontend/shared/ui/kline, backend/tests

## 2026-09-09 - 微服务认证与授权最佳实践研究
- 基于 NIST Zero Trust、OWASP API Security、OAuth JWT Access Token/JWK 与 SPIFFE/SPIRE 官方资料，明确 Gateway 与业务服务二次校验、workload identity/mTLS、aud/iss/scope/RBAC 责任边界、token 转发与 exchange/downscoping 原则，并形成 stock_bot（FastAPI、RabbitMQ Worker、Scheduler、Redis、PostgreSQL、Docker Compose）的落地建议。
- 涉及模块：docs/references/best-practices、backend/api、backend/core、backend/workers、backend/scheduler、docker-compose

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
- 2026-09-04 新增大盘资金流卡（沪深两市合成口径）：今日主力/超大/大/中/小四档净额实时 + 近 30 日主力净流入红绿柱；新表 market_moneyflow_daily，16:20 盘后增量 + 120 日历史回补，东财 fflow/daykline 恒等式入库校验。
- 2026-09-04 市场页重设计（对齐主流）：涨跌分布加涨跌平衡条与成交额头部、柱色改数值驱动强度渐变（移除桶名字符串匹配）；板块热力图改连续色阶（Finviz 式梯度）+ 浅色块深字 + 点击下钻行业页 + 领涨股 tooltip；大盘资金流接口补实时成交额（f6）。
## 2026-09-09 - P1 财务数据底座 + 估值分位（行业投研工作台前置能力）
- **背景**：按 `plans/industry-research-workbench.md` P1（财务与估值底座），当前系统只有日频行情/`daily_basic`，无三大报表与财务指标
- **新增（后端）**：
  - 六张财务表（`financial_raw_records` / `financial_report_versions` / `income_statement_facts` / `balance_sheet_facts` / `cash_flow_statement_facts` / `financial_metrics`），报告版本带 end_date/report_type/comp_type/ann_date/source/update_flag/quality_status，派生指标带 calc_method+quality；迁移 `b1f2c3d4e5a6`
  - TuShare Provider 新增 `fetch_income` / `fetch_balance_sheet` / `fetch_cash_flow` / `fetch_financial_indicator`
  - `FinancialIngestService`（拉取→raw JSONL+DB→报告版本→facts→派生指标），`FinancialWorker` + `financial.fetch` 队列 + `POST /tasks/fetch-financial`
  - 财务 API（`financial-summary` / `financial-statements` / `financial-metrics/history`）、估值历史分位 API（`valuation-history`，基于 daily_basic，按 1y/3y/5y 正样本计算分位）
- **派生指标口径**：折让比/费用率/负债率/杜邦拆解为 `calculated_from_statement`（derived），ROE/毛利率/同比等优先取 TuShare `fina_indicator` 的 `reported_by_provider` 值，缺失显示空而非 0；负值/缺失从估值分位样本中排除
- **前端**：个股详情页新增"估值/财务"Tab（指标卡+ECharts 趋势+三张报表表+估值分位图），数据来源/报告期/质量徽章展示（由子 Agent 并行实现）
- **注意**：P0 的 outbox 任务投递事务、跨交易所 symbol-only enriched 修复等仍未纳入本次切片；全市场财报回补需先按 `financial-data-dictionary` 做数据源 POC
- 涉及模块：backend/models, backend/migrations, backend/core/providers/tushare_client, backend/services(financial_ingest/financial_service), backend/repositories/financial_repo, backend/api/v1(financials/tasks), backend/workers, backend/core/mq, frontend/pages/stock-detail, frontend/features/stock-detail, frontend/shared/api

## 2026-09-08 - 全市场财务回填 + 财务表查询索引优化
- 财务数据改为"自动回填"：新增 `financial_backfill` service 逐批幂等回填缺失财报的标的（共享 FinancialWorker 同源 ingest），scheduler 注册 `financial_backfill` cron（工作日 7-8/15-23 点每 20 分钟），并新增 `python -m app.scheduler.backfill` 一次性全量入口
- 新增 financial_metrics 两个索引：覆盖索引 `(stock_id, metric_key, report_version_id)` 加速个股财务指标时序读取与排序，`(report_version_id)` 外键索引加速 join 与级联删除；实测个股财务时序查询 ~6ms
- 涉及模块：backend(scheduler/services/repositories/models migrations), 全市场约 5300 只标的逐步补齐

## 2026-09-08 - 后端 CI 债务清理（并入财务 PR）
- **根因**：CI 的 `uv run` 未带 `--extra dev`，导致 ruff/mypy/pytest 从未真正安装 → 后端 Lint/TypeCheck 形同虚设，存量 92 文件格式分叉、mypy 115 错累计到 main
- **修复**：
  - ci.yml 三个后端 job 的依赖安装统一加 `--extra dev`；Test job 的 alembic 步骤补 `DATABASE_URL` 指向 `stock_bot_test`；pytest 改 `-m "not e2e"`
  - 全仓 `ruff format` 对齐（141 文件）+ 全部 115 个 mypy 错误清零（历史欠账+财务新代码）
  - 修复 2 个真 bug：`task_repo` 缺 `func` import（count_tasks 运行时 NameError）、`financial_ingest` 缺 FinancialReportVersion import
  - `test_health`/`test_stocks` 标记为 e2e（依赖真实运行 API），CI 只跑非 e2e（本地 125 passed）
- 涉及模块：backend(全仓 lint/type/test), 顶层 CI 配置, backend/services(task/financial_ingest), backend/repositories/task_repo, backend/tests

## 2026-09-09 - feat/market-data-face PR CI 修复（合并 main 落债务清理 + Alembic merge 迁移）
- **根因**：PR #1 基于旧 main，CI 跑的是坏管道（dev group 未装→Lint/TypeCheck spawn 失败；alembic 无 DATABASE_URL）；债务清理此前锁在 PR #2，未落 main
- **修复**：先合并 PR #2 到 main（落 CI 修复+全仓格式/mypy 清理+.env.example），再把 main merge 进 PR #1（解决 10 处冲突，双侧逻辑并集）；新增 Alembic merge 迁移 49741053b341 合并财务/市场数据两条链；ruff format 对齐 PR#1 自有 11 文件
- 本地验证 ruff/mypy 全绿、pytest 155 passed；PR #1 六项 CI 全绿
- 涉及模块：backend(migrations/models/scheduler/services/providers/repos), frontend(stock-detail), docs

## 2026-09-09 - 认证微服务、Gateway 与数据归属架构设计 (Stage 0)
- 完成企业级认证授权与多租户数据隔离体系的 Stage 0 顶层设计：明确 Gateway BFF 与 auth-service 目标拓扑、HttpOnly Session Cookie 与 CSRF 双重防御机制、下游 API 零信任短时 Principal Assertion (RS256) 签名与 JWKS 验签机制、统一 JSON 错误契约 (code/message/details/trace_id)、独立 auth_* 数据模型 (Argon2id / RTR Token Family)、业务模型多用户改造方案 (stock_custom_sw_tags 增加 user_id、自选股服务端化、tasks 增加 requested_by、Redis Key 用户隔离) 与 Stage 0-6 分阶段实施计划。
- 涉及模块：docs/architecture/authentication-and-gateway, docs/architecture/auth-data-model, plans/2026-09-09-auth-gateway-implementation

## 2026-09-09 - auth-service 独立微服务开发与凭证/JWKS体系 (Stage 1)
- 实现独立的 `auth-service` 微服务工程体系，构建基于 Argon2id 的安全密码哈希与防暴力锁定、Redis 高性能滑动会话与 DB 持久化同步、CSRF 双重校验、RSA 短时 Principal Assertion JWT 签名与 RFC 7517 JWKS 公钥分发端点，完善了注册、登录、登出、会话查询与多端下线、网关会话内省等接口，提供全链路 100% 通过的离线单元与集成测试套件。
- 涉及模块：auth-service(core/models/services/schemas/api/migrations/tests), plans, docs

## 2026-09-09 - 引入 Traefik API Gateway 并完成容器拓扑与端口收敛 (Stage 3)
- 引入 Traefik v3 API Gateway 作为整站统一流量入口，配置 JSON 结构化日志、Prometheus 监控指标与动态中间件（Security Headers、Rate Limit、Compression）；重构根 `docker-compose.yml` 容器编排体系，新增 `auth-db`、`migrate-auth` 与 `auth-service` 微服务，收敛下线 `api:8000` 与 `frontend:3000` 的宿主机端口暴露，实现基于 Traefik labels 的动态路由分发与 tasks 任务触发写限流保护，并提供 `.env.docker.example` 容器配置模板。
- 涉及模块：gateway, docker-compose.yml, auth-service, backend/docker-compose.yml, .env.docker.example, plans, docs

## 2026-09-09 - Stock API 零信任身份断言验签与权限矩阵保护 (Stage 4)
- 实现基于 JWKS 异步加载与本地公钥缓存（单飞刷新）的 Principal Assertion (RS256) 验签器与 RBAC 权限依赖注入器（CurrentUserDep / OptionalUserDep / require_roles / require_permissions）；全面保护 tasks 触发与取消、行业指标 batch 导入、SSE 历史回补等敏感接口；客户端未签名 X-User-* 伪造头一律严格丢弃；修复前端跨交易所 fallback 仅对 404 降级与股票详情页 error/not-found 状态分流；新增 9 项无 DB 依赖单元与集成测试。
- 涉及模块：backend/config, backend/core/auth, backend/api/deps, backend/api/v1/tasks, backend/api/v1/industries, backend/api/v1/market, frontend/shared/api, frontend/pages/stock-detail, backend/tests

## 2026-09-09 - 用户标签与自选股服务端化与多用户数据隔离 (Stage 5)
- 实现多用户业务数据隔离与自选股服务端化：ORM 模型（StockUserTag / UserWatchlist / UserWatchlistItem / Task）增加 `user_id` / `requested_by` 并完成 Alembic 迁移脚本 `5a1b2c3d4e5f`；新增 `watchlists` 路由、改造 `user-tags` 及全局标签端点接入 `CurrentUserDep` 与 `user:{user_id}:*` 缓存隔离；前端对接服务端自选股 API，升级 `useWatchlist` 状态机与标签组件按登录态分流读写，登出全量清理；编写 9 项纯单元与路由隔离测试，验证 100% 通过。
- 涉及模块：backend/models, backend/migrations, backend/repositories, backend/services, backend/schemas, backend/api/v1(stocks/tags/watchlists/tasks), frontend/shared/api(watchlist/userTags), frontend/features/watchlist, frontend/features/stock-detail, frontend/pages(watchlist/tags/tags-detail), backend/tests

## 2026-09-09 - CI/CD 自动化门禁扩展与端到端测试覆盖 (Stage 6)
- 扩展 `.github/workflows/ci.yml` 引入 `auth-service` 的 Lint、TypeCheck 与 Test 门禁 job，并更新 `docker-smoke` 支持在 Traefik 网关（端口 80）下验证前端、API 及 `/auth` 接口全链路连通性；扩展 `.github/workflows/cd.yml` 增加 `auth-service` 多架构镜像构建推送；新增前端 Playwright E2E 认证与多用户隔离测试套件（`auth.spec.ts` 与 `userIsolation.spec.ts`）；完成全系统各微服务本地全量验证与阶段性实施计划收官。
- 涉及模块：.github/workflows/ci.yml, .github/workflows/cd.yml, frontend/e2e, plans, docs




## 2026-09-09 - P0 评审修复：forward-auth 断言注入与 JWT 契约统一
- 修复 Traefik 未向 backend 注入身份断言导致登录后受保护 API 全部 401 的 P0 缺陷：新建 `forward-auth` sidecar（会话 Cookie 换取短时 Principal Assertion、CSRF 强制、匿名放行、fail-closed 503），新增 `strip-assertion` 防伪造中间件并接线 `api`/`api-tasks` 路由；统一 auth-service 与 backend 的 JWT iss/aud 默认契约（`stock-bot-auth` / `urn:stock-bot:api`）并在 compose 显式对齐，新增两端交叉契约测试与 CI 注册→登录→受保护接口 401/200 闭环 smoke。
- 涉及模块：forward-auth(新建), gateway/dynamic, docker-compose.yml, .env.docker.example, auth-service/config, backend/tests, auth-service/tests, .github/workflows/ci.yml, docs, plans

## 2026-09-09 - P1 安全加固：凭据收敛、CSRF 强制与生产安全配置
- 落实安全评审 P1 项：登录响应体不再返回 session_id/csrf_token（凭据仅经 Set-Cookie 下发）；移除 X-Session-Id Header 旁路（会话识别仅认 Cookie）；auth-service 对 register/login 强制匿名 double-submit CSRF、logout 升级为会话绑定校验（失败 403 AUTH_CSRF_FAILED）；/internal/* 新增 X-Internal-Token 共享密钥校验（非空即强制，401 拒绝）；JWT 密钥支持经环境变量持久化注入并在 APP_ENV=production 时对 COOKIE_SECURE=false 启动 fail-fast；前端类型同步、CI smoke 补注册 CSRF 步骤、架构文档 3.3/4.5/4.6 与计划文档更新。
- 涉及模块：auth-service(api/auth, api/internal, config, schemas, tests), frontend/shared/api/auth.ts, docker-compose.yml, .env.docker.example, .github/workflows/ci.yml, docs, plans

## 2026-09-09 - P1/P2 评审修复：基建暴露面收敛、会话绝对上限与审计脱敏
- 落实最后一批评审修复：RabbitMQ 替换 guest/guest 默认凭据并移除 5672/15672 宿主机映射（migrate/api/worker 的 RABBITMQ_URL 统一引用新变量）；Traefik dashboard 8080 端口不再映射宿主机；nginx 仅保留 /health 透传、不再公开后端 docs/redoc/openapi.json；auth-service CORS 默认列表移除 8000/8001 端口项；会话引入 7 天绝对过期上限（超限强制登出语义）；审计事件 payload 中明文 session_id 改为 SHA-256 哈希；X-Forwarded-For 仅在 trust_forwarded_for 开启时解析（默认不信任）；Vite 开发代理补全 /auth 与 /.well-known；补存量标签认领策略与暴力破解双层防护文档。
- 涉及模块：docker-compose.yml, .env.docker.example, backend/.env.example, frontend/vite.config.ts, frontend/nginx.conf, auth-service(config, api/auth, services, tests), docs/architecture, plans

## 2026-09-09 - 本地 Docker Compose 实跑验证（P7 认证闭环 E2E）
- **实跑结果**：全栈经 Traefik Gateway 完成认证闭环实测 10 项场景全绿——公开路由 200、注册/登录 CSRF double-submit 正常、登录响应体无凭据、带 Cookie 访问受保护接口 200（forward-auth 断言注入 + backend JWKS 验签全链打通）、匿名 401、写请求无 CSRF 403、自选股写入正确归属 user_id、api:8000 与 frontend:3000 直连被阻断
- **修复一**：Traefik ≤3.3 与 Docker Engine 29（最低 API 1.44）不兼容导致 Docker provider 协商 v1.24 被拒、路由无法加载——镜像钉至 v3.6.2 并注释勿降级
- **修复二**：forward-auth 生产路径 lifespan 在 state 未挂 http 属性时访问即崩溃（测试注入路径掩盖）——getattr 兜底修复并新增回归测试
- 涉及模块：docker-compose(gateway 镜像版本), forward-auth(app lifespan + tests), plans(实跑记录)

## 2026-09-09 - 前端空数据库白屏修复 + 全局 ErrorBoundary
- **问题**：全新部署（空库）下打开 /market 白屏——ECharts treemap 对内部虚拟节点执行 label 渲染时 `changePercent` 为 undefined，SectorHeatmap formatter 抛 TypeError 导致 React 18 卸载整棵树；且浏览器缓存旧 index.html 使修复不可见
- **修复**：SectorHeatmap label/tooltip formatter 与两张资金流卡片 tooltip 补空值防御；新增全局 `ErrorBoundary`（路由级兜底，任何子树渲染异常降级为错误卡片而非白屏）；nginx SPA 入口增加 `Cache-Control: no-cache`（assets 仍长缓存，入口每次回源，杜绝发版后浏览器跑旧 bundle）
- 涉及模块：frontend/features/market/components(SectorHeatmap/MarketMoneyflowCard/SectorMoneyflowCard), frontend/shared/ui(ErrorBoundary 新增), frontend/App, frontend/nginx.conf

## 2026-09-10 - TradingView 风格明暗双主题基础设施（Stage A）
- 落实 `docs/design/landing-market-theme.md` §1 配色契约：`theme.ts` 重构为 `buildAntdTheme(mode)` 工厂（dark 走 darkAlgorithm）+ `THEME_COLORS` 双模式色板（up/down 红涨绿跌随主题切换）；新建 `app/styles/theme.css` 双轨 CSS 变量（`:root[data-theme]` + 首帧浅色兜底）；新建 `ThemeContext/ThemeProvider`（localStorage `stockbot-theme` > `prefers-color-scheme`，写 `data-theme` 持久化）；新增 `ThemeToggle` 挂 MainLayout Header；MainLayout/SearchBar/UserMenu 壳层硬编码色全部换 CSS 变量；`shared/ui/EChart` 封装内深合并注入 axisLabel/legend/splitLine/textStyle 主题色（调用方显式设置恒优先）；ChangeText/KlineChart/klineOption 全局件改经 `useTheme().colors` 取具体 hex。业务卡片内部细节留给 Stage C。
- 涉及模块：frontend/app(theme 新工厂/theme-context 新增/styles 新增/layouts/MainLayout), frontend/shared/ui(EChart/ChangeText/ThemeToggle 新增/kline), frontend/features/search, frontend/features/auth(UserMenu), frontend/main.tsx, frontend/App

## 2026-09-10 - StockBot 品牌宣传页 Landing（Stage B）
- 落实 `docs/design/landing-market-theme.md` §0-§5：`/` 从重定向 /market 改为公开 lazy Landing 页（独立布局不套 MainLayout，已登录停留此页）；10 区块全量落地——透明吸顶导航（滚动 >24px 加底色+边框，锚点 功能/数据/行业，登录态 CTA）、Hero（「把一个行业，研究透。」+ 信任行 + 纯 CSS accent 渐变装饰）、实时脉搏卡（/market/indices 8 ticker + /market/distribution 涨跌摘要，60s 轮询，失败静默降级占位）、价值三卡、深色产品展示区（纯 CSS+DOM 绘制周期相位条/来源徽章/信号卡，明暗主题恒定）、§5 十行数据覆盖矩阵（统计带 + 免费徽章）、申万行业网格（按个股数 accent 透明度阶梯，失败占位文案）、账号能力三档横条、底部 CTA、页脚（免责声明 + GitHub）。CTA 登录态感知：已登录「进入工作台」→ /market，未登录「免费开始」→ /login（isAuthReady 前隐藏文字防闪现）；颜色全部走 CSS 变量或 useTheme().colors，不引入新依赖。
- 涉及模块：frontend/app/router, frontend/pages/landing(新增 index/landing.css/useLandingCta/sections×11)

## 2026-09-10 - 市场页 TradingView 化重构与全量主题适配（Stage C）
- 落实 `docs/design/landing-market-theme.md` §4/§5：`/market` 重构为四分类 Tab（`data-testid="market-tabs"`：指数总览=全球指数卡+A股核心指数卡 / A股全景=涨跌分布+板块热力图+申万行业网格+热门板块+行业分类 / 资金流向=板块+大盘+北向三卡 / 数据面=龙虎榜等 Tab 表现状迁移），页脚上方新增数据版图精简矩阵；新增 `CoreIndexCards`（六核心指数复用 `GlobalIndexCardView`，与 GlobalMarketBoard 共享 ["global-indices"] 查询缓存）；`GlobalIndexCardView` 按 TV ticker 行规范重排（名称左/价格右对齐/涨跌幅色块带 +/- 号）并接入 useTheme；市场页卡片规范（--bg-panel 底、1px --border、8px 圆角）经 `pages/market/market.css` 落地。
- Stage B 的 DataCoverage 矩阵与 IndustryGrid 提取为共享组件 `features/market/components/DataCoverageMatrix` + `SwIndustryGrid`（CSS co-locate 随组件走，landing.css 移除已迁移规则），landing 两 section 改为薄包装，市场页直接复用。
- Stage A 主题债全量清偿：features/market、features/industry-research、features/stock-detail 全部静态 `COLORS.*` 引用（15 处）与图表 `backgroundColor:"#fff"`/splitLine `#f0f0f0` 硬编码改经 `useTheme().colors` 或 CSS 变量注入（纯函数 buildOption 改收 colors 参数）；市场页五张图从裸 ReactECharts 迁至 `shared/ui/EChart` 封装（默认注入 backgroundColor transparent + 轴/分隔线主题色，新增 onEvents 透传支撑热力图点击）；SectorHeatmap/DistributionChart 渐变端点色改由主题色推导（Dark class 阈值 0.8 保留，近中性块文字用 textPrimary/textSecondary 随主题成立）；e2e marketDataFace 板块资金流/数据面两用例补顶层 Tab 切换。deprecated `COLORS` 导出保留（research-workbench 存量引用另行迁移）。
- 涉及模块：frontend/pages/market(重构+market.css 新增), frontend/pages/landing(sections 两处薄包装+landing.css 瘦身), frontend/features/market(components×10+新增 CoreIndexCards/SwIndustryGrid/DataCoverageMatrix), frontend/features/industry-research(components×5), frontend/features/stock-detail(RelatedEvents), frontend/shared/ui(EChart), frontend/e2e(marketDataFace)

## 2026-09-10 - 宣传页/TV 风格市场页/暗色模式 E2E 自测收尾
- **E2E 新增与修复**：新增 landing.spec.ts（8 用例：公开路由/登录态 CTA 互换/脉搏卡/数据矩阵/行业网格）与 darkmode.spec.ts（3 用例：切换/持久化/暗色无白底）；修复 auth.spec 两处 strict-mode 脆弱选择器（注册表单子串双匹配、Modal 双标题）
- **E2E 环境**：vite dev proxy 改经 Gateway(:80)（端口收敛后 8000/8001 不可达）、dev server 显式绑 127.0.0.1（默认 [::1] 致 Chromium 拒连）
- **全量回归**：36 用例 33 过；余 3 项（research×2/userIsolation×1）为存量数据态依赖（industry metrics 空、固定用户名重复注册非幂等），与本分支无关
- **调试沉淀**：Playwright addInitScript 与 expect 断言器存在状态翻转交互，清 storage 场景应改用「加载后清理+reload」；Mock 数据字段名必须对齐前端类型（MarketIndex 用 value/changePercent/tsCode）
- 涉及模块：frontend/e2e(landing/darkmode 新增, auth 修复), frontend/vite.config

## 2026-09-11 - 宣传页市场脉搏与市场页数据同源化（修复滞后性）
- **问题**：用户实测 landing 首页"实时市场脉搏"与 /market 页指数对不上且滞后——landing 走旧接口 `/api/v1/market/indices`（`market_service.list_market_indices` 读 index_dailies 盘后日线，asof 硬编码 15:00），market 页走 `/api/v1/market/global-indices`（东财实时快照 + 60s 缓存），两条链路口径不同（EOD vs realtime）；landing 文案"每 60 秒自动刷新"轮询的却是盘后库表
- **修复**：
  - 前端 `MarketPulse.tsx` 改用与市场页同源的 `fetchGlobalIndices()`（global-indices 实时链路），Ticker 适配 GlobalIndexCard 字段（price/pctChange），文案改"东财实时快照，与市场页同源"
  - 后端 `GLOBAL_INDICES` 补 5 只 A 股宽基：沪深300/中证500/科创50/上证50/北证50（东财 secid 1.000300/1.000905/1.000688/1.000016/0.899050 全部实测返回实时行情），landing 8 格与市场页 A 股指数完全对齐
  - E2E mock 同步：`**/api/v1/market/indices` → `**/api/v1/market/global-indices`（snake_case 载荷）
  - 单测同步：registry shape / cards 合并断言 9 → 14，新增 A 股 em_secid 集合锁定
- **验证**：tsc ✓、ruff ✓、mapping 18 测试 ✓、gateway 实测 14 条（8 CN 全 realtime）
- 涉及模块：frontend/src/pages/landing/sections/MarketPulse.tsx, frontend/e2e/landing.spec.ts, backend/app/services/market_data_service.py, backend/tests/test_market_data_mapping.py

## 2026-09-11 - 申万 custom-tag overlay 导入修复（"其他"分类 1497→61 只回落）
- **问题**：用户实测宣传页行业网格出现"其他 1497 只"分类。诊断链：tree API 的"其他"= 未命中申万 L3 成分与自定义标签的股票兜底聚合（market_service.get_sw_industry_tree）→ 库里 stock_custom_sw_tags 为 0 行 → 2026-09-03 的 OTHER→SW merge（1439 行）在 P7 重建数据库后从未导入
- **根因**：`import_custom_tags_from_sql` 按 `;` 切分且把以 `--` 开头的块整块跳过——seed 文件是「9 行注释头 + 单条 1439 行 VALUES INSERT（行内无分号，仅末尾一个分号）」，切分后首块恰好 = 注释头+完整 INSERT，以 `--` 开头被整体丢弃，导入恒为 0 行（api 日志 "Imported custom-tag overlay: 0 rows" 安静存在了两天）；seed 文件本身格式正确（40ff00c 入库即无分号），问题纯在解析器
- **修复**：解析重构为 `_split_sql_statements`（先剥离注释行再按分号切分，纯函数可单测）+ 修正 seed 头行分号 + 新增 tests/test_sw_seed_import.py 3 用例锁定畸形输入行为
- **验证**：api 重建后 data_init 自动导入 1439 行；清 `market:sw-tree` 缓存后 tree API "其他"= 61 只（残余为确无申万映射股票，兜底保留），5560 只全可见
- 涉及模块：backend/app/services/sw_industry_service.py, backend/data/sw_custom_tags_seed.sql, backend/tests/test_sw_seed_import.py(新增)

## 2026-09-11 - 代码评审 P1-P4 优化（缺失值语义 / 解析器边界 / 快照键碰撞 / 日志语义）
- **P1 缺失涨跌幅误显示 0.00%**（用户可见）：MarketPulse 的 Ticker 对 `pctChange=null` 用 `?? 0` 兜底，渲染成"0.00% + 中性色"会被读成平盘（EOD 兜底且 spark 不足时真实可达）。改为与价格同口径显示 `--`；先写 E2E 复现（Playwright 明确报 `unexpected value "0.00%"`）再修复，landing.spec 7/7 通过
- **P2 解析器边界**：`_split_sql_statements` 原先只处理注释独占行，行尾注释（`INSERT ...; -- 说明`）会与下条语句粘连并被当语句执行（报语法错）。改为对每个分号块逐行剔除注释（覆盖两种形态），docstring 显式声明"不支持字符串字面量内分号"；单测 5 个（含红→绿复现）
- **P3 快照键跨市场碰撞**：`_em_code` 取数字段短码，沪/深同号段（1.000001 vs 0.000001）会静默错配——单测精确复现（`assert 222.0 == 111.0`，沪市指数拿到深市报价）。client 返回体新增完整 `secid`（f13 市场号 + f12 代码），service 改按 secid 索引并删除 `_em_code`
- **P4 导入日志语义含混**：`Imported ... N rows` 实为全表计数（排障时被误读为"本次导入量"）。改为 `table now has N rows (+M this run)`，实机验证输出 `1439 rows (+0 this run)`（幂等重放）
- **验证**：后端 ruff/mypy ✓、单测 180 通过 ✓、landing E2E 7/7 ✓、实机重建 api+frontend 后 global-indices 14 条全 realtime 无回归 ✓
- 涉及模块：frontend/src/pages/landing/sections/MarketPulse.tsx, frontend/e2e/landing.spec.ts, backend/app/services/sw_industry_service.py, backend/app/services/market_data_service.py, backend/app/core/providers/eastmoney_client.py, backend/tests/{test_sw_seed_import,test_market_data_mapping,test_eastmoney_client}.py
## 2026-09-09 - 浏览器登录会话安全研究
- 基于 RFC 9700、RFC 10017（OAuth 2.0 for Browser-Based Applications）、OpenID Connect Core 与 OWASP Session/CSRF Cheat Sheet，形成 stock_bot 的 BFF+HttpOnly 会话、刷新令牌轮换、CSRF 与 Cookie flags 建议。
- 涉及模块：安全架构、backend、frontend、Docker Compose、docs

- **根因**：根 README.md 长期与实现漂移（仍写 SQLModel / Tailwind+shadcn，实际已迁至 SQLAlchemy 2.0 async + Ant Design 5），且只字未提产品化方向"行业投研工作台"；Docker 部署细节散落且未链接到 build.md，双份 README（中/英）重复漂移
- **修复**：
  - 重写根 README.md 为中文唯一权威文档：技术栈/项目结构/API 路由全面对齐当前实现，补充 Docker 部署核心步骤（`cp .env.docker.example backend/.env`、填 TUSHARE_TOKEN、前端预构建说明）并链接 docs/build.md
  - 收敛双文档：删除 README_zh.md（无任何文件引用）
  - 补强根 AGENTS.md：新增"部署约定"章节（.env 生成、前端 runtime 预构建、交叉引用一致性规则），关键文档列入 build.md / ARCHITECTURE.md / 投研工作台计划
  - 校正部署文档与 docker-compose 不一致处：docs/build.md 服务数 7→9（补 scheduler / redis-init）、redis 端口 6379→6380、启动顺序加 scheduler；docs/ARCHITECTURE.md 服务表补 scheduler / redis-init
- 涉及模块：README.md, README_zh.md(删除), AGENTS.md, docs/build.md, docs/ARCHITECTURE.md

## 2026-09-09 - best-practices 重构（按主题归档 + 去重）
- 将 112 条扁平沉淀重组为 8 类主题分组并加目录：数据源与采集 / 数据库与性能 / Docker 与部署 / 前端 / 测试与E2E / 架构与分层 / 指标建模与规则引擎 / 工程流程与文档
- 合并确认重复条目：时区/交易日守卫（UTC vs Asia/Shanghai）两处并一、同表多频指标 freq 仲裁与冲突键两条并一、uv `--extra dev` 冗余提及清理；条目 112→97
- 保留 `best-practice.md` / `best-practices.md` 两文件合并历史说明；无有效经验丢失（已用关键锚点校验）
- 涉及模块：docs/references/best-practices.md

## 2026-09-09 - 完成前自检门禁（强档：约定 + 脚本 + pre-commit/CI 兜底）
- **问题**：best-practices 只是"被动检索"，agent 改完代码不会有机制逼它拿已知错误对照本次 diff；规范类错误有 CI 兜底，业务类（时区/source 优先级/N+1/dockerignore）无兜底
- **修复（四层强档）**：
  - AGENTS.md 新增「完成前自检门禁」章节：每次收尾强制 6 步（列改动 → 探测器对照 → 跑 self_review → 文档同步 → 反馈闭环 → 固定格式收尾结论）
  - best-practices.md 新增「自检探测器映射表」：8 分类 → 关键词，把"对照已知错误"变成可 grep 的机械操作
  - 新增 `scripts/self_review.sh`：快检（空白/冲突 + 改动文件 ruff + 文档同步告警）+ `--full`（追加 mypy/pytest/tsc）
  - 接入 `.pre-commit-config.yaml` 本地钩子（每次 commit 强制跑 self_review）
  - CI 新增 `docs-consistency` job（信息性不阻断）：PR/提交 diff 空白硬检 + 文档未 touch Changelog 告警
- 涉及模块：AGENTS, docs/references/best-practices, scripts/self_review.sh(新增), .pre-commit-config.yaml, .github/workflows/ci.yml

## 2026-09-09 - pre-commit 扩展：每次 commit 自动跑测试 + 前端类型检查
- **需求**：每次 commit 前自动跑 test / 格式 / benchmark
- **现状核实**：后端格式(ruff/ruff-format)+类型(mypy) 原本已在 pre-commit；非 e2e 测试 155 个可无 DB 独立运行(~2.4s)；**前端实际未配置 eslint/prettier**（package.json 无常量依赖亦无 eslint.config，CI 的 "Lint(frontend)" job 实为 `tsc --noEmit`）；仓库无 benchmark 基建
- **修复**：
  - pre-commit 新增 `backend-test` 钩子：任何 backend .py 变更即跑 `uv run pytest -m "not e2e" --no-cov`（e2e 仍需 DB，留在 CI）
  - pre-commit 新增 `frontend-typecheck` 钩子：任何 frontend ts/tsx 变更即跑 `npx tsc --noEmit`（与 CI 一致的既有前端静态校验）
- 涉及模块：.pre-commit-config.yaml, docs/Changelog.md

## 2026-09-09 - Benchmark Tier 1 落地（Phase A：纯计算域硬门禁）
- **需求**：按 [plans/2026-09-09-benchmark-tiers.md](../plans/2026-09-09-benchmark-tiers.md) 落地 CPU 基准硬门禁，让纯计算热点回归能被 PR 自动卡住
- **实现**：
  - 新增 `backend/tests/benchmarks/` 三个基准：规则引擎周期判定（`evaluate_pig_cycle` 批量 400 样本）、指标 rollup 与源/频率仲裁（`_rollup_monthly_rows` 730 行日频 / `_pick_latest` 冲突裁决）、行情归一化 mapper（`map_daily_rows` 5000 行）；全部无 DB 纯函数，固定 seed=20260909 + 尺寸常量即基线契约
  - `pytest-benchmark` 入 dev extra（5.3.0；**有意放 dev 而非独立 bench extra**：pytest 收集 tests/benchmarks/ 需要包在场，避免为一个小包拆两套 CI sync；文件内 `pytest.importorskip` 兜底）；pyproject 注册 `bench` marker，addopts 默认 `-m 'not e2e and not bench'`
  - marker 泄漏修正：pre-commit `backend-test`、CI `backend-test`、`self_review.sh --full` 三处 `-m "not e2e"` → `"not e2e and not bench"`
  - `scripts/bench.sh`（gate / `--save-baseline` / `--quick` 三模式，`--benchmark-warmup=on` 抗冷启动）+ `scripts/bench_compare.py`（按 median 相对退化判定，阈值默认 12%，新增基准无基线也红）；`benchmarks/baseline.json` 入库、`last.json` 进 .gitignore
  - CI 新增 `bench-cpu` 硬门禁 job（uv sync --frozen --extra dev → bench.sh，产物 artifact 7 天）；AGENTS/AGENTS(backend) 常用命令与自检门禁第 3 步引用基准脚本
- **验证**：4 基准 ~5s 跑完；同机三次 gate 全绿（波动 -1.2%~+8.1% < 12%）；临时收紧阈值验证门禁可红（exit 1 + 明细）；大样本基准（13ms/次）抖动 7.5% → 缩到 5000 行后 1.2%
- 涉及模块：backend/tests/benchmarks(新增), backend/pyproject.toml, backend/uv.lock, scripts/bench.sh(新增), scripts/bench_compare.py(新增), benchmarks/baseline.json(新增), .github/workflows/ci.yml, .pre-commit-config.yaml, scripts/self_review.sh, AGENTS.md, backend/AGENTS.md, .gitignore

## 2026-09-09 - bench-cpu CI 门禁修复：同 runner A/B 基线（跨机器基线不可比）
- **问题**：PR#4 的 bench-cpu 连续两次失败——第一次 exit 126（Windows 创建的 bench_compare.py 无执行位，Linux 直接执行 Permission denied）；修复执行方式后第二次仍红，4 项基准全部"退化"37-49%，根因是 **wall-clock 基线绑定硬件**：本机 Windows 跑的 baseline.json 对 CI runner 无参照意义，跨机器相对对比必假红
- **修复**：
  - `scripts/bench_compare.py` 加 `--allow-added`（新增基准不判失败——A/B 的 base 侧本就没有新基准）
  - `scripts/bench.sh`：`--allow-added` 透传；`--benchmark-json` 接 `$BENCH_CURRENT`（原硬编码漏改）；base commit 无 `tests/benchmarks/` 时短路写空产物；产物路径支持 `BENCH_BASELINE/BENCH_CURRENT` 环境变量覆盖（CI 指到 `$RUNNER_TEMP`，避免污染 tracked 的 baseline.json 导致 `git checkout` 拒切）
  - CI `bench-cpu` 重写为**同 runner A/B**：resolve base（PR base.sha → event.before → HEAD~1 兜底）→ A 侧 checkout base + `uv sync` + `--save-baseline`（临时路径）→ B 侧 checkout head + sync + gate；入库 `benchmarks/baseline.json` 降级为本地开发参考
- **验证**：本地 T1 save 路径覆盖 / T2 正常 A/B（退化 -1.2% 内）/ T3 空基线全新增放行 全绿；CI 待推送后观察
- 涉及模块：scripts/bench.sh, scripts/bench_compare.py, .github/workflows/ci.yml, plans/2026-09-09-benchmark-tiers.md(决策记录), docs/references/best-practices.md, docs/Changelog.md

## 2026-09-09 - bench-cpu A/B 修复二轮：base 改 origin/main + 空产物兜底
- **问题**：A/B 首跑 127——PR 的 `base.sha` 是 PR 创建时点的 main（162f576，早于 PR#3 合并），checkout 后连 scripts/bench.sh 都不存在；且 base 的 uv.lock 无 pytest-benchmark 时 importorskip 全 skip，pytest 不产出 JSON 会让 save 的 cp 失败
- **修复**：base 语义改为 `git rev-parse origin/main`（合并目标最新 head，脚本/依赖齐全，语义也更正确——"PR 合并后 main 性能不得退化"）；bench.sh 在 pytest 无产物输出时补写空 JSON，save/gate 流程不中断
- 涉及模块：.github/workflows/ci.yml, scripts/bench.sh, docs/Changelog.md

## 2026-09-09 - bench-cpu A/B 修复三轮：A 侧从 head 取回 bench 脚本
- **问题**：bench.sh 本身随本 PR 新增，origin/main 上没有——A 侧 checkout base 后 `bash ../scripts/bench.sh` 报 127（前一轮同症状但根因不同：上一轮是 base.sha 太老，这一轮是脚本未入库）
- **修复**：A 侧 checkout base 后 `git checkout head -- scripts/bench.sh scripts/bench_compare.py` 借回脚本再跑（当前 PR：base 无 tests/benchmarks → 短路空基线 → B 侧全新增放行；未来 PR：A 侧真跑基线）；同时把 git pathspec 操作移回仓库根（pathspec 相对 cwd，backend/ 子目录会解析成 backend/scripts/）
- 涉及模块：.github/workflows/ci.yml, docs/Changelog.md
