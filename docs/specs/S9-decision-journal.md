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

快照**元数据键**（`version` / `as_of` / `quote_as_of` / `symbol` / `missing_reason`）
之外，`s9.r2`（S9.7 Phase A）新增两个键，同样**创建时固化、事后不可改**：

| 键 | 形状 | 说明 |
|---|---|---|
| `bench_spec` | `{bet_type, kind, industry_code, industry_name, fallback}` | 基准口径（spec §S9.7.3）：`kind ∈ {industry_equal_weight, hs300}` |
| `bench_basis_note` | `string` | 上者的人话说明（后端下发，前端不硬编码） |

`s9.r1` 的历史行没有这两个键：读取方按列上的 `bet_type`（迁移给了默认 `direction`）
兜底，**不得改写历史快照**（快照是"当时怎么定的"的证据）。

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
  - Phase A 追加（spec §S9.7.2/§S9.7.6/§S9.7.7，**既有字段类型不变**）：
    `by_kind{...}`（五 `kind` 条数，合计 = `n`）· `avg_net_excess_pct`（净口径，排除 `pass`）·
    `net_cost_bps` · `share_ci{discipline_break,win}`（Wilson 95%）· `multiple_comparison_note`；
    `avg_excess_pct` 同样**排除 `pass`**；`discipline_break_share` 仍是 `number|null`
  - `evaluation` 追加：`net_outcome_pct` / `net_excess_pct` / `bench_basis_note` / `bench_fallback`
  - Phase B1 追加（spec §S9.7.1/§S9.7.5/§S9.7.8，**只加字段**）：条目响应 `position_size` / `confidence` /
    `reviewable`；`evaluation` 再加 `invalidation_observable`（`reviewable=false` 时复盘端点 422）
  - Phase B2 追加（spec §S9.7.1/§S9.7.4/§S9.7.5/§S9.7.9，**只加字段**）：`evaluation` 再加路径四指标
    `mae_pct` / `first_touch_invalidation` / `touched_then_reversed` / `days_to_invalidation`；
    `stats` 再加 `by_size` / `avg_weighted_excess_pct` / `by_kind_median` /
    `calibration{n,bins,brier}` / `calibration_note` / `export_hint`；新增
    `GET /api/v1/journal/export?format=json|csv`（`Content-Disposition: attachment`，只含本人，
    CSV = UTF-8 BOM + 固定列序）
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
- 计算失败**不得阻断创建**：`peer_history` 降级为 `self=null / market=null`，并在 `notes` 写明原因
  （`no_stats_product` / `pipeline_error` / `stale_guard`）。
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

### S9.6.6 实现与离线产物（**2026-10-19 已落地**）

- **已落地** `backend/app/services/peer_history_service.py`：`bucket_of(snapshot)` · `self_history(db, user_id, bucket, before)` ·
  `market_history(bucket, snapshot_day)`；分桶常量 `SIMILARITY_BUCKETS`（边界 20/40/60/80、±2、±5，`unknown` 单列）
- **已落地** `backend/scripts/build_peer_history_stats.py` → `backend/app/services/peer_history_stats.json`
  （12 个月、按 45 桶聚合、交易日历对齐 + 复权 + winsorize ±50%，与既有 `triage_rule_stats` **同口径**；
  超额 = 个股 T+N 收益 − 当日全市场中位；样本 = 桶内「股票 × 交易日」，两次运行**逐字节一致**）
- **已落地** `POST /journal` 响应与 `snapshot.peer_history` 新增 `peer_history`（快照版本 `s9.r3`；不影响既有字段）
- **已落地** 前端：创建成功后的结果卡片 `features/journal/components/PeerHistoryCard.tsx`（无新路由、无新 tab）
- **隐私口径（实现时写死）**：L2 产物只由行情/行业/情绪三张表算出，**不含任何用户日志**
  （spec §S9.7.9 不做跨用户聚合）；L1 只查本人。
- **降级**：算不出来 → `notes=["pipeline_error"]`（创建照旧 201）；产物缺失 → `notes=["no_stats_product"]`；
  产物 `as_of >= 快照日` → `notes=["stale_guard"]` 且 `market=null`（PIT）
- 查询预算：`self_history` 一次查询（用户自己的日志），L2 读进程内产物（0 查询）；
  `build_snapshot` 的「≤5 条」契约不受影响（`peer_history` 在它之外算）

