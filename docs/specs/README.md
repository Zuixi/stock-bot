# docs/specs — 模块规格目录

本目录存放**投研工作台各模块的契约规格**：一个 spec = 一个可独立交付、独立验收的单元。

## 与其它文档的分工（别写错地方）

| 想知道 | 去哪 | 本目录**不**写 |
|---|---|---|
| 现在是什么样 | `docs/architecture/**` · `docs/features.md` | 当前态描述（只写目标契约）|
| 当时为什么这么选 | `docs/decisions/**`（ADR ≤40 行）| 决策论证（只引用 ADR 编号）|
| 怎么一步步做、做到哪了 | `plans/**` | 排期、进度、勾选清单 |
| **这个模块的契约与验收判据是什么** | **本目录** | — |
| 外部数据源字段口径 | `docs/references/**` · `docs/design/data-source.md` | 外部源字段表（只写我方映射）|

**三条红线**：本目录不写排期、不写历史、不写"当前实现到哪一步"。契约一旦发布即为**目标真相**，实现落后于契约是常态。

## 编号规则

- 文件：`S<交付序>-<短横线小写主题>.md`。交付序 = **交付顺序**，与 plan 的 Wave 编号不一一对应（Wave 1.5 在交付序里是 S2）。
- 条目：文件内用 `S<交付序>.<n>` 标识单个 spec，例如 `S1.3`。
- 编号**不复用、不跳过**；作废的 spec 标 `superseded` 并指向替代条目。

| 交付序 | 文件 | 对应 Wave | 状态 |
|---|---|---|---|
| S0 | [S0-platform-contracts.md](./S0-platform-contracts.md) | Wave 0 平台契约 | detailed |
| S1 | [S1-market-sentiment.md](./S1-market-sentiment.md) | Wave 1 市场 + 情绪 | detailed |
| S2 | [S2-signal-engine.md](./S2-signal-engine.md) | Wave 1.5 信号 · 因子 · 回测 · 提醒（复用既有基建）| detailed |
| S3 | [S3-content.md](./S3-content.md) | Wave 2 资讯地基 | detailed |
| S4 | _(待建)_ | Wave 3 行业 / 公司 / 宏观 | catalog-only |
| S5 | _(待建)_ | Wave 4 价值网图谱 | catalog-only |
| S6 | _(待建)_ | Wave 5 策略引擎 | catalog-only |
| S7 | _(待建)_ | Wave 6 交易执行 + 风险管理 | catalog-only |
| S8 | [S8-watchlist-triage.md](./S8-watchlist-triage.md) | 散户工具型「自选体检」（8 个事实型探测器 + 噪声控制 + 站内角标）| detailed |
| S9 | [S9-decision-journal.md](./S9-decision-journal.md) | 决策日志与归因（3 行必填 + 自动快照 + 五类归因 + 个人「最常犯的错」统计）| detailed |
| S10 | [S10-market-regime.md](./S10-market-regime.md) | 市场状态机（五态 + 迟滞 + 转移矩阵 + 同态后续分布；零新表零新源）| detailed |
| S11 | [S11-unlock-calendar.md](./S11-unlock-calendar.md) | 解禁日历（补 `share_floats` 写入方 + triage 探测器 + 日志窗口标记；零新表）| detailed |
| S12 | [S12-closed-loop.md](./S12-closed-loop.md) | 闭环三件（一键记一笔 / 到期站内提醒 / 离线产物自愈；零新表零新 cron）| detailed |

> **渐进明细**：只有 S0–S3 写详细契约——S4 起的 spec 在**启动前一周**才细化。理由：Wave 1 的数据事实底座没落地前，Wave 5 的因子契约一定会返工，提前细化是浪费。

## spec 状态取值

`detailed`（契约完备，可开工）· `catalog-only`（已列条目，待细化）· `in-progress` · `shipped` · `superseded`

## spec 模板

每个 spec 用同一骨架，**缺项必须显式写"不适用"**——留空的章节会腐烂成假契约：

