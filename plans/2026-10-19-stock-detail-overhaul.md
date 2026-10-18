---
status:      draft
scope:       标的详情页（`/company/:symbol`）整改，分 P0/P1/P2 三档：P0 修 5 处「页面说假话」+ K 线跨 pane 联动 + 防复发契约测试（2 人日）；P1 ribbon / 图内事件标记与定位联动 / 概览重排 / 移动端 / 财务口径（2.5 人日）；P2 记一笔 / 股性事实 / 加载错误态（1.5 人日）
touches:     frontend/src/shared/ui/kline/, frontend/src/shared/ui/EChart.tsx, frontend/src/shared/api/{stocks,quotes,marketData,financial}.ts, frontend/src/shared/config/exchange.ts(新), frontend/src/features/stock-detail/, frontend/src/features/journal/components/EntryForm.tsx, frontend/src/pages/stock-detail/, frontend/scripts/check-api-fields.mjs(新), backend/app/api/v1/market_data.py, backend/app/services/market_data_service.py, backend/app/repositories/market_data_repo.py, backend/tests/, docs/features.md, docs/Changelog.md
updated:     2026-10-19
next-action: 待 owner 批准范围（§2 三档）；批准后从 P0-1 开工
---

# 标的详情页整改（`/company/:symbol`）实施计划

> **For agentic workers:** 本文件即 spec。**按 §2 范围裁定执行，不要平铺做完所有 Task**——
> 价值集中在 P0（约占工作量 35%、价值约 70%）。编号规则：**`F*` = 审计事实**（§1），
> **`P0-*` / `P1-*` / `P2-*` = 任务**（§4 起）。每个任务自带「改什么 / 验收 / 门禁」。
> 依赖：`P0-4`（交易所直连）是 `P1-3`（报价条）的前置；`P0-5`（联动）是 `P1-1`（ribbon）的前置。
> 契约暂驻本文件（S4「行业/公司/宏观」在 [docs/specs/README.md](../docs/specs/README.md) 仍是
> catalog-only），S4 立项时把 `P0-5` / `P1-1` / `P1-2` 的验收判据回填进 spec。

**Goal:** 让详情页从「数据罗列」变成「能回答三个问题」的界面——
**它现在什么状态**（行情 + 估值分位）、**那天为什么涨/跌**（图内事件标记）、**我要做什么**（记一笔）。
同时修掉 5 处「页面在说假话」的缺陷。

## ⚠️ 本计划的上限（先读这段）

做完后页面会从「半成品」变成**一个不丢人的通用行情页**——仍然是任何一家都能抄的合格品。
**它不碰差异化层**：投研视角下，猪企详情页的「它现在什么状态」应该是能繁母猪存栏、自繁自养利润、
行业产能去化、成本曲线位置（`industry_metrics` 里已有一部分），而不是 PE 分位。
那是另一个计划（S4）。**批准前请确认你要的是"合格"还是"有辨识度"。**

---

## 1. 审计事实表

