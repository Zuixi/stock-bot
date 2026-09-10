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
| `sow_inventory` | 能繁母猪存栏 | 月度环比 + 季度末绝对数 | **推荐抓中国畜牧业协会猪业分会**（pig.caaa.cn，每月转载"全国生猪产品数据"，HTML 规范好解析）；官方出口为五部门"生猪产品信息数据平台" | 月度多为**环比变化率**，**绝对数以统计局季度末为准**（data.stats.gov.cn）——两种口径分 source 存储 |
| `hog_inventory` | 生猪存栏 | 季度 | 国家统计局 data.stats.gov.cn | 可抓可查 |
| `hog_slaughter_quarterly` | 生猪出栏量 / 猪肉产量 | 季度/年度 | 国家统计局 | 长周期对比基准 |
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
- 能繁存栏：2018 至今约 100 个月度点，协会网站/历史发布会一次性整理，约半天。
- 出栏量/成本：随公告季滚动补，无集中回补。

## 六、参考链接

- [国务院新闻办 · 五部门生猪产品信息数据平台](http://www.scio.gov.cn/gwyzclxcfh/cfh/2021n_16129/2021n07y23r/xgbdbj_16377/202208/t20220808_307249.html)
- [中国畜牧业协会猪业分会 · 每月全国生猪产品数据](https://pig.caaa.cn)（[示例：2026年3月](https://pig.caaa.cn/html/pig_rd/pig_hydt/2026/0427/2467.html)）
- [国家统计局数据平台](https://data.stats.gov.cn)
- [AKShare 现货数据文档](https://akshare.akfamily.xyz/data/spot/spot.html) · [AKShare 数据字典（生猪信息专区）](https://akshare.akfamily.xyz/data/index.html)
- [Tushare Pro](https://tushare.pro/document/2)
- [博亚和讯（日度生猪市场评论，备用人工源）](https://www.boyar.cn/)

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

- 7 张表：`sector_moneyflow_snapshots` / `dragon_tiger_entries` / `northbound_daily` / `block_trades` / `share_floats` / `stock_repurchases` / `announcements`；读取端点 Redis 缓存 TTL 300s；手动触发走 `POST /api/v1/tasks/fetch-market-data`（`market_data.fetch` 队列，9 类 payload 二选一）。
- 单位总原则：接三方行情先 curl 实测定字段与单位再写映射，消费端只做展示分档（详见 [best-practices](../references/best-practices.md)）。

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
