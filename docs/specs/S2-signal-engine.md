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

## Wave 1.5 整体完成判据

- [ ] **新增表 = 3 张**（`backtest_runs` / `alert_subscriptions` / `alerts`）；`industry_signals` 加 4 列；**无 `factor_defs` / `signal_rules` / `signals` / `backtest_events` / action 表**
- [ ] **无规则 DSL**：`grep -rn "eval(\|ast.parse\|condition.*text" backend/app/services/signal_rules.py` 无命中；阈值全部来自 registry
- [ ] 情绪极端值预警**端到端跑通**（规则 → 信号 → 提醒 → 已读）
- [ ] 每个信号可展开证据链（`basis`），含来源与 `published_at`
- [ ] **PIT 护栏测试 + PIT 对照实验双双通过**（回测可信度的机械保证）
- [ ] `bash scripts/bench.sh` + `bash scripts/self_review.sh --full` 全绿

---

## 明确推迟（消费方出现再长）

| 推迟项 | 何时做 |
|---|---|
| 订阅管理 UI · 按 target 订阅 | 消费方 #2（公司风险红线，`S4.5`）落地时 |
| 参数优化 · walk-forward · 蒙特卡洛 | Wave 5 `S6.5` |
| 组合级回测 | Wave 5 `S6.5` |
| `action` 层（止损/调仓动作） | Wave 6 `S7.7`（届时可能只需一张待办表）|
| 因子库 / 信号市场 UI | Wave 5+，有真实需求再说 |
| 邮件/推送通道 | 站内够用之后 |