| 编号 | 事实 | 落点 | 用户可见后果 | 档 |
|---|---|---|---|---|
| F1 | 成长三张卡恒为 `--`：接口**没有** `roe`/`revenue_growth`/`profit_growth` 字段，`FundamentalCards` 却在读 | [schemas/stock.py:16-69](../backend/app/schemas/stock.py) ↔ [FundamentalCards.tsx:24-28](../frontend/src/features/stock-detail/components/FundamentalCards.tsx) | 概览说「无数据」，财务页却有 | **P0 假数据** |
| F2 | 龙虎榜 tab 对绝大多数股票恒为空：`/market/dragon-tiger` **无 `symbol` 参数**，前端取全市场最新日 top50 再 filter | [market_data.py:92-103](../backend/app/api/v1/market_data.py)、[RelatedEvents.tsx:36](../frontend/src/features/stock-detail/components/RelatedEvents.tsx) | 上榜过的票显示「暂无上榜记录」 | **P0 假数据** |
| F3 | 5 张相关数据表**查询失败**时也显示「暂无相关公告」——`dataSource={x.data ?? []}` 且无 `isError` 分支 | [RelatedEvents.tsx:88-114](../frontend/src/features/stock-detail/components/RelatedEvents.tsx) | 网络/后端故障被伪装成「这只票没公告」 | **P0 假数据** |
| F4 | K 线竖线只在本 pane：缺顶层 `axisPointer.link` | [klineOption.ts:85](../frontend/src/shared/ui/kline/klineOption.ts) | **用户反馈的现象** | **P0** |
| F5 | 悬停成交量区 tooltip 为空白：formatter 找不到 candlestick 就 `return ""` | [klineOption.ts:54-55](../frontend/src/shared/ui/kline/klineOption.ts) | 下半区「有框无内容」 | **P0** |
| F6 | 进详情页最多 3 次串行请求：按 `[SH,SZ,BJ]` 逐个探测 | [stocks.ts:32,199](../frontend/src/shared/api/stocks.ts)、[quotes.ts:23,36](../frontend/src/shared/api/quotes.ts) | 北交所票 3 个 RTT | **P0** |
| F7 | 类型层无护栏：`StockRecord` 把后端没有的字段声明成可选（`roe?: number`），TS 永远不报错 | [types/index.ts:63-65](../frontend/src/shared/types/index.ts) | **F1 会以另一个字段名复发** | **P0 根因** |
| F8 | 估值/财务两张图不跟主题：直接 `import ReactECharts`，绕过 `withChartTheme` | [ValuationTab.tsx:6](../frontend/src/features/stock-detail/components/ValuationTab.tsx)、[FinancialTab.tsx:2](../frontend/src/features/stock-detail/components/FinancialTab.tsx) vs [EChart.tsx:56-83](../frontend/src/shared/ui/EChart.tsx) | 暗色下轴/图例近不可读 | P1 |
| F9 | 成交量区无 Y 轴刻度（`axisLabel.show:false`） | [klineOption.ts:116-122](../frontend/src/shared/ui/kline/klineOption.ts) | 量能只能靠 tooltip 读 | P1 |
| F10 | MA 数值行显示「最后一根」，hover 不跟随；全图信息塞在浮动 tooltip 里挡住 K 线 | [KlineChart.tsx:118-147](../frontend/src/shared/ui/kline/KlineChart.tsx) | 判断不了金叉/死叉发生在哪一天 | P1 |
| F11 | 事件与 K 线空间上完全割裂：公告/龙虎榜/解禁只在页面底部列表 | [RelatedEvents.tsx](../frontend/src/features/stock-detail/components/RelatedEvents.tsx) | **「那天为什么涨」只能自己翻** | **P1 最高价值** |
| F12 | 财务指标趋势：`histories[0].points` 假设六指标期间对齐；`smooth:true` 把季度数据连成曲线 | [FinancialTab.tsx:147-161](../frontend/src/features/stock-detail/components/FinancialTab.tsx) | 缺一期即整列错位；趋势被平滑伪造 | P1 |
| F13 | Tab 不进 URL（`defaultActiveKey`） | [pages/stock-detail/index.tsx:113](../frontend/src/pages/stock-detail/index.tsx) | 刷新回落概览，财务 tab 不可分享 | P1 |
| F14 | K 线只占 `lg={14}`；顶部 SW/自定义/概念三块各占一行 | [pages/stock-detail/index.tsx:101-133](../frontend/src/pages/stock-detail/index.tsx) | 图被压到半宽、首屏被推下去 | P1 |
| F15 | 52 周高低、估值分位在概览页看不到（分位后端已返回） | [ValuationTab.tsx:124-151](../frontend/src/features/stock-detail/components/ValuationTab.tsx) | 概览只有 4 个绝对值，看不出「贵不贵」 | P1 |
| F16 | 文档漂移：`docs/features.md` 声称个股详情有「资金流」，页面无此物 | [docs/features.md:31](../docs/features.md) | 契约与实现不符 | P1 |
| F17 | 公司概况字段（`full_name`/`list_date`/`area`/`act_name`）已在响应里，前端连类型都没映射 | [schemas/stock.py:16-39](../backend/app/schemas/stock.py) ↔ [types/index.ts:47-75](../frontend/src/shared/types/index.ts) | 白给的信息不展示 | P2 |
| F18 | 整页 8 个 query 无分块错误边界、无固定高度占位 | [pages/stock-detail/index.tsx](../frontend/src/pages/stock-detail/index.tsx) | 单块失败整页空 / 加载时 CLS 抖动 | P2 |

### 对上一轮口头判断的两处更正

1. **「特征雷达后端已做好」不成立。** 端点 `/exchanges/{exchange}/stocks/{symbol}/features/radar`
   在（[stocks.py:262-274](../backend/app/api/v1/stocks.py)），但全仓**没有任何代码写
   `stock_features` 表**（`feature_repo.upsert_features` 无调用方），且 `RadarAxis.percentile`
   恒 `None` ⇒「有端点、无数据、无分位」。
