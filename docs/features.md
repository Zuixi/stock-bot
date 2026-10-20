# 功能清单

> 本表是**可执行契约**：每条都必须带"代码入口"，`scripts/doc_gate.sh` 会校验页面目录存在且被路由引用、API 前缀真实存在。删功能不同步删本表 ⇒ 门禁失败。
>
> 入口写法约定：页面 `frontend/src/pages/<dir>`；接口 `/api/v1/<prefix>`；业务域 `frontend/src/features/<domain>`。

## 行情与市场

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 宣传首页 | 公开独立布局，登录 CTA | `frontend/src/pages/landing` |
| 行情台首页 | 指数卡、涨跌分布、榜单、板块、资金流、热门板块（免登录可用） | `frontend/src/pages/market` · `frontend/src/features/market` · `/api/v1/market` |
| 指数详情 | 指数 K 线、SSE 指数快照与盘中序列（路由 `/market/index/:tsCode`，由 `/index/:tsCode` redirect） | `frontend/src/pages/market-index` · `/api/v1/market` |
| 申万行业浏览 | 申万一级/二级/三级树、下钻、行业表现；**末级成分股页**（`/market/industry/:level1Code/:level2Code/:level3Code`）带 7 项行情字段（K 线中位数/涨跌幅/换手率/成交量/量比/振幅/成交额）与 **SCD2 归属区间**（`effective_from`/`effective_to`，可按历史交易日还原成分） | `frontend/src/pages/market-category` · `frontend/src/pages/market-industry-level2` · `frontend/src/pages/market-industry-level3` · `frontend/src/pages/market-industry-level4` · `/api/v1/market` |
| 概念板块 | 板块列表、详情、成分股（成分以 `symbol` 为业务键）；`/market/concept/:boardCode` **保留**（Ruling 30） | `frontend/src/pages/market-concept` · `frontend/src/features/concept` · `/api/v1/concepts` |
| 热门板块与情绪 | 涨停梯队、板块涨停、昨日涨停、情绪日历/盘中；`/market/hot-sectors/:category` **保留**（Ruling 30） | `frontend/src/pages/market-hot-sectors` · `frontend/src/shared/api/limitUp.ts` · `/api/v1/market` |
| 监管异动雷达 | 交易所「异常波动」价格口径（**读时计算、零新表**，120s 缓存）：连续 3 个交易日收盘价涨跌幅偏离值累计 ≥ 板块阈值（主板 20% / 主板 ST 12% / 创业板·科创板 30% / 北交所 40%）→ 已达线 / 临近（≥80% 阈值）/ 观察；列出今日涨停与连板梯队、`10 日 N 次` 与 `10/30 日累计` 严重异常命中、明日触发价（最近 2 日 + 明日，假设基准持平）；近 10 个交易日按「首次越过阈值日」回看；阈值/基准（上证综指/深证成指/创业板指/科创50/北证50）/失效场景由 payload 下发；**价格口径 + `turnover_only[]` 换手率旁路**（仅列价格未达线、3 日均换手 ≥ 前 5 日 30 倍且 3 日累计 ≥20% 的票；`daily_basic` 未更新时 `turnover_available=false`）；`?date=` 限最近 **250 个交易日**（越界 400），页面顶部日期选择器可切历史日（不轮询）。路由 `/market/regulatory` | `frontend/src/pages/market-regulatory` · `frontend/src/shared/api/regulatory.ts` · `/api/v1/market/regulatory-anomalies` · `backend/app/services/regulatory_anomaly.py`（纯计算） / `regulatory_anomaly_service.py` |
| 市场热力图 | 板块/概念热力图（Task 5 建占位路由 `/market/heatmap`，真实现归 S1.5；**不是任何 redirect 的目标**） | `frontend/src/pages/market-heatmap` · `/api/v1/market` |
| 市场面数据 | 北向、龙虎榜、大宗交易、解禁、回购、公告、板块与市场资金流、全球指数 | `/api/v1/market`（`market_data.py`）· `frontend/src/shared/api/marketData.ts` |
| 数据新鲜度 | 各表最新业务时间、缺口、上游状态（数据面健康度权威入口）；另附**只读巡检域覆盖率** `coverage`（逐域 `have_days` / `complete_days` / `cover_pct` / `missing_days`；分母 = **交易日**，取 `daily_quotes` ∪ `stock_price_limits` 并集、窗口末端锚定日历末日、测量窗为最近 30 自然日；`complete_days` 只认 `capture_kind='eod_full'` 的完整日，主源缺日被并集补上时 `degraded_trading_calendar=true`；缺失渲染 `--`）；2026-10-16 追加 `market_metrics`：派生指标（§S1.7–§S1.13）四条腿各自的落库点 / 落后交易日数 / 是否喂综合指数（这一族此前只由手工回补脚本写，正是「页面数据停更而行情在更新」的暴露面） | `/api/v1/market/data-freshness` |
| 情绪趋势与历史分位 | 读时计算（**不物化**，不新建表/队列/Job）：最近 `days`（默认 250，上限 500）个交易日情绪序列 + **仅 3 个**指标（`zt_count`/`broken_rate`/`promo_1to2`）的历史分位，**只用有效样本**（该字段当日 NULL 不进该字段样本、绝不计 0）；有效样本 <60 → `insufficient_history=true` 且 `percentile=null`（不给假分位）；无插值，端点值 p0/p100 可达 | `/api/v1/market/sentiment/trends` |
| 情绪指标历史追踪 | 温度计 **9 张卡各自可回放**：`GET /sentiment/metrics/{metric_key}` 读时计算（不物化、无新 Job）——序列 + 窗口统计（min/pXX/median/mean/std）+ 等宽 10 箱分布 + 连续同向天数 + `percentile_rank`（有效样本 <60 不给）+ 9 指标切换值；**小样本纪律**：样本型指标 `n<5` 的日子标 `low_sample`，原始值仍展示（空心点）但不进统计/分位/分布，比值类 `n≥5` 给 Wilson 95% 区间；**周期阶段**（PIT 250 日、规则见 [ADR 0022](./decisions/0022-sentiment-regime-and-sample-gate.md)）作为图背景带。详情页 `/market/sentiment/:metric` 支持 `?days=20|60|120|250`（默认 250），ECharts 画序列（P25/P50/P75 参考线）+ 分布 + 近 20 日折线（红/绿点=较前一日，逐日明细可折叠）；同页切换指标不重挂、不丢滚动位置。温度计卡片在**收盘口径**下可点（盘中不落库 ⇒ 不可点）。日表补 `yzt_avg_open_premium`/`yzt_n` 两列，历史行由只补 NULL 的脚本回填 | `/api/v1/market` · `frontend/src/pages/market-sentiment-metric` · `frontend/src/features/sentiment` |
| 情绪脉搏 | 温度计之前的**仪表带**（不是卡片）：左侧当前阶段 + 三个输入分位，右侧 4 张 small multiples（涨停家数/炸板率/最高板/1进2，共享时间轴、`echarts.connect` hover 联动），底部「情绪 vs 创业板指」同分位轴对比 + 连续背离段。`GET /sentiment/pulse?days=60&benchmark=399006.SZ` 读时计算（Redis 900s）：9 指标序列 + 阶段区段（PIT 250 日、持续 2 天平滑）+ 指数 5 日收益分位 + 背离；`20D/60D/120D/250D` 与基准写进 URL（`?pulse=60&bench=...`）。本地未落中证1000，基准仅创业板指/上证 | `/api/v1/market` · `frontend/src/features/market/components/SentimentPulse.tsx` · `frontend/src/features/sentiment/regimeStyle.ts` |
| A股全景 · 市场结构脉搏 | 五张今日卡升级为可回看结构面板：`GET /market/panorama?days=20|60|120|250` 读三张日表（广度/11 桶/申万一级），一次请求返回广度序列、最新 11 桶、31 行业 + 排名变化、结构四态（普涨/结构/普跌/抱团，广度+Top3 集中度+中位涨跌幅）、资金流 Top 与 `available_from`；前端仪表带 = 广度×集中度罗盘 + 4 个带 5 日 sparkline 的 KPI，不轮询、`keepPreviousData` 切范围不跳。涨跌分布加"较昨日/5 日上涨占比"；行业网格加今日涨跌角标；热力图可切今日/昨日（`GET /market/sectors?date=`）；资金流只认 `eod_full`（旧的是完整日，新的是盘中子集，不再混用）。回补脚本 `backend/scripts/backfill_market_panorama.py`（244 天/2,684 桶/7,564 行业行）+ 日终 job `market_panorama_daily`（Mon-Fri 17:25，纯本地派生、幂等、迟到补跑）；实测冷请求 20D 19ms/60D 21ms/120D 24ms/250D 31ms，缓存 4–6ms | `/api/v1/market/panorama` · `backend/app/services/market_panorama_service.py` · `backend/app/repositories/panorama_repo.py` · `frontend/src/features/market/components/MarketPanoramaPulse.tsx` · `frontend/src/shared/api/panorama.ts` |
| 市场情绪综合指数（hero） | S1.13 合成指数：**只展示「各腿齐备到 T-1」的值**——读面取最新一个生效权重之和 ≥ `0.80`（发布闸门）的行；薄日**不发布值**（`value=null` + `missing_reason='below_publish_weight'`，参与集/逐腿分位/生效权重仍在 `extra`）；分位 = 尾部 1260 交易日有效样本的 nearest-rank（无插值，样本 <60 不给假分位）；新增 `expected_as_of` / `lag_trading_days` / `stale`（三态，`null` = 判不出，**不得**读成"新鲜"）；再加 `latest_withheld`（当日有行但没过闸门时的腿数/权重/缺腿与逐腿原因）——文案据此说"当日为什么不是它"，**不得**把日历期望日写成"已齐备日"（2026-10-19）。数据由**对账后置收敛器**日更（§S1.7–§S1.13，见 [ADR 0016](./decisions/0016-derived-metrics-converge.md)） | `/api/v1/market/sentiment/composite` · `frontend/src/pages/sentiment` · `frontend/src/features/sentiment` |
| 市场状态机 | 五态（冰点/修复/亢奋/退潮/中性）+ **3 日迟滞**（防抖动）；特征只用全历史可算的 5 项（成交额分位、涨跌家数比、截面中位涨跌幅、60 日新高占比、20 日实现波动）；离线产物给**转移矩阵**与**同态后续 5/20 日分布**（中位/最差 5%/样本数，`n<30` 只给 n）；读数一律与「中性」态同期对比（样本期漂移已在 `notes` 明示）；`/me?tab=triage` 顶部一行，接口失败整行不渲染 | `/api/v1/market/regime` · `backend/app/services/market_regime_service.py` · `frontend/src/features/triage/components/RegimeLine.tsx` |
| 板块趋势 | 读时计算（**不物化**）：窗口 = 以该维度快照最近日为右端、向前恰好 `days` 个交易日（`window.days`=请求值 / `observed_days`=实际跨度 / `samples`=完整日数，三者并排使天数不符不可能悄悄发生）；连续上榜天数（按当日 `main_net_inflow` Top-N，`top_n` 默认 20、tiebreak `board_code ASC`）、连续净流入/流出天数、龙头更替次数、板块涨停家数序列；**完整日闸门** = 只统计当日有 `capture_kind='eod_full'` 行的日子（`poll`-only 日进 `missing_days`，**绝不**当 0 流入）；`available_from`（该维度最早完整日，可为 null）与 `attribution` 必填——`industry`/`region` = `current_sw_snapshot`（**按当前分类回溯，非当时成分**），`concept` = `point_in_time`（成分按 `concept_member_changes` 反推）；该维度一行都没有时早退（`observed_days=0`、`missing_days=[]`，不查行情表） | `/api/v1/market/sector-trends` |

