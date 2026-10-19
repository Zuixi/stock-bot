# A 股官方一手数据出口核实（Wave 1 缺口：S1.7 / S1.9 / S1.12）

调研日期：**2026-10-16**（本文所有"实测"均以该日为准）
方法：web_search 定位官方栏目 → 页面/接口直取（`backend/scripts/spike_official_sources.py`，实跑 exit 0）→ 取真实响应片段。
**无法实测证实的条目一律标注「未证实」。**

## 本文的定位（别写错地方）

| 想知道 | 去哪 |
|---|---|
| 某数据「可得 / 受限 / 需自建」的**权威结论** | [`data-source.md`](./data-source.md) 附.1–附.10（**唯一真相**） |
| 本文 | **Step-0 证据台账 + 工程可行性评估**：为"是否直连官网"这一决策提供实测输入；结论裁定后回写 `data-source.md` |

> ✅ **证据可复现（Ruling 51）**：本文每条"实测"都可一键复跑 ——
> `cd backend && uv run python scripts/spike_official_sources.py`（只读、不写库；每个 probe 带**反证断言**，
> 结论一旦不成立即非零退出，例如「历史 ETF 份额取不到」「类目过滤未生效」「更早季末仍能取到」都会当场红）。
> 本文**不替代** [`data-source.md`](./data-source.md)：三态结论的权威仍是它，本文件是 Step-0 证据台账与工程评估。

---

## 0. 结论总表（TL;DR）

| # | 数据 | 官方出口 | 三态 | 覆盖与历史（实测） | PIT 字段 | 建议 |
|---|---|---|---|---|---|---|
| **D1** | **董监高及关联人**持股变动（沪） | `query.sse.com.cn/commonQuery.do`（`sqlId=COMMON_SSE_XXPL_CXJL_SSGSGFBDQK_S`） | **可得** | 窗口 2026-10-12~16 = **69 行 / 1 页**；单日 10-16 = 5 行；全历史 1990~2050 = **71,512 行 / 716 页** | **`FORM_DATE` = 填报（披露）日** ✅ | **可立即接**，但须**新开 key**（口径见 §2.D） |
| **D2** | **董监高及关联人**持股变动（深） | `www.szse.cn/api/report/ShowReport/data`（`CATALOGID=1801_cxda`） | **可得** | 全历史 124,057 行 / 6203 页；**带 `txtStart`/`txtEnd` 窗口**后 6 页 / 104 行（2.5 周） | **无披露日**（仅 `jyrq` 变动日）❌ | 可接，但 PIT 需补公告披露日或按保守可得日处理 |
| **D3** | **5%+ 股东 / 实控人**增减持与权益变动 | 巨潮 cninfo `hisAnnouncement/query`，类目 **`category_gqbd_szsh`**（股本变动） | **可得（管道已在）** | 2026-10-08~10-16 共 **1102 条**（含回购噪音） | `announcementTime` = 披露时点 ✅ | **成本最低的一条**：既有 cninfo 管道加一个类目 + 标题过滤 |
| **A** | ETF 每日份额（全市场） | 沪 `COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE=`；深 `CATALOGID=scsj_fund_jjgm` + `txtStart/txtEnd`（xlsx） | **可得** | 沪：2026-10-14 **922 只** / 2024-01-15 **518** / 2020-01-15 **174**（10-16 实测 924）；深：2026-10-14 **756** / 2024-01-15 **355** / 2020-01-15 **72** | `STAT_DATE` / `日期` ✅ | 可替代现有 6 只篮子并扩到全市场；**深市 T+1 才定稿** |
| **B** | 融资融券每日汇总 | 沪 `commonSoaQuery.do?sqlId=RZRQ_HZ_INFO` + 逐日静态 `.xls`；深 `CATALOGID=1837_xxpl` | **可得** | 两所均 **2010-03-31 起 / 4016 个交易日**；沪 Excel 实测回溯到 **2011-01-04** | `opDate` ✅（**深市行内无日期列** ❌） | **不必替换** TuShare `margin`（已 833 天、三所求和）；只作对拍 |
| **C** | 中证指数官方估值（PE / 股息率） | 中证指数官网估值出口（经 AKShare `stock_zh_index_value_csindex`） | **可得但仅近 20 行** | 实测 000300 / 000905 各 **20 行**（2026-09-21 → 10-16） | `日期` ✅ | 仅作**对拍**（附.7.4 #5 的 PE 口径核对），不进产线 |
| **E** | 月度新增开户数 | 上交所《上证统计月报》PDF | **受限（官方月度序列事实已断）** | PDF 归档**止于 2021-12**；中国结算旧月度 URL **实测 404**，只剩年度 | 月 ✅ | **不进自动化链路**（维持 S1.12 裁定） |
| **F** | 陆股通持仓（季度） | 港交所披露易 CCASS：`www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=sh\|sz`（ASP.NET POST） | **可得但只保留最近几季** | 实测沪 2026-09-30 → 2039 行（深 2269）；**2025-12-31 及更早已返回 0 行 → 历史不可回补** | 无独立披露日字段（发布日期 = 季末后第 5 个北向交易日） | 官方只作**当期快照**；历史序列仍须 `hk_hold` |
| **G** | 媒体搜索热度 | 无官方出口 | **不可得（无官方）** | — | — | 维持 S1.14 `superseded` |

