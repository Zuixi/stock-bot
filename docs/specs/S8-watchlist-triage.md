# S8 自选体检（散户工具型 · 事实清单）

> 交付序 **S8**。计划：[plans/2026-10-17-watchlist-triage.md](../../plans/2026-10-17-watchlist-triage.md)。
> **定位**：工具型 —— 只陈述**已发生的事实**（异动/破位/涨停炸板/公告/龙虎榜/板块/新高低），**不做预测、不宣称 edge、不构成投资建议**；不做社区/跟单。
> **为什么不是"信号清单"**：2026-10-17 的规则标定（[signal-rule-calibration.md](../design/signal-rule-calibration.md)）显示当前数据面**支撑不了可靠 edge**（放量新高 −0.92%、首板 −0.28%、PE 低位 +0.12%、候选规则仅 +1% 且 in-sample）⇒ 砍掉预测型产品，改为事实型体检。

---

## S8.1 入口与页面

**范围**：做完之后，用户打开默认入口就能看到"我的自选今天哪几只有事、是什么事、怎么复核"。

**交付物**
- 前端 `/me?tab=triage`（**`/me` 的默认 Tab**，登录后打开 `/me` 即落在它）：自选体检台
- 前端 `/me` 每个自选标的行尾显示"今日状态"（静默 / 1 项 / 2+ 项）——**v2 待做**（本轮只做体检 Tab）
- 路由清单登记：**零新增路由**（复用既有 `/me`；`route-manifest.ts` / `ARCHITECTURE §6` 鉴权列 / `check:routes` 都不用改）
- **不改** `/market` 的 5 个 tab、**不隐藏** 6 个一级导航（owner 2026-10-17）

> **独立 `/today` 路由推迟**：原计划的 `/today`（登录后默认落地）本轮**不做**。它要新增一条一级路由，
> 必须同时登记信息架构 §4（`plans/2026-10-12-investment-research-platform-ia.md`）、`route-manifest.ts`、
> `ARCHITECTURE §6` 鉴权列，并过 `check:routes`——收益只是少一次点击，先落在 `/me` 的默认 Tab。
> **本文其余章节里出现的 `/today`，一律读作 `/me?tab=triage`。**

**不做什么**：不做预测/评分/胜率 · 不做组合建议 · 不做社区/跟单 · 不做导出

**契约**
- `/me?tab=triage` 三区（顺序固定）：① **今日有事的票**（分组：风险 / 事件 / 关注 / 背景）② **今日平静的票**（一行数字）③ 页脚（`as_of` + 免责声明）
- 每条只输出三件事：**发生了什么（含数值）** · **数据截至** · **点这里复核**（公告原文 / K 线 / 龙虎榜）
- 没有"推荐 / 看好 / 建议"这类判断词；分级只用 **风险 / 事件 / 关注 / 背景** 四枚举
- **状态是契约字段**：`state = ok | no_data | no_watchlist`，前端按它选文案，**不**从 `items=[] && quiet_count=0` 反推
  （那个组合同时对应"没自选"和"最近完整交易日不存在"，反推就会把后者画成"你还没加自选"）
- `unavailable` 下发**人话 label**（「所属板块在动」），机器 key 只走 `unavailable_keys`（前端禁止渲染）

**依赖**：S8.2（探测器）· S8.3（读面）· 既有 `/api/v1/watchlists`（+ 新增读面 `GET /api/v1/watchlists/triage`）
**验收判据**
- 行为：四种状态都有明确文案——有命中 / 无命中（全静默）/ 无数据日 / 无自选（无自选时给"去添加自选"入口，不空页）
- 行为：每条都带 `as_of` 与复核入口（点开能到公告原文或该股 K 线）
- 门禁：`check:routes` 通过（零新增路由，天然通过）；`/me` 鉴权 = **登录即可**（不要求 `research:read`：数据是公开行情 + 用户自己的自选）
- 门禁：e2e 断言 `/market` 5 个 tab 仍在（防顺手改）
- 门禁：`backend/scripts/check_triage_compliance.py` 0 命中（判断词扫前端 + API 常量 + 探测器 headline）

**风险**：体检与 `/market` 功能重叠 → 已用"只看自选 + 只说事实"约束；验收里禁止出现全市场列表。

---

## S8.2 探测器（8 个，事实型）

**范围**：做完之后，系统能对任意自选集合，按当日数据判定"发生了什么事"，且每条都能被数据复核。

**交付物**
- `backend/app/services/triage_detectors.py`（**纯函数**：输入 `TriageContext`，输出 `TriageItem | None`）
- registry 常量 `TRIAGE_DETECTORS: tuple[DetectorSpec, ...]`（阈值即配置；`severity` 固定枚举）
- 导入期断言 `_assert_triage_detectors()`：8 个 · key 唯一 · `severity ∈ {risk,event,attention,background}` · **`required_fields` 在库内真实存在**

