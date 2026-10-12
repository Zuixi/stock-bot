# 命名与口径字典（S0.4 交付物）

> **定位**：Wave 0 的**跨模块命名与边界字典**。上游契约是
> [`docs/specs/S0-platform-contracts.md` §S0.4](../specs/S0-platform-contracts.md#s04-命名与口径字典)（六节 §4.1–§4.6 的绑定内容与验收判据），
> 本文件是它的**落地交付物**——两者冲突时以 spec 为准。
>
> **冻结范围**：徽标语义 · `metric_key` 命名 · `publisher_tier` · 口径版本 · 模块边界 · 兜底规则。
> 这六项是后续 7 个 Wave 会反复引用的口径，**变更必须走 spec 修订**，不在本文件就地改。
>
> **与其它文档的边界**（避免同一决策两份权威，必有一份腐烂）：
> - **数据源事实**（来源 URL / 频率 / 历史深度 / 三态结论 / 实机调用记录）→ [`docs/design/data-source.md`](./data-source.md) §附.1–附.5
> - **指标注册表**（单位 / 频率 / 阈值 / 展示分组 / mock 基准）→ [`backend/app/services/industry_registry.py`](../../backend/app/services/industry_registry.py)
> - **为什么这么定**（候选与否决原因）→ [`docs/decisions/`](../decisions/) 的 ADR
> - 本文件只回答「**同一个词在不同模块里指的是不是同一件事**」

---

## §4.1 徽标语义：必须区分两种

原图 7 个入口徽标数字（29 / 5 / **18** / 16 / 3 / 9 / 31）**混用了两套语义**——「有数据」与「已做好体验」被当成同一个数。本节把两套语义、命名与逐数字归属钉死。

| 语义 | 命名（API 字段） | 定义 | 判定标准（可机械核对） |
|---|---|---|---|
| **已接入数** | `ingested_count` | 该入口下**已有真实数据可展示**的条目数 | 该条目在事实层存在 ≥1 条真实来源的行（非 mock、非占位）；**不要求**有策展摘要、对比视图或完整详情页 |
| **已策展数** | `curated_count` | 该入口下**已有策展产物**的条目数 | 该条目满足二者之一：① 在 `industry_registry` 中有**指标定义**（`MetricDef`：单位 + 频率 + 阈值 + 展示分组）② 已有**策展产物**（overlay 种子 / 策展摘要 / 行业页 / 榜单配置）。**唯一判定标准就是这两条**，不使用"完整体验""做得好不好"这类无法判定的表述 |

> **`curated_count` 的定义只在本表这一处**（这就是唯一定义）。下文任何地方提到"已策展"都指本行，不再另给定义。

**不变量**：数据健康的入口应满足 `curated_count ≤ ingested_count`。**若出现 `curated_count > ingested_count`**，意味着"有策展产物但事实层无数据"（如已配 registry 定义却没有真实序列）——这是**数据或配置错误**，记 open item，**不上徽标**（展示一个用户点进去看不到数据的数字，比不显示更糟）。

### 7 个数字的逐项归属

| # | 徽标数字 | 覆盖面 | 归属语义 | 判定依据 |
|---|---|---|---|---|
| 1 | 大宗商品 **29** | 29 个商品品类（能化 / 黑色 / 有色 / 农产品等） | `ingested_count` | 品类名录已在事实层存在、可逐条取到价格序列；尚无逐品类的策展摘要与对比视图 |
| 2 | 外汇市场 **5** | 5 个汇率品种（美元 / 欧元 / 日元 / 港元 / 英镑等） | `ingested_count` | 同上：有真实序列，无策展层 |
| 3 | 主要指数 **9** | 9 个指数（上证 / 深证 / 创业板 / 科创50 / 沪深300 / 中证500 / 恒生 / 标普500 / 纳斯达克综合等） | `ingested_count` | 行情事实层已覆盖，不依赖任何人工策展 |
| 4 | 申万一级 **31** | 申万一级行业 **31** 个（分类体系全集） | `ingested_count` | 分类体系本身全量落库（SCD2 归属表），属于"名录已接入" |
| 5 | 行业看板 **3** | 已有策展产物的行业工作台 | `curated_count` | **语义**已定（走上面 `curated_count` 的唯一定义）；**数值 3 是 owner 图上的标注值，尚未与数据侧核对**——registry 当前只注册 **2** 个**行业** scope，核对方法见下方"语义归类已定 vs 数值待核" |
| 6 | **行业分类（申万）16** | 申万一级中**已策展的行业**数 | **`curated_count`** | 与第 5 行 `行业看板 3` 同语义（同一钻取路径）；**语义**归类见下方"`行业分类（申万）16` 的归类裁定"，**数值 16 是 owner 标注值、尚未与数据侧核对**（registry 今日实测行业 scope = 2）|
| 7 | ~~**A股市场 18**~~ | —— | **删除（不再存在）** | 见下方 Ruling 4 |

### Ruling 4：`A股市场 18` 徽标**删除**

**裁定**：原图 `A股市场` 节点上的 `18` **不进入任何语义**，直接删除该徽标；`A股市场` 入口不显示数字（或显示展开后子节点各自的数字）。

**理由（三条，任一条已足以否决）**：

1. **对不上**：`18` 与该节点**展开后的 5 个子节点**、与同级 `主要指数 9`、与 `行业分类（申万）16` **均对不上**——它不是任何一个可见集合的基数。
2. **无语义**：owner 未给出 `18` 的口径（是 18 个页面？18 个数据项？18 个交易日？），**不允许以"大概是个数"存在**——徽标是数量承诺，用户会按它核对。
3. **不可验收**：两套既有语义各有可机械核对的判定标准（见上表），而 `18` 挂不上任何一条 → 它永远无法被验收，也永远无法被维护（下个月该改成几？无人能答）。

> **不作为替代**：删除**不是**把它改成 `ingested_count` 或 `curated_count`。改归类等于替 owner 发明一个语义，同样不可验收；等 owner 给出**明确口径**后再单独加回。

### `行业分类（申万）16` 的归类裁定：`curated_count`

> 本表裁定的是**语义归类**（`16` 归哪一套语义），**不是**声称 `16` 这个数值已被核实——数值状态见本表最后一行与紧随其后的"语义归类已定 vs 数值待核"。

| 项 | 内容 |
|---|---|
| **归类** | `curated_count`（已策展数，定义见上表**唯一一处**） |
| **它数的不是** | **不是**申万一级的**分类总数**——分类总数是 `申万一级 31`（上表第 4 行，归 `ingested_count`）|
| **它数的是** | 申万一级行业里**已有策展产物**（`industry_registry` 指标定义 / 行业页 / 榜单配置）的行业数 |
| **依据 1（语义自洽）** | 与上表第 5 行 `行业看板 3` 同源同义：`行业分类（申万）→ 逐行业` 是同一个钻取路径，`16` 与 `3` 只能同属一套语义；否则同一个入口的两个数字互不可比 |
| **依据 2（与 spec 落位一致）** | spec §4.1 已把 `行业分类 16` 排在 `已策展数` 一列（原文标注"待 owner 复核归类"，本行即完成**语义**复核）；`ingested_count` 一列是 29 / 5 / 9 / 31 |
| **依据 3（可验收：核对方法）** | 核对方法 = **数 `industry_registry.get_industry_scopes()` 的长度**（判据 = `IndustryConfig.is_industry`，即 `sw_l3_codes` 非空的 **行业** scope）。**今日实测 = 2**（`pig` / `broiler`）→ **与图上的 `16` 不符**，故 `16` 只作 owner 标注值、**待数据侧复核**（不在本任务裁决范围）。**不许**改数 `INDUSTRIES` 的 key 数：`INDUSTRIES` 现在同时登记非行业 scope（`market` = 全市场情绪，spec §S1.8），key 数 = 3，把全市场数成一个"行业"会让分母虚高（Ruling 54 第 4 条）|
| **数值状态** | 图上的 `16` **尚未与数据侧核对**（当前 registry 实际注册 2 个**行业** scope / 3 个 scope）。**不作为已核实事实使用**；UI 上架前必须先核对 |
| **因此不许** | ① 不允许把 `16` 读成"16 个行业有数据"（那是 `ingested_count` 的口径，且与 `31` 冲突）② **不允许把未经核对的 `16` 写成已核实事实** ③ **同一入口同时展示两个语义时必须带标签**（如「已策展 16 / 已接入 31」），裸数字不再出现 |

**语义归类已定 vs 数值待核（必须显式分开，不许混写）**：

| 徽标 | 语义归类（本次裁定，已定） | 数值（owner 图上标注，**待核**） | 核对方法 |
|---|---|---|---|
| `行业分类（申万）16` | `curated_count` | **16 —— 未与数据侧核对** | 数 `get_industry_scopes()` 的长度（今日 = **2**，与 16 不符 → 待复核）|
| `行业看板 3` | `curated_count` | **3 —— 未与数据侧核对** | 同上（今日 = **2**）|

**可复跑的核对命令**（"今日 = 2"的出处）：

```bash
cd backend && UV_CACHE_DIR=/tmp/uv-cache uv run python -c \
  "from app.services.industry_registry import INDUSTRIES, get_industry_scopes; \
   print(len(INDUSTRIES), list(INDUSTRIES), '->', len(get_industry_scopes()), [c.key for c in get_industry_scopes()])"
# → 3 ['pig', 'broiler', 'market'] -> 2 ['pig', 'broiler']
```

> 两个数**都要看**：`INDUSTRIES` 的 key 数是 **scope 数**（3，含全市场情绪 `market`），
> `get_industry_scopes()` 才是**行业数**（2）。徽标的「行业分类（申万）」分母是后者。

> **口径纪律**：本文件只负责把**语义**钉死；**数值**必须由数据侧核对后才可写成事实。凡本文件出现"今日 = N"的写法，都是**截至编写时可复跑的实测值**（附核对命令），不是定格口径——数值变化时改数据侧或徽标，不改本节的语义裁定。

---

## §4.2 `metric_key` 命名规则

`metric_key` 是**指标的唯一业务键**，跨表、跨 API、跨前端共用。命名对齐 [`docs/design/data-source.md`](./data-source.md) 与 [`industry_registry.py`](../../backend/app/services/industry_registry.py)（同一命名空间）。

### 规则（5 条）

| # | 规则 | 正例 | 反例与原因 |
|---|---|---|---|
| 1 | **全小写 + 下划线**，不含大写、空格、连字符 | `hog_price` · `hog_corn_ratio` · `sow_inventory` | `HogPrice` / `hog-price` / `hog price` —— 大小写与分隔符混用会让 `grep` 与前端映射双双失效 |
| 2 | **语义完整，不自造缩写** | `sow_inventory`（能繁母猪存栏）· `idx_turnover_ratio_sse`（上证换手率） | `sow_inv` / `turnover_ratio` —— 缩写不可检索；省略"哪个市场的换手率"会产生歧义键 |
| 3 | **单位与频率随 key 在 registry 固定**，**不在展示层换算** | `piglet_price_15kg`：`unit="元/kg"` / `freq="weekly"` 写在 `MetricDef`（本文件只**转录** registry，改单位 = 改 registry） | 前端 `value * 100` 换单位 —— 同一 key 两种量纲，序列被污染且不可回溯 |
| 4 | **与既有 `industry_metrics.metric_key` 同一命名空间** | 新 key 与 `hog_price` 同级注册，不另建 `market_metric_key` 之类平行空间 | 另起命名空间 → 需要第二张指标表（ADR-E 已禁止）与两套前端映射 |
| 5 | **同一口径一个 key**（口径不同则必须分 key） | 央行 OMO **操作利率** `reverse_repo_7d_rate` 与市场侧 **成交** 利率 `fr007` / `fdr007` 各占一个 key | 把操作利率与成交加权利率塞进同一 key —— 两套口径的数值混成一条序列，事后无法拆分 |

### 真实命名样例（照此风格新增）

> **本表的单位与频率逐字抄自 `industry_registry.py` 的 `MetricDef`**（这就是规则 3 的落地：unit/freq 固定在 registry，本文件只是**转录**）。若本表与 registry 不一致，**以 registry 为准**，改本表——改完请跑随附核对脚本（见下），不要靠眼看。

**已存在（代码中真实注册的 key，`industry_registry.INDUSTRIES[].metrics` 可直接引用）**：

| `metric_key` | registry `name` | 单位 `unit` | 频率 `freq` |
|---|---|---|---|
| `hog_price` | 生猪均价 | 元/kg | daily |
| `corn_price` | 玉米价格 | 元/kg | daily |
| `piglet_price_15kg` | 仔猪价格（15kg） | 元/kg | weekly |
| `hog_corn_ratio` | 猪粮比 | （**空串** = 无量纲比值） | daily |
| `sow_inventory` | 能繁母猪存栏 | 万头 | monthly |
| `company.hogs_sold_monthly` | 月度出栏量 | 万头 | monthly |
| `company.cost_complete` | 完全成本 | 元/kg | quarterly |
| `mcap_per_head` | 头均市值 | 元/头 | monthly |

> 上表 `unit` 列的"（空串 = 无量纲比值）"对应 registry 里的 `unit=""`（`hog_corn_ratio` 是纯比值，没有量纲单位）——**这是 registry 的原值，不是省略**。

**已存在（情绪 · 市场宽度与动量，scope = `market`；spec §S1.8，2026-10-12 落地）**：

| `metric_key` | registry `name` | 单位 `unit` | 频率 `freq` |
|---|---|---|---|
| `advance_decline_ratio` | 涨跌比 | （**空串** = 无量纲比值） | daily |
| `limit_up_down_ratio` | 涨跌停比 | （**空串** = 无量纲比值） | daily |
| `above_ma20_ratio` | 站上 20 日均线占比 | % | daily |
| `limit_up_break_ratio` | 炸板率 | % | daily |
| `max_consecutive_limit_up` | 连板高度 | 板 | daily |
| `first_board_promotion_ratio` | 首板晋级率 | % | daily |
| `market_advance_count` | 上涨家数 | 家 | daily |
| `market_decline_count` | 下跌家数 | 家 | daily |

> 这 8 个 key 与上表**同属一个命名空间**（规则 4），落进**同一张** `industry_metrics`；
> `industry_key='market'` 是通用 scope 键的第一个非行业取值（见 spec §S1.2 的
> `industry_key` 泛化备注），读面复用 `GET /api/v1/metrics/*`，**没有第二张指标表**。
> `market_advance_count` / `market_decline_count` 是 `advance_decline_ratio` 的分子分母，
> 之所以**也注册成指标**：spec §S1.8 的行为判据要求"市场 `A股涨跌分布` 与情绪
> `涨跌家数` 同源同值"，而分母为 0 的日子无法从 ratio 反推家数——家数必须自己可查。
>
> **PIT**：这 8 个 key 的每行 `published_at` 恒为"该交易日 15:00 Asia/Shanghai"
> （`market_sentiment_service.published_at_for`），读面的 `as_of_source` 因此恒为
> `published_at`，**不回退** `period`（ADR 0011 / Ruling 34-35）。
>
> **口径版本**：`caliber_version = 's1.8-v2'`（fix round 1 从 v1 bump）。v1 把
> `pct_chg IS NULL` 的行按市场分布的 `None → 0~1%` 行为记成"上涨"，v2 在分桶前剔除
> NULL 行并加覆盖率门槛；两版的行**不可混用**（ADR 0011 第 4 条）。
>
> **涨跌类指标的双口径（Ruling 54.1，必须可查）**：`market_advance_count` /
> `market_decline_count` / `advance_decline_ratio` 的**主值 = 市场 `A股涨跌分布` 的
> 桶之和**（`up` = `0~1%`/`1~3%`/`3~5%`/`>5%`/`涨停` 之和，`down` = 六个负数桶之和），
> 与市场页逐桶可对拍；`extra.sign_basis='market_distribution_buckets'` 记录该基准。
> 桶口径里 **`0~1%` 含 `pct_chg == 0` 的平盘**，故与符号口径（`pct_chg > 0`）之差 =
> 平盘家数，落 `extra.flat_in_positive_bucket`（恒等式
> `up_count - up_sign_count == flat_in_positive_bucket` 有测试锁定）。
> **`pct_chg IS NULL` 的行在分桶前剔除**（Ruling 55）：它们**不计涨也不计跌**，
> 家数落 `extra.unavailable_pct_chg`；可用覆盖率 `extra.coverage` 低于
> `extra.min_coverage`（当前 `0.95`）时，三个指标整组 `value = NULL` +
> `missing_reason='partial_pct_chg'`，**不落任何由缺失数据拼出的数字**。
> `above_ma20_ratio` 的 `extra` 另带 `max_window_span_days` / `max_gap_days` /
> `span_over_alert_days`（20 行窗口最坏的日历跨度、最大相邻间隔、跨度超
> `span_alert_days`=40 天的合格标的家数：窗口是「20 行」而非「20 个连续交易日」）。
>
> **A/B/C 归类（ADR 0014）**：8 个全属 **B 类**（只用当日点内事实，可回算、允许回补，
> 带 20 日窗口完备守卫）。无 A 类、无 C 类指标。

**已注册（交易活跃度 · 杠杆资金 · 估值与风险溢价，scope = `market`；spec §S1.9 + §S1.11，2026-10-12 落地）**：

> 单位 / 频率逐字抄自 `industry_registry.MARKET_ACTIVITY_METRICS`（本表只是转录）。
> 13 个 key **同属一个命名空间**（规则 4）、落**同一张** `industry_metrics`、
> `industry_key='market'`（与上面 §S1.8 的 8 个**同一个 scope**、同一个读面）。
> `pe_csi300` 是 `erp_10y` 的**分子底座**（也注册、也落库），不计入 spec 承诺的 13 个。

> 本表**刻意只有 4 列**（与上面的样例表同形）：`check_metric_dictionary.py` 用
> "5 列表"识别「新增提案」表，给本表加第 5 列（如展示分组）会让它被误判成提案表
> （已注册 key 出现在提案表 = 失败）。分组信息看 `MetricDef.group`。

| `metric_key` | registry `name` | 单位 `unit` | 频率 `freq` |
|---|---|---|---|
| `market_turnover_amount` | 全市场成交额 | 亿元 | daily |
| `idx_turnover_ratio_sse` | 上证指数换手率 | % | daily |
| `idx_turnover_ratio_szse` | 深证成指换手率 | % | daily |
| `idx_turnover_ratio_bse50` | 北证50换手率 | % | daily |
| `idx_turnover_ratio_star` | 科创综指换手率 | % | daily |
| `idx_turnover_ratio_chinext` | 创业板指换手率 | % | daily |
| `idx_turnover_ratio_sse50` | 上证50换手率 | % | daily |
| `idx_turnover_ratio_csi300` | 沪深300换手率 | % | daily |
| `margin_financing_balance` | 融资余额 | 亿元 | daily |
| `margin_financing_buy_ratio` | 融资买入额占比 | % | daily |
| `margin_short_balance` | 融券余额 | 亿元 | daily |
| `cn_treasury_yield_10y` | 10年期国债收益率 | % | daily |
| `erp_10y` | 股债收益差（10年期） | % | daily |
| `pe_csi300` | 沪深300市盈率（整体法） | 倍 | daily |

> **换手率口径（spec §S1.9 验收判据：口径必须可查）**：7 个换手率**一律自由流通股本口径**。
> 5 个直取指数取 TuShare `index_dailybasic.turnover_rate_f`（上游自身定义 = 成交量 /
> 自由流通股本 `free_share`，**不做二次换算**）；北证50 与科创综指因
> `index_dailybasic` **不覆盖**（实测返回 0 行且不报错）改**自建** = 成分股
> `daily_basic.turnover_rate_f` 按 `index_weight.weight` 的权重加权平均。
> 总股本口径 `turnover_rate` **不落库**，只在 `extra.turnover_rate_total` 留痕供对账。
> 自建行的 `extra` 恒带 `caliber='free_float_weighted'`、`weight_period`、
> `member_coverage`、`weight_coverage`、`attribution='current_index_weight_snapshot'`。
>
> **`erp_10y` 的 PE 口径与指数（spec §S1.11 验收判据：两者都必须在 UI 可查）**：
> **整体法**（指数总市值 / 指数口径净利润，**不是成分中位数**）、**指数 = 沪深300
> （`000300.SH`）**，取 TuShare `index_dailybasic.pe`。公式
> `erp_10y = 1/PE × 100 − cn_treasury_yield_10y`（单位：百分点）。
> PE 与收益率都落 `extra`（`pe` / `earnings_yield_pct` / `treasury_yield`），可逐日手算。
> **PE 单独落 `pe_csi300`**，让 `1/PE` 这一半能从库里独立复原。
>
> **§S1.9 的杠杆资金口径**：`margin_financing_balance` / `margin_short_balance` 是交易所
> 汇总口径（TuShare `margin`）**三所求和**（SSE + SZSE + BSE），原生单位**元**、
> 落库换算**亿元**，逐所拆分落 `extra.by_exchange_yuan`；
> `margin_financing_buy_ratio` = 融资买入额 / **当日全市场成交额**（`daily_quotes.amount`
> 合计，亿元）× 100，分母缺失或为 0 → NULL。
>
> **PIT（ADR 0011 / Ruling 34-35）**：`published_at` 按**产地各自的可得出处**分三档
> （不是一律 15:00，也不是回退 `period`）——行情类与 `erp_10y` 当日 **15:00**、
> 三个 `margin_*` 当日 **18:00**（交易所收盘后清算发布）、`cn_treasury_yield_10y`
> 当日 **19:00**（银行间收市后中债/东财发布）。均带 `Asia/Shanghai` 时区。
>
> **口径版本**：`caliber_version = 's1.9s1.11-v1'`。
>
> **A/B/C 归类（ADR 0014）**：13 个全属 **B 类**（只用当日点内事实，可回算、允许回补）。
> 自建的两个换手率**含 C 类成分权重快照**，但只作**归属**用且**字段级标注**
> （`attribution='current_index_weight_snapshot'`，权重取该日或之前最近一期，**不看未来**）
> —— 符合 ADR 0014 第②③条：禁止用当前快照回算"成分本身"，允许用行情事实按成分聚合。
> 无 A 类指标（本批次不消费"只给当下"的盘中明细）。
>
> **Step-0 实机结论 / 未验证余项**见 [`data-source.md` 附.7](./data-source.md#附7-s19--s111-段交易活跃度--杠杆资金--估值与风险溢价的数据源复核2026-10-12)。
> **本批次没有新增/改动的 `metric_key` 提案**：`erp_10y` 与 `idx_turnover_ratio_sse`
> 已从下方"新增提案"表**移入本表**（它们现在有代码可核）。

**已注册因子（`factor_key` · spec §S2.1；登记地 = `industry_registry.FACTORS`）**：

> **因子不是指标**：`metric_key` 描述**一条序列**，`factor_key` 描述**一次变换**
> （`(序列, window, params) → 序列` 的纯函数）。两者**共用同一命名空间且不得撞名**
> （撞名会让 `grep` 与前端映射双双失效，`validate_factors` + 单测机械锁定），
> 但**登记处不同**：指标在 `INDUSTRIES[].metrics`，因子在 `FACTORS`。
> 本表是 `FACTORS` 的逐字转录（名称 / 单位 / 窗口 `window`），
> `backend/tests/test_indicators_registry.py::TestMetricDictionarySync` 机械比对。

| `factor_key` | registry `name` | 单位 `unit` | 窗口 `window` | 分组 · 存储 |
|---|---|---|---|---|
| `pct_rank` | 滚动分位 | — | 252 | 分位与估值位 · 读时算 |
| `zscore_20d` | 20 日 z-score | — | 20 | 分位与估值位 · 读时算 |
| `yoy` | 同比 | — | 12 | 动量与变化 · 读时算 |
| `mom` | 环比 | — | 1 | 动量与变化 · 读时算 |
| `ma_20d` | 20 日均线 | — | 20 | 趋势与均线 · 读时算 |
| `volatility_60d` | 60 日年化波动率 | — | 60 | 风险与波动 · 读时算 |
| `drawdown` | 逐点回撤 | — | — | 风险与波动 · 读时算 |
| `trend_slope_60d` | 60 日趋势斜率 | — | 60 | 趋势与均线 · 读时算 |
| `ma_bullish` | 均线多头排列 | — | 60 | 趋势与均线 · 读时算 |
| `consecutive_negative` | 连续为负期数 | 期 | — | 动量与变化 · 读时算 |
| `return_percentile` | 收益分位 | — | 60 | 分位与估值位 · **物化** `stock_features.return_percentile` |
| `max_drawdown` | 最大回撤 | — | — | 风险与波动 · **物化** `stock_features.max_drawdown` |
| `trend_slope` | 趋势斜率 | — | 60 | 趋势与均线 · **物化** `stock_features.trend_slope` |
| `annual_volatility` | 年化波动率 | — | 60 | 风险与波动 · **物化** `stock_features.annual_volatility` |

> **单位列的「—」**：表示该因子**无量纲**（比值 / 分位 / 布尔 / 标准化值）——
> registry 里就是 `unit=""`。`ma_20d` 的单位随输入序列（元/kg、万股、元…），
> **有意**登记为无量纲：写死一个量纲，同一因子用在成交量上时单位就成了谎言。
> 唯一例外是 `consecutive_negative`（单位「期」）。
> 本表**不用**指标表那句「（**空串** = 无量纲比值）」的写法——那句话是
> `check_metric_dictionary.py` 的 `EMPTY_UNIT_DOC` 常量，只对**指标** 4 列表生效。
>
> **窗口列的「—」**：`drawdown`、`max_drawdown`、`consecutive_negative` **无窗口参数**
> —— registry 里 `window=None`。三者是**全序列口径**的纯函数（回撤算的是"历史最高
> 到当前"的谷底、连续为负数是尾部计数，都与窗口无关）；物化/读面要裁剪到 N 日时由
> **调用方**传窗口（`max_drawdown` 即由 `indicators.stock_feature_row` 按
> `stock_features.window_days` 裁剪，见 `window_param` 的说明）。
>
> **窗口与口径参数**：其余因子的 `window` 值必须与函数签名默认值 **逐字一致**
> （口径只住 registry 一处，`test_declared_window_matches_function_default` 锁定）；
> 除窗口以外的口径参数（如年化波动率的 `periods_per_year=252`、趋势斜率的
> `normalize=True`）登记在 `FactorDef.params`，由
> `test_declared_params_match_function_defaults` 锁定——**口径参数不得只活在函数默认值里**。
>
> **存储（读时算 vs 物化）**：低成本因子**不落库**，读时算 + Redis 缓存；
> 只有昂贵的（长窗口、跨全市场、逐标的 × 逐 `asof_date`）才物化。4 个物化因子的
> `materialized_column` **必须是 `stock_features` 的既有列**——本 spec 不新增表、
> 不新增列（`test_materialized_factors_name_existing_columns` 把这条与 ORM 模型机械绑定）。
> 长窗口因子（如 1260 日情绪分位）走既有 `stock_features.extra`（JSONB），**无需迁移**。
>
> **量纲约定（读时算因子）**：变化率类（`yoy` / `mom`）返回**比值**（0.05 = +5%），
> 不是百分数；分位类返回 **0–1**；波动率返回**比值**（0.28 = 年化 28%）；
> 回撤类返回 **负数**（`-0.32` = 最大跌 32%，与逐点 `drawdown` 同号）。
> 这些约定写在 `indicators.py` 的模块 docstring 与各函数 docstring 里并**被单测锁定**
> ——口径必须在实现处显式声明，不能靠消费方猜。

**手动检查脚本（未接入门禁；接入方案见 deferred）**（§4.2 规则 3 的机械验证；本文件上表 = registry 的转录，此脚本专治"抄错"）：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --project backend python backend/scripts/check_metric_dictionary.py
```

脚本逐行打印 `文档 name/unit/freq` vs `registry name/unit/freq` 的对比，并额外查两件事：**反查**（已注册的 key 不得被错列进下方"新增提案"表）与**覆盖面**（样例表覆盖了几个已注册 key）。任一条不满足即非 0 退出。

> **声明降级（勿过度声称）**：本脚本**尚未接入任何门禁**——它不在 `scripts/self_review.sh`、pre-commit 或 CI 的自动检查面内（放在 `backend/scripts/` 只保证被 ruff **lint/format** 覆盖，不保证被**执行**）。把 CI 的 ruff 面 / 执行面扩到该目录属**独立变更**（扩面可能暴露该目录既有未格式化文件），故记为 **deferred**，不由本文件声称"已是门禁"。

**已实测**（2026-10-12）：`核对 8 行，MISMATCH 0 行 → ✔ 全绿`。改本表后**必须手动重跑**，不要靠眼看。

**本文件落册的新增提案**（**尚未注册**，命名按上表 5 条规则；接源见 `data-source.md` 附.1，注册动作在 Wave 1）：

| `metric_key` | 含义 | 单位 | 频率 | 备注 |
|---|---|---|---|---|
| `etf_share_change` | 宽基 ETF 份额变化 | 份 | 日 | 提案见 spec §S1.7（**不在 registry 中**）|
| `southbound_net_buy` | 南向资金净买入 | 元 | 日 | 提案见 spec §S1.7（**不在 registry 中**）|
| `industrial_capital_net_change` | 产业资本净增减持 | 元 | 事件驱动 | 提案见 spec §S1.7，依赖 S3.2（**不在 registry 中**）|
| `reverse_repo_7d_rate` | 7 天期逆回购操作利率（政策利率锚） | % | 日 | 与市场侧回购利率分 key（规则 5）|
| `fr007` | 银行间 7 天回购定盘利率 | % | 日 | 市场侧成交口径 |
| `fdr007` | 银银间 7 天回购定盘利率 | % | 日 | 市场侧成交口径 |
| `northbound_holdings_quarterly` | 陆股通持仓（季度，香港中央结算汇总账户） | 股 | 季度 | **禁止**命名为 `foreign_*`（不得读作"外资"，见 `data-source.md` 附.1②）|

> **两张表的区别**：上表是**已注册事实**（有代码可核）；本表是**命名提案**（无代码可核，只有 spec 出处）。**引用时必须分开**——把提案 key 写成"既有 key"会让实现者去找一个不存在的 `MetricDef`。

**历史遗留与例外（明说不许复制）**：`industry_registry.py` 的公司级指标键 `company.hogs_sold_monthly` / `company.cost_complete` **在实现里含 `.`**，是既有兼容键（已入库 `industry_metrics`，行数与测试引用 `backend/tests/test_industry_companies.py` 均在），**不在本次改名范围**（改名要动数据 + 迁移 + 测试，收益为 0）。**新键一律按规则 1 只用下划线**，不得以"既有键带点"为由新增带点键。

> **注册地唯一**：新增指标 = 在 `industry_registry.py` 加一条 `MetricDef`（单位 / 频率 / 阈值 / 分组同处声明），**不建新表、不在别处再写一份定义**（ADR-E、ADR 0013）。

---

## §4.3 `publisher_tier`（发布主体可信度分层）

### 定义与取值

| tier | 含义 | 例 |
|---|---|---|
| `legal` | **法定披露** | 上市公司公告、定期报告、问询函、招股书 |
| `official` | **官方 / 政府 / 交易所** | 国务院、部委、地方政府、监管处罚、统计局、央行、交易所 |
| `sellside` | **卖方研究** | 券商研报、盈利预测、评级 |
| `media` | **媒体与舆情** | 财经快讯、搜索热度、股吧人气 |

排序（资讯列表默认顺序或分组）：`legal > official > sellside > media`。

**它回答的问题**：**这是谁发布的** —— 用来判断一条**内容/资讯**的可信度与排序优先级。

### ★ 与既有 `industry_metrics.source_tier` 的区别（**两者语义不同，不得混用**）

| | `publisher_tier`（本节新增） | `industry_metrics.source_tier`（既有，已被占用） |
|---|---|---|
| **回答** | **谁发布的**（Who publishes） | **指标怎么来的**（How was it produced） |
| **取值** | `legal` / `official` / `sellside` / `media` | `official` / `highfreq` / `calc` / `manual` / `derived` |
| **取值定义处** | 本文件 §4.3 | [`industry_registry.py`](../../backend/app/services/industry_registry.py) 的 `TIER_*` 常量 + `backend/app/models/industry_research.py:59` 列注释 |
| **取值中文标签** | 见上表 | 官方基准 / 高频参考 / 测算 / 人工录入 / 派生（`TIER_LABELS`）|
| **作用对象** | **资讯 / 内容**（谁发的） | **指标行**（怎么算的 / 哪个源） |
| **用于** | 资讯列表排序与分组、抽取可信度裁决 | 指标徽章"官方基准/高频参考/测算"、权威性裁决 |

**为什么会撞名**：`official` 在两层里**都出现**，但一层是"官方发布的**内容**"，另一层是"官方口径的**指标源**"。**同名不同义会让 `grep` 与 code review 双双失效**（搜 `official` 会同时命中两种语义，评审时极易误判），故本层**改用 `publisher_tier`**，不复用 `source_tier`。

**照此对照（避免误判的判例）**：

| 实例 | `publisher_tier` | `source_tier` | 说明 |
|---|---|---|---|
| 农业农村部月度生猪存栏指标 | `official`（农业农村部发布） | `official`（官方基准源） | 两层同为 `official`，**恰好同值**，但问题不同 |
| 上市公司月度出栏量（销售简报 PDF） | `legal`（法定披露） | `manual`（人工录入） | **典型不同值**：发布主体是法定披露，录入方式是人工 |
| 券商研报里的盈利预测 | `sellside` | `manual` | 卖方发布、人工录入 |
| 猪粮比（由玉米价与猪价计算） | —— （非发布物，无发布主体） | `derived` | **`publisher_tier` 不适用**：计算派生项没有发布主体 |
| 龙虎榜 / 大宗交易 | `official`（交易所口径） | `official` | 见 §4.5 ③：资讯是原料 |

**禁用**：新增发布主体分层**不得**命名 `source_tier`；新增"指标怎么来的"取值**不得**写进 `publisher_tier`。两者各自独立演进。

---

## §4.4 指标口径版本（`caliber_version` + 断点标记）

### 规则

任何**上游统计口径发生修订**的指标，其序列上必须**同时**带两样东西：

1. **`caliber_version`**：口径版本标识。**落在 `industry_metrics.caliber_version`，该列当前不存在、待加**（Wave 1 / `T1.2`，`plans/2026-10-12-investment-research-platform-ia.md` 仍未勾选）。列的新增依据是 **ADR 0013 的附带裁定**与 `docs/specs/S0-platform-contracts.md` §S0.1 的 `alter table industry_metrics add column caliber_version text`（**以"待应用"呈现，不是既成事实**）。本文件只约定**该列一旦加上后的口径纪律**，不声称它已存在
2. **断点标记**：在修订生效时点显式标注，**禁止**把断点前后拼成一条连续序列、**禁止**静默回填

并遵守 PIT 契约（ADR 0011）：口径修订的**实际可得时点**要落 `published_at`，不得用修订后的口径回填修订前的历史。

### 硬性约束

| # | 约束 | 原因 |
|---|---|---|
| 1 | 同一 `metric_key` 的不同口径**必须可分**：`caliber_version` 非空且取值可区分 | 否则回测与图表会把两套口径当一条序列 |
| 2 | 断点前序列**不重算、不回填** | 回填 = 未来函数，摧毁回测可信度（ADR 0011）|
| 3 | 展示层遇到口径断点**必须给视觉标记**（如序列断开 + tooltip 注明口径与生效日） | 一条看似连续的线会让用户把断点读成"数据波动" |
| 4 | 修订须留**官方出处**（公告/文件 URL）于 `data-source.md` | 口径修订是事实主张，无出处的版本号不可复核 |

### 已知案例：M1（狭义货币）

| 项 | 内容 |
|---|---|
| 指标 | M1 同比/余额（宏观 · 货币） |
| 修订时点 | **2024-12 修订**，**2025-01 起**按新口径统计 |
| 口径变化 | 修订后的 M1 = 流通中货币（M0）+ 单位活期存款 + **个人活期存款** + **非银行支付机构客户备付金** |
| 处理 | 序列在 2025-01 处标断点；断点前保留原口径值、**不重算**；带 `caliber_version` 区分新旧 |
| 官方出处 | [央行公告 2024 年第 17 号](https://www.gov.cn/zhengce/zhengceku/202412/content_6990650.htm)（逐字见 [`data-source.md` 附.2 #19](./data-source.md)）|

> **判定提示**：口径修订不限于"指标定义变了"。**统计范围变化**（纳入口径扩大/缩小）、**发布主体更换**（如中国结算口径 → 上交所口径）、**频率变化**（日频 → 季度）**都算**口径修订，都必须留版本与断点。北向资金 2024-08-19 由每日净买入改为季度持股披露即属此类（见 `data-source.md` 附.1①）。

---

## §4.5 模块边界裁定（5 处重叠）

原图 10 张图之间同一批数据被多处消费（"原料/视图分离"ADR 0009 的直接动因）。逐处裁定如下——**裁定的是"谁拥有口径与计算"，不是"谁显示"**。

| # | 重叠 | 裁定 | 落地约束 |
|---|---|---|---|
| ① | 市场 `A股涨跌分布` ↔ 情绪 `市场宽度与动量` | **口径与计算归情绪**；市场只做**只读快照** | 市场页的涨跌分布**引用情绪 API**，不在市场模块实现第二份阈值/分箱逻辑；市场侧不含任何口径参数 |
| ② | 市场 `行业主力净流入` ↔ 情绪 `资金流向` | **计算归一处**，按**维度**取数 | 同一份计算按 `scope` 取数：行业级 / 全市场级是**同一实现的两种维度**，不是两份实现。禁止两边各算一遍再做"对账" |
| ③ | `龙虎榜数据` / `大宗交易` | **资讯是原料**（`publisher_tier=official`，交易所口径），**情绪可作视图** | 事实由资讯模块采集落库（原料/视图分离 ADR 0009）；情绪模块**只读**做视图，不自行采集、不另存一份 |
| ④ | 价值网 `价值分配` × `利润分配流向图` | **合并为一处** | 同一个可视化（各环节利润占比）在两个入口出现 → 合并为**一个模块、一处实现、一个数据源**，另一个入口只做跳转/引用，不保留第二份实现 |
| ⑤ | 交易执行 `标的分类` 批注 | 批注说的**实为关注池**（经行业分析筛选、带理由），不是资产类型 → **拆为 `关注池` + `资产类型`** | `关注池`：用户维度的多态关注（ADR 0010，含 `stage` / `source_module` / `reason`）；`资产类型`：标的的客观属性（股票/ETF/可转债…）。**两者不得再混在一个字段里** |

**共性判据**（后续遇到新的重叠照此三分）：先问 ① 谁拥有**口径**（阈值、分箱、公式）② 谁拥有**计算**③ 其余都是**视图**。口径与计算**各只有一个所有者**，视图可以任意多。

---

## §4.6 「其他分类」兜底规则（原图的日期是**数据事实**，不是业务规则）

### 先纠正一处归因

原图写"将 **2026年5月** 以后上市的公司放在此处"。**这个日期不是 owner 拍脑袋的业务规则，而是引用了一条既有的代码事实**：

> `backend/app/models/concept.py:3` —— *"`stocks` 名录会滞后（**实测冻结在 2026-05-08**，66 只新上市股票缺行），若以 stock_id 为键，名录滞后会静默丢掉成分股"*

**同一个日期**。owner 描述的是真实的**数据陈旧度**（券商名录未跟上新上市），不是"上市公司按上市时间分类"这种业务规则。

**因此**：兜底规则**不得**写成"上市时间晚于某日 → 归入其他分类"（那是把数据缺陷固化成业务语义，且日期会腐烂）；也**不得**把 `2026年5月` / `2026-05-08` 写进任何规则表达式。上面这段是**引用既有代码事实**，不是规则定义。

### 兜底规则（**条件表达式**，非硬编码日期）

```
兜底条件：该实体在目标分类体系中无有效归类
处理顺序：① 可自动归类      → 自动归入
          ② 不可自动归类    → 外键留空 + 计数外露（不阻塞采集、不静默丢数据）
          ③ 人工覆盖（overlay 种子）优先于自动结果
```

**规则本身不含任何日期常量**：条件只依赖"**是否存在有效归类**"这一可判定谓词。今天、明年、新上市 200 家，表达式都不变——变的只是 `unresolved_count` 的数值。

### 复用既有模式（不新造机制）

该模式**仓库里已经跑了**（`backend/app/models/concept.py:3-5` 业务键与可空外键的列定义 + 缺失量的 API/schema 聚合层），新分类场景**照抄**：

| 要素 | 既有实现 | 新场景照抄 |
|---|---|---|
| **业务键取稳定标识** | `ConceptMember.symbol`（不取 `stock_id`，`concept.py:64`）| 一律用稳定业务键，**不用**会被名录滞后影响的代理键 |
| **外键降级为可空解析列** | `ConceptMember.stock_id` 是**可空整数列**（`concept.py:66`，非 NOT NULL）| 解析不到就留空，**不阻塞采集**、不丢弃整行 |
| **缺失量外露** | `unresolved_count` —— **不是** `ConceptMember` 的列，而是 **API/schema 聚合**：`schemas/concept.py` 的响应字段 + `repositories/concept_repo.py` 的 `count(*) FILTER (WHERE cm.stock_id IS NULL)` | 同样以 `unresolved_count` 计数外露，**不静默丢数据** |
| **人工覆盖优先** | 人工策展走 overlay 种子（[ADR 0005](../decisions/0005-overlay-seed-files.md)） | 人工结果**永久优先于**自动结果，重跑采集不得覆盖人工值 |

**四条底线**（缺一条即视为实现错误）：

1. 名录滞后**不得**导致整条记录丢失（丢行是最严重的静默失效）
2. 缺失**必须**可观测（`unresolved_count` 有非零值就要能看见）
3. 采集**不得**因未命中而失败或阻塞其他行
4. 人工覆盖**不得**被自动重跑冲掉

### 明确**不建**：服务 4 个消费方的通用人工裁定队列

**不建**"服务 4 个消费方（概念板块 / 产业链环节归属 / 研报有效性 / 行业分类）的通用人工裁定队列"。

理由：现在建就是**为不存在的需求写框架**。当前每个消费方各有自己的兜底模式（本节的"稳定键 + 可空外键 + 计数外露"）已经够用；等到**真的出现第 2 个需要同一队列的消费方**、且能写出它的状态机与权限模型时再抽象（那是 **Wave 3+** 的事，不属于 Wave 0）。

**本条只保证**：每个分类场景能用上面的条件表达式独立兜底，且缺失量可观测。**不保证**提供统一裁定 UI / 状态机 / 审计表。
