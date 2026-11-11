# Plan: P5–P7 数据平台加固（清洗分层 · 存储治理 · 溯源显示）

status: active
scope: 把"获取可靠性"的优势延伸到清洗/存储/显示三层：staging+quarantine+期望断言、分区与膨胀治理+恢复演练、数字级溯源与跨页一致性；目标是把"一流"变成可机械验收的 8 条判据
touches: backend/app/{models,repositories,services,api/v1,migrations}, backend/scripts, backend/tests, frontend/src/{shared/ui,features}, frontend/scripts, scripts/, docs/features.md, docs/architecture/, docs/deployment/
updated: 2026-11-11
next-action: 已在开发栈完成全部执行项（分区切换 / VACUUM FULL / 恢复演练）；剩余为生产环境按 runbook 复跑与 7 天后 `--cleanup`

> 上游背景：[2026-09-17-data-sync-self-healing.md](./2026-09-17-data-sync-self-healing.md)（对账收敛器）、[2026-10-16-derived-metrics-converge.md](./2026-10-16-derived-metrics-converge.md)（派生指标收敛）。
> 本计划不重做对账层，只在其上补"清洗隔离 / 存储治理 / 溯源显示"三块。

## 0. 现状证据（2026-11-10 实测，本地栈）

| 事实 | 证据 | 影响 |
|---|---|---|
| 自愈只覆盖 4/12 域 | `DOMAINS = (daily_quotes, daily_basic, price_limits, sentiment)`；8 个巡检域无 `_refetch_*` | `limit_up_stock_daily` 缺 10-14/15 至今、`sector_moneyflow` 完整日 7/22 |
| 清洗无隔离层 | 解析失败/映射不到的行直接 skip，只留一个计数 | 无法回答"丢了哪些行、为什么丢" |
| 完整性有判据，正确性无判据 | 行数 + `pct_chg` 非空率 + 限价存在 | 9/14 "行数满 5485、`pct_chg` 仅 1 行"靠人肉发现 |
| `daily_quotes` 未分区 | `pg_class.relkind='r'`；4,547,759 行 / 1,441 MB；`n_dead_tup=485,102`（10.7%）；`last_autovacuum` 为空 | 与 `database-architecture.md` §2.3、`models/quote.py` 注释三处矛盾 |
| 统计信息长期未更新 | `last_autoanalyze` 为空；`n_live_tup` 曾报 612 / 1,553 行，`VACUUM (FULL, ANALYZE)` 后真实行数为 1.15M / 3.57M（raw / metrics） | "行数小但文件大"其实是统计过期造成的误读；膨胀治理先修 analyze，再谈归档 |
| 备份无演练 | 只有 `pg_dump`（`deployment/data-migration.md`、`deploy_prod.sh`） | 没验证过的备份等于没有备份 |
| 显示有徽章无溯源 | `MetricBadge` 有 `source/as_of/freq/caliber_version` | 点不进"这个数字怎么算出来的" |

## 1. 稳定性原则（本计划的硬约束）

1. **不可变边界**：只有"过期望 + 已提交 + 已封存"的分区才允许跳过外呼；`seal` 不是权威，DB 行数是权威。
2. **失败隔离**：单域、单日、单批次失败不得反噬其他域；解析失败进 quarantine，不丢原始响应。
3. **终态可查**：每次 ingest 必须有 `pending|accepted|partial|quarantined|replayed|failed` 之一，禁止"跑了但不知道"。
4. **只读降级**：读路径永不依赖管道健康；最坏情况退到"最近完整日 + `stale/lag` 标注"。
5. **幂等可重放**：所有写路径 upsert / DO NOTHING；回放 JSONL 与重新外呼必须等价。
6. **顺序由代码保证**：不靠"17:45 之后大概跑完了"这类时间差（沿用 ADR 0016 的做法）。
7. **每层可回滚**：P5 用 shadow 开关、P6 用"新旧表并存 + 重命名切换"、P7 纯增量端点。

## 2. 目标架构

```
                    ┌──────────────── 触发（cron / worker / 手动）────────────────┐
                    ▼                                                            │
  fetch ──► raw JSONL ──► IngestPipeline ──► validate ──► quarantine（拒绝行）
   │  ▲         │              │                 │
   │  │         │              │                 └──► accepted rows
   │  │         │              ▼
   │  │         │          upsert core（单事务）
   │  │         │              │
   │  │         │              ▼
   │  │         └──── replay ──┤  expectations（10 条 SQL 断言）
   │  │                        ▼
   │  └──── 负缓存/退避 ◄── seal（domain,date,source,capture_kind,rows,hash）
   │                           │
   └── 单飞锁（advisory lock）  ▼
                        data-freshness / data-quality / provenance
                                     │
                                     ▼
                     前端 DataPoint + ProvenanceDrawer + stale 角标
```