**不做什么**：不做打分/排序模型（排序只用 `severity` 枚举 + 固定次序）· 不做预测 · 不做 ML · 不做用户自定义探测器

**契约**

```python
@dataclass(frozen=True)
class DetectorSpec:
    key: str                    # 'break_ma20' | ...
    label: str                  # 「跌破 20 日线」
    severity: str               # risk | event | attention | background
    thresholds: dict[str, float]
    required_fields: tuple[str, ...]   # 例 ('daily_quotes.close',)
    verify: str                 # 'kline' | 'announcement' | 'dragon_tiger' | 'sector'
```

| # | key | 人话 | 判定（事实） | severity | 数据 |
|---|---|---|---|---|---|
| 1 | `break_ma20` | 跌破 20 日线（5 日内首次） | 收盘 < MA20 且 前一日收盘 ≥ MA20 | risk | `daily_quotes` |
| 2 | `drop_sharp` | 今天跌得急 | 当日跌幅 ≥ 7%，或 5 日跌幅 ≥ 12% | risk | `daily_quotes` |
| 3 | `volume_spike` | 放量异动 | 成交额 ≥ 2× 前 20 日均额 且 \|涨跌幅\| ≥ 5% | attention | `daily_quotes` |
| 4 | `limit_up_state` | 涨停 / 炸板 | 收盘 = 涨停价（涨停），或 盘中触及涨停但收在板下（炸板） | attention / risk | `stock_price_limits`（**唯一权威涨停价**；`market_limit_up_stock_daily` 实测只有 2 天，不得使用） |
| 5 | `new_announcement` | 有新公告 | 近 3 个自然日有公告；`equity_change`（减持/增持/权益变动）优先 | event | `announcements`（含 S1.7/D3 的 `equity_change`） |
| 6 | `dragon_tiger` | 上了龙虎榜 | 近 3 个交易日上榜，展示净买卖额 | event | `dragon_tiger_entries` |
| 7 | `sector_move` | 所属板块在动 | 所属申万一级当日涨幅或主力净流入进全市场前 10% | background | `industry_metrics` / `sector_moneyflow_snapshots` |
| 8 | `new_high_low` | 创 60 日新高/新低 | 收盘 = 60 日最高/最低（**排除上市 <60 交易日**，否则次新恒真） | attention | `daily_quotes` |

**口径禁令（写死，均由 2026-10-17 实机标定得出）**
- 换手率只能用 `turnover_rate`（**`turnover_rate_f` 实测全列 NULL**：133,308 行 0 非空）；
- 涨停判定只能走 `stock_price_limits.up_limit`（`close >= up_limit - 0.005`）；
- **个股融资余额不可得**（库里只有市场级 `margin`）⇒ 任何"个股杠杆"条件不得出现；
- 涨跌幅/收益用 `close/pre_close - 1`（`adj_factor` 实测仅 **1.3%** 非空，不可用于除权判断）。

**依赖**：S8.3 · `watchlists`/`watchlist_items` · 上表各数据源
**验收判据**
- 数据契约：每条 `TriageItem` 带 `severity` / `as_of` / `facts[]`（每项含 `label,value,unit`）/ `verify`
- 行为：探测器**纯函数**——同一 `TriageContext` 输入两次输出一致（可重放）
- 行为：`required_fields` 缺失的探测器在**导入期**失败（不是运行期静默不出）
- 行为：`new_high_low` 对上市 <60 交易日的股票**不得命中**（单测：构造只含 20 行的序列）
- 门禁：探测器数量 / severity / key 唯一性由导入期断言锁定

**风险**：探测器越多噪声越大 → 由 S8.4 控制；`sector_move` 依赖板块资金流快照可得性（缺失时不输出并在 `unavailable` 明示）。

---

## S8.3 读面与数据（**v1 零新表**）

**范围**：做完之后，任意自选集合的体检结果都能在一次请求内返回，且查询次数不随自选数增长。

**交付物**
- `backend/app/api/v1/watchlist_triage.py`：`GET /api/v1/watchlists/triage`
- `backend/app/schemas/triage.py`
- 批量取数层（**5 条固定查询**：行情窗口 / 涨停价 / 公告 / 龙虎榜 / 板块），反 N+1

**不做什么**：**不建表**（不建 `daily_signal_items` / `alerts`）· 不落历史（复盘推迟到 v2，届时单独立项）· 不新开 cron（读时算）· 不改 `industry_signals`

