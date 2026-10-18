# S9 决策日志与归因（Decision Journal）

> 交付序 **S9**。计划：[plans/2026-10-18-decision-journal.md](../../plans/2026-10-18-decision-journal.md)。
> **定位**：工具型 —— 用户写下**自己的判断**，系统自动附快照、到期算结果、把"错"分类。**不构成投资建议**，也不评价用户"该不该买"。
> **为什么做**：2026-10-18 owner 判断「用户最需要的是把历史经验沉淀出来、优化自己的交易系统」。全库唯一的用户自由文本是 `user_watchlist_items.notes`，`/trade` 是占位页——**"经验沉淀"零代码**。这是四个需求里唯一会**复利**的一层，且**不需要任何新外部数据源**。

---

## S9.1 入口与页面

**范围**：做完之后，用户能在 30 秒内记录一次判断（3 行必填），并在到期时被迫面对结果。

**交付物**
- 前端 `/me?tab=journal`（新增 Tab，**不新增路由**）+ 顶部「待复盘 N 条」
- `/me` 的 journal tab 内三块：**记一笔**（表单）/ **待复盘**（到期未复盘）/ **已复盘**（含归因标签与统计）

**不做什么**：不做富文本/评论/模板库/导出 · 不做买卖记账（持仓成本/盈亏计算属交易执行 Wave 6）· 不做推送（v1 仅页面角标）

**契约**
- 表单**恰好 3 个必填字段**：`thesis`（≤200 字）· `invalidation`（二选一：价格阈值 / 日期）· `horizon_days ∈ {7,30,90}`
- 选填：`symbol`（留空 = 市场级判断）· `kind ∈ {watch, buy, sell, pass, hold}`（默认 `watch`，**只作归档标签，不参与计算**）
- 创建后 `thesis / invalidation / horizon_days / symbol` **不可修改**（接口层不提供 PATCH/PUT/DELETE）
- 列表按 `status` 分：`open`（未到期）/ `due`（到期待复盘）/ `reviewed`
- 每行固定展示：标的或"市场"、thesis 首行、到期日、快照摘要（1 行）

**依赖**：S9.2/S9.3（数据与快照）· `/me` 既有结构
**验收判据**
- 行为：3 个必填缺一 → `422`，且前端提交按钮在缺字段时禁用（不是提交后报错）
- 行为：创建成功后立刻能在 `open` 列表看到，且**快照已落库**（不是前端拼的）
- 行为：到期日（`created_at + horizon_days`）当天起出现在 `due`；未到期不得出现
- 门禁：`/me` 其余 tab（`watchlist/tags/triage`）行为不变（e2e 断言）

**风险**：用户不写 → 由 S9 整体的 kill criteria 兜底（2 周 <5 条即停）。

---

## S9.2 数据模型（2 张表，append-only）

**范围**：做完之后，判断与复盘都可追溯、不可篡改。

**交付物**
- Alembic 迁移：`decision_journal_entries` + `decision_journal_reviews`（**仅这 2 张**）
- 模型 `backend/app/models/journal.py`

**不做什么**：不建"持仓/成本/盈亏"表 · 不做软删除 · 不做多用户可见（默认只有作者能看）

**契约**
```sql
decision_journal_entries(
  id             bigserial primary key,
  user_id        uuid not null,
  symbol         varchar(16),                    -- NULL = 市场级判断
  kind           varchar(16) not null default 'watch',
  thesis         text not null,
  invalidation   jsonb not null,                 -- {"kind":"price_below","value":38.0} | {"kind":"price_above",...} | {"kind":"date_only","value":"2026-12-31"}
  horizon_days   int not null,
  snapshot       jsonb not null,                 -- 创建时的市场快照（S9.3），不可变
  created_at     timestamptz not null default now(),
  due_on         date not null,                  -- created_at::date + horizon_days（写入时固化，便于索引）
  check (char_length(thesis) between 1 and 200),
  check (horizon_days in (7, 30, 90)),
  check (invalidation ? 'kind' and invalidation ? 'value'),
  check (kind in ('watch','buy','sell','pass','hold'))
);
create index on decision_journal_entries (user_id, due_on);
create index on decision_journal_entries (user_id, created_at desc);

-- ★ append-only：只有 INSERT；一次复盘一条，不可改
decision_journal_reviews(
  id             bigserial primary key,
  entry_id       bigint not null references decision_journal_entries(id),
  reviewed_at    timestamptz not null default now(),
  outcome_pct    numeric(10,4) not null,         -- 绝对收益 %
  bench_pct      numeric(10,4) not null,         -- 基准（沪深300 或所属申万一级）%
  excess_pct     numeric(10,4) not null,
  invalidation_hit boolean not null,
  verdict        varchar(24) not null,           -- thesis_wrong | timing_wrong | discipline_break | right_for_wrong_reason | luck
  note           text,
  unique (entry_id),
  check (verdict in ('thesis_wrong','timing_wrong','discipline_break','right_for_wrong_reason','luck'))
);
```

**验收判据**
- 数据契约：迁移**只新增 2 张表**（`grep create_table` = 2）
- 数据契约：同一条目重复复盘被 `unique(entry_id)` 拒绝（第二次写入报冲突而非静默覆盖）
- 数据契约：`thesis` 超 200 字 / `horizon_days` 不在 {7,30,90} / `verdict` 越界 → DB 拒绝
- 门禁：路由清单里**没有** journal 的 PATCH/PUT/DELETE（`grep` 可验）

