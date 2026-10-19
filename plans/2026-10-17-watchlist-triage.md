---
status:      draft
scope:       面向散户的「自选体检」：只看用户自己的自选，回答"今天哪几只有事、是什么事、怎么复核"；事实型、零新表、不做预测、不做社区；/market 与 6 个一级导航不动
touches:     plans/, docs/specs/, docs/design/, backend/app/services/, backend/app/api/v1/, backend/app/schemas/, backend/scripts/, backend/tests/, frontend/src/pages/, frontend/src/features/, frontend/src/app/router/, docs/Changelog.md
updated:     2026-10-17
next-action: 用户审阅 docs/specs/S8-watchlist-triage.md；批准后用 writing-plans 出实施计划，再进 M0
---

# 自选体检（Watchlist Triage）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:writing-plans` 把 §4 的 M0–M3 拆成可执行任务；实现按仓库既有 SDD 节奏（brief → 实现 → 评审 → 收口）。

**Goal**：把"用户每天扫 12 只票的 K 线与公告"这件事，变成**打开一处、30 秒看完**：哪几只有事、是什么事、点哪里复核。

**为什么是这个形态**（完整论证见 [signal-rule-calibration.md](../docs/design/signal-rule-calibration.md)）：

1. 产品现状：读面约 21 页，用户唯一能写的资产是自选与标签 —— 看完之后无处落。
2. 2026-10-17 规则标定把"每日信号清单"这条路走完了：**放量新高 −0.92%、首板 −0.28%、PE 低位 +0.12%**（5 日超额中位数），候选规则仅 **+1% 且 in-sample** ⇒ 当前数据面**支撑不了可靠 edge**。
3. 因此砍掉预测型产品，改做**事实型体检**：不需要 edge 也有用（"你手里的票今天发生了什么"本身就是价值），且合规压力最低（不预测、不承诺）。

---

## 1. 产品定位（owner 已拍板）

| 决策 | 值 |
|---|---|
| 目标用户 | **泛散户** |
| 合规定位 | **工具型**：只陈述**已发生的事实**，不出现买卖指令/收益承诺/预测话术 |
| 输入 | **用户自己的自选**（≤50 只）——不是全市场 |
| 输出 | ≤8 条事实 + 每条的数据引用与复核入口；无命中日明确说"都平静" |
| 社交 | **不做**（无发帖/关注人/跟单） |
| `/market` | **零改动**（5 个 tab 原样） |
| 一级导航 | **不隐藏**（占位页保持"规划中"） |
| 新表 | **0 张**（读时算；历史留痕推迟到 v2） |

## 2. 与已有计划/规格的关系

- **不取代** [2026-10-12 投研工作台 IA 主线](./2026-10-12-investment-research-platform-ia.md)：10 导航 IA、PIT、S2.5 假设等仍有效；本计划是**面向散户的第一优先切片**。
- **复用**：`watchlists`（既有）· `daily_quotes`/`daily_basic_indicators`/`stock_price_limits`/`announcements`/`dragon_tiger_entries`/`industry_metrics` · "最近完整日"判据（`resolve_latest_complete_day`）· `SectionCard`/`DegradedNotice` 等既有 UI 原语。
- **不使用**：`market_limit_up_stock_daily`（实测仅 2 天）· `turnover_rate_f`（全列 NULL）· 个股融资余额（不可得）· dormant 的两条腿（`insider_net_change_daily`/`northbound_holding_quarterly`）。

## 3. 契约（详见 [spec S8](../docs/specs/S8-watchlist-triage.md)）

- **8 个探测器**（纯函数、事实型、`severity ∈ {risk,event,attention,background}`），阈值登记在 registry；`required_fields` 导入期核对；
- **零新表 / 零新 cron**：`GET /api/v1/watchlists/triage` 读时算，**5 条固定查询**（反 N+1，不随自选数增长）；
- **噪声控制是门禁**：单股合并 1 条、每页 ≤8 条、`risk > event > attention > background`、缺数据进 `unavailable` 不静默；
- **合规**：固定免责 + 禁用词 ≥20（清单在代码）+ 分级只有四个枚举。

## 4. 里程碑

| # | 交付 | 可验证结果 | 量级 |
|---|---|---|---|
| **M0** | 探测器契约（`TRIAGE_DETECTORS` + `required_fields` 断言）· `check_triage_compliance.py` · spec/计划登记收口 | 门禁能对"缺字段的探测器 / 新增禁用词"**变红**；`self_review` + `doc_gate` 绿 | S |
| **M1** | `/today` + 探测器 1–4（破位 / 急跌 / 放量异动 / 涨停炸板）+ `GET /watchlists/triage` | 自己的自选里能看到当天真实事件，数值可点回复核 | M |
| **M2** | 事件类探测器（公告 / 龙虎榜 / 板块 / 新高低）+ 噪声控制（合并 / ≤8 / 排序 / unavailable） | 单股最多 1 条、每页 ≤8、缺数据明示 | M |
| **M3** | `/me` 今日状态行 + 铃铛角标（今日命中数）+ 合规扫描 + e2e | 角标与命中数一致；页面 0 禁用词；`/market` 5 tab 未回归 | S |

**做 M1 就能交付价值**（4 个最高频事实：破位、急跌、放量、涨停/炸板）；M2 的公告/龙虎榜把"为什么跌"补上；M3 才解决"记得打开"。

## 5. Kill criteria（先写死）

1. `/today` 连续 **5 个交易日**没被打开 → 入口假设错：停下来改设计，不加探测器。
2. **单日命中率失控**：命中条数 > 3 条/页 的股票占比 > 30% → 收窄阈值（噪声 = 用户流失）。
3. **死探测器**：任意探测器 **2 周内从未命中** → 删掉它（不留"看起来有"的功能）。

## 6. 成功判据（可核对）

- `/today` 的条目数 = API 条目数；每条带 `as_of` 与复核入口；
- 查询次数 ≤6 且**不随自选数增长**（测试断言）；
- `/today` 三种状态（有命中 / 全静默 / 无自选）都有明确文案；
- 0 禁用词命中；`/market` 5 个 tab 与 6 个一级导航未被改动（e2e 断言）。

## 7. 决策日志

| 日期 | 决策 | 来源 |
|---|---|---|
| 2026-10-17 | 目标用户 = **泛散户** | owner |
| 2026-10-17 | 合规定位 = **工具型**（不构成投资建议） | owner |
| 2026-10-17 | 形态先定 **A（每日清单）+ B（自选体检）合体** | owner 采纳推荐 |
| 2026-10-17 | **`/market` 5 tab 不动**、**6 个导航不隐藏**、**不做社区** | owner 修正 |
| 2026-10-17 | 规则标定证伪（放量新高 −0.92%、首板 −0.28%、PE 低位 +0.12%、候选 +1% in-sample）⇒ **放弃预测型清单** | 标定报告 + owner |
| 2026-10-17 | **转向 A：自选体检**（事实型、零新表、8 探测器、噪声控制当门禁） | owner |
| 2026-10-17 | 里程碑 M0–M3 + 三条 kill criteria（含"死探测器要删"） | owner 确认 |