**契约**
```
GET /api/v1/watchlists/triage?group=&limit=
→ {
  as_of,                          # 最近完整交易日（沿用 resolve_latest_complete_day 的判据）
  quiet_count: 9,
  items: [{ symbol, name, detector, severity, headline,
            facts: [{label, value, unit}], as_of, source,
            verify: {kind: 'kline'|'announcement'|'dragon_tiger'|'sector', target} }],
  unavailable: ['sector_move'],   # 本日不可用的探测器（明示，不静默）
  disclaimer
}
```
- 自选 > 50 只 → **422** 并提示分组（**不静默截断**）
- 查询次数 **与自选数量无关**（测试断言查询计数 ≤ 6）

**依赖**：S8.2 · `watchlists`（既有）
**验收判据**
- 数据契约：本 spec **无迁移文件**（`grep create_table` = 0）
- 行为：`GET` 只算自选内标的（不扫全市场）；只读 `daily_quotes` 最近 60 交易日窗口
- 行为：反 N+1——查询次数 ≤ 6 且不随自选数增长（断言计数）
- 行为：`as_of` = 最近完整日，不返回半日数据
- 门禁：`bash scripts/self_review.sh --full` + `doc_gate`

**风险**：读时算延迟 → 自选 ≤50、窗口 ≤60 交易日、5 条固定查询；超过 50 只直接拒绝（不降级）。

---

## S8.4 噪声控制（本 spec 的真门禁，替代"样本外 edge"）

**范围**：做完之后，用户看到的**永远 ≤8 条**，每条都是他今天需要知道的。

**交付物**：合并与排序纯函数 + 单测；响应里的 `quiet_count` / `unavailable`

**契约**
- **单股合并**：同一标的当日命中多个探测器 → 合并 1 条，取 `severity` 最高者，`facts` 合并，headline 后缀「另有 N 项」；
- **排序**：`risk > event > attention > background`，同级按 `|当日涨跌幅|` 降序；
- **上限**：`items ≤ 8`（超出进"今日其余"折叠区，不丢数据）；
- **安静日**：全部无命中 → `items=[]` + 前端"今天你的 N 只都平静"；
- **不可用**：探测器缺数据 → 进 `unavailable`，**不静默当作无事件**。

**不做什么**：不做学习型降噪（v1 固定规则）· 不做用户自定义阈值

**验收判据**
- 行为：构造一只股命中 4 个探测器 → 返回 1 条且含「另有 3 项」
- 行为：构造 30 条命中 → 返回 ≤8 条 + 其余进折叠区
- 行为：缺数据的探测器进 `unavailable` 而不是"无命中"
- 门禁：单测 + e2e 断言页面条目数 = 响应条目数

**风险**：合并会隐藏细节 → `facts` 合并 + 复核入口保留，折叠区可展开。

---

## S8.5 合规

**范围**：做完之后，产品在任何页面都不构成投资建议。

**交付物**：`check_triage_compliance.py`（**M0 交付**，落 `backend/` 脚本目录）+ 固定 footer + 前端分级枚举

**契约**
- 固定 footer：**"本产品只展示已发生的数据事实，不构成投资建议；历史数据不代表未来表现。"**
- 禁用词（**下限 ≥ 20 词**，完整清单在代码 `triage_compliance.py` 常量里，文档不复制第二份）：
  `买入` `卖出` `推荐` `建议关注` `看好` `目标价` `必涨` `稳赚` `抄底` `翻倍` `满仓` `保本` `上车` `跟上` `牛股` `黑马` `止盈` `目标位` `机会` `布局`
- 扫描面：`/today` 文案 + API 的 `headline` / `facts[].label` + 角标文案

**不做什么**：不做收益承诺 · 不做"跟单"入口 · 不做真人观点

**验收判据**
- 门禁：词表命中 = 0
- 行为：`/today` 与 `/me` 状态行可见免责（或一键展开）
- 行为：分级只用 `风险/事件/关注/背景` 四枚举（`grep` 无第五种）

---

## S8 整体验收判据

- 数据契约：**零新表**（本 spec 相关迁移 `create_table` = 0）· 无新 cron
- 行为：一次请求返回 ≤8 条 + `quiet_count`；用户能看到当天真实事件
- 行为：查询次数 ≤6 且不随自选数增长；自选 >50 返回 422
- 行为：`/today` 三种状态（有命中 / 全静默 / 无自选）都有明确文案
- 门禁：`self_review --full` · `doc_gate` · Playwright（条目与响应一致、免责可见、`/market` 5 tab 未回归、0 禁用词）

## 明确推迟

- **历史留痕与复盘**（"三个月前它说了什么"）—— 需要落表，**v2 单独立项**，且先证明 v1 有人用
- 预测型信号/评分/胜率（标定未通过，见 [signal-rule-calibration.md](../design/signal-rule-calibration.md)）
- 邮件/微信通道 · 订阅管理 UI · 用户自定义探测器
- 社区/关注人/跟单（产品边界）