```markdown
### S<序>.<n> <名称>

**范围**：一句话，必须能用"做完之后，用户可以 ___"结尾。
**交付物**：表 / 端点 / 组件 / 迁移 / 文档，逐项列路径。
**不做什么**：显式边界（防范围蔓延）。

**契约**
- 数据：表名、关键列、约束、唯一键、索引
- 端点：`METHOD /api/v1/...`（请求/响应要点）
- 前端：路由、组件落位、UI 原语

**依赖**：上游 spec / 既有模块 / 外部数据源
**验收判据**：见下方三类
**风险**：已知的坑与缓解
```

## 验收判据的三类（每类至少一条，且必须可机械核对）

| 类 | 判据形态 | 例 |
|---|---|---|
| **数据契约** | 表存在 / **迁移只加列不加表** / 列非空率 / 唯一键生效 / 幂等（重跑零新行）| "`industry_metrics` 重跑幂等：二次 ingest 新增行数 = 0"；"本 spec 的迁移文件中无 `create_table`" |
| **行为** | 端到端可复现的脚本化步骤 + 期望输出 | "构造 PIT 对照，用报告期对齐比用披露日对齐的收益高 ≥X%，证明 PIT 生效" |
| **门禁** | 具体脚本 + 期望结果 | "`bash scripts/doc_gate.sh` 全绿" |

> 判据只允许写**可核对的事实**。禁止写"体验流畅""性能良好"这类无法判定的描述——这类需求必须转成具体数字或直接删除。

## 全量 spec 目录

### S0 平台契约（Wave 0 · 无业务代码）

| ID | 名称 | 交付物与契约要点 | 依赖 | 尺寸 |
|---|---|---|---|---|
| S0.1 | 平台决策 ADR 批次 | 5 篇 ADR：原料/视图分离 · 多态关注与标签 · PIT 时点数据 · 不做自动下单 · 信号引擎为跨模块基础设施。登记 `docs/decisions/index.md` | — | S |
| S0.2 | IA 冻结与路由契约 | 10 导航 × 子模块 × 路由表；**13 条旧路由的 redirect 映射**（`/market` `/research` `/tags` `/tags/:tagName` `/watchlist` 等，一条都不能删）| S0.1 | M |
| S0.3 | 数据源可行性复核 | 7 项逐条落「可得 / 受限 / 需自建」三态结论 + 替代口径；产出进 `docs/design/data-source.md` | — | M |
| S0.4 | 命名与口径字典 | 徽标语义（已接入数 vs 已策展数）· `metric_key` 命名 · **`publisher_tier`**（勿与既有 `source_tier` 混）· 指标口径版本 · 模块边界裁定 | S0.3 | M |

### S1 市场 + 情绪（Wave 1 · 不依赖资讯）