### S9.6.7 验收判据

- 行为：分桶边界（19.9/20/40/60/80/80.1、板块 ±2、MA20 ±5）逐值落桶确定（单测表驱动）
- 行为：**PIT 对照** —— 候选集加入未来条目 → 输出不变；L2 `as_of ≥ 快照日` → 拒绝并 `notes=stale_guard`
- 行为：L1 `n=4` 显示"样本不足"；`n=5` 显示比例；L2 `n=29` 不给数字、`n=30` 给
- 行为：`self` 与 `market` 分开展示（e2e 断言两块标题各自存在，且不存在合并的"胜率"）
- 数据契约：`snapshot.peer_history.version` 必填；`wrong_by` 合计 = `wrong`
- 行为：`peer_history` 计算失败时创建仍成功（201）且 `notes` 有原因
- 门禁：离线脚本可复跑、两次产物逐字节一致；`PEER_HISTORY_VERSION` 变更必须同时改 spec

---

## S9.7 专业口径补强（仓位 · 净成本 · 基准 · 路径 · 信心 · 选择偏差 · 统计质量 · 隐私）

**为什么有这一节**：S9.1–S9.6 能回答"我错在哪"，但回答不了"**我为什么错、下次怎么改**"。
2026-10-18 owner 以「十年投研的人会怎么挑」审了一遍，本节把挑出来的口径**先写死**（仍不含实现）。

### S9.7.1 仓位与风险单位（不记 size，收益无法解释）

- 新增可选字段 `position_size ∈ {none, light, normal, heavy}`（默认 `none` = 只观察不下单）与 `risk_note`（≤100 字，写"这笔最多能亏多少"）。
- **已落地（Phase B1，2026-10-19）**：`position_size` **落列**（`NOT NULL DEFAULT 'none'` + `ck_journal_position_size`，
  迁移只加列、不搬数据），创建请求可传（越界 **422**），列表/详情响应带出；`risk_note` 与
  `by_size` / 加权超额统计留 Phase B2。
- **已落地（Phase B2，2026-10-19）**：`stats.by_size`（四档**判断条数**，合计 = `n`）与
  `stats.avg_weighted_excess_pct`（按上表 `size_weight` 加权，`none` 权重 0 → **完全退出分母**，
  不是乘 0 后混进均值）。加权的是**毛口径 `excess_pct`**（与 `avg_excess_pct` 同一口径；净口径
  看 `avg_net_excess_pct`），复盘表只存毛超额，净口径在展示层减 12bp。`risk_note` 仍未做。
- 统计口径：`weighted_outcome = outcome_pct × size_weight`，`size_weight = {none:0, light:0.5, normal:1, heavy:2}`。
- **`none` 的条目依然参与"判断对错"统计**（那是决策质量），但**不参与收益统计**（`weighted_*` 与"实际盈亏"类字段一律排除）。
- 不做：真实成交价/股数/手续费明细（属 Wave 6 交易执行），v1 只到粗档。

**验收**：`position_size` 越界 422；`none` 条目在收益统计里被排除（用例断言 `weighted_outcome` 的 n 少 1）；`by_kind`/`by_size` 分栏自洽。

### S9.7.2 净成本（毛收益会让所有统计虚高）

- 常量 `COST_ROUND_TRIP_BPS = 12`（佣金双边 5bp + 印花税卖出 5bp + 过户费/滑点 2bp，**保守估计**），写在 registry 常量里并注明日期与假设。
- **已落地（Phase A，2026-10-19）**：常量在 `backend/app/services/journal_service.py` 的常量区（含假设表与日期），
  配对常量 `JOURNAL_STATS_VERSION`（Phase B2 起 `s97.b1`：`share_ci.win` 分母排除 `pass` 是区间口径变化；
  改成本/口径必须同时改它与本 spec）；净口径由
  `evaluation.net_outcome_pct` / `evaluation.net_excess_pct` / `stats.avg_net_excess_pct` / `stats.net_cost_bps` 下发。
- `net_outcome_pct = outcome_pct - COST_ROUND_TRIP_BPS / 100`；`net_excess_pct = net_outcome_pct - bench_pct`。
- 统计与展示**默认用净口径**，毛口径只在"口径说明"里出现。
- 不做：按持有天数分档的成本、税费差异（v2 再说）。

