# Plan: 从"能自愈"到"可信赖"——可靠性闭环与研究质量（Phase 2）

status: active
scope: 补齐数据面剩余缺口（重试队列/全域名自愈/A 类当日必达），并把"投研可信度"做成机械门禁（PIT/回测/证据链/溯源全覆盖）；目标是把 SLO 与质量判据从"能看见"升级为"能验收"
touches: backend/app/{services,scheduler,api/v1,models,migrations}, backend/scripts, backend/tests, frontend/src, docs/specs, docs/deployment, scripts/
updated: 2026-11-11
next-action: Sprint 1 的 R1（失败重试队列）+ R2（`limit_up_stock_daily` 纳入自愈）+ R3（A 类多窗口采集）；先修 `triage_rule_stats KeyError`，再扩域

> 前置：[2026-11-11-data-platform-hardening-p5-p7.md](./2026-11-11-data-platform-hardening-p5-p7.md)
> （账本/quarantine/期望/分区/溯源已就位）。本计划**不重做**这些地基，只补"覆盖、重试、可信度"。
> 研究侧契约以 [docs/specs/S2-signal-engine.md](../docs/specs/S2-signal-engine.md) 为准；本计划只排期与验收，不另立契约。

## 0. 先定义"稳定可靠"和"高质量"（否则无法验收）

### 0.1 数据面 SLO（可机械判定的 6 条）

| # | 指标 | 目标 | 检测点 |
|---|---|---|---|
| 1 | 30 日 `expected_as_of` 覆盖率（按域） | ≥ 99.5% | 日结单（`/ops/data-quality` + coverage） |
| 2 | 静默缺口（无 `reason_code`/无归因） | **0** | `ingest_quarantine` + 日结单 |
| 3 | 可补缺口的自动收敛率 | ≥ 95% | 对账 refetched / (refetched + 人工补) |
| 4 | 失败重试成功率 | ≥ 90%，DLQ < 5 条/日 | 重试队列 + dead letter |
| 5 | 关键表膨胀率 | < 20% | `db-health` |
| 6 | 恢复演练 | 每月 1 次，RTO < 30min | `restore_drill.sh` 记录 |

### 0.2 研究质量（投研平台区别于行情站的 5 条）

| # | 判据 | 为什么 |
|---|---|---|
| 1 | **PIT 强制**：任一信号/回测只使用 `published_at <= as_of` 的数据 | 偷看未来会让结论系统性虚高 |
| 2 | **口径可追溯**：每个数字带 `source / as_of / caliber_version` | 口径变更不标注 = 研究不可复现 |
| 3 | **归因可解释**：信号有 `basis`，回测有基准/样本切分/退市处理 | 没有归因的收益是噪声 |
| 4 | **样本纪律**：`n<5` 标注、`n<30` 不给统计推断 | 小样本上做结论是投研最常见的自欺 |
| 5 | **可复现**：结论能还原到"数据版本 + 规则版本 + 基准口径" | 否则无法审计、无法迭代 |

> 北极星：**可信 > 覆盖**。一个错的数字比缺一个数字更伤投研产品。

## 1. 数据面剩余缺口（Sprint 1–2）

### R1 失败重试队列 + dead letter（最高优先）

现状：13 条失败 job 只登记在 Redis hash（7 天 TTL），除对账外**不重跑**。`share_float` 17:30 失败一次要等到明天。

**设计**（零新表，复用 Redis）：

```
失败 → ZSET retry:jobs  field=job_id  score=next_attempt_at
scheduler 每 60s：取 score<=now 的项 → 重跑注册表中的 callable
退避：1 / 5 / 15 / 60 / 180 min（max 5 次）
超过 5 次 → HSET retry:dead + 告警（不再无限重试）
成功 → ZREM
```

- 需要一张 `JOB_REGISTRY: dict[str, Callable]`（id → job function），由 `jobs.py` 显式登记；
  `test_every_registered_scheduler_job_is_alert_wired` 扩一条"可重试 id 必须在 registry 内"。
