# 猪智投 · 数据源调研（data-source.md）

> 调研日期：2026-08-31。服务于《生猪养殖 v3.2 产品化 PRD》与 [实施计划](../../plans/industry-research-workbench.md)。
> `metric_key` 与后端 metric registry 命名对齐，新增/改名指标时两处同步修改。

## 一、结论总览：四层获取体系

| 层级 | 获取方式 | 成本 | 覆盖指标 |
|---|---|---|---|
| L1 免费自动 | AKShare 等开源接口，定时采集 | 0 | 价格与行情类（看板大半指标） |
| L2 官方半自动 | 定时抓取官方页面 + 人工兜底 | 0 | 产能类（能繁存栏、屠宰量、进口） |
| L3 人工维护 | batch 导入 / CSV，低频少量 | 人力 | 成本、出栏量、效率均值、政策事件 |
| L4 付费 | 涌益 / Mysteel / 卓创 | 万元级/年起 | 高频产能调研（周度能繁、出栏体重） |

**MVP 策略**：L1 + L2 + L3 足以支撑完整投资看板（周期判定、信号、仓位）；L4 是唯一付费墙，产品价值验证后再评估。周期阶段 / 交易信号 / 仓位建议**不需要外部数据源**，是规则计算。

> 泛化验证注记（2026-09-03，P6）：registry 另含演示行业 **broiler 白羽肉鸡**（申万Ⅲ 110703）的
> `chick_price` 鸡苗价格 / `broiler_price` 毛鸡价格——mock 专用演示指标（`MetricDef.mock_base`），
> 不在本数据源清单的采集规划内，仅用于验证"新行业 = 写配置"的零前端改动复制链路。

## 二、指标明细

### L1 · 免费可自动化（第一批接入）

| metric_key | 指标 | 频率 | 主源 | 备源（交叉验证） | 回补 |
|---|---|---|---|---|---|
| `hog_price` | 生猪现货均价 | 日度 | AKShare·生意社 | AKShare·搜猪网、99期货 | 历史 10 年+，一次拉全 |
| `corn_price` | 玉米现货价 | 日度 | AKShare·生意社 | 搜猪网（有专门玉米接口） | 同上 |
| `soybean_meal_price` | 豆粕现货价 | 日度 | AKShare·生意社 | — | 同上 |
| `pork_wholesale` | 猪肉（白条）批发价 | 日度 | 农业农村部·全国农产品批发市场价格信息系统 | 生意社 | 同上 |
| `piglet_price_15kg` | 仔猪价格(15kg) | 周度 | AKShare·搜猪/生意社 | 协会月度数据 | 同上 |
| `lh_future_main` | 生猪期货主力 | 日度 | AKShare·新浪期货（`LH0` 主力连续） | Tushare `fut_daily`（需积分）、DCE 官网 | 2021 上市至今 |
| `hog_corn_ratio` | **猪粮比（派生）** | 随价格日度 | **自算**：`hog_price ÷ corn_price` | 发改委周度发布值（人工校准） | 随价格回补 |
| `cpi_pork` | CPI 猪肉分项 | 月度 | AKShare 宏观接口 | 统计局 | 全历史 |

股票行情/市值/PE、ETF、可转债：**Tushare 现有管道**（`daily_basic` / `fund_daily` / `cb_daily`），标的分析直接复用，无新增工作。

### L2 · 官方数据，半自动（每月个位数数据点）

| metric_key | 指标 | 频率 | 渠道 | 说明 |
|---|---|---|---|---|
| `sow_inventory` | 能繁母猪存栏 | 月度环比 + 季度末绝对数 | **推荐抓中国畜牧业协会猪业分会**（pig.caaa.cn，每月转载"全国生猪产品数据"，HTML 规范好解析）；官方出口为五部门"生猪产品信息数据平台"；**已接入：xuantian 源**（玄田镜像，2009→now 年/季/月全历史，见 §六） | 月度多为**环比变化率**，**绝对数以统计局季度末为准**（data.stats.gov.cn）——两种口径分 source 存储 |
| `hog_inventory` | 生猪存栏 | 季度 | 国家统计局 data.stats.gov.cn；**已接入：xuantian 源**（2009→now 年/季全历史） | 可抓可查 |
| `hog_slaughter_quarterly` | 生猪出栏量 | 季度/年度 | 国家统计局；**已接入：xuantian 源**（2009→now 年/季全历史） | 长周期对比基准 |
| `pork_output` | 猪肉产量 | 季度/年度 | 国家统计局；**已接入：xuantian 源**（2009→now 年/季全历史） | registry 新增指标，随玄田通道落库 |
| `hog_slaughter_monthly` | 规模以上屠宰企业屠宰量 | 月度 | 农业农村部（协会转载） | |
| `pork_import` | 猪肉及杂碎进口量 | 月度 | 海关总署 | 猪价高位时的重要边际变量 |

### L3 · 人工维护（低频，量小，走 batch 导入）

| metric_key | 指标 | 频率 | 来源 | 维护量 |
|---|---|---|---|---|
| `company.hogs_sold_monthly` | 上市公司月度出栏量 | 月度 | 巨潮资讯网"销售简报"公告 PDF | 十几家 × 每月 1 数 |
| `company.cost_complete` | 上市公司完全成本 | 季度 | 投资者关系活动记录表、业绩会纪要 | 十几家 × 每季 1 数 |
| `industry_cost_avg` | 行业平均完全成本 | 季度 | 协会调研 / 研报 | 每季 1 数 |
| `msy` / `psy` / `feed_meat_ratio` | 行业效率均值 | 年度 | 协会年报 / 白皮书 | 每年 1 数 |
| 政策事件（收储/投放） | 事件流 | 不定期 | 发改委、华储网公告 | 事件级 |

**头均市值（`mcap_per_head`，派生）** = 总市值(Tushare) ÷ 出栏量(人工月度，年化)。出栏量入库后自动派生。

### L4 · 付费墙（后置，按需评估）

周度能繁存栏、出栏体重、样本点养殖利润等高频产能调研数据：**涌益咨询 / Mysteel / 卓创**，万元级/年起。这是行业真正的信息差所在；免费渠道下产能数据只有官方月度颗粒度，信号粒度做到周/月级即可。

## 三、派生指标定义（ingest 后计算，统一落表 `source='derived'`）

| 派生指标 | 公式 | 依赖 |
|---|---|---|
| `hog_corn_ratio` 猪粮比 | `hog_price ÷ corn_price` | L1 两个日度价 |
| `sow_inventory_mom` 能繁存栏环比 | `(本月 − 上月) ÷ 上月 × 100`（多源共存期按 registry 源优先级去重后计算） | `sow_inventory` 月度序列 |
| `mcap_per_head` 头均市值 | 总市值 ÷ 年化出栏量 | Tushare + L3 出栏量 |
| `mcap_per_head_percentile` 头均市值历史分位 | 滚动 5 年分位 | 上项时序 |
| 周期阶段 / 信号 / 仓位 | 规则引擎（猪粮比区间 + 能繁环比连续性 + 头均市值分位） | 上述全部 |

## 四、口径与坑（务必写进采集注释与 UI 提示）

1. **能繁存栏双口径**：农业农村部月度发布的是环比/同比变化率；绝对数看统计局季度末。做时序时环比序列与绝对值序列要分开存（不同 `source`），绝对值序列优先用于图表。
2. **正常保有量锚会修订**：4100（2021 方案）→ 3900（2024 方案）→ **3750 万头（2026 修订）**。参考线必须走 `industry_reference_points`（带 `effective_from`），禁止硬编码。
3. **猪粮比口径**：自算值 = 现货生猪价 ÷ 玉米价，与发改委"出场价/批发玉米价"口径略有差异，UI 标注"自算口径"；发改委周度官方值可作 L2 校准源。
4. **多源分歧原则**（PRD）：产能以农业农村部为最终基准，价格以发改委/农业农村部为官方基准，Mysteel/涌益等高频数据仅作边际参考；同一指标至少两源对比。UI 上以源徽章（官方基准/高频参考/测算）显性化。
5. **AKShare 接口变动**：接口名可能随上游改版调整（本次调研已见数据字典含"生猪信息"专区和搜猪/99期货系列接口），采集层对每个源做适配器隔离 + fixture 单测，接口失效时影响面可控。

## 五、历史回补工作量

- 价格类：AKShare 一次拉全（10 年+），无工作量。
- 能繁存栏：~~2018 至今约 100 个月度点，协会网站/历史发布会一次性整理，约半天~~ 已完成——玄田直连一次返回 2009→now 全历史年/季/月序列（2026-09-29 实测落库 79 行 = 能繁 22 + 产量/存栏/出栏各 19）；月度环比历史仍需 CAAA 文章通道逐月回补。
- 出栏量/成本：出栏量/存栏/猪肉产量已随玄田通道全历史落库（已完成）；成本随公告季滚动补，无集中回补。

## 六、参考链接