2. **「北向持股 / 高管增减持」不是「有数据没出口」。** 两个 service 只有 ingest 侧，
   读面三件套（repo 查询 / service 读方法 / 端点）**完全不存在**。

### 对本计划第一版的三处自我更正

1. **第一版把 10 个 Task 平铺排期（6 人日），是错的。** 价值集中在 P0，其余是「能想到的」
   而非「值得做的」。
2. **第一版漏掉了两件更值钱的事**：`F11`（图内事件标记 + 定位联动）与 `F7`（契约测试防复发）。
   第一版把事件做成了页面底部的「时间轴」——那只是列表换了个皮，没解决「为什么那天涨」。
   第一版还让 `A1`–`A18` 同时表示"审计事实"与"Task 编号"，同一串字母两种含义；
   本版改为 `F*`（事实）/ `P*-*`（任务）。
3. **第一版编了验收阈值**（首屏 ≤8 请求 / P95 <16ms / K 线 ≥800px），无基线实测。
   本版改为「改前测一次作基线，改后不劣化」，见 §6。

---

## 2. 范围裁定（owner 批准这一节即可开工）

| 档 | 覆盖事实 | 工作量 | 累计价值 | 判断 |
|---|---|---|---|---|
| **P0** | F1 F2 F3（假数据）+ F4 F5（联动）+ F6（交易所）+ F7（契约测试） | **2 人日** | ~70% | **必做。只做这一段，页面就不再撒谎** |
| **P1** | F8（主题）+ F9 F10（刻度/ribbon）+ **F11（事件标记）** + F12 F13 F14 F15 F16（重排与口径）+ 移动端 | **2.5 人日** | ~95% | 做到「不丢人的行情页」 |
| **P2** | F17（公司概况）+ F18（加载错误态）+ 股性事实行 + 记一笔 | **1.5 人日** | 100% | 按需，可与下一轮合并 |
| **砍掉** | 事件时间轴取代 5 tab · 右轴涨跌幅 · 股性雷达 | — | — | 理由见 §7 |

---

## 3. 设计决策

**D1 · 事件不做「时间轴取代 tab」，做「图内标记 + 可点列表 + 定位联动」。**
第一版把「5 个 tab 换成时间轴」当成核心决策，是把偏好当成了行业标准——同花顺/东财/雪球都是
**tabs**，富途才是统一 feed。而「不知道哪块有内容」这个真问题，角标计数 30 分钟能解决。
**真正值钱的是 `F11`**：事件标记打在 K 线上，点列表某行 → K 线跳到那天并高亮。它回答详情页
最高频的问题——「那天为什么涨/跌」，且复用同一批数据、几乎零额外请求。

**D2 · 股性先做「事实行」，不画雷达。**
雷达的前提是每根轴同量纲可比（全市场分位）；当前分位不存在（见更正 1）。**这是取舍，不是
「画雷达就是撒谎」**——第一版措辞过重。真要做，成本是新建特征生产管道，远超本页收益。
股性降为 P2，并明确它属于「看起来专业」而非「改变决策」。

**D3 · 概览三段式，图不再和 4 张绝对值卡并排。**

```
┌ 报价条（全宽）：名称/代码/交易所 · 现价+涨跌 · 今开/最高/最低/昨收 · 量/额/换手 ─┐
│                 距52周高 -18.3% · PE_TTM 3年分位 62% · 数据截至 09-10              │
├ 左 16/24 ─────────────────────┬ 右 8/24 ─────────────────────────┤
│ K 线（ribbon + 跨 pane 联动    │ 估值指标（4 项绝对值）             │
│      + 事件标记）               │ 成长与盈利（接 financial summary） │
│ 成交量                          │ [P2] 股性事实行                    │
├ 事件列表（全宽，可点定位到图）───────────────────────────────────┤
```

**D4 · 防复发靠机械门禁，不靠文档纪律。**
F1 / F3 / F7 三个「页面说假话」的共同根因是：**类型层允许前端声明后端不存在的字段**、
**查询失败没有错误分支**、**没有契约比对**。本版把「前端字段 ⊆ 后端 schema」做成 CI 检查
（`P0-6`），这是整份计划里杠杆最高的 0.5 人日。

## 4. Global Constraints

- **缺失 ≠ 0、失败 ≠ 无记录**：`null` 渲染 `--`；查询失败必须显示「加载失败 + 重试」，
  **不得**落进空态文案（`F3`）。这条同时是 P0 的验收项。
- **不重叠取数口径**：成长指标唯一来源是 `GET /financials/{symbol}/financial-summary`
  （`roe` / `revenue_yoy` / `profit_yoy`，单位 `%`）；**禁止**再往 `StockEnrichedOut` 加基本面列。