> **一句话**：Wave 1 缺的**不是"有没有官方源"**，而是**决定"官方一手 vs 主源转售"的分层原则**；且
> **S1.7 的"产业资本增减持"在官网其实有两条现成路径**（D1/D2 董监高 + D3 大股东公告），
> 不必等 Wave 2 的 S3.2 全量公告抽取 —— 但**口径必须拆开，不能混装**（§2.D）。

---

## 1. 现状基线：Wave 1 的数据**本来就是多源混合**

`industry_metrics`（`industry_key='market'`）**当前 38 个 key / 12 组产地**（DB 实测）：

| tier | source | keys | 说明 |
|---|---|---|---|
| official | `chinabond_yield_curve` | 1 | **唯一一条真正的官网一手直采**（中债 10Y） |
| official | `tushare_margin` | 2 | TuShare 转售交易所两融 |
| official | `tushare_index_dailybasic` | 1 | PE 底座 |
| official | `local_calc` | 5 | 本地计算（底座为交易所口径行情） |
| derived | `local_calc` | 13 | 本地派生 |
| derived | `tushare_fut_daily+index_daily` | 6 | 期货基差 / 贴水率 |
| derived | `tushare_fund_share` | 1 | ETF 份额（**6 只篮子**） |
| derived | `tushare_margin` / `tushare_moneyflow_hsgt` | 2 | 融资买入占比 / 南向 |
| highfreq | `akshare_optbbs_qvix` | 5 | **第三方编制**（QVIX，官方 iVIX 2018 停发） |
| calc | `akshare_legulegu_fund_position` | 1 | **第三方周度估算**（公募仓位） |

**含义**：「接官网」不是从零开始的路线变更，而是给既有的混合结构**定一条可执行的分层判据**（§4）。

---

## 2. 逐条核实

### 2.A ETF 每日份额 —— 可得（沪深都可按日期回补）

| 项 | 上交所 | 深交所 |
|---|---|---|
| 出口 | `https://query.sse.com.cn/commonQuery.do`<br>`sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L` + `STAT_DATE=YYYY-MM-DD` | `https://www.szse.cn/api/report/ShowReport`<br>`SHOWTYPE=xlsx&CATALOGID=scsj_fund_jjgm` + `txtStart/txtEnd` |
| 形态 | JSON（需 `Referer: https://www.sse.com.cn/`） | **xlsx 下载**（`Referer: https://www.szse.cn/market/fund/volume/etf/index.html`） |
| 字段 | `STAT_DATE / SEC_CODE / SEC_NAME / ETF类型 / TOT_VOL`（**万份**） | `日期 / 基金代码 / 基金简称 / 基金份额` |
| 实测 | 2026-10-14 → **922 只**（10-16 为 924）；2024-01-15 → 518；2020-01-15 → 174 | 2026-10-14 → **756 只**；2024-01-15 → 355；2020-01-15 → 72 |
| 历史 | **逐日可取，实测回溯到 2015-06-30（69 只）** | **逐日可取，实测回溯到 2020-01-15** |
| PIT | `STAT_DATE` = 统计日；**份额是当日收盘后的状态量**，T 日数据 T 日晚可得 | 官网页脚自述：**T 日夜间值仅供参考，以 T+1 日早间为准** → 须 T+1 采或回改 |

**与现状的关系**：现有 `etf_share_change` 只覆盖**6 只宽基 ETF**（`ETF_BASKET_CALIBER` 自述"已知收窄：510310 / 159919 未纳入"），
且 TuShare `fund_share` 有"单次 2000 行上限、逐代码取"的硬约束。官方出口是**全市场、按日一次取**，可直接：
①把篮子补全（或改为"每个指数族按规模动态选"）；②消除逐代码取数与截断风险。

