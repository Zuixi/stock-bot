# 概念板块成分 + 次新股追踪 实施计划（Concept Boards & New Stocks）

> 状态：**计划入库，未实施**（2026-09-18 制定）。
> 决策已定：数据源**东财为主**（同花顺兜底明确不做，容器缺 libstdc++/py_mini_racer，见 §0.3）；历史口径**只做"当日/向前"**（选 1，不做历史成分回算）；次新股口径**以东财 `BK0501` 概念成分为准**，不自建时间窗。
> 本文同时是本特性的 spec：§2 的字段名/口径/降级约定是权威，改名时须同步改本文与代码注释。实施完成后把 §2/§3 抽到 `docs/design/concept-boards.md`。

**Goal:** 给系统补上"概念板块成分 + 概念内情绪 + 次新股追踪"三层能力：概念板块不再只有一个空的 tab，个股能看到自己属于哪些概念，次新股（东财口径上市 ≤1 年）有独立的情绪卡与明细表。

**Architecture:** 新增 `concept_*` 三张表（成分表以 `symbol` 为业务键、`stock_id` 可空），东财 `clist` 全量分页采集每日盘后一次；**读路径本地聚合**（成分 × `daily_quotes`）保口径准确，资金流沿用已落库的东财快照；既有代码只在 4 个 seam 上改动，其余全部新增文件（§1）。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + PostgreSQL / APScheduler + RabbitMQ Worker / React 18 + antd 5 + TanStack Query + Playwright。

**Spec:** 本文（§2 契约 = 权威口径）。

## Global Constraints

- **数据源**：概念板块列表与成分**只用东财** `push2delay /api/qt/clist/get`；不引入同花顺（Alpine 镜像缺 `libstdc++.so.6`，`py_mini_racer` 不可用，实测 `OSError`）。`source` 字段值固定 `em_clist`。
- **历史口径**：只保证"当日/向前"。**禁止**用当前成分回算历史涨跌并当作历史事实展示；响应必须回传 `membership_as_of`（成分快照日），UI 必须显示"成分截至 X"。
- **单位/口径**：涨跌幅用 `daily_quotes.pct_chg`（TuShare 权威、已回填 3 年）；涨停判定用 `stock_price_limits` 交易所口径限价，容差 `0.005`，**禁止**按名称/代码前缀推比例。资金流用 `sector_moneyflow_snapshots`（元）。
- **缺失 ≠ 0**：无法判断的值一律 `null`，UI 渲染 `--`；禁止用 0 或 `false` 冒充。
- **确定性**：所有分组排行用 `(主序 DESC, 次序 DESC, 代码 ASC)`，禁止出现两次相同请求结果跳变。
- **节流**：东财请求走 `EastmoneyClient._get_json`（已有 `_MIN_INTERVAL = 0.3s`），**禁止**绕过它直接 httpx。
- **分页上限**：东财 `clist` 的 `pz` 服务端硬上限 **100**（实测传 500 仍只回 100），必须翻页 + 去重。
- **不新增队列容器**：复用 `market_data.fetch` 队列 + `MarketDataJobType` 新增一个值，不新增 `QUEUES` 条目/worker 进程。
- 后端门禁：`TUSHARE_TOKEN= uv run pytest`、`uv run --extra dev ruff check .`、`uv run --extra dev mypy app`；前端：`npm run lint`、`npm run build`、`npm run test:e2e`。

---

## 0. 现状分析（实施前必读）

### 0.1 已有 vs 缺失（实测，勿再猜）

| 能力 | 现状 | 证据 |
|---|---|---|
| 概念板块**列表**（涨跌幅/涨跌家数/主力资金流/领涨股） | ✅ 已有，落 `sector_moneyflow_snapshots` `dimension='concept'`，盘中 5 分钟 upsert | 库内 386 个板块有行，2026-09-04 起 |
| 概念**资金流**端点 | ✅ 已有 | `GET /api/v1/market/sector-moneyflow?dimension=concept` |
| 概念板块 tab（热门板块页） | ❌ **空**：`get_hot_boards()` 里 `if category == "concept": return []` | `backend/app/services/market_service.py:445` |
| 概念**成分股**（多对多成员） | ❌ 完全没有。`stocks` 只有单值 `csrc_desc`(行业) / `province`(地域) | `backend/app/models/stock.py:56-70` |
| 个股 → 概念标签 | ❌ 没有 | 个股详情只有申万链 + 自定义标签 |
| 概念快照覆盖面 | ⚠️ **偏样本**：只拉 `pz=100` 按主力净流入降序、**不分页**，东财共 **504** 个概念板块 → 至少 250 个永远无数据；每行 `pct_change` 是"最后一次进 Top100 那一刻"的值 | `eastmoney_client.py:107-146`；实测 09-18 攒到 251 行 |
| 次新股口径 | ❌ 无。但有现成外部口径可选（见 0.2） | — |
| 新股发行价/中签率（破发基准） | ❌ `fetch_new_share` 客户端方法存在但零调用 | `tushare_client.py:135` |

### 0.2 已实测的关键事实（直接决定设计）

1. **东财概念板块总数 504**，`fs=m:90+t:3+f:!50`，`fid=f12` 分页稳定（实测 pn=6 → 4 行，5×100+4=504）。
2. **成分股端点** `fs=b:BK0501` 可用：`total=162`，2 页拿全。字段：`f12`代码 / `f14`名称 / `f13`市场(1=沪,0=深) / `f2`价 / `f3`涨跌幅 / `f8`换手率 / `f20`总市值 / `f100`东财行业名 / `f62`主力净流入。
3. **`BK0501`（次新股）= 上市 ≤1 年滚动**：162 只成分**全部**上市于 `2025-09-19 ~ 2026-09-17`，与 TuShare `stock_basic` 口径的"近 1 年上市 162 只"完全一致。→ 次新股定义直接用板块成分，不写时间窗规则。
4. **名录冻结**：`stocks` 表 `max(list_date)=2026-05-07`、`max(asof)=2026-05-08`，而 TuShare 在上市 5565 vs 库内 5513 → **66 只 5-08 后上市的新股无 `stocks` 行**（如 `601091.SH` C沈鼓 在 `daily_quotes`/`stock_price_limits` 均 0 行）。`universe_refresh_job` 周六 09:00 首跑前，这类缺失仍在累积。
5. **同花顺兜底不可用**：`ak.stock_board_concept_name_ths()` → `OSError: Error loading shared library libstdc++.so.6`（镜像 `python3.13-alpine` + `py_mini_racer` musl 变体）。已决策不做。
6. **首日新股是口径雷区**：`601091.SH` 上市日 `stk_limit` 返回哨兵值 `up_limit=99999.999 / down_limit=0.01`（"首日无涨跌幅限制"），`pre_close=4.39`=发行价，TuShare `pct_chg=373.8%` 而东财 `f3=177.74%` → **同一天两个源的涨跌幅基准不同**，禁止混源拼同一条曲线。

### 0.3 代码地图（要动/要复用的文件）

**后端可复用（不改）**
- `app/repositories/limit_up_repo.py`：`latest_quote_date` / `list_recent_trade_dates` / `fetch_limit_up_window` / `has_price_limits`
- `app/services/limit_up_service.py:get_snapshot(cache, as_of, lookback)` → 已含 `as_of` / `echelons`（含 `streak`/`days_span`/`missing_days`）/ `degraded_reason` / `limits_present`，**概念页的板内梯队直接过滤它的 `echelons`，不重写 SQL**
- `app/services/market_service.py:get_stocks_enriched_by_symbols(db, symbols)` → `list[StockEnrichedOut]`，含最新价/开高低/成交额/换手率/流通市值/`list_date`（概念成分列表直接复用）
- `app/core/providers/eastmoney_client.py`：`_get_json`（节流+重试+rc 校验）/ `_CLIST_BASE` / `_EM_UT`
- `app/repositories/market_data_repo.py:list_sector_moneyflow` → 板块资金流
- `app/schemas/limit_up.py`（`LadderStockOut`/`EchelonOut`/`LimitUpLadderOut`）、`app/schemas/stock.py:StockEnrichedOut`

**前端可复用（不改）**
- `src/features/market/components/StockTable.tsx`（自选星标 + 排序 + 分页 + 点击进个股）
- `src/features/market/components/LimitUpLadder.tsx`（吃 `echelons` 形状）
- `src/shared/api/stocks.ts:mapBackendStockEnriched`（`StockEnrichedOut` → `StockRecord`，**已导出**）
- `src/shared/ui/SectionCard.tsx` / `ChangeText` / `formatCnDate`；`src/shared/api/client.ts:apiGet`

**允许修改的 4 个 seam（其余一律新增文件）**
1. `app/services/market_service.py` `get_hot_boards()` 的 `concept` 分支 → 一行委托给 `concept_service`
2. `app/schemas/task.py` `MarketDataJobType` + `app/workers/market_data_worker.py` 一个 `elif` 分支
3. `app/scheduler/{jobs,runner}.py` 新增一个 job（沿用既有注册模式）
4. 前端 `src/pages/stock-detail/index.tsx` 挂载点 + `src/pages/market/index.tsx` 新增一张卡 + `src/app/router/index.tsx` 一条路由 + `src/pages/market-hot-sectors/index.tsx` 行点击跳转