- **只重试幂等 job**（ingest/派生/对账）；SSE/公告轮询这类高频任务不排重试（下一 tick 自然会跑）。
- 验收：人为让 `share_float` 抛错一次 → 5 分钟内自动重跑成功；DLQ 面板可见。

### R2 自愈扩域（4 → 10+）

按"补漏成本 / 业务价值"排序接入 `DOMAINS`：

| 域 | 补漏通道 | 依赖 | 备注 |
|---|---|---|---|
| `limit_up_stock_daily` | 已有 ingest（`_refresh_limit_up_facts`） | quotes + limits 齐备 | 当前缺 10-14/15，最该先接 |
| `dragon_tiger` | 已有按日 ingest | 无 | T+1 上游 |
| `block_trade` | 已有按日 ingest | 无 | T+1 |
| `share_float` | 已有按日 ingest | 无 | 按 `float_date` 而非 `ann_date` |
| `repurchase` | 已有按日 ingest | 无 | 按 `ann_date` |
| `northbound` | 已有按日 ingest | 无 | 口径已统一 |
| `market_moneyflow` | 已有按日 ingest | 无 | |
| `global_index` | 已有按日 ingest | 无 | 海外假日 |
| `sector_moneyflow` | **A 类不可补** | 当日 eod_full | 只能"当日必达"，见 R3 |

- 每个域接入 = `DomainSpec`（表/日期列/完整判据/refetch/窗口/SLA）+ 单测；
  门禁：`refetch is None` 必须写 `backfillable=False` 与原因。
- 窗口：日频 10 → **30 个交易日**；新增**周环**（周六）深扫 90 天。
- 验收：`limit_up_stock_daily` 的 10-14/15 被自动补回；`coverage` 逐域 ≥ 99.5%。

### R3 A 类"当日必达"（sector_moneyflow）

现状：15:05 一次 + 15:35 一次，失败/容器重启即**永久丢失**；完整日 31.82%。

- 采集守卫从"跑过一次"改为"**行数达标直到 16:30**"：每 10 分钟重试，`eod_full` 行数 ≥ 当日板块总数才停。
- 原始响应先落 JSONL（已有 `data_saver`），入库失败可回放；宿主挂载已修（api/scheduler 共享 `./data`）。
- 当日结束仍未达标 → `ingest_batches.status='quarantined'` + 显式 `permanently_missing` 标记 + 日结单归档。
- 验收：人为在 15:05 断网，16:30 前恢复 → 当日完整日仍能落库；覆盖率 ≥ 90%。

### R4 契约与跨源校验（正确性，不是覆盖）

- **源契约**：每个域声明 `required_columns / dtypes / units / enum`，落地为 `RowRules`（P5 已有，需接线）；
  上游缺列 = 整批 quarantine（已在 pipeline 实现，待接主路径）。
- **校验源（只读）**：单源写入不变（ADR 0001），第二源只做 count / 关键值抽样比对：
  - `daily_quotes` 行数/停牌数 vs 交易所公开统计
  - `sector_moneyflow` eod_full 行数 vs 上游 `data.total`
  - `daily_basic` 市值/PE 抽样 vs 第二源
  - 不一致 → `consistency` 新增 checks + 告警，不自动修。
- 验收：一致性检查从 3 条扩到 ≥ 8 条；每月误报 < 2 次。

## 2. 存储与运维（Sprint 2）

- **S1 恢复演练月度化**：cron/清单 + `operations.md` 记录；评估 PITR（WAL 归档）——先有演练再谈 RPO。
- **S2 保留策略实测**：`data_retention_weekly` 首次真跑，核对 quarantine/quality/batches/poll 的删除量与归档可查。
- **S3 性能预算**：
  - 读面 p95：行情台 < 500ms、个股详情 < 800ms、全景 < 200ms（已有 19–31ms 实测）
  - 慢查询日志 + `EXPLAIN` 门禁（分区裁剪）
  - 前端首屏 bundle ≤ 1.2MB gzip（当前 306KB/349KB，留余量）
