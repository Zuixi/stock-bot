# S2 信号 · 因子 · 回测 · 提醒（Wave 1.5）

> 本文件是**契约**，不是排期。执行清单见主线计划 §7 Wave 1.5。

**为什么单独成 Wave**：这套东西不在 10 个导航里，但被 **7 处**消费（情绪极端值预警 · 领先指标筛选 · 宏观政策预期差 · 历史同类政策回测 · 公司风险红线 · 策略买卖信号 · 风控止损动作）。若不先建，会被复制 7 遍，**而口径不一致会直接摧毁产品可信度**。

**但"先建"不等于"建全"。** 7 处共用的只是**骨架**，不是**功能全集**。本 Wave 只交付骨架 + 打通第一个消费方（情绪分位越界 → 站内提醒），其余按消费方落地顺序增长。

---

## 设计立场（先钉死四件事）

**① 复用既有三件套，不新造轮子。** 本仓库已经跑通这套模式：

| 既有 | 位置 | 复用什么 |
|---|---|---|
| 纯函数规则引擎 | `backend/app/services/cycle_engine.py` | 编写形态：「pure functions, no I/O；规则可读性优先；阈值调整改 registry 而非本文件逻辑」 |
| 规则输出历史表 | `industry_signals`（`backend/app/models/industry_research.py:102`，注释即 *"rules engine output history"*）| 表结构：已有 `signal_type / reason / basis(JSONB) / effective_date`，**`basis` 就是证据链** |
| 代码即配置的 registry | `backend/app/services/industry_registry.py` | 阈值、参考区间、单位、频率的登记方式 |
| 物化因子表 | `stock_features`（`backend/app/models/feature.py`，按 `asof_date` 分区）| 因子的存储与计算方式（`return_percentile` / `max_drawdown` / `trend_slope` 等已存在）|

**② 不引入规则 DSL。** 规则 = Python 纯函数，阈值 = registry 配置。理由：该模式在本仓库已验证；而文本表达式 + 解析器 + 求值器是最贵的一类代码（错一次即静默算错），且它当前要解决的需求数为 **0**。

**③ 不做 `action` 层。** [ADR-4](./S0-platform-contracts.md) 已裁定不下单 → 所谓"动作"（止损/调仓）落到实现上就是**通知 + 一条待办**，与 `alert` 是同一条路径。不为此建只有一个实现的接口。