| ID | 名称 | 交付物与契约要点 | 依赖 | 尺寸 |
|---|---|---|---|---|
| S1.1 | 导航骨架与路由迁移 | head bar 10 导航 + 顶栏全局工具（搜索/自选/我的）+ `/me` 骨架 + 旧路由 redirect（**不含 `/calendar`**）| S0.2 | M |
| S1.2 | `industry_metrics` **加 2 列** + 统一读面 | `+published_at`（PIT）`+caliber_version`；读 API 与四徽章**复用既有**；**不建 `metric_series`** | S0.1 | S |
| S1.3 | 市场/全球与跨市场标的 | 美/港/其他市场指数、大宗 29、外汇 5；**名称 + 数据源代码**统一格式（修正 NDX 串号）| S1.2 | M |
| S1.4 | 市场/行业分类钻取 | 申万 31 → 二级 → 三级 → 个股 + 7 行情字段（含**补成交额**）| S1.2 | M |
| S1.5 | 市场/个股与板块热力图 | treemap（面积=市值/成交额，色=涨跌幅）；**对标 iFinD 的信息密度** | S1.2 | M |
| S1.6 | 市场/A股排行 7 榜 | 市值/半年利润/年利润/月涨幅/月跌幅/ROE/股息率 各百强 | S1.2 | S |
| S1.7 | 资金流四件套 + 外资画像重构 | ETF 份额变化 · 两融 · **董监高及关联人（官方一手，日频）** · **5%+ 股东（公告类目，事件）** · 南向；陆股通持仓降级为季度并具名。**产业资本拆两个 key**（2026-10-16，[ADR 0019](../decisions/0019-official-first-source-layer.md)，实测见 [data-source 附.11](../design/data-source.md)）| S1.2 | M |
| S1.8 | 情绪/市场宽度与动量 | 涨跌家数比 · 涨跌停家数比 · 突破 20 日线占比 · 炸板率 · **补连板高度 / 首板晋级率** | S1.2 | M |
| S1.9 | 情绪/交易活跃度与杠杆资金 | 全市场成交额 · 分指数换手率 · 融资余额 · 融资买入额占比 · 融券余额 | S1.2 | M |
| S1.10 | 情绪/波动率与衍生品 | 期权波动率 · 股指期货升贴水（**含 IC/IM 贴水率作为对冲成本代理**）| S1.2 | M |
| S1.11 | 情绪/估值与风险溢价 | 股债收益差（ERP = 1/PE − 10Y 国债）| S1.2 | S |
| S1.12 | 情绪/投资者行为 | 新成立基金份额 · ETF 份额变化 · 偏股基金发行占比（**替代低频/受限口径**）| S1.2 | M |
| S1.13 | 情绪/综合情绪指数 | **自建 composite**（加权 + 分位标准化）；第三方指数仅作对照，**不进信号链路** | S1.8–S1.12 | M |
| S1.14 | 情绪/散户舆情（可选） | 媒体平台搜索热度；无稳定源，**排在末位、不做承诺**。**2026-10-16 落 `superseded`**：官方出口不存在（百度指数需登录态被反爬 / 微信指数无公开入口），东财股吧人气榜不是同一口径 | — | S |

### S2 信号 · 因子 · 回测 · 提醒（Wave 1.5 · **复用既有基建**）

> **只有骨架，没有引擎。** 规则 = 纯函数（同 `cycle_engine.py`）、阈值 = `industry_registry`、信号 = `industry_signals`、因子 = `stock_features` 物化模式。**新增表仅 6 张**（S2.3 1 + S2.4 2 + S2.5 3）。

| ID | 名称 | 交付物与契约要点 | 依赖 | 尺寸 |
|---|---|---|---|---|
| S2.1 | 因子层（**不建表**）| 纯函数 + registry 元数据；高成本因子复用 `stock_features` 物化模式 | S1.2 | S |
| S2.2 | 规则与信号（**复用 `industry_signals`**）| 规则为纯函数；`industry_signals` **加 4 列**（`rule_key`/`rule_version`/`confidence`/`severity`）；`basis` 即证据链；**PIT 护栏** | S2.1 | M |
| S2.3 | 事件研究回测（最小版）| `backtest_runs` 一表（**无 `backtest_events`**，读时算）；PIT 强制 + 样本切分强制；**PIT 对照实验** | S2.2 | M |
| S2.4 | 提醒订阅与触达 | `alert_subscriptions` + `alerts`；站内必做；订阅管理 UI 推迟 | S2.2 | S |
| S2.5 | 用户研究假设与自动判定 | `research_hypotheses` + `research_hypothesis_events`（append-only 证据链）+ `user_alert_cursors`；**写下即冻结**（无 PATCH/PUT/DELETE）；判定**三态**（缺数据 ≠ 不满足）+ PIT；`research:write` 权限；**证伪线推迟到 v2** | S1.2, S2.4 | L |

### S3 资讯地基（Wave 2）

| ID | 名称 | 交付物与契约要点 | 依赖 | 尺寸 |
|---|---|---|---|---|
| S3.1 | 内容事实层四表 | `content_items` · `content_entities` · `content_metrics` · `user_content_state`（**状态必须归用户维度**）| S0.1 | L |
| S3.2 | 公告采集（cninfo） | 定期报告 / 临时公告 / 业绩预告 / 问询函；`raw_hash` 去重 + 增量幂等 | S3.1 | L |
| S3.3 | 财经快讯采集 | 秒~分钟级；复用既有对账自愈模式 | S3.1 | M |
| S3.4 | LLM 抽取管线与溯源 | 定期报告 → 主营业务收入/利润；研报 → 核心观点；**带 `source_span` 原文定位** | S3.1 | L |
| S3.5 | 人工裁定队列 | `review_status` 状态机 + overlay 种子；裁定结果**优先于自动结果** | S3.1 | M |
| S3.6 | 资讯视图层 | 4 类 × 子类列表 + 状态机 + 来源可溯（原文 URL / 发布主体 / 抓取时间）| S3.4 | M |
| S3.7 | "有效研报"判定规则 | 去重 / 行业相关 / 有数据支撑；规则显式且**可人工推翻** | S3.4 | S |