- **主题必须跟随**：所有 ECharts 一律经 [EChart.tsx](../frontend/src/shared/ui/EChart.tsx)
  或显式传 `THEME_COLORS[mode]`；裸用 `ReactECharts` 由机械检查拦住（P1 门禁）。
- **A 股涨跌色**：涨 `colors.up`（红）、跌 `colors.down`（绿）、零 `colors.flat`。
- 前端门禁唯一口径 `npx tsc --noEmit`（仓库无 eslint，见 [ADR 0006](../docs/decisions/0006-no-eslint-tsc-as-static-check.md)）。
- **零新表、零新 cron、零迁移**。

---

## P0 · 不留假数据，且不再复发（2 人日）

### P0-1 · 成长与盈利接真实数据（F1，0.3 天）

- [ ] `FundamentalCards` 增加 `summary` 入参；`GROWTH_METRICS` 取值从 `stock[key]` 改为
      `summary.metrics[key]?.value`，三键 `roe` / `revenue_yoy` / `profit_yoy`。
- [ ] 百分比渲染用既有 `<ChangeText value={v} />`（已按 `12.34 → +12.34%` 处理，
      [ChangeText.tsx:11-25](../frontend/src/shared/ui/ChangeText.tsx)）。
- [ ] 卡内加 `latest_period` 与 `quality` 徽标（把 `FinancialTab` 的 `QualityTag` 提为共用组件）。
- [ ] 概览 tab 发起 `fetchFinancialSummary`，**与财务 tab 同 key**（`["financial-summary", exchange, symbol]`）
      → 切 tab 命中缓存，不二次请求。
- [ ] **删** `StockRecord.roe` / `revenueGrowth` / `profitGrowth`（无生产者的死字段）。
- **验收**：三张卡显示真实数字；无财报数据的股票显示 `--` 且不显示 quality 徽标；切财务 tab 零新请求。
- **门禁**：`tsc`；e2e 断言三卡非 `--`（有财报的 fixture 股票）。

### P0-2 · 龙虎榜支持按标的过滤（F2，0.5 天）

- [ ] `market_data_repo` 新增 `list_dragon_tiger_by_symbol(db, symbol, limit)`：
      **`ts_code = '{symbol}.{SZ|SH|BJ}'` 精确匹配**（索引友好），交易所无法判定时才退 `LIKE`。
      依据：S11 实测 `split_part(ts_code)` 过滤把 50 条查询从 0.015s 拉到 4.9s（[Changelog](../docs/Changelog.md)）。
- [ ] `market_data_service.get_dragon_tiger(..., symbol=None)`：**缓存键必须带 symbol**，且加 `v2`
      前缀（`market:dragon-tiger:v2:{symbol}:{date}`），不兼容旧键——旧键缓存的是全市场行，
      复用会把 A 股的榜返给 B 股（本任务唯一的静默错误风险点）。
- [ ] `api/v1/market_data.py` 加 `symbol: str | None = Query(None, ...)`，与 `block-trades` 同形。
- [ ] 前端 `fetchDragonTiger(limit, symbol?)` + `RelatedEvents` 传 symbol。
- **验收**：`GET /market/dragon-tiger?symbol=<code>` 返回该股跨多日记录；同股二次请求命中缓存。
- **门禁**：新增用例 ①symbol 过滤只回该股 ②A/B 两股交叉请求不串（缓存键隔离）③缺 symbol 时
  与改前逐字一致（回归保护）；`uv run pytest -k dragon`。

### P0-3 · 失败不再伪装成「无记录」（F3，0.2 天）

- [ ] `RelatedEvents` 5 张表各自处理 `isError`：行内 `Alert type="error"` + 「重试」按钮
      （调 `query.refetch()`），**不复用 `emptyText`**。
- [ ] 空态与错误态文案分开：`暂无相关公告`（成功且为空）vs `加载失败，请重试`（失败）。
- **验收**：mock 500 时 5 个 tab 全部显示错误 + 可重试；恢复后重试成功。
- **门禁**：单测 mock `isError` 断言渲染错误文案而非空态文案。

### P0-4 · 交易所直连（F6，0.1 天）

- [ ] 新建 `frontend/src/shared/config/exchange.ts`：