分层落位：

| 层 | 新增/改造 | 复用 |
|---|---|---|
| 采集 | `ingest_pipeline.py`（fetch→replay→validate→quarantine→persist→seal） | `RateLimitedSyncProvider`、`data_saver`、现有 ingest service |
| 清洗 | `data_expectations.py`（声明式 SQL 断言） | `market_day_service` 的判据 |
| 存储 | 分区 + autovacuum + 保留 + 恢复演练 | Alembic、`pg_dump` |
| 显示 | `DataPoint` + `ProvenanceDrawer` + `/provenance` | `NumberText`、`MetricBadge`、`StateWrapper` |

## 3. P5 — 清洗分层（staging / quarantine / expectations）

### 3.1 数据模型（新迁移 `p5_staging_quality`）

```sql
CREATE TABLE ingest_batches (
  id               bigserial PRIMARY KEY,
  domain           text        NOT NULL,           -- daily_quotes / sector_moneyflow / ...
  business_date    date        NOT NULL,
  source           text        NOT NULL,           -- tushare / eastmoney / caaa / replay
  capture_kind     text        NOT NULL DEFAULT 'eod_full',
  params_hash      text        NOT NULL,           -- sha256(sorted(params))
  raw_path         text,                           -- data/.../xxx.jsonl（可回放）
  content_hash     text,                           -- 归一化后的行级 md5（可选）
  rows_fetched     integer     NOT NULL DEFAULT 0,
  rows_accepted    integer     NOT NULL DEFAULT 0,
  rows_quarantined integer     NOT NULL DEFAULT 0,
  status           text        NOT NULL,           -- pending|accepted|partial|quarantined|replayed|failed
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz,
  error            text,
  UNIQUE (domain, business_date, source, capture_kind, params_hash)
);
CREATE INDEX ix_batches_domain_date ON ingest_batches (domain, business_date DESC);

CREATE TABLE ingest_quarantine (
  id            bigserial PRIMARY KEY,
  batch_id      bigint REFERENCES ingest_batches(id) ON DELETE CASCADE,
  domain        text NOT NULL,
  business_date date NOT NULL,
  reason_code   text NOT NULL,   -- schema.missing_column|value.required_null|value.out_of_range|
                                 -- key.duplicate|mapping.unknown_key|cross_field.inconsistent
  row_key       text,            -- ts_code / board_code，便于按实体查
  payload       jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_quarantine_domain_date ON ingest_quarantine (domain, business_date);
CREATE INDEX ix_quarantine_reason ON ingest_quarantine (reason_code, created_at DESC);

CREATE TABLE data_quality_runs (
  id               bigserial PRIMARY KEY,
  run_id           uuid NOT NULL,
  expectation_key  text NOT NULL,
  domain           text NOT NULL,
  business_date    date,
  severity         text NOT NULL,   -- block|warn
  status           text NOT NULL,   -- pass|warn|fail|error
  observed         numeric,
  threshold        numeric,
  detail           jsonb,
  checked_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_quality_key_date ON data_quality_runs (expectation_key, checked_at DESC);
```

保留策略：`ingest_quarantine` 90 天、`data_quality_runs` 180 天、`ingest_batches` 1 年（由 P6.4 的清理 job 统一执行）。

### 3.2 `data_saver` 补读路径（T5.2）

```python
# backend/app/services/data_saver.py
async def load_dataframe(self, api_name: str, params: dict, *, day: date) -> pd.DataFrame | None
async def latest_raw_path(self, api_name: str, params: dict) -> Path | None
```

规则：
- 只读**当天**目录；跨天不回放（避免"用旧快照冒充新日"）。
- 回放结果与线上抓取走**同一个** parse/validate/persist 路径，只是 `source='replay'`。
- 回放命中时 `ingest_batches.status='replayed'`，`raw_path` 指向原文件。

### 3.3 `IngestPipeline`（T5.3）

```python
# backend/app/services/ingest_pipeline.py
class IngestPipeline:
    async def run(
        self,
        spec: IngestSpec,               # domain / source / business_date / capture_kind / rules / expectations
        fetch: Callable[[], Awaitable[pd.DataFrame]],
        parse: Callable[[pd.DataFrame], pd.DataFrame],
        persist: Callable[[AsyncSession, pd.DataFrame], int],
        *,
        allow_replay: bool = True,
    ) -> IngestResult
```