---

## 1. 解耦设计原则（硬约束，评审时逐条核对）

1. **成分表以 `symbol` 为业务键**，`stock_id` 是可空解析列 → 概念数据**不依赖 `stocks` 名录是否新鲜**；名录滞后不再静默丢数，而是以 `unresolved_count` 显式暴露在接口与 UI 上。
2. **涨跌/家数/情绪本地算，资金流用东财快照**：本地聚合（`concept_members × daily_quotes`）消除 Top100 偏样本；资金流字段来自快照，响应里两个来源分字段名表达，**禁止混算成一个"板块涨幅"**。
3. **概念读路径不复用 `csrc_desc`/`province` 聚合 SQL**（那是单值字段的写法），新 SQL 集中在 `concept_repo.py`。
4. **不重构既有函数**：`sector_ladder` / `fetch_limit_up_window` / `get_hot_boards` 的 industry 分支 / `get_stocks_enriched_by_symbols` 均不动（有单测与 e2e 钉住）；概念侧只**调用**它们。
5. **两个消费者、一个来源**：概念详情页与次新股卡各自持有自己的查询（不同聚合粒度），仅共享 `concept_members` 这一份来源；次新股口径常量只出现在 `new_stock_service.py` 一处。
6. **前端不改既有契约**：`HotSectors` / `MarketHotSectorsPage` 组件 props 不变（后端委托后概念 tab 自动有数据）；个股详情不修改 `fetchStockEnrichedBySymbol` 的响应形状，概念标签走独立端点、失败即整块不渲染。

---

## 2. 数据契约

### 2.1 表（3 张，迁移 `d5b7c9e1a3f2`，`down_revision = c9e3f2d4a5b6`）

```
concept_boards
  id              bigserial PK
  board_code      varchar(16)  NOT NULL   -- BK0501
  board_name      varchar(32)  NOT NULL
  source          varchar(16)  NOT NULL   -- 'em_clist'
  member_count    integer      NULL       -- 本次采集到的成分数（与东财 total 对账用）
  first_seen_at   timestamptz  NOT NULL
  last_seen_at    timestamptz  NOT NULL
  is_active       boolean      NOT NULL DEFAULT true   -- 本轮列表里没出现 → false（**不删成分**）
  UNIQUE (board_code)

concept_members              -- 当前成分（覆盖式：存在即"仍在板上"，缺失即剔除）
  id              bigserial PK
  board_code      varchar(16)  NOT NULL
  symbol          varchar(12)  NOT NULL   -- 业务键（不依赖 stocks 解析）
  stock_name      varchar(32)  NOT NULL
  stock_id        integer      NULL       -- 可空：名录滞后/未收录 → NULL（无 FK，与 stock_price_limits 一致）
  first_seen_on   date         NOT NULL   -- 本表首次见到该成分
  last_seen_on    date         NOT NULL   -- 最近一次抓到的日期（diff/清理依据）
  UNIQUE (board_code, symbol)
  INDEX (symbol)
  INDEX (stock_id)

concept_member_changes       -- 每日差分，追加式，永不更新/删除（历史口径从今天起积累）
  id              bigserial PK
  observed_on     date         NOT NULL
  board_code      varchar(16)  NOT NULL
  symbol          varchar(12)  NOT NULL
  stock_name      varchar(32)  NULL
  change_type     varchar(8)   NOT NULL   -- 'add' | 'remove'
  UNIQUE (observed_on, board_code, symbol, change_type)
  INDEX (board_code, observed_on)
```

**差分语义（必须按此实现，测试钉住）**
- 本轮**抓取成功**的板块：`seen = 抓到的 symbol 集合`
  - `new = seen - 库内` → INSERT 成员（`first_seen_on = last_seen_on = today`）+ 写 `change_type='add'`
  - `gone = 库内 - seen` → DELETE 成员 + 写 `change_type='remove'`
  - `keep = seen ∩ 库内` → UPDATE `last_seen_on = today`、刷新 `stock_name`/`stock_id`
- 本轮**抓取失败**的板块：**整块跳过**（不删成员、不写 change）——失败绝不能表现为"成分清空"
- 库内 `is_active=true` 但本轮列表未出现的板块 → `is_active = false`（保留其成分，不参与 diff）
- 首次初始化（库空）：只写 `add`（预期 504 板块 ≈ 5 万行），不写 `remove`

### 2.2 端点（新 router，两个，均为只读 + `CacheDep`）

```
GET /api/v1/concepts?sort=pct|inflow&limit=50&offset=0
  → { as_of, membership_as_of, price_source:"local_agg", flow_source:"em_clist"|null,
      total, items:[BoardItem] }
GET /api/v1/concepts/by-symbol/{symbol}
  → { as_of, membership_as_of, items:[{board_code, board_name, pct_change|null}] }
GET /api/v1/concepts/{board_code}
  → { as_of, membership_as_of, source, degraded_reason, board:BoardItem,
      kpis:{zt_count, max_streak, leader_symbol|null, leader_name|null},
      echelons:[EchelonOut],          # 复用既有 schema（板内过滤）
      unresolved_count, stock_count }
GET /api/v1/concepts/{board_code}/stocks
  → list[StockEnrichedOut]            # 复用既有 schema，前端复用 mapBackendStockEnriched
  # 额外两列以响应头方式给（见下），列表体保持既有形状
GET /api/v1/new-stocks
  → { as_of, membership_as_of, board_code:"BK0501", board_name:"次新股",
      source, degraded_reason,
      kpis:{up_count, flat_count, down_count, unpriced_count, limit_up_count,
            unbroken_count, above_first_open_count, avg_pct},
      items:[NewStockItem] }
```

- `BoardItem`：`{board_code, board_name, member_count, unresolved_count, priced_count, up_count, flat_count, down_count, avg_pct, main_net_inflow, main_net_ratio, lead_stock_name, lead_stock_code, lead_stock_pct, leaders:[{symbol,name,change_percent}]}`
- `NewStockItem`：`{symbol, name, exchange, list_date|null, listed_trade_days, pct_chg|null, close|null, turnover_rate|null, circ_mv|null, amount|null, streak|null, is_lu, never_broken|null, first_open|null, above_first_open|null}`
- 概念成分列表的**分页外的元信息**（`membership_as_of` / `unresolved_count` / `degraded_reason`）以 `GET /concepts/{board_code}` 提供，`/stocks` 保持纯数组以便前端零改映射（**不要**为它造 envelope，否则 `mapBackendStockEnriched` 用不上）。

### 2.3 口径表（写法即定义，UI 文案对齐）

| 字段 | 定义 | 缺失时 |
|---|---|---|
| `as_of` | 行情最新交易日（`limit_up_repo.latest_quote_date`） | 无行情 → `degraded_reason="no_quotes"` |
| `membership_as_of` | 该板块成分快照日 = `max(last_seen_on)` | 无成分 → `degraded_reason="no_members"` |
| `member_count` | `concept_members` 行数（含未解析） | — |
| `unresolved_count` | `stock_id IS NULL` 的成分数（= 名录滞后暴露面） | 0 |
| `priced_count` | 当日 `daily_quotes` 有行的成分数 | 0 |
| `up/flat/down_count` | `pct_chg > 0 / = 0 / < 0` 家数 | 不计入任何一档 |
| `avg_pct` | `pct_chg IS NOT NULL` 的成分均值（**不是**全成分口径，UI 注明"n=有行情家数"） | `null` |
| `main_net_inflow` | 东财快照，单位**元** | `null`（快照只覆盖当日 Top100 震荡集，**允许缺失**） |
| `zt_count`（板内） | 板块成分 × 当日 `is_lu`（`close >= up_limit - 0.005`）家数 | 限价缺失 → 0 + `degraded_reason="price_limits_missing"` |
| `max_streak` | 板内成分当日最大 `streak_upto` | 无涨停 → 0 |
| `listed_trade_days` | 该股 `daily_quotes` 行数（上市以来有行情的交易日数，停牌不计） | 0 |
| `never_broken` | 上市以来**每一行**都 `is_lu`（= 未开板新股） | `null`（限价缺失则不可判，**不得写成 false**） |
| `first_open` | 上市首日 `open` | `null` |
| `above_first_open` | 最新 `close >= first_open`（**替代破发指标**，发行价未采集，UI 必须写明"非破发口径"） | `null` |
| `limit_up_count` | 板块内当日 `is_lu` 家数 | 同 `zt_count` |
| `unbroken_count` | `never_broken = true` 家数 | 0 |

---

## 3. UI/UX 设计

> 复用既有视觉语言：卡片壳 `SectionCard`、涨跌色 `ChangeText`、缺失值 `--`、`asof` 行、降级用 `Alert` 文案。**不引入新样式体系或新图表库**。

### 触点 A：`A股热门板块` 卡的概念 tab（前端零改动）

- 唯一前置是后端委托（任务 T7）。委托后该 tab 与"行业板块"完全同形：名称 / 板块代码 / 涨跌幅 / 上涨·平盘·下跌 / 前 2 只领涨股。
- 排序沿用组件既有逻辑（`|changePercent|` 前 6）；「查看全部」→ `/market/hot-sectors/concept`（既有页，修好后自然有数据）。
- 说明文案改为「概念板块涨跌幅与家数按本地成分聚合，每日 18:20 刷新」。