**④ 引擎不采集数据。** 输入恒为指标表（`industry_metrics`，见 [S1.2](./S1-market-sentiment.md#s12-industry_metrics-扩展与统一读面)）。

---

## S2.1 因子层（**不建表**）

**范围**：做完之后，新增一个因子只是加一段纯函数 + 一条 registry 元数据，不新增表。

**交付物**
- `backend/app/services/indicators.py`：可复用因子函数（分位 / 同比 / 环比 / 回撤 / 均线 / 波动率 …），**复用 `cycle_engine.count_consecutive_negative` 这类既有纯函数**
- `industry_registry` 扩展：因子元数据（`factor_key → {名称, 单位, 输入指标, 窗口, 展示分组}`）
- 需要物化落库的高成本因子 → 复用 `stock_features` 的**物化 + 按 `asof_date` 分区**模式（新增列或新增分区列，不新增表）

**不做什么**
- **不建 `factor_defs` 表**：`transform(pct_change|zscore|percentile|yoy|ma|std)` 的通用执行引擎，是"为不存在的因子写框架"。既有 `stock_features` 直接把结果物化成列，更简单也更快。
- **不建因子库 UI**：Wave 5 才有消费方。

**契约**
- 因子是**纯函数**：`(序列, window, params) → 序列`。有 IO / 副作用的实现违规。
- 低成本的因子**不落库**，读时算（Redis 缓存）；只有昂贵的（长窗口、跨全市场）才物化。
- 因子定义变更必须可复算历史（同一输入必得同一输出，有测试锁定）。

**依赖**：S1.2
**验收判据**
- 数据契约：本 spec **不新增任何表**（迁移文件中无 `create_table`）
- 行为：`percentile` / `max_drawdown` 等复用因子对同一输入可复算，误差 = 0
- 行为：因子定义变更后，用旧定义重算历史仍得原值（不漂移）
- 门禁：纯函数必须有单测（无 DB、无 marker）；改因子计算后跑 `bash scripts/bench.sh`（Tier 1 纯计算热点）

---

## S2.2 规则与信号（**复用 `industry_signals`**）

**范围**：做完之后，新增一个信号规则 = 写一个纯函数 + 在 registry 登记阈值；每次运行落一条带证据链的信号。

**交付物**
- `backend/app/services/signal_rules.py`：规则函数，与 `cycle_engine.py` 同构
- Alembic 迁移：`industry_signals` **加 4 列**（不建新表）
- `backend/app/api/v1/signals.py`：信号查询端点
- registry 扩展：`rule_key → {适用 scope, 阈值, direction, severity, cooldown_days}`

**契约**

`industry_signals` 加列（既有列不动）：
```sql
alter table industry_signals
  add column rule_key     text,        -- 规则标识，对齐 registry
  add column rule_version int,         -- ★ 规则版本快照，历史信号可复算
  add column confidence   text,        -- high | medium | low（★ S6.3 用它映射仓位）
  add column severity     text;        -- info | warn | critical

-- 唯一约束从 (industry_key, effective_date) 扩为：
--   unique (industry_key, rule_key, effective_date)
-- 索引：
create index on industry_signals (severity, effective_date desc);
```

> `ponytail: industry_key 现被当作通用 scope 键（'pig' / 'market' / 'broiler'），语义已泛化。第二次接入非行业 scope 时改名为 scope_key。`

**规则编写形态（示例，须能直接跑通）**
```python
# app/services/signal_rules.py
def sentiment_extreme(ctx: SignalContext) -> RuleResult | None:
    """情绪极端值：综合情绪指数 5 年分位越界。"""
    pct = ctx.percentile("market_sentiment_composite", window=1260)
    if pct is None:
        return None
    if pct > 0.90:
        return RuleResult(direction="bearish", severity="warn",
                          confidence="medium", basis={"percentile": pct})
    if pct < 0.10:
        return RuleResult(direction="bullish", severity="warn",
                          confidence="medium", basis={"percentile": pct})
    return None
```
阈值（0.90 / 0.10）**必须**住 registry，不写在函数里——与 `cycle_engine` 的既有纪律一致。

**PIT 护栏（硬性，本 spec 最关键的一条）**
`basis` 里每个指标引用必须携带 `published_at`，且 **`published_at ≤ effective_date`**。违反 → **信号不生成 + 记告警**，**绝不静默回退到 `period`**。这是防止未来函数的第一道闸。

**不做什么**
- **不建 `signal_rules` 表**：规则是代码（与 `cycle_engine.py` 一致），阈值是 registry（代码即配置）。表驱动的规则引擎需要一个 DSL 才能有用，而 DSL 已被否决。
- **不建 `factor_defs` / `signals` 新表**（见 S2.1 与本节）。

**依赖**：S2.1
**验收判据**
- 数据契约：**不新增表**；`industry_signals` 4 列到位、`unique(industry_key, rule_key, effective_date)` 生效；重跑同日不产生重复信号
- 数据契约：**PIT 护栏测试** —— 构造 `published_at = effective_date + 1d` 的指标，信号**必须不生成**且产生告警
- 行为：`sentiment_extreme` 规则端到端产出信号，`basis` 含分位值与引用来源
- 行为：阈值改动只动 registry，不改 `signal_rules.py`（`grep` 可验）
- 行为：`confidence` 取值受限于 `high|medium|low`
- 门禁：规则纯函数单测；`bash scripts/bench.sh` 绿

---

## S2.3 事件研究回测（最小版）

**范围**：做完之后，能回答"这个规则历史上有效吗"，且答案不会因为偷看未来而虚高。

**交付物**
- Alembic 迁移：`backtest_runs`（**只此一表**）
- `backend/app/services/backtest.py`：纯查询 + 统计
- 回测结果 API

**契约**

```sql
backtest_runs(
  id            bigserial primary key,
  rule_key      text not null,
  rule_version  int  not null,
  scope_type    text not null,
  scope_keys    jsonb not null,          -- 回测样本空间
  window_from   date not null,
  window_to     date not null,
  horizons      int[] not null,          -- [1,5,20,60]
  benchmark     text,                    -- 如 '000300.SH'
  split         jsonb not null,          -- ★ 样本切分，样本外必须非空
  pit_enforced  bool not null default true,
  stats         jsonb not null,          -- 各 horizon 的 均值/中位数/胜率/样本数
  created_at    timestamptz not null default now()
)
```

**逐笔明细不落表**：`backtest_events` 砍掉——逐笔 forward return 是 `industry_signals` + 行情表的一次 join 就能算出来的（4 个 horizon × N 笔，读时算很便宜）。详情页按需查询，不物化。

**硬性约束**
- **PIT 强制**：所有指标按 `published_at` 对齐；`published_at IS NULL` 的指标进入回测 → **报错**，不静默回退到 `period`
- **样本切分强制**：`split` 必填且样本外非空；只展示样本内结果的回测视为无效
- **退市股不得剔除**（幸存者偏差）；分类归属按当日 SCD2 快照（[ADR-3](./S0-platform-contracts.md)）

**本 Wave 不做**（留给 Wave 5 `S6.5`）
- 参数优化、walk-forward 滚动、蒙特卡洛模拟
- 组合级回测（本 Wave 只做**单规则事件研究**：信号出现后 N 日收益 vs 基准）

**验证 PIT 是否真的生效（本 spec 最关键的一条验收）**
> 构造**对照实验**：同一规则分别用 `published_at`（披露日）与 `period`（报告期）对齐，两者统计结果必须产生显著差异。若差异为 0，说明 PIT 未生效（很可能字段没接上）——**这条测试是回测可信度的唯一机械保证。**

**依赖**：S2.2
**验收判据**
- 数据契约：`backtest_runs` 存在；`pit_enforced = false` 的运行结果**不得**出现在任何对外展示路径
- 行为：上述 PIT 对照实验通过（差异 > 0 且方向符合预期）
- 行为：`split` 为空或样本外为空的回测被拒绝保存
- 行为：回测详情可按需列出逐笔（读时查，无物化表）
- 门禁：`bash scripts/bench.sh` 绿（回测统计为 Tier 1 纯计算热点）

---

## S2.4 提醒订阅与触达

**范围**：做完之后，规则命中后用户能在站内收到提醒。

**交付物**
- Alembic 迁移：`alert_subscriptions` + `alerts`
- 触达通道：站内（必做）；邮件（选做）
- 站内通知中心

**契约**

```sql
alert_subscriptions(
  id           bigserial primary key,
  user_id      uuid not null,
  rule_key     text,                    -- NULL = 全部规则
  scope_type   text,
  scope_key    text,
  min_severity text not null default 'warn',
  channel      text[] not null default '{inapp}',
  enabled      bool not null default true,
  created_at   timestamptz not null default now()
)

alerts(
  id             bigserial primary key,
  subscription_id bigint references alert_subscriptions(id) on delete cascade,
  user_id        uuid not null,
  signal_id      bigint not null references industry_signals(id) on delete cascade,
  channel        text not null,
  delivered_at   timestamptz,
  read_at        timestamptz,           -- ★ 已读是用户维度
  unique (subscription_id, signal_id, channel)
)
```

**最小形态（本 Wave 的边界）**
- 每用户一行**默认订阅**（`rule_key IS NULL, min_severity='warn'`），不建订阅管理 UI
- **订阅管理 UI 与按 target 订阅推迟**到出现第 2 个消费方（公司风险红线）时
- 尊重 registry 的 `cooldown_days`，不得绕过

**依赖**：S2.2
**验收判据**
- 数据契约：`unique(subscription_id, signal_id, channel)` 生效 —— 同一信号对同一订阅同一渠道只投递一次（重跑幂等）
- 行为：**端到端跑通**情绪极端值预警：规则 → 信号 → 站内提醒 → 用户可查可标已读
- 行为：`cooldown_days` 生效（冷却期内不产生新提醒）
- 行为：未登录用户不产生 `alerts` 行

---

## S2.5 用户研究假设与自动判定

**范围**：做完之后，用户可以在行业工作台上写下一个带指标与阈值的**可验证假设**，系统在每个交易日数据落库后自动判定、把结论与证据链留痕，并在站内以铃铛角标提醒。

> **为什么在这里**：它与 S2.1–S2.4 是**同一套机制**（纯函数规则 + registry 阈值 + 证据链 + 站内触达），
> 与 S2.2 的差别只在**作者与结论归属**：S2.2 是系统产出的信号，本节是**用户提出的判断**。
> 决策论证见 [ADR 0018](../decisions/0018-hypothesis-immutability-and-pit-verdicts.md)（写下即冻结 / 三态 / PIT）。

**交付物**
- Alembic 迁移：`research_hypotheses` + `research_hypothesis_events` + `user_alert_cursors`（**仅此 3 张**）
- `backend/app/services/hypothesis_engine.py`：**纯函数**判定（无 IO、无时钟注入之外的时间依赖）
- `backend/app/services/hypothesis_service.py`：取数（复用统一读面）+ 判定 + 写事件
- `backend/app/api/v1/hypotheses.py` · `backend/app/api/v1/notifications.py`
- 新增门禁脚本 `check_hypothesis_metrics.py`（落在 backend 侧脚本目录，**具体路径由实施计划钉死**）：条件可选指标集合的门禁脚本
- auth-service 迁移：新增权限 `research:write`
- 前端：`/industry/:key` 工作台的「行情调研追踪」Tab **首个区块** + 投资看板页头入口条 + 顶栏铃铛（复用 `features/nav/CountBadge.tsx`）

**不做什么**
- **不做证伪线**（"若 X 则本轮判断失效"）—— 真实研报最常写的一句，但会让条件模型从单角色变双角色（状态机/UI/测试对称展开），列为 **v2 第一优先项**
- 不做相对变化 / 历史分位条件；不做富文本、评论线程、@提及、假设模板库、批量导入
- **不做编辑**（无 PATCH / PUT / DELETE）；不做邮件通道；不做订阅管理 UI
- 不做个股 scope（`scope_key` 只留形状，v1 恒为行业）

**契约**

```sql
research_hypotheses(
  id                bigserial primary key,
  scope_key         text not null,                 -- v1 恒为行业（'pig'）；形状给 'stock:600519.SH' 留位
  author_user_id    text not null,                 -- 来自 forward-auth 断言，与 user_watchlists 同源
  author_username   text not null,                 -- ★ 冗余留痕：用户表在 auth-service 库，跨库 join 不到
  claim             text not null,                 -- 人能读的断言
  conditions        jsonb not null,                -- ★ 创建后不可改
  deadline          date  not null,                -- ★ 创建后不可改；必须 > 创建日
  status            text  not null default 'open', -- open | satisfied | falsified | withdrawn
  resolved_at       timestamptz,
  resolved_event_id bigint,                        -- → research_hypothesis_events.id（刻意不加 FK：避免与 events 互相引用）
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  check (jsonb_array_length(conditions) between 1 and 3),
  check (status in ('open','satisfied','falsified','withdrawn')),
  check (status = 'open' or resolved_at is not null)
);
create index on research_hypotheses (scope_key, status, deadline);
create index on research_hypotheses (author_user_id, created_at desc);

-- ★ append-only：只有 INSERT，无 UPDATE / DELETE 路径
research_hypothesis_events(
  id             bigserial primary key,
  hypothesis_id  bigint not null references research_hypotheses(id),
  kind           text not null,        -- satisfied | falsified | withdrawn
  as_of          date not null,        -- ★ 判定数据边界（最新可得期）；withdrawn 记撤回当日（仅作唯一键维度，非数据边界）
  readings       jsonb not null,       -- ★ 证据快照，见下
  note           text,                 -- 撤回理由（withdrawn 时必填，应用层校验）
  actor_user_id  text,                 -- 系统判定为 NULL
  actor_username text,
  created_at     timestamptz not null default now(),
  unique (hypothesis_id, kind, as_of)  -- ★ 重跑幂等
);

user_alert_cursors(
  user_id            text primary key,
  last_seen_event_id bigint not null default 0,
  updated_at         timestamptz not null default now()
);
```

`conditions` 形状（1–3 条 AND）：
```json
[{"metric_key":"hog_price","op":"lt","threshold":13.0,"threshold_hi":null,"sustained_periods":5}]
```
`op ∈ {lt,lte,gt,gte,between}`；`between` 用 `threshold`(下界) + `threshold_hi`(上界) 且必 `threshold < threshold_hi`；`sustained_periods ∈ [1,60]`。

**校验边界（一律 422，不得落 500）**：`claim ≤ 200` 字；`deadline` 必须 > 创建日；
同一 `scope_key` 的 `open` 假设 ≤ 50（防刷）；`conditions[].metric_key` 必须 ∈ registry 的 `pig` scope 且**有真实 writer**；
`conditions` 1–3 条、`op` 取值、`between` 顺序与 `sustained_periods` 范围见上。

`readings` 形状（证据链，判定不可变）：
```json
[{"metric_key":"hog_price","op":"lt","threshold":13.0,"value":13.4,"period":"2026-10-15",
  "source":"soozhu","state":"failed","data_lag":false}]
```

**判定语义（本节最关键，全部可机械核对）**
1. `sustained_periods` 是**每条条件各按该指标自己的 `freq` 计**的期数（日频 = 交易日 / 月频 = 月 / 季频 = 季）。
   **绝不用"最后 N 行"** —— 必须用 registry 的 `freq` + 交易日历锚定（10-12 的"薄行"事故是同类病）。
2. 每条条件取 `period >= created_at::date` 且 `period <= as_of` 的**最近 N_i 个可得期**：
   全部满足 → `passed`；可得期 ≥ N_i 且有不满足 → `failed`；**可得期 < N_i → `insufficient_data`**。
3. 假设判 `satisfied` 当且仅当**所有**条件为 `passed`。
4. 到期 = `deadline` **次日**（deadline 当天仍算在期内）；到期时任一条件为 `insufficient_data` →
   **推迟结算**，最多等 **10 个交易日**，超时按现状结算并在 `readings` 标 `data_lag=true`。
5. **期间只可能产出 `satisfied`**；`falsified` 只来自到期未达（ADR 0018 ④）。
6. 撤回由作者或 `users:manage` 持有者发起，`note` 必填，结论计 `withdrawn` 并**计入准确率分母**
   （准确率 = 成立 / (成立 + 到期未达 + 撤回)）。
7. **`industry_data_source == 'mock'` 时不写 `satisfied`/`falsified`**（记告警 + UI 明示"演示数据源不判定"）——
   在演示数据上产出的"结论"比不产出更糟。

**端点**（权限沿用既有依赖注入；读 `research:read`、写 `research:write`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/industries/{key}/hypotheses` | 假设墙列表（含**读时计算**的逐条件进展；`?status=&author=&limit=&cursor=`）|
| `POST` | `/api/v1/industries/{key}/hypotheses` | 建假设（`claim` + `conditions[]` + `deadline`）|
| `GET` | `/api/v1/hypotheses/{id}` | 详情：条件进展 + 事件时间线 |
| `GET` | `/api/v1/hypotheses/{id}/events` | 证据链（append-only，分页）|
| `POST` | `/api/v1/hypotheses/{id}:withdraw` | 撤回（`note` 必填）|
| `GET` | `/api/v1/notifications/summary` | 铃铛：`{unread_count, latest_event_id}` |
| `GET` | `/api/v1/notifications` | 通知列表（v1 只有假设事件）|
| `POST` | `/api/v1/notifications/read` | 推进游标 |

**反 N+1（硬性）**：列表的 DB 查询次数**不得随假设条数增长** —— 一次批量取全部涉及序列后在内存判定。
必须配"断言查询次数"的测试，不靠自觉（本仓有过列表 N+1 被审计抓出的先例）。

**执行挂载（不新开 cron）**
挂 `reconcile_market_data_job` 尾步，**必须排在 `derived_metrics_converge` 之后**（否则会拿薄行当结论）；
PG advisory lock 互斥；无 `open` 假设时零查询直接返回；手动轨道 `MarketDataJobType.hypothesis_evaluate`。
**判定绝不在 GET 里外呼上游**（ADR 0016 同类纪律）。

**与 S2.4 的关系（不得静默并存两套"已读"模型）**
本节的"未读"用 `user_alert_cursors` + 读时计算，**不做 per-user 扇出** ——
业务后端**无法枚举**"全体持 `research:read` 的用户"（用户表在 auth-service 库），扇出要跨服务列用户；
游标方案零跨服务依赖。**S2.4 落地时**：`alerts.signal_id` 必须泛化为 `(source_kind, source_id)`；
届时本游标表**要么**降级为纯读游标保留、**要么**迁移进 `alerts.read_at`，由那次实现裁定并回写本节。

**依赖**：S1.2（统一读面）· S2.4（提醒的目标形态）· [ADR 0011](../decisions/0011-point-in-time-data-contract.md)（PIT）· [ADR 0016](../decisions/0016-derived-metrics-converge.md)（挂载点）· [ADR 0018](../decisions/0018-hypothesis-immutability-and-pit-verdicts.md)

**验收判据**
- 数据契约：迁移**新增 3 张表**（`research_hypotheses` / `research_hypothesis_events` / `user_alert_cursors`），**无其它新表**；`unique(hypothesis_id, kind, as_of)` 生效（同日重跑**新增事件数 = 0**）
- 数据契约：`conditions` 长度与 `status` 取值约束生效（越界插入被 DB 拒绝）；路由表**无**假设的 PATCH/PUT/DELETE（`grep` 可验）
- 行为：**PIT 对照实验** —— 摘掉判定取数的 `period >= created_at::date` 过滤，测试**必须变红**
- 行为：三态语义 —— 可得期 < N 的条件判 `insufficient_data`，且该假设**不得**被结算为 satisfied/falsified
- 行为：迟到数据 —— deadline 已过且某条件最新可得期 < deadline → **不结算**；超 10 个交易日 → 强制结算且 `readings` 含 `data_lag=true`
- 行为：`mock` 数据源下判定**不产出** `satisfied`/`falsified`
- 行为：撤回 `note` 为空被拒（422）；准确率分母含 `withdrawn`
- 行为：反 N+1 —— 假设墙列表的 DB 查询次数不随假设条数增长
- 门禁：`self_review.sh` 的**条件指标集合检查**全绿 —— 条件可选指标集合**逐字等于** registry 中 `pig` scope **且有真实 writer** 的指标集合，且**双向**核对无死条目
- 门禁：无 `research:read` → 读路由 403；无 `research:write` → 写路由 403；缺 CSRF → 403；"路由清单集合 == `router.routes` 集合"绊线通过
- 门禁：`bash scripts/self_review.sh --full` + `bash scripts/doc_gate.sh` 全绿；Playwright 含**三态视觉断言**（`数据不足` 不得渲染成"未满足"）与铃铛角标用例

**风险**
- **混合频率**（日 + 月 + 季）的"最近 N 期"是最容易写错的一处 → 用 registry `freq` + 交易日历，配单测（恰好 N / N−1 / 中间缺一期 / 跨月频）。
- registry 里存在**已登记但全仓无写入**的指标（`company.hogs_sold_monthly` · `company.cost_complete` · 依赖前者的派生 `mcap_per_head`）→ 由门禁脚本机械排除，不靠人记得。
- 挂载顺序错（早于收敛器）会被薄行触发误判 → 顺序写进验收判据并配测试。
- 判定序列读取可能拖慢对账任务 → 无 `open` 假设时零查询；批量取数上限有界。

---

## Wave 1.5 整体完成判据

- [ ] **新增表 = 6 张**，每张都有不可替代的存在理由：
  - S2.3 `backtest_runs`（回测批次与统计）
  - S2.4 `alert_subscriptions` · `alerts`（订阅关系与投递记录）
  - S2.5 `research_hypotheses` · `research_hypothesis_events` · `user_alert_cursors`（用户假设 · append-only 证据链 · 每人未读游标）
  - 同时：`industry_signals` 加 4 列；**无 `factor_defs` / `signal_rules` / `signals` / `backtest_events` / action 表**
- [ ] **无规则 DSL**：`grep -rn "eval(\|ast.parse\|condition.*text" backend/app/services/signal_rules.py` 无命中；阈值全部来自 registry
- [ ] 情绪极端值预警**端到端跑通**（规则 → 信号 → 提醒 → 已读）
- [ ] **用户假设端到端跑通**（建假设 → 数据落库 → 自动判定 → 铃铛可查 → 证据链可展开，S2.5）
- [ ] 每个信号可展开证据链（`basis`），含来源与 `published_at`；每条假设判定可展开 `readings` 快照
- [ ] **PIT 护栏测试 + PIT 对照实验双双通过**（回测可信度与假设判定的机械保证）
- [ ] `self_review.sh` 的条件指标集合检查全绿（S2.5）
- [ ] `bash scripts/bench.sh` + `bash scripts/self_review.sh --full` 全绿

---

## 明确推迟（消费方出现再长）

| 推迟项 | 何时做 |
|---|---|
| **证伪线**（"若 X 则本轮判断失效"） | **v2 第一优先项**（S2.5 落地后立刻评估）|
| 假设的相对变化 / 历史分位条件 | 分位条件被真实需要时（S2.5 v2）|
| 假设的个股 scope（`scope_key='stock:*'`） | 公司分析 4 卡（`S4.4`）落地后 |
| 订阅管理 UI · 按 target 订阅 | 消费方 #2（公司风险红线，`S4.5`）落地时 |
| 参数优化 · walk-forward · 蒙特卡洛 | Wave 5 `S6.5` |
| 组合级回测 | Wave 5 `S6.5` |
| `action` 层（止损/调仓动作） | Wave 6 `S7.7`（届时可能只需一张待办表）|
| 因子库 / 信号市场 UI | Wave 5+，有真实需求再说 |
| 邮件/推送通道 | 站内够用之后 |