- **S4 运维手册与告警分级**：
  - T0 域（quotes/basic/limits/sentiment）未达 → 立即告警
  - T1（派生/情绪指数）→ 22:00 汇总
  - T2（补充域）→ 周报
  - 每周数据复盘模板（缺口、归因、人工补数、复发项）

## 3. 研究质量（Sprint 3–4，真正的差异化）

### Q1 PIT 护栏（最高优先）

- 所有研究读面（`/metrics/*`、行业看板、信号、回测）**禁止**用 `period` 回退当 `as_of`；
  `published_at IS NULL` 必须显式 `missing_reason='no_published_at'`，不得静默。
- 新增 `tests/contracts/test_no_lookahead.py`：对每个派生指标，用"截断到 as_of 的数据"与"全量数据"分别计算，结果必须一致；
  不一致 = 前视，直接失败。
- 验收：PIT 对照实验（S2.3）：同一规则分别按 `published_at` 与 `period` 对齐，统计差异 > 0 且方向符合预期。

### Q2 财报重述与版本

- `financial_report_versions` 已有；读面（个股成长/盈利、排名榜）必须取"当时可见的最新版本"，不得用事后修订值。
- 验收：构造一份重述（revised）数据，`as_of` 早于重述日的读面看不到修订值。

### Q3 分类/成分归因

- 申万成员 SCD2 + 概念成分 `point_in_time` 已在读面使用；回测中必须按**当日**归属，禁止用当前快照回算。
- 退市股不得剔除（幸存者偏差），回测样本空间按当日在市。
- 验收：构造一只退市股，回测样本中仍存在；行业归属逐日可还原。

### Q4 事件研究回测（按 S2.3 落地）

- `backtest_runs` 单表 + `services/backtest.py`（纯查询/统计）+ API。
- 硬约束：`split` 必填且样本外非空；`pit_enforced=false` 的结果不得对外；逐笔不物化。
- 验收：PIT 对照实验 + 样本切分拒绝测试 + 退市股保留测试 + `bench.sh` 绿。

### Q5 信号证据链与样本纪律

- `industry_signals.basis` 必须含：输入指标值、来源、`published_at`、规则版本、缺失项。
- 前端信号卡可展开证据链；`n<5` 标注、`n<30` 不给推断（已有部分，扩展到全部信号）。
- 验收：每条信号都能回答"为什么是它"；缺输入的信号不生成（不静默补 0）。

### Q6 指标字典与口径门禁

- `check_metric_dictionary.py` 扩为 CI 门禁：registry 的每个 `metric_key` 必须有定义/单位/频率/源优先级/口径版本；
  前端不得出现未登记 key（已有 404 兜底，补"构建期"检查）。
- 验收：新增指标漏登记 → CI 红。

### Q7 研究可复现

- 决策日志 `snapshot` 增加：`data_as_of`、`caliber_version`、`provenance_batch_id`；导出时带这些字段。
- 验收：任一历史结论可还原到"当时的数据批次 + 规则版本"。

## 4. 产品体验（与 Q1/Q6 并行）

- **U1 溯源全覆盖**：6 → 所有研究指标（行业指标、信号、回测统计）；用 `ProvenanceLink` 渐进接入。
- **U2 陈旧/部分数据角标统一**：`stale` / `partial` / `missing_reason` 在所有卡片可见，禁止静默 `--`。
- **U3 性能预算**：首屏、切日、图表 hover 的 p95 进 CI（已有 e2e，可加性能断言）。
- **U4 权限与审计**：研究域 `research:read` 已做；补"谁在什么时候看了什么"的审计（可选，按合规需要）。

## 5. 组织与流程（持续）

- **O1 每周数据复盘**（30 分钟）：看日结单、DLQ、覆盖率、人工补数；复发项写成 ADR/门禁。
- **O2 混沌演练**（每月）：kill scheduler → 启动对账收敛；断上游 → 回放/重试；删一天数据 → 自愈补回。
- **O3 事故复盘模板**：时间线、根因、为何没被发现、新增哪条机械门禁。
- **O4 ADR**：PIT 强制、校验源、A 类不可补声明、回测样本切分，各写一篇（只增不改）。

