# Wave 2 Plan 2：券商研报结构化切片

status: completed
scope: 东财研报元数据 → `content_items/content_entities/content_metrics`；评级/EPS/PE/目标价/行业结构化入库；「有效研报」三条判据；`/news` 研报 Tab；不接 LLM `core_view`（明确显示未抽取）
touches: backend/app/{core/providers,services,api/v1,schemas,scheduler}, backend/tests, backend/scripts, frontend/src/{features/content,pages/news,shared/api,app/router}, docs/specs, docs/features.md, docs/Changelog.md, plans/index.md
updated: 2026-11-11
next-action: 无（Plan 2 收口；下一步 Wave 2 Plan 3 快讯）

> 契约：[docs/specs/S3-content.md](../docs/specs/S3-content.md) §S3.4 / §S3.7。
> 本计划**复用** Plan 1 的四表，不新建表、不新建迁移。
> owner 裁定延续：只存链接；正文/PDF 不落库；`source_span` 用 `structured` 形态。

## 0. 本计划的边界

**做**：
- 东财研报列表接口（结构化元数据）采集、幂等入库、实体关联、指标落库。
- 「有效研报」三条判据（去重 / 行业相关 / 有量化字段）+ 人工终态优先。
- `/news` 研报 Tab 展示评级、评级变动、EPS/PE 预测、目标价、行业；被过滤的研报可显式查看。
- 失败可见：接口失败告警、分页截断响亮失败、URL 不可凭空构造。

**不做（明确留给 Plan 2b）**：
- **LLM `core_view` 抽取**：没有选定模型供应商与预算前不写"假核心观点"；列表显示
  `core_view: 未抽取` + `missing_reason='core_view_not_extracted'`。
- 研报 PDF 全文解析、研报有效性的人工裁定界面（沿用 `review_status`，UI 后续补）。
- 付费源（慧博/Choice/Wind）与新闻快讯。

## 1. 数据源与口径

**源**：东方财富研报列表
`http://reportapi.eastmoney.com/report/list`（容器内实测可用；HTTPS 端点在本机容器网络不可达）

参数（固定，避免"参数静默退化为全量"）：
```
pageSize=100&pageNo=N&qType=0&beginTime=YYYY-MM-DD&endTime=YYYY-MM-DD
&industryCode=*&industry=*&rating=&ratingChange=&orgCode=&code=*&rcode=&p=1&pageNum=N
```
必须带 `User-Agent` 与 `Referer: http://data.eastmoney.com/report/`。

**返回关键字段**（实测）：
`infoCode`（唯一键）、`title`、`stockCode`、`stockName`、`orgSName`/`orgName`、
`publishDate`（`"YYYY-MM-DD 00:00:00.000"`，上海墙上时间）、`emRatingName`、
`ratingChange`、`predictThisYearEps`/`predictNextYearEps`/`predictNextTwoYearEps`、
`predictThisYearPe`/`predictNextYearPe`/`predictNextTwoYearPe`、
`indvAimPriceT`/`indvAimPriceL`、`indvInduName`/`indvInduCode`、`researcher`、`encodeUrl`。

**原文链接**：`https://pdf.dfcfw.com/pdf/H3_{infoCode}_1.pdf?{encodeUrl}`。
容器网络当前不可达 PDF 域名，但这是东财公开链接格式；**只存链接**，不在服务端抓 PDF。
测试只断言 URL 构造，不做容器内可达性探测（部署环境网络不同）。

**PIT**：`published_at` 用 `publishDate` 的上海墙上时间显式附加 `Asia/Shanghai`；
`ingested_at` 由 DB 默认。任何"最新评级"读面必须按 `published_at` 排序。

## 2. 落库映射

`content_items`：
- `content_type='report'`（卖方研报，区别于公告里的定期报告子类 `report`）
- `source_name='eastmoney_report'`，`publisher_tier='sellside'`
- `publisher=orgSName`，`title`，`published_at`（上海 aware），`url=pdf_url`
- `raw_hash=sha256("eastmoney_report:{infoCode}")`
- `extracted={"source_item_id": infoCode, "stock_code": ..., "stock_name": ...,
  "org_name": ..., "industry": ..., "core_view_status": "not_extracted"}`

`content_entities`：
- `("stock", stockCode)`（6 位）
- `("industry", indvInduName)`（东财行业名；`indvInduCode` 存 `extracted`，不混申万）

`content_metrics`（全部 `source_span.kind='structured'`，`table='eastmoney_report_list'`）：
| metric_key | 取值 | period | unit |
|---|---|---|---|
| `rating` | `emRatingName`（value_text） | NULL | NULL |
| `rating_change` | `ratingChange`（value，数值码） | NULL | NULL |
| `target_price` | `indvAimPriceT` | NULL | `CNY` |
| `eps_forecast` | 三个 `predict*Eps` | `{year}E` / `{year+1}E` / `{year+2}E` | `CNY` |
| `pe_forecast` | 三个 `predict*Pe` | 同上 | `x` |

空值不写 0；缺字段写 `extracted.missing_reason`（如 `target_price_missing`）。

## 3. 「有效研报」判据（S3.7）

在 `backend/app/services/research_report_service.py` 中集中实现，**配置驱动**：

```python
REPORT_RULES = {
    "require_quantitative": True,
    "require_industry": True,
    "dedup_by_rating_change": True,
}
```

判定顺序（人工终态优先）：
1. `review_status='confirmed'` → 有效；`'rejected'` → 无效（原因 `human_rejected`）。
2. `extracted.superseded_by` 存在 → 无效（`superseded`，保留可查）。
3. 无任一量化字段（rating/target_price/eps/pe） → 无效（`no_quantitative_field`）。
4. 无 `industry` 实体且标题未命中行业词 → 无效（`industry_mismatch`）。
5. 其余 → 有效。