### S4 行业 / 公司 / 宏观（Wave 3 · catalog-only）

| ID | 名称 | 契约要点 | 依赖 |
|---|---|---|---|
| S4.1 | 行业看板（申万三级策展） | 策展清单 + 逐行业页骨架 | S3, S1.4 |
| S4.2 | 行业报告模板 | **Top5 公司定期报告 + 有效研报 → 字段级对齐对比表** | S4.1, S3.4 |
| S4.3 | 行业比较 | 景气度排名 · 估值分位对比 · 资金流向排行 · 涨跌幅对比（**分位需历史回填深度**）| S4.1 |
| S4.4 | 公司分析 4 卡（定量）| 基本信息 · 财务数据 · 估值分析 · 股东股权 | S1.2 |
| S4.5 | 公司风险红线 | 规则化命中 + **100% 可点回公告原文** | S2.2, S3.2 |
| S4.6 | 公司分析 5 卡（定性）| 商业模式 · 竞争力 · 管理层 · 事件驱动（+ 基本面恶化）| S3.4 |
| S4.7 | 宏观/经济指标树 | PMI·CPI·PPI·GDP·社融·进出口·工业增加值·固投·社零；带 `发布日` 与 `口径版本`（**M1 断点显式标注**）| S1.2 |
| S4.8 | 宏观/货币与财政 | 货币供应 · **7 天 OMO（主政策利率）** · MLF 降级 · DR/同业存单/LPR · 买断式逆回购 + 国债买卖 · 赤字与政府债券 | S4.7 |
| S4.9 | 宏观/海外与外部流动性 | 美联储 · 美债 10Y · 美元指数 · **中美利差** · 在岸/离岸人民币 | S4.7 |
| S4.10 | 宏观/政策视图 | 顶层政策 · 产业政策 · 监管政策（**原料来自 S3，不重复采集**）| S3.6 |
| S4.11 | 宏观/政策影响追踪 | 收益受损板块 · 历史同类政策回测 · **预期差** · 政策传导路径（→ S5.1 传导图）| S4.10, S2.3 |
| S4.12 | 兜底分类与人工裁定通用机制 | "其他分类" 兜底 + 人工覆盖持久优先；推广为通用 review queue | S3.5 |

### S5 价值网图谱（Wave 4 · catalog-only）

| ID | 名称 | 契约要点 | 依赖 |
|---|---|---|---|
| S5.1 | 传导图基础设施 | **只建 `graph_edges`**（含 **`lag_days` 时滞** · `confidence` · `review_status`）；**不建 `graph_nodes`**（节点已是既有表的行）；产业链与政策共用 | S0.1 |
| S5.2 | 首批 3–5 行业策展建图 | 生猪养殖优先（链条短、猪智投已有基础）| S5.1 |
| S5.3 | 产业链边抽取与确认 | 财报前五大客户/供应商 > LLM 抽取 > 申万粗粒度 > 工商；**≥2 证据源才 `confirmed`** | S5.2, S3.4 |
| S5.4 | 价值分配 / 利润流向 / 替代互补 | **价值分配与利润分配流向合并为一处**；替代品与互补品为跨产业链边 | S5.2 |

### S6 策略引擎（Wave 5 · catalog-only）