## 个股

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 个股详情 | 日线 K 线（复权开关 · 十字光标跨价格/成交量 pane 联动 · 成交量刻度 · 数据条随光标联动 · 公告/龙虎榜/大宗/解禁/回购**事件标注**与点击定位，仅日K定位）；报价条（八项行情 + 距 52 周高/低 + PE(TTM) 3 年分位）；成长与盈利（读 financial-summary，与财务 tab 共用缓存）；股性事实行（60 日收益/波动/回撤/趋势 R²/均线多头/量能，样本不足整行不渲染）；公司概况（全称/上市日期/地区/实控人，NULL 不渲染）；「记一笔」直达决策日志（公开路由下未登录给登录入口）；相关数据五类**失败态与空态分离**（失败给重试，不冒充「无记录」）；各区块独立错误边界 + 固定高度占位（加载不跳动） | `frontend/src/pages/stock-detail` · `frontend/src/features/stock-detail` · `/api/v1/exchanges`（`/{exchange}/stocks/{symbol}/quotes`·`/financial-summary`·`/financial-statements`·`/valuation-history`）· `/api/v1/market`（`/dragon-tiger`·`/announcements` 等，均支持 `symbol`）· `/api/v1/journal` |
| 个股搜索 | 代码/名称/拼音搜索 | `frontend/src/features/search` · `/api/v1/exchanges` |
| 股票名录与分类 | 交易所名录、类别、enriched 列表（含行业与估值字段） | `/api/v1/exchanges` |
| 次新股 | 次新股列表与追踪 | `frontend/src/shared/api/market.ts` · `/api/v1/new-stocks` |