## 6. 路线图（按 4 个 Sprint）

| Sprint | 内容 | 验收 |
|---|---|---|
| **S1（1 周）** | R1 重试队列、R2 `limit_up_stock_daily`+2 域、R3 A 类多窗口、修 `triage_rule_stats` | DLQ 可见；10-14/15 补回；A 类覆盖率 ≥90% |
| **S2（1–2 周）** | R2 其余域、R4 契约+校验源、窗口 30 天+周环、S1 恢复演练、S2 保留实测 | 逐域覆盖率 ≥99.5%；一致性 ≥8 条；演练记录 |
| **S3（2–3 周）** | Q1 PIT 护栏、Q2 重述版本、Q3 归因、Q4 回测（S2.3） | no-lookahead 测试全绿；PIT 对照实验差异 >0；backtest 发布 |
| **S4（持续）** | Q5–Q7、U1–U4、O1–O4 | 溯源全覆盖；周复盘/月度演练成例行 |

## 7. 明确不做

- 不引入 Kafka/Spark/湖仓/dbt：日增 ~5.5k 行，单机 Postgres 足够。
- 不做多源降级链（ADR 0001）；校验源只读比对。
- 不追"所有表每天都有今天的数据"：T+1 数据按 `expected_lag_days` 判定。
- 不做收益承诺/交易下单；信号只做"可回测、可解释"。
- 不在样本 <30 的情况下做参数优化/滚动回测（留给 S6.5，且需更长历史）。

## 8. 验收门槛（Gate Matrix）

### 9.1 两类门槛，不能混用

| 类型 | 性质 | 何时跑 | 失败动作 |
|---|---|---|---|
| **Merge gate** | 确定性（单测/契约/故障注入/结构不变量/基准） | 每次 commit / PR / `self_review --full` | **阻断合并**，不允许"先合后修" |
| **Runtime SLO gate** | 统计性（窗口 + 错误预算） | 每日日结单 + 每周复盘 | 告警 / 事故 / 回滚，**不阻断 PR**（上游挂了不应让代码合不进去） |

把统计指标塞进 CI 会得到 flaky 门禁；把确定性检查留给人工会得到"发完才发现"。

### 9.2 Merge gates（必须机械化，能红一次才准入库）

| ID | 判据 | 检测点 | 失败动作 |
|---|---|---|---|
| G1 | `DOMAINS` 每域都有 `_refetch_`，或显式 `backfillable=False` + 原因 | `test_domain_registry.py` 结构断言 | 阻断 |
| G2 | 每个可重试 job 都在 `JOB_REGISTRY`；高频轮询 job **不在** | `test_job_alerts.py` 扩展 | 阻断 |
| G3 | 外呼抛错 → 回放当天 JSONL（零第二次外呼）；无 JSONL → `failed` + 负缓存 | `test_ingest_pipeline`（fake client 计数） | 阻断 |
| G4 | 删 3 天数据 → reconcile 自动补回；batch `accepted`、行数一致 | `test_reconcile_restore.py`（fixture DB） | 阻断 |
| G5 | 缺列 → 整批 `schema.missing_column` 进 quarantine，core 行数不变 | `test_ingest_pipeline` | 阻断 |
| G6 | 同批跑 3 次 → 行数、账本、quarantine 数都不变 | 幂等测试 | 阻断 |
| G7 | **PIT**：截断到 `as_of` 与全量数据算同一指标结果必须相等；对照实验（`published_at` vs `period`）差异 > 0 | `tests/contracts/test_no_lookahead.py` | 阻断 |
| G8 | 回测：`split` 空 / 样本外空 → 拒绝；退市股保留；分类按当日 SCD2 | `test_backtest_contract.py` | 阻断 |
| G9 | 每个注册 `metric_key` 的 `/provenance` 200 且 `sources` 非空 | `test_provenance_contract.py` | 阻断 |
| G10 | 跨页一致性契约（同一事实 ≥2 端点值一致）；`check:data-display` 无新增 | `test_shared_metric_consistency.py` + 现有门禁 | 阻断 |
| G11 | Tier 1 基准不退化 > 5% | `bash scripts/bench.sh` | 阻断 |
| G12 | 文档系统（ADR 只增、features 契约、端口/队列/metric_key 一致） | `bash scripts/doc_gate.sh` | 阻断 |