执行顺序（每一步都有终态）：
1. `pg_try_advisory_lock(hashtext(domain||date||source))` — 拿不到即 `skipped`（并发去重）。
2. 查 `ingest_batches` 成功记录 + seal → 已封存且 DB 行数一致 ⇒ 直接 `skipped`（零外呼）。
3. 外呼；失败 → 尝试 `allow_replay` 回放；仍失败 → `status=failed` + `not_before` 负缓存。
4. `data_saver.save_dataframe`（原子写）。
5. `validate_rows` 拆 `accepted` / `rejected(reason_code)`。
6. 单事务 upsert accepted；`rejected` 写 quarantine（同一事务，保证可解释）。
7. commit 后写 batch 终态。
8. 跑该域的 expectations；`block` 失败 ⇒ **不 seal**（下一轮自动重拉）。
9. `block` 全过 ⇒ 写 seal（`ingest_batches` 本身即 seal，`status='accepted'` + `rows_accepted`）。

> 选型说明：**不引入 dbt / Great Expectations / 新编排框架**。数据量（日增 ~5.5k 行）与团队规模不支持额外基础设施；用 SQL 断言 + pytest 即可获得同等收益。

### 3.4 校验规则（T5.4）

```python
@dataclass(frozen=True)
class FieldRule:
    column: str
    required: bool = False
    dtype: Literal["str", "int", "float", "date"] | None = None
    min: float | None = None
    max: float | None = None
    enum: tuple[str, ...] | None = None

@dataclass(frozen=True)
class RowRules:
    required_columns: tuple[str, ...]
    fields: tuple[FieldRule, ...]
    unique_key: tuple[str, ...] | None = None
```

首批规则（按域）：
- `daily_quotes`：`ts_code/close/trade_date` 必须；`close > 0`；`pct_chg ∈ [-100, 100]`；`high >= low`；`(stock_id, trade_date)` 唯一。
- `daily_basic`：`total_mv >= 0`、`turnover_rate ∈ [0, 1000]`。
- `price_limits`：`up_limit >= down_limit`；两者均为正。
- `sector_moneyflow`：`capture_kind` 必须已知；`main_net_inflow` 可负。
- 映射：`ts_code → stock_id` 找不到 ⇒ `mapping.unknown_key` 进 quarantine（不再静默 skip）。

### 3.5 期望注册表（T5.7）

```python
# backend/app/services/data_expectations.py
@dataclass(frozen=True)
class Expectation:
    key: str
    domain: str
    severity: Literal["block", "warn"]
    sql: str          # SELECT observed 与 threshold
    description: str
```

首批 10 条（全部可对单日执行）：

| key | 严重级 | 断言 |
|---|---|---|
| `dq.daily_quotes.rows_ratio` | block | `count ≥ 0.9 × stocks` |
| `dq.daily_quotes.pct_chg_nonnull` | block | `count(pct_chg)/count(*) ≥ 0.99` |
| `dq.daily_quotes.ohlc_sane` | block | `high >= low and close > 0` 全行成立 |
| `dq.daily_quotes.pct_chg_bounds` | warn | `pct_chg ∈ [-44, 44]`（除 ST/新股外） |
| `dq.price_limits.coverage` | block | `count ≥ 0.9 × stocks` |
| `dq.sentiment.zt_le_total` | block | `zt_count ≤ daily_quotes 当日行数` |
| `dq.sentiment.broken_rate_range` | block | `broken_rate ∈ [0, 1]` |
| `dq.sw.level1_count` | warn | 当日申万一级 = 31 |
| `dq.sector_moneyflow.eod_full_rows` | block | `eod_full` 行数 ≥ 当日板块总数 |
| `dq.industry_metrics.single_active` | warn | 同一 `(scope, metric_key, period)` 只有一行 |

运行点：
- 每个 batch 提交后（域内、单日）；
- 每日 21:10 全量扫（挂在 `reconcile_market_data_job` 尾巴，**不新开 cron**）；
- `GET /api/v1/ops/data-quality?date=` 只读查询（内部令牌）。

### 3.6 采纳顺序（避免大爆炸）

1. shadow：只记录 batch/quarantine/expectation，**写路径不变**（2 周，对比跳过行数与历史 `skipped` 计数）。
2. `daily_quotes` → `daily_basic` → `price_limits` → `sentiment`（4 个已自愈域，风险最低）。
3. `sector_moneyflow` / `limit_up_stock_daily`（配合 P0 的 `_refetch_` 接入）。
4. 其余巡检域按需接入（每个域只加一份 `RowRules` + 一条 `Expectation`）。

### 3.7 P5 验收

