# 功能清单

> 本表是**可执行契约**：每条都必须带"代码入口"，`scripts/doc_gate.sh` 会校验页面目录存在且被路由引用、API 前缀真实存在。删功能不同步删本表 ⇒ 门禁失败。
>
> 入口写法约定：页面 `frontend/src/pages/<dir>`；接口 `/api/v1/<prefix>`；业务域 `frontend/src/features/<domain>`。

## 行情与市场

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 宣传首页 | 公开独立布局，登录 CTA | `frontend/src/pages/landing` |
| 行情台首页 | 指数卡、涨跌分布、榜单、板块、资金流、热门板块（免登录可用） | `frontend/src/pages/market` · `frontend/src/features/market` · `/api/v1/market` |
| 指数详情 | 指数 K 线、SSE 指数快照与盘中序列 | `frontend/src/pages/index-detail` · `/api/v1/market` |
| 申万行业浏览 | 申万一级/二级/三级树、下钻、行业表现 | `frontend/src/pages/market-category` · `frontend/src/pages/market-industry-level2` · `frontend/src/pages/market-industry-level3` · `/api/v1/market` |
| 概念板块 | 板块列表、详情、成分股（成分以 `symbol` 为业务键） | `frontend/src/pages/market-concept` · `frontend/src/features/concept` · `/api/v1/concepts` |
| 热门板块与情绪 | 涨停梯队、板块涨停、昨日涨停、情绪日历/盘中 | `frontend/src/pages/market-hot-sectors` · `frontend/src/shared/api/limitUp.ts` · `/api/v1/market` |
| 市场面数据 | 北向、龙虎榜、大宗交易、解禁、回购、公告、板块与市场资金流、全球指数 | `/api/v1/market`（`market_data.py`）· `frontend/src/shared/api/marketData.ts` |
| 数据新鲜度 | 各表最新业务时间、缺口、上游状态（数据面健康度权威入口） | `/api/v1/market/data-freshness` |

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
| 自选股 | 用户自选列表（数据归属按 `user_id` 隔离，需登录） | `frontend/src/pages/watchlist` · `frontend/src/features/watchlist` · `/api/v1/watchlists` |
| 自定义标签 | 标签分组与按标签看股票 | `frontend/src/pages/tags` · `frontend/src/pages/tags-detail` · `/api/v1/tags` |
| 注册 / 登录 / 会话 | 独立认证库、HttpOnly Cookie、CSRF 双防御 | `frontend/src/pages/login` · `frontend/src/features/auth` · auth-service `/auth` · gateway `forward-auth` |

## 行业投研

| 功能 | 说明 | 代码入口 |
|---|---|---|
| 行业列表 | 已产品化行业（当前：生猪养殖"猪智投"） | `frontend/src/pages/research` · `/api/v1/industries` |
| 投研工作台 | 四 Tab：投资看板 / 行业知识库 / 行情调研追踪 / 交易管理 | `frontend/src/pages/research-workbench` · `frontend/src/features/industry-research` · `/api/v1/industries` |
| 指标注册表 | `metric_key → {名称,单位,频率,源优先级,层级,参考区间}` 由后端下发驱动前端渲染 | `backend/app/services/`（industry registry）· `/api/v1/industries` |
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
