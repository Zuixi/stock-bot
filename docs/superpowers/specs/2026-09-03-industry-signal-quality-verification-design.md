# 行业数据质量门控与信号验证设计

## 目标

为行业投研工作台增加一条确定性、可审计的闭环：指标入库后评估数据质量，只有关键输入满足要求时才更新周期信号；信号或阶段发生变化时形成不可变事件；猪周期的买入/卖出事件在 30/90 个自然日窗口后按行业指标变化自动验证。

本功能不使用 AI 生成指标、信号或验证结果，也不归档文件形式的原始响应；所有状态写入 PostgreSQL。

## 范围

第一版包含：

1. Registry 驱动的数据质量定义：存在性、新鲜度、来源可接受性、公司级实体覆盖率。
2. 每次行业 ingest 后持久化一条质量快照。
3. 猪周期关键输入不合格时不运行规则引擎、不覆盖最近一次有效信号。
4. 信号类型或周期阶段变化时创建不可变信号事件；首次有效信号也创建基线事件。
5. 猪周期买入/卖出事件创建 30 天、90 天验证记录。
6. 到期验证按冻结的规则版本和输入快照计算 `confirmed`、`partially_confirmed`、`invalidated` 或 `inconclusive`。
7. Dashboard 展示非阻断式数据质量提示、当前信号是否为历史保留值、信号事件及验证状态。
8. Scheduler 在行业指标和证券数据刷新后扫描到期验证。

第一版不包含：

- AI 复盘或叙事生成。
- ETF/个股收益回测；验证对象是周期判断，不是投资收益。
- 关注/空仓事件的准确率评价。
- 白羽肉鸡的正式验证模型；该行业继续明确标记为 mock 演示。
- 原始响应归档。
- 单独的 MQ 验证任务；验证由 ingest 后尝试执行，并由 scheduler 每日兜底扫描。

## 数据质量模型

### Registry 声明

`MetricDef` 增加：

- `required_for_dashboard: bool = False`
- `required_for_signal: bool = False`
- `max_age_days: int | None = None`
- `allow_signal_sources: tuple[str, ...] = ()`
- `coverage_scope: str = "industry"`，可选 `industry | company`
- `min_entity_coverage: float | None = None`

`IndustryConfig` 墳加：

- `signal_quality_required: bool = True`
- `verification: SignalVerificationConfig | None = None`

猪周期关键输入为 `hog_price`、`hog_corn_ratio`、`sow_inventory_mom`，其中日度指标按 7 天新鲜度、月度指标按 75 天新鲜度；用于信号的来源不得是 `mock`。`industry_cost_avg` 作为增强证据而非第一版硬门槛，避免人工月度成本长期缺失导致所有信号停摆。

白羽肉鸡设置 `signal_quality_required=False` 且无 verification 配置，保留演示行为，但质量状态明确为 `demo`，不产生正式验证记录。

### 质量状态

每个指标状态为：

- `ready`：存在、未过期、来源符合要求。
- `missing`：没有可选行。
- `stale`：数据期次超过 `max_age_days`。
- `source_rejected`：用于信号时来源不在允许列表，例如 mock。
- `partial`：公司级实体覆盖率低于阈值。

行业快照状态为：

- `healthy`：所有 dashboard 与 signal 必需项合格。
- `degraded`：Dashboard 可展示，但存在缺失、过期、fallback 或实体覆盖不足。
- `unavailable`：正式行业的 signal 必需项不合格。
- `demo`：行业明确配置为演示模式。

`signal_ready` 只表示本次是否允许生成正式信号。数据质量不足不会回滚已成功入库的指标，也不会阻断 Dashboard 返回已有数据。

### 持久化

新增 `industry_data_quality_snapshots`：

- 唯一键 `(industry_key, as_of)`，同日重跑幂等更新。
- 保存 status、signal_ready、计数和 details JSONB。
- details 中保存每个指标选中的 source、period、age_days、status、reason、实体覆盖率。

## 信号生成与事件

### 每日信号

现有 `industry_signals` 继续作为“每日有效规则输出”。流程为：

1. 指标与派生指标落库。
2. 计算并持久化质量快照。
3. `signal_ready=true` 或行业处于演示模式时才运行 `cycle_engine`。
4. 正式行业质量不合格时不调用引擎、不 upsert 当日信号，返回最近一次有效信号并标记 `is_stale=true`。
5. Dashboard GET 保持只读；删除空库时隐式生成信号的兜底。

`cycle_engine` 保持纯函数，不负责数据质量判断。现有“关键指标缺失时按萧条处理”分支保留为纯函数防御，但正式调用路径不会让不合格输入进入引擎。

### 不可变信号事件

新增 `industry_signal_events`：

- 首次有效信号创建事件。
- 后续只有 `signal_type` 或 `phase` 与最近事件不同才创建事件。
- 保存 previous signal/phase、当前 signal/phase、event_date、basis、basis_periods、quality_snapshot、rule_version。
- 唯一键 `(industry_key, event_date, signal_type, phase)`，同日重跑幂等。

每日重复的同一信号不创建新事件，避免把一个持续状态统计成多个独立样本。

## 30/90 日验证

### 验证对象

第一版只为猪周期 `买入`、`卖出` 事件创建验证记录；关注和空仓事件仍展示在事件历史中，但 `verification_supported=false`。