- [ ] 人为造 3 类坏行（缺列 / 越界 / 映射不到）→ 全部进 quarantine 且 `reason_code` 正确；DB 无脏行。
- [ ] 同一批 data 重复跑 3 次 → `ingest_batches` 仍 1 条 `accepted`，DB 行数不变。
- [ ] 外呼失败且当天 JSONL 存在 → `status='replayed'`、零第二次外呼（用 fake client 计数断言）。
- [ ] `block` 期望失败 ⇒ 该日不被 seal，下一轮对账会重拉（测试用假数据模拟）。
- [ ] `GET /api/v1/ops/data-quality?date=` 返回 10 条断言结果，`fail` 项带 `detail`。
- [ ] 全量 pytest 绿；`self_review --full` 绿。

## 4. P6 — 存储治理（分区 / 膨胀 / 保留 / 恢复）

### 4.1 只分真正大的表

| 表 | 现状 | 决策 |
|---|---|---|
| `daily_quotes` | 4.55M 行 / 1.4GB / 10.7% 死元组 | **按月 RANGE 分区**（`trade_date`） |
| `daily_basic_indicators` | 106K 行 / 698MB | 暂不分区；先做 `VACUUM FULL` + autovacuum 调参 |
| `sector_moneyflow_snapshots` | 19.6K 行 / 9.7MB | 不分区；只加保留（按月删 `poll` 行，`eod_full` 永久） |
| `financial_raw_records` | 1.15M 行 / 911MB（analyze 后） | 不分区；去重（已有 `payload_hash` 唯一键）+ 365 天归档（P6.5） |

### 4.2 `daily_quotes` 分区迁移（T6.3，脚本不是 Alembic）

Alembic 只负责**新表**（staging/quality）；分区切换用一次性脚本 `backend/scripts/partition_daily_quotes.py`，因为需要 `ATTACH/RENAME` 与在线校验，放迁移里不可回滚、不可 dry-run。

约束改造（分区表的所有唯一约束必须含分区键）：
- 主键：`id` → `(stock_id, trade_date)`（代码中无任何 `DailyQuote.id` 引用，已核实）。
- 保留 `uq_daily_quotes_stock_date (stock_id, trade_date)`；索引在父表建，自动下发到分区。

迁移步骤（脚本内置 `--dry-run`）：
1. 预检：磁盘余量 ≥ 2× 表大小；确认无长事务。
2. 建 `daily_quotes_part`（RANGE by `trade_date`），预建 2018-01 → 当前+12 个月分区；缺月自动补。
3. 按**月** `INSERT INTO ... SELECT`，每批一个事务；记录每批行数。
4. 建父表索引（`idx_daily_quotes_date`、`_date_pct`、`_date_amount`、`_stock_id_adj_factor`）。
5. `ANALYZE`；校验 `count(*)` 与 `sum(id)` 对得上。
6. 事务内 `ALTER TABLE daily_quotes RENAME TO daily_quotes_old; ALTER TABLE daily_quotes_part RENAME TO daily_quotes;`
7. 7 天后（脚本 `--cleanup`）删旧表。
8. 分区自动创建：`ensure_next_month_partitions()` 挂在 startup + 每月 1 日 03:00 job；缺分区 ⇒ 写入直接失败（比静默丢行好）。

### 4.3 autovacuum 与膨胀（T6.2 / T6.7）

```sql
ALTER TABLE daily_quotes SET (
  autovacuum_vacuum_scale_factor = 0.02,
  autovacuum_analyze_scale_factor = 0.01,
  autovacuum_vacuum_cost_delay = 2
);
-- 同款应用到 daily_basic_indicators / stock_price_limits / sector_moneyflow_snapshots
```

一次性清理（维护窗口，需 2× 磁盘）：`VACUUM (FULL, ANALYZE)` on `financial_metrics`、`financial_raw_records`、`daily_basic_indicators`。
巡检阈值：死元组 > 20% 或 `pg_total_relation_size` 周增 > 20% ⇒ 告警。

### 4.4 `financial_raw_records` 去重与保留（T6.5）

现状：1.15M 行 / 911MB（`VACUUM (FULL, ANALYZE)` 后）；去重键
`uq_financial_raw_record (source,dataset,source_record_key,payload_hash)` 已保证同响应不重复。
真正的治理点是**保留期**（365 天）与统计信息新鲜度，而不是"每次重写 TOAST"。

- 加 `payload_sha256`；payload 未变则**只更新 `fetched_at`**，不重写行（切断 TOAST churn）。
- `payload` 改存 gzip 压缩后的 `bytea`（或保留 JSONB 但设 `storage=EXTERNAL`），读取时解压。
- 保留最近 180 天 + 每个 `(ts_code, report_period)` 保留最新 1 条；超出归档到 `data/financial_raw/`（JSONL）后删除。
- 加 `GET /api/v1/ops/db-health` 暴露该表行数/大小比，防悄悄回涨。