### 9.3 Runtime SLO gates（按域分层，窗口 + 错误预算）

| 层级 | 域 | 目标 | 计量口径 | 失败动作 |
|---|---|---|---|---|
| **T0 底座** | quotes / basic / limits / sentiment | **交易日 100%**（23:59 前齐备） | 每交易日 `expected_latest` 是否齐备 | 任一日缺失 = 事故 + 复盘 |
| **T1 研究** | limit_up facts / 派生腿 / 情绪指数 | ≥ 99.5% 域-日 | 30 日窗口 | 告警；连续 2 日 → 事故 |
| **T2 补充** | 龙虎榜/大宗/解禁/回购/北向/全球 | ≥ 98%，缺失必须带原因 | 30 日窗口 | 周报；无归因 → 事故 |
| **重试** | 所有可重试 job | 成功率 ≥ 90%，DLQ < 5 条/日 | 7 日滚动 | 告警 + 人工清理 |
| **存储** | 关键表 | 膨胀 < 20%；恢复演练 RTO < 30min/月 | `db-health` + 演练记录 | 维护窗口处理 |
| **时延** | 行情台/详情/全景 | p95 < 500 / 800 / 200ms | 每日采样 | 性能回归排查 |

**错误预算**：T0 零容忍（缺一天就是事故）；T1 30 天允许 1 个域-日缺口；T2 允许带原因的缺口。
"覆盖"只算**过齐备门禁**的日子，`poll`-only / 部分行 / 无 `eod_full` 一律不算。

### 9.4 每个任务的 DoD（下一步直接照这个判）

| 任务 | Merge gates | Runtime gate | 证明方式 |
|---|---|---|---|
| R1 重试队列 | G2 + G3 + G6 | S3（重试成功率/DLQ） | 人为让 `share_float` 失败 → 5 分钟内自动成功；DLQ 面板 |
| R2 自愈扩域 | G1 + G4 | S1/S2 | 删 `limit_up_stock_daily` 10-14/15 → 对账补回 |
| R3 A 类当日必达 | G3 + G5 | S4（≥90%） | 15:05 断网、16:00 恢复 → 当日 `eod_full` 仍达标 |
| Q1 PIT 护栏 | G7 | — | 对照实验差异 > 0；截断等价 |
| Q4 回测（S2.3） | G8 + G11 | — | `split` 拒绝测试 + 退市股保留 + bench 绿 |

### 9.5 防"门槛腐烂"

1. **每个 gate 必须先红一次**（故意破坏一次，确认它真的会失败）——只写过绿测试的门禁等于没有。
2. **阈值来自真实分布**（如 0.9 行数、0.99 非空率），不拍脑袋；只收紧不放宽，放宽必须写 ADR。
3. **不能机械化的不进 checklist**：写 ADR 或 runbook（带日期/负责人），否则会腐烂成"假绿"。
4. **季度复核**：门禁数量只增不减；被上游变化打破的门禁先修判据，不许直接调阈值。

## 9. 交叉引用

- 数据面地基：[2026-11-11-data-platform-hardening-p5-p7.md](./2026-11-11-data-platform-hardening-p5-p7.md)
- 回测契约：[docs/specs/S2-signal-engine.md](../docs/specs/S2-signal-engine.md) §S2.3
- PIT/口径：[docs/specs/S0-platform-contracts.md](../docs/specs/S0-platform-contracts.md) §4.4
- 自愈设计：[2026-09-17-data-sync-self-healing.md](./2026-09-17-data-sync-self-healing.md)
- 运维：[docs/deployment/operations.md](../docs/deployment/operations.md)