## 用户功能

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 自选股 | 用户自选列表 + 标题/清空（数据归属按 `user_id` 隔离，需登录）；路由 `/me?tab=watchlist`（`/watchlist` redirect 到此）。**2026-10-19 修**：未登录（或登录态未确定）时加的自选会在登录后**自动合并到账户**；某只票行情取不到时**仍留行**（`行情未取到` + 可直接移除），不再静默丢行；行内移除按钮有可访问名称，今日体检卡片也提供「从自选移除」 | `frontend/src/features/watchlist`（`WatchlistPanel` / `WatchlistTable`） · `/api/v1/watchlists` |
| 决策日志与归因 | 用户 30 秒记录一次判断（thesis / invalidation / horizon 三必填），创建时自动落市场快照（价格、MA20 距离、情绪分位、板块状态、当日事件、估值位置）；到期自动算 outcome/bench/excess 与是否触及失效条件，用户从五类归因里选一个，长期统计「最常犯的错」。创建后不可改、一次只可复盘一次；`reviewed_n<5` 不显示占比。**入口直达**：体检卡片「记一笔」带入标的（`/me?tab=journal&symbol=&from=triage`）；`/me` 两个 Tab 带待办角标；产物在 reconcile 尾步**按天/按版本自愈**。路由 `/me?tab=journal` | `frontend/src/features/journal`（`JournalPanel` / `EntryForm` / `ReviewModal`） · `/api/v1/journal` |
| 自定义标签 | 标签分组总览与按标签看股票；路由 `/me?tab=tags`（`/tags` redirect 到此）、`/tags/:tagName`（**redirect** 到 `/me?tab=tags&name=`） | `frontend/src/features/watchlist`（`TagsOverview` / `TagsPanel`） · `/api/v1/tags` |
| 今日体检 | 自选的事实清单（10 个事实型探测器：跌破 20 日线 / 跌得急 / 放量异动 / 涨停与炸板 / 新公告 / 龙虎榜 / 创 60 日新高新低 / 所属板块在动 / 30 天内解禁 / **监管异动（价格 3 日偏离 + 换手率旁路）**），分级只有 风险 / 事件 / 关注 / 背景；`as_of` = 最近完整交易日；缺数据的检查逐项留痕（`unavailable` 是**人话** label，机器 key 只在 `unavailable_keys`，不上屏）；四态显式（`state`：有命中 / 全静默 / 无数据日 / 无自选），无自选给「添加自选」入口；路由 `/me?tab=triage`（**默认 Tab**；独立 `/today` 推迟，见 S8.1） | `frontend/src/features/triage`（`TriagePanel`） · `/api/v1/watchlists/triage` · `backend/app/services/triage_detectors.py`（10 探测器） / `triage_service.py`（装载+组合） |
| 账号设置 | 已登录用户自助修改自己的用户名 / 邮箱 / 显示名（唯一性校验 + 审计） | `frontend/src/features/auth/components/ProfileModal.tsx` · `PATCH /auth/profile` |
| 登录 / 会话 / 改密 | 独立认证库、HttpOnly Cookie、CSRF 双防御；**邀请制**（自助注册已关闭，[ADR 0015](./decisions/0015-invite-only-provisioning-and-admin-console.md)） | `frontend/src/pages/login` · `frontend/src/pages/change-password` · `frontend/src/features/auth` · auth-service `/auth` · gateway `forward-auth` |
| 用户管理（admin） | 开户（角色必选）· 停用/启用 · 角色替换 · 重置密码 · 审计检索；一次性临时密码 + 强制首登改密 | `frontend/src/pages/admin-users` · `frontend/src/features/admin` · auth-service `/auth/admin/*`（需 `users:manage`） |