### 触点 B（新页）`/market/concept/:boardCode` — 概念板块详情

```
面包屑   市场 / A股热门板块 / {概念名}
┌ 头部（SectionCard：标题={概念名}，asof=as_of）
│   [BK0714]  涨跌幅 +3.01%   上涨 250 · 平盘 4 · 下跌 75   成分 329
│   ⓘ 成分截至 9月18日（东财）· 涨跌按本地聚合
├ KPI 四瓦片：板内涨停(家) | 最高板(N连板 · 龙头名) | 主力净流入(亿) | 今日上涨占比
├ 板内连板梯队（复用 LimitUpLadder；空态"今日板内无涨停"）
└ 成分股表（复用 StockTable：代码/名称/最新价/涨跌幅/换手率/流通市值/上市日/连板/成交额，
   默认按涨跌幅降序，行点击 → /stock/:symbol）
```

- **连板列**：来自 `/concepts/{code}` 的 `echelons` 拍平成 `{symbol: streak}`，未在梯队中的成分渲染 `--`（缺失 ≠ 0）。
- **降级**：`degraded_reason` 非空 → 复用 §3 触点 D 的 `DegradedNotice` 映射（本期把它从 `pages/market/index.tsx` 提到 `shared/ui`）；`unresolved_count > 0` → 追加一条 `Alert type="warning"`：「另有 N 只成分股未收录（名录待刷新），未参与涨跌统计」。
- **空态**：板块码不存在 → `Empty description="未找到该概念板块"` + 返回 `/market/hot-sectors/concept`。
- **入口**：`MarketHotSectorsPage` 概念分类的表行点击 → 本页（1 行 `onRow` 改动，见 T13）。
- **数据获取**：两个 `useQuery` 各自独立（`concepts/{code}` 与 `concepts/{code}/stocks`），单点失败不牵连另一块。

### 触点 C：个股详情「所属概念」

- 位置：`UserTags` 之后同一行距（`marginTop: 8`），**不插入 Tabs**（保持现有版式密度）。
- 形态：`Tag` 列表，每枚显示「概念名 ±X.XX%」（涨跌色；`pct_change` 为 `null` 时只显示名字）；默认展示 8 枚 + `+N` 展开按钮（`CheckableTag` 风格，与 `SwL3LimitUpBoard` 的"仅看 ≥2 板"同构）。
- 交互：点击任一 Tag → `/market/concept/{board_code}`。
- **降级（关键）**：查询失败或 `items` 为空 → **整块不渲染**（不显示空壳/占位），保证既有页面版式零扰动。
- 数据源：`GET /api/v1/concepts/by-symbol/{symbol}`，独立 `useQuery`，`staleTime 5min`。

### 触点 D：「短线情绪」→「次新股情绪」卡

```
SectionCard title="次新股情绪" asof={as_of}
  KPI 瓦片：次新涨停(家) | 未开板(家) | 平均涨跌幅 | 现价高于首日开盘(家)
  CheckableTag 切换：仅看连板（streak ≥ 1）      [默认关闭]
  轻量 Table（同 SwL3LimitUpBoard 行式版式，**不复用 StockTable**——它不接受自定义列）：
    名称 | 上市日 | 交易日数 | 涨跌幅 | 换手率 | 连板 | 成交额
  注脚：次新股 = 东财概念板块 BK0501 成分（上市 ≤1 年滚动）；「高于首日开盘」为替代口径，非破发
        （发行价未采集）；成分每日 18:20 刷新
```

- 位置：`SentimentTab` 中「昨日涨停今日表现」之后（`Col span={24}`）。
- 排序：涨跌幅降序（`null` 排最后）。
- 降级/空态：`degraded_reason` → `DegradedNotice`；无数据 → `Empty description="成分数据每日 18:20 刷新"`。
- KPI 在家数口径上必须与表格行数一致（同一 `items` 数组聚合，**不新造口径**，与梯队 pill 同款做法）。

### 触点 E：一致性小改（可选，随 T14 一起）

- `DataCoverageMatrix` 加一行：`概念板块成分 | 板块列表 + 成分股 | 每日 18:20 | 东财`。
- `SectorMoneyflowCard` 概念维度 `extra` 加 `Tooltip`：主力资金流为东财按净流入排序的 Top100 样本，非全量板块。

---

## 4. 采集与调度

- **入口**：`concept_service.ingest_concept_members(db) -> dict[str, int]`
  1. `boards = await em.fetch_concept_boards()`（5 页，去重；实测 504）
  2. upsert `concept_boards`（`last_seen_at=now`；本轮未出现的置 `is_active=false`）
  3. 逐板 `await em.fetch_concept_members(code)`（504 板 × ~1.6 页 ≈ 800 请求，`_MIN_INTERVAL=0.3` → **约 5 分钟**）
  4. 逐板 diff + 写 `concept_member_changes`（§2.1 语义），单板失败 `failed_boards += 1` 并跳过
  5. 返回 `{boards, members_upserted, added, removed, failed_boards, unresolved}`
- **调度**：`concept_members_refresh_job`，`CronTrigger(day_of_week="mon-fri", hour=18, minute=20, timezone="Asia/Shanghai")`（避开 17:45 对账、18:00 龙虎榜）。吃全局 `job_defaults`，即 `misfire_grace_time=None`（停摆后补跑，任务幂等）。
- **手动**：`MarketDataJobType` 增 `"concept_members"`，`POST /api/v1/tasks/fetch-market-data {type:"concept_members"}`。**不新增队列、不新增 worker 进程。**
- **限流/成本**：约 800-1000 请求/日、5 分钟。不做的优化：**不**分片多日拉（会让不同板块的 `membership_as_of` 不一致，破坏口径唯一性）。
- **失败隔离原则**：任何一次抓取失败都不改变既有成分（宁可少更新，不可清空）。

---

## 5. 任务分解（tracer-bullet 分期）

> 每个任务结束后可独立验证 + commit。命令一律在 `backend/` 或 `frontend/` 下执行。

### T0（运维前置，非代码）：解冻 `stocks` 名录

**为什么必须先做**：`stocks` 冻结在 2026-05-08，66 只新上市股票无行（§0.2-4）。它们会以 `unresolved_count` 出现在概念板块上、且参与不了本地聚合。

- [ ] **Step 1**：手动触发全量名录刷新

```bash
cd backend && uv run python -c "
import asyncio
from app.core.database import async_session_factory
from app.services.universe_ingest import ingest_stock_universe
async def m():
    async with async_session_factory() as db:
        print(await ingest_stock_universe(db)); await db.commit()
asyncio.run(m())"
```

- [ ] **Step 2**：验证

```bash
docker compose exec -T postgres psql -U postgres -d stock_bot -c \
 "select max(list_date) 最新上市日, max(asof) 名录时间, count(*) 总数 from stocks;"
```
预期：`最新上市日` 为最近交易日、`总数` ≥ 5565-退市。

- [ ] **Step 3**：若 `daily_quotes` 仍有缺口（新收录股票没有历史行情），在缺行情时补跑一次对账：

```bash
curl -X POST localhost/api/v1/tasks/fetch-market-data \
  -H 'Content-Type: application/json' -d '{"type":"reconcile"}'
```

> T0 不阻塞 T1-T11 的开发（成分表以 `symbol` 为键，功能自洽），但**上线前必须完成**，否则概念页会显示 `unresolved_count > 0`。

---

### Phase 1 — 数据面（后端）

#### T1 模型 + 迁移

**Files:** Create `backend/app/models/concept.py`、`backend/app/migrations/versions/d5b7c9e1a3f2_add_concept_tables.py`；Modify `backend/app/models/__init__.py`

**Interfaces (Produces):** `ConceptBoard` / `ConceptMember` / `ConceptMemberChange`（字段名见 §2.1）

- [ ] **Step 1**：写模型

```python
"""概念板块与成分股 ORM models（东财口径）。

业务键取 ``symbol`` 而非 ``stock_id``：``stocks`` 名录会滞后（实测冻结在 2026-05-08，
66 只新上市股票缺行），若以 stock_id 为键，名录滞后会静默丢掉成分股；此处把
``stock_id`` 降级为可空解析列，缺失以 ``unresolved_count`` 对外暴露。
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConceptBoard(Base):
    __tablename__ = "concept_boards"
    __table_args__ = (UniqueConstraint("board_code", name="uq_concept_boards_code"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    board_name: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="em_clist")
    member_count: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ConceptMember(Base):
    __tablename__ = "concept_members"
    __table_args__ = (
        UniqueConstraint("board_code", "symbol", name="uq_concept_member_board_symbol"),
        Index("idx_concept_members_symbol", "symbol"),
        Index("idx_concept_members_stock_id", "stock_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(32), nullable=False)
    stock_id: Mapped[int | None] = mapped_column(Integer)
    first_seen_on: Mapped[date] = mapped_column(Date, nullable=False)
    last_seen_on: Mapped[date] = mapped_column(Date, nullable=False)


class ConceptMemberChange(Base):
    """成分变更差分（追加式，永不更新）：历史口径从启用之日开始积累。"""

    __tablename__ = "concept_member_changes"
    __table_args__ = (
        UniqueConstraint(
            "observed_on", "board_code", "symbol", "change_type",
            name="uq_concept_change_row",
        ),
        Index("idx_concept_changes_board_date", "board_code", "observed_on"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    board_code: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(12), nullable=False)
    stock_name: Mapped[str | None] = mapped_column(String(32))
    change_type: Mapped[str] = mapped_column(String(8), nullable=False)
```