- [国务院新闻办 · 五部门生猪产品信息数据平台](http://www.scio.gov.cn/gwyzclxcfh/cfh/2021n_16129/2021n07y23r/xgbdbj_16377/202208/t20220808_307249.html)
- [中国畜牧业协会猪业分会 · 每月全国生猪产品数据](https://pig.caaa.cn)（[示例：2026年3月](https://pig.caaa.cn/html/pig_rd/pig_hydt/2026/0427/2467.html)）
- [国家统计局数据平台](https://data.stats.gov.cn)
- [AKShare 现货数据文档](https://akshare.akfamily.xyz/data/spot/spot.html) · [AKShare 数据字典（生猪信息专区）](https://akshare.akfamily.xyz/data/index.html)
- [Tushare Pro](https://tushare.pro/document/2)
- [博亚和讯（日度生猪市场评论，备用人工源）](https://www.boyar.cn/)
- [玄田数据 · 产能数据（中国养猪网，2009→now 年/季/月产能四指标）](https://zhujia.zhuwang.com.cn)（API：`POST https://xt.yangzhu.vip/data/getmapdata?ptype=7&areano=-1`，Referer 必带；2026-09-29 实测可用，月度行产量/存栏/出栏为 0 哨兵）

## 七、市场数据面数据源（2026-09-03 实测）

> 服务「市场数据面」功能（全球指数/板块资金流/北向/龙虎榜/大宗/解禁/回购/公告快讯），
> 实施计划见 [plans/2026-09-03-market-data-face.md](../../plans/2026-09-03-market-data-face.md)。
> 端点/字段/单位均经容器内 curl 与 TuShare 实测，勿再猜测；调度统一 Asia/Shanghai 时区。

### 东财 push2delay `GET /api/qt/ulist.np/get`（全球+A股指数实时快照）

- 参数：`ut=fa5fd1943c7b386f172d6893dbfba10b&fltt=2&invt=2&np=1&fields=f1,f2,f3,f4,f12,f13,f14&secids=1.000001,0.399001,0.399006,100.HSI,100.N225,100.KS11,100.DJIA,100.SPX,100.NDX`，需 UA Header。
- 响应 `data.diff[]`：`f2` 最新价（停牌为字符串 `"-"`）、`f3` 涨跌幅、`f4` 涨跌额、`f12` 代码（`N225`）、`f13` 市场、`f14` 名称（`日经225`/`道琼斯`）。
- **限频/可用性**：push2 主站对 ulist 可能 TCP 拒连（日内可变），必须走 **push2delay** 镜像；前端 60s 轮询，指数日线由 TuShare `index_global` 每日 17:30 回补。

### 东财 push2 `GET /api/qt/clist/get`（板块主力资金流）

- 行业 `fs=m:90+t:2+f:!50`、概念 `fs=m:90+t:3+f:!50`、地域 `fs=m:90+t:1+f:!50`；`pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f62&fields=f12,f14,f3,f62,f66,f72,f104,f105,f128,f136,f140,f184`，按 `f62` 降序。
- 字段：`f12` 板块代码（BK1203）、`f14` 名称、`f3` 涨跌幅、`f62` 主力净流入（**元**）、`f66`/`f72` 超大单/大单净额（元）、`f104`/`f105` 上涨/下跌家数、`f184` 主力净占比（%）、`f128`/`f140`/`f136` 主力净流入最大股名称/代码/涨跌幅（与 data.eastmoney.com/bkzj/ 排行页同源同列）。

### 大盘资金流（data.eastmoney.com/zjlx/dpzjlx.html 同源）

- 今日四档：`push2delay/api/qt/ulist.np/get?secids=1.000001,0.399001&fields=f62,f66,f72,f78,f84,f184`——f62 主力、f66 超大、f72 大单、f78 中单、f84 小单（元）、f184 主力净占比（%），沪/深两行由服务端合计。
- 历史日线：`push2his/api/qt/stock/fflow/daykline/get?klt=101&lmt=120&secid=1.000001&secid2=0.399001&ut=b2884a393a59ad64002292a3e90d46a5`——服务端合成沪深两市；kline 为 CSV 字符串：`日期,主力,小单,中单,大单,超大,占比×5,收盘,涨跌幅,成交额(亿),…`；恒等式 主力=大单+超大单（入库前校验）。当日分钟走势为同族 `fflow/kline/get?klt=1`（未接）。
- **限频**：盘中调度 mon-fri 9:00-15:55 每 5 分钟（job 内 `_is_workday/_in_trading_hours` 守卫），当日快照 upsert 覆盖；clist 类端点在 push2delay 同构镜像可用（字段/数值一致）。

### TuShare（token 在 backend/.env `TUSHARE_TOKEN`；多接口全列返回字符串，映射层归一数值）

| 接口 | 关键列与单位 | 调度（Asia/Shanghai） |
|---|---|---|
| `index_global`（ts_code 裸代码，无 A 股） | OHLCV + `pct_chg`/`swing`，`vol` 可 NaN | 每日 17:30 全指数日线幂等 upsert |
| `moneyflow_hsgt`（北向） | 全列字符串，`north_money` **万元**（实测 hgt+sgt==north_money） | mon-fri 16:10 |
| `top_list`（龙虎榜） | 金额**元**，`reason` 上榜原因长文本（入库截断 160 字符） | mon-fri 18:00 |
| `block_trade`（大宗） | `price` 元 / `vol` 万股 / `amount` 万元（实测 price×vol≈amount） | mon-fri 17:00 |
| `share_float`（解禁） | `float_share` 万股、`float_ratio` %，`ann_date` 可空（唯一约束不判重 NULL） | mon-fri 17:30 |
| `repurchase`（回购，**接口名不是 share_repurchase**） | `vol` 股、`amount` 元，`proc`∈{实施,完成,...} | mon-fri 17:40 |

### 巨潮 cninfo `POST http://www.cninfo.com.cn/new/hisAnnouncement/query`（公告快讯）

- form 表单：`pageNum/pageSize/column=szse/tabName=fulltext/seDate=YYYY-MM-DD~YYYY-MM-DD/category=<;分隔类目>/isHLtitle=true`；**column=szse 即同时覆盖沪深**（实测 002xxx 与 601xxx 混合返回）。
- 响应 `announcements[]`：`announcementId`（去重主键）、`secCode/secName`、`announcementTitle`（isHLtitle 时含 `<em>` 高亮需 strip）、`announcementTime`（**毫秒** epoch → 上海时区 wall-clock）、`adjunctUrl`（拼 `http://static.cninfo.com.cn/` 前缀为 PDF）。类目：财报=`category_yjdbg_szsh;category_bndbg_szsh;category_sjdbg_szsh;category_ndbg_szsh;category_yjygjxz_szsh;category_yjkb_szsh`，重大事项=`category_zf_szsh;category_pgjz_szsh;category_gqfpxzcs_szsh;category_lr_gqbl_szsh`。
- **限频**：每日 8-22 点每 10 分钟轮询（公告含非交易日/盘后发布，无工作日守卫），`announcement_id` DO NOTHING 去重。

### 落库与读取约定

- 7 张表：`sector_moneyflow_snapshots` / `dragon_tiger_entries` / `northbound_daily` / `block_trades` / `share_floats` / `stock_repurchases` / `announcements`；读取端点 Redis 缓存 TTL 300s；手动触发走 `POST /api/v1/tasks/fetch-market-data`（`market_data.fetch` 队列，`MarketDataJobType` 14 类 payload 每请求选一类；2026-09-18 起含 `concept_members` 概念成分采集）。
- 单位总原则：接三方行情先 curl 实测定字段与单位再写映射，消费端只做展示分档（详见 [best-practices](../references/best-practices.md)）。

### 涨跌停价与涨停池（2026-09-14 实测）

| 源 | 接口 | 内容 | 结论 |
|---|---|---|---|
| TuShare | `stk_limit`（`fields=trade_date,ts_code,pre_close,up_limit,down_limit`） | 全市场（含基金/B股，单日 5,637 行）交易所口径涨跌停价 + 原生 `pre_close` | **权威基准**，单交易日一次调用；5,499 行可映射到 A 股 `stocks`。不传 `fields` 时 `pre_close` 不返回 |
| 东财 push2ex | `/getTopicZTPool`（`ut=7eea3edcaed734bea9cbfc24409ed989`） | 当日涨停股池：`lbc` 连板数、`fbt/lbt` 首/末封板时间、`fund` 封单、`zbc` 炸板次数、`zttj{days,ct}` | 仅作**增强**（本地日线给不出封板时间/封单）。`date=YYYYMMDD` **必填**（不带 `date` 返回 `rc=102`）；`push2delay` 同路径返回空，只能用 `push2ex`；响应 `qdate` 恒为当天、不可当校验用 |
| 东财 push2ex | `/getYesterdayZTPool`、`/getTopicZBPool` | 昨日涨停今日表现 / 炸板池 | 备选对账源，不入主路径 |

> 本地化计算不依赖 `trade_cal`：窗口交易日取自 `daily_quotes` 实际存在的日期（库内无持久化交易日历，且"真有行情"本身就是最准的可用性判据）。

## 八、日历与新闻源实测（2026-09-11）

> 服务「公网首页（免登录行情面板）」Phase 4（市场日历）/ Phase 5（新闻流）的可行性输入。
> 探针脚本 [backend/scripts/spike_calendar_sources.py](../../backend/scripts/spike_calendar_sources.py)，
> 调用真实 `TUSHARE_TOKEN`（`backend/.env`，不落库、不打印）；结果由本人手抄本节。
> 探针走 `TuShareClient._query`，失败时再经底层 `_pro.query` 取上游原文——
> 客户端 `handle_error` 会把权限错误重写成 canned RuntimeError、其余错误重写成
> `failed after N retries`，直接读 `_query` 的异常拿不到逐字原文。

**结论：8 个接口 —— 5 可用 / 3 积分不足 / 0 不存在 / 0 网络不可达。**

| 接口 | 用途 | 结论 | 实测证据（2026-09-11，见下方原文） |
|---|---|---|---|
| `disclosure_date` | 财报披露计划 | **可用（区间过滤见坑）** | `end_date=20260630`（报告期）→ 5561 行；`pre_date=20260831` → 164 行；`actual_date=20260829` → 765 行；传 `start_date/end_date` 组合 → **0 行** |
| `dividend` | 分红送股 | **可用** | `ts_code=600519.SH` → 89 行，含 `div_proc/record_date/ex_date/pay_date`（本次只按单票探，未探全市场区间语义） |
| `new_share` | IPO 新股 | **可用** | `start_date=20260801&end_date=20260911` → 26 行，含 `ipo_date/price/pe/funds/ballot` |
| `trade_cal` | 交易日历 | **可用** | `exchange=SSE` 2026 全年 → 365 行；`is_open=0` 9 月 → 9 行（非交易日单列可按 `is_open` 过滤） |
| `eco_cal` | 宏观日历 | **可用（单次上限 100 行）** | 20260901–20260930 → 100 行；拉到 20260101–20261231 仍 100 行 → 每次调用硬上限 100 行，需分窗/逐日翻页 |
| `news` | 新闻快讯 | **积分不足** | 上游原文见下；客户端侧为 canned `permission denied` |
| `major_news` | 长篇通讯 | **积分不足** | 同上 |
| `cctv_news` | 新闻联播文字稿 | **积分不足** | 同上 |

**可用接口返回列名（`df.columns`，逐字抄自实测输出，供 Phase 4/5 写字段映射）：**

| 接口 | 返回列 |
|---|---|
| `disclosure_date` | `ts_code, ann_date, end_date, pre_date, actual_date` |
| `dividend` | `ts_code, end_date, ann_date, div_proc, stk_div, stk_bo_rate, stk_co_rate, cash_div, cash_div_tax, record_date, ex_date, pay_date, div_listdate, imp_ann_date` |
| `new_share` | `ts_code, sub_code, name, ipo_date, issue_date, amount, market_amount, price, pe, limit_amount, funds, ballot` |
| `trade_cal` | `exchange, cal_date, is_open, pretrade_date` |
| `eco_cal` | `date, time, currency, country, event, value, pre_value, fore_value` |

### 不可用接口的上游返回文案（逐字，仅 3 个新闻源）

> 脚本 `[RAW]` 行打印为 `Exception: <文案>`；下表为去掉该 Python 异常类名前缀后的上游原文。

```
news:       抱歉，您没有接口(news)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。
major_news: 抱歉，您没有接口(major_news)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。
cctv_news:  抱歉，您没有接口(cctv_news)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。
```

三者为**权限不足（积分档未开）**，不是接口不存在、也不是网络不可达——接口名有效，
上游以权限文案明确拒绝；同 token 下其余 5 个接口正常返回，网络本身可达。

### 坑：`disclosure_date` 的日期区间过滤待确认

该接口可用参数为 `ts_code` / `end_date`（**报告期**，如 `20260630`）/ `pre_date`（预约披露日，单日）/
`actual_date`（实际披露日，单日）。任务书初稿的 `start_date + end_date` 组合实测**返回 0 行且不报错**，
极易误判成「接口不可用」；0 行的原因**疑为区间参数不被支持**（已补单传 `end_date` 对照仍为 0，
但未单传 `start_date` 对照，故此处只作推断，不作定论）。实测对照：

| 传参 | 行数 | 含义 |
|---|---|---|
| `start_date=20260901, end_date=20260930` | 0 | 实测该参数组合返回 0 行，疑因区间参数不被支持 |
| `end_date=20260930`（单传） | 0 | 单传亦为 0，说明 `end_date` 是报告期过滤而非自然日区间 |
| `end_date=20260630` | 5561 | 该报告期（2026 半年报）全部披露计划 |
| `ts_code=600519.SH, end_date=20260630` | 1 | 单票该报告期计划 |
| `pre_date=20260831` | 164 | 当日预约披露的股票 |
| `actual_date=20260829` | 765 | 当实际披露的股票 |
| 空参 | 6000 | 上游默认上限 6000 行（全历史截断） |

Phase 4 取「未来某段日历」须**按日循环 `pre_date`**（或按报告期取 `end_date` 再本地按
`pre_date` 分桶），不能指望一次区间调用。

### 降级决策（Phase 4 / Phase 5）

- **Phase 4 市场日历：无需降级到「只做三类」。** 四类日历全部可用——
  `trade_cal`（交易日）+ `disclosure_date`（财报披露，按 `pre_date` 逐日）+ `dividend`（分红）+
  `new_share`（新股）；`eco_cal` 宏观事件**可选**纳入，但受 100 行/次上限，需按日或短窗拉取。
  任务书预留的「`eco_cal` 不可用 → 只做 财报/分红/新股 三类」分支**未触发**。
- **Phase 5 新闻流：只做公告流。** `news` / `major_news` / `cctv_news` 全部积分不足 →
  新闻快讯路径不建，复用既有巨潮 cninfo `announcements` 管道（见 §七）作为首页信息流，
  零新增外部接口。**不适用**「全网络不可达 → mock-first」约定：网络可达，仅权限档未开，
  后续如需新闻可评估 TuShare 积分升级或换源（如东财快讯），不在本 Phase 落地。
- **落库/调度**：本节点仅确认可用性，Phase 4/5 的表结构、缓存与调度在各自计划中定义；
  未改动任何生产代码。

## 附：投研工作台新增数据源可行性复核（2026-10-12）

> 本节是 [S0.3 数据源可行性复核](../specs/S0-platform-contracts.md#s03-数据源可行性复核) 的交付物，服务于
> [投研工作台 IA/数据模型计划](../../plans/2026-10-12-investment-research-platform-ia.md)：Wave 1（市场+情绪）直接照本节接源。
> 方法沿用仓库纪律「**先实机验证再写适配器**」——每源先做一次真实网络调用，把原始返回要点落进
> [附.2 实机调用记录](#附2-实机调用记录s03-要求的实机调用记录栏)，再给三态结论。**核查日期：2026-10-12**。
>
> **四栏定义**：来源 / 频率 · 历史深度 / 三态结论 / 替代方案（首列「数据源」仅作行标签）。
> 三态结论只取六种值：`可得` · `受限` · `需自建` · `可得但降级` · `可得但不可当情绪指标` · `blocked-on-verification`。
> **本轮未取得一手实机数据的项一律标 `blocked-on-verification`，并写明验证方法（去哪查 / 查什么字段 / 判断标准）；
> 不填估计值、不填模糊量级。** 与 §8「数据源风险与替代口径」的逐项对照见 [附.3](#附3-与计划-8-的逐项一致性对照)。

### 附.1 七项复核结论

| 数据源（行标签） | 来源 | 频率 / 历史深度 | 三态结论 | 替代方案 |
|---|---|---|---|---|
| ① **北向资金**（沪股通+深股通） | 披露机制两次调整的官方出处：**2024-05-13 第一阶段**（港交所不再披露沪深股通实时买入/卖出金额与成交总额；额度余额 ≥30% 只显示"额度充足"）——[证券时报《今起，北向资金重要调整！》](https://stcn.com/article/detail/1202725.html)；**2024-08-19 第二阶段**（盘后只留"沪深股通成交总额及总笔数"、ETF 成交总额、前十大成交活跃证券；**个股持股明细降为季度**）——[上海市地方金融管理局（转证券日报）2024-08-19 通稿](https://jrj.sh.gov.cn/SC212/20240819/7454806b9909434ea8309f8b566177fd.html)、上交所规则原文[《关于沪港通交易信息披露机制调整相关事项的通知》（落款 2024-07-26）](https://www.sse.com.cn/lawandrules/sselawsrules2025/global/hkexsc/c/c_20250613_10781806.shtml)。2024-08-16 及以前的历史存量仍可回溯 · **实机记录**：附.2 #1 / #2 / #3 / #16 | 现状：**成交总额日频（买卖合计、无分向）**；个股持股**季度**。**"每日净买入"自 2024-08-19 起永久终止且不可复算**（买卖分向不再披露）→ 序列存在 **2024-08-19 口径断点**，前后不可拼接成一条连续序列 | `受限` | 按 §8.1 改走**资金流四件套**：ETF 份额变化 + 两融 + 产业资本增减持 + 南向。本轮已实测：**两融** → [上交所·融资融券汇总数据](https://www.sse.com.cn/market/othersdata/margin/sum/)（官方日频）；**南向** → 港股通**保留**每日买入/卖出金额、笔数、成交总额与**每日**单只证券持股（与沪深股通"仅合计成交总额 + 季度持股"不对称）——**具体条款出处**：[证券时报 2024-05-13](https://stcn.com/article/detail/1202725.html) 正文逐字"港股通的调整主要有两方面：…每日收市后披露当日买入成交金额及笔数，卖出成交金额及笔数，成交总额及总笔数…每日收市后披露单只证券港股通投资者合计持有数量"（见附.2 #1；**属 2024-08-19 机制文本，2026-10 现状未再次实测**，已入附.4 第 7 项复核）；**产业资本增减持** → 巨潮公告类别（接口实测可用，见附.2）。**尚未通过实机验证的一项**：**ETF 份额**——本仓库现有 ETF 管道只有 `fund_daily`（价格/成交，见 [securities.py](../../backend/app/models/securities.py)），**无份额字段**；份额取数路径须 Wave 1 另行实测（Tushare `fund_share` 或交易所/东财基金份额），**不得**用 ETF 成交额或价格代替份额 |
| ② **陆股通持仓**（香港中央结算有限公司） | ①[香港交易所《沪港通及深港通常见问题》FAQ（2022-07-04 更新）](https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market/Stock-Connect/Getting-Started/Information-Booklet-and-FAQ/FAQ/FAQ_Cn.pdf)，Q2.38–Q2.41 逐字：香港结算是代表香港及海外投资者持有沪股通证券的「**名义持有人**」，投资者是「实际权益拥有人」，且**同样安排适用于深港通**；②季度持股披露安排的出处同 ① 行的 2024-08-19 机制；③[证监会令第 200 号（国务院公报 2022 年第 23 号）](https://www.gov.cn/gongbao/content/2022/content_5705841.htm)：第十三条第一款「沪股通和深股通投资者**不包括内地投资者**」，2022-07-25 施行，存量内地投资者 2023-07-24 后只能卖出 · **实机记录**：附.2 #4 / #5 | **季度**（每季度第 5 个沪深股通交易日，披露上季度末**单只证券**的沪深股通投资者合计持有数量）。历史：2024-08-16 及以前是**旧口径日频个股持股**，与新的季度序列存在断点，**不得拼成一条连续序列** | `可得但降级` | 具名「**陆股通持仓（季度）**」，**移出「外资画像」**、降级进**公司/股东股权**视图（与 §8.1、§11-B2 定案一致，本行已按此具名）。**禁止**把它读成"外资"或"内地资金"：它是名义持有人**汇总账户**，单一股东名册条目 = 全部沪深股通投资者合计（HKEX FAQ 官方口径），既不是单一外资机构，也无法从中拆出借道部分。⚠ **对 §8.1 一处理由表述的更正（结论与替代口径不变，仅证据表述）**：§8.1 写「其中绝大部分是内地资金借道香港」，本轮**未找到**支持"绝大部分"的权威证据，可核到的量级在相反方向——**（证据层级：卖方研报 / 媒体转引口径，2021-12，单源，非官方统计；时点亦早于 2024-08-19 机制）** 兴业证券 2021-12 按港交所「托管机构」口径把北向持股拆为：中资（内资/港台）机构托管 **3.54%**、外资银行（配置盘）78.8%、外资券商（交易盘）17.66%，并引证监会口径称"假外资"成交占比约 1%（[智通财经转载兴业证券《北向资金结构再拆分》](https://www.zhitongcaijing.com/content/detail/624791.html)）；叠加 ③ 的存量清理安排。→ **降级理由改为三条均有官方出处的**：名义持有人汇总账户、持仓主体不可拆分、季度低频率 |
| ③ **新增散户开户数**（中国结算 / 上交所口径） | 中国结算官网「资讯中心 → 市场数据」现仅有[统计年报](https://www.chinaclear.cn/zdjs/tjnb/center_scsj_tlist.shtml)栏目（实测无"统计月报"入口，月度「新增投资者」序列是否仍发布**未证实**）。当前市场引用的口径是**上交所月报**：[《上证统计月报》官方栏目](https://www.sse.com.cn/aboutus/publication/monthly/)（官方自述逐字："…每月15日披露"）。权威转引（均注明来自"上交所官网/上交所月报"）：[21世纪经济报道 2026-10-10](https://m.21jingji.com/article/20261010/herald/d849e4bca8384555a29102d9e516ed44_zaker.html)、[南方财经·投资通 2026-02-03](https://www.sfccn.com/2026/2-3/wNMDE1MjBfMjEwMzcwNg.html)。另有**两融新开户**口径（中证金融），本轮**未实测** · **实机记录**：附.2 #6 / #7 / #8 / #20 | 官方口径为**月度**（每月 15 日）。历史深度**未核实**：媒体转引序列可回溯至 2015 年（[南方财经·投资通 2026-02-03](https://www.sfccn.com/2026/2-3/wNMDE1MjBfMjEwMzcwNg.html) 列举"2015年4月的720万、2015年6月的712万…"，见附.2 #8；**属媒体口径，非官方序列证据**）；口径是**沪市 A 股**还是全市场 A 股，官方页面也**未证实** | `blocked-on-verification` | **验证方法（去哪查 / 查什么字段 / 判断标准）**：**①去哪查** —— 上交所官网 [《上证统计月报》](https://www.sse.com.cn/aboutus/publication/monthly/) 逐期打开最新一期，并行交叉中国结算[统计年报](https://www.chinaclear.cn/zdjs/tjnb/center_scsj_tlist.shtml)与[通知公告](https://www.chinaclear.cn/zdjs/xtzgg/center_flist.shtml)（通知公告栏目入口本轮未单独实测，仅经中国结算首页导航确认存在，见附.2 #20）；**②查什么字段** —— 月报内"新增开户数"表及**个人/机构拆分**，并读该表口径注释（沪市 or 全市场 A 股）；**③判断标准** —— **连续 ≥3 期**可稳定取到该字段 → 判 `可得`，`metric_key` **提案** `sse_new_accounts_monthly`（**命名待 S0.4 §4.2 命名字典落册**，本任务无权定名）并在指标名与 UI 上**显式标注沪市口径**；若月报内**无该字段**（官方栏目自述仅含"市场行情、会员资料、上市公司资料和交易所公告"）→ 判 `需自建`（低频人工录入 + 交易所/巨潮公告兜底）；两者皆不可得 → **不接入**，情绪模块改用两融新开户（中证金融）+ ⑤ 的东财股吧人气榜。**在通过上述任一条之前，任何"开户数"数值（含媒体转引值）不得入库、不得上 UI** |
| ④ **公募基金仓位** | [《公开募集证券投资基金信息披露管理办法》第十八条](https://www.csrc.gov.cn/csrc/c106256/c1653985/content.shtml)（证监会官网，逐字："基金管理人应当在季度结束之日起**十五个工作日**内，编制完成基金季度报告，将季度报告登载在规定网站上…"）→ 季报内的**基金资产组合**（股票占基金总资产比例）是"仓位"的一手口径。行业汇总口径另见[中国证券投资基金业协会](https://www.amac.org.cn/)（首页实测可达；公募市场数据栏目本轮未定位，不列入承诺源） · **实机记录**：附.2 #9 | **季度**（季报；滞后 = 季度结束之日起 15 个工作日内完成编制，与 §8.2「滞后 ~15 工作日」一致）。历史：季报全历史可回溯至基金成立，但形态是**报告期末快照**，不是连续高频序列 | `可得但不可当情绪指标` | 只作**背景/结构**口径（重仓股、行业配置、仓位水平），**不进情绪模块**；情绪侧改用**新成立基金份额**与 **ETF 份额**（与 §8.2 一致；份额取数路径待实测，同 ① 备注）。**禁止**把第三方"实时仓位估算"（模型推算、非披露数据）当披露值入库 |
| ⑤ **各媒体平台股市搜索热度** | ①百度指数：[index.baidu.com](https://index.baidu.com/v2/index.html)（首页实测 HTTP 200 / 2,067 bytes、标题「百度指数」但为 JS 空壳，见附.2 #21；数据接口 `index.baidu.com/api/SearchApi/index` **无 cookie 实测被反爬拦截**，返回 `{"status":10018,…,"message":"您好，百度指数监测到您疑似存在异常访问行为…"}`，见附.2 #10）；②微信指数：仅微信内小程序，本轮**未找到**公开 Web/API 入口；③东方财富·股吧人气榜：`POST https://emappdata.eastmoney.com/stockrank/getAllCurrentList`（**实测可用、无需鉴权**，返回排名 JSON，见附.2 #11）——它是**散户关注度**代理，**不是**"媒体搜索热度" · **实机记录**：附.2 #10 / #11 / #21 | 无官方稳定口径：百度指数标称**日频**（**未实测**：需登录态，本轮只记录到反爬拦截（附.2 #10），标称频率待 ①(a) 验证项确认）；股吧人气榜为**实时榜单**（历史深度未验证；返回体含 `hisRc` 字段，**原始片段见附.2 #11**，能否用于历史对比未验证） | `blocked-on-verification` | **验证方法**：**①待验证问题（任一条通过即可立项）** ——（a）**百度指数**：用真实百度账号 cookie（`BDUSS`）调用 `GET https://index.baidu.com/api/SearchApi/index?area=0&word=<关键词>&startDate=<起>&endDate=<止>`，查询字段 = 返回 JSON 中的搜索指数序列（**结构字段名必须在拿到 cookie 后一次性打印确认，不得按印象写映射**）；判断标准 = **连续 7 天、每日 1 次**调用成功率 ≥90% 且不触发 10018 风控 → 判 `可得`（**备注：需登录态**），否则判 `需自建`（自建采集 + 频控/代理）；（b）**微信指数 / 抖音指数**：确认是否存在可编程入口，无则直接排除、不纳入承诺。**②已确定的部分** —— 东财股吧人气榜可立即接入（实测无需鉴权），作为散户关注度低成本代理，但**不得**在 UI 上标成"媒体搜索热度"。**③排期** —— 按 §8.2「差异化亮点、排在最后、不做承诺」，本项**不阻塞 Wave 1** |
| ⑥ **7 天期逆回购利率（OMO）** | 央行官网[公开市场业务交易公告栏目](https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html)；本轮实机样本[《公开市场业务交易公告 [2026]第 196 号》](https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/2026101208533774222/index.html)（2026-10-12 09:20 发布，逐字："2026年10月12日中国人民银行以固定利率、数量招标方式开展了500亿元7天期逆回购操作，全额满足了一级交易商需求"；表内 期限 7 天 / 利率 1.40% / 投标量 500 亿元 / 中标量 500 亿元）。政策利率地位佐证：[新华财经 2024-07-22](https://www.cnfin.com/zs-lb/detail/20240722/4078249_1.html)——"即日起公开市场7天期逆回购操作调整为固定利率、数量招标…操作利率由此前的1.80%调整为1.70%"，并明确"将7天期逆回购操作利率明确为**主要政策利率**，MLF利率的政策色彩有所淡化"。市场侧对照：[中国货币网·回购定盘利率 FR007/FDR007](https://www.chinamoney.com.cn/chinese/bkfrr/)。（[Shibor 官网](https://www.shibor.org/)本轮 TLS 连接失败，**未核实**，不列入承诺源） · **实机记录**：附.2 #12 / #13 / #14 / #15 | **日频**（每个公开市场操作日发一期；本轮实测 2026-09-30、10-08、10-09、10-10、10-12 连续有号，见附.2 #12）。历史深度：同栏目分页实测**尾页 = [2004]第 1 号（2004-01-16），共 191 页** → **最早可回溯至 2004-01-16**；**序列连续性未验证**（单页观察不足以外推连续性） | `可得` | 不适用（本项可得）。但按 §8.2 **必须补入**资金利率清单：新增 `reverse_repo_7d_rate`（政策利率锚，央行公告一手口径）与 `fr007` / `fdr007`（市场侧，中国货币网）双轨（**三个 `metric_key` 均为提案，命名待 S0.4 §4.2 命名字典落册**）；MLF 降级为"中期资金价格"。**且 `公开市场操作` 不能只写逆回购**：§8.2 要求同时纳入**央行新工具**——买断式逆回购（2024-10 启用）与国债买卖（2024-08 启用），否则"公开市场操作"这一口径会漏掉两项主力投放/回笼渠道（**启用月份引自 §8.2，本轮未独立复核公告**，Wave 1 接入前需按附.2 #12 同栏目补验）。**口径纪律**：OMO 公告值是**操作利率（政策利率）**、不是成交加权利率，**不得**与市场侧回购利率（`fr007` / `fdr007`，以及银行间 DR007 等成交类口径）落进同一个 `metric_key`；三者应各占一个 key，单位与频率在 registry 固定 |
| ⑦ **A 股产业链上下游关系** | 无权威、免费、结构化的 A 股产业链数据源（§8.2 已认定）。本轮实测的**可用原料**：①**定期报告「前五大客户/供应商」**——巨潮资讯公告接口 `POST http://www.cninfo.com.cn/new/hisAnnouncement/query`（**实测可用**，返回 `announcementTitle` / `adjunctUrl` 指向 PDF；样例 `000876 新希望 2025年年度报告`；与本文档第七节既有巨潮管道同源）；②**工商信息**——[国家企业信用信息公示系统](https://www.gsxt.gov.cn/index.html)（**实测 HTTP 521 风控拦截**，不可程序化稳定采集）；③**研报**——卖方研报（权限/付费） · **实机记录**：附.2 #17 / #18 | 年报/半年报（客户供应商是主要定期披露项），**结构化程度为零**，必须抽取；历史深度 = 定期报告全历史（巨潮可回溯）。工商信息无稳定频率 | `需自建` | **人工策展 + 抽取**，证据优先级与 §8.2 / §9 一致：**财报客户/供应商 > LLM 抽取 > 申万粗粒度 > 工商**；每条边须 **≥2 证据源**才置 `confirmed`，否则 `pending`；`lag_days` 无数据支撑时标 `pending`。**不承诺**"全市场产业链图谱"，只做**按行业策展**（首个实例仍是生猪养殖）；环节归属走人工裁定队列，沿用既有兜底模式（稳定业务键 + 可空外键 + `unresolved_count` 外露，见 [concept.py:3](../../backend/app/models/concept.py)） |

### 附.2 实机调用记录（S0.3 要求的「实机调用记录」栏）

> 记录本轮**真实发出的调用与原始返回要点**，不是文档推断。全部为 2026-10-12 单次核查。

| # | 实测对象 | 返回要点 | 日期 |
|---|---|---|---|
| 1 | [证券时报 2024-05-13《今起，北向资金重要调整！》](https://stcn.com/article/detail/1202725.html) | HTTP 200 · 正文逐字确认"5月13日正式实施""不再披露北向资金实时买入交易金额、卖出交易金额和交易总额"，并列出盘后披露三项与季度持股安排；**同文逐字给出港股通（南向）对照条款**："每日收市后披露当日买入成交金额及笔数，卖出成交金额及笔数，成交总额及总笔数…每日收市后披露单只证券港股通投资者合计持有数量" → ① 的南向替代口径出处 | 2026-10-12 |
| 2 | [上海市地方金融管理局 2024-08-19 通稿](https://jrj.sh.gov.cn/SC212/20240819/7454806b9909434ea8309f8b566177fd.html) | HTTP 200 · 政府站点 · 逐字"每季度第五个沪深股通交易日公布上季度末单只证券的沪深股通投资者合计持有数量" | 2026-10-12 |
| 3 | [上交所《关于沪港通交易信息披露机制调整相关事项的通知》](https://www.sse.com.cn/lawandrules/sselawsrules2025/global/hkexsc/c/c_20250613_10781806.shtml) | HTTP 200 · 规则原文 · 落款"上海证券交易所 2024年7月26日" | 2026-10-12 |
| 4 | [香港交易所 Stock Connect FAQ（PDF）](https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market/Stock-Connect/Getting-Started/Information-Booklet-and-FAQ/FAQ/FAQ_Cn.pdf) | HTTP 200 · 2,187,624 bytes · 112 页 · `pdftotext` 抽取后 Q2.39/Q2.40 逐字命中"名义持有人""实际权益拥有人""同样安排适用于深港通" | 2026-10-12 |
| 5 | [证监会令第 200 号（国务院公报）](https://www.gov.cn/gongbao/content/2022/content_5705841.htm) | HTTP 200 · 逐字"沪股通和深股通投资者不包括内地投资者""自2022年7月25日起施行""2023年7月24日后，上述投资者所持A股可继续卖出" | 2026-10-12 |
| 6 | [中国结算·统计年报栏目](https://www.chinaclear.cn/zdjs/tjnb/center_scsj_tlist.shtml) | HTTP 200 · 正文仅"统计年报" · 全页面 0 次命中"统计月报""新增投资者" | 2026-10-12 |
| 7 | [上交所·《上证统计月报》栏目](https://www.sse.com.cn/aboutus/publication/monthly/) | HTTP 200 · 标题"上证统计月报" · 官方描述"…每月15日披露" · **页面为 JS 渲染，本轮未取到月报内字段值，也未找到公开 JSON API** | 2026-10-12 |
| 8 | [21世纪经济报道 2026-10-10](https://m.21jingji.com/article/20261010/herald/d849e4bca8384555a29102d9e516ed44_zaker.html) / [南方财经·投资通 2026-02-03](https://www.sfccn.com/2026/2-3/wNMDE1MjBfMjEwMzcwNg.html) | HTTP 200 · 均注明数据来自"上交所最新月报"/"上交所官网发布的数据"，含个人/机构拆分；**属媒体转引，非本轮一手取数，不得直接入库** | 2026-10-12 |
| 9 | [证监会《公开募集证券投资基金信息披露管理办法》](https://www.csrc.gov.cn/csrc/c106256/c1653985/content.shtml) | HTTP 200 · 第十八条逐字"季度结束之日起十五个工作日内，编制完成基金季度报告" | 2026-10-12 |
| 10 | 百度指数数据接口 `GET https://index.baidu.com/api/SearchApi/index`（无 cookie） | HTTP 200 · `{"status":10018,…,"message":"您好，百度指数监测到您疑似存在异常访问行为…"}` → **反爬拦截，未取到数据** | 2026-10-12 |
| 11 | 东财股吧人气榜 `POST https://emappdata.eastmoney.com/stockrank/getAllCurrentList` | HTTP 200 · 原始片段（前 2 条，逐字）：`{"globalId":"786e4c21-70dc-435a-93bb-38","message":"OK","status":0,"code":0,"data":[{"sc":"SH600693","rk":1,"rc":0,"hisRc":0},{"sc":"SH600418","rk":2,"rc":0,"hisRc":0},…]}` → **无需鉴权即可取到榜单**；字段 `sc`（代码）/ `rk`（当前排名）/ `rc`（排名变动）/ `hisRc`（历史排名，语义未验证） | 2026-10-12 |
| 12 | [央行公开市场业务交易公告 [2026]第 196 号](https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/2026101208533774222/index.html) + 同栏目列表页 | HTTP 200 · 第 196 号：2026-10-12 09:20 · "开展500亿元7天期逆回购操作" · 利率 1.40% · **列表页同一次返回中观测到的相邻号次**：第 195 号 2026-10-10、第 194 号 2026-10-09、第 193 号 2026-10-08、第 192 号 2026-09-30（即 ⑥ 频率列"2026-09-30、10-08、10-09、10-10、10-12 连续有号"的出处） | 2026-10-12 |
| 13 | 央行公开市场业务交易公告栏目**尾页** `/17081-191.html` | HTTP 200 · 最末条目 = `公开市场业务交易公告 [2004]第1号`（日期 2004-01-16）→ 历史深度 2004 至今，共 191 页 | 2026-10-12 |
| 14 | [中国货币网·回购定盘利率](https://www.chinamoney.com.cn/chinese/bkfrr/) | HTTP 200 · 标题"回购定盘利率_FR007_FDR007" · 官方口径"每天上午11:30起对外发布"，含 FR001/FR007/FR014 与 FDR 系列 | 2026-10-12 |
| 15 | [新华财经 2024-07-22 解读](https://www.cnfin.com/zs-lb/detail/20240722/4078249_1.html) | HTTP 200 · 逐字"7天期逆回购操作调整为固定利率、数量招标""由此前的1.80%调整为1.70%""将7天期逆回购操作利率明确为主要政策利率" | 2026-10-12 |
| 16 | [上交所·融资融券汇总数据](https://www.sse.com.cn/market/othersdata/margin/sum/) | HTTP 200 · 标题"融资融券汇总数据"（官方日频，北向受限后的替代源之一） | 2026-10-12 |
| 17 | 巨潮资讯公告接口 `POST http://www.cninfo.com.cn/new/hisAnnouncement/query` | HTTP 200 · 命中 `000876 新 希 望 2025年年度报告`，返回 `adjunctUrl: finalpage/2026-07-22/1225434550.PDF` → **财报原文可取** | 2026-10-12 |
| 18 | [国家企业信用信息公示系统](https://www.gsxt.gov.cn/index.html) | HTTP **521**（风控拦截）→ 工商数据**不可**程序化稳定采集 | 2026-10-12 |
| 19 | [央行 2024 年第 17 号公告（M1 口径）](https://www.gov.cn/zhengce/zhengceku/202412/content_6990650.htm) | HTTP 200 · 逐字"自统计2025年1月份数据起，启用新修订的狭义货币（M1）统计口径。修订后的M1包括：流通中货币（M0）、单位活期存款、个人活期存款、非银行支付机构客户备付金" → 序列 **2025-01 口径断点**，必须带 `口径版本`（§8.2 / S0.4 §4.4） | 2026-10-12 |
| 20 | [中国结算首页](https://www.chinaclear.cn/) | HTTP 200 · 首页导航确认「资讯中心 → 市场数据」下仅"统计年报 / 标准券折算率 / 股票质押信息 / 港股通交易结算日历"，**无"统计月报"**；③ 引用的[通知公告栏目](https://www.chinaclear.cn/zdjs/xtzgg/center_flist.shtml)本身**本轮未单独实测**（仅由首页导航确认存在） | 2026-10-12 |
| 21 | [百度指数首页](https://index.baidu.com/v2/index.html) | HTTP 200 · 2,067 bytes（对比：同站数据接口 #10 有实质返回）· 标题「百度指数」· 正文无数据、**JS 空壳**（SPA 壳页，取数须走 #10 的接口 + 登录态） | 2026-10-12 |

### 附.3 与计划 §8 的逐项一致性对照

| # | 本节三态结论 | §8 对应条目 | 是否一致 |
|---|---|---|---|
| ① | `受限` | §8.1：北向资金受限 → 替代 = ETF 份额变化 + 两融 + 产业资本增减持 + 南向 | ✔ 结论与替代口径逐字对齐（仅 ETF 份额取数路径另列为 open item） |
| ② | `可得但降级` | §8.1 + §11-B2：改名「陆股通持仓（季度）」并降级进公司/股东股权；**不把**香港中央结算持仓称作"外资" | ✔ 结论、具名、降级落点一致；仅"绝大部分是内地资金借道"这一**理由表述**按附.1② 的官方证据更正，不影响结论与替代口径 |
| ③ | `blocked-on-verification` | §8.2：口径与频率历史上多次调整，**必须先实机验证再写适配器** | ✔ 未定论即不填结论，符合 §8.2 的处理要求；验证方法已给全 |
| ④ | `可得但不可当情绪指标` | §8.2：只能作背景，不能当情绪指标；改用新成立基金份额 / ETF 份额 | ✔ 一致 |
| ⑤ | `blocked-on-verification` | §8.2：无稳定 API，需爬虫 + 反爬；差异化亮点但**排在最后**、不做承诺 | ✔ 一致，且明确**不阻塞 Wave 1** |
| ⑥ | `可得` | §8.2：补 7 天 OMO；MLF 降级为"中期资金价格"；央行新工具（买断式逆回购 / 国债买卖）不得漏 | ✔ 一致，并补入 `reverse_repo_7d_rate` / `fr007` / `fdr007`（**均为提案，待 S0.4 §4.2 落册**）命名与口径纪律，及"公开市场操作不能只写逆回购"的新工具要求 |
| ⑦ | `需自建` | §8.2 + §9：无权威免费结构化源 → 按行业策展 + 多源交叉（财报客户/供应商 > LLM 抽取 > 申万粗粒度 > 工商）；边须 ≥2 证据源才 `confirmed` | ✔ 一致 |

### 附.4 未验证余项（open items，Wave 1 接入前必须补验）

| # | 余项 | 影响 | 补验方式 |
|---|---|---|---|
| 1 | **ETF 份额**取数路径 | ① 的替代口径「ETF 份额变化」是本计划"国家队"痕迹的日频可见项，缺它则四件套少一件 | Wave 1 前实测 Tushare `fund_share` 或交易所/东财基金份额接口；**通过前不得**用 ETF 成交额/价格代替 |
| 2 | **新增散户开户数**官方序列、字段与口径（沪市 or 全市场）、历史深度 | ③ 未定论 | 见附.1③ 的①②③步 |
| 3 | **媒体搜索热度**（百度指数登录态 / 微信指数 / 抖音指数） | ⑤ 未定论 | 见附.1⑤ 的①②步 |
| 4 | **两融新开户数**（中证金融口径） | ③ 的兜底替代之一，本轮未实测 | 中证金融官网月度披露；确认字段与频率后再决定是否入库 |
| 5 | [Shibor 官网](https://www.shibor.org/) 可达性 | 资金利率的候选源之一 | 本轮 TLS 连接失败；若需 Shibor，换用中国货币网 [Shibor 栏目](https://www.chinamoney.com.cn/chinese/bkshibor/) 复核 |
| 6 | [中国证券投资基金业协会](https://www.amac.org.cn/) 公募基金市场数据栏目路径 | ④ 的行业汇总补充口径 | 站内定位栏目 URL 后复核频率与历史深度 |
| 7 | **南向（港股通）每日披露现状** | ① 的替代口径之一；本轮只核到 **2024-08-19 机制文本**（附.2 #1），未核对 2026-10 的现行披露页 | 打开港交所/交易所港股通披露页，确认"每日买入/卖出金额与笔数""每日单只证券港股通投资者合计持有数量"是否仍在发布；若已调整则同步修正 ① 的替代口径 |
| 8 | **中国结算「通知公告」栏目** | ③ 的交叉验证入口之一 | 本轮仅由[中国结算首页](https://www.chinaclear.cn/)（附.2 #20）导航确认栏目存在，未单独实测；补验方式：直接打开该栏目确认月度/年度投资者数据是否发布 |

> **口径断点汇总（Wave 1 建表时必须带 `口径版本` 与断点标记）**：北向资金 **2024-08-19**（日频净买入终止、个股持股转季度）；
> 陆股通个股持股 **2024-08-19**（旧日频 → 新季度，不可拼接）；M1 **2025-01**（纳入个人活期存款 + 非银行支付机构客户备付金，见附.2 第 19 条）。

---

## 附.5 段 B 可行性复核：大宗商品 / 外汇（S1.3 · 2026-10-16）

> 本节是 [S1.3 市场/全球市场与跨市场标的](../specs/S1-market-sentiment.md#s13-市场全球市场与跨市场标的) 里
> 「大宗商品 29 · 外汇 5」两段的**数据源可行性交付物**。方法沿用仓库纪律「**先实机验证再写适配器**」，
> 结论格式与 [附.1](#附1-七项复核结论) 一致（来源 / 频率 · 历史深度 / 三态结论 / 替代方案）。
> **核查日期：2026-10-16**（与附.1 的 2026-10-12 不同批，故单列一节，不改既有内容）。
>
> **本节的边界**：只给结论与验证方法。**未通过验证前不写适配器、不建表、不往 UI 填数字**；
> `/market/global` 页对这两段只放「数据源待接入」占位（无清单、无图表、无占位数值）。

### 附.5.1 结论总览

| 数据源（行标签） | 来源 | 频率 / 历史深度 | 三态结论 | 替代方案 |
|---|---|---|---|---|
| ⑧ **国内商品期货**（主力连续 + 单合约日线） | ①**TuShare（单一主源，优先）**：`fut_basic` / `fut_daily` / `fut_mapping`——[TuShare 期货日线文档](https://tushare.pro/document/2?doc_id=138)；②AKShare `futures_zh_daily_sina` / `futures_main_sina`（新浪 InnerFuturesNewService）；③AKShare `futures_zh_spot` / `futures_zh_minute_sina`（盘中）；④实时与规则面 `futures_comm_info` · **实机记录**：附.5.2 #1–#9 | **日频**。历史深度**深**：TuShare `fut_daily` 单次返回上限 **2000 行**（`CU.SHF` 实机 `min_date=20180710 → max_date=20261009`，须按日期窗口分片回补，不能"一次拉全"）；AKShare 新浪侧**一次调用即返回全历史**（`RB0` 4256 行 `2009-03-27` 起、`CU0` 5292 行 `2005-01-04` 起、`LH0` 1391 行 `2021-01-08` 起）；盘中另有 1 分钟线（`RB0` 1023 行）与快照 | `可得` | **不需要替代方案，但需要"品种清单"**。29 个标的的**具体品种名录**在 owner 导图里只有数量（29），没有名单，**本轮不替它发明**——名单须由投研侧裁定（`.github`/计划文件都无此清单）。建议入库键沿用既有 `industry_metrics` 的 `metric_key` 命名纪律（见 ⑪），**不新建商品专表** |
| ⑨ **国际商品期货**（原油/天然气/黄金/白银/铜/农产品） | ①AKShare `futures_foreign_hist`（新浪外盘历史）；②TuShare `fx_daily` 的 `COMMODITY` 类别（**疑似 2023-06 起停更**，见"频率/历史深度"栏）；③`futures_global_*_em`（东财全球期货）· **实机记录**：附.5.2 #10–#12 | **日频**。①实机：`futures_foreign_hist("CL")` **7689 行**，`1996-10-14 → 2026-10-12`（**深度最深、且在更新**，但 `volume`/`position` 实测恒为 0/running 空值，**只有价没有量**）；②实机：`XAUUSD.FXCM` 4000 行 `20111227 → 20261011`（在更新）、`USOil.FXCM` 4000 行 `20080312 → 20230601`（**止于 2023-06-01**）、`NGAS.FXCM` 2845 行 `20120623 → 20230601`（**同上止于 2023-06-01**）→ 该类别**对油气已停更、对贵金属仍在更**，**不得**当成一个整体源用 | `受限` | 能用但**须按品种分流**：贵金属/铜可用 TuShare `fx_daily`（`COMMODITY`），原油/天然气改走 AKShare `futures_foreign_hist`；③东财全球期货接口**本环境 `push2` 域拒连**（附.5.2 #12），未取得一手数据 → **不得**据此排期。**降级口径**：若某品种两源都拿不到，宁可不接入，也不要用"相近品种"顶替（原油 ≠ 燃料油） |
| ⑩ **外汇**（5 个标的） | ①**TuShare `fx_obasic` + `fx_daily`（单一主源，优先，本仓库客户端尚未接入）**——`fx_obasic` 实测 **69 个标的**（`FX` 40 / `INDEX` 13 / `COMMODITY` 9 / `CRYPTO` 5 / `BUND` 1 / `FX_BASKET` 1），**并自带 `trading_hours` 字段**（FX 全体实测 = `Sun 17.00 - Fri 16.55`，即**连续 5×24 小时**）；②AKShare `currency_boc_sina`（中行牌价，日频）；③AKShare `currency_boc_safe`（外管局月度）；④AKShare `fx_spot_quote`（实时买卖报价）· **实机记录**：附.5.2 #13–#19 | **日频**（`fx_daily`）。历史深度：`USDCNH.FXCM` 4000 行 `20131004 → 20261011`、`EURUSD.FXCM` 9 行（10-01→10-16 窗口内）；**4000 行/次上限**同 ⑧，长历史须分片。②`currency_boc_sina("美元")` 244 行 `2026-01-01 → 2026-10-12`（**长历史未验证**）；③`currency_boc_safe` **8062 行 `1994-01-01 → 2026-10-12`**、26 个币种列，但**是月度口径**（同一报价在月内重复，见附.5.2 #18 的 head 两行完全相同）；④`fx_spot_quote` 25 个货币对**实时买/卖报价**（无历史） | `可得` | **不适用（本项可得）**，但**取数口径必须先定**：5 个标的若指"人民币汇率"（USD/CNY、EUR/CNY、JPY/CNY、HKD/CNY、GBP/CNY），**建议走 ②`currency_boc_sina`**（中行牌价，与 A 股投研的"人民币中间价"直觉一致，日频、含 `央行中间价` 列）；若指"外盘直盘"（EURUSD/GBPUSD/AUDUSD/USDJPY/USDCNH），**走 ①`fx_daily`**。**两者不得混进同一个 `metric_key`**（牌价 vs 市场价是两种口径，同 ⑥ 的"操作利率 ≠ 成交利率"纪律）。⚠️ `currency_boc_safe` **标称月度**，同月内多行重复同一值，**只有日频需求时不得用它**（会把月度值灌成日频序列） |
| ⑪ **落库形态（两段共用，前置决策）** | **既有基建**：`industry_metrics`（`backend/app/models/industry_research.py`，S1.2 已加 `published_at` / `caliber_version`）+ `industry_metric_service._AKSHARE_SPECS`（表驱动规格）+ `AkShareClient.fetch_lh_future_daily`（**已在跑**：`metric_key = lh_future_main`、`source = akshare_sina`，见 `app/services/industry_metric_service.py:199`）· **实机记录**：附.5.2 #20 | — | `可得`（复用既有） | **建议不新建商品/外汇表**：既有表已是「scope + `metric_key` + `source` + `freq` + `period` + `value`」形状，S1.2 又把 scope 泛化到 `industry_key='market'`。29 + 5 个标的按"一个标的 = 一个 `metric_key`"落进 `industry_metrics` 即可（**`metric_key` 命名须先在 metric-dictionary 落册**，本任务无权定名——见 [S0.4 §4.2](../specs/S0-platform-contracts.md)）。**唯一需要新增的是"日线级别的商品/外汇读面"**（`industry_metrics` 的读路径是指标式，不是行情式 K 线）；若确需 K 线，须先量测再决定是否复用 `index_dailies` 的 `ts_code` 维度，**不得**直接开新表 |

> **三态取值说明**：⑧ 与 ⑪ 判 `可得`（实机取到真实数据 + 既有管道已在跑）；⑨ 判 `受限`（贵金属可得、油气停更、东财一路未验证）；
> ⑩ 判 `可得` 但**附带一句口径前置条件**（人民币牌价 vs 外盘直盘二选一，不得混用）。
> **未验证项一律 `blocked-on-verification` 并在 [附.5.5](#附55-未验证余项open-items-接入前必须补验) 写明补验方法**，本节不填任何估计值。

### 附.5.2 实机调用记录（本节专属，2026-10-16）

> 与附.2 同规矩：记录**真实发出的调用与原始返回要点**。失败项**逐字记录上游文案**。
> 全部调用环境 = 本 worktree（`backend/.venv`，`akshare 1.18.94`，`tushare` 用 `backend/.env` 的有效 token）。

| # | 实测对象 | 返回要点 | 结论 |
|---|---|---|---|
| 1 | TuShare `fut_basic(exchange="SHFE")` | **3633 行** · 列含 `ts_code / symbol / exchange / name / fut_code / multiplier / trade_unit / per_unit / quote_unit / list_date`，样例 `AO2507.SHF 氧化铝2507` | 品种/合约主数据可得 |
| 2 | TuShare `fut_daily(ts_code="CU.SHF")` | **2000 行**（**命中单次上限**）· `min_date=20180710` / `max_date=20261009` · 列含 `pre_close/pre_settle/open/high/low/close/settle/vol/amount/oi` → **分片回补是硬要求** | ⑧ 主源可用 |
| 3 | TuShare `fut_mapping(ts_code="CU.SHF", 20261001–20261016)` | **3 行** · `CU2611.SHF`（主力连续 → 具体合约的映射） | 主力连续可解析 |
| 4 | AKShare `futures_zh_daily_sina("RB0")` | **4256 行** · `2009-03-27 → 2026-10-09` · 列 `date/open/high/low/close/volume/hold/settle`（**一次调用即全历史**） | ⑧ 备用源可用 |
| 5 | AKShare `futures_zh_daily_sina("CU0")` | **5292 行** · `2005-01-04 → 2026-10-09` | 同上（历史最深样本） |
| 6 | AKShare `futures_zh_daily_sina("LH0")` | **1391 行** · `2021-01-08 → 2026-10-09`（生猪期货 2021-01 上市，深度与上市日一致） | 既有 `lh_future_main` 同源 |
| 7 | AKShare `futures_zh_spot(symbol="V0", market="CF")` | **1 行** · `symbol="PVC连续"` · `time=113000` · `current_price=4924.0` / `bid_price` / `ask_price` / `hold` | 盘中快照可得 |
| 8 | AKShare `futures_zh_minute_sina("RB0", period="1")` | **1023 行** · `2026-09-30 09:14` 起 · `datetime/open/high/low/close/volume/hold` | 分钟线可得（本任务不用） |
| 9 | AKShare `futures_comm_info(symbol="所有")` | **822 行** · 含 `交易所名称/合约名称/合约代码/现价/涨停板/跌停板/保证金/手续费/价格更新时间`；样例 `ag2610 白银2610 现价 14589.0` | 合约规则/实时面可得 |
| 10 | AKShare `futures_foreign_hist("CL")` | **7689 行** · `1996-10-14 → 2026-10-12` · 列 `date/open/high/low/close/volume/position/settlement`；**`volume` 与 `position` 实测为 0**（样例首行 `volume=0, position=0`） | ⑨ 原油：**可得但无量** |
| 11 | AKShare `futures_foreign_detail("CL")` | **4 行** · 返回的是**规格说明表**（`交易品种/交易单位/报价单位/最小变动价位/上市交易所/交易代码`），非行情 | 合约规格可得 |
| 12 | AKShare `futures_global_hist_em(symbol="伦敦金")` | **失败逐字**：`ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))` · 同批 `forex_hist_em("USDCNH")` / `forex_spot_em()` 同款失败；旁证：`push2.eastmoney.com/api/qt/clist/get` 亦 `RemoteProtocolError`，而 `futsseapi.eastmoney.com/list/113` **HTTP 200**（沪银 278 个合约）、`forex.eastmoney.com` **HTTP 200** → **失败限 `push2` 域**，非域名整体不通 | **`blocked-on-verification`**（③ 未取到一手数据） |
| 13 | TuShare `fx_obasic()`（不带参数） | **69 行** · `classify` 分布 = `FX 40 / INDEX 13 / COMMODITY 9 / CRYPTO 5 / BUND 1 / FX_BASKET 1` · 列含 **`trading_hours` / `break_time` / `pip` / `traget_spread`**；FX 全体 `trading_hours = "Sun 17.00 - Fri 16.55"` | ⑩ 标的+**时段元数据**可得 |
| 14 | TuShare `fx_daily(ts_code="USDCNH.FXCM", 20000101–20261016)` | **4000 行**（**命中上限**）· `min_date=20131004` / `max_date=20261011` · 列 `bid_open/bid_close/bid_high/bid_low/ask_open/ask_close/tick_qty`（**买卖双档**） | ⑩ 主源可用 |
| 15 | TuShare `fx_daily(ts_code="EURUSD.FXCM", 20261001–20261016)` | **9 行** · 样例 `20261011 bid_open=1.11853 bid_close=1.12046 ask_close=1.12059 tick_qty=4586` | ⑩ 可用 |
| 16 | TuShare `fx_daily(ts_code="XAUUSD.FXCM", 20000101–20261016)` | **4000 行** · `20111227 → 20261011`（**在更新**） | ⑨ 贵金属走此路 |
| 17 | TuShare `fx_daily(ts_code="USOil.FXCM" / "NGAS.FXCM")` | 各 **4000 / 2845 行**，但 `max_date` **均为 `20230601`**（油气**止于 2023-06-01**，此后无新数据） | ⑨ 油气**停更**，`受限` |
| 18 | AKShare `currency_boc_safe()` | **8062 行** · `1994-01-01 → 2026-10-12` · 26 个币种列（美元/欧元/日元/港元/…）；**head 两行（01-01 与 01-03）取值逐字相同** → 月度重复口径 | `可得`但**月度** |
| 19 | AKShare `currency_boc_sina("美元", 20260101–20261016)` · `fx_spot_quote()` | 前者 **244 行** · `2026-01-01 → 2026-10-12` · 列 `日期/中行汇买价/中行钞买价/中行钞卖价（汇卖价）/央行中间价/中行折算价`；后者 **25 行**实时买卖报价（`USD/CNY 买 6.6925 卖 6.6928`） | 人民币牌价可得 |
| 20 | 既有管道旁证（不新发调用）：`app/services/industry_metric_service.py:199` | `("lh_future_main", "akshare_sina", "fetch_lh_future_daily", "date", "close", 100000.0)` —— 商品期货**已经在往 `industry_metrics` 落数**（2026-09-03 验证批） | ⑪ 复用路径已存在 |

### 附.5.3 A50 时段元数据的证据（S1.3 段 A 用）

`market_data_service.GLOBAL_INDICES` 里 `XIN9`（富时中国A50 指数期货）的 `session` 写成
`T 09:00-16:30 / T+1 17:00-05:15（新加坡夜盘，非 A 股时段）`、`timezone=Asia/Singapore`、
`currency=CNY`。依据分三层：

| 层 | 证据 | 强度 |
|---|---|---|
| a. 标的可得 | TuShare `index_global(ts_code="XIN9")` 实机：**4000 行**（单次上限）· `max_date=20261009 close=13909.62`；同批枚举该接口 22 个 `ts_code` 含 `XIN9` | **一手实机**（本 worktree，2026-10-16） |
| b. 时段是"新交所 T+1 夜盘"而非 A 股时段 | [新交所 T+1 时段开盘时间调整通知](https://henghua.hk/cmsbigfile/2025/04/f05d5388-013d-4b0f-9444-3e33f1810940/SGSHIJIAN.pdf)、[SGX 指数商品交易时间异动通知](https://www.ibff.com.tw/Support/Bulletin/75ad66b9-7818-439d-9687-6b736fa4f4a1/2025SGX%E4%BA%A4%E6%98%93%E6%99%82%E9%96%93%E7%95%B0%E5%8B%95%E9%80%9A%E7%9F%A5.pdf)（券商转发的交易所通知） | 二手（交易所公告转载）；**`sgx.com` 本环境 DNS 不可达，未取到官网一手页** |
| c. "夜盘"这一事实与本项目的相关性 | A 股现货 15:00 收盘后，A50 仍在交易（T+1 时段）→ 它是 A 股休市期间的**唯一可交易代理**，故导图要求页面明示、不得与 A 股时段混淆 | 结论性判断（不依赖 a/b 的精确分钟数） |

> **`T 09:00-16:30 / T+1 17:00-05:15` 的精确分钟数属"展示用元数据"**：a 只证明"标的可得"，
> b 只证明"存在 T+1 夜盘且在 17:00 后仍交易"，**两者都不足以逐分钟证实上述两个区间**。
> 若要把它当成"交易时段可用性判据"（例如据此判断"现在能不能下单/取快照"），
> **必须**先补一次官网一手核对（SGX 官网合约规格页的 "Trading Hours" 栏，或用
> `fx_obasic` 同款的 `trading_hours` 字段——本任务实机确认该字段存在于 `fx_obasic`，
> 但 `index_global` 的返回**没有**该字段）。在此之前，本字段只承担"**不与 A 股时段混淆**"
> 这一 UI 提示职责，不得用于任何自动化判定。

### 附.5.4 与 S1.3 覆盖数的对照

| spec 条目 | 数量 | 本轮结论 | 差距 |
|---|---|---|---|
| 美国市场 | 6 | **已交付**（`DJI/SPX/IXIC/NDX/SOX/HXC`，其中 `IXIC` 为纳斯达克综合、`NDX` 为纳斯达克100，两者不同标的） | 无 |
| 香港市场 | 2 | **已交付**（`HSI` 恒生指数 / `HKTECH` 恒生科技指数） | 无 |
| 其他市场 | 3 | **已交付 3/3**（`N225` 日经225 + `KS11` 韩国KOSPI + `XIN9` 富时中国A50〔新加坡 · CNY 夜盘〕）；按 Ruling 40，spec 的"其他市场 3"由这三条满足 | 仅"名录未具名"：导图只给数量不给名单，故上表按 `market` 逐条列出实际成员；若后续要把"其他"扩到欧洲（`FCHI`/`GDAXI`/`FTSE`），TuShare `index_global` **已覆盖且本轮实机确认在库**（均在 22 个 `ts_code` 内）→ 可低成本扩，但**属新增范围**，需投研侧先裁名录（本任务不擅自扩） |
| 大宗商品 | 29 | **数据源已定（⑧`可得`）**，名录未定 → 未接入 | 名录 + `metric_key` 落册 |
| 外汇 | 5 | **数据源已定（⑩`可得`）**，名录口径未定（人民币牌价 vs 外盘直盘）→ 未接入 | 口径选择 + 名录 + `metric_key` 落册 |

### 附.5.5 未验证余项（open items，接入前必须补验）

| # | 余项 | 影响 | 补验方式 |
|---|---|---|---|
| 1 | **大宗商品 29 的品种名录** | 无法建 `metric_key` / 无法定"已接入"判据 | 投研侧裁定名录（本仓库无此清单）；裁定后按 ⑪ 逐条落 `metric_key` |
| 2 | **外汇 5 的口径**（人民币牌价 vs 外盘直盘） | 选错口径会让序列与"人民币汇率"直觉不符；两口径**不得混进同一 key** | 产品侧确认；若选牌价 → 走 ②`currency_boc_sina`（注意其长历史**未验证**，244 行只覆盖 2026 年内）；若选直盘 → 走 ①`fx_daily` |
| 3 | **`currency_boc_sina` 的长历史** | ② 只实测到 2026-01-01 起 244 行；更早是否可得未知 | 用更大 `start_date`/`end_date`（如 `20200101–20261016`）复跑，确认返回行数是否随窗口增长；不增长则记"近端窗口"并靠滚动 ingest 累积 |
| 4 | **东财全球期货 / 外汇接口在正常网络下的可用性**（附.5.2 #12） | ③ 是本轮唯一被判 `blocked` 的一手证据缺口 | 在 `push2` 域可达的环境复跑 `futures_global_hist_em("伦敦金")` / `forex_spot_em()`；返回真实行情则把 ⑨ 的"东财一路"补为可用，并把 [eastmoney_client](../../backend/app/core/providers/eastmoney_client.py) 的"本环境 push2 拒连"备注更新为最新实测 |
| 5 | **TuShare `COMMODITY` 类别停更是否为账户权限所致** | 若是权限问题，升级后可恢复油气；若上游确已停更，则 ⑨ 必须永久走新浪 | 用更高积分账户复跑 `fx_daily(ts_code="USOil.FXCM")`；若 `max_date` 仍为 2023-06-01 → 判上游停更并在此处**逐字**记录 |
| 6 | **东财 `100.IXIC` 这个 secid 是否被上游接受、且确实指向纳斯达克综合指数**（段 A 改动的 live 读路） | `market_data_service.py` 把**既有标的 `IXIC`** 的东财 secid 从 `100.NDX` 改为 `100.IXIC`（原值与本任务新增的 `NDX` 撞车，会让"两者最新值不得相等"的判据必然假通过）。两种失败形态：**(a) 良性** = 东财不认 `100.IXIC` → `IXIC` 静默从实时降级到收盘（`price_source=eod`，值来自 TuShare 日线）；**(b) 有害** = 东财认它但指向**别的标的** → 综合指数卡片显示另一个指数的价。**(b) 是唯一会"取错"的形态，必须先排除**。⚠️ **仓库内唯一一手记录**（本文档 §七 的东财 ulist 参数串）只出现 `100.NDX`，**既不能证明也不能证伪** `100.IXIC` | **去哪儿跑**：一台能连 `push2delay.eastmoney.com` 的主机（本沙箱该域 TCP 拒连，见附.5.2 #12）。**跑什么**：`backend/.venv/bin/python scripts/check_global_instruments.py --live`。**看什么**：① `IXIC` 的 `price_source` 是否 = `realtime`（=`(a)` 未发生）；② 同一次输出里 `IXIC` 的值与 TuShare `index_global(ts_code="IXIC")` 最近收盘是否同向同量级（=`(b)` 未发生）；③ 反向对照：把 secids 换成 `100.DJIA,100.IXIC` 手工发一次 `ulist.np/get`，看返回的 `f14`（名称）是"纳斯达克"还是别的。**判定标准**：三条都对 → 把本条划掉并把结论写回 `market_data_service.py` 的 `IXIC` 注释；任一条不对 → 先回退为"无 `em_secid`"（明确降级），**不得**留一个可能取错的 secid 在线上。**不阻塞 Wave 1**（失败形态 a 只是降级；形态 b 的暴露面是市场首页的一张卡） |

> **本节与附.1 的关系**：附.1 复核的是**情绪/资金/政策**侧的七个源（①②③④⑤⑥⑦），与市场的商品/外汇无交集；
> 两节的 `metric_key` **不得互相顶替**（例如附.1 ① 的"ETF 份额"与本节 ⑧ 的"商品期货持仓"是两件不同的事）。

---

## 附.6 S1.4 段：申万行业成分历史可得性（SCD2 回填的第 0 步，2026-10-12）

> 本节是 [S1.4 市场/行业分类钻取（申万）](../specs/S1-market-sentiment.md#s14-市场行业分类钻取申万) 里
> 「分类归属必须走 SCD2」那一条的**数据源可行性交付物**。方法沿用仓库纪律「**先实机验证再写适配器**」，
> 结论格式与 [附.1](#附1-七项复核结论) / [附.5](#附5-段-b-可行性复核大宗商品--外汇s13--2026-10-16) 一致
> （来源 / 频率 · 历史深度 / 三态结论 / 替代方案）。**核查日期：2026-10-12**，探针脚本
> [`backend/scripts/spike_sw_member_history.py`](../../backend/scripts/spike_sw_member_history.py)。
>
> **本节回答的唯一问题**：spec §S1.4 的验收判据「任取一个历史交易日，按当时的分类归属还原成分股
> （SCD2 生效），与当日实际分类快照一致」**今天能不能成立**——即上游有没有 `in_date` / `out_date`
> 这两个使 SCD2 历史**可回填**的字段。**没有这两个字段就只能"从今天开始记"**，那与判据原样是两回事。
>
> ### 附.6.1 结论总览

| 数据源（行标签） | 来源 | 频率 / 历史深度 | 三态结论 | 替代方案 |
|---|---|---|---|---|
| ⑫ **申万行业成分（含历史区间）** | **TuShare `index_member`（单一主源，已接入）**：`index_code=<指数代码>&fields=index_code,con_code,in_date,out_date,is_new`——[TuShare 指数成分文档](https://tushare.pro/document/2?doc_id=96)。行业代码 → 指数代码的对照表随镜像发布于 `backend/data/sw_index_mapping.csv`（生成自 [申万行业分类.md](../references/sw/申万行业分类.md)，511 行 = L1 31 + L2 134 + L3 346）· **实机记录**：附.6.2 #1–#10 | **日频可得但只在"变更时"新增行**：上游返回的是**逐期区间**（每只股票每段进出各一行），不是逐日快照。历史深度**深**：31 个一级行业逐条实测（附.6.2 #3）合计 **7,740 行区间**、`in_date` 最早 **19901210** → **可回溯到 1990 年**；覆盖度 **31/31 一级**；⚠️ **每一级都必须各自灌**（祖级 ≠ 其下 L3 的并集，见附.6.2 #11） | `可得` | **不适用（本项可得）**，故 spec §S1.4 的验收判据**无需收窄**（⚠️ 但"原样成立"要靠读路按**节点自身层级**取成员；Fix round 1 更正，见附.6.2 #11～#13）。落库形态**不新建"成分表"以外的基建**：新增 `sw_industry_member_periods`（SCD2：`effective_from` 必填 / `effective_to` 可空 / `is_current`）+ `sw_member_ingest_runs`（记录 `as_of` 上界），**既有 `sw_industry_members` 一列未动**（它是整表替换式快照，硬塞 SCD2 会被 `data/sw_seed.sql` 的 `DELETE` 清空历史） |
| ⑬ **申万分类树** | 同上接口族 **`index_classify`**——**注意 `src` 参数没有默认值语义**：`src='SW'` 实测 **0 行**，正确值是 **`src='SW2021'`**（511 行）或**不传 `src`**（359 行 = SW2014+SW2021 混装，`level` 过滤下 `L1` 只有 28 行）· **实机记录**：附.6.2 #2 | SW2021 树 = L1 31 / L2 134 / L3 346（与 `backend/data/sw_seed.sql` 的 511 节点逐数一致） | `可得` | **不适用**：本地分类树已由 `data/sw_seed.sql` 支撑，本任务**不切树源**（切源属 S1.2 的统一读面范畴，不在 S1.4）。本行只用于把"上游指数代码"与"本地行业代码"对齐 |

> **三态取值说明**：⑫ 判 `可得`（实机取到真实历史区间 + 已在 `stock_bot_test` 落 7,624 行、0 失败）；
> ⑬ 判 `可得`（实机取到 511 行 SW2021 树），但**不接入**（本地树已够用），故只留结论与 `src` 参数的坑。
> **未验证项**见 [附.6.4](#附64-未验证余项open-items)。

### 附.6.2 实机调用记录（本节专属，2026-10-12）

> 与附.2 / 附.5.2 同规矩：记录**真实发出的调用与原始返回要点**。探针 = `backend/scripts/spike_sw_member_history.py`
> （用真实 `TUSHARE_TOKEN`，不落库、不打印 token）。

| # | 实测对象 | 返回要点 | 结论 |
|---|---|---|---|
| 1 | `index_member(index_code="801010.SI")`（农林牧渔 L1） | **184 行** · 列 = `index_code, con_code, in_date, out_date, is_new` · `in_date` 非空 **184/184**（该行业 min `19930917` / max `20260702`）· `out_date` 非空 **80/184**（NULL = 至今仍是成分） | ⑫ **带历史区间**，SCD2 可回填 |
| 2 | `index_classify` 参数枚举：**不带参** / `level='L1'` / `level='L1',src='SW'` / `src='SW2021'` / `src='SW',level='L1'` | 行数依次 **359 / 28 / 0 / 511 / 0** · `src='SW2021'` 的 `level` 分布 = `{'L3': 346, 'L2': 134, 'L1': 31}` | ⑬ **`src` 必须写 `SW2021`**；`src='SW'` 静默返回 **0 行**（不是报错） |
| 3 | 31 个 L1 逐条 `index_member` 覆盖度扫描（本节收尾复核） | **31/31** 全部返回非空且都带 `in_date`；`in_date` 最早 **`19901210`**（`601988.SH` 等老银行股一级行业）；区间行数 31 个行业合计 **7,740 行**（单行业 34 ~ 738 行） | ⑫ 一级覆盖 **31/31** |
| 4 | `index_member(index_code="801011.SI")`（林业Ⅱ，L2 抽样） | **7 行** · `in_date` 7/7 · `out_date` 7/7（该行业 2014 年后无新增） | ⑫ 二级可得 |
| 5 | `index_member(index_code="850111.SI")`（种子，L3 抽样） | **13 行** · `in_date` 13/13 · `out_date` 5/13 | ⑫ 三级可得 |
| 6 | 同一 `index_member` 传 `start_date=20240102&end_date=20240102` | 返回**与不传参完全相同**（184 行） → 该接口**不接受日期窗口**，返回的是全量区间 | 只能全量拉取后本地按区间判定；不存在"按日翻页"路径 |
| 7 | `is_new='Y'` / `is_new='N'` 分裂 | `Y` → **104 行**（`out_date` 全空）；`N` → **80 行**（`out_date` 全非空）；`104 + 80 = 184` | `is_new` 与 `out_date IS NULL` **等价**（本样本上）；落地时两者都存，`is_current` 取 `is_new` |
| 8 | `index_member(index_code="801780.SI")`（银行 L2）是否含 `000001.SZ` | **含**（42 行）；同一只股票在其下的 L3 `801782.SI`（**六大行**，6 行：`601288/601328/601398/601658/601939/601988`）里**不含** | ⚠️ **本行原先的推论是错的，见下方更正**（附.6.2 更正条） |
| 9 | 回填落库：`scripts/ingest_sw_member_periods.py`（**Fix round 1 后默认灌全部三级 = 511 个指数**）在 `stock_bot_test` 上运行 | **as_of=2026-10-12** · `index_codes=511` · `rows_inserted=23,534` · `L1=31/7,740 行 · L2=134/8,170 行 · L3=346/7,624 行` · **failed=0** · `fully_ingested=true` | ⑫ 全量三级回填**一次成功**，无权限/限频问题 |
| 10 | 还原一致性抽验（本地 SCD2 SQL vs 上游原始过滤） | `850172.SI`（生猪养殖）在 `2019-06-03` 与 `2020-01-02` 两日：本地按 `effective_from <= T < effective_to` 还原 = **7 只**，上游按 `in_date <= T` 且（`out_date` 空或 `> T`）过滤 = **7 只**，**集合逐元素相等** | ⚠️ 这是**机制自检**（拿喂表的那批行回比），**不是独立验证**；独立证据见更正条 |
| 11 | **Fix round 1 更正条**：31 个 L1 的"上游直取" vs "其下 L3 并集"逐条对照 | L1 合计 **上游 5,211 vs 上卷 4,950**（−261，−5.0%）；**24/31 个 L1 不一致**：农林牧渔 `104 vs 80`（−23%）、钢铁 `43 vs 28`（−35%）、家用电器 `93 vs 72`、电力设备 `377 vs 352`、社会服务 `82 vs 65`、传媒 `130 vs 117`；L2 同样：种植业 `20 vs 14`、饲料 `18 vs 11`、养殖业 `22 vs 19` | **「祖级返回并集 ⇒ 可由 L3 上卷」是错的**（见附.6.3 决定 5 的更正）。本行的"上卷"列可由 `backend/scripts/reconcile_sw_member_counts.py`（本地 L3-并集 vs own-level）复算 |
| 12 | **Fix round 1 更正条**：L3 自身层级**当前成分**为 0 的节点数 | 346 个 L3 里 **87 个**在 `as_of=2026-10-12` 的当前成分为 0；抽验上游直取：`110102 粮食种植` 12 行/0 当前、`110201 海洋捕捞` 2 行/0 当前、`110301 林业Ⅲ` 7 行/0 当前、`110104 食用菌`/`110403 水产饲料`/`110404 宠物食品` **0 行** | 这 87 个节点的"0 成分"是**上游事实**（不是本地缺数据）；另有 **71 个 L3 指数上游直接返回 0 行** |
| 13 | **Fix round 1 更正条**：改灌三级后 31/31 L1 与上游逐条一致 | `local == upstream` **31/31**（合计 5,211 = 5,211）；农林牧渔 104、钢铁 43、饲料 18 全部对齐；农林牧渔 L1 成交额 由 `23,743,561,160` 元 → **`29,851,841,770` 元**（原口径低报 **25.7%**） | 读面改为"**节点自身层级优先**"后 C1 消除。**证据可复现（Ruling 51/52）**：入库脚本 `backend/scripts/reconcile_sw_member_counts.py` 逐 L1 比对本地 own-level 当前成分 vs 上游 `index_member`，任一不一致即非零退出（需真实 `TUSHARE_TOKEN`） |

### 附.6.3 由此得出的实现决定（写进代码注释与报告）

> ⚠️ **Fix round 1 · Ruling 49 的就地更正**：下表第 1 行与第 5 行**已被实测推翻**，
> 原文保留在"更正"栏里以便追溯；生效口径以**更正后**的文字为准。

| # | 决定 | 依据 |
|---|---|---|
| 1 | ~~**历史可回填 → spec §S1.4 的验收判据原样成立，不收窄**~~ → **更正**：「**历史可回填**」**成立**（附.6.1 ⑫ = `可得`，列、覆盖、深度、`src` 枚举都经复核），故**不需要**收窄成"自 SCD2 生效日起"；但「**判据原样成立**」**不成立**——判据还要求"与当日实际分类快照一致"，而当时的读路把 L1/L2 成分当"其下 L3 的并集"，实测 24/31 个 L1 因此**偏少**。行为证据当时只有 1 个 L3 × 2 日的集合相等，且比的就是喂表的那批行（**机制自检，非独立验证**）。**有效结论**：上游历史可得（不收窄）；"判据成立"要等读路改成自身层级后由附.6.2 #13 的 31/31 一致来支撑 | 附.6.1 ⑫；附.6.2 #1 / #3 / #11 / #13 |
| 2 | SCD2 落 `sw_industry_member_periods`，**不动** `sw_industry_members` | 既有表的唯一键是 `(industry_code, stock_code)`，**结构上装不下**同一只股票多次进出同一行业（附.6.2 #1 的 `000034.SZ` 在 801010.SI 有 3 段）；且既有导入整表 `DELETE` 后重灌（`data/sw_seed.sql`），加列会被静默清空 |
| 3 | 区间语义取 **`[effective_from, effective_to)`** | 与上游 `out_date` 语义一致（样例 `000034.SZ` `20070703 ~ 20090601`，2009-06-01 当天已不属该行业） |
| 4 | **必须外露 `as_of` 上界**（即使历史可回填） | 回填覆盖"上游记录过的区间"，但**最后一次 ingest 之后**的新增/变更只有下次 ingest 才有；`effective_to IS NULL` 的语义因此是"截至最近一次**完整成功** ingest 仍为成分"。**Fix round 1 加严**：`--limit` 截断的冒烟运行与 `status='partial'` 的运行**不得**抬高该上界（原先会——这正是 C1 未被发现的根因） |
| 5 | ~~默认只灌 **L3**（346 个指数），L1/L2 由 L3 上卷~~ → **更正**：**默认灌全部三级（511 个指数）**。`index_member` 对祖级指数**并不总是**返回其下全部后代：实测 24/31 个 L1 的直取数 > 其下 L3 并集（合计 5,211 vs 4,950），因为上游存在**直接挂在 L1/L2 而未落在任何 L3 的成员**（农林牧渔有 24 只这样的股票）。读面因此改为"**节点自身层级优先**"，上卷只作兜底且**明确标注不等价**（`member_source` / `member_source_note`） | 附.6.2 #11 / #13；`sw_drilldown_service` 的 `_EXACT_MEMBERS_SQL` / `_ROLLUP_MEMBERS_SQL` |
| 6 | **下界断言进 ingest**（Fix round 1 · I5） | 每条指数要有行、单层行数相对上次完整运行跌幅 ≤20%、0 行响应占比 ≤30%；任一不过 → `status='partial'` 且**不抬高口径日**，并写进 `sw_member_ingest_runs.detail`，读面 `scd2_coverage` 原样外露 |

### 附.6.4 未验证余项（open items）

| # | 余项 | 影响 | 补验方式 |
|---|---|---|---|
| 1 | **上游"当前成分"与本地 `data/sw_seed.sql` 快照的逐只差异** | 两套当前态（`sw_industry_members` vs `sw_industry_member_periods` 的 `is_current`）可能不一致；读面已只用后者，但**没有**两表互比的对账 | 跑一次 `SELECT` 比对两表在同一 `(industry_code, symbol)` 上的集合差，把差异计数落进 `sw_member_ingest_runs.detail`（Fix round 1 已把**本地上卷 vs 自身层级**的对账做进读面 `member_source`，两表互比仍未做） |
| 2 | **"任意历史日"的抽样面** | 附.6.2 #10 只抽了 1 个 L3 × 2 个交易日，且那是**机制自检**；"全部 346 个三级行业 × 全部历史日"未逐日验证 | 扩大抽样（每层各若干指数 × 若干日）并做成**独立**对账（不拿喂表的那批行回比） |
| 3 | **`in_date` / `out_date` 是否存在重叠或空洞** | 上游若给同一 (行业, 股票) 的重叠区间，"当日成分"会有重复行 | Fix round 1 已用 `COALESCE` 表达式唯一索引挡掉**完全同键**的重复；**部分重叠**（起点不同、区间相交）仍未被拒绝，需一条区间相交断言查询 |
| 4 | **退市股与更名** | `stocks` 表只含在市股票，退市股的历史成分还原得到 `symbol` 但可能 join 不到 `stocks.id`（行情为空） | 读面已用 `LEFT JOIN` + `stock_name` 兜底；若要退市股行情需另接上游 |
| 5 | **`index_classify` 的 `src` 取值全集** | 探针现在枚举 `""` / `SW` / `SW2021` / `SW2014` 四个（`scripts/spike_sw_member_history.py`）；是否还有未来版本未验证 | 按 `index_classify` 文档枚举 `src` 全部合法值 |
| 6 | **周期性 ingest 未注册** | 本任务只交付 CLI（`scripts/ingest_sw_member_periods.py`）与读面；没有定时任务，故 `scd2_as_of` 会停在上次手跑的那天 | 接进 scheduler / RabbitMQ：新增队列须同步 `app/core/mq.py` 的 `QUEUES` + `docs/deployment` 口径表（跨面改动，另立任务） |
| 7 | **0 行响应未逐指数留痕** | `sw_member_ingest_runs.zero_row_indices` 只记**个数**（本轮 80 个），没记是哪几个指数 | 若上游某指数由"无成员"变成"有成员"，个数字段看不出来；需要时把代码列表落进 `detail` 或单开一列 |

---

## 附.7 S1.9 + S1.11 段：交易活跃度 / 杠杆资金 / 估值与风险溢价的数据源复核（2026-10-12）

> 本节是 [S1.9 情绪/交易活跃度与杠杆资金](../specs/S1-market-sentiment.md#s19-情绪交易活跃度与杠杆资金)
> 与 [S1.11 情绪/估值与风险溢价](../specs/S1-market-sentiment.md#s111-情绪估值与风险溢价)
> 的**数据源可行性交付物**。方法沿用仓库纪律「**先实机验证再写适配器**」，结论格式与
> [附.1](#附1-七项复核结论) / [附.5](#附5-段-b-可行性复核大宗商品--外汇s13--2026-10-16) 一致
> （来源 / 频率 · 历史深度 / 三态结论 / 替代方案）。**核查日期：2026-10-12**，探针脚本
> [`backend/scripts/spike_s19_s111_sources.py`](../../backend/scripts/spike_s19_s111_sources.py)
> （**已入库**，任一源为空即 `SystemExit`，不在空结果上打印 `[OK]`）。
>
> **本节回答的三个问题**：①两融可得吗（频率/深度/行数上限）②7 个指数的换手率从哪来、
> 口径是自由流通还是总股本 ③10Y 国债收益率走哪条接口。
> **未通过验证前不写适配器、不落库、不往 UI 填数字。**

### 附.7.1 结论总览

| 数据源（行标签） | 来源 | 频率 / 历史深度 | 三态结论 | 替代方案 |
|---|---|---|---|---|
| ⑭ **融资融券（市场汇总）** | **TuShare `margin`（已接入）**——[TuShare 两融汇总文档](https://tushare.pro/document/2?doc_id=58)。**实机记录**：附.7.2 #1–#5 | **日频**，一行 = 一个交易日 × 一个交易所（实测 `exchange_id` 分布 = `SSE 1557 / SZSE 1557 / BSE 886`）。历史深度：单次调用**4000 行上限**，实测 `20000101→20261016` 只回 4000 行（`20200508 → 20261009`）→ **更早的年份被静默截断，必须按日期分片**；分片后至少可回溯到 2014-01（实测 `2014-01-01…01-10` 有 14 行）。金额单位实测为**元**（`20261009` SSE `rzye = 1.291437e12`），`rqyl` 单位**股** | `可得` | 不适用（本项可得）。**但必须分片**：不分片会把"≥3 年序列"做成"只有最近 8 年"且头部静默缺失 |
| ⑮ **融资融券（个股明细）** | **TuShare `margin_detail`**——同族接口。**实机记录**：附.7.2 #3 / #4 | **日频**；实测 `20261009` 单日 **4,455 行**（沪深北全市场）。**单次上限 6000 行**：区间调用实测两天窗口就撞满 6000 行（`20261008 → 20261009`）→ 只能**按日**拉 | `可得`（**本任务不用**） | §S1.9 的验收判据只要求**市场级**杠杆水平，故落库走 ⑭ 的三所求和；个股明细留作后续面（与 S1.7 资金流四件套的个股口径在同族） |
| ⑯ **指数换手率（5 个指数直取）** | **TuShare `index_dailybasic`（已接入）**——[TuShare 指数日线指标文档](https://tushare.pro/document/2?doc_id=128)。**实机记录**：附.7.2 #6–#9 | **日频**。**两个口径并存**：`turnover_rate` = 成交量 / **总股本**，`turnover_rate_f` = 成交量 / **自由流通股本**（`free_share`）——两者实测**同日不同值**（`000001.SH@20261012`：`0.9407` vs `2.7148`）。历史深度：单次上限实测 **3000 行**（`000300.SH` 无窗口 = 3000 行，最早 `20140609`）→ 长历史须分片；分片后 2014 至今可得 | `受限` | **7 个里只有 5 个直取**（见 ⑰）。**口径选定**：落库一律取 `turnover_rate_f`（**自由流通股本**，spec §S1.9 要求"流通股本口径"）；总股本口径只在 `extra.turnover_rate_total` 留痕。`index_dailybasic` 全接口实测只有 **15 个指数代码**（`trade_date=20261009` 快照） |
| ⑰ **指数换手率（北证50 / 科创综指，自建）** | **`index_weight` + `daily_basic` 自建（已接入）**——两个指数在 `index_dailybasic` 里**返回 0 行且不报错**（实测 `899050.BJ`、`000680.SH` 各窗口均 0 行）。**实机记录**：附.7.2 #8–#12 | **日频**（由当日成分行情加权得出）。**成分权重是月度快照**：`index_weight` 实测 `899050.BJ` 自 **20221130** 起 47 期、`000680.SH` 自 **20250930** 起 13 期（**不传窗口只回最新一期**）。成分行情 `daily_basic` 单日实测 5,559 行（含北交所 **349** 行，`turnover_rate_f` 全列非空）| `需自建` | 自建口径 = 成分股 `daily_basic.turnover_rate_f` 按 `index_weight.weight` 的**加权平均**（权重缺失按实际覆盖权重归一）。**属 C 类归属**（`attribution=current_index_weight_snapshot` 字段级标注，ADR 0014 第②③条）。⚠️ 科创综指受上游权重快照限制**只有 ~1 年**，北证50 ~3.9 年 → 见附.7.4 |
| ⑱ **10Y 国债收益率** | ①**中债官方（主源，已接入）**：中国债券信息网 `yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery`（`qxId=ycqx`），经 AKShare `bond_china_yield` 逐年翻窗取 `曲线名称='中债国债收益率曲线'` 的 `10年` 列；②东财镜像（备源，实测同值）：`datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD`，经 AKShare `bond_zh_us_rate` 的 `中国国债收益率10年` 列。**实机记录**：附.7.2 #13–#18 | **日频**。①**窗口必须 < 1 年**（> 1 年**静默返回 0 行**，不报错：实测 `20180101→20261016` / `20100101→…` / `20050101→…` 全 0 行；`20250101→20251231` 回 248 个交易日）；②一次调用可取 **2002-01-04 → 2026-10-12 全历史**（`10年` 列非空 6,186 行）。**两源同日逐值相同**（实测 5 个交易日差值全为 `0.0000`） | ①`可得`；②`可得`（备源） | 不适用（两源都可得）。⚠️ **TuShare `yc_cb` 在本 token 上权限不足**（逐字：`抱歉，您没有接口(yc_cb)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。`），故 **S4.8 若将来走 `yc_cb` 需先解决积分权限**；本任务按 spec §S1.11 的"若 S4.8 未完成则先用国债数据源直取"走中债直取 |
| ⑲ **落库形态（4 段共用，前置决策）** | **既有基建**：`industry_metrics`（`industry_key='market'`，S1.2 已加 `published_at` / `caliber_version`）+ 既有读面 `GET /api/v1/metrics/{latest,series}` | — | `可得`（复用既有） | **不新建表**：13 个指标（11 + 2）落进同一张 `industry_metrics`，`industry_key='market'`（Ruling 16 泛化出的通用 scope 键）；ERP 的 PE 底座另落 `pe_csi300` 一行。**没有第二张指标表、没有第二套读路** |

> **三态取值说明**：⑭ 判 `可得`（实机取到真实三所数据 + 已知行数上限与分片策略）；
> ⑯ 判 `受限`（7 个指数里 2 个直取不到 —— **受限的是覆盖度，不是可得性**，故给自建替代方案而非 `blocked`）；
> ⑰ 判 `需自建`；⑱ 的主源判 `可得`（中债官方实机取到 3 年逐日数据）而 TuShare `yc_cb` 判 `blocked-on-verification`。
> **未验证项见 [附.7.4](#附74-未验证余项open-items)**，本节不填任何估计值。

### 附.7.2 实机调用记录（本节专属，2026-10-12）

> 与附.2 / 附.5.2 / 附.6.2 同规矩：记录**真实发出的调用与原始返回要点**。探针 =
> `backend/scripts/spike_s19_s111_sources.py`（真实 `TUSHARE_TOKEN` + akshare 1.18.94；
> 不落库、不打印 token）。**注**：#6–#12 的"候选代码枚举 / `index_weight` 窗口语义"两条
> 由同一次核查的命令行对照输出补记（探针脚本本身覆盖同一批调用）。

| # | 实测对象 | 返回要点 | 结论 |
|---|---|---|---|
| 1 | `margin(trade_date=20261009)` | **3 行** · 列 = `trade_date, exchange_id, rzye, rzmre, rzche, rqye, rqmcl, rzrqye, rqyl` · SSE `rzye=1.291437e12` / SZSE `1.230448e12` / BSE `8.169949e09`（**元**） | ⑭ 市场汇总可得，一行一所 |
| 2 | `margin(start_date=20000101, end_date=20261016)` | **4000 行**（**命中单次上限**）· `min=20200508 / max=20261009` · `exchange_id` 分布 `SSE 1557 / SZSE 1557 / BSE 886` | ⑭ **必须分片**，否则头部静默缺失 |
| 3 | `margin_detail(trade_date=20261009)` | **4,455 行** · 列含 `rzye, rqye, rzmre, rqyl, rzche, rqchl, rqmcl, rzrqye` · 样例 `000001.SZ rzye=4.509201e09` | ⑮ 个股明细可得 |
| 4 | `margin_detail(start_date=20260101, end_date=20261016)` | **6000 行**（命中上限）· `min=20261008 / max=20261009` → 两天窗口即撞满 | ⑮ 只能按日拉 |
| 5 | `margin(start_date=20140101, end_date=20140110)` | **14 行** → 分片后 ≥2014-01 有数据（**0 行则须收窄深度结论**；本探针把这条写成断言） | ⑭ 深度 ≥ 3 年成立 |
| 6 | `index_dailybasic(ts_code=<7 个>, 20260101–20261016)` | **5/7 命中**：`000001.SH 184 行` / `399001.SZ 184` / `399006.SZ 184` / `000016.SH 184` / `000300.SH 184`（`20260105~20261012`）；**`899050.BJ` 0 行、`000680.SH` 0 行**（不报错） | ⑯ 覆盖 **5/7**；⑰ 缺口两个 |
| 7 | 同上返回列（逐字） | `ts_code, trade_date, total_mv, float_mv, total_share, float_share, free_share, turnover_rate, turnover_rate_f, pe, pe_ttm, pb`；两列换手率**均 184/184 非空**。样例 `000001.SH@20261012`：`turnover_rate=0.9407`、`turnover_rate_f=2.7148` | **口径两列真实可分**（自由流通 ≠ 总股本） |
| 8 | `index_dailybasic(ts_code="000300.SH", 20050101→)` 与无窗口调用 | 两者都 **3000 行**（**单次上限**）· `min=20140609 / max=20261012` | ⑯ 长历史须分片 |
| 9 | `index_dailybasic(trade_date="20261009")` | **15 行**（全接口只有 15 个指数代码：`000001.SH, 000005.SH, 000006.SH, 000016.SH, 000300.SH, 000688.SH, 000852.SH, 000905.SH, 399001.SZ, 399005.SZ, 399006.SZ, 399016.SZ, 399300.SZ, 399673.SZ, 399905.SZ`） | ⑯ 7 个目标命中 5（另一旁证） |
| 10 | 候选代码枚举 `index_dailybasic`：`899050.BJ / 899050.CSI / 899050.SH / 000680.SH / 000680.CSI / 950090.CSI / 000698.SH / 000688.SH` | 只有 `000688.SH`（科创50）与 `399006.SZ` 回 184 行，其余**全 0 行** | ⑰ **换代码也不行**：上游就是没有这两个指数 |
| 11 | `index_weight(index_code="899050.BJ", trade_date=20260930)` / 不带窗口 | 前者 **50 行**（50 只成分）；后者 **2350 行 = 47 期**（`20221130`→`20260930`，**月度快照**） | ⑰ 北证50 自建可行、权重可回溯 3.9 年 |
| 12 | `index_weight(index_code="000680.SH", ...)` / `daily_basic(trade_date=20261009)` | 科创综指 **579 行/期**、全窗口 **7000 行 = 13 期**（`20250930`→`20260930`，**最早 2025-09-30**）；`daily_basic` 单日 **5559 行，其中 `.BJ` 349 行** | ⑰ 科创综指权重**仅 ~1 年**；`.BJ` 覆盖 ⇒ 自建北证50可行 |
| 13 | `yc_cb(curve_type=0/1/2, curve_term=10)`（**三态中的 blocked 一手证据**） | 上层：`RuntimeError: TuShare API 'yc_cb' permission denied.`；**底层逐字**：`Exception: 抱歉，您没有接口(yc_cb)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。` | ⑱ **TuShare 路线 `blocked-on-verification`（权限，非接口不存在、非网络不可达）** |
| 14 | `bond_china_yield(2024/2025/2026 各一年)` | 三年分别 **753 / 744 / 570 行**（国债曲线行 251 / 248 / 190）· 列 = `曲线名称, 日期, 3月, 6月, 1年, 3年, 5年, 7年, 10年, 30年` · 合计 **689 行** `2024-01-02→2026-10-12` | ⑱ ①主源可得（逐年翻窗） |
| 15 | `bond_china_yield` 超 1 年窗口（`20180101–20261016` / `20100101–…` / `20050101–…`） | **三次全 0 行、不报错** | ⑱ ①**窗口 < 1 年是硬约束**（静默坑，与 `disclosure_date` 的区间坑同族） |
| 16 | `bond_zh_us_rate(start_date="20000101")` | **7101 行** · 列含 `中国国债收益率2年/5年/10年/30年` 与美债同族 · `中国国债收益率10年` **非空 6186 行** `2002-01-04→2026-10-12` | ⑱ ②备源可得（一次全历史） |
| 17 | 两源同日对拍（官方中债 vs 东财镜像） | `2026-09-30 1.6822/1.6822`、`10-08 1.6899/1.6899`、`10-09 1.6864/1.6864`、`10-10 1.6849/1.6849`、`10-12 1.6722/1.6722` · **差全为 0.0000** | ⑱ 两源**逐值一致** → 备源可用于交叉验证与断点补数 |
| 18 | 中债官方口径取值（用于落库） | `曲线名称='中债国债收益率曲线'` 的 `10年` 列；样例行 `2026-10-12 = 1.6722` | ⑱ 落库字段唯一化（不取 `3月/1年` 等其它期限） |

### 附.7.3 由此得出的实现决定（写进代码注释与报告）

| # | 决定 | 依据 |
|---|---|---|
| 1 | **不新建表**：13 个指标（S1.9 的 11 + S1.11 的 2）落 `industry_metrics(industry_key='market')`，另加 ERP 的 PE 底座 `pe_csi300` 一行 | 附.7.1 ⑲；spec §S1.2 与 ADR 0013 |
| 2 | **换手率一律自由流通口径**：直取取 `turnover_rate_f`，自建取成分股 `turnover_rate_f` 的权重加权平均；总股本口径只进 `extra.turnover_rate_total` | 附.7.2 #7；spec §S1.9「换手率需流通股本口径，须在 registry 写明是自由流通还是总股本」 |
| 3 | **两融 = 三所求和**（市场级），逐所拆分进 `extra.by_exchange_yuan`；`margin` 按 ≤2 年分片 | 附.7.2 #1/#2；spec §S1.9 要的是"杠杆水平"（市场级） |
| 4 | **10Y 国债走中债官方**（逐年翻窗），东财镜像作备源；**不用** TuShare `yc_cb` | 附.7.2 #13–#18；spec §S1.11「若 S4.8 未完成则先用国债数据源直取」 |
| 5 | **ERP 的 PE 口径 = 整体法（指数总市值 / 指数口径净利润）**，指数 = **沪深300（000300.SH）**，取 `index_dailybasic.pe` | spec §S1.11「需明示用哪个 PE 口径（整体法/中位数）与哪个指数」；实测 `index_dailybasic` **没有全A（中证全指 000985）**，故取沪深300 |
| 6 | **PE 底座单独落 `pe_csi300`**（而不是只落 ERP） | 只落 ERP 的话 `1/PE` 这一半无法从库里独立复原；spec §S1.11 的"PE 口径与指数在 UI 可查"要有可查对象 |
| 7 | **`published_at` 按产地分三档**：行情类/ERP = 当日 15:00、两融 = 18:00、国债 = 19:00（Asia/Shanghai） | ADR 0011；两融在收盘后清算发布、中债/东财曲线在银行间收市（16:30）后发布 —— 统一写 15:00 会给回测一个"当时还拿不到"的时点 |
| 8 | **自建指数披露 C 类归属**：`extra.attribution='current_index_weight_snapshot'` + `weight_source` + `weight_period` | ADR 0014 第②③条：禁止用当前快照回算"成分本身"，允许用行情事实按成分聚合、且必须字段级标注 |
| 9 | **`upsert` 必须分批**（每批 500 行） | asyncpg 拒绝 > 32767 个参数的语句：5.2 年 × 14 指标 ≈ 11,600 行 = 13.9 万参数，实测 `InterfaceError: the number of query arguments cannot exceed 32767`（附.7.4 #4） |
| 10 | **自建换手率的成分行情不能读本地 `daily_basic_indicators`** | 实测该表 `turnover_rate_f` **整列为 NULL**（5559/5559 行为空，只有总股本口径有值）→ 必须按交易日直取上游 `daily_basic`（一次调用覆盖全市场，含北交所） |
| 11 | **自建换手率加双重响亮失败守卫**：①交易日取数 0 行 → `RuntimeError`；②按**当期成分**算的匹配率中位数 < 0.95 → `RuntimeError` | 长回补（800+ 天）实测会因上游偶发 0 行而把整段静默写成 `source_empty`（"采集失败"伪装成"缺数"）；分母用全历史并集会误判（北证50 110 只并集 vs 每期 50 只 → 中位数 0.936） |

### 附.7.4 未验证余项（open items）

| # | 余项 | 影响 | 补验方式 |
|---|---|---|---|
| 1 | **科创综指（000680.SH）的换手率只有 ~1 年**（实测落库 **244 个交易日**：`2025-09-30 → 2026-10-09`，其余 585 个交易日缺 `index_weight` 快照） | spec §S1.9 判据是"7 个指数换手率有 ≥3 年序列"；该指数受上游 `index_weight` 快照起点（实测 2025-09-30）限制，**头部缺口是上游事实，不是本地漏采** | 若上游后续补齐更早快照则重跑回补即可。**Ruling 59.3a 已裁定：保留科创综指、不替换成科创50**（spec 点名的是该指数，换指数 = 交付另一个指标）；并已实机判定限制来自**源侧权重快照**而非指数成立日（见附.7.5 ②） |
| 6 | **自建换手率的两处已修坑（修复记录，非余项）** | ①`_self_built_rows` 原先直接从本地 `daily_basic_indicators` 取 `turnover_rate_f`，**该列整列为 NULL**（实测 5559/5559 行为空）→ 改按交易日直取上游；②同一处**未规范化键空间**，`920001.BJ` 与带空白/小写的 ts_code 会假不匹配 → 整列落 `partial_member_coverage`/`source_empty`（实测北证50 曾出现"596 天有值 / 233 天空"的抖动） | 两坑均已修：上游直取 + `.strip().upper()` 双向规范化；并加**双重响亮失败守卫** —— ①交易日取数 0 行即 `RuntimeError`（不静默记成缺数）②按**当期成分**算的匹配率中位数 < 0.95 即 `RuntimeError`（防"取到行但一只都没匹配上"）。⚠️ 分母必须用**当期**成分（北证50 全历史并集 110 只 vs 每期 50 只：按并集算中位数 0.936，会被误判） | 复跑 `scripts/backfill_market_activity.py` 即复现守卫；修复后北证50 **829/829 天全覆盖**（最小期成分匹配率 1.000） |
| 7 | **`margin` 按 2 年分片是否够（更深历史未验）** | 附.7.1 ⑭ 的深度结论只到 2014-01（探针断言），2014 以前未逐段确认 | 用 `start_date=20050101&end_date=20131231` 单独分片复跑，行数 > 0 再把深度结论前推 |
| 8 | **`daily_basic` 偶发 0 行（上游限频/超时）** | 实测 `daily_basic(20250602)` / `(20260102)` 单次调用会返回 0 行（真实交易日），是本轮"自建换手率长回补抖动"的直接成因 | 已加守卫：0 行即 `RuntimeError`（**不静默落 NULL**），且自建有增量缓存，重跑只补缺口。回补运维按"失败即重跑"处理；若频繁出现，需在 `TuShareClient` 上加按接口的退避 |
| 2 | **自建换手率的官方口径对标** | 本任务自建口径 = 成分 `turnover_rate_f` 的**权重加权平均**；中证指数公司官网对"指数换手率"的官方定义（若为 SUM 口径）可能给出不同数值 | 取中证指数官网任一指数页的"换手率"字段与本地同日值对拍；不一致则记录差值并登记为口径差异（**不得**直接改数） |
| 3 | **`margin` 的历史深度上限** | 实测分片后 ≥2014-01 有数；更早（2005–2013）是否可得未逐段确认 | 用 `start_date=20050101&end_date=20131231` 单独分片复跑，确认行数 > 0 后再把深度结论前推 |
| 4 | **一次 upsert 的行数上限** | 已按 asyncpg 32767 参数上限分批（500 行/批）；若未来指标数或年份跨度大幅增加，批大小需重算 | 已有实测证据（附.7.3 #9）；批大小是常量 `UPSERT_BATCH_ROWS`，改动时按"行数 × 12 < 32767"重算 |
| 5 | **ERP 的 PE 是否与中证指数官网公布值一致** | `index_dailybasic.pe` 的口径是"整体法"由上游声明；本任务**未**与中证指数官网的沪深300 市盈率逐日对拍 | 取中证指数官网（或交易所月报）的沪深300 市盈率与本地同日 `pe_csi300` 对拍；若系统性偏移则登记口径差并考虑改用官网口径序列 |

### 附.7.5 Ruling 59 fix round 1 的复核实测（2026-10-13）

本节的每条结论都是本轮**重新实机复测**得到的（不是转抄），命令见各条。

**① 「自建换手率成分匹配率中位数 = 0.000」不是键空间问题 —— 键空间实测 100% 命中**

```
$ .venv/bin/python <键空间对拍>      # index_weight.con_code ∩ daily_basic.ts_code
daily_basic(20261009) rows=5559  cols=['ts_code','trade_date','turnover_rate','turnover_rate_f',...]
index_weight(899050.BJ)  rows=2350 periods=47 20221130..20260930
  latest 20260930: n=50   sample=['920045.BJ','920438.BJ',...]  ∩ daily_basic = 50/50
index_weight(000680.SH)  rows=7000 periods=13 20250930..20260930
  latest 20260930: n=579  sample=['688256.SH','688041.SH',...]  ∩ daily_basic = 579/579
```

两侧都带交易所后缀、大小写一致，**没有** `000680.SH` vs `680.SH` 之类的命名空间差异，
也没有代码迁移残留。中位数之所以是 `0.000`，是守卫自身的**空集默认值**造成的：

- `_self_built_rows` 只在**增量缓存未命中**的日子上评估匹配率；科创综指落库的 244 天
  （`2025-09-30 → 2026-10-09`，**恰好等于 `daily_quotes` 在该快照区间内的交易日数**）
  全部命中缓存，剩下 585 天**全部在快照期之前**（`_eligible` 为空 → `continue`）；
- 于是 `_cov` 是**空列表**，`_median_cov = _cov[len(_cov)//2] if _cov else 0.0` 取默认 `0.0`
  → 抛"键空间/代码迁移"的错。**报错文案与真实原因相反**，且它会把整条回补（含北证50）
  一起中止 —— 这就是"回补崩溃、指标无法刷新"的机制。

实测复现（同一个函数、同一个指数，只换窗口）：

```
$ .venv/bin/python <真实 _self_built_rows 复现>
=== 000680.SH weight periods 2025-09-30..2026-09-30 (n=13)
    slice 2023-05-09..2023-05-12: any eligible period? False
    RuntimeError -> 自建换手率的成分匹配率中位数=0.000 < 0.95 ...
    slice 2026-09-28..2026-10-09: any eligible period? True
    OK -> {'2026-09-28': 4.1108, '2026-09-29': 3.2371, '2026-09-30': 3.5204,
           '2026-10-08': 4.2647, '2026-10-09': 4.7489}
```

**修法（不削弱门槛）**：先按快照期分诊再取数 —— 快照期之前的交易日直接落
`no_weight_snapshot`（**不调用上游**），只在**有快照期的可评估日**上算匹配率中位数；
`_cov` 为空且确有可评估日时才另报"快照行存在但成分列为空"的结构异常。
`MIN_MEMBER_COVERAGE = 0.95` **一个字没改**，键空间真对不上时照样抛错（有单测锁定）。

**② 科创综指 244 天的限制来自哪一侧：源侧权重快照，不是指数成立日**

```
$ TuShare index_basic(market=SSE)
  ts_code    name    market  publisher                  category  base_date  base_point  list_date
  000680.SH  科创综指  SSE     中证指数有限公司、上海证券交易所  综合指数    20191231      1000.0  20250120
  000688.SH  科创50   SSE     中证指数有限公司、上海证券交易所  规模指数    20191231      1000.0  20200723
$ TuShare index_daily(000680.SH, 19900101→20261009)  rows=1639  min=20191231 max=20261009
$ TuShare index_weight(000680.SH)                    rows=7000  periods=13  20250930..20260930
```

- 指数**本身**的日线序列从**基日 2019-12-31** 就有（1639 行），比权重快照早 **5.75 年**；
- `index_weight` 的科创综指快照**最早只到 2025-09-30**（13 期，一个月一期）。

**结论**：`idx_turnover_ratio_star` 的 244 天来自**上游权重快照起点**（源侧限制，
**可由上游补齐**），**不是**该指数的成立日 —— 故「≥3 年」对该指数**当前不可满足但不是
结构性不可能**；库里 585 个缺口日逐行标 `missing_reason='no_weight_snapshot'`。
（对照：北证50 `index_weight` 起点 20221130 ≈ 其 `list_date` 20221121 + 1 个月，
即"快照随指数成立而开始"；科创综指的快照起点晚于其发布日 2025-01-20 约 8 个月。）

**③ ERP 的落库天改锚：中债曲线 2021–2023 实测可得，瓶颈从来不是收益率侧**

```
$ AKShare bond_china_yield 逐年翻窗（窗口 < 1 年）
2020: rows=747 国债行=249  min=2020-01-02 max=2020-12-31
2021: rows=750 国债行=250  min=2021-01-04 max=2021-12-31
2022: rows=750 国债行=250  min=2022-01-04 max=2022-12-31
2023: rows=750 国债行=250  min=2023-01-03 max=2023-12-31
2024: rows=753 国债行=251  min=2024-01-02 max=2024-12-31
```

原实现把 `erp_10y` 的落库天绑在 `daily_quotes` 交易日上（2023-05-09 起 **829 天**），
而 ERP = `1/PE − 10Y` **不需要 `daily_quotes`**：PE 底座（`index_dailybasic.pe`，
沪深300 整体法）实测 **1259 天（2021-07-27 起）**，中债 10Y 侧 2021 起实测可得。
**改锚 = 取两者可得性交集**，窗口 `2021-07-27 → 2026-10-09` = **1259 天 ≈ 5.2 年**
（≥ spec §S1.11 的「≥5 年」），口径版本随之 bump 到 `s1.9s1.11-v2`。

**④ 顺带修掉的两个真实缺陷（本轮新增）**

- `fetch_treasury_10y` 按年翻窗（`{year}0101→{year}1231`）时会**返回窗口外的日子**；
  v1 只按交易日查表所以没暴露，v2 把它的结果当作"可得日集合"用就会把区间外的一年
  灌进落库集合（实测让一个 1 天窗口落出 391 行）。已裁剪到 `[day_lo, day_hi]`。
- **重跑会把自建换手率的好值覆盖成 NULL**：缓存命中的日子不重算上游 →
  `compute_day_metrics` 只能给 `source_empty`(NULL) → v1 照写就抹掉了已落库的值
  （这是 §附.7.4 #6 所说"有值/空值抖动"的真正机制）。v2 改为**缓存命中的 (key, day)
  不重写**，且缓存**口径感知**（只认当前 `caliber_version` 的行）。

---

## 附.8 S1.10 段：波动率与衍生品（期权隐含波动率 / 股指期货升贴水）的数据源复核（2026-10-13）

> 本节是 [S1.10 情绪/波动率与衍生品](../specs/S1-market-sentiment.md#s110-情绪波动率与衍生品)
> 的**数据源可行性交付物**。方法与结论格式沿用 [附.1](#附1-七项复核结论) / 附.5 / 附.6 / 附.7
> （来源 / 频率 · 历史深度 / 三态结论 / 替代方案）。**核查日期：2026-10-13**，探针脚本
> [`backend/scripts/spike_s110_volatility_futures_sources.py`](../../backend/scripts/spike_s110_volatility_futures_sources.py)
> （**已入库**，任一源为空 / 一手关键假设不成立即 `SystemExit`，**不在空结果上打印 `[OK]`**）。
>
> **本节回答的四个问题**：①5 条 QVIX 中国波指可得吗（频率/深度/行数上限/字段/新鲜度）
> ②CFFEX IF/IC/IH/IM 走哪些接口、单次行数上限与分片要求、主力连续怎么解析且**是否无前视**
> ③现货指数腿走哪个源 ④**升贴水的符号约定**（基差 = 期货 − 现货 还是 现货 − 期货）。
> **未通过验证前不写适配器、不落库、不往 UI 填数字。**

### 附.8.1 结论总览

| 数据源（行标签） | 来源 | 频率 / 历史深度 | 三态结论 | 替代方案 |
|---|---|---|---|---|
| ⑳ **5 条 QVIX 中国波指**（50ETF / 300ETF / 500ETF / 创业板ETF / 科创50ETF） | **AKShare 内置函数（已依赖 akshare 1.18.94）**：`index_option_50etf_qvix` / `index_option_300etf_qvix` / `index_option_500etf_qvix` / `index_option_cyb_qvix` / `index_option_kcb_qvix`。底层 = **第三方站点 optbbs.com 的单个静态 CSV** `http://1.optbbs.com/d/csv/d/k.csv`（实测 `__get_optbbs_daily` 直读该 URL，gbk 编码，87 列多品种拼接，AKShare 按列偏移切分）。**实机记录**：附.8.2 #1–#8 | **日频**（最大相邻日间隔 = 11 个自然日 = 长假；实测相邻间隔 >5 天仅 11–33 处，与 A 股假期数一致）。**深度逐品种不同，但 5 条全部 ≥3 年**：50ETF **2823 非空日 2015-02-09→2026-09-30（11.64 年）**、300ETF 1635 日 2019-12-23→（6.77 年）、500ETF 971 日 2022-09-19→（4.03 年）、创业板ETF 971 日 2022-09-19→（4.03 年）、科创50ETF 801 日 2023-06-05→（3.32 年）。**行数上限 = 文件本身**：原始 CSV 行数 2823 == AKShare 返回行数 2823（**无库内截断**），且上游**没有分页 API**（早期数据不可翻页补取）。列 = `date, open, high, low, close`（**落库取 `close`**）。⚠️ **新鲜度：CSV `Last-Modified` = `Wed, 30 Sep 2026 07:13:54 GMT`，末行 = 2026-09-30** —— 比期货腿（2026-10-12）**晚 3 个交易日** | `可得`（5/5，且 spec 的「≥3 个品种」被超额满足） | 不适用（本项可得）。⚠️ **两条必须写清的限制**：①**是第三方编制、不是交易所官方口径** —— 官方的上证50ETF波动率指数（iVIX）已于 **2018-02-22 起由中证指数公司暂停发布**（[金融界报道](https://m.jrj.com.cn/madapter/finance/2018/02/22154024136042.shtml)），故 registry 的 `source_tier` 记 `highfreq`（第三方参考）**而不是 `official`**；②**有 1–3 个交易日的发布滞后**（实测 2026-10-08/09/12 尚无值）→ 这些日子落 `NULL + missing_reason='source_not_updated'`，**不是**落 0、也不是落"最近值" |
| ㉑ **股指期货主力连续日线**（IF/IC/IH/IM） | **TuShare `fut_daily`（需新增客户端方法）**——[TuShare 期货日线文档](https://tushare.pro/document/2?doc_id=138)。参数为**连续合约代码** `IF.CFX` / `IC.CFX` / `IH.CFX` / `IM.CFX`。**实机记录**：附.8.2 #9–#14 | **日频**，列 = `ts_code, trade_date, pre_close, pre_settle, open, high, low, close, settle, change1, change2, vol, amount, oi, oi_chg`。**单次 2000 行上限（实测复现）**：不带窗口 / 带 `start_date=20000101` / 甚至 `start_date=20180101` **都只回 2000 行、最早都停在 20180711** ⇒ **起点写早没用，分片是唯一出路**。按 **≤3 年**分片（每片 ≈730 行）后合计：IF **4002 日 2010-04-16→2026-10-12（16.40 年）**、IC 2791 日 2015-04-16→（11.44 年）、IH 2791 日 2015-04-16→（11.44 年）、IM **1021 日 2022-07-22→（4.18 年）**。`close`/`settle`/`oi` 实测**全列非空、无 0 值、无重复日期** | `可得` | 不适用（本项可得）。**但必须分片**：不分片会把「≥3 年序列」做成「只有最近 8 年」且头部静默缺失（与附.7 ⑭ `margin` 同族） |
| ㉒ **主力连续是怎么解析的**（而**不是**静态"当前主力"） | **TuShare `fut_mapping`（需新增客户端方法）**——返回 `ts_code, trade_date, mapping_ts_code`，**一行 = 一个交易日**。**实机记录**：附.8.2 #15–#16 | **日频、逐日映射**（不带窗口返回全部历史且**未撞 2000 行上限**）：IF.CFX 4003 行 2010-04-16→2026-10-13 映射到 **183 个真实合约**、IC 2792 行/**126 个**、IH 2792 行/**124 个**、IM 1022 行/**35 个**。**关键对拍（附.8.2 #17）**：把窗口内出现过的**每一个真实合约**整段取回，与 `.CFX` 连续序列**逐日**比对 `close` —— 4 个品种各 **484 个交易日 / 23–24 个合约，不一致 0 日** | `可得` | 不适用（本项可得）。**结论**：`<品种>.CFX` 是**逐日**主力连续且**未复权**（= 当日映射合约的原始收盘价）⇒ ①无前视偏差（不是用"今天的主力"拼历史）②与现货相减有经济含义（复权序列相减得到的基差无意义） |
| ㉓ **现货指数腿**（IF→沪深300 `000300.SH` / IC→中证500 `000905.SH` / IH→上证50 `000016.SH` / IM→中证1000 `000852.SH`） | **TuShare `index_daily`（已接入）**。**实机记录**：附.8.2 #18–#19 | **日频**：`000300.SH` 6006 行 2002-01-04→2026-10-12、`000905.SH` 5286 行 2004-12-31→、`000016.SH` 5529 行 2003-12-31→、`000852.SH` 5286 行 2004-12-31→。**实测单次最多 6006 行、未撞 2000 行上限**（仍按 ≤5 年分片留余量）。列 = `ts_code, trade_date, close, open, high, low, pre_close, change, pct_chg, vol, amount` | `可得` | **本地 `index_dailies` 不能统一走**：实测覆盖 `000300.SH` / `000905.SH` / `000016.SH`（2021-09-22 起各 1221 行）但**没有 `000852.SH`（中证1000）** ⇒ 4 条腿统一走 `index_daily`（一个产地贯穿 4 个品种） |
| ㉔ **升贴水符号约定（前置决策，必须写死）** | — （不是数据源，是口径裁定） | — | **`基差 = 期货价 − 现货指数价`**；**正 = 升水（期货高于现货）、负 = 贴水（期货低于现货）**。spec §S1.10 的判据是「升贴水**负值**时 UI 明示"贴水"语义」——只有本约定能让"负值 = 贴水"成立；`现货 − 期货` 会让贴水显示为**正数**，正好是要防的误读。**实测手算核对（附.8.2 #20）**：IF2612 `4233.60 − 4287.98 = −54.38`（贴水）· IC2612 `6986.00 − 7093.02 = −107.02` · IH2612 `2779.00 − 2819.41 = −40.41` · IM2612 `6921.00 − 7021.47 = −100.47`，四者同为负 ⇒ 四品种当日均为**贴水** | `可得`（约定已定死） | 该约定同时写进 `industry_registry.py` 的 `MetricDef.description` 与 `docs/design/metric-dictionary.md` —— **前端不需要猜、也不允许自己翻转符号** |
| ㉕ **落库形态（前置决策）** | **既有基建**：`industry_metrics`（`industry_key='market'`，S1.2 已加 `published_at` / `caliber_version`）+ 既有读面 `GET /api/v1/metrics/{latest,series}` | — | `可得`（复用既有） | **不新建表、不新建读路**：11 个指标（5 QVIX + 4 基差 + 2 贴水率）落进同一张 `industry_metrics`、同一个 `market` scope、同一个读面 |

> **三态取值说明**：⑳ 判 `可得`（5/5 品种实机取到、全部 ≥3 年，且已知"上限 = 文件本身 + 3 个交易日发布滞后"两个陷阱）；
> ㉑ 判 `可得`（分片后 IF 16.4 年 / IM 4.18 年，全部 ≥3 年）；㉒ 判 `可得` 且**逐日对拍 0 不一致**；
> ㉓ 判 `可得`；㉔ 是口径裁定而非源；㉕ 判 `可得`（复用既有基建）。
> **本段没有 `blocked-on-verification` 的源**；未验证项见 [附.8.4](#附84-未验证余项open-items)。
> 本节**不填任何估计值**：缺失一律 `NULL + missing_reason`（见附.8.3 #6）。

### 附.8.2 实机调用记录（本节专属，2026-10-13）

> 与附.2 / 附.5.2 / 附.6.2 / 附.7.2 同规矩：记录**真实发出的调用与原始返回要点**。
> 探针 = `backend/scripts/spike_s110_volatility_futures_sources.py`（真实 `TUSHARE_TOKEN` +
> akshare 1.18.94；不落库、不打印 token）。原样输出：`[OK]` 行即探针实测行。

| # | 实测对象 | 返回要点 | 结论 |
|---|---|---|---|
| 1 | `index_option_50etf_qvix()` | rows=**2823** 非空 close=**2823** `20150209..20260930`（11.64 年）cols=`[date, open, high, low, close]`；首个非空日 **== 期权上市日 20150209**；最大日间隔 11 天 / >5 天间隔 33 处；末日 close=13.84 | ⑳ 50ETF QVIX 可得、日频、11.6 年 |
| 2 | `index_option_300etf_qvix()` | rows=2823 非空 close=**1635** `20191223..20260930`（6.77 年）；首个非空日 **== 期权上市日 20191223**；末日 close=15.96 | ⑳ 300ETF 可得、6.8 年 |
| 3 | `index_option_500etf_qvix()` | rows=2823 非空 close=**971** `20220919..20260930`（4.03 年）；首个非空日 **== 期权上市日 20220919**；末日 close=23.33 | ⑳ 500ETF 可得、4.0 年 |
| 4 | `index_option_cyb_qvix()` | rows=2823 非空 close=**971** `20220919..20260930`（4.03 年）；首个非空日 **== 期权上市日 20220919**；末日 close=28.92 | ⑳ 创业板ETF 可得、4.0 年 |
| 5 | `index_option_kcb_qvix()` | rows=2823 非空 close=**801** `20230605..20260930`（3.32 年）；首个非空日 **== 期权上市日 20230605**；末日 close=34.57 | ⑳ 科创50ETF 可得、3.3 年（**最短的一条，仍 > spec 的 3 年**） |
| 6 | 覆盖率判定（探针断言） | `[COV] QVIX 覆盖 5/5 个品种，且全部 ≥3 年（最短 = 3.32 年）` | ⑳ spec「期权波动率覆盖 ≥3 个品种」**超额满足**（5/5） |
| 7 | 上游 HTTP 头 + 原始 CSV | `Last-Modified: Wed, 30 Sep 2026 07:13:54 GMT`、`Content-Length: 913900`、`Content-Type: application/octet-stream`；原始 CSV rows=**2823** == AKShare 返回 rows=**2823**；首行 `2015/2/9`、末行 `2026/9/30`、**列数 87** | ⑳ **"单次上限"这一问的答案 = 文件本身**（无库内截断、无分页 API）；⚠️ **末行 2026-09-30 比期货腿 2026-10-12 晚 3 个交易日 = 源侧发布滞后** |
| 8 | 上游口径（背景核实，非探针输出） | 官方「上证50ETF波动率指数（iVIX）」**自 2018-02-22 起由中证指数公司暂停发布** | ⑳ 现存 5 条 QVIX 均为**第三方编制** ⇒ `source_tier='highfreq'`（**不得**记 `official`） |
| 9 | `fut_basic(exchange="CFFEX")` | rows=**728** cols=`[ts_code, symbol, exchange, name, fut_code, multiplier, trade_unit, per_unit, quote_unit, quote_unit_desc, d_mode_desc, list_date, delist_date, d_month, last_ddate]`；`fut_code` 分布 `{IF:206, IC:146, IH:146, TF:59, IM:59, T:52, TS:39, TL:21}` | ㉑ spec 点名的 **IF/IC/IH/IM 四品种全部在册** |
| 10 | 同上，逐品种 | IF：206 个合约 `list_date 20100416..20260921`、`multiplier=[300.0]`；IC：146 个 `20150416..`、`multiplier=[200.0]`；IH：146 个 `20150416..`、`multiplier=[300.0]`；IM：59 个 `20220722..`、`multiplier=[200.0]` | ㉑ 合约乘数实测与常识一致（IF/IH=300、IC/IM=200）⇒ 点位→金额换算可用 |
| 11 | `fut_daily(ts_code="IF.CFX", start_date="20000101")` | rows=**2000**（**命中单次上限**）· 最早 `20180711` 最新 `20261012` | ㉑ **必须分片**（不分片则 2018 年以前整段静默丢失） |
| 12 | 反向证据：`fut_daily(IF.CFX, 20180101→20261016)` | 仍 rows=**2000**、最早仍 `20180711` | ㉑ **"起点写早一点"救不了** ⇒ 分片是唯一出路（这条正是"看起来 ≥3 年、实际缺头"的机制） |
| 13 | `fut_daily(ts_code="IF1005.CFX")`（已退市合约，不带窗口） | rows=**25** `20100416..20100521` | ㉑ 退市合约整段可回 ⇒ 备选方案「逐合约取 + 用 `fut_mapping` 拼主力」可行 |
| 14 | 分片（每片 ≤3 年，实测每片均 <2000 行）后的深度 | `IF.CFX` 合计 **4002** 行 `20100416..20261012`（≈16.40 年）· `IC.CFX` **2791** 行 `20150416..`（≈11.44 年）· `IH.CFX` **2791** 行 `20150416..`（≈11.44 年）· `IM.CFX` **1021** 行 `20220722..`（≈4.18 年） | ㉑ spec「≥3 年日频升贴水」**四品种全部满足**（最短 IM 4.18 年） |
| 15 | `fut_mapping(ts_code="IF.CFX")`（不带窗口） | rows=**4003** cols=`[ts_code, trade_date, mapping_ts_code]` 日期 `20100416..20261013`，映射到 **183 个真实合约**（首个 `IF1005.CFX`、末个 `IF2612.CFX`） | ㉒ 主力连续**逐日**可解析；**未撞 2000 行上限**（fut_mapping 与 fut_daily 上限不同） |
| 16 | 同上 IC / IH / IM | IC 2792 行 / **126** 个合约（`20150416..20261013`）· IH 2792 行 / **124** 个 · IM 1022 行 / **35** 个（`20220722..20261013`） | ㉒ 四品种均**逐日**映射（不是"当前快照"） |
| 17 | **主力连续语义逐日对拍**（窗口 `20230101–20241231`）：`fut_daily(<品种>.CFX)` × `fut_mapping` 的当日合约 × `fut_daily(该合约)` | `IF.CFX` 对拍 **484 日 / 24 个合约 → 不一致 0 日**；`IC.CFX` 484 日 / 24 → **0**；`IH.CFX` 484 日 / 24 → **0**；`IM.CFX` 484 日 / 23 → **0** | ㉒ **结论：`.CFX` 是逐日主力连续 + 未复权**（= 当日映射合约原始收盘价）⇒ 无前视偏差、可与现货相减 |
| 18 | `index_daily(ts_code=<4 个现货指数>, start_date="20000101")` | `000300.SH` **6006** 行 `20020104..20261012` · `000905.SH` **5286** 行 `20041231..20261012` · `000016.SH` **5529** 行 `20031231..` · `000852.SH` **5286** 行 `20041231..`；列 = `ts_code, trade_date, close, open, high, low, pre_close, change, pct_chg, vol, amount` | ㉓ 四条现货腿全部可得、深度远超 3 年 |
| 19 | 本地 `index_dailies` 覆盖（旁证，查库非探针） | 有 `000300.SH` / `000905.SH` / `000016.SH`（均 `2021-09-22` 起 **1221** 行）**没有 `000852.SH`** | ㉓ 本地表**无法统一承载四条腿** ⇒ 统一走 `index_daily` |
| 20 | **升贴水符号手算核对**（探针 PROBE 4，末日 = 2026-10-12，正值/负值语义逐条打印） | `IF`: 合约 `IF2612.CFX` 期货收盘 **4233.60** − 沪深300 现货收盘 **4287.98** = **−54.38**（−1.268%）= **贴水**；`IC`: `IC2612.CFX` 6986.00 − 7093.02 = **−107.02**（−1.509%）= 贴水；`IH`: `IH2612.CFX` 2779.00 − 2819.41 = **−40.41**（−1.433%）= 贴水；`IM`: `IM2612.CFX` 6921.00 − 7021.47 = **−100.47**（−1.431%）= 贴水 | ㉔ 符号约定**由真实数字定死**：`基差 = 期货 − 现货`，负 = 贴水。探针另带 `|基差|/现货 > 20%` 即 `SystemExit` 的合理区间守卫（防"品种↔指数配错 / 量纲错"静默落库） |
| 21 | 替代方案枚举（`[ALT1]`–`[ALT4]`） | ①分钟级 QVIX 函数 **9 个**（`index_option_{50etf,300etf,500etf,cyb,kcb,50index,300index,1000index,100etf}_min_qvix`）——spec 只要日频，登记为可扩展面；②官方 `option_risk_indicator_sse(date="20261009")` rows=**600** 列含 `IMPLC_VOLATLTY`（**逐合约**明细，要成指数须自建加权 ⇒ 本轮按 spec「直接取隐含波动率」不采用）；③`fut_daily(trade_date="20261009")` rows=**1076**（全市场，其中 CFFEX `.CFX` 64 行）⇒ 按日取需 1700+ 次调用，**按品种分片取连续合约更省**；④现货腿备源 = `index_daily`（主）/ 本地 `index_dailies`（缺 000852.SH） | 替代方案**逐条实机记录**；本段无 `blocked` 源，故这些替代**不启用**，仅登记 |

### 附.8.3 由此得出的实现决定（写进代码注释与报告）

| # | 决定 | 依据 |
|---|---|---|
| 1 | **不新建表、不新建读路**：11 个指标（5 QVIX + 4 基差 + 2 贴水率）落 `industry_metrics(industry_key='market')`，走既有 `GET /api/v1/metrics/{latest,series}` | 附.8.1 ㉕；spec §S1.2 与 ADR 0013 |
| 2 | **QVIX 直接取隐含波动率序列**（`index_option_*_qvix` 的 `close` 列），**不自己实现 Black-Scholes 反解**、也不用 `option_risk_indicator_sse` 自建指数 | spec §S1.10 只要求 IV；"复用优先"（控制器已核实 5 条序列内置存在） |
| 3 | **QVIX 的 `source_tier = highfreq`（第三方参考）**，不是 `official` | 附.8.2 #8：官方 iVIX 自 2018-02-22 暂停发布；现存序列产自第三方 optbbs.com。**产地决定可信度**（ADR 0014），不得借官方之名 |
| 4 | **期货腿取 `<品种>.CFX`（逐日主力连续）、现货腿取对应 `index_daily`**；`fut_daily` **按 ≤3 年分片** | 附.8.2 #11–#14、#17：不分片头部静默丢失；`.CFX` 与当日映射合约**逐日 0 不一致**且未复权 |
| 5 | **基差 = 期货 − 现货（负 = 贴水）**，写进 registry 描述与字典；`fut_mapping` 只作**合约标注**（`extra.contract`）与探针/测试的**独立复核**用 | 附.8.1 ㉔ / 附.8.2 #20；spec「升贴水负值时 UI 明示贴水语义」 |
| 6 | **缺数恒 `NULL` + `extra.missing_reason` + 覆盖率**，**绝不落 0**；三条原因码分开：`source_not_updated`（源侧滞后，实测 QVIX 有 3 个交易日）/ `spot_index_missing` / `futures_leg_missing` | 照 §S1.8/S1.9 既有纪律（ADR 0014「有行 ≠ 有值」）；QVIX 的滞后是**源侧事实**，落 0 会把"没有数据"伪装成"波动率为 0" |
| 7 | **`published_at` 按产地分两档**（Asia/Shanghai，带时区）：QVIX 当日 **15:00**（IV 在收盘那刻定型）；基差/贴水率当日 **16:00**（**期货结算价**在中金所收盘后计算发布，是本行的**较晚一条腿**） | ADR 0011 / Ruling 34-35。**不得回退 `period`**；`_upsert_batched` 有"缺 `published_at`（或非带时区）/ 缺 `caliber_version` 即响亮失败"的落库前守卫 |
| 8 | **基差主值取期货 `settle`（结算价）− 现货收盘**，收盘口径（`close`）另落 `extra.basis_close` 供与行情商口径对拍；两者**都 ≤ 16:00** 可得 | 中金所结算价是当日官方标价（保证金/对冲成本的结算依据），与「IC/IM 贴水率作对冲成本代理」同源；`close` 口径在 15:00 已知，放在 `published_at=16:00` 的行里**不构成前视** |
| 9 | **贴水率 = 基差 / 现货指数收盘 × 100**，**符号与基差一致**（负 = 贴水），**不年化**；年化口径落 `extra.annualized_pct`（= 贴水率 × 365 / 距最后交易日天数）留痕 | spec「`IC/IM 贴水率`作对冲成本与中性策略拥挤度代理」。**必须同号**：若为让贴水显示为正而翻转符号，就正好是 spec 要防的正负号误读。年化值依赖合约最后交易日（`fut_basic.delist_date`，挂牌即已知 ⇒ 无前视），仅作留痕 |
| 10 | **A/B/C 归类 = 全部 B 类**（ADR 0014）：QVIX 与期货日线都是**当日点内事实**，可回算、允许回补；基差/贴水率由两个当日事实相减得出，同样 B 类 | ADR 0014 判据；**无 A 类、无 C 类**（不消费"只给当下"的盘中明细，也不用成分快照回算） |
| 11 | **落库天锚在各源自身可得日上**：QVIX 锚在上游 CSV 有非空值的日；基差锚在"期货腿 ∩ 现货腿"的共同交易日。**默认回补窗口刻意宽于两个源的可得起点**（1900 天会把 QVIX 的 11.6 年截成 5.2 年——那正是 Ruling 59 的形态） | Ruling 59：不拿无关输入截断序列；与 §S1.9/S1.11 的 `erp_10y` 改锚同一纪律 |

### 附.8.4 未验证余项（open items）

| # | 余项 | 影响 | 补验方式 |
|---|---|---|---|
| 1 | **QVIX 的第三方口径未与官方/其它源对拍** | 现存 5 条 QVIX 产自 optbbs.com（官方 iVIX 已停发），其编制方法（加权方式、无风险利率、期限插值）**未公开**，与 2015–2018 官方 iVIX 是否逐值一致**未验证** | 找任一"50ETF 期权隐含波动率指数"的第二个产地（如券商研报/同花顺 iFinD 导出），取重叠区间**逐日对拍**；系统性偏移则登记为口径差（**不得**直接改数） |
| 2 | **中金所结算价的精确发布时刻未验证** | 附.8.3 #7 的 `16:00` 是**保守取值**（收盘 15:00 后计算发布）。若实际晚于 16:00，回测在 16:00–实际发布之间会"提前看到"结算价 | 用中金所官网每日结算数据页的 `Last-Modified`/发布日期字段采样若干日；或改用收盘口径（`extra.basis_close` 已有，可直接作为主值而无需改 schema） |
| 3 | **QVIX 的发布滞后是否稳定（1–3 个交易日）** | 实测 2026-10-13 时 CSV 仍在 2026-09-30（`Last-Modified` 2026-09-30）。若滞后长期 >5 个交易日，该指标对短周期择时的价值下降 | 连续多日记录 CSV 的 `Last-Modified` 与末行日期，统计滞后分布；超过阈值则在 UI 明示"源侧滞后 N 日" |
| 4 | **`fut_basic(exchange="CFFEX")` 与不带参数时的行数不一致（728 vs 642）** | 不带 `exchange` 的调用返回 10000 行（**命中该接口上限**，各交易所被截断），故 CFFEX 的 642 是**截断后的残值**、728 才是完整表。本任务只用 `exchange="CFFEX"`，且实测 4 品种**全部** 183/126/124/35 个映射合约都在表内、`delist_date` 全非空 | 若将来要全市场期货表，必须按 `exchange` 逐个取，**不得**用不带参数的一次调用（会静默少行）——这条已由本探针记录 |
| 5 | **基差只做主力连续，未做次主力/各月份曲线** | spec §S1.10 只要"四品种升贴水"；期限结构（跨期价差）与次主力（`IFL.CFX`）**未落库** | 若后续要期限结构，`fut_daily` 里 `IFL/IFL1/IFL2/IFL3.CFX`（当/下/下季/下下季连续）实测同日可得（附.8.2 #21 ③ 的 1076 行里已含），按新 `metric_key` 另立即可 |

---

## 附.9 S1.7 段：资金流四件套 + 陆股通持仓（季度）的数据源复核，含**北向每日净买入的裁决**（2026-10-13）

> 本节是 [S1.7 资金流四件套 + 外资画像重构](../specs/S1-market-sentiment.md#s17-资金流四件套--外资画像重构)
> 的**数据源可行性交付物 + Step-0 闸门的裁决记录**。方法与结论格式沿用
> [附.1](#附1-七项复核结论) / 附.5 / 附.6 / 附.7 / [附.8](#附8-s110-段波动率与衍生品期权隐含波动率--股指期货升贴水的数据源复核2026-10-13)
> （来源 / 频率 · 历史深度 / 三态结论 / 替代方案）。**核查日期：2026-10-13**，探针脚本
> [`backend/scripts/spike_s17_capital_flows_sources.py`](../../backend/scripts/spike_s17_capital_flows_sources.py)
> （**已入库**，任一源为空 / 关键假设不成立即 `SystemExit`，**不在空结果上打印 `[OK]`**）。
>
> **本节回答的五个问题**：①**北向每日净买入到底还在不在**（spec 的"背景硬事实"与控制器实测
> 矛盾，必须先裁决）②ETF 份额从哪来、取哪几只宽基、依据是什么 ③南向 `south_money`
> 是日频还是累计 ④产业资本增减持的原料（公告 S3.2）到了没有 ⑤陆股通持仓（季度）的
> 可得性、行数上限与覆盖是否规则。
> **未通过验证前不写适配器、不落库、不往 UI 填数字。**

### 附.9.1 结论总览

| 数据源（行标签） | 来源 | 频率 / 历史深度 | 三态结论 | 替代方案 |
|---|---|---|---|---|
| ㉖ **北向每日净买入（已取消）** | **TuShare `moneyflow_hsgt` 的 `north_money` + 独立产地东财 `stock_hsgt_hist_em`（AKShare 封装）**。**实机记录**：附.9.2 #1–#5 | **该字段仍在日更，但语义已变**：断点前（2024-01-01…2024-08-16）148 日**有 83 个负值**（`[-13056.64, 22448.99]`）= 净买入（百万元）；断点后（2024-08-19…2026-10-12）**502 日非空、负值 0、零值 0**（`[77584.24, 510101.29]`）= 换成**成交量口径**。**独立产地交叉验证**：东财「北向·当日成交净买额」在断点后 **502/502 行全空**（`买入成交额`/`卖出成交额`/`历史累计净买额` 同样全空）⇒ **净买入口径确实已取消** | `不可得`（**净买入**）；`可得但已换语义`（该列的成交量口径） | 无（净买入本身没有替代：交易所自 2024-08-19 起不再披露北向每日净买入）。**处置：不删除既有能力**（删除即删活能力，Ruling 30）—— 见附.9.5 的 spec 修正请求 |
| ㉗ **宽基 ETF 份额** | **TuShare `fund_share`（需新增客户端方法 `fetch_fund_share`）**。**实机记录**：附.9.2 #6–#13 | **日频**。单日全市场快照 **1783 行**（SH 1005 / SZ 778，其中 `fund_type='ETF'` **1694**），`fd_share` **非空 1783/1783**。**单次 2000 行上限且超限截"最近"**：不带 `ts_code` 的宽窗（`20000101→20261016`）只回 2000 行、**只覆盖 `20261009..20261016`** ⇒ 长历史必须**逐 `ts_code`** 取。逐只深度：篮子 6 只各自 `20230103..20261012` **915–916 行、非空 100%、重复日 0、零值 0** | `可得`（spec「≥1 年日频序列」被超额满足：实测 **3.77 年**） | 不适用（本项可得）。**但必须逐代码取**（分片/宽窗会静默缺头，与附.7 ⑭ `margin`、附.8 ㉑ `fut_daily` 同族陷阱） |
| ㉘ **南向资金（含**一处口径断点**）** | **TuShare `moneyflow_hsgt` 的 `south_money`（已接入）+ 独立产地东财 `stock_hsgt_hist_em(南向资金)`**。**实机记录**：附.9.2 #14–#20 | **日频**（港股通交易日 ⊂ A 股交易日）。**`south_money` 是「历史累计净买额」，不是日频值**：与东财「历史累计净买额×1e4」**650/650 日逐值相等**，其一阶差分与东财「当日成交净买额」**649/649 日逐值相等**（单位同为亿元）。⚠️ **该列在 `2023-11-24` 换过语义**（此前 114 日全部匹配"日频/百万元"且**37 日为负**；此后 61 日全部匹配"累计/亿元"且无一日 ≤0） | `可得`（**必须差分**，且**只落断点及之后**） | 东财 `stock_hsgt_hist_em` 的「当日成交净买额」可直取日频值（2014 起 2727 非空日）→ 作**独立对拍**用；本任务**不换主源**（TuShare 已接入，且两源逐值一致） |
| ㉙ **产业资本增减持** | **原料 = 上市公司公告（spec §S3.2 公告采集）** —— **S3.2 未落地**（`app/services/content_ingest/` 不存在、`content_items`/`content_entities` 模型不存在；本 token 的 `anns_d` 亦**权限不足**）。**实机记录**：附.9.2 #21–#23 | —（原料不可得，谈不上频率/深度） | `blocked-on-verification`（**待接**） | **上游替代 `stk_holdertrade`（结构化增减持）实测可得**：`trade_date=20261012` 回 **3000 行**（**已撞 3000 行上限** ⇒ 按日取也要分页），列含 `in_de ∈ {IN,DE}` / `holder_type ∈ {C,G,P}` / `change_vol` / `change_ratio`。**本轮不采用**：spec 明写原料来自公告（公告面才提供 `publisher_tier` 与正文可回溯），**换原料是控制器裁定权** |
| ㉚ **陆股通持仓（季度）** | **TuShare `hk_hold`（候选产地，本轮不接 ingest）**。**实机记录**：附.9.2 #24 | **季度末抽样 12 期，10 期给出陆股通（`exchange ∈ {SH,SZ}`）行**（3,254–3,788 行/期）；**覆盖不规则**：`20240329` 返回 **0 行**、`20260930` **只回港股通行**（1014 行全 `HK`）。**单次 4200 行上限**（多期已撞满 ⇒ 将来 ingest 必须分页/逐所取） | `可得但覆盖不规则`（本轮**只落口径与滞后说明，不落任何数据**） | 不适用（无第二产地调研；本轮不接 ingest）。**滞后说明**：2024-08-19 起沪深港通持股改为**按季度披露**，故落后当季最多约一个季度 + 源侧发布滞后 —— 该文案已落 registry `MetricDef.description` 与字典 |
| ㉛ **落库形态（前置决策）** | **既有基建**：`industry_metrics`（`industry_key='market'`，S1.2 已加 `published_at` / `caliber_version`）+ 既有读面 `GET /api/v1/metrics/{latest,series}` + 既有 PIT 守卫 `market_activity_service.upsert_metric_rows` | — | `可得`（复用既有） | **不新建表、不新建读路**；**两融不重做**：`margin_financing_balance` / `margin_short_balance` 由 S1.9 已落，本批次**只引用**（机械可证：见附.9.3 #1） |

> **三态取值说明**：㉖ 的**净买入**判 `不可得`（两产地一致：东财 502 行全空、TuShare 该列零负值）；
> ㉗ / ㉘ 判 `可得`（且都带着"必须先处理"的上游陷阱：逐代码取；先差分 + 口径断点）；
> ㉙ 判 `blocked-on-verification`（spec 自己写明"若 S3.2 未完成则标记为待接、不得用假数据填充"）；
> ㉚ 判 `可得但覆盖不规则`（故只落口径）；㉛ 判 `可得`（复用既有基建）。
> **本节不填任何估计值**：缺失一律 `NULL + missing_reason`（见附.9.3 #5）。

### 附.9.2 实机调用记录（本节专属，2026-10-13）

> 全部由 `backend/scripts/spike_s17_capital_flows_sources.py` 产出（脚本在任一源为空 /
> 关键假设不成立时 `SystemExit`）。下表逐行标注"哪一条断言"决定了附.9.1 的哪个结论。

| # | 调用 / 断言（probe 行） | 原始输出（节选，逐字） | 支撑 |
|---|---|---|---|
| 1 | `moneyflow_hsgt(20240101..20240816)`（断点前） | `[PRE ] rows=148 非空=148 负值=83 区间=[-13056.64, 22448.99]` | ㉖ **断点前 = 净买入**（净买入才可能为负） |
| 2 | `moneyflow_hsgt` 断点后三段（`20240819..20250630` 200 行、`20250701..20251231` 122 行、`20260101..20261016` 180 行） | `[POST] 合计 rows=502 20240819..20261012 非空=502 负值=0 零值=0`；`区间=[77584.24, 510101.29]` | ㉖ **上游仍在给值，但零负值/零零值 ⇒ 语义已变** |
| 3 | 断点邻域逐行（`20240814..20240823`） | `20240816 hgt=-2568.22 sgt=-4206.77 north_money=-6774.99`；`20240819 hgt=46129.34 sgt=41981.21 north_money=88110.55` | ㉖ **切换点就在 2024-08-19**（同列、同量纲、语义换掉） |
| 4 | 东财 `stock_hsgt_hist_em(北向资金)` 逐列非空计数 | `断点前 当日成交净买额 非空=2264/2264`；**`断点后 当日成交净买额 非空=0/502`**（买入成交额 0/502、卖出成交额 0/502、历史累计净买额 0/502） | ㉖ **独立产地证实"北向每日净买入已取消"** |
| 5 | 断点前两产地逐值对拍（`north_money`(百万元) vs 东财「当日净买额×100」） | `共同日 148，超出容差 0.01 的 1 日（占比 0.6757%，上限 2.00%）`；`[DIFF] 20240708: 我方=-3209.2300 对方=-2199.9200 差=-1009.3100` | ㉖ **断点前两产地逐值一致**（唯一分歧日是 2024-07-08，登记为**产地修订**，见附.9.4 #1）；同时证明 `north_money` 在该段的单位是**百万元** |
| 6 | `fund_share(trade_date=20261012)` | `rows=1783 cols=['ts_code','trade_date','fd_share','fund_type','market']`；`fd_share 非空=1783/1783`；`market 分布={'SH':1005,'SZ':778}`；`fund_type 分布={'ETF':1694}` | ㉗ 可得、日频、全非空 |
| 7 | `fund_share(ts_code=510300.SH, 20100101→)` | `rows=2000 20180724..20261012`（撞 2000 行上限） | ㉗ 单代码上限 2000 行（≈8 年，够 ≥1 年） |
| 8 | `fund_share` 不带 `ts_code` 的宽窗 | `rows=2000 只覆盖 20261009..20261016 ⇒ 单次上限 2000 且**截最近**` | ㉗ **必须逐 `ts_code` 取** |
| 9 | `fund_basic(market=E)` | `rows=2978；被动指数型 & L = 1559` | ㉗ 选篮的候选集来源 |
| 10 | 篮子选定的**规模**排名（规模 = `fd_share` × `fund_daily.close`） | `沪深300: 510300.SH 规模=1069.5 亿元（2452998.8 万份 × 4.36）`；`上证50: 510050.SH 208.7 亿（711966.7 × 2.932）`；`中证500: 510500.SH 404.5 亿（567776.9 × 7.125）`；`中证1000: 512100.SH 230.3 亿`；`科创50: 588000.SH 952.3 亿`；`创业板指: 159915.SZ 656.9 亿` | ㉗ **六只都是族内规模第一**；⚠️ **只比份额会选错**（`512010.SH` 沪深300医药卫生ETF 有 387 万份却只有 147 亿元） |
| 11 | `fund_basic.benchmark` 文本匹配的**坑**（逐条打印选定者的 benchmark 原文） | `[MISS] 510050.SH`（benchmark 原文 `'上海证券交易所50成份指数×100%'`，规模 208.7 亿 > 命中集最大 `510100.SH` 28.5 亿）；`[MISS] 510500.SH`（原文 `'中证小盘500指数×100%'`，404.5 亿 > `159820.SZ` 17.1 亿） | ㉗ **benchmark 文本匹配不得单独作为选篮依据**（另 4 只命中且为族内最大） |
| 12 | 篮子逐只可得性（`20230101` 起） | `510300/510050/510500/512100/588000 各 rows=915`、`159915 rows=916`，全部 `20230103..20261012 非空=100% 重复日=0 零值=0` | ㉗ **≥1 年（实测 3.77 年）** |
| 13 | 份额序列的**非交易日行**扫描（篮子 6 只 × `20260101..20261231`） | `[DIRTY] 159915.SZ(易方达创业板ETF) 非交易日行 = ['20260117']`；`[VERDICT 2d] 上游 fund_share 有非交易日行（['159915.SZ']）` | ㉗ **上游有周六脏行** ⇒ 该日并集日历多一天、只有 1/6 成员可得 ⇒ 落 NULL + `partial_basket_coverage`（实测 `n_with_share=1`、`member_coverage=0.166667`），**不拿一只成员冒充全篮子**。⚠️ 本行是**反证型断言**：若某次扫描一只脏行都没有，探针 `SystemExit`（不许静默保留结论） |
| 14 | `moneyflow_hsgt` 南向全区间（`20240102..20261012`） | `[IDENT] south_money == ggt_ss + ggt_sz 不成立的行数 = 98 / 650`；`[SHAPE] south_money 负值=0；一阶差分下降天数=150/649` | ㉘ 该列**不是**日频净买入（累计序列几乎只增；两条腿的恒等式**并不总成立**，说明南向腿的语义本身也不干净） |
| 15 | 东财 `stock_hsgt_hist_em(南向资金)` | `rows=2728 累计净买额非空=2728 当日净买额非空=2727` | ㉘ 对拍样本可用 |
| 16 | 累计口径逐值对拍（`south_money` vs 东财「历史累计净买额×1e4」） | `共同日 650，超出容差 0.01 的 0 日（占比 0.0000%）` | ㉘ **单位 = 亿元、口径 = 历史累计净买额**（逐值相等） |
| 17 | 差分逐值对拍（`Δsouth_money` vs 东财「当日成交净买额」） | `共同日 649，超出容差 0.01 的 0 日（占比 0.0000%）` | ㉘ **一阶差分就是日频净买入**（逐值相等） |
| 18 | 南向样例（末日） | `[SAMPLE] 20261012: south_money=55470.72（亿元，累计）；当日净买入 = 26.0300 亿元`（东财同日 26.0327） | ㉘ 手算可复核 |
| 19 | 南向**未**被取消 | `[LIVE] 东财南向 断点后 rows=503 当日净买额非空=502 ⇒ 南向每日净买入未被取消（与北向相反）` | ㉘ 与 ㉖ 形成对照：**同一个交易日，北向是空的、南向有值** |
| 20 | **口径断点**检测（把每一天同时与东财「当日净买额×100」和「累计净买额×1e4」比） | `[TAGS ] 窗口内 175 日：DAILY=114 CUM=61 ??=0`；`[BREAK ] 第一个「累计」日 = 20231124（此前最后一日 = 20231123）`；`[PRE ] 断点前 114 日，其中负值 37 日`；`[POST ] 断点后 61 日，不符「累计」假设或 ≤0 的 0 日`；`[HAND ] 20231127: diff = 28841.79 − 28838.31 = 3.4800；东财当日净买额 = 3.4773` | ㉘ **`south_money` 在 2023-11-24 换过语义**（同一列、量纲相近，只有逐值对拍才能发现）；**断点日自身无累计前值 ⇒ 差分不可得** |
| 21 | S3.2 交付物是否存在（代码面） | `[CODE ] app/services/content_ingest/ 存在？False`；`content_items / content_entities 模型存在？False` | ㉙ **公告采集未落地** |
| 22 | 上游替代 `stk_holdertrade(trade_date=20261012)` | `rows=3000 cols=[..., 'in_de', 'holder_type', 'change_vol', ...]（上限 3000）`；`in_de 取值=['DE','IN'] holder_type 取值=['C','G','P']`；`⚠️ 单日 3000 行已撞上限` | ㉙ 替代**可得但本轮不采用**（换原料属裁定权） |
| 23 | `anns_d(20261001..20261016)`（公告原始面，旁证） | `[ANN ] anns_d 不可得：RuntimeError: TuShare API 'anns_d' permission denied` | ㉙ 本 token 连公告原始面都没有权限 |
| 24 | `hk_hold` 逐季度末（12 期） | `20231229 rows=4042 {SZ:1758, SH:1513, HK:771} 陆股通=3271`；**`20240329 rows=0`**；`20240628 rows=4137 陆股通=3347`；`20240930 rows=3540 陆股通=3540`；`20241231 / 20250331 / 20250930 / 20251231 / 20260331 / 20260630` 各 `陆股通≈3254–3385`（**均撞 4200 行上限**）；**`20260930 rows=1014`（全 HK，陆股通=0）** | ㉚ **10/12 期可得、覆盖不规则、4200 行上限** ⇒ 只落口径与滞后说明 |
| 25 | 探针自身的反证断言（防"前提不成立还照抄结论"） | 断点前若**无**负值 → `SystemExit`；断点后若出现**负值/零值** → `SystemExit`；东财北向断点后若**仍有**净买额 → `SystemExit`；东财南向断点后若当日净买额**全空** → `SystemExit`；篮子某只 <250 行 / 有重复日 / 有空值 → `SystemExit`；**选定者规模 < benchmark 文本命中集最大者** → `SystemExit`；**2026 全年一只非交易日行都没有** → `SystemExit`；断点后出现**不符「累计」假设或 ≤0** 的值 → `SystemExit`；S3.2 若已落地（`content_ingest`/`content_items` 存在）→ `SystemExit`；`hk_hold` **全部**季度末都拿不到陆股通行 → `SystemExit` | 全节：**结论可被机器推翻**，不是叙述。（服务侧的**口径回退守卫**另有一档更严的下界 `south_money <= 10000` 亿元，见附.9.3 #4 —— 那是落库路径的守卫，不是探针的） |

### 附.9.3 由此得出的实现决定（写进代码注释与报告）

| # | 决定 | 依据 |
|---|---|---|
| 1 | **两融"引用不重做"**：`margin_financing_balance` / `margin_short_balance` 沿用 §S1.9 的登记（`MARKET_ACTIVITY_METRICS`），本批次**不重复登记、不重算**；并在 `_assert_capital_flows_keys()` 里机械断言这两个 key **确实存在于 S1.9 的登记面**（"引用"落空即导入期失败） | 附.9.1 ㉛；spec §S1.7 契约表自己写着「已有（S1.9）」 |
| 2 | **北向：不删除既有能力**（`northbound_daily` / `get_northbound_series` / `northbound_daily_job` 一律不动） | 附.9.2 #2/#4：上游**仍在日更**该字段（502/502 非空）—— 删除即删活能力（Ruling 30）；该能力是"北向成交额"序列，不是"净买入" |
| 3 | **ETF 份额变化的落库天锚在 `fund_share` 有值的日**（各成员可得日的并集），逐成员取**自己的**前一个份额日做差；篮子以**规模**（份额×收盘价）选定 | 附.9.2 #10–#12；Ruling 59（不拿无关输入当日历底座） |
| 4 | **南向必须差分，且只落 `2023-11-24` 及之后**；断点日自身落 `NULL + prev_connect_day_missing`；断点后出现 `south_money <= 10000`（亿元）即**响亮失败** | 附.9.2 #16/#17/#20。**这是 v1→v2 的唯一实质差异**：v1 把整段历史当"累计"差分，断点前落的是**二阶差分（纯噪声）**；v2 加断点窗口 + 口径回退守卫，并按 ADR 0011 第 4 条**重灌**（`caliber_version` 从 `s1.7-v1` bump 到 `s1.7-v2`） |
| 5 | **缺数恒 `NULL` + `extra.missing_reason` + 覆盖率，绝不落 0**；原因码分开：`partial_basket_coverage`（篮子不全，**不归一**）/ `no_prev_fund_day`（序列起点）/ `source_empty`（上游缺值日）/ `prev_connect_day_missing`（无累计前值）/ `prev_value_missing`（差分跨缺口，**不**回退到更早的行）/ `upstream_dependency_pending`（待接 S3.2）/ `not_ingested_page_pending`（陆股通持仓只登记口径） | 照 §S1.8/S1.9/S1.10 既有纪律（ADR 0014「有行 ≠ 有值」）。**0 份净申购是事实断言**，与"没有数据"完全相反 |
| 6 | **`published_at` 两档（同日 21:00，Asia/Shanghai，带时区）**：ETF 份额（交易所/基金管理人盘后披露当日份额）、沪深港通当日数据（收市后披露）；`caliber_version` 恒填当前版本；**落库前经 `market_activity_service.upsert_metric_rows` 的 PIT 守卫**（缺列即响亮失败、一行不落） | ADR 0011 / Ruling 34-35；Ruling 59 的原始事故形态（12036 行 NULL 而无人报错）。**21:00 本身未验证**（附.9.4 #2） |
| 7 | **产业资本增减持只登记、不落行**；读面回全 `None` 骨架（不是 404），覆盖率对账里记 `PENDING: upstream_dependency_pending` | 附.9.2 #21–#23；spec 原话「标记为待接，不得用假数据填充」 |
| 8 | **陆股通持仓（季度）只落口径与滞后说明**（`freq='quarterly'`、`missing_reason='not_ingested_page_pending'`）；一切文案写「**陆股通持仓**」，**不得**称「外资」 | 附.9.2 #24；Ruling 19（HKSCC 是名义持有人**汇总账户**，持仓主体不可拆分）。**spec 说的 `market/foreign` 重构是"从未建过"**：该路由与"外资画像"页在本分支与 `origin/main` 都不存在 ⇒ 页面与 ingest 归后续批次 |
| 9 | **A/B/C 归类 = 两个落库指标均为 B 类**（ADR 0014）：ETF 份额日变化只用 T 与 T−1 两行；南向净买入只用 T 与前一累计日两行。**无 A 类**（不消费盘中明细）、**无 C 类**（不用成分快照回算） | ADR 0014 判据 |
| 10 | **Ruling 61 的读面小改动**：`MetricLatestOut` / `MetricHistoryPointOut` 增加 `missing_reason` 与 `coverage`，取数口唯一（`industry_metric_service._missing_info`，覆盖率键按 `coverage` → `weight_coverage` → `member_coverage` 固定顺序取第一个数值） | 四个前序任务（S1.8/S1.9/S1.10/S1.11）都落了 `extra.missing_reason`，读面不回传 ⇒ 前端只看到 `value=null`，**分不清"源侧滞后"与"真缺数"** |

### 附.9.4 未验证余项（open items）

| # | 余项 | 影响 | 补验方式 |
|---|---|---|---|
| 1 | **2024-07-08 的北向净买入两产地不一致**（TuShare −3209.23 vs 东财 −2199.92，百万元，差 1009.31） | 只影响断点前那一段的**对拍**（本批次不落北向净买入），不影响 ETF/南向的任何落库值 | 取第三产地（如 Wind/iFinD 导出或交易所原始披露）判定谁被修订；若属 TuShare 修订，在其它批次引用该段时须显式标注 |
| 2 | **`published_at = 当日 21:00` 这两个时刻未验证** | 21:00 是**保守取值**（盘后清算披露）。若实际晚于 21:00，回测在 21:00–实际披露之间会"提前看到"当日份额/南向数据 | 连续多日记录 `fund_share`/`moneyflow_hsgt` 当日数据的**首次可得时刻**（盘后轮询采样），得到滞后分布后收紧或放宽该档；或改记次日 09:00（更保守但会低估可得性） |
| 3 | **口径回退守卫是启发式的，不是证明** | 它挡住两种**已知**形态（"改回日频/百万元"→负值或近 0；"改回小额口径"→< 10000 亿元），但未穷举第三种（例如恒正的"日频成交额/亿元"约 600，会被 10000 的下界挡住 ✓，但若改成恒正且量级 >10000 的**日频**序列则挡不住） | 把独立产地（东财 `stock_hsgt_hist_em` 的「当日成交净买额」）纳入 ingest 做**逐值对拍**（本任务已证明两源 649/649 逐值相等 ⇒ 该路径可行）；或在契约层要求"口径变更必须 bump `caliber_version`"并由上游变更公告触发重核 |
| 4 | **南向 `south_money` 与 `ggt_ss + ggt_sz` 的恒等式有 98/650 日不成立** | 说明南向两条腿的语义本身也不干净（可能是"累计/当日"混装）。**本任务的落库值不依赖该恒等式**（只依赖 `south_money` 自身的累计口径，且与东财 650/650 逐值相等） | 逐日对拍 `ggt_ss`/`ggt_sz` 与东财「港股通(沪)/(深) 当日净买额」，判定哪一段不成立；若将来要拆沪/深两条南向腿，必须先做这件事 |
| 5 | **陆股通持仓的季度末覆盖不规则**（12 期里 1 期 0 行、1 期只回港股通行） | 本轮不落数据，故无影响；将来 ingest 时必须能"某季末无陆股通行"与"采集失败"区分开 | 扩大季度末抽样（2018 起全部 30+ 期）并记录每期的 `exchange` 分布；把"无陆股通行"登记为 `missing_reason`（而不是 0/空图） |
| 6 | **`hk_hold` 的 4200 行上限未做分页验证** | 多期已撞满 ⇒ 若不分页，单期持仓明细会**静默少行**（与附.7 ⑭ / 附.8 ㉑ 同族陷阱） | 用 `exchange`/`trade_date` 交叉切分验证能否绕过上限；或按 `trade_date` + 分页参数逐页取并核对总行数 |
| 7 | **沪深300 族只取了一只（510300）** | 本指标表达的是"六只代表性宽基 ETF 的合计净申购"，**不是**"全部宽基 ETF"；2024 年汇金同时增持了 510310 / 159919 等同族品种，故"国家队"痕迹被**系统性低估** | 若要更贴近"国家队痕迹"，把篮子扩成"各族规模 Top-N"（N≥3），并将篮子定义随 `caliber_version` bump 一次；扩篮前后**不可混用**同一条序列 |

### 附.9.5 请求控制器裁定：spec §S1.7 的"背景硬事实"与验收判据需要修正

**spec 原文（§S1.7）**：

> **背景（硬事实）**：北向资金**盘中实时**数据 2024-05-13 取消、**每日成交额/净买入**
> 2024-08-19 取消（改按季度披露持股）。
> **验收判据**：`grep -rn "北向\|northbound" backend/app/ frontend/src/` **不得**出现
> **每日净买入**口径的字段名。

**实机裁决（附.9.2 #1–#5）**：spec 的这句话**一半成立、一半不成立**：

- ✅ **成立**：**每日净买入**确实在 **2024-08-19** 取消。两产地一致 ——
  东财「北向·当日成交净买额」断点后 **502/502 行全空**；
  TuShare `moneyflow_hsgt.north_money` 同期的 502 行**零负值、零零值**
  （而断点前 148 日有 **83 个负值**）⇒ 该列已被上游**换成语义不同的量**（成交量口径）。
- ❌ **不成立**：**每日成交额**没有被取消。上游仍在**每交易日**给值（502/502 非空），
  该 ingest 也是活任务（`northbound_daily_job` 已注册在 `scheduler/runner.py`）。

**因此"删除 `northbound` 相关能力"会删掉一条活序列**（Ruling 30：删能力前必须先证明它是死的
—— 实测证明的恰好相反）。同时，**既有的命名是错的**：`northbound_daily.net_amount`、
`get_northbound_series()`（文档字符串写"北向净流入"）、`NorthboundCard` 等把
**2024-08-19 之后的成交量值**标成了"净流入"，dev 库里那 **42 行**（`2026-08-05..2026-10-09`，
`207,941.62 … 351,740.65`，非空 42/42、零 0 值）**全部是这类错标值**。
这不是"能力死了"，而是**口径错标**（且属于既有市场数据面的历史欠账，不在本任务范围内）。

**本任务已做**：**一行都没删、也没改** `northbound`（Ruling 30 的反面同样适用：
未证明为死的东西不得删除）；把原始实证与裁决记在本节，并在报告里逐条分类
`grep` 结果（保留 / 有实证支撑 vs 已清理）。

**请控制器裁定（本任务无权改 `docs/specs/**`）**：

1. **修正"背景硬事实"的措辞**（建议逐字）：

   > **背景（硬事实）**：北向资金**盘中实时**数据 2024-05-13 取消；**每日净买入**
   > 2024-08-19 取消（改按季度披露持股）。**每日成交额仍披露**：TuShare
   > `moneyflow_hsgt.north_money` 自 2024-08-19 起改为成交量口径（实测 502/502 非空、
   > 零负值），与 2024-08-19 之前的净买入口径**同列不同义**，引用时必须标注断点。

2. **把验收判据改成语义正确的表述**（二选一，建议 A）：

   - **A（推荐，保能力 + 修命名）**：`grep -rn "北向\|northbound" backend/app/ frontend/src/`
     中，**凡承载 2024-08-19 之后 `north_money` 的字段/接口/文案，其命名与说明必须体现
     "成交额"口径**（不得写成"净流入/净买入/net"）；**每日净买入口径的字段名只允许出现在
     "该口径已取消"的说明文字里**。
   - **B（只做命名审计，最小改动）**：`grep -rn "净流入\|净买入\|net_amount\|net_flow"`
     在 `northbound` 命中处必须逐条给出"这是成交额还是净买入"的书面判定，
     不允许存在"未标注口径的北向每日净买入口径字段名"。

3. **是否需要把既有 `northbound` 命名改成成交额口径**（`net_amount` → `turnover_amount` 等）
   —— 这是一次**跨表/跨 API/跨前端**的改名，且会动到 `scheduler` 任务名与迁移，
   属独立的修订批次。若控制器批准，建议与"北向成交额"指标在 `industry_metrics` 的登记
   一并做（本任务已把 `moneyflow_hsgt` 的客户端方法与断点实证备好）。

### 附.9.6 控制器裁定与 Task 16 处置结果（2026-10-13，后续批次补记）

> 本节只补记"附.9.1–附.9.5 写完之后发生了什么"，**不改写**上面的实机证据。

- 控制器**采纳了附.9.5 的方案 A**，并把 §S1.7 的验收判据改成**语义判据**（Ruling 62/63）：
  凡承载 2024-08-19 之后 `north_money` 的字段名 / DTO 字段 / UI 文案必须体现**成交额**口径，
  不得呈现为净买入/净流入；`grep -rn "北向\|northbound" backend/app/ frontend/src/` 的每一处
  命中都要能归入「断点前的净买入历史（显式标注断点）」或「成交额口径（命名已体现）」。
- **Task 16 已按方案 A 落地**（`feat/research-platform-ia`）：
  - 行级口径列：迁移 `b6a1f0c2d7e4` 给 `northbound_daily` 加 `amount_kind`
    （`'net_buy'` | `'turnover'`，`NOT NULL` + `CHECK`），按断点回填既有行；
    并把 `net_amount` **改名**为 `amount`（数值一个不改）。
  - 断点常量的唯一活来源：`app/services/northbound_caliber.py`（`NORTHBOUND_TURNOVER_SINCE`）。
  - 读面：`GET /api/v1/market/northbound` 每点带 `amount_kind`，信封再给本窗口口径
    （`net_buy` / `turnover` / 跨断点 `mixed` / 无行 `None`）。
  - 前端 `NorthboundCard`：按口径念数（成交额不带正负号、不着涨跌色、零轴仅净买入时画）；
    原「近30日累计（净流入）」改为「近30日累计成交额」，跨断点 `mixed` **不累计**。
  - 覆盖矩阵公开文案的「沪深港通净流入」改为「沪深港通成交额（2024-08-19 起）」
    （与 `docs/design/landing-market-theme.md` §5 同源）。
  - 能力**一行未删**：表 / `ingest_northbound` / `northbound_daily_job` / 读端点全部保留；
    断点前的净买入历史如出现，会被标成 `amount_kind='net_buy'` 而不是混进成交额序列。
- 因此本节 3 号问题（"是否改名"）所设想的 `net_amount → turnover_amount` 单一改名**未被采用**：
  同一列两种口径，只把列名改成"成交额"会让断点前的净买入行说谎；改用
  **`amount` + 行级 `amount_kind`** 的单值列加口径判别列。