## 行业投研

> **本板块需 `research:read` 权限才可读**（[ADR 0008](./decisions/0008-research-read-requires-permission.md)，仅 researcher / analyst / admin）：
> `/industry*` 路由（由 `/research*` redirect 而来，redirect 不放宽权限）、`/api/v1/industries*` 与
> **统一指标读面 `/api/v1/metrics*`**（router 级依赖，[backend/app/api/v1/metrics.py](../backend/app/api/v1/metrics.py)）同样受限（匿名 401、无权限 403）；
> 公开行情（行情台首页 / 个股 / 指数）不受影响。

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 行业列表 | 已产品化行业（当前：生猪养殖"猪智投"）；路由前缀迁移到 `/industry`（由 `/research` redirect） | `frontend/src/pages/industry` · `/api/v1/industries` |
| 投研工作台 | 四 Tab：投资看板 / 行业知识库 / 行情调研追踪 / 交易管理（路由 `/industry/:industryKey`，由 `/research/:industryKey` redirect） | `frontend/src/pages/industry/workbench` · `frontend/src/features/industry-research` · `/api/v1/industries` |
| 指标注册表 | `metric_key → {名称,单位,频率,源优先级,层级,参考区间,口径版本}` 由后端下发驱动前端渲染；未登记 `metric_key` 一律拒绝（404） | `backend/app/services/`（industry registry）· `/api/v1/industries` · `/api/v1/metrics` |
| 统一指标读面 | 行业/情绪/宏观/策略因子共用 `industry_metrics` 单表，`scope_key`（DB 列名仍为 `industry_key`）区分 scope；每指标最新值 + 窗口内序列，均带 `source / as_of / freq / caliber_version` 四徽章，其中 `as_of_source` 标注 PIT 实际可得日是否回退（`published_at` \| `period`）；**缺数可见**：`value=null` 的点另带 `missing_reason`（缺数原因码）与 `coverage`（该行覆盖率，取 `extra` 里 `coverage`/`weight_coverage`/`member_coverage` 之首），前端据此区分「源侧滞后」与「真缺数」（Ruling 61） | `/api/v1/metrics/latest` · `/api/v1/metrics/series` · `frontend/src/features/industry-research/components/MetricBadge.tsx` |
| 规则引擎与信号 | 周期阶段判定、参考区间随政策生效日切换、信号可回测可审计 | `/api/v1/industries` · `backend/tests/benchmarks/test_rule_engine_bench.py` |

## 数据管道与运维

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 异步任务与手动回补 | 队列触发抓取/计算，任务状态可查 | `/api/v1/tasks` |
| 定时采集 | APScheduler 增量 + 对账式自愈（宿主睡眠/容器重启后自动收敛） | `backend/app/scheduler/` |
| 采集 Worker | RabbitMQ 消费者，与定时任务共用同一 ingest 方法 | `backend/app/workers/` · `backend/app/core/mq.py` |
| 原始响应备份 | 每次上游调用以 JSONL 原子写入 `data/`（对账兜底依据） | `backend/app/services/data_saver.py` |
| 聚类分析 | 股票行为相似性聚类与解释（**前端无入口，API 保留**） | `/api/v1/clusters` |

## 维护说明

- 新增功能：在本表加一行 + 在 `frontend/src/app/router/index.tsx` 挂路由（若有页面）
- 下线功能：删代码**同时**删除本表对应行，否则文档门禁失败
- 本表与路由/接口不一致时，以代码为准并当轮修正本表