- [ ] **Step 2**：迁移文件（`upgrade` 建三表 + 索引；`downgrade` 逆序 drop），`revision="d5b7c9e1a3f2"`、`down_revision="c9e3f2d4a5b6"`。
- [ ] **Step 3**：`app/models/__init__.py` 导入并加入 `__all__`（`ConceptBoard`, `ConceptMember`, `ConceptMemberChange`）。
- [ ] **Step 4**：应用迁移并验证

```bash
cd backend && uv run alembic upgrade head && \
docker compose exec -T postgres psql -U postgres -d stock_bot -c "\d concept_members"
```
预期：3 张表 + 索引 `idx_concept_members_symbol` / `idx_concept_members_stock_id` 存在。

- [ ] **Step 5**：Commit `feat(concept): add concept board/member tables`

#### T2 东财客户端：板块列表 + 成分（分页）

**Files:** Modify `backend/app/core/providers/eastmoney_client.py`；Test `backend/tests/test_eastmoney_client.py`

**Interfaces (Produces):**
- `EastmoneyClient.fetch_concept_boards() -> list[dict]` → `{board_code, board_name, member_total}`
- `EastmoneyClient.fetch_concept_members(board_code: str) -> list[dict]` → `{symbol, name, market_flag}`

- [ ] **Step 1**：先写失败测试（分页 + 去重 + 上限守卫）

```python
def test_fetch_concept_boards_paginates_and_dedupes(monkeypatch):
    """pz 服务端上限 100：必须翻页，且翻页抖动产生的重复行要去重（实测 504 板）。"""
    page1 = {"data": {"total": 150, "diff": [_board(f"BK{i:04d}") for i in range(100)]}}
    page2 = {"data": {"total": 150, "diff": [_board("BK0099")] + [_board(f"BK{i:04d}") for i in range(100, 150)]}}
    calls: list[int] = []

    async def fake_get_json(self, base, path, params):
        calls.append(params["pn"])
        assert params["fid"] == "f12"  # 按代码排序，盘中翻页稳定
        return page1 if params["pn"] == 1 else page2

    monkeypatch.setattr(EastmoneyClient, "_get_json", fake_get_json)
    boards = asyncio.run(EastmoneyClient().fetch_concept_boards())
    assert calls == [1, 2]
    assert len(boards) == 150 and boards[0]["board_code"] == "BK0000"


def test_fetch_concept_boards_stops_on_empty_page(monkeypatch):
    """total 报大但返回空页时必须收敛（防死循环把 5 分钟任务变成长驻）。"""

    async def fake_get_json(self, base, path, params):
        return {"data": {"total": 9999, "diff": []}}

    monkeypatch.setattr(EastmoneyClient, "_get_json", fake_get_json)
    assert asyncio.run(EastmoneyClient().fetch_concept_boards()) == []
```

- [ ] **Step 2**：跑测试确认失败

```bash
cd backend && uv run pytest tests/test_eastmoney_client.py -k concept -v
```
预期：`AttributeError: 'EastmoneyClient' object has no attribute 'fetch_concept_boards'`

- [ ] **Step 3**：实现（含 `_PAGE_SIZE = 100`、`_MAX_PAGES = 20` 硬护栏、`_num()` 归一）

```python
_CONCEPT_FS = "m:90+t:3+f:!50"

async def fetch_concept_boards(self) -> list[dict[str, Any]]:
    """概念板块全量列表（实测 2026-09-18：total=504，pz 上限 100，需翻 6 页）。

    ``fid=f12``（按板块代码排）而非 f3（涨跌幅）：盘中按涨跌幅排序翻页会漏/重。
    """
    return await self._paged_clist(_CONCEPT_FS, "f12,f14,f104,f105,f106", _map_concept_board)

async def fetch_concept_members(self, board_code: str) -> list[dict[str, Any]]:
    """单板成分股（实测 BK0501 total=162 → 2 页）。"""
    return await self._paged_clist(f"b:{board_code}", "f12,f13,f14", _map_concept_member)
```

> **字段口径（T2 评审 I1 修正）**：`member_total` 必须 = `f104 + f105 + f106`（上涨 + 下跌 + **平盘**），不能用 `f104+f105`。实测证据（2026-09-18）：`BK1753 光刻胶` 列表行 `f104=55 / f105=6 / f106=2`，而 `fs=b:BK1753` 的 `total=63` —— 漏平盘会把 T4 的成分数对账长期算低并产生幻影差异告警（`BK0501` 平盘为 0，故该板看不出来）。三个计数全不可解析时 `member_total = None`（不得写 0）。

`_paged_clist(fs, fields, mapper)` 统一实现：循环 `pn`、`pz=100`、`fid=f12`、累计去重（按映射后的 key）、`len(diff)==0 or len(out) >= total or pn > _MAX_PAGES` 时收敛。

- [ ] **Step 4**：补一条实测断言（防字段名漂移）

```python
def test_concept_member_fields_are_stable(monkeypatch):
    """字段形状实测钉住：f12 代码 / f14 名称 / f13 市场（1=沪,0=深）。"""
    async def fake_get_json(self, base, path, params):
        return {"data": {"total": 1, "diff": [{"f12": "601091", "f14": "C沈鼓", "f13": 1}]}}

    monkeypatch.setattr(EastmoneyClient, "_get_json", fake_get_json)
    rows = asyncio.run(EastmoneyClient().fetch_concept_members("BK0501"))
    assert rows == [{"symbol": "601091", "name": "C沈鼓", "market_flag": 1}]
```

- [ ] **Step 5**：`uv run pytest tests/test_eastmoney_client.py -v` 全绿；Commit `feat(concept): eastmoney concept board + member client (paged)`

#### T3 concept_repo：upsert / diff / 查询

**Files:** Create `backend/app/repositories/concept_repo.py`；Test `backend/tests/test_concept_repo.py`

**Interfaces (Produces):**
- `upsert_boards(db, rows: list[dict], today: date) -> int`
- `deactivate_missing_boards(db, codes: set[str]) -> int`
- `list_member_symbols(db, board_code) -> list[tuple[str, str]]`（symbol, stock_name）
- `upsert_members(db, board_code, rows, today) -> tuple[int, int, int]`（added, updated, removed）
- `record_changes(db, observed_on, changes: list[dict]) -> int`
- `aggregate_boards(db, as_of, sort, limit, offset) -> list[dict]`（§2.3 口径）
- `member_history_stats(db, board_code) -> dict[str, dict]`（`listed_trade_days` / `never_broken` / `first_open`）
- `symbol_to_stock_ids(db, symbols) -> dict[str, int]`
- `diff_members(existing: dict[str, str], seen: dict[str, str], today: date) -> MemberDiff`（纯函数，`MemberDiff{added, removed, kept, degraded}`）

- [ ] **Step 1**：失败测试——**diff 语义 + 失败隔离**（本任务的核心不变量）

```python
def test_diff_member_rows_adds_removes_and_keeps():
    """纯函数形态的 diff 语义（不触 DB）：新增写 add、消失写 remove、留存只刷 last_seen_on。"""
    existing = {"600000": "浦发银行", "600001": "邯郸钢铁"}
    seen = {"600000": "浦发银行", "600002": "东北证券"}
    plan = diff_members(existing, seen, today=date(2026, 9, 18))
    assert plan.added == [{"symbol": "600002", "stock_name": "东北证券"}]
    assert plan.removed == ["600001"]
    assert plan.kept == ["600000"]          # 只 refresh，不写 change


def test_diff_members_empty_seen_is_not_a_wipe():
    """抓取失败/空页绝不能表现为"成分清空"：调用方据此跳过该板。"""
    plan = diff_members({"600000": "浦发银行"}, seen={}, today=date(2026, 9, 18))
    assert plan.removed == ["600000"] and plan.degraded is True
```

- [ ] **Step 2**：跑测试确认失败 → `uv run pytest tests/test_concept_repo.py -v`
- [ ] **Step 3**：实现 `diff_members(existing, seen, today)` 纯函数（`seen` 为空时置 `degraded=True`，调用方跳过落库）+ 上述 repo 函数。聚合 SQL 用 §2.3 口径：

```sql
WITH px AS (SELECT stock_id, pct_chg FROM daily_quotes WHERE trade_date = :as_of)
SELECT b.board_code, b.board_name,
       count(*)                                        AS member_count,
       count(*) FILTER (WHERE cm.stock_id IS NULL)      AS unresolved_count,
       count(px.pct_chg)                                AS priced_count,
       count(*) FILTER (WHERE px.pct_chg > 0)           AS up_count,
       count(*) FILTER (WHERE px.pct_chg = 0)           AS flat_count,
       count(*) FILTER (WHERE px.pct_chg < 0)           AS down_count,
       avg(px.pct_chg)                                  AS avg_pct
FROM concept_boards b
JOIN concept_members cm ON cm.board_code = b.board_code
LEFT JOIN px ON px.stock_id = cm.stock_id
WHERE b.is_active
GROUP BY b.board_code, b.board_name
ORDER BY avg_pct DESC NULLS LAST, b.board_code ASC
```

