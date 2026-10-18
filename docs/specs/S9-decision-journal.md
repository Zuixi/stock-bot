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

---

## S9.6 同类历史（peer history）—— 让"记录"立刻换回信息

**为什么有这一节**：S9.1–S9.5 只在**到期时**给回报，写完那一刻用户什么都没得到——这是留存杀手。
本节让创建响应**立刻**返回两段事实：**你在同类情形下的历史** 与 **全市场同桶的历史分布**。

**这一节只定义口径**（owner 2026-10-18：「先设计口径再写」）。实现排期见 §S9.6.6。

### S9.6.1 相似的三个维度（不多不少）

只用快照里**当时就存在**的 3 个字段分桶，避免"事后才有的信息"混进来：

| 维度 | 来源字段 | 桶 |
|---|---|---|
| 市场情绪 | `snapshot.sentiment_percentile` | `<20` · `20–40` · `40–60` · `60–80` · `≥80` · **`unknown`**（null） |
| 板块方向 | `snapshot.sector_flow_state.pct` | `≤−2%` · `(−2%, 2%)` · `≥2%` · **`unknown`**（无行业/null） |
| 位置 | `snapshot.ma20_dist_pct` | `<−5%` · `[−5%, 5%]` · `>5%` · **`unknown`**（null） |

- 组合桶 = `(情绪, 板块, 位置)`，上限 5×3×3 = **45** 个（`unknown` 单列，不与其他值合并）。
- 分桶常量放代码 `SIMILARITY_BUCKETS`（边界值 20/40/60/80、±2、±5），**改动必须 bump `PEER_HISTORY_VERSION`**。
- **只按桶相等匹配**：不做距离、不做加权、不做聚类（避免"看起来很像"的伪相似）。
- **标的**：用户历史层**优先同标的**（`same_symbol`），不足再退到同桶任意标的；两层分别标注来源。

### S9.6.2 两层样本与阈值

| 层 | 来源 | 最少样本 | 不足时 |
|---|---|---|---|
| L1 你自己的历史 | 该用户**已复盘**的日志（`reviewed_n`） | **5** | 只显示"同类情形下你只有 N 条，样本不足（<5）"，**不给比例** |
| L2 全市场同桶 | 离线统计产物 `peer_history_stats.json`（12 个月、同口径） | **30** | 该桶只显示"样本不足"，**不给数字** |

- L1 与 L2 **必须分开呈现**，不得合并成一个"胜率"（用户历史是自我参照，全市场是背景，混在一起就是误导）。
- L1 的"对/错"用 `verdict` 分类：`thesis_wrong` / `timing_wrong` / `discipline_break` → 错；`right_for_wrong_reason` 单列"结果对但理由错"；`luck` 单列"不可归因"；未复盘不计入。
- L2 只给**统计量**（同桶 5/20 日超额中位与样本数），不给"对错"。

### S9.6.3 PIT 纪律（本节最容易写错的地方）

- L1 **只能使用 `created_at < 当前条目 created_at` 且已复盘**的条目——**不得包含未来**，否则等于自己泄露自己。
- L2 的统计窗口必须在**当前条目的快照日之前**结束（离线产物带 `as_of`，请求时若 `as_of >= 快照日` 则拒绝使用并标 `stale_guard`）。
- 对照实验（必测）：把一条**未来**的 reviewed 日志塞进候选集，输出**必须不变**；把 L2 的 `as_of` 调到快照日之后，必须拒绝。

### S9.6.4 计算结果必须落进不可变快照

`POST /journal` 的响应新增 `peer_history`，并**同时写进该条目的 `snapshot.peer_history`**：

```json
{ "version": "ph-v1", "computed_at": "2026-10-18T10:00:00+08:00",
  "bucket": { "sentiment": "20-40", "sector": "below_-2", "ma20": "neg_5" },
  "self": { "same_symbol": {"n": 2, "right": 1, "wrong": 1},
            "same_bucket": {"n": 6, "right": 4, "wrong": 2,
                            "wrong_by": {"timing_wrong": 2}} },
  "market": { "as_of": "2026-09-30", "n": 812, "t5_excess_med": -0.42, "t20_excess_med": -1.10 },
  "notes": [] }
```