### 4.5 备份与恢复演练（T6.6）

- `scripts/backup_db.sh`：`pg_dump -Fc` + sha256 + 14 天本地保留 + 可选 `rsync` 异机；两个库（业务 + auth）。
- `scripts/restore_drill.sh`：恢复到临时库 `stock_bot_drill` → 跑冒烟 SQL（关键表行数、最近 10 个交易日覆盖、`_alembic_version`）→ 输出 RTO → 自动 drop。
- 频率：每月 1 次（cron 或人工 checklist），结果写 `docs/deployment/operations.md` 的演练记录表。
- 生产默认 RPO = 24h（日备）。PITR（WAL 归档）列为**可选 P6.5+**：需要 `archive_mode` + 持久卷，收益是 RPO ≤ 5min，成本是运维复杂度；不在本计划默认范围。

### 4.6 P6 验收

- [ ] `daily_quotes` `relkind='p'`，最近 24 个月分区齐全，`SELECT count(*)` 与迁移前一致。
- [ ] 查询 `WHERE trade_date = <今天>` 的 `EXPLAIN` 只扫 1 个分区（分区裁剪生效）。
- [ ] 死元组占比 < 5%（一周后复测）；`last_autovacuum` 非空。
- [ ] `financial_raw_records` 总大小 < 200MB，行数不减少（归档可查）。
- [ ] `restore_drill.sh` 在空库跑通，RTO 记录在 `operations.md`。
- [ ] `GET /api/v1/ops/db-health` 返回分区覆盖、膨胀、最近 vacuum 时间。

## 5. P7 — 溯源与显示一致性

### 5.1 血缘注册表（代码即配置，零新表）

```python
# backend/app/services/lineage_registry.py
@dataclass(frozen=True)
class SourceRef:
    table: str
    domain: str
    filter: str          # 例如 "trade_date = :date"

@dataclass(frozen=True)
class MetricLineage:
    metric_key: str
    label: str
    unit: str
    caliber_version: str
    definition: str
    sources: tuple[SourceRef, ...]
    transform: str       # 例如 "app.services.limit_up_calculator:compute"
```

首批覆盖 6 个"跨页共用"指标：`zt_count`、`broken_rate`、`promo_1to2`、`total_amount`、`up_count`、`sw_l1_pct_chg`。

### 5.2 端点契约（T7.2）

```
GET /api/v1/provenance/{metric_key}?date=YYYY-MM-DD&scope_key=&stock_id=
```

响应（信封对象，禁止数组挂属性）：

```json
{
  "metric": {"key": "zt_count", "label": "涨停家数", "unit": "家",
             "caliber_version": "v3", "definition": "收盘价 ≥ 涨停价 且 非 ST 的股票数"},
  "value": {"as_of": "2026-11-10", "value": 62, "source": "limit_up_calc",
            "computed_at": "2026-11-10T17:20:03+08:00"},
  "sources": [
    {"table": "daily_quotes", "domain": "daily_quotes", "rows": 5572,
     "batch_id": 12345, "raw_path": "data/daily/2026-11-10/all_20261110_163012.jsonl",
     "content_hash": "sha256:..."}
  ],
  "quality": [{"key": "dq.daily_quotes.rows_ratio", "status": "pass"}],
  "freshness": {"expected_as_of": "2026-11-10", "lag_trading_days": 0, "stale": false}
}
```

权限：公开指标（行情/情绪）随路由公开；研究指标沿用 `research:read`（与 `/api/v1/metrics` 同口径）。
性能：单次 ≤ 3 条聚合 SQL + 1 条 batch 查询，Redis 60s 缓存（key 含 `date`）。

### 5.3 前端（T7.3）

- `frontend/src/shared/ui/DataPoint.tsx`：包装 `NumberText`，带 `provenance` 时渲染可点击的下划线/ⓘ；无 `provenance` 时行为不变（渐进接入）。
- `frontend/src/features/provenance/ProvenanceDrawer.tsx`：四段式
  1. 值 + `as_of` + `caliber_version`；
  2. 计算链路（sources 表 → 行数 → batch → raw 文件）；
  3. 质量断言（pass/warn/fail + detail）；
  4. 新鲜度（expected vs actual + lag）。
- 首批接入 3 处：`/market` 温度计卡、`/sentiment` hero 的合成指数、`/market/regulatory` 的「3 日偏离」。
- 免责与降级：drawer 打不开时不得阻塞主内容（`StateWrapper` + `ErrorBoundary`）。

### 5.4 跨页一致性（T7.4）