---

## S9.3 自动快照（用户一个字都不用填）

**范围**：做完之后，每条判断都自带"当时的市场状态"，事后不用回忆。

**交付物**：`backend/app/services/journal_service.py` 的 `build_snapshot(db, symbol, as_of)`（**只读、单次最多 5 条查询**）

**契约（快照字段，全部落 `snapshot` jsonb）**
| 字段 | 来源 | 缺失时 |
|---|---|---|
| `price` / `pct_chg` | `daily_quotes` 最近完整日 | `null` + `missing_reason` |
| `ma20_dist_pct` | 同上（收盘距 MA20 的 %） | `null` |
| `sentiment_percentile` | 情绪综合指标分位（`industry_metrics` market scope，取最近值） | `null` + `missing_reason` |
| `sector_flow_state` | 所属申万一级当日涨跌/资金流状态（行业口径） | `null` |
| `events` | 复用 S8 探测器（该标的当日命中，最多 3 条 `{detector, headline}`） | `[]` |
| `valuation_pct` | `pe_ttm` 近 120 日区间位置（%） | `null` |

**不做什么**：不在快照里放大段文案 · 不存原始 K 线 · 不做跨用户对比

**验收判据**
- 行为：快照在**创建时**写入，之后不随行情变化（重跑不会改）
- 行为：任一来源缺数 → 该字段 `null` + `missing_reason`，**不得用 0 代替**
- 行为：查询次数 ≤5 且与条目数无关（反 N+1）
- 门禁：快照字段名由 schema 固定（新增字段必须同时更新 spec 与测试）

---

## S9.4 结果与归因

**范围**：做完之后，到期条目自动算出结果，用户必须选一个"错在哪"，长期能看到自己最常犯的错。

**交付物**：`evaluate_entry`（自动）· `POST /api/v1/journal/{id}/review`（归因）· `GET /api/v1/journal/stats`

**契约**
- 自动计算（到期后、复盘前即可展示）：
  - `outcome_pct` = 标的从 `snapshot.price` 到 `due_on` 最近收盘的涨跌幅；无 `symbol`（市场级）→ 用沪深 300
  - `bench_pct` = 沪深 300 同期（有 `symbol` 且属申万一级 → 用该行业指数平均；无 → 沪深 300）
  - `excess_pct = outcome_pct - bench_pct`
  - `invalidation_hit`：`price_below` / `price_above` 用 `due_on` 前是否触达判定；`date_only` 到期即为 `true`
- 归因（**用户必选**，5 枚举）与可选 `note`（≤500 字）
- `stats`：`{n, reviewed_n, by_verdict{...}, by_attribution{...}, avg_excess_pct, discipline_break_share}`
- **不写不归因**：未选 `verdict` 不得写入（`422`）；`reviewed` 后条目移到"已复盘"，不可再评

**验收判据**
- 行为：归因五枚举之外 → 422；缺 `verdict` → 422
- 行为：同一 entry 二次复盘 → 409（不是静默覆盖）
- 行为：`stats.by_attribution` 合计 = `reviewed_n`（算术自洽）
- 行为：**PIT**：结果只用 `snapshot.price` 之后的数据；把起始价改成复盘日价格 → 测试必须变红
- 门禁：`discipline_break_share` 在无样本时为 `null`（不是 0）

**风险**：归因主观 → 不追求"客观正确"，只要求"用户自己的长期分布"；`right_for_wrong_reason` 单列，避免"赚了就是对的"。

---

## S9.5 合规与防滥用

**契约**
- 固定免责（页面底部）：**"决策日志记录的是你自己的判断，不构成投资建议；结果统计不代表未来表现。"**
- 产品文案禁用词沿用 S8 的词表（`backend/scripts/triage_compliance.py`）；**用户自由文本不参与扫描**（那是用户的内容），但页面**不得**对用户文本做任何"推荐/看好"的加工
- 不展示"胜率排行榜" · 不做用户间对比 · 不做自动交易关联

**验收判据**
- 门禁：页面与接口返回的**产品文案**0 禁用词命中（用户输入的中文标题不算）
- 行为：`stats` 只在 `reviewed_n >= 5` 时展示占比，否则显示"样本不足（<5 条）"

---

## S9 整体验收判据

- 数据契约：**仅 2 张新表**（`decision_journal_entries` / `decision_journal_reviews`）· 无新 cron
- 行为：3 行必填（缺一 422）· 创建后不可改（无 PATCH/PUT/DELETE）
- 行为：快照落库且不随行情变化；缺数用 `null + missing_reason`
- 行为：到期 → 自动结果 → 归因（必选）→ `stats` 自洽
- 门禁：`self_review --full` · `doc_gate` · Playwright（表单三态、到期列表、归因五选一、样本不足文案）

## 明确推迟

- 持仓成本/盈亏（Wave 6 交易执行）· 决策与 triage 提醒的自动关联（需要 S8 历史留痕 v2）
- 富文本/附件/评论/模板库 · 团队共享与对比 · 推送通道
- 任何"推荐下一步动作"的能力（产品边界：只记录与归因，不给建议）