```ts
/** 6 位代码 → 交易所候选（首个最可能；无法判定时返回全部三所，保持旧行为）。 */
export function exchangeCandidates(symbol: string): Exchange[] {
  const s = symbol.padStart(6, "0");
  const head = s[0];
  if (head === "6") return ["Shanghai_Stocks"];                 // 600/601/603/605/688/689
  if (head === "0" || head === "3") return ["Shenzen_Stocks"];  // 000/001/002/003/300/301
  if (head === "4" || head === "8" || s.startsWith("92")) return ["Beijing_Stocks"];
  return EXCHANGES;                                             // 未知前缀：全部试
}
```
- [ ] `fetchStockEnrichedBySymbol` / `fetchKlineBySymbol` 遍历候选**再拼 fallback**（候选全失退三所，不回归）。
- **验收**：深市股票 Network 面板各 1 次 enriched + 1 次 kline；北交所同；未知代码仍 404 不无限重试。
- **门禁**：单测覆盖 `6/0/3/4/8/92/未知` 七种前缀；`tsc`。

### P0-5 · K 线跨 pane 联动 + 空 tooltip（F4 F5，0.5 天）

- [ ] `buildKlineOption` 顶层新增（**缺这一行就是用户看到的现象**）：

```ts
axisPointer: {
  link: [{ xAxisIndex: "all" }],
  lineStyle: { color: c.flat, type: "dashed", width: 1 },
  label: { backgroundColor: c.textPrimary, color: c.bgPage, fontSize: 10 },
},
```
- [ ] 两个 `xAxis` 各加 `axisPointer: { type: "line", snap: true }`。
      **实测修正（2026-10-19，P0-5 落地时发现）**：轴级写 `type: "cross"` 会让 ECharts 5.6.0
      抛 `pointerShapeBuilder[axisPointerType] is not a function`，十字光标与 tooltip 一起消失
      （`modelHelper.makeAxisPointerModel` 里轴级配置优先级最高，盖掉了 tooltip 派生的 `line`，
      而 `pointerShapeBuilder` 只有 `line`/`shadow`/`none`）。**`cross` 只能出现在
      `tooltip.axisPointer.type` 与顶层 axisPointer**：轴级给 `line` + `snap`，跨 pane 由顶层
      `link` 提供，十字语义由 `tooltip.axisPointer.type: "cross"` 提供。轴级 `triggerOn` 是惰性的
      （`AxisPointerView` 只读顶层），`triggerOn` 写在顶层。
- [ ] `tooltip.confine` 在 `narrow` 时开启。
- [ ] formatter 改「任意 series 定位」：`const i = list[0]?.dataIndex; if (i == null || !points[i]) return "";`
- **验收**（人工）：竖线贯穿 K 线与成交量两个 pane、横线只在当前 pane、吸附蜡烛中心、触屏点按出十字；
      悬停成交量区 tooltip 完整 11 行。
- **门禁**（**本题唯一的回归护栏，必须写**）：option 单测断言
      ①`axisPointer.link` 深等于 `[{xAxisIndex:'all'}]` ②两个 `xAxis[].axisPointer.type === 'line'`
      **且不是 `'cross'`**（断言"不能写成 cross"本身，见上面的实测修正）③`tooltip.axisPointer.type === 'cross'`
      ④formatter 在 `params=[{dataIndex:3,seriesType:"bar"}]` 下返回非空且含日期。

### P0-6 · 契约测试防 F1 复发（F7，0.5 天，杠杆最高）

- [ ] 新增 `frontend/scripts/check-api-fields.mjs`：
  - 正则从 `backend/app/schemas/stock.py` 抽取 `StockOut` + `StockEnrichedOut` 的字段名集合；
  - 从 `frontend/src/shared/types/index.ts` 抽取 `StockRecord` 字段，`camelCase → snake_case`；
  - 断言 **`StockRecord` 的每个字段都在后端字段集合里**（单向：前端不许声明不存在的字段）；
  - 例外走显式允许清单（如 `id`），**加一项必须在清单里写理由**。
- [ ] 挂进 `frontend/package.json` 的 `check:routes` 同级（如 `check:api-fields`），并入 `self_review.sh`。
- [ ] 反向验证：手工给 `StockRecord` 加一个 `dividendYield?: number`，脚本必须红。
- **验收**：脚本对当前代码绿；注入假字段后红；删掉 F1 那三个字段后仍绿（证明不是靠白名单混过去）。
- **门禁**：`bash scripts/self_review.sh` 含该检查；红/绿对照写进 PR 说明。

---

## P1 · 让它像个专业行情页（2.5 人日）

### P1-1 · ribbon + MA 跟随 hover（F10，0.5 天）

- [ ] `KlineChart` 增加 `hoveredIndex` 状态，`onChartReady` 后订阅：