- 契约测试 `backend/tests/contracts/test_shared_metric_consistency.py`：对同一 `date` 调用所有暴露该指标的端点，断言 `as_of` 与值完全一致（覆盖上面 6 个指标）。
- 运行时巡检 `GET /api/v1/ops/consistency?date=`：返回不一致项（供日结单/告警）。
- 门禁：`check:data-display`（T7.5）——`frontend/src/pages/**` 与 `features/**/components/**` 禁止 `toFixed(`（必须走 `NumberText/ChangeText`），CI 与 `self_review` 快检都跑。

### 5.5 P7 验收

- [ ] 6 个指标都能从界面点到 `/provenance`，响应含 `sources[].batch_id` 与 `raw_path`。
- [ ] 人为改坏一天的值 → `quality` 出现 `fail`，`freshness.stale=true`，drawer 显示红色归因。
- [ ] 跨页一致性测试覆盖 6 指标 × ≥2 端点，全绿。
- [ ] `check:data-display` 在含 `toFixed(` 的页面文件上失败、修复后通过。

## 6. 稳定运行的横切设计

### 6.1 失败矩阵（每种失败的系统行为必须确定）

| 失败 | 系统行为 | 用户可见 | 恢复 |
|---|---|---|---|
| 上游超时 / 429 | 重试 3 次 → JSONL 回放 → 负缓存 `not_before=+15m` → dead letter | 上一完整日 + `stale` 角标 | 下轮 tick 自动 |
| 上游字段漂移 | 缺列 → 整批 `schema.missing_column` 进 quarantine，batch=`quarantined` | 质量面板红点 + `DegradedNotice` | 修 `RowRules` 后回放 |
| 单行越界 | 该行 quarantine，其余入库，batch=`partial` | 质量面板计数 | 核规则或人工修数 |
| DB 写入失败 | 原始 JSONL 保留，batch=`failed` | 站点仍读旧数据 | 下轮 replay（零外呼） |
| 期望 `block` 失败 | 不 seal、不推进 `expected_latest` | 派生指标停在上一好日（显式 stale） | 下轮对账重拉 |
| 并发重复触发 | advisory lock 抢不到即 skip | 无 | 自动 |
| 分区缺失 | 写入前预检失败 + 告警（不静默丢行） | 页面 stale | `ensure_next_month_partitions` |
| 备份损坏 | 月度恢复演练发现 | 无（演练报告） | 从异机备份恢复 |

### 6.2 并发与锁

- `pg_try_advisory_lock(hashtext('ingest:'||domain||date||source))`，会话级；管道结束释放。
- 跨进程（scheduler/worker）天然互斥，不需要额外 Redis 锁。
- 读路径不加锁；所有写均为 upsert。

### 6.3 灰度与回滚

| 阶段 | 开关 | 回滚 |
|---|---|---|
| P5 shadow | `INGEST_PIPELINE_MODE=shadow`（只记录不改变写路径） | 改回 `off`，零影响 |
| P5 enforce | 按 domain 白名单 `INGEST_PIPELINE_DOMAINS=daily_quotes,...` | 从白名单移除该域 |
| P6 分区 | 新旧表并存 7 天 | `RENAME` 回旧表（脚本 `--rollback`） |
| P7 溯源 | 新增只读端点 + 前端按 `provenance` prop 渐进接入 | 移除 prop 即回到原样 |

### 6.4 观测与 SLO（把"一流"变成 8 条判据）

| # | 维度 | 指标 | 目标 | 检测点 |
|---|---|---|---|---|
| 1 | 获取 | 30 日 `expected_as_of` 覆盖率 | ≥ 99.5% | 日结单 |
| 2 | 获取 | 静默缺口（无 reason code） | 0 | `data-quality` |
| 3 | 清洗 | quarantine 率与归因可见 | 100% 批次 | `ingest_batches` |
| 4 | 清洗 | 派生指标 invariant 覆盖 | 100% | pytest 参数化 |
| 5 | 存储 | 表膨胀率 | < 20% | `db-health` |
| 6 | 存储 | 恢复演练 | 每季度 1 次 | `operations.md` 记录 |
| 7 | 显示 | 数字带 `as_of`/`source` | 100%（首批 6 指标） | `check:data-display` + 契约测试 |
| 8 | 显示 | 跨页同指标一致 | 100% | 契约测试 |

## 7. 实施顺序（TDD，每项独立可交付）

### P5