窗口使用自然日：

- 30d：`target_date = event_date + 30 days`
- 90d：`target_date = event_date + 90 days`

验证取目标日期当日或之后第一个可用观测。日度指标最多等待 7 天；月度指标最多等待 45 天。超过宽限仍缺少必需证据时为 `inconclusive`，而不是判错。

### 冻结规则

Registry 为 pig 配置 `methodology_version="pig-cycle-v1"`。事件创建时将版本和规则复制到验证记录，未来 registry 调整不会静默重写历史结论。

买入事件规则：

- 猪粮比相对事件值上升至少 3%，权重 40。
- 生猪均价相对事件值上升至少 3%，权重 30。
- 到期后的能繁存栏环比不高于 0，权重 30。

卖出事件规则：

- 猪粮比相对事件值下降至少 3%，权重 40。
- 生猪均价相对事件值下降至少 3%，权重 30。
- 到期后的能繁存栏环比不低于 0，权重 30。

规则分数：满足得满权重，出现明确反向变化得 0，中性变化得一半权重；必需指标缺失且超过宽限则 `inconclusive`。

结论：

- `score >= 70`：`confirmed`
- `40 <= score < 70`：`partially_confirmed`
- `score < 40`：`invalidated`
- 证据不足：`inconclusive`
- 未到期：`pending`

新增 `industry_signal_evaluations`，唯一键 `(signal_event_id, horizon_days, methodology_version)`，保存目标日期、状态、起止快照、各规则结果、score 和 evaluated_at。

## 服务流程与事务

完整 ingest 事务：

```text
fetch → upsert metrics → purge covered mock/derived
→ compute derived metrics
→ assess and upsert quality snapshot
→ if allowed: upsert daily signal → create transition event/evaluations
→ evaluate due records that now have sufficient data
→ caller commit
```

Repository 和 service 不自行 commit；API dependency、worker 和 scheduler 保持现有事务所有权。

Scheduler 在 17:20（证券刷新之后）增加每日验证扫描，遍历存在 verification 配置的行业。扫描幂等，可与 ingest 后的扫描重复执行。

Dashboard 缓存继续使用 60 秒 TTL。为避免在事务提交前重建旧值，worker/scheduler 在成功 commit 后删除 `industry:{key}:dashboard`；API batch 写路径由路由在依赖提交边界无法安全立即清缓存，因此第一版继续接受最多 60 秒 TTL。

## API 合约

`DashboardOut` 增加：

- `data_quality: DataQualityOut`
- `signal: SignalOut | None`
- `signal_is_stale: bool`
- `signal_events: list[SignalEventOut]`
- `verification_summary: VerificationSummaryOut`

`SignalEventOut` 包含 event_date、signal、phase、previous 状态、rule_version 和 evaluations。历史 basis 默认不直接全部展开到 UI，只由后端保留审计。

增加只读端点：

- `GET /api/v1/industries/{industry_key}/signal-events?limit=20`

GET 不触发质量重算或到期验证。

## 前端

新增 `DataQualityBanner`：

- healthy 时显示紧凑绿色状态，不抢占主视觉。
- degraded/unavailable/demo 时显示 Alert，列出最多 3 个关键问题，并允许展开全部指标明细。
- 明确区分“指标业务预警”和“数据质量异常”。
- 当 `signal_is_stale=true` 时显示“当前展示为最近一次有效信号，本次因数据不足未更新”。

扩展 `SignalPanel`：

- 时间线使用 signal events，不再用每日重复信号。
- 每个事件展示 30d/90d 状态徽章、分数和主要证据。
- pending 显示目标日期；inconclusive 显示数据不足原因。
- 样本不足时不展示误导性的百分比准确率，只展示已验证数量分布。

API mapper 必须显式完成 snake_case → camelCase 转换，不在组件中重复解释后端字段。

## 测试与验收

### 后端纯单测

- 新鲜度、来源拒绝、缺失、公司覆盖率和行业聚合状态。
- signal_ready=false 时不调用 cycle engine、不覆盖最新有效信号。
- 首次有效信号和 signal/phase 转换创建事件；同状态重复运行不创建事件。
- 30/90d pending、confirmed、partial、invalidated、inconclusive。
- 目标日后首个观测和宽限期边界。
- 迁移/ORM 唯一约束与 repository conflict key 一致。

### API/数据库集成

Docker Compose 启动并迁移后验证：

- ingest 返回 quality 与 signal_updated。
- Dashboard 合约包含质量、stale 信号、事件与验证摘要。
- 重复 ingest 不产生重复事件/验证。
- signal-events 端点只读且分页限制生效。

### 前端与 E2E

- `npm run build`。
- 补齐现有 ESLint 配置所需依赖后 `npm run lint`。
- Playwright 使用 route interception 覆盖 unavailable、stale signal、pending、confirmed、inconclusive 状态。
- 真实 Docker 栈运行现有 research E2E，确保列表、工作台、公司、证券和导航无回归。

### 完成标准

- 后端离线测试、Docker 集成测试、ruff、mypy 通过。
- 前端 lint、build 通过。
- Playwright E2E 通过。
- Alembic upgrade head 成功。
- `docs/Changelog.md` 与 `docs/references/best-practices.md` 各追加一句总结。