**验收**：`net = gross - 0.12`（用例逐值断言）；改常量必须同时改 spec 与统计产物版本。

### S9.7.3 基准必须匹配「赌的是什么」

| `bet_type` | 用户的意思 | 基准 |
|---|---|---|
| `direction`（默认） | 赌这个行业/标的会涨 | 所属**申万一级等权平均收益**（无行业 → 沪深300） |
| `relative` | 赌这只**跑赢同行** | 同行业等权平均收益 |
| `market` | 市场级判断（无 symbol） | 沪深300 |

- `bet_type` 在创建时选定并写入快照（`snapshot.bench_spec`），事后不可改。
- **诚实标注**：我们用的是"同行业成分股等权平均收益"，**不是**申万一级指数行情（我们没有指数日线）；差异在 `bench_basis_note` 里写清，不假装是指数。
- **已落地（Phase A）**：`bet_type` 落列（`decision_journal_entries.bet_type`，`NOT NULL DEFAULT 'direction'` + `ck_journal_bet_type`），
  同时写 `snapshot.bench_spec` + `snapshot.bench_basis_note`；`bench_fallback` 只在"要行业基准却拿不到行业归属"时为 `true`
  （显式 `market` 的沪深300 是用户选的，不算退化）。

**验收**：三种 `bet_type` 各自命中正确基准（用例断言 `bench_pct` 与手工计算一致）；`bench_spec` 落快照；缺失行业时退化到沪深300 并标 `bench_fallback=true`。

### S9.7.4 路径分析（终点对错之外，还要看"中途发生了什么"）

- 新增派生指标（不新表，复盘时算）：`mae_pct`（入场到到期期间的最大不利偏移）· `first_touch_invalidation`（首次触达失效条件的日期）· `touched_then_reversed`（**先触失效、到期时反而盈利**）· `days_to_invalidation`。
- 归因辅助：若 `touched_then_reversed = true` 且 `verdict = thesis_wrong` → 在复盘弹窗里提示：「你先触发了失效条件，之后才反弹——这更像**纪律**问题」。
- 不做：日内路径（我们只有日线）、盘中止损模拟。
- **已落地（Phase B2，2026-10-19）**：`evaluation` 新增四字段，全部**到期时现算、不新表**：
  - `mae_pct` = `(区间最低价 / snapshot.price − 1) × 100`，**夹到 ≤ 0**（最低价高于入场价 = 没有
    不利偏移 → 0；正数是另一个指标 MFE，不在这里出）；
  - `first_touch_invalidation` = 区间 `(入场交易日, due_on]` 内首个满足 `price_below`（`low ≤ 阈值`）/
    `price_above`（`high ≥ 阈值`）的**交易日**；未触达 = `null`。该列由评估的同一条批 SQL 逐日比较
    得出（不按天再查库）；
  - `days_to_invalidation` = 入场日之后到首次触达日的**交易日数**（该标的的 K 线根数，不是自然日）；
  - `touched_then_reversed` = 有触达 **且** 到期 `net_outcome_pct > 0`；缺任一证据一律 `false`
    （不猜"发生过"）。
  `date_only` 形态：到期即命中，不存在"先触达后反弹"→ `first_touch_invalidation` /
  `days_to_invalidation` 为 `null`、`touched_then_reversed` 为 `false`；`mae_pct` 仍是有效的路径
  事实（照算）。市场级判断（快照无 `price`）→ `mae_pct = null` + `missing_reason`。

**验收**：四指标逐值断言（构造"先跌破后反弹"序列 → `touched_then_reversed=true`）；`first_touch_invalidation` 只落在到期日之前。

### S9.7.5 事前信心与校准（零新数据里最强的反馈）

- 新增字段 `confidence ∈ {1,2,3,4,5}`，映射固定概率 `{1:0.2, 2:0.35, 3:0.5, 4:0.65, 5:0.8}`（写在常量里，不可在实现时"优化"）。
- **已落地（Phase B1，2026-10-19）**：`confidence` **落列**（int，**可空、无默认值** —— NULL = 当时没填，
  不参与校准，给默认值会把没填的人算进校准样本）+ `ck_journal_confidence`（1..5）；创建请求可传
  （越界 **422**），响应带出；`calibration` / `brier` 统计留 Phase B2。