```ts
instance.on("updateAxisPointer", (e) => {
  const idx = e.axesInfo?.[0]?.value;
  setHoveredIndex(typeof idx === "number" ? idx : null);
});
```
- [ ] 图上方 MA 行升级为 ribbon：`日期 开 高 低 收 涨跌幅 量 额` + 4 条 MA，取值一律
      `hoveredIndex ?? points.length - 1`。
- [ ] **关键**：`option` 的 `useMemo` 依赖**不含** `hoveredIndex`（只驱动 DOM 行），否则鼠标每动
      一像素就重建 2400 点 option（`notMerge` 下必然掉帧）。用 `requestAnimationFrame` 节流 setState。
- [ ] 浮动 tooltip 降级为次要（窄屏依赖它）。
- **验收**：ribbon 与 MA 跟随 hover；鼠标移出恢复最后一根；10 年日线连续移动无肉眼卡顿。
- **门禁**：单测断言 `buildKlineOption` 对同一组入参**引用稳定**（hover 不进入其依赖）；
      e2e 断言 ribbon 日期随 hover 变化。

### P1-2 · 图内事件标记 + 列表定位联动（F11，0.5 天，本计划最高价值项）

- [ ] 新增 `features/stock-detail/components/eventModel.ts`（纯函数）：

```ts
export type EventKind = "announcement" | "dragon-tiger" | "block-trade" | "share-float" | "repurchase";
export interface StockEvent { kind: EventKind; date: string; title: string; meta: string[]; url?: string; }
/** 按日期聚合到「一根 K 线一个气泡」：同日多类事件合并成一条，避免 markPoint 重叠。 */
export function toMarkPoints(events: StockEvent[], dates: string[], maxBubbles = 8): MarkPointSpec[];
```
- [ ] K 线 series 加 `markPoint`：气泡按类型配色（公告=蓝 / 龙虎榜=橙 / 大宗=紫 / 解禁=黄），
      **只标最近 8 个**，同日多事件合并为 `公告+1`。
- [ ] 事件列表行可点击 → `dispatchAction({ type: "dataZoom", startValue: i-20, endValue: i+20 })`
      + 该日蜡烛高亮（`dispatchAction({ type: "showTip", dataIndex: i })`）。
- [ ] 列表保留类型 chip（全部/公告/龙虎榜/大宗/解禁/回购）作为**筛选器**，不做「时间轴取代 tab」（见 D1）。
      **Ruling R21（2026-10-19 落地时撤销角标计数）**：计划原写「每类角标计数（`limit=1` 取总数）」，
      与同一条「零额外请求」以及既有 e2e（断言打开详情页时不得有 `/dragon-tiger` 请求）直接冲突——
      角标要为 4 类未加载查询各发一次首屏请求。决定：**不做计数**，列表点一下就能看到有无内容。
- **验收**：K 线上能看到气泡且不重叠；点列表任一行，图跳到该日 ±20 根并高亮；
      **周/月频率下不得跳到错误日期**（实测缺陷：索引是按日线解析、却施加到聚合序列上 →
      落在最新几根并显示错日期；修法见 R22）；某类无记录时不画气泡。
- **门禁**：`eventModel.test.ts` 覆盖 ①同日多类合并 ②超过 8 个只留最近 ③日期不在 K 线范围内
      （上市前/停牌）不产生 markPoint ④空输入；`tsc`。

### P1-3 · 概览重排（URL / 报价条 / 栅格 / 标签）（F13 F14 F15，0.8 天）

- [ ] Tab 进 URL：`?tab=overview|valuation|financial`，非法值回落 `overview`，
      切换用 `setSearchParams({ tab }, { replace: true })`。
- [ ] 新增 `useStockRangeStats(symbol)`：复用 K 线查询键（`["kline", `stock-kline-${symbol}`, adjust]`），
      从已加载日线算 `high52w` / `low52w` / 距高低点 —— **零新增请求**。
- [ ] 估值分位复用 `["valuation-history", exchange, symbol, "pe_ttm", "3y"]` 的 `percentiles["3y"]`
      （与估值 tab 同 key，切 tab 不重复请求）。
- [ ] `StockHeader` 改紧凑报价条（一行 8 项 + 一行次级指标），「数据截至」右对齐到条尾。
- [ ] 栅格：K 线 `lg={16}`、侧栏 `lg={8}`、事件列表全宽；SW/自定义/概念三块合并为一个
      `flexWrap` 行，超出折叠 `+N`。