**坑**：万份→份；沪/深两套代码与 `Referer`；无区间参数（N 年 = N 次请求，须限速+断点续采+幂等）。

### 2.B 融资融券每日汇总 —— 可得（三项里最稳）

- 沪：`query.sse.com.cn/commonSoaQuery.do?sqlId=RZRQ_HZ_INFO`（明细 `RZRQ_MX_INFO`），实测 `total=4016`（**2010-03-31 起**，即两融试点开通日）；
  另有**逐日静态 Excel** `https://www.sse.com.cn/market/dealingdata/overview/margin/a/rzrqjygk{YYYYMMDD}.xls`，**实测回溯到 2011-01-04**，含**逐只证券明细**（`.xls` 是 BIFF/OLE 老格式）。
- 深：`www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1837_xxpl`，**一次返回 4016 行全量**；单日单测 `stock_margin_szse(date)` 可回一行（融资余额 12006.74 亿元）。
- **中证金融（csf.com.cn）已不是出口**：官网资料下载页只有报送接口规范；据[界面新闻](https://m.jiemian.com/article/12401404.html)，**2022-02-25 起官方微信号不再披露两融每日数据**（只留转融通）。（该条属**媒体报道**，非本轮一手页面核实。）

**坑**：深市 JSON **行内无日期字段**（须靠返回顺序 + 交易日历对齐，顺序变动即错位）；量纲沪=元 / 深=亿元；融券量沪="股/份/手"混用 / 深="亿股、亿份"；沪市汇总**含已调出标的**。

**结论**：现有 TuShare `margin` 已是 `tier=official` 且三所求和（833 天）→ **不换**，官方出口作**对拍/兜底**（尤其深市无日期列这一条，用 TuShare 反查更省事）。

### 2.C 中证指数官方估值 —— 可得但只有近 20 行

实测 `000300`：`2026-09-21 … 2026-10-16` 共 **20 行**，字段 `市盈率1 / 市盈率2 / 股息率1 / 股息率2`（`000905` 同样 20 行）。
→ 用于附.7.4 #5「`pe_csi300` 是否与官网口径一致」的**近期对拍**够用；**不能**用于历史序列。

### 2.D 产业资本增减持 —— 官网有两条路，但**口径不同，必须拆 key**（本次最重要的发现）

| 路线 | 覆盖主体 | 出口 | 形态 | 实测 | PIT |
|---|---|---|---|---|---|
| **D1/D2 交易所结构化表** | **董监高及关联人**（`DUTY`=董事/高管/核心技术人员；深市 `gxlb`=本人/亲属） | 沪 `commonQuery.do`(`COMMON_SSE_XXPL_CXJL_SSGSGFBDQK_S`)、深 `ShowReport`(`1801_cxda`) | JSON 分页 | 沪：2026-10-12~16 = **69 行 / 1 页**；10-16 单日 5 行；全历史 **71,512 行 / 716 页**（2024 全年 7345 行 / 74 页）。深：2026-10-12~16 = **73 行 / 4 页**；全历史 **124,057 行 / 6203 页** | 沪 **有 `FORM_DATE`**；深 **无** |
| **D3 cninfo 公告类目** | **5%+ 股东 / 实控人 / 机构**（权益变动报告书、减持计划及其届满、增持） | `www.cninfo.com.cn/new/hisAnnouncement/query`，`category=category_gqbd_szsh`（**股本变动**） | JSON 列表 + PDF | 2026-10-08~16 = **1102 条**（前 30 条中：回购 15 / 权益变动 8 / 减持 4 / 增持 1 → **回购噪音大，必须标题过滤**） | `announcementTime` ✅ |

**三个必须写进设计的实测坑**：

1. **口径不同**：D1/D2 是"董监高（内部人）"口径，D3 才是"产业资本（大股东/机构）"。
   现有 registry 的 `industrial_capital_net_change`（`freq=event`，单位待定）**不能**被 D1/D2 的数据直接填充 —— 那是口径混装。
   → **建议拆两个 key**（命名待 S0.4 字典落册）：`insider_net_change_daily`（董监高，日频，official）/ `industrial_capital_net_change`（大股东公告，事件驱动，待抽取）。
2. **单位不同**：沪 `CHANGE_NUM=-54000`（股）、深 `bdgs=-6.50`（**万股**）→ 归一化是必做项。
3. **补报滞后**：实测沪市一条 2024-03-05 的变动，`FORM_DATE=2026-09-20` → **增量窗口必须用披露日，不能用变动日**，否则历史补报永久漏采（这正是 ADR 0011 的 PIT 要求）。
   另：深市**无披露日字段** → 只能按"最新可得日"保守处理或回到公告取披露时点。

### 2.E 月度新增开户数 —— 受限（官方月度序列事实已断）

- **上交所《上证统计月报》里确实有开户表**（本轮已下载 PDF 并抽取复核）：2021-12 期第 69–70 页两张表 ——
  「帐户新开户状况表」（总数/A股/B股/基金，万户）与「股票帐户新开户状况表」（总数/A股/B股 × 个人/机构）。
  实测 2021-12 总数 **256.05 万户**（A股 241.73 / B股 0.04 / 基金 14.27），2021 全年 3530.41 万户，累计总户数 31080.70 万户。
  → ⚠️ **更正** `data-source.md` 附.1③ 的一处推断：那里据栏目自述（"市场行情、会员资料、上市公司资料和交易所公告"）
  推测月报可能无开户字段；**实测月报有开户表**，只是**栏目自述没列全**。
- **但 PDF 归档止于 2021-12**（`documents/` 实测 15 期：20年10月→21年12月），2022 起改为 JS 渲染的 `overview/`，未找到公开表格出口（**未证实**）。
- 中国结算：旧月度统计表 URL **实测 404**，站点地图"市场数据"下**只剩统计年报**；2025 年报（2026-06-12）只有**年度**数字（全年新增投资者 1386.95 万，且口径为"期末−期初"）。

**结论**：S1.12 已裁定的"开户数不接入"**维持不变**（当期官方月度序列不可得）；历史 PDF（≤2021-12）可选作回补，ROI 低。

### 2.F 陆股通持仓（季度）

- 机制与官方证据（季度披露、`FORM_DATE` 断点、HKEX 名义持有人定性）已在 `data-source.md` 附.1② 固化，**结论不变**。
- **待补**：季度数据的**机器可读出口**（HKEX CCASS / 沪深港通持股纪录的具体页面与形态）、历史断点边界、以及它与 TuShare `hk_hold` 的关系 —— 本轮未完成一手实测，列为 **Step-0 待验**。

### 2.F 陆股通持仓（季度）—— 官方出口**已实测到**：可得，但**只有最近几季、不可回补**

**出口**：港交所披露易 CCASS 沪深港通持股查询
`https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=sh`（沪股通）/ `?t=sz`（深股通）
→ 页面自述（**逐字**）：*"Starting from 19 August 2024, Stock Connect Northbound Shareholding information will only be available on a quarterly basis.
Information in respect of the preceding quarter will be published on the 5th Northbound trading day following the quarter end.
Shareholding information displayed represents the shareholdings of CCASS Participants in aggregate as at the end of the specified date…
This Stock Connect shareholding search facility will only display shareholding information from 17 March 2017."*

**实测（2026-10-16）**：ASP.NET WebForms，需 `__VIEWSTATE` 往返 + `__EVENTTARGET=btnSearch` + `txtShareholdingDate=YYYY/MM/DD` 的 **POST**（无 CSV/JSON 下载出口，只能解析 HTML 表）：

| 请求日期 | 返回行数 | 结论 |
|---|---|---|
| 沪 2026-09-30（季末） | **2039** | 可取 |
| 沪 2026-10-15 | 2089 | 可取（非季末也返回值，口径待核） |
| 沪 2026-06-30 / 2026-03-31 | 1958 / 1907 | 可取 |
| 沪 **2025-12-31 / 2025-06-30 / 2024-09-30** | **0** | **取不到** → 历史季度**不可回补** |
| 深 2026-09-30 | **2269** | 可取 |

返回行字段：`Stock Code / Name / Shareholding in CCASS / % of the total number of securities listed and traded on the SSE`。

**结论与影响**：
1. 官方出口**存在且可程序化**（季度一次，第 5 个北向交易日发布）——**但它是"当期快照"，不是历史序列**：
   实测更早的季末（含 12 个月内的 2025-12-31）**已返回 0 行**，与页面自述的"保留 12 个月"不一致（真实可用窗口≈最近 3 个季末，规则待核）。
2. ⇒ 若采用官方源，**必须建立季度快照纪律**（错过当期即永久缺该季），且**历史回补只能走 TuShare `hk_hold`**；
   这也意味着 `northbound_holding_quarterly`（现 registry 已登记、0 行）的**历史序列应来自主源、当期快照来自官网**（L1 取当期 + L2 补历史）。
3. 数据含义与既有裁定一致：CCASS participants **汇总**（名义持有人），**不得**读作"外资"（Ruling 19 不变）。

### 2.G 媒体搜索热度

百度指数无 cookie 被反爬（10018）、微信指数无公开入口（`data-source.md` 附.1⑤ 已实测）→ **无官方出口**，维持 S1.14 `superseded`（且状态需在 spec 落字）。

---

## 3. 工程可行性（接入一条官方腿 = 多少工作量）

### 3.1 固定成本（每条腿都一样，按既有 backfill 腿估）

`provider/client 方法` → `ingest service`（幂等 upsert）→ `registry 登记`（unit / freq / tier / caliber_version / missing_reason）
→ `worker 或脚本入口`（双轨：APScheduler + RabbitMQ，**新队列必须进 `core/mq.py` 的 `QUEUES`**）
→ `对账自愈挂载`（走既有 `reconcile_*` 尾步，不新开 cron）→ `回补脚本` → `测试（含反例）` → `文档（data-source 附.N + metric-dictionary）`。

### 3.2 本轮实测暴露的"官网特有的坑"（都与主源转售不同）

1. **无效参数静默退化为全量**：cninfo `category` 传一个不存在的 code（如 `category_gddj_szsh`）**不报错**，直接返回全量（实测同窗口 9,236 条 = 无类目的总量）
   → 写路径必须**断言过滤生效**（对比 total 或校验类目非空），否则会把全量公告灌进一个类目。
2. **官方现成封装不可直接用**：AKShare 的 `stock_share_hold_change_sse/szse` 都是**全历史翻页**（沪按 1990–2050 全窗、深 6203 页）；
   实测深市翻到第 176 页即 **`ConnectionError`（被限流/断连）** → 必须自写**按日期窗口**的增量取数（两所都实测支持窗口）。
3. **接口无 SLA**：交易所接口对高频不友好（见 2），需限速、退避、断点续采与告警。
4. **两所不对称**：单位（股 vs 万股；元 vs 亿元）、PIT 字段（沪有披露日 / 深无）、形态（JSON vs xlsx/OLE .xls）、时效（深市 ETF 份额 T+1 才定稿）。
5. **JS 渲染页面不可抓**：上交所两融/ETF、深交所两融页面表格都在接口里，抓 HTML 只能拿到页脚。

### 3.3 一句话结论

官网直采的**数据可得性**普遍没问题，**成本在"口径归一化 + PIT + 限流 + 双向对账"**，不在"能不能取到"。

---

## 4. 建议

### 4.1 分层判据（建议落成 ADR，否则每次接入都要重吵）

| 层 | 什么时候用 | tier 标注 |
|---|---|---|
| **L1 官方一手直采** | 官方是**权威一手**且出口**稳定、可按窗口增量、带披露时点** | `official` |
| **L2 主源转售（TuShare Pro）** | 需要**长历史 + 统一口径 + 已生产验证**；或官方出口无披露日/需逐页翻 | 按上游实际（`official` 若上游是交易所） |
| **L3 第三方编制** | 官方结构性不可得（iVIX 停发、无搜索热度 API、工商 521 风控） | `highfreq` / `calc`，**禁入信号链路** |

判据（5 条全满足才值得开 L1）：① 官方是唯一/权威一手；② 可按窗口增量（不必全量翻页）；③ 有**披露日**或明确可得时点；④ TuShare 没有 / 已受限 / 口径被质疑；⑤ 该指标要进**信号链路**（可复算性要求更高）。

### 4.2 按判据给 Wave 1 排序

1. **D3（cninfo 股本变动类目）** —— ✅ **2026-10-17 已落地**（类目 `equity_change` + 分页截断响亮失败 + 读面过滤/400 + 回补脚本；实机 30 行/日、重跑 0 行）；交付**原料 + 家数读面**，指标值（亿元）仍待金额抽取。
2. **A（ETF 份额官方化）** —— ✅ **2026-10-17 已裁定：不切换源**（沪市官方与 TuShare 同日逐点一致；深市值一致但日期标签差 1 个交易日）＋**篮子 6→8 已落地**（口径 `s1.7-etf-v3`）。
3. **D1/D2（董监高持股变动）** —— ✅ **2026-10-17 已落地**：`insider_net_change_daily`（沪带披露日；深 `published_at=NULL` + 显式豁免，不进回测）。
4. **B（两融）** —— **只做对拍**，不替换。
5. **C（中证指数估值）** —— 一次性对拍，写进验收记录。
6. **E（开户数）/ G（搜索热度）** —— **不做**（维持既有裁定）。
7. **F（陆股通季度持股）** —— 官方出口**已核实**（§2.F），但它**只保留最近几季**：折算下来官方只能承担**当期快照**，历史序列仍须 `hk_hold`；且必须落"季度快照纪律"（错过即永久缺）。

### 4.3 需 owner 裁定的四件事

1. **要不要立一条"官方一手优先"的 ADR**（修订/补充 ADR 0001 的单一主源纪律）？不立，则每次接源都要重问一次。
2. **S1.7 的 `industrial_capital_net_change` 是否按 §2.D 拆成两个 key**（董监高 / 大股东公告）？这直接影响 registry 与 UI 文案。
3. **深市增减持的 PIT 缺口怎么处理**：补公告披露时点（更贵、更准）／按"最新可得日"保守落库（便宜、但回测会偏乐观）？
4. **陆股通季报是否接受"官网当期快照 + 主源历史"的双源拼接**（§2.F）？若接受，需同时定义拼接处的口径断点与 `caliber_version`。

### 4.4 下一步（Step-0）

1. ~~把本轮探针脚本化~~ —— ✅ **已交付** `backend/scripts/spike_official_sources.py`（7 个 probe、带反证断言、`--probe` 可选）；
2. 结论回写 `data-source.md` 附.11（三态 + 实机记录编号 + 未验证余项），并**更正附.1③ 的月报推断**；
3. 关闭附.4 的 #1（ETF 份额路径）与 #2（开户数：结论=官方月度已断）；#7（南向每日披露）仍需单独以官方页复核——本轮只核了**北向**；
4. spec 侧：S1.7 的 key 拆分提案 + S1.14 状态落字。

---

## 附. 本文引用的一手 URL

**上交所**： [ETF 规模](https://www.sse.com.cn/market/funddata/volumn/etfvolumn/) ·
[融资融券汇总](https://www.sse.com.cn/market/othersdata/margin/sum/) · `query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE=2026-10-16` ·
`query.sse.com.cn/commonSoaQuery.do?sqlId=RZRQ_HZ_INFO` ·
`www.sse.com.cn/market/dealingdata/overview/margin/a/rzrqjygk20261015.xls` ·
[《上证统计月报》PDF 归档](https://www.sse.com.cn/aboutus/publication/monthly/documents/) ·
[2021-12 期 PDF（含开户表）](https://www.sse.com.cn/aboutus/publication/monthly/documents/c/10061118/files/2e31b357ff4641f1956279cd7decc195.pdf) ·
[董监高股份变动栏目](https://www.sse.com.cn/disclosure/credibility/supervision/change/)

**深交所 / 巨潮**： [ETF 列表](https://www.szse.cn/disclosure/fund/etf/index.html) ·
[ETF 规模（xlsx 窗口）](https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=scsj_fund_jjgm) ·
[融资融券交易](https://www.szse.cn/www/disclosure/margin/margin/index.html) ·
[董监高股份变动](https://www.szse.cn/disclosure/supervision/change/index.html) ·
`www.cninfo.com.cn/new/hisAnnouncement/query`（`category=category_gqbd_szsh`）

**中证指数 / 中国结算 / 中证金融**： [中证指数官网](https://www.csindex.com.cn/)（经 `stock_zh_index_value_csindex`） ·
[中国结算统计年报](http://www.chinaclear.cn/zdjs/tjnb/center_scsj_tlist.shtml) ·
[中国结算网站地图](http://www.chinaclear.cn/zdjs/wzdt/wzdt.shtml) ·
[中证金融资料下载](http://www.csf.com.cn/publish/main/1046/1060/index.html)

**港交所**： [Stock Connect Northbound Shareholding Search](https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=sh)（`?t=sz` 为深股通；页面自述季度化与 12 个月保留，见 §2.F）

**未证实清单**：港交所页面"保留 12 个月"自述与实测（更早季末返回 0 行）的差异规则 ·
上交所 2022 起月度开户数的公开出口 · 深市增减持的披露时点字段 · 深市两融 JSON 的日期对齐稳定性 ·
中证金融是否仍保留任何公开日频两融出口 · 南向（港股通）每日披露的 2026-10 现行状态。