- [ ] **Step 4**：计划守卫测试（沿用 `tests/test_limit_up_window_sql.py` 先例）：断言聚合查询在 `concept_members(board_code)` 上先收敛、`daily_quotes` 走 `(stock_id, trade_date)` 唯一键，无 Nested Loop 逐票探测。
- [ ] **Step 5**：`uv run pytest tests/test_concept_repo.py -v` 全绿；Commit `feat(concept): concept repo with member diff + local aggregation`

#### T4 concept_service：采集

**Files:** Create `backend/app/services/concept_service.py`；Test `backend/tests/test_concept_ingest.py`

**Interfaces (Produces):** `ingest_concept_members(db) -> dict[str, int]`

- [ ] **Step 1**：失败测试——单板失败隔离 + 统计字段

```python
class _FakeEm:
    def __init__(self, fail_on: str | None = None) -> None:
        self.fail_on = fail_on

    async def fetch_concept_boards(self):
        return [{"board_code": "BK0001", "board_name": "A", "member_total": 2},
                {"board_code": "BK0002", "board_name": "B", "member_total": 1}]

    async def fetch_concept_members(self, code):
        if code == self.fail_on:
            raise httpx.TransportError("boom")
        return ([{"symbol": "600000", "name": "浦发银行", "market_flag": 1},
                 {"symbol": "600001", "name": "邯郸钢铁", "market_flag": 1}]
                if code == "BK0001" else [])


async def test_ingest_isolates_single_board_failure(monkeypatch, fake_repo):
    """一个板块抓不到：跳过它（不删成员、不写 change），其余照常，且 failed_boards 上报。"""
    monkeypatch.setattr(concept_service, "_get_eastmoney", lambda: _FakeEm(fail_on="BK0002"))
    out = await concept_service.ingest_concept_members(db=None)
    assert out["boards"] == 2 and out["failed_boards"] == 1 and out["added"] == 2
    assert [c["board_code"] for c in fake_repo.changes] == ["BK0001"]
```

- [ ] **Step 2**：跑失败 → **Step 3**：实现（`_get_eastmoney()` 取客户端；逐板 try/except 记 `failed_boards`；`symbol → stock_id` 用一条 `SELECT symbol, id FROM stocks` 建映射，**不做交易所推断**）
- [ ] **Step 4**：`uv run pytest tests/test_concept_ingest.py -v` 全绿
- [ ] **Step 5**：实机跑一次全量（约 5 分钟）并记录真实数字

```bash
cd backend && uv run python -c "
import asyncio
from app.core.database import async_session_factory
from app.services.concept_service import ingest_concept_members
async def m():
    async with async_session_factory() as db:
        print(await ingest_concept_members(db)); await db.commit()
asyncio.run(m())"
```
预期（2026-09-18 基线）：`boards≈504`、`added≈50000`、`removed=0`、`failed_boards=0`、`unresolved>0`（T0 未做时）。

- [ ] **Step 6**：Commit `feat(concept): ingest concept members with per-board failure isolation`

#### T5 调度 + worker 手动入口

**Files:** Modify `backend/app/scheduler/jobs.py`、`backend/app/scheduler/runner.py`、`backend/app/schemas/task.py`、`backend/app/workers/market_data_worker.py`；Test `backend/tests/test_scheduler_config.py`

- [ ] **Step 1**：失败测试（注册断言，沿用既有写法）

```python
def test_concept_refresh_job_registered_after_close():
    jobs = {j.id: j for j in create_scheduler().get_jobs()}
    job = jobs["concept_members_refresh"]
    # 避开 17:45 对账与 18:00 龙虎榜；不依赖 APScheduler 内部表达式对象
    assert "hour='18'" in str(job.trigger) and "minute='20'" in str(job.trigger)
    assert "day_of_week='mon-fri'" in str(job.trigger)
    assert job.misfire_grace_time is None          # 吃全局 job_defaults（停摆后补跑）
```

- [ ] **Step 2**：跑失败 → **Step 3**：`jobs.py` 加 `concept_members_refresh_job()`（薄封装 `ingest_concept_members` + `db.commit()` + 结构化日志）；`runner.py` 注册 cron；`MarketDataJobType` 加 `"concept_members"`；`market_data_worker._run` 加分支。
- [ ] **Step 4**：`uv run pytest tests/test_scheduler_config.py tests/test_market_data_worker.py -v` 全绿
- [ ] **Step 5**：Commit `feat(concept): schedule daily concept member refresh + manual trigger`

---

### Phase 2 — 读路径（后端）

#### T6 `GET /api/v1/concepts` 板块列表

**Files:** Create `backend/app/schemas/concept.py`、`backend/app/api/v1/concepts.py`；Modify `backend/app/api/v1/__init__.py`；Test `backend/tests/test_concept_api.py`

**Interfaces (Produces):** `BoardItemOut` / `ConceptListOut`；`concept_service.list_boards(db, cache, sort, limit, offset) -> dict`；`concept_service.hot_board_rows(cache, limit) -> list[dict]`（T7 委托入口，形状与 industry 分支一致）

> 命名歧义澄清：`BoardItem.member_count` 一律指**本地 `concept_members` 行数**（权威）；`concept_boards.member_count` 只是采集时刻与东财 `total` 的对账值，不出现在 API 响应里。
> 会话边界：`hot_board_rows(cache, limit)` 自开 `async_session_factory()` 会话（因为 `get_hot_boards(category, cache)` 无 `db` 参数，与 `market_data_service.get_sector_moneyflow` 同款）；`list_boards(db, cache, ...)` 吃外部会话，便于单测注入。

- [ ] **Step 1**：失败测试（契约字段 + 排序确定性）

```python
async def test_concept_list_returns_membership_as_of_and_local_source(seeded_boards, cache):
    """契约：membership_as_of 必须回传（历史口径声明）；价格源与资金流源分字段，不得混算。"""
    body = await concept_service.list_boards(db, cache, sort="pct", limit=50, offset=0)
    assert body["price_source"] == "local_agg"
    assert body["membership_as_of"] == "2026-09-18"
    first = body["items"][0]
    assert first["member_count"] >= first["priced_count"]
    # 资金流允许缺失（东财快照只覆盖当日 Top100 震荡集），且不得用 0 填充
    assert first["main_net_inflow"] is None or isinstance(first["main_net_inflow"], float)
    # 确定性：相同请求两次结果顺序一致（avg_pct desc, board_code asc）
    again = await concept_service.list_boards(db, cache, sort="pct", limit=50, offset=0)
    assert [i["board_code"] for i in again["items"]] == [i["board_code"] for i in body["items"]]
```

- [ ] **Step 2**：跑失败 → **Step 3**：实现 service `list_boards(db, cache, sort, limit, offset)`：`concept_repo.aggregate_boards` + `market_data_repo.list_sector_moneyflow(as_of,'concept',limit=100)` 内存 join（**不是 SQL join**——快照只覆盖当日部分板块，left join 会放大行数风险）；`leaders` = 板块内 `pct_chg` 前 2（复用聚合结果或一次 `DISTINCT ON` 查询，二选一，测试钉住确定性）；Redis 缓存 TTL 300s。
- [ ] **Step 4**：端点 + `api/v1/__init__.py` 注册（2 行 seam）；`uv run pytest tests/test_concept_api.py -v`
- [ ] **Step 5**：Commit `feat(concept): concept board list endpoint`

#### T7 修活「热门板块」概念 tab（1 行委托）

**Files:** Modify `backend/app/services/market_service.py:445`；Test `backend/tests/test_market_contract.py`

- [ ] **Step 1**：失败测试

```python
async def test_hot_boards_concept_is_not_empty_anymore():
    """概念分支必须走本地成分聚合（此前硬编码 return []，前端 tab 永远空白）。"""
    rows = await market_service.get_hot_boards("concept")
    assert rows, "概念板块不得为空"
    assert {"id", "name", "code", "changePercent", "upCount", "flatCount", "downCount", "leaders"} <= set(rows[0])
```

- [ ] **Step 2**：跑失败 → **Step 3**：把 `if category == "concept": return []` 改成委托（**保持返回形状与 industry 完全一致，前端零改动**）：

```python
    if category == "concept":
        # 概念是"当前成分 × 本地行情"聚合，与 stocks.csrc_desc/province 单值字段无共同形状；
        # 实现在 concept_service（见 §1 原则 3），此处只做形状适配。
        from app.services import concept_service  # noqa: PLC0415

        return await concept_service.hot_board_rows(cache, limit=10)
```

- [ ] **Step 4**：`uv run pytest tests/test_market_contract.py -v`；前端人工确认 `A股全景 → A股热门板块 → 概念板块` 有数据
- [ ] **Step 5**：Commit `fix(concept): wire concept category into hot boards`

#### T8 `GET /api/v1/concepts/{board_code}` 板块详情（含板内梯队）

**Files:** Modify `backend/app/schemas/concept.py`、`backend/app/api/v1/concepts.py`；Test `backend/tests/test_concept_api.py`

