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
| 市场热力图 | 板块/概念热力图（Task 5 建占位路由 `/market/heatmap`，真实现归 S1.5；**不是任何 redirect 的目标**） | `frontend/src/pages/market-heatmap` · `/api/v1/market` |
| 市场面数据 | 北向、龙虎榜、大宗交易、解禁、回购、公告、板块与市场资金流、全球指数 | `/api/v1/market`（`market_data.py`）· `frontend/src/shared/api/marketData.ts` |
| 数据新鲜度 | 各表最新业务时间、缺口、上游状态（数据面健康度权威入口）；另附**只读巡检域覆盖率** `coverage`（逐域 `have_days` / `complete_days` / `cover_pct` / `missing_days`；分母 = **交易日**，取 `daily_quotes` ∪ `stock_price_limits` 并集、窗口末端锚定日历末日、测量窗为最近 30 自然日；`complete_days` 只认 `capture_kind='eod_full'` 的完整日，主源缺日被并集补上时 `degraded_trading_calendar=true`；缺失渲染 `--`）；2026-10-16 追加 `market_metrics`：派生指标（§S1.7–§S1.13）四条腿各自的落库点 / 落后交易日数 / 是否喂综合指数（这一族此前只由手工回补脚本写，正是「页面数据停更而行情在更新」的暴露面） | `/api/v1/market/data-freshness` |
| 情绪趋势与历史分位 | 读时计算（**不物化**，不新建表/队列/Job）：最近 `days`（默认 250，上限 500）个交易日情绪序列 + **仅 3 个**指标（`zt_count`/`broken_rate`/`promo_1to2`）的历史分位，**只用有效样本**（该字段当日 NULL 不进该字段样本、绝不计 0）；有效样本 <60 → `insufficient_history=true` 且 `percentile=null`（不给假分位）；无插值，端点值 p0/p100 可达 | `/api/v1/market/sentiment/trends` |
| 市场情绪综合指数（hero） | S1.13 合成指数：**只展示「各腿齐备到 T-1」的值**——读面取最新一个生效权重之和 ≥ `0.80`（发布闸门）的行；薄日**不发布值**（`value=null` + `missing_reason='below_publish_weight'`，参与集/逐腿分位/生效权重仍在 `extra`）；分位 = 尾部 1260 交易日有效样本的 nearest-rank（无插值，样本 <60 不给假分位）；新增 `expected_as_of` / `lag_trading_days` / `stale`（三态，`null` = 判不出，**不得**读成"新鲜"）。数据由**对账后置收敛器**日更（§S1.7–§S1.13，见 [ADR 0016](./decisions/0016-derived-metrics-converge.md)） | `/api/v1/market/sentiment/composite` · `frontend/src/pages/sentiment` · `frontend/src/features/sentiment` |
| 板块趋势 | 读时计算（**不物化**）：窗口 = 以该维度快照最近日为右端、向前恰好 `days` 个交易日（`window.days`=请求值 / `observed_days`=实际跨度 / `samples`=完整日数，三者并排使天数不符不可能悄悄发生）；连续上榜天数（按当日 `main_net_inflow` Top-N，`top_n` 默认 20、tiebreak `board_code ASC`）、连续净流入/流出天数、龙头更替次数、板块涨停家数序列；**完整日闸门** = 只统计当日有 `capture_kind='eod_full'` 行的日子（`poll`-only 日进 `missing_days`，**绝不**当 0 流入）；`available_from`（该维度最早完整日，可为 null）与 `attribution` 必填——`industry`/`region` = `current_sw_snapshot`（**按当前分类回溯，非当时成分**），`concept` = `point_in_time`（成分按 `concept_member_changes` 反推）；该维度一行都没有时早退（`observed_days=0`、`missing_days=[]`，不查行情表） | `/api/v1/market/sector-trends` |

## 个股

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 个股详情 | 日线 K 线（含复权开关）、最新行情、资金流、财务摘要与估值历史 | `frontend/src/pages/stock-detail` · `frontend/src/features/stock-detail` · `/api/v1/exchanges`（`/{exchange}/stocks/{symbol}/quotes`·`/features`·`/financial-*`） |
| 个股搜索 | 代码/名称/拼音搜索 | `frontend/src/features/search` · `/api/v1/exchanges` |
| 股票名录与分类 | 交易所名录、类别、enriched 列表（含行业与估值字段） | `/api/v1/exchanges` |
| 次新股 | 次新股列表与追踪 | `frontend/src/shared/api/market.ts` · `/api/v1/new-stocks` |

## 用户功能

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 自选股 | 用户自选列表 + 标题/清空（数据归属按 `user_id` 隔离，需登录）；路由 `/me?tab=watchlist`（`/watchlist` redirect 到此） | `frontend/src/features/watchlist`（`WatchlistPanel` / `WatchlistTable`） · `/api/v1/watchlists` |
| 自定义标签 | 标签分组总览与按标签看股票；路由 `/me?tab=tags`（`/tags` redirect 到此）、`/tags/:tagName`（**redirect** 到 `/me?tab=tags&name=`） | `frontend/src/features/watchlist`（`TagsOverview` / `TagsPanel`） · `/api/v1/tags` |
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