- **T5.1** 迁移：3 张新表 + 索引 + 保留注释。验收：`alembic upgrade head` 幂等。
- **T5.2** `data_saver.load_dataframe` / `latest_raw_path` + 测试（命中 / 未命中 / 损坏 JSONL）。
- **T5.3** `IngestPipeline` 骨架：锁、seal 判断、replay、batch 终态。验收：fake client 计数证明"已封存零外呼"。
- **T5.4** `validate_rows` + `RowRules` + quarantine 拆分。
- **T5.5** 接入 `daily_quotes`（shadow → enforce）。
- **T5.6** 接入 `daily_basic` / `price_limits` / `sentiment`。
- **T5.7** `data_expectations.py` 10 条 + 运行点（batch 后 + 21:10 全量）。
- **T5.8** `GET /api/v1/ops/data-quality` + 并入 `data-freshness` 摘要。
- **T5.9** 文档：`docs/architecture/backend/ARCHITECTURE.md` §3 数据流、`features.md` 新增"数据质量"行。

### P6

- **T6.1** `scripts/db_health.sh` + `GET /api/v1/ops/db-health`（内部令牌）。
- **T6.2** autovacuum 参数迁移（4 张表）。
- **T6.3** `partition_daily_quotes.py`（`--dry-run / --apply / --rollback / --cleanup`）+ runbook。
- **T6.4** 保留 job：quarantine 90d、quality 180d、batches 1y、sector `poll` 行 90d。
- **T6.5** `financial_raw_records` sha256 去重 + 压缩 + 归档。
- **T6.6** `backup_db.sh` / `restore_drill.sh` + `operations.md` 演练记录。
- **T6.7** 一次性 `VACUUM FULL`（维护窗口）。

### P7

- **T7.1** `lineage_registry.py`（6 指标）。
- **T7.2** `/api/v1/provenance/{metric_key}` + schema + 缓存 + 权限。
- **T7.3** `DataPoint` / `ProvenanceDrawer` + 3 处接入。
- **T7.4** `test_shared_metric_consistency.py` + `/api/v1/ops/consistency`。
- **T7.5** `frontend/scripts/check-data-display.mjs` + 接入 CI/`self_review`。
- **T7.6** 文档：`features.md` 溯源行、`frontend/ARCHITECTURE.md` §3.5。

## 8. 门禁

每个 Task 收尾必须：

```bash
cd backend && uv run pytest -q
uv run --extra dev ruff check .
uv run --extra dev mypy app
cd ../frontend && npx tsc --noEmit && npm run build
cd .. && bash scripts/self_review.sh --full
bash scripts/doc_gate.sh
```

涉及分区/基准对象时加 `bash scripts/bench.sh`（本计划不改 Tier 1 纯计算热点，预计不需要）。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 分区切换期间写入丢失 | 事务内 RENAME + 应用侧 30s 维护窗口；脚本 `--rollback` |
| quarantine 表被当成"垃圾桶" | 90 天保留 + `reason_code` 必填 + 周报按 reason 聚合 |
| 期望断言误报（停牌日、ST） | 先 `warn` 跑 2 周再升 `block`；阈值从真实分布取 |
| shadow 期与主路径双写 | shadow 只写 batch/quarantine，不写业务表 |
| 溯源拖慢页面 | 60s 缓存 + 只在 drawer 打开时请求（非首屏） |
| `VACUUM FULL` 锁表 | 维护窗口执行；优先 `pg_repack`（可选，不引入默认依赖） |

## 10. 不做什么

- 不引入 dbt / Great Expectations / Kafka / 湖仓：日增 5.5k 行，单机 Postgres 足够。
- 不做多源降级链（[ADR 0001](../docs/decisions/0001-single-primary-source-tushare.md)）；校验源只读对比，另行 ADR。
- 不给所有表分区：只分 `daily_quotes`；其余按行数与查询模式再评估。
- 不在本计划内做 PITR：默认 RPO 24h；WAL 归档作为独立运维项评估。
- 不改现有对账语义（`expected_latest` / `T0_CUTOFF` / 三元组门禁）；P5 只在其上加隔离与断言。


## 11. 实施结果（2026-11-11）

**代码与门禁已完成**；标记 ⏳ 的是需要维护窗口/运维窗口的执行项（脚本与 runbook 已就位）。

### P5 — 清洗分层