**Interfaces (Consumes):** `limit_up_service.get_snapshot(cache)`；**Produces:** `ConceptDetailOut`

- [ ] **Step 1**：失败测试——**口径同源**（板内梯队必须与梯队卡同源，不重算 streak）

```python
async def test_board_detail_reuses_snapshot_echelons(monkeypatch):
    """板内梯队 = get_snapshot().echelons 按成分过滤；streak 不得来自第二套计算。"""
    snap = {"as_of": date(2026, 9, 18), "echelons": [
        {"streak": 2, "label": "2连板", "stocks": [
            {"symbol": "600000", "name": "X", "streak_upto": 2},
            {"symbol": "999999", "name": "Y", "streak_upto": 2}]}],
        "degraded_reason": None, "limits_present": True}
    monkeypatch.setattr(limit_up_service, "get_snapshot", async_fake(snap))
    out = await concept_service.get_board_detail(db, "BK0501", cache=None)
    assert [s["symbol"] for s in out["echelons"][0]["stocks"]] == ["600000"]
    assert out["kpis"]["max_streak"] == 2
```

- [ ] **Step 2**：跑失败 → **Step 3**：实现 service：`snap = await limit_up_service.get_snapshot(cache)` → `members = set(concept_repo.list_member_symbols(...))` → 过滤 echelons → KPI（`zt_count`/`max_streak`/龙头按 `-streak, -amount, symbol` 确定性裁决）→ `membership_as_of`/`unresolved_count`；`degraded_reason` 为空但 `limits_present=false` 时置 `price_limits_missing`；无成分置 `no_members`。
- [ ] **Step 4**：`uv run pytest tests/test_concept_api.py -v`
- [ ] **Step 5**：Commit `feat(concept): concept board detail with in-board ladder`

#### T9 `GET /api/v1/concepts/{board_code}/stocks` + `by-symbol`

**Files:** Modify `concept_service.py`、`concepts.py`；Test `backend/tests/test_concept_api.py`

**Interfaces (Consumes):** `market_service.get_stocks_enriched_by_symbols(db, symbols)`；**Produces:** `concept_service.get_board_stocks(db, board_code) -> list[StockEnrichedOut]`、`concept_service.get_concepts_by_symbol(db, symbol) -> list[dict]`

- [ ] **Step 1**：失败测试（形状 = `StockEnrichedOut`，前端才能零改映射复用 `StockTable`）

```python
async def test_board_stocks_reuses_enriched_shape():
    rows = await concept_service.get_board_stocks(db, "BK0501")
    assert {"symbol", "name", "exchange", "category", "asof"} <= set(rows[0])
    assert rows == sorted(rows, key=lambda r: (r.get("change_percent") is None, -(r.get("change_percent") or 0)))
```

- [ ] **Step 2**：跑失败 → **Step 3**：实现 `list_member_symbols` → `get_stocks_enriched_by_symbols` → 涨跌幅降序（`null` 最后，符号升序 tiebreak）；`by-symbol`：查该 symbol 的板块码 → 只对这些板块跑聚合（`WHERE board_code = ANY(...)`）→ 按 `pct_change DESC NULLS LAST, board_code` 排序。
- [ ] **Step 4**：`uv run pytest tests/test_concept_api.py -v`；Commit `feat(concept): board member stocks + symbol→concepts lookups`

#### T10 `GET /api/v1/new-stocks` 次新股

**Files:** Create `backend/app/services/new_stock_service.py`、`backend/app/api/v1/new_stocks.py`；Modify `app/api/v1/__init__.py`；Test `backend/tests/test_new_stock_service.py`

**Interfaces (Consumes):** `concept_repo.member_history_stats`、`market_service.get_stocks_enriched_by_symbols`；**Produces:** `NEW_STOCK_BOARD_CODE`、`new_stock_service.get_new_stock_board(db, cache) -> dict`、`new_stock_service.build_kpis(items: list[dict], stats: dict[str, dict]) -> dict`

- [ ] **Step 1**：失败测试（KPI 定义 + 缺失语义）

```python
def test_kpis_treat_missing_limits_as_unknown_not_broken():
    """限价缺失时 never_broken 必须是 None（不可判），绝不能是 False。"""
    stats = {"600000": {"listed_trade_days": 5, "never_broken": None, "first_open": 10.0}}
    rows = [{"symbol": "600000", "pct_chg": 3.0, "close": 11.0}]
    kpis = build_kpis(rows, stats)
    assert kpis["unbroken_count"] == 0
    assert kpis["above_first_open_count"] == 1
    assert rows[0]["never_broken"] is None       # 透传给 UI 渲染 `--`


def test_board_code_constant_is_single_source():
    """次新股口径 = 东财 BK0501（实测 162 只全为上市 ≤1 年），不得另立时间窗规则。"""
    assert NEW_STOCK_BOARD_CODE == "BK0501"
```

- [ ] **Step 2**：跑失败 → **Step 3**：实现 `member_history_stats` SQL（§2.3 口径，`bool_and` 保留 NULL 语义）：

```sql
WITH h AS (
    SELECT cm.stock_id, cm.symbol, q.trade_date, q.open,
           (l.up_limit IS NOT NULL AND q.close >= l.up_limit - 0.005) AS is_lu,
           (l.up_limit IS NULL) AS limits_missing
    FROM concept_members cm
    JOIN daily_quotes q ON q.stock_id = cm.stock_id
    LEFT JOIN stock_price_limits l ON l.stock_id = q.stock_id AND l.trade_date = q.trade_date
    WHERE cm.board_code = :board_code AND cm.stock_id IS NOT NULL
)
SELECT symbol, stock_id, count(*) AS listed_trade_days,
       CASE WHEN bool_or(limits_missing) THEN NULL ELSE bool_and(is_lu) END AS never_broken,
       (array_agg(open ORDER BY trade_date))[1] AS first_open
FROM h GROUP BY symbol, stock_id
```

- [ ] **Step 4**：service 合并 enriched 行 → `above_first_open = close >= first_open`，`streak` 从 `get_snapshot()` 拍平表取（缺失 → `None`）；端点 + 路由注册；`uv run pytest tests/test_new_stock_service.py -v`
- [ ] **Step 5**：实机验收（数值对拍东财）

```bash
docker compose exec -T api python -c "
import asyncio, httpx, json
async def m():
    async with httpx.AsyncClient(base_url='http://localhost:8000') as c:
        r = await c.get('/api/v1/new-stocks')
        print(json.dumps(r.json()['kpis'], ensure_ascii=False))
asyncio.run(m())"
```
与东财 `fs=b:BK0501` 的 `f104/f105`（实测 157 涨 / 5 跌）对拍：`up_count`/`down_count` 数量级一致（差异只应来自停牌/无行情）。

- [ ] **Step 6**：Commit `feat(new-stock): eastmoney BK0501 based new-stock board endpoint`

---

### Phase 3 — 前端

#### T11 API 层 + 类型

**Files:** Create `frontend/src/shared/api/concept.ts`；Test 由 T13/T15 的 e2e 覆盖

- [ ] **Step 1**：实现（形状对齐后端，复用既有 mapper）

```ts
import { apiGet } from "./client";
import { mapBackendStockEnriched, type BackendStockEnriched } from "./stocks";
import type { StockRecord } from "@/shared/types";

export interface ConceptBoardItem {
  board_code: string; board_name: string;
  member_count: number; unresolved_count: number; priced_count: number;
  up_count: number; flat_count: number; down_count: number;
  avg_pct: number | null;
  main_net_inflow: number | null; main_net_ratio: number | null;
  lead_stock_name: string | null; lead_stock_code: string | null; lead_stock_pct: number | null;
  leaders: { symbol: string; name: string; change_percent: number }[];
}
export interface ConceptDetail {
  as_of: string; membership_as_of: string | null; source: string; degraded_reason: string | null;
  board: ConceptBoardItem;
  kpis: { zt_count: number; max_streak: number; leader_symbol: string | null; leader_name: string | null };
  echelons: Echelon[];
  unresolved_count: number; stock_count: number;
}
export interface SymbolConcept { board_code: string; board_name: string; pct_change: number | null }
export interface NewStockKpis {
  up_count: number; flat_count: number; down_count: number; unpriced_count: number;
  limit_up_count: number; unbroken_count: number; above_first_open_count: number; avg_pct: number | null;
}
export interface NewStockItem { symbol: string; name: string; exchange: string; list_date: string | null;
  listed_trade_days: number; pct_chg: number | null; close: number | null; turnover_rate: number | null;
  circ_mv: number | null; amount: number | null; streak: number | null; is_lu: boolean;
  never_broken: boolean | null; first_open: number | null; above_first_open: boolean | null }

export interface NewStocksResponse {
  as_of: string; membership_as_of: string | null; source: string;
  degraded_reason: string | null; kpis: NewStockKpis; items: NewStockItem[];
}
/** 与后端 `app/schemas/limit_up.py:EchelonOut` 同形（板内梯队直接复用 `<LimitUpLadder>`）。 */
export interface EchelonStock {
  symbol: string; name: string; streak: number; days_span: number; boards_in_window: number;
  missing_days: number; amount?: number | null;
  seal_time?: string | null; seal_fund?: number | null; break_count?: number | null;
}
export interface Echelon { streak: number; label: string; stocks: EchelonStock[] }
export function fetchConceptDetail(code: string) { return apiGet<ConceptDetail>(`/api/v1/concepts/${code}`); }
export function fetchConceptStocks(code: string): Promise<StockRecord[]> {
  return apiGet<BackendStockEnriched[]>(`/api/v1/concepts/${code}/stocks`).then((r) => r.map(mapBackendStockEnriched));
}
export function fetchConceptsBySymbol(symbol: string) {
  return apiGet<{ as_of: string; membership_as_of: string | null; items: SymbolConcept[] }>(
    `/api/v1/concepts/by-symbol/${symbol}`);
}
export function fetchNewStocks(): Promise<NewStocksResponse> {
  return apiGet<NewStocksResponse>("/api/v1/new-stocks");
}
```