## 10. Sprint 1 实施结果（2026-11-11）

- ✅ **R1 重试队列**：`services/retry_queue_service.py`（ZSET + 1/5/15/60/180min 退避 +
  5 次后 DLQ）、`jobs.retry_dispatch_job`（每 60s）、`job:failures` 自动入队（仅
  `RETRYABLE_JOBS` 里的 20 个幂等 job；高频轮询排除）、`GET /api/v1/ops/retries`。
  实测：手动入队 `global_index_daily` → 下一 tick 自动执行成功、队列清空。
- ✅ **R2 自愈扩域（4 → 12）**：`DOMAINS` 新增 `limit_up_stock_daily` / `dragon_tiger` /
  `block_trade` / `share_float` / `repurchase` / `northbound` / `market_moneyflow` /
  `global_index`；`DOMAIN_COMPLETENESS` 区分 `rows|batch`（空数据也可能是合法事实）；
  窗口 10 → **30 个交易日**；新增周六 90 天深扫 `reconcile_weekly_deep`。
  结构门禁 `test_domain_registry.py`：每个域必须有 `_refetch_` 或显式不可补。
- ✅ **R3 A 类当日必达**：`sector_moneyflow_capture`（15:15–16:25 每 10 分钟，抓到
  `eod_full` 为止）；已有 15:05/15:35 两次保留。
- ✅ **已知洞回填**：`limit_up_stock_daily` 的 10-14/15/16 已补回（122 / 81 / 141 行）；
  `ingest_batches` 已记录各域批次。
- ✅ **triage 标定修复**：`DATE_COLUMN` 补 `index_dailies`，消除
  `artifacts_refresh:triage_rule_stats KeyError('index_dailies')`。
- ✅ **门禁**：`pytest 2011 passed`；新增 retry/domain-registry/coverage 语义测试；
  `mypy` 243 文件无问题。
- ⏳ **Sprint 2 已完成的部分**（见 §11）：Q4 回测（S2.3）、Q6 口径字典门禁、
  一致性检查扩到 6 条；Q1 PIT 读面 / Q2 重述留痕 / Q3 退市归因已补齐。
  **仍未做**：独立校验源（需第二个数据源）、其余派生读面的截断等价套件。
- ⚠️ `share_float` 上游 API 仍失败（3 次重试后进 DLQ），日缺口保持可见；不是代码回归。
- ✅ 实测收敛：重启后启动对账 ~60s 完成（不再被重复窗口外呼拖住）；`dragon_tiger`/
  `block_trade` 缺口 30 → 10、窗口型补充域 30 → 14，逐轮递减；`share_float` 因上游
  API 持续失败保持 30（可见、不静默）。
- ✅ 限流保护：窗口型补充域每轮只补 1 天（一次窗口请求覆盖多日，避免撞
  `index_global 10 次/分钟`）；`limit_up_stock_daily` 本地纯计算，上限 30。


## 11. Sprint 2 实施结果（2026-11-11）

- ✅ **Q4 单规则事件研究回测**（spec §S2.3）：
  - 新表 `backtest_runs`（迁移 `d5e6f7a8b9c0`）：`rule_key/rule_version/scope/window/
    horizons/benchmark/split/pit_enforced/stats` 全部落库，结论可复现。
  - `services/backtest_service.py`：资产 = 信号日 SCD2 成分股等权收益中位数；
    基准 = 指数同窗口收益；`split` 必填且样本内/外不重叠；信号生成时间晚于
    生效日 +1 天 → **PIT 不通过、拒绝落库**；窗口内无信号 → 422。
  - `POST /api/v1/backtests` / `GET /api/v1/backtests/{id}`（`research:read`）。
  - 实测（pig / 关注 / 2026-09-01..11-10 / 1,5,20 日）：27 个事件；
    1 日 n=26、5 日 n=22、20 日 n=9；全部 `insufficient_sample=true`
    （<30），20 日样本外 n=0（远期数据尚未产生）。**不粉饰小样本**。
  - 单测 `tests/test_backtest_service.py`（9 条）：切分重叠/越界/非行业 scope、
    空样本不伪造 0、PIT 迟到信号拒绝、T+1 生成放行。