- **落库意义**：以后算法或数据变了，用户当时看到的"同类历史"仍可复现（与本模块"不可篡改"同一条哲学）。
- 计算失败**不得阻断创建**：`peer_history` 写 `null` 并在 `notes` 写明原因（`no_stats_product` / `pipeline_error`）。
- `notes` 里也必须写明"该统计只描述过去，不预测结果"。

### S9.6.5 展示契约（一句话 + 一个比例 + 一个指向）

前端在创建成功后原样展示三段，不得改写：

> **同类情形**（情绪 30–40 分位 · 电子 −1.8% · 距 MA20 −3.2%）
> 你自己：6 条 → 4 对 / 2 错，**错的 2 次都是"时机错"**
> 全市场同桶（截至 9 月 30 日，812 条）：5 日超额中位 −0.42%
> *以上只描述过去，不预测结果。*

- **必须**显示 `wrong_by` 的构成（"错在哪"比"错几次"有用）。
- 样本不足时只显示"样本不足"那句 + 已有什么（不显示 0、不显示百分比）。
- 不出现判断词（沿用 S8 禁用词表）。

### S9.6.6 实现与离线产物（**排期未定，等 owner 批准本口径**）

- 新增 `backend/app/services/peer_history_service.py`：`bucket_of(snapshot)`、`self_history(db, user_id, bucket, before)`、`market_history(bucket)`
- 新增离线脚本 `build_peer_history_stats.py`（**待建**，落 `backend/` 脚本目录）→ 产物 `peer_history_stats.json`
  （12 个月、按 45 桶聚合、交易日历对齐 + 复权 + winsorize ±50%，与既有 `triage_rule_stats` **同口径**）
- `POST /journal` 响应与快照新增 `peer_history`（不影响既有字段）
- 前端：创建成功后的结果卡片（无新路由、无新 tab）

### S9.6.7 验收判据

- 行为：分桶边界（19.9/20/40/60/80/80.1、板块 ±2、MA20 ±5）逐值落桶确定（单测表驱动）
- 行为：**PIT 对照** —— 候选集加入未来条目 → 输出不变；L2 `as_of ≥ 快照日` → 拒绝并 `notes=stale_guard`
- 行为：L1 `n=4` 显示"样本不足"；`n=5` 显示比例；L2 `n=29` 不给数字、`n=30` 给
- 行为：`self` 与 `market` 分开展示（e2e 断言两块标题各自存在，且不存在合并的"胜率"）
- 数据契约：`snapshot.peer_history.version` 必填；`wrong_by` 合计 = `wrong`
- 行为：`peer_history` 计算失败时创建仍成功（201）且 `notes` 有原因
- 门禁：离线脚本可复跑、两次产物逐字节一致；`PEER_HISTORY_VERSION` 变更必须同时改 spec

## S9 整体验收判据

- 数据契约：**仅 2 张新表**（`decision_journal_entries` / `decision_journal_reviews`）· 无新 cron
- 行为：3 行必填（缺一 422）· 创建后不可改（无 PATCH/PUT/DELETE）
- 行为：快照落库且不随行情变化；缺数用 `null + missing_reason`
- 行为：到期 → 自动结果 → 归因（必选）→ `stats` 自洽
- 行为：创建响应含 `peer_history`（S9.6），且**写入不可变快照**；失败不阻断创建（`notes` 有原因）
- 门禁：`self_review --full` · `doc_gate` · Playwright（表单三态、到期列表、归因五选一、样本不足文案）

## 明确推迟

- 持仓成本/盈亏（Wave 6 交易执行）· 决策与 triage 提醒的自动关联（需要 S8 历史留痕 v2）
- 富文本/附件/评论/模板库 · 团队共享与对比 · 推送通道
- 任何"推荐下一步动作"的能力（产品边界：只记录与归因，不给建议）