- [ ] **Step 2**：`npm run lint`；Commit `feat(concept): frontend concept api layer`

#### T12 提取 `DegradedNotice` 到 shared

**Files:** Create `frontend/src/shared/ui/DegradedNotice.tsx`；Modify `frontend/src/shared/ui/index.ts`、`frontend/src/pages/market/index.tsx`

- [ ] **Step 1**：把 `pages/market/index.tsx` 里的 `DEGRADED_REASON_TEXT` + `DegradedNotice` 原样搬到 `shared/ui/DegradedNotice.tsx`（文案一字不改，追加 `no_members: "成分数据尚未采集（每日 18:20 刷新）"` 与 `no_quotes` 复用）。
- [ ] **Step 2**：`pages/market/index.tsx` 改为 import；`npm run build` 通过（证明无循环依赖）；Commit `refactor(ui): extract DegradedNotice to shared/ui`

#### T13 概念详情页

**Files:** Create `frontend/src/pages/market-concept/index.tsx`；Modify `frontend/src/app/router/index.tsx`、`frontend/src/pages/market-hot-sectors/index.tsx`

- [ ] **Step 1**：页面骨架（两个独立 query；KPI 用 `Row/Col` + `Statistic`，不用新图表）

```tsx
export default function ConceptBoardPage() {
  const { boardCode = "" } = useParams();
  const detail = useQuery({ queryKey: ["concept-detail", boardCode], queryFn: () => fetchConceptDetail(boardCode), staleTime: 60_000 });
  const stocks = useQuery({ queryKey: ["concept-stocks", boardCode], queryFn: () => fetchConceptStocks(boardCode), enabled: Boolean(detail.data), staleTime: 60_000 });
  // 连板列：从 detail.data.echelons 拍平 {symbol: streak}，缺失渲染 `--`
  // KPI 四瓦片：板内涨停/最高板取自 detail.kpis；主力净流入取自 board.main_net_inflow（单位元 → 亿）；
  //   今日上涨占比 = board.up_count / (up+flat+down)（同一 items 口径，不新造指标）
  // 降级：detail.data?.degraded_reason → <DegradedNotice/>
  // unresolved_count>0 → Alert "另有 N 只成分股未收录（名录待刷新），未参与涨跌统计"
  // 成分表：<StockTable data={records} />（复用自选星标/排序/点击进个股）
  // 板内梯队：<LimitUpLadder echelons={detail.data?.echelons ?? []} degraded={Boolean(detail.data?.degraded_reason)} />
}
```

- [ ] **Step 2**：路由 `/market/concept/:boardCode`（懒加载，沿用既有写法）
- [ ] **Step 3**：`MarketHotSectorsPage` 表格加行点击跳转（仅概念分类生效，避免影响行业/地域既有行为）：

```tsx
onRow={(record) => ({
  style: { cursor: "pointer" },
  onClick: () => { if (activeCategory === "concept") navigate(`/market/concept/${record.code}`); },
})}
```

- [ ] **Step 4**：`npm run lint && npm run build`；手动验证 `/market/concept/BK0501` 渲染（成分表 162 行、板内梯队、KPI）
- [ ] **Step 5**：Commit `feat(concept): concept board detail page`

#### T14 个股详情「所属概念」

**Files:** Create `frontend/src/features/concept/components/ConceptTags.tsx`、`frontend/src/features/concept/index.ts`；Modify `frontend/src/pages/stock-detail/index.tsx`

- [ ] **Step 1**：组件（失败/空 → 返回 `null`，**零占位**）

```tsx
export function ConceptTags({ symbol }: { symbol: string }) {
  const [expanded, setExpanded] = useState(false);
  const { data } = useQuery({ queryKey: ["stock-concepts", symbol], queryFn: () => fetchConceptsBySymbol(symbol), staleTime: 300_000 });
  const items = data?.items ?? [];
  if (items.length === 0) return null;            // 失败/空 → 整块不渲染（e2e 断言 toHaveCount(0)）
  const shown = expanded ? items : items.slice(0, 8);
  return (
    <Space size={4} wrap data-testid="concept-tags">
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>所属概念</Typography.Text>
      {/* Tag: `{name} {pct != null ? `${pct>0?"+":""}${pct.toFixed(2)}%` : ""}`，色 = 涨跌色，
          点击 → navigate(`/market/concept/${board_code}`)；超出显示 `+N` / `收起` */}
    </Space>
  );
}
```

- [ ] **Step 2**：在 `UserTags` 之后挂载（与既有两块同间距）

```tsx
      <div style={{ marginTop: 8 }}>
        <ConceptTags symbol={stock.symbol} />
      </div>
```

- [ ] **Step 3**：`npm run lint && npm run build`；手动验证个股页出现概念标签、点击跳转、abort 该请求时页面其余部分不受影响
- [ ] **Step 4**：Commit `feat(concept): show concept tags on stock detail`

#### T15 「次新股情绪」卡

**Files:** Create `frontend/src/features/concept/components/NewStockBoard.tsx`；Modify `frontend/src/features/market/components/index.ts`、`frontend/src/pages/market/index.tsx`

- [ ] **Step 1**：组件（KPI 由同一 `items` 聚合，不新造口径；`CheckableTag` 过滤连板）

```tsx
export function NewStockBoard({ data, degraded }: { data?: NewStocksResponse; degraded: boolean }) {
  const [onlyLimitUp, setOnlyLimitUp] = useState(false);
  const rows = (data?.items ?? []).filter((r) => (onlyLimitUp ? (r.streak ?? 0) >= 1 : true));
  // KPI 瓦片：次新涨停 / 未开板 / 平均涨跌幅 / 现价高于首日开盘
  // 表（轻量 antd Table，同 SwL3LimitUpBoard 行式版式）：名称 | 上市日 | 交易日数 | 涨跌幅 | 换手率 | 连板 | 成交额
  // 缺失渲染 `--`；`never_broken === null` 显示 `--`（不可判 ≠ 未开板）
  // 注脚：次新股 = 东财概念板块 BK0501 成分（上市 ≤1 年滚动）；「高于首日开盘」为替代口径，非破发
}
```

- [ ] **Step 2**：挂到 `SentimentTab`（「昨日涨停今日表现」之后，`Col span={24}`），使用 `SectionCard title="次新股情绪" asof={data?.as_of}`
- [ ] **Step 3**：`npm run lint && npm run build`；Commit `feat(concept): new-stock sentiment card in sentiment tab`

#### T16 e2e + 数据版图

**Files:** Create `frontend/e2e/conceptBoard.spec.ts`；Modify `frontend/src/features/market/components/DataCoverageMatrix.tsx`

- [ ] **Step 1**：e2e（沿用 `page.route` mock 风格，四段断言）