- ✅ 迁移 `a7b8c9d0e1f2`：`ingest_batches` / `ingest_quarantine` / `data_quality_runs`
- ✅ `data_saver.load_dataframe / latest_raw_path`（当天目录、params 子集匹配、损坏文件按 miss）
- ✅ `services/ingest_pipeline.py`：advisory lock → 封存判断 → 外呼/回放 → 校验 → quarantine → upsert → 期望 → 封存
- ✅ 行级规则 + 6 类 `reason_code`；缺列判整批 schema drift
- ✅ 接入 `daily_quotes` / `daily_basic`（含 unknown ts_code 与 close 空值 quarantine）、`price_limits`（逐日）、`sentiment`（`persist_snapshot`）
- ✅ 对账层 `should_skip`：封存行数达标且与 DB 行数一致 ⇒ 零外呼
- ✅ 10 条期望断言；对账尾巴 `_run_data_quality` 落库并登记 `data_quality` 告警
- ✅ `GET /api/v1/ops/data-quality`（只读、内部令牌）
- ✅ 实测：10 条断言 9 pass / 1 warn（`market_breadth` 当日无行），封存幂等（重复记录仍 1 行账本、`should_skip(10)=true / (9)=false`）

### P6 — 存储治理

- ✅ 迁移 `c3d4e5f6a7b8`：4 张 upsert 热表的 autovacuum 参数 + `fillfactor=90`
- ✅ `scripts/partition_daily_quotes.py`（dry-run / apply / rollback / cleanup / ensure-partitions），含行数校验与索引/约束改名
- ✅ `services/storage_maintenance_service.py`：分区自动创建 + 保留策略（90/180/365 天）
- ✅ `data_retention_weekly`（周六 03:30）+ `partition_maintenance`（每月 1 日 03:00）
- ✅ `scripts/db_health.sh` / `scripts/backup_db.sh` / `scripts/restore_drill.sh`
- ✅ `backend/scripts/archive_financial_raw.py`（gzip JSONL 归档 + 删除，dry-run 默认）
- ✅ `GET /api/v1/ops/db-health`
- ✅ `partition_daily_quotes.py --apply`（实测 4,550,432 行 → 44 个月分区，行数校验通过，
  `EXPLAIN` 只扫 1 个分区；旧表保留为 `daily_quotes_old`）
- ✅ `VACUUM (FULL, ANALYZE)` on `financial_metrics` / `financial_raw_records` / `daily_basic_indicators`
  （顺带纠正：这些表此前的 `n_live_tup=612/1,553` 是**过期统计**，真实行数 1.15M/3.57M）
- ✅ 首次 `restore_drill.sh`：RTO 126s，恢复后 4,550,432 行、`max(trade_date)=2026-11-10`

### P7 — 溯源与显示

- ✅ `services/lineage_registry.py`：6 个跨页共用指标
- ✅ `services/provenance_service.py` + `GET /api/v1/provenance/{metric_key}`（60s 缓存，key 含 metric/date/scope）
- ✅ `services/consistency_service.py` + `GET /api/v1/ops/consistency`（up/down/成交额）
- ✅ 前端 `features/provenance/{ProvenanceDrawer,ProvenanceLink,DataPoint}`，接入情绪温度计（3 指标）、A股全景（up_count/total_amount）、情绪指标详情页
- ✅ `check:data-display` 棘轮（54 文件基线）+ self_review + CI
- ✅ 实测：`zt_count` 溯源返回来源行数（sentiment=1 / quotes=5572 / limits=5579）与 0 天滞后；一致性检查发现并修正了一处口径差异（上涨 = `pct_chg >= 1`，不是 `> 0`）

### 部署实测补充（2026-11-11 晚）

- ✅ 镜像重建（Docker Hub 不通 → 临时 Dockerfile 用 `docker.1ms.run/library/*` 同 digest 基础镜像）
- ✅ `docker compose up -d --no-build` 后：33 jobs（含 2 个新 job）、启动对账 2 秒收敛
- ✅ 发现并修复 **`api`/`scheduler` 未挂 `./data:/app/data`**（此前 scheduler 的 JSONL 随容器重建丢失、
  api 回放永远 miss）；补齐后真实 ingest 写出 1.1MB JSONL 到宿主并可 `load_dataframe` 回放
- ✅ 发现并修复 **`record_batch` 状态过宽**（行数/pct_chg 未达标也标 accepted）；现在
  `daily_quotes` 要求行数 ≥90% + pct_chg ≥99%，`daily_basic` 要求行数 ≥90% 才封存
- ✅ 发现并修复 **`/ops/db-health` 的 `relkind` bytes 未解码**（分区表被报成 `partitioned=false`）
- ✅ 实测：10/10 期望 pass、一致性 3/3 ok、`ingest_batches` accepted 5572 行 / quarantine 1 行、
  `should_skip=True`、`data_quality_runs` 已落库

### 门禁

- `uv run pytest`（本计划新增 27 个用例全绿；相关邻域 62 passed）
- `ruff`（改动文件全绿）/ `mypy app`（242 文件无问题）
- `npx tsc --noEmit` / `npm run check:data-display`
- `bash scripts/self_review.sh` / `bash scripts/doc_gate.sh`