- **验收**：`?tab=financial` 深链直达且刷新保持；报价条的 52 周与分位与估值 tab **逐字一致**；
      390px 下标签区 ≤2 行且无横向溢出。
- **门禁**：`node scripts/check-routes.mjs` 保持绿；`useStockRangeStats` 单测（250 根窗口）；
      e2e 深链 + 390px 溢出断言。

### P1-4 · 主题跟随 + 财务口径 + 文档漂移（F8 F12 F16，0.7 天）

- [ ] `ValuationTab`、`FinancialTab` 的 `ReactECharts` 换 `EChart`（`height` 透传 340/320）。
- [ ] `MetricsHistoryChart`：期间取六指标 key 的**并集**并排序，每序列按 `period` 对齐；
      `smooth: false`、`connectNulls: false`；`buildStatementTable` 的 `rows.find()` 改 `Map`。
- [ ] `docs/features.md:31` 修正：删「资金流」（页面无此物）、删 `/features`（端点无数据），
      补「事件标记 · 报价条（52 周 + 估值分位）」。
- **验收**：暗色下两图轴/图例跟随；人为让某指标缺一期，图上**断开**而非错位；报表数值与改前逐字一致。
- **门禁**：新增 `check-chart-theme.mjs` 断言 `from "echarts-for-react"` 只出现在
      `EChart.tsx` 与 `kline/KlineChart.tsx`（白名单）；`tsc`；单测（期间并集/缺口）。

---

## P2 · 按需（1.5 人日）

### P2-1 · 加载 / 错误 / 空态（F18，0.5 天）
- [ ] 四块独立 `ErrorBoundary` + 固定高度占位（图 400 / 侧栏卡 min-height），单块失败不拖垮整页。
- [ ] 空态三态文案统一：`加载中` / `无记录` / `加载失败，重试`。
- **验收**：断后端时骨架仍在，逐块显示可重试错误；加载过程无 CLS 抖动（DevTools 观察）。

### P2-2 · 记一笔（0.5 天）
- [ ] `EntryForm` 加可选 `defaultSymbol`；`StockHeader` 加按钮。
- [ ] **页面是公开路由**（[route-manifest.ts:223](../frontend/src/app/router/route-manifest.ts)）：
      未登录 → 按钮渲染为 `<Link to="/login">登录后记一笔</Link>`，**不弹表单**（否则 401 白填 200 字）。
- **验收**：匿名跳登录；登录后按 symbol 预填；提交后不跳转，按钮变「已记录 · 查看」。

### P2-3 · 公司概况 + 股性事实行（F17，0.5 天）
- [ ] `StockRecord` 补 `fullName`/`listDate`/`area`/`actName` 映射，NULL 不渲染该项。
- [ ] `characterStats(bars, 60)` 纯函数 + 单测；样本 <60 根返回 `null`（整行不渲染，不显示 0）。
- **验收**：`600519` 显示全称/上市日期/地区/实控人；新股不足 60 根时股性行不出现。

---

## 5. 移动端专章（P1 内 0.5 天，单列，P1 合计 2.5）

- 报价条：`<768` 折叠为 2 行（名称+现价 / 量额换手），52 周与分位进第二行。
- 图高：固定 400px → 窄屏 320px（K 线 220 + 量 70 + slider 20），调用方按断点给。
- **手势冲突**：`dataZoom inside` 与页面滚动争抢。窄屏只保留 `slider` 拖拽 + 双指捏合，
  单指滑动**归还给页面**（`zoomOnMouseWheel: false`、`moveOnMouseMove: false` 在 touch 下）。
- ribbon：`<768` 隐藏，信息回落到 tooltip（避免两行 DOM 挤掉 60px 画布）。
- 事件气泡：小图上只标最近 3 个，其余进列表。
- 侧栏堆叠顺序：图 → 估值 → 成长 →（P2 股性）。
- **验收**：390px 无横向溢出；单指上下滑动页面不被图吃住；触屏点按出十字光标。

## 6. 验收与门禁

**基线先测，改后不劣化**（不写死阈值——第一版编过 8 请求 / 16ms / 800px，都是估的）：