- **已落地（Phase B2，2026-10-19）**：`stats.calibration = {n, bins[{confidence,p,n,hit_rate,sufficient}],
  brier}`。`p` 取上面的**固定**映射（`CALIBRATION_P`，源码注明不可优化）；`hit_rate` 与 `brier` 一律按
  **`net_excess_pct > 0`** 判对（`excess = 0.12` 恰在成本线上 → 不算对）；每档 `sufficient = n >= 10`，
  `n = 0` 的档**不出现**；总 `n` = 已复盘且填了 `confidence` 的条数，`n < 20` → `calibration = null` +
  `calibration_note`（带当前条数）。`brier` 由每档 `(n, hits)` 闭式求和（`p` 档内恒定），不把 N 条记录搬到 Python。
- `calibration`：按信心档分组，算**实际净超额 > 0 的比例**与样本数；`brier = mean((p - I[net_excess>0])**2)`。
- 展示纪律：每档 `n ≥ 10` 才显示该档；总样本 `n < 20` 不给曲线，只显示"样本不足"。
- **不把校准当作"能力评价"**：文案固定为"你的信心与实际结果的对照"，不出现"你高估自己"这类判断。

**验收**：`confidence` 越界 422；校准分档样本 <10 该档不显示；`brier` 用 `net_excess>0` 而非毛收益（PIT 对照：改成本常量 → brier 变）。

### S9.7.6 选择偏差（只记"做了"不记"没做"，胜率天然虚高）

- `kind = pass` 升为**一等公民**：表单在"标的"旁提供「我决定不动」按钮（一键把 `kind` 设为 `pass`，仍要求 3 必填）。
- 统计新增 `by_kind`：`watch / buy / sell / pass / hold` 各自的条数与净超额中位。
- **每周提示**（页面一行，不推送）：若近 30 天 `buy|watch` ≥ 3 且 `pass` = 0 →
  「你还没记录过放弃的判断；只记买入会让胜率偏高。」（一次性、可关闭）
- **已落地（Phase A）**：`stats.by_kind` 只给条数（合计 = `n`）；收益统计（`avg_excess_pct` /
  `avg_net_excess_pct` / `share_ci.win` 的分母）按契约 §4 排除 `pass`。`by_kind` 的**净超额中位**未做
  （契约 §4 未定义该字段），留 Phase B。
- **已落地（Phase B2，2026-10-19）**：新增 `stats.by_kind_median`（**不动** `by_kind` 的纯数字形状）：
  每类 `{count, median_net_excess_pct}`，`count` 是**中位数的样本数**（已复盘的条数），中位净超额 =
  中位毛超额 − 12bp；`pass` 有自己的一栏（它也是判断），但总体的 `avg_*` 仍排除 `pass`。
  **裁定 2**：`share_ci.win` 的分子与分母都排除 `pass`（分母 = 已复盘且 `kind != pass`；
  全是 `pass` 时 0/0 → `null`，不是 100%）。
- 不做：强迫记录、扣分、社交比较。

**验收**：`pass` 条目计入判断统计、不计入收益统计；提示只在条件满足时出现一次；`by_kind` 合计 = `n`。

### S9.7.7 统计质量（点估计 + 小样本 = 误导）

- 所有比例**必须带 Wilson 95% 区间**（`wilson_interval(successes, n)`，`n=0` 时返回 `null`）。
- 展示纪律：`n < 5` 不给比例（沿用 S9.6）；`n ≥ 5` 时必须同时给区间（例如「4/6（95% 区间 30%–90%）」）。
- 统计页固定标注：「**未做多重比较校正**：按 45 个桶检索时，总会有桶看起来显著」。
- **已落地（Phase A）**：`journal_service.wilson_interval(successes, n)`（`z = 1.96`，`n = 0 → null`）；
  `stats.share_ci = {discipline_break, win}` 各下发 `{n, successes, ci_low, ci_high}`，
  固定文案 `stats.multiple_comparison_note` 由后端原样下发。
- 不做：显著性检验、p 值、排序榜单。

**验收**：Wilson 边界值表驱动（0/0→null、1/1、4/6≈[0.30,0.90]、5/5）；展示层 `n<5` 无百分比。
**验收**：`discipline_break_share` 等比例字段同时返回 `{n, successes, ci_low, ci_high}` 对象（不再只给一个数）。