- ✅ **R4（部分）一致性检查扩到 6 条**：新增 `flat_count`、`limit_up_count`、
  `limit_down_count` 的"派生表 vs 底座重算"；2026-11-10 实测 6/6 ok、diff 全 0。
- ✅ **Q6 口径字典门禁**：`scripts/check_metric_dictionary.py` 接入
  `scripts/self_review.sh` 与 CI `backend-lint`；49 行样例表 0 MISMATCH。
- ✅ **Q1 PIT 读面（2026-11-11 补齐）**：`/metrics/latest` 与 `/metrics/series`
  新增 `as_of` 参数，仓储层用 `coalesce(date(published_at), period) <= as_of`
  过滤；缓存键含 `as_of`（不同截止日不共用缓存）。真库测试
  `test_series_and_latest_respect_published_at_as_of`：8 月值 9/5 发布、9 月值
  10/30 发布，`as_of=10/1` 只看到 8 月值，`as_of=11/1` 才看到两期。
  回测侧仍保留"信号生成时间晚于生效日 +1 天即拒绝"的硬门禁。
- ✅ **Q2 重述留痕（2026-11-11）**：新表 `financial_report_revisions`
  （迁移 `f7a8b9c0d1e2`；与回测 head 用 `a8b9c0d1e2f3` 合并）。主表唯一键不含
  公告日，修订会覆盖同一行；`upsert_report_version` 在覆盖前把旧值
  （`ann_date/f_ann_date/update_flag/quality_status/as_of/raw_record_id`）快照到
  修订表。新增 `GET .../financial-reports/{end_date}/revisions`。真库测试
  `test_restatement_snapshot_is_kept`：两次公告日后修订 → 1 条旧值快照。
- ✅ **U1 溯源扩面**：`/provenance/{metric_key}` 对**所有已登记**指标可用；
  未接批次账本的指标返回 `lineage_status=definition_only` + 实际读取行，
  **不伪造 batch/quality**。6 个跨页核心指标仍走完整链路。
- ✅ **Q3 退市/归因**：回测篮子按信号日 SCD2 成分（`sw_industry_member_periods`）
  取数，SQL **不过滤** `stocks.list_status/delist_date`；有行情的退市股会被计入，
  避免"只用今天还活着的公司"的幸存者偏差。窗口内无行情的成员自然跳过，
  样本数在 `stats` 里可见。
- ✅ **R4 独立校验源（2026-11-11 补齐）**：
  - `services/verification_service.py`：抽样 12 只（`md5(symbol)` 定序，可复现）用
    **无血缘**的东财 push2his 日线比对 `close`（相对 0.5%）与 `pct_chg`（0.3pp）；
    一致率 ≥95% pass / ≥85% warn / 否则 fail；**源不可用 = error，不是 pass**；
    只读、不写业务表、不自动修数（自动覆盖会把错误固化）。
  - `GET /api/v1/ops/verification` 读最近结果（未跑过 404）；`independent_verify`
    周六 04:30 执行并落 `data_quality_runs`（severity=warn），已进 `RETRYABLE_JOBS`。
  - 新增**离线**恒等式断言 `dq.daily_quotes.price_identity`（close/pre_close 与
    pct_chg 自洽，违规率 ≤0.1%，block 级）：上游把 close 写错时行数/非空率/区间
    全绿，只有这条会红。2026-11-10 实测 11/11 pass（5572 行 0 违规）。
  - 实测边界：本机到 eastmoney 的网络时通时断（`ConnectError`），因此现网
    `/ops/verification` 会如实返回 `error` / `source_error`——这正是设计要的
    "查不了 ≠ 通过"；网络恢复后同一条命令即得到 pass/fail。