| 指标 | 怎么测 | 判据 |
|---|---|---|
| 首屏 `/api` 请求数 | Playwright 记录 `request`，1440×900，`/stock/<code>` | **实测基线（2026-10-19，主树 :3000）**：`600519` 16 次 / 探活 3 次（全上海）；`000001` **17 次 / 探活 4 次，其中 2 次打错交易所**（enriched 与 quotes 各先试上海）。**改后实测**：`000001` 的 4 次请求全部直达 Shenzen，错所探活 **2 → 0**；请求总数不变是因为 `P0-1` 同时新增了 `financial-summary`，不要用总数下结论 |
| K 线区宽度 | 1440 视口量 canvas `boundingBox().width` | **实测基线 756px**（`lg={14}`）。`P1-3` 改 `lg={16}` 后应 ≥ 850px；不回退即可 |
| hover 掉帧 | 连续移动 100 次，`performance.now` 记录 | ribbon 落地后 P95 不高于落地前（**先测再比**） |
| 无错误伪装 | 断后端 30s 后逐块检查 | 每个失败块都出现「加载失败/重试」 |
| 契约盲区 | 注入假字段 | `check-api-fields` 必红 |

**回归门禁**：`uv run pytest`（排除 e2e/bench）、`npx tsc --noEmit`、`npm run build`、
`bash scripts/doc_gate.sh`、`bash scripts/slop_scan.sh`、`bash scripts/self_review.sh --full`。

**没有埋点，所以价值无法证伪。** 若想事后回答「这些改动有没有用」，最小成本是给
`stock-detail` 加 3 个事件：tab 切换 / 记一笔点击 / 事件列表定位点击。**本计划不含埋点体系**，
若需要另立计划。

## 7. 明确不做（附理由）

| 不做 | 理由 | 留到 |
|---|---|---|
| 事件时间轴取代 5 tab | 同花顺/东财/雪球都用 tabs；真问题是「不知道哪块有内容」，角标 30 分钟解决（D1） | 若埋点显示用户在翻 tab |
| 右轴涨跌幅 | A 股用户习惯看价格轴，默认关掉后开了也没人开 | — |
| 股性雷达 | 需全市场特征生产管道（无写入方），成本远超本页收益；**是取舍不是不可能**（D2） | 特征管道立项后 |
| 北向持股 / 高管增减持 | 只有 ingest 侧，读面三件套全缺 | 单独计划 |
| 个股资金流 | `capital_flows_service` 是市场/ETF 级 | 数据源调研后 |
| 新闻 / 研报聚合 | `/news` 是全市场页 | S3 |
| 分时 / 分钟 K | 后端无分钟线落库 | 需求确认后 |
| 叠加对比（沪深300/同业） | 依赖区间归一化 + 多序列对齐 | 后续 |
| 拖选区间统计 | 拖拽已被 dataZoom 占用，需先定手势 | 后续 |
| 实时行情轮询 | 数据源是日频（`latestQuoteDate` 为 trade_date），做成「实时」是假象 | 数据源变更时 |

## 8. 风险与回滚

| 风险 | 触发 | 处置 |
|---|---|---|
| `axisPointer.link` 与 `snap` 在 dataZoom 拖拽时互相干扰 | 拖滑块时竖线卡住 | 先关 `snap` 验证；必要时 `triggerOn` 只留 `mousemove` |
| ribbon 高频 setState 抖动 | 移动端 | rAF 节流；`<768` 直接不渲染 ribbon |
| 龙虎榜缓存键改动命中旧缓存错数据 | 部署时旧键仍在 Redis | 新键 `v2` 前缀，不兼容旧键 |
| 删 `StockRecord.roe` 等字段影响别处 | 有未发现的消费者 | 删前全仓 grep（当前仅 `FundamentalCards`）；独立提交便于 revert |
| `check-api-fields` 误报（前端有意省略后端字段） | 首次接入 | 只做单向断言（前端 ⊆ 后端）+ 显式允许清单，清单项写理由 |
| 事件 markPoint 与 MA 线视觉打架 | 数据点多 | 只标最近 8 个 + 同日合并；`symbolSize` 收敛到 6 |

## 9. 执行顺序

| 序 | 内容 | 人日 | 依赖 |
|---|---|---|---|
| 1 | P0-1 / P0-3 / P0-4 | 0.6 | 无 |
| 2 | P0-2（后端 + 测试） | 0.5 | 无 |
| 3 | P0-5 联动 | 0.5 | 无 |
| 4 | P0-6 契约测试 | 0.5 | P0-1（要先删死字段） |
| 5 | — **P0 人工验收 + 发布** — | — | — |
| 6 | P1-1 / P1-2 / P1-3 / P1-4 + §5 移动端 | 2.5 | P0-4 / P0-5 |
| 7 | — **P1 验收 + 发布** — | — | — |
| 8 | P2-1 / P2-2 / P2-3 | 1.5 | 无 |

**P0 落地即发布**（页面不再撒谎、联动修好），P1 单独发布（结构变化），P2 视排期。