| ID | 名称 | 契约要点 | 依赖 |
|---|---|---|---|
| S6.1 | 因子库与选股策略 | 价值/成长/质量**必须翻译为因子组合**；"护城河"须定义代理指标（ROIC 持续性 / 毛利率稳定性 / 市占率趋势 / 单位成本）| S2.1 |
| S6.2 | 择时策略 | 技术（趋势/均值回归/指标）· 宏观（货币信用周期/美林时钟）· 情绪（来自 S1.13）| S2.1, S4.8, S1.13 |
| S6.3 | 信号系统 | 四维评分（基本面+技术+情绪+资金）+ **置信度分级 → 仓位** | S2.2, S6.1 |
| S6.4 | 组合构建与风险预算 | 行业权重 · 个股仓位 · **消费 S7.4 定义的风险预算** | S6.3, S7.4 |
| S6.5 | 回测引擎 | **PIT 强制** + walk-forward + 参数优化；蒙特卡洛/相关性优化**延后**（S2.3 只做单规则事件研究）| S2.3 |

### S7 交易执行 + 风险管理（Wave 6 · catalog-only · **Wave 内倒序**）

| ID | 名称 | 契约要点 | 依赖 |
|---|---|---|---|
| S7.1 | 关注池 | `stage(watch/candidate/position/closed)` + `source_module` + **`reason`（为什么关注它）** | S0.1 |
| S7.2 | 交易记录与持仓 | 买卖记录/价格/数量/时间 · 实时持仓盈亏 · 资金使用率 | S7.1 |
| S7.3 | 交易日志闭环 | 记录 → **复盘（每笔逻辑评分）** → **盈亏归因（须先定模型：Brinson / 因子 / 时间）** → 统计 | S7.2 |
| S7.4 | 个股风控规则 | 止损（固定比例/技术位/波动率）· 个股仓位上限 · 集中度限制（**不依赖持仓，可先行**）| S2.2 |
| S7.5 | 组合风控 | 最大回撤控制线 · VaR/CVaR · 行业暴露 · Beta 暴露（**依赖 S7.2 持仓**）| S7.2 |
| S7.6 | 系统性风险与预案 | 大盘风险信号（← S1.13）· 流动性危机（← S4.8+S1.9）· 黑天鹅（← S3.6）· 极端行情 playbook | S1.13, S4.8, S3.6 |
| S7.7 | 风控规则引擎与审计 | 自动止损触发 · 仓位调整 · 风险预算定义 · **日志 append-only 不可篡改**（含"应触发未触发"）。**动作 = 通知 + 待办**（ADR-D 不下单），与 `alerts` 同路径 | S7.4, S2.4 |

### S8 自选体检（散户工具型 · 事实清单）

> 计划：[plans/2026-10-17-watchlist-triage.md](../../plans/2026-10-17-watchlist-triage.md)。
> **定位**：工具型（只陈述已发生的事实，不构成投资建议）；**不做**预测/社区/跟单；**不动** `/market` 的 tab 与 6 个一级导航。
> **为什么不是信号清单**：2026-10-17 规则标定证伪（放量新高 −0.92%、首板 −0.28%、PE 低位 +0.12%，见
> [signal-rule-calibration](../../docs/design/signal-rule-calibration.md)）⇒ 砍掉预测型产品。
> **v1 零新表、零新 cron**：`GET /api/v1/watchlists/triage` 读时算（5 条固定查询，反 N+1）。

| ID | 名称 | 交付物与契约要点 | 依赖 | 尺寸 |
|---|---|---|---|---|
| S8.1 | 入口与页面 | `/today`（**登录后默认落地**，三区：有事的票 / 平静的票 / 页脚免责）；每条只有"发生了什么 + 数据截至 + 复核"；`/me` 加"今日状态" | S8.2, S8.3 | M |
| S8.2 | 探测器（8 个，事实型） | 纯函数 + registry `TRIAGE_DETECTORS`；`severity ∈ {risk,event,attention,background}`；`required_fields` 导入期核对；**口径禁令**（禁 `turnover_rate_f`、禁 `market_limit_up_stock_daily`、禁个股融资余额、禁 `adj_factor` 判除权） | S1.2 | L |
| S8.3 | 读面与数据（零新表） | `GET /api/v1/watchlists/triage`；**5 条固定查询**，查询次数不随自选数增长；自选 >50 返回 422（不静默截断） | S8.2 | M |
| S8.4 | 噪声控制 | 单股合并 1 条（「另有 N 项」）· 每页 ≤8 · `risk > event > attention > background` · 缺数据进 `unavailable` 不静默 | S8.3 | S |
| S8.5 | 合规 | 固定免责 + 禁用词门禁（≥20 词，清单在代码）+ 分级只四枚举 | S8.2 | S |