### S9.7.8 可证伪性闸（写不出可观测的失效条件 = 这条判断不可复盘）

- `invalidation` 只接受**可观测**形态：`price_below` / `price_above` / `date_only`（日期本身可观测）。
- 自由文本式失效条件（"逻辑破坏"）**不收**：`422` + 指导文案（"请给一个价格或日期，或改用备注写理由"）。
- 新建条目若无 `invalidation` 的可观测值 → 允许创建但标记 `reviewable: false`（到期只显示结果，不给归因入口），并在列表上显示"不可复盘"。
- **已落地（Phase B1，2026-10-19）**：入参闸在 `app/schemas/journal.py`（白名单 `INVALIDATION_KINDS` +
  指导文案 `INVALIDATION_GUIDANCE`；自由文本 → **422 且带"怎么改"**，不是只报 `union_tag_invalid`）；
  条目响应新增 `reviewable`，`evaluation` 新增 `invalidation_observable`（三形态 `true`，
  历史行里的不可识别形态 `false`）；`reviewable=false` 时 `POST /journal/{id}/review` → **422**
  （`NotReviewable`，明确文案"失效条件不可观测"），但**结果照常展示**。历史行**不改写**（闸只影响
  读取判断与复盘动作）。

**验收**：自由文本失效条件 422；`reviewable=false` 的条目在到期列表里**没有**"复盘"按钮（e2e 断言）。

### S9.7.9 隐私底线（日志是"投资体检报告"，泄露=灾难）

- 只有作者可见（沿用）；**不做**任何跨用户聚合/排行/分享。
- **导出**：`GET /api/v1/journal/export?format=json|csv`（含快照与复盘，一次性下载）。
- **删除权的取舍（明写）**：单条**不可删除**（否则"复盘记录可被篡改"，与 S9 的不可篡改哲学冲突）；账号删除时**级联删除**全部日志。
  页面必须原文告知：「日志不提供单条删除，以保证复盘记录不可篡改；如需清除请删除账号」。
- 不做：加密存储、审计日志（v2；先靠"只有作者可读"这一条）。
- **已落地（Phase B2，2026-10-19）**：`GET /api/v1/journal/export?format=json|csv`（`json` 默认），
  200 + `Content-Disposition: attachment`；查询带 `user_id`、**不分页不截断**（与列表页的 `LIST_LIMIT`
  无关）；内容 = 条目字段 + `snapshot`（原样）+ `review`（含现算的 `net_excess_pct`）。
  CSV = **UTF-8 BOM**（Excel 中文不乱码）+ 固定列序
  `id,symbol,kind,bet_type,position_size,confidence,thesis,invalidation,horizon_days,created_at,due_on,status,outcome_pct,bench_pct,excess_pct,net_excess_pct,verdict`，
  缺失值是空串（不是 0）；`stats.export_hint = "/api/v1/journal/export?format=csv"`。
  `format` 越界 → **422**（不静默退回 json）。

**验收**：导出端点返回本人全部条目且**不含**他人数据（用户隔离用例）；页面有上述告知原文；`grep` 无跨用户查询路径。

### S9.7.10 落地顺序（若全部批准）

`S9.7.6（pass/选择偏差）→ S9.7.2（净成本）+ S9.7.3（基准）→ S9.7.5（信心校准）→ S9.7.4（路径）→ S9.7.7（Wilson）→ S9.7.8（可证伪闸）→ S9.7.1（仓位）→ S9.7.9（导出/隐私）`

理由：前两项决定"统计可不可信"；第 3 项决定"用户会不会坚持写"；后四项是精度与底线。

## S9 整体验收判据（含 S9.7 口径批准后的追加项）

- 数据契约：仓位/信心/赌注类型（S9.7.1/5/3）落库为列或快照字段，越界 422
- 行为：统计默认**净口径**（S9.7.2）；基准按 `bet_type` 选择并写 `bench_spec`（S9.7.3）
- 行为：比例一律带 Wilson 区间（S9.7.7）；`pass` 分栏（S9.7.6）；不可观测失效条件 422 或 `reviewable=false`（S9.7.8）
- 行为：导出只含本人数据（S9.7.9）



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