**去重（写入时）**：同 `publisher + stock + rating_change` 只保留 `published_at` 最新一条；
旧条目写 `extracted.superseded_by = 新条目 id`，不删除。乱序回补也要正确处理：
新到的是旧报告时，给它写 `superseded_by` 指向已存在的新报告。

## 4. Tasks

### Task 1：东财研报 client + fixture 解析
**Files**：`backend/app/core/providers/eastmoney_report_client.py`（create）·
`backend/tests/test_eastmoney_report_client.py`（create）
**要求**：
- `fetch_reports(begin_date, end_date, *, page_size=100, max_pages=50) -> list[dict]`
- 固定参数、UA/Referer；HTTP 失败重试 3 次；分页跑满上限时用一页探针确认，**拒绝静默截断**。
- `publishDate` → 上海 aware datetime；`infoCode` 缺失/重复 → 报错。
- `pdf_url` 构造：`https://pdf.dfcfw.com/pdf/H3_{infoCode}_1.pdf?{encodeUrl}`（`encodeUrl` 为空则 `url=None`）。
- 测试用 fixture JSON，不联网；断言字段映射、URL、分页截断反例。

### Task 2：研报写入内容层 + 去重标记
**Files**：`backend/app/services/content_ingest/research_report.py`（create）·
`backend/app/services/research_report_service.py`（create，规则/去重）·
`backend/tests/test_research_report_ingest.py`（create）
**要求**：
- `ingest_research_reports(db, begin_date, end_date) -> dict[str,int]`：
  client → `content_repo.upsert_content_items` → entities → metrics → 去重标记。
- 幂等：同一 `infoCode` 重跑新增 0；指标 upsert 覆盖。
- 缺 `stockCode` 或 `encodeUrl`：不入库，记 warning（没有实体/链接的研报不可用）。
- 去重规则按 §3；测试覆盖"新报告到 → 旧报告 superseded"和"乱序回补"。
- `evaluate_report(item, metrics, entities) -> ReportEvaluation` 纯函数 + `review_status` 优先。
- 测试真库（`stock_bot_test`）+ 自清理。

### Task 3：API 扩展（effective / include_ineffective / 研报字段）
**Files**：`backend/app/api/v1/content.py` · `backend/app/schemas/content.py` ·
`backend/tests/test_content_api.py`
**要求**：
- `ContentItemOut` 增加 `effective: bool | None`、`ineffective_reasons: list[str]`。
- `GET /api/v1/content?content_type=report` 对每条研报计算 effective；默认隐藏无效，
  `include_ineffective=true` 时返回（供"显示被过滤"开关）。
- 非 report 内容 `effective=None`、reasons=[]。
- 列表仍批量取 metrics/entities，无 N+1。
- 测试：默认过滤、include 后可见、人工 `confirmed` 翻案、非 report 不受影响。

### Task 4：调度 + 回补脚本
**Files**：`backend/app/scheduler/jobs.py` · `backend/app/scheduler/runner.py` ·
`backend/app/services/market_data_service.py`（或 `task_service` 的任务类型）·
`backend/scripts/backfill_research_reports.py`（create）·
`backend/tests/test_research_report_job.py`（create）
**要求**：
- 每日 18:40 增量抓近 3 日；失败走既有 `_alert_job_failure`。
- 手动任务类型 `research_reports`（`POST /api/v1/tasks/fetch-market-data`）。
- 回补脚本 `--start --end`，逐日窗口、幂等、打印新增/更新条数。
- 不新增队列（复用 `market_data.fetch`）或新增 `content.research.fetch` 并登记 `QUEUES`；
  二选一，保持单一入径。

### Task 5：`/news` 研报 Tab + e2e
**Files**：`frontend/src/features/content/ResearchReportRow.tsx`（create）或扩展 `ContentList` ·
`frontend/src/pages/news/index.tsx` · `frontend/src/shared/api/content.ts` ·
`frontend/e2e/newsResearch.spec.ts`（create）
**要求**：
- 研报 Tab 接 `ContentList contentType="report"`；行显示机构、评级、评级变动、目标价、
  EPS/PE 预测、行业；`core_view` 显示「核心观点未抽取」+ 不伪造。
- 默认只显示有效研报；顶部开关「显示被过滤」→ `include_ineffective=true`，被过滤行带原因徽章。
- 无数据空态与失败态分离。
- e2e mock 两条（有效 + 被过滤），断言默认只显示有效、开关后出现被过滤行与原因。

### Task 6：文档/门禁/回填
**Files**：`docs/features.md` · `docs/architecture/database-architecture.md`（仅补研报映射说明）·
`docs/Changelog.md` · 本计划 · `plans/index.md`
**要求**：只写事实；`doc_gate`、`check:routes`、`check:data-display` 全绿；Changelog 一条。

## 5. 验收判据

- [x] 东财 fixture 解析：`publishDate` 上海时区、`pdf_url`、分页截断反例全过。
- [x] 同一批重跑：`content_items` 新增 0、指标覆盖、实体不重复。
- [x] 去重：新报告使旧报告 `superseded_by` 生效；乱序回补不产生双有效。
- [x] 有效研报三条判据可逐条解释；`confirmed` 人工翻案生效。
- [x] API 默认过滤无效研报；`include_ineffective=true` 可见且带原因。
- [x] `/news` 研报 Tab 可用；`core_view` 未抽取明确显示，不伪造。
- [x] `npx tsc --noEmit` + `npm run build` + Playwright 研报 e2e 通过。
- [x] 后端内容相关测试 64 passed；`doc_gate` / `tsc --noEmit` / Playwright 研报 e2e 全绿。`self_review --full` 未跑：工作区另有未提交改动，全量门禁交由合入时跑。