### S9 决策日志与归因

> 计划：[plans/2026-10-18-decision-journal.md](../../plans/2026-10-18-decision-journal.md)。
> **定位**：记录用户**自己的判断**并自动归因；不构成投资建议，不评测"该不该买"。
> **新增表仅 2 张**、0 新 cron、无新外部数据源。入口 `/me?tab=journal`（不新增路由）。

| ID | 名称 | 契约要点 | 依赖 | 尺寸 |
|---|---|---|---|---|
| S9.1 | 入口与页面 | 3 必填（thesis ≤200 / invalidation / horizon ∈ {7,30,90}）；创建后不可改；`open/due/reviewed` 三态 | S9.2, S9.3 | M |
| S9.2 | 数据模型 | `decision_journal_entries`（snapshot jsonb、due_on 固化）+ `decision_journal_reviews`（append-only、`unique(entry_id)`） | — | S |
| S9.3 | 自动快照 | 价格/MA20 距离/情绪分位/板块状态/当日事件/估值分位；缺数 `null + missing_reason`；≤5 查询 | S8 | M |
| S9.4 | 结果与归因 | 到期自动算 `outcome/bench/excess/invalidation_hit`；归因五枚举必选；`stats` 自洽；`reviewed_n<5` 不显示占比 | S9.3 | M |
| S9.5 | 合规 | 固定免责；产品文案禁用词沿用 S8 词表；无排行/无对比 | — | S |

---

## 跨 spec 的通用约束

这些约束适用于**所有** spec，不逐条重复：

0. **先爬梯子：这个功能已经存在了吗？** 动手写 spec 前先 `grep` 一遍下表。**初版计划在这里栽过——16 张新表里 4 张是这张表上已有东西的复制品。**

   | 已有 | 位置 | 不要重复造 |
   |---|---|---|
   | `industry_metrics` | `app/models/industry_research.py:30` | 统一指标表 → **加列复用** |
   | `industry_signals` | 同上 `:102`（*"rules engine output history"*）| 信号表 → **加列复用** |
   | `cycle_engine.py` | `app/services/cycle_engine.py`（*纯函数、阈值进 registry*）| 规则引擎 → **照抄形态，不要 DSL** |
   | `industry_registry.py` | `app/services/industry_registry.py` | 指标/阈值配置表 |
   | `stock_features` | `app/models/feature.py` | 因子存储与物化模式 |
   | `concept.py` 的 `symbol` 键 + 可空 `stock_id` + `unresolved_count` | `app/models/concept.py:3` | 分类未命中兜底 |
   | `SourceBadge` · `StateWrapper` · `EChart` · `PagedResponse` | `frontend/src/shared/` · `backend/app/schemas/` | 徽章 / 状态壳 / 图表 / 分页 |

1. **原料/视图分离**：同一批原料可支撑多个视图，但只允许一份采集、一份事实表（[ADR 待写：S0.1]）。
2. **禁止平行实现**：新页面优先复用既有原语（`PagedResponse` / deps 注入 / Alembic 链 / `QUEUES`-Worker-Scheduler 双轨 / 限流重试基类 / `StateWrapper` / `EChart` 封装）。
3. **数据徽章**：所有展示的数字必须带 `source / as_of / freq`；口径有版本变更的另带 `口径版本`。
4. **可溯源**：任何"结论性"内容（摘要、对比表单元格、风险红线命中、信号）必须能点回原始出处。
5. **PIT**：任何进入回测的时间序列必须携带 `published_at`（实际可得时点），不得只用 `period`。
6. **口径变更即标注**：上游统计口径修订（如 M1）必须在序列上留断点标记，不得静默拼接。
7. **队列与定时任务**：新增 RabbitMQ 队列必须在 `app/core/mq.py` 的 `QUEUES` 注册（`doc_gate` 校验）；采集双轨（APScheduler + Worker）共用同一 ingest service 方法。
8. **不做项**：自动下单 / 券商对接 · 全市场产业链大网 · 付费 L4 数据源 · tick 级行情 · "恐贪指数"命名。
