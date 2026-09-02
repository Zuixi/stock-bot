# 行业投研工作台收尾（P3 剩余 + P5 + P6）Implementation Plan

> **For agentic workers:** superpowers:subagent-driven-development，每阶段独立提交。

**Goal:** 完成 plans/industry-research-workbench.md 的剩余阶段：P3 协会源、P5 标的分析、P6 知识库与泛化验证。

> **完成状态**（2026-09-03）：五阶段全部落地并独立提交（Stage A `c2ffc0a`+`7a6ad41` / Stage B `63b0813` /
> Stage C `6ca66a9` / Stage D `7cb93c5` / Stage E 见 Changelog 2026-09-03 P6 收尾条目）；
> 全量验收（离线单测 / e2e / Playwright / 实跑重建）通过，母计划验收项已勾选留痕。

## 侦察结论（2026-09-03 实测）

- pig.caaa.cn 根域/文章页 200（目录列表 403）；月度文章正文含"能繁母猪存栏3904万头，环比下降1.5%"文本，无 `<table>`，正则可解析；文章 URL 形如 `/html/pig_rd/pig_hydt/{Y}/{MMDD}/{id}.html`。
- TuShare `fund_daily`（510300 返回数据）与 `cb_daily`（列结构正常返回）权限均可用。
- 前端挂载点：`pages/research-workbench` 行情调研追踪 Tab（占位）、`pages/market-industry-level3`（banner）、`pages/stock-detail`（/stock/:symbol）。

## Stages

### Stage A（P3 剩余）：能繁协会源 — 完成
- 新 `app/core/providers/caaa_client.py`：httpx 异步抓 栏目列表页 → 最新含"生猪产品数据"文章 → 正则提取 能繁母猪存栏(万头,绝对数) 与环比；UA/超时/容错（解析失败 log+跳过，绝不抛穿）。
- 接入：`source=="akshare"` 时 ingest 追加 caaa 行（sow_inventory, source="caaa", freq=monthly, period=文章月月末）；registry sow sources → `["stats_gov", "caaa", "mock"]`；sow 纳入 covered purge。
- 测试：HTML fixture（真实文章快照）解析单测 + 网络隔离；实跑验证真实落库。

### Stage B（P5 核心）：标的分析 — 完成
- Registry 公司指标：`company.hogs_sold_monthly`(万头,monthly,manual)、`company.cost_complete`(元/kg,quarterly,manual)、派生 `mcap_per_head`(元/头,calc)。
- batch 导入已有 stock_id 通道（验证 ge=1 语义）；ingest 派生：公司年化出栏 × join `daily_basic_indicators.total_mv` → 头均市值落表。
- API：`GET /industries/{key}/companies`（sw_l3_codes → 成分股 + 公司指标 latest + enriched 行情/PE/市值 + mcap_per_head，列定义由 registry 下发）。
- 前端：research-workbench「行情调研追踪」Tab → CompanyComparisonTable（列 registry 驱动、行点击跳 /stock/:symbol）；market-industry-level3（110301）加"进入投研工作台"banner → /research/pig。
- 测试：派生纯函数单测 + companies 端点 e2e + Playwright 用例（表格渲染/跳转/banner）。

### Stage C（P5 行情）：ETF/可转债 — 完成
- 新表 `fund_etf_daily`、`cb_daily`（迁移）+ TuShare fetcher（回补 1 年 + 日增量）+ QUEUES/Worker/Scheduler 注册。
- Registry：industry 配置 `etf_codes`（畜牧ETF 159865.SZ 等）/`cb_codes`（牧原/温氏等转债，实现时用 cb_basic 校验在市代码）。
- API：`GET /industries/{key}/securities?type=etf|cb`；前端两张表挂「行情调研追踪」Tab。
- 测试：fetcher fixture 单测 + e2e。

### Stage D（P6 知识库）— 完成
- 新表 `industry_knowledge`(industry_key, kind: org|principle|mindmap, payload JSONB) + 迁移内 seed（内容取自 PRD：机构图谱四分组带权威性徽章、数据权威性原则、行业思维导图树）。
- API `GET /industries/{key}/knowledge`；前端知识库 Tab：机构图谱分组卡片 + EChart tree 思维导图 + 原则卡。
- 测试：e2e + Playwright。

### Stage E（P6 收尾）— 完成
- `/research` 列表卡片：加 周期阶段/当前信号（后端 list_industries 增 latest signal 查询）。
- 泛化验证：registry 增演示行业（如 "broiler" 白羽肉鸡，2-3 个 mock 价格指标）→ 零前端改动跑通 列表→工作台。
- 勾选 plans/industry-research-workbench.md 各阶段验收项；全量测试（单测+e2e+Playwright）；Changelog/best-practices 收尾。

## Global Constraints
- 每阶段：实现子代理自带提交（conventional 中文）；AGENTS.md docs 沉淀随阶段；测试先离线单测后实跑。
- 栈在运行（api:8000/frontend:3000，INDUSTRY_DATA_SOURCE=akshare）；后端改动需重建镜像实跑验证后过关。
- 外部源失败不阻塞：解析/抓取失败 log+跳过（Stage A 容错优先）。