```ts
import { expect, test } from "@playwright/test";

/** 契约（§2.3）：missing ⇒ `--`；membership_as_of 必须上屏；概念标签失败时零占位。 */
const DETAIL = {
  as_of: "2026-09-18", membership_as_of: "2026-09-18", source: "em_clist", degraded_reason: null,
  board: { board_code: "BK0501", board_name: "次新股", member_count: 162, unresolved_count: 0,
    priced_count: 162, up_count: 157, flat_count: 0, down_count: 5, avg_pct: 4.66,
    main_net_inflow: 1234567890.0, main_net_ratio: 2.1, lead_stock_name: "某股",
    lead_stock_code: "601091", lead_stock_pct: 20.0,
    leaders: [{ symbol: "601091", name: "C沈鼓", change_percent: 20.0 }] },
  kpis: { zt_count: 9, max_streak: 3, leader_symbol: "601091", leader_name: "C沈鼓" },
  echelons: [{ streak: 3, label: "3连板", stocks: [{ symbol: "601091", name: "C沈鼓", streak: 3,
    days_span: 3, boards_in_window: 3, missing_days: 0, amount: 1.0e8,
    seal_time: null, seal_fund: null, break_count: null }] }],
  unresolved_count: 0, stock_count: 162,
};
const STOCKS = [
  { symbol: "601091", name: "C沈鼓", exchange: "Shanghai_Stocks", category: "stock",
    list_date: "2026-09-17", asof: "2026-05-08T00:00:00Z", latest_price: 20.8,
    change_percent: 20.0, turnover_rate: 89.08, circ_mv: 179664849567.0, amount: 2181187.373,
    latest_quote_date: "2026-09-18" },
  // 缺失值样本：无行情 → 涨跌幅/换手渲染 `--`，不得渲染 0
  { symbol: "688837", name: "C信诺维", exchange: "Shanghai_Stocks", category: "stock",
    list_date: null, asof: "2026-05-08T00:00:00Z", latest_price: null, change_percent: null,
    turnover_rate: null, circ_mv: null, amount: null, latest_quote_date: null },
];

test("概念详情页展示成分与板内梯队，并宣布成分口径", async ({ page }) => {
  await page.route("**/api/v1/concepts/BK0501/stocks", (r) => r.fulfill({ json: STOCKS }));
  await page.route("**/api/v1/concepts/BK0501", (r) => r.fulfill({ json: DETAIL }));
  await page.goto("/market/concept/BK0501");
  await expect(page.getByRole("heading", { name: "次新股" })).toBeVisible();
  await expect(page.getByText(/成分截至/)).toBeVisible();          // 历史口径声明必须在页面上
  await expect(page.getByText("未开板")).toBeVisible();
  await expect(page.getByText("C沈鼓")).toBeVisible();
});

test("概念标签请求失败时整块不渲染，页面其余部分正常", async ({ page }) => {
  await page.route("**/api/v1/exchanges/Shanghai_Stocks/stocks/600000/enriched", (r) =>
    r.fulfill({ json: { symbol: "600000", name: "浦发银行", exchange: "Shanghai_Stocks",
      category: "stock", asof: "2026-05-08T00:00:00Z", latest_price: 10.0, change_percent: 1.0,
      latest_quote_date: "2026-09-18" } }));
  await page.route("**/api/v1/concepts/by-symbol/**", (r) => r.abort());
  await page.goto("/stock/600000");
  await expect(page.getByTestId("concept-tags")).toHaveCount(0);  // 零占位
  await expect(page.getByRole("tab", { name: "概览" })).toBeVisible();
});

test("概念 tab 不再是空态，行点击进入概念详情", async ({ page }) => {
  await page.route("**/api/v1/market/hot-boards**", (r) => r.fulfill({ json: [
    { id: "concept-BK0501", name: "次新股", code: "BK0501", changePercent: 4.66,
      upCount: 157, flatCount: 0, downCount: 5,
      leaders: [{ symbol: "601091", name: "C沈鼓", changePercent: 20.0 }] }] }));
  await page.goto("/market/hot-sectors/concept");
  await expect(page.getByText("次新股")).toBeVisible();
  await page.getByRole("cell", { name: /次新股/ }).first().click();
  await expect(page).toHaveURL(/\/market\/concept\/BK0501/);
});

test("次新股情绪卡渲染 KPI 与替代口径注脚", async ({ page }) => {
  await page.route("**/api/v1/new-stocks", (r) => r.fulfill({ json: {
    as_of: "2026-09-18", membership_as_of: "2026-09-18", source: "em_clist",
    degraded_reason: null, board_code: "BK0501", board_name: "次新股",
    kpis: { up_count: 157, flat_count: 0, down_count: 5, unpriced_count: 0,
      limit_up_count: 9, unbroken_count: 3, above_first_open_count: 120, avg_pct: 4.66 },
    items: [{ symbol: "601091", name: "C沈鼓", exchange: "Shanghai_Stocks",
      list_date: "2026-09-17", listed_trade_days: 2, pct_chg: 20.0, close: 20.8,
      turnover_rate: 89.08, circ_mv: 179664849567.0, amount: 2181187.373,
      streak: 2, is_lu: true, never_broken: null, first_open: 13.0, above_first_open: true }] }));
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await expect(page.getByText("未开板")).toBeVisible();
  await expect(page.getByText(/非破发/)).toBeVisible();            // 替代口径必须声明
  await expect(page.getByText("--")).toBeVisible();               // never_broken=null → `--`，不得是 0/false
});
```

- [ ] **Step 2**：`DataCoverageMatrix` 加行 `概念板块成分 | 板块列表 + 成分股 | 每日 18:20 | 东财`；`SectorMoneyflowCard` 概念维度的 `extra` 加 `Tooltip`（「主力资金流为东财按净流入排序的 Top100 样本，非全量板块」）——两者都不改组件 props
- [ ] **Step 3**：`npm run lint && npm run test:e2e -- conceptBoard`（后端与前端服务需已起，见 §6）
- [ ] **Step 4**：Commit `test(concept): e2e for concept board + new-stock card`

#### T17（可选，可整块删除）成分变更的历史口径入口

**Files:** Modify `backend/app/api/v1/concepts.py`、`frontend/src/pages/market-concept/index.tsx`

- 只有在需要"最近新增/剔除的成分"时做：`GET /api/v1/concepts/{board_code}/changes?days=30` → 读 `concept_member_changes`，页脚加一个"成分变动"折叠列表（含日期/方向/名称）。
- **本期默认不做**（§0.3 决策：只做当日/向前）。`concept_member_changes` 表从第一天起照常写入，历史留到需要时再消费。

---

## 6. 验证与门禁

```bash
# 后端
cd backend && TUSHARE_TOKEN= uv run pytest          # 纯单测（CI 同口径，禁止靠本机 .env 掩盖）
uv run --extra dev ruff check . && uv run --extra dev mypy app

# 前端
cd frontend && npm run lint && npm run build
npm run test:e2e -- conceptBoard                    # 需 API/前端已起（E2E_BASE_URL 默认 localhost:3000）

# 收尾
bash scripts/self_review.sh                          # 改动多时加 --full
```

- **不需要跑 `scripts/bench.sh`**：本特性不触碰 Tier 1 纯计算热点（规则引擎/rollup/归一化 mapper），新 SQL 以计划守卫测试替代性能门禁。
- **实机验收清单**（逐条留证进 Changelog）：
  1. 采集：`boards≈504 / added≈50000 / failed_boards=0`；
  2. 对拍：`/api/v1/new-stocks` 的 `up_count/down_count` 与东财 `fs=b:BK0501` 的 `f104/f105` 数量级一致（差异仅停牌/无行情）；
  3. UI：概念 tab 非空、`/market/concept/BK0501` 渲染 162 行成分 + 板内梯队 + 「成分截至」声明、个股页出现概念标签、情绪 tab 出现次新股卡；
  4. 降级：`docker compose stop scheduler` 跨过 18:20 再 start → `misfire_grace_time=None` 补跑，`last_seen_on` 更新到当日。

## 7. 风险与边界

| 风险 | 影响 | 缓解 |
|---|---|---|
| 东财 `clist` 字段/参数漂移 | 采集静默落空 | 客户端注释固化实测日期与字段；`_paged_clist` 空页收敛 + `member_count=0` 视为异常记 warning；`unresolved_count`/`failed_boards` 进任务 result 可观测 |
| 800-1000 请求/日被限流或半途失败 | 成分不全 | 逐板失败隔离（不删成员）；`last_seen_on` 天然区分"今天抓到的"与"陈旧的"；`membership_as_of` 按板块取值，陈旧板块在 UI 上可见 |
| 名录仍滞后（T0 未做/新上市） | 部分成分无 `stock_id`，不参与聚合 | 设计上已容忍：仍落库、以 `unresolved_count` 暴露、UI 显式提示；`universe_refresh_job` 每周末补 |
| 概念成分是"当前快照"，无历史 | 任何历史回算都有前视/幸存者偏差 | 本期明确只做当日/向前；响应强制回传 `membership_as_of`，UI 强制显示"成分截至"；差分表从第一天起积累真历史 |
| 资金流快照只覆盖 Top100 震荡集 | 部分板块 `main_net_inflow` 为 null | 字段允许 null，UI 渲染 `--`；**不用 0 填充**；文案注明是东财样本 |
| 首日新股 `stk_limit` 哨兵值（99999.999） | 误判涨停/污染均值 | 现有判定 `close >= up_limit - 0.005` 对哨兵天然不命中（实测安全）；次新股卡以 `never_broken`/`above_first_open` 表达，不用首日 `pct_chg` 均值做 KPI；**禁止**把东财 `f3` 与 TuShare `pct_chg` 混进同一曲线 |
| 概念成分表 5 万行，聚合 SQL 走错计划 | 概念页变慢 | `concept_members(board_code)` 唯一键前缀 + `(stock_id)` 索引；计划守卫测试（T3 Step 4）钉住"先收敛再回查" |
| 与既有情绪卡口径不一致 | 用户看到两个"涨停家数" | 板内涨停/梯队**复用同一份 `get_snapshot()`**（T8），不重算 streak；同源即同口径 |

## 8. 交叉引用

- 既有数据源实测记录：`docs/design/data-source.md` §七（东财 clist 字段/单位、`pz` 行为）
- 情绪口径与降级矩阵：`docs/design/limit-up-sentiment.md`（本特性复用其 `local_calc` 口径与 `degraded_reason` 集合）
- 名录冻结事故与对账：`docs/Changelog.md` 2026-09-17 条 + `plans/2026-09-17-data-sync-self-healing.md`
- 经验沉淀：`docs/references/best-practices.md` 一、数据源与采集（东财字段实测、缺失≠零、映射表不刷新的静默丢失）
- 部署与运维：`docs/build.md`（scheduler/worker 容器、手动触发端点）
