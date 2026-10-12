# S3 资讯地基（Wave 2）

> 本文件是**契约**，不是排期。执行清单见主线计划 §7 Wave 2。
> **为什么单独一个 Wave**：资讯是 `行业报告对比表(S4.2)` · `公司 5 张卡(S4.6)` · `公司风险红线(S4.5)` · `宏观政策视图(S4.10)` · `事件驱动选股(S6.1)` · `价值网产业链边抽取(S5.3)` 的**共同上游**。不做它，Wave 3 起全部落空。

**owner 对资讯的定义（产品原则，写在最前面）**：
> **带状态的时序文本信息，尽可能追求数据实时性、来源可溯性。**

三个必需属性拆开：
- **带状态** → 状态机，且是**用户维度**
- **时序** → `published_at`（发布）+ `ingested_at`（入库）**两个**时间戳，不是一个
- **来源可溯** → 原文 URL + 发布主体 + `publisher_tier`

**两条抽取原则（来自 owner 批注）**
- 定期报告「**仅显示核心信息，主营业务收入、利润**」
- 券商研报「**仅显示核心观点**」
→ 列表里展示的是**抽取后的结构化字段**，原文是后备。**本模块的成败取决于抽取管线，不是采集管线。**

---

## S3.1 内容事实层四表

**范围**：做完之后，任何新内容源（公告/研报/政策/快讯/协会/第三方）都有同一个家，且同一批内容能支撑多个视图。

**这是 Wave 2 的拱心石**：S3.2–S3.7 全部依赖它。

**交付物**：Alembic 迁移（4 张表）+ `backend/app/models/content.py`

**契约**

```sql
-- ① 内容事实层（只由采集/抽取写入）
content_items(
  id             bigserial primary key,
  content_type   text not null,   -- announcement | report | policy | news | association | third_party | inquiry
  source_name    text not null,   -- 具体来源，如 'cninfo' / 'sse' / 'gov.cn'
  publisher_tier text not null,   -- legal | official | sellside | media（见 S0.4 §4.3；勿与 industry_metrics.source_tier 混）
  publisher      text,            -- 发布主体（公司名 / 部委名 / 券商名）
  title          text not null,
  published_at   timestamptz not null,  -- ★ 发布时点
  ingested_at    timestamptz not null default now(),  -- ★ 入库时点（两者不同！）
  url            text not null,
  raw_hash       text not null,   -- 去重键
  body_ref       text,            -- 原文存储引用（对象存储/文件路径），不塞 DB
  extracted      jsonb not null default '{}',
  extract_version int,
  extract_confidence numeric(4,3),
  review_status  text not null default 'auto',  -- auto | pending | confirmed | rejected
  created_at     timestamptz not null default now(),
  unique (source_name, raw_hash)
)
create index on content_items (content_type, published_at desc);
create index on content_items (published_at desc);
create index on content_items (review_status) where review_status <> 'auto';

-- ② 内容 ↔ 实体
content_entities(
  content_id  bigint not null references content_items(id) on delete cascade,
  entity_type text not null,   -- stock | industry | policy_tag | concept
  entity_id   text not null,
  primary key (content_id, entity_type, entity_id)
)

-- ③ 从内容抽取的规范化指标（★ 对比表的原料）
content_metrics(
  id          bigserial primary key,
  content_id  bigint not null references content_items(id) on delete cascade,
  entity_type text not null,
  entity_id   text not null,
  metric_key  text not null,   -- 如 'revenue' / 'net_profit' / 'core_view'
  period      text,            -- 报告期，如 '2026H1'
  value       numeric(18,4),
  value_text  text,            -- 非数值型（如"核心观点"）
  unit        text,
  source_span jsonb not null,  -- ★ 原文定位：{page, char_start, char_end, quote}
  created_at  timestamptz not null default now(),
  unique (content_id, entity_id, metric_key, period)
)

-- ④ 用户 × 内容状态（★ 状态必须归用户维度）
user_content_state(
  user_id    uuid not null,
  content_id bigint not null references content_items(id) on delete cascade,
  status     text not null default 'new',  -- new | read | processed | ignored | archived
  note       text,
  updated_at timestamptz not null default now(),
  primary key (user_id, content_id)
)
```

**硬性约束（违反任一条即为设计事故）**
1. **`status` 不得写在 `content_items` 上**——多用户下"已读"是个人状态。写在事实层会导致 A 读完 B 就看不到。
2. **`source_span` 必填**（数值型抽取）——没有原文定位就没有溯源，等于没抽。
3. **`url` 必填**——每条内容必须能点回原文。
4. **`published_at` 与 `ingested_at` 都必须有**——两者之差是数据新鲜度的唯一度量。
5. **`review_status` 默认 `auto`**，人工裁定后置为 `confirmed` / `rejected`，**人工结果永久优先于自动结果**。

**不做什么**：不做全文检索（先按类型+时间+实体查）；不存原文正文到 DB（只存 `body_ref`）。

**依赖**：S0.1（ADR-1 原料/视图分离）
**验收判据**
- 数据契约：4 张表存在；`unique(source_name, raw_hash)` 生效（同一原文重放不新增行）
- 数据契约：`content_items` 上**不存在** `read_at` / `status` 之类用户状态列（有测试锁定 schema）
- 行为：同一用户标已读后，另一用户看到的仍是 `new`
- 行为：任一 `content_metrics` 行都能通过 `source_span` 在原文中定位到对应片段

---

## S3.2 上市公司公告采集

**范围**：做完之后，用户可以按类型看到全部上市公司公告，且能点回原文。

**交付物**：`app/services/content_ingest/announcement.py` + `QUEUES` 注册 `"content.announcement.fetch"` + APScheduler 定时 + Worker 手动触发（双轨共用同一 ingest 方法）

**契约**
- 四类：定期报告（年报 / 半年报 / 季报）· 临时公告（重大事项 / 增减持 / 分红送转）· 业绩预告与快报 · 问询函与回复
- `publisher_tier = legal`
- 去重：`raw_hash`（正文哈希，非标题哈希——标题相同正文可能不同）
- 增量：按 `published_at` 水位 + 内容哈希两层门控（**复用既有玄田 ingest 的三层门控模式**）
- `content_entities`：定期报告必须解析出对应 `stock`

**不做什么**：不做公告正文的完整 PDF 解析（只抽 §S3.4 约定的字段）；不做历史全量回补（先保证增量不丢，回补另立 spec）。

**依赖**：S3.1
**验收判据**
- 数据契约：重跑幂等（二次 ingest 新增行数 = 0）
- 数据契约：连抓 5 个交易日，**每日公告条数有对账**（缺失即告警，不能静默为 0）
- 行为：随机抽 30 条，`url` 全部可达且指向正确公告
- 行为：定期报告类内容的 `content_entities` 能正确解析到 `stock`

---

## S3.3 财经快讯采集

**范围**：做完之后，用户能看到分钟级的财经快讯。

**交付物**：`app/services/content_ingest/news.py` + `QUEUES` 注册 + 定时（分钟级）
**契约**：`publisher_tier = media`；`published_at` 精确到分钟；高频去重（同一事件多家转载需合并或标记）
**依赖**：S3.1
**验收判据**
- 数据契约：`published_at` 与 `ingested_at` 之差的中位数 **< 10 分钟**（实时性的机械度量）
- 行为：同一事件的转载被标记或合并，不刷屏
- 行为：采集失败有对账告警（沿用既有 `reconciliation_service` 模式）

---

## S3.4 LLM 抽取管线与溯源

**范围**：做完之后，用户看到的是「主营收入 / 利润 / 核心观点」这样的结构化字段，而不是原文。

**这是 Wave 2 的决定性环节**——列表质量 = 抽取质量。

**交付物**
- `app/services/extraction/` ：抽取器（按 `content_type` 分派）
- `extract_version` 版本管理 + 重抽能力
- 抽取质量评估脚本（人工核对样本）

**契约**

| `content_type` | 抽取目标 | `metric_key` |
|---|---|---|
| 定期报告 | 主营业务收入 · 利润 | `revenue` · `net_profit` |
| 券商研报 | 核心观点（文本） | `core_view`（`value_text`）|
| 临时公告 | 事件类型 + 关键要素（增减持数量/金额、分红比例）| `event_*` |
| 政策 | 政策类型 + 影响面（行业/板块）| `policy_*` |

**硬性约束**
1. **每条抽取必须带 `source_span`**（页码 + 字符区间 + 原文 quote）——UI 可点开验原
2. **必须产出 `extract_confidence`**；低于阈值的自动置 `review_status='pending'` 进人工队列
3. **`extract_version` 必填**，支持上游 prompt/模型变更后的重抽与对比
4. **抽取结果不得覆盖原文**：`extracted` 与 `body_ref` 并存

**不做什么**：不做开放式摘要（LLM 自由发挥）；只抽约定字段。**"核心观点"是唯一允许的自由文本字段**，且必须有 quote 支撑。

**依赖**：S3.1、S3.2
**验收判据**
- 数据契约：抽取出的数值与原文一致 —— **抽样 ≥30 条人工核对，一致率必须 ≥95%**（未达标不得进入 Wave 3）
- 数据契约：`source_span` 非空率 = 100%；随机抽 20 条能正确定位原文
- 行为：`extract_version` bump 后可重抽且旧结果保留可对比
- 行为：低置信度样本自动进 `pending` 队列

---

## S3.5 人工裁定队列（**只服务本模块，不做通用化**）

**范围**：做完之后，抽取拿不准的内容有人来定，且人的判断永久优先。

**交付物**
- `content_items.review_status` 状态机 + 一个可用的裁定界面（`/admin/review`）
- overlay 种子文件（**沿用 [ADR 0005](../../docs/decisions/0005-overlay-seed-files.md)：人工策展数据用 overlay，不改自动生成的种子**）

**契约**
- 状态：`auto`（自动判定，未复核）→ `pending`（待裁定）→ `confirmed` / `rejected`（人工终态）
- 进 `pending` 的触发条件：抽取置信度低于阈值 · 实体解析歧义 · 研报"有效性"边界情形
- **人工终态优先级最高**：任何后续自动任务不得覆盖 `confirmed` / `rejected` 行
- 裁定结果落 overlay 种子，可随仓库版本化

**不做什么（评审收缩）**
- **不做"服务 4 个消费方的通用裁定队列"**。原方案要覆盖 行业分类 / 概念板块 / 产业链环节 / 研报有效性 四处——后三处分别是 Wave 2/3/4 的事，现在通用化即是**为不存在的需求写框架**。
- **分类未命中的兜底不走这里**：复用既有 `concept.py:3` 的模式（稳定业务键 + 可空外键 + `unresolved_count` 外露），见 [S0.4 §4.6](./S0-platform-contracts.md#46-其他分类兜底原图的日期是数据事实不是业务规则)。
- 不做多人协作 / 权限分级（先单管理员）。

**依赖**：S3.1、S3.4
**验收判据**
- 数据契约：自动任务重跑后，`confirmed` / `rejected` 行的 `extracted` 与 `review_status` **均不变**（有测试锁定）
- 数据契约：**本 spec 不新增表**（状态位在 `content_items` 上）
- 行为：`pending` 队列可按类型筛选、可批量裁定、裁定后样本不再回到队列
- 行为：裁定结果持久化到 overlay 种子，重启后仍在

---

## S3.6 资讯视图层

**范围**：做完之后，用户能在四类资讯里浏览、筛选、标记状态，并且每个结论都能点回原文。

**交付物**：`/news`（四类 × 子类）+ 状态机交互 + 溯源面板

**契约**
- 四类：上市公司公告 · 政府官媒政策资讯 · 研究资讯 · 市场资讯
- 列表默认排序：`publisher_tier` 权重（`legal > official > sellside > media`）**然后** `published_at desc`
- 每条显示：标题 · 发布主体 · `published_at` · `publisher_tier` 徽章 · 抽取出的核心字段 · **原文链接**
- 状态操作：未读 / 已读 / 已处理 / 忽略（写 `user_content_state`）
- 筛选项：类型 · 时间 · 实体（股票/行业）· 状态 · 来源
- **研报类必须显示 `core_view` 与原文 quote**

**依赖**：S3.4
**验收判据**
- 行为：四类 × 子类全部可访问，每条有原文链接（点击可达率 100%，抽样 50 条）
- 行为：状态筛选生效，多用户互不干扰
- 行为：研报列表显示核心观点，可展开看原文 quote 与页码
- 行为：列表首屏加载 < 2s（在合理数据量下）
- 门禁：`npx tsc --noEmit` + `npm run build`

---

## S3.7 「有效研报」判定规则

**范围**：做完之后，"有效研报"有明确定义，且定义可以被人工推翻。

**背景**：owner 图上写的是「券商或机构**有效**研报」——"有效"必须被定义，否则研报列表会变成垃圾场。

**契约（三条判据，全部显式且可解释）**

| 判据 | 默认规则 | 可否人工推翻 |
|---|---|---|
| **去重** | 同一券商对同一标的的同一评级变动，只留最新一份 | 是 |
| **行业相关** | 标题/摘要命中该行业关键词，或 `content_entities` 已关联该行业 | 是 |
| **有数据支撑** | 含至少一个可抽取的量化字段（目标价/盈利预测/评级）| 是 |

- 判据与阈值必须**配置化**（进 registry），不硬编码
- 被判为"无效"的研报不是删除，而是**标记 + 可查**（`review_status`），人工可翻案

**依赖**：S3.4、S3.5
**验收判据**
- 行为：给定一批研报，三条判据的判定结果可逐条解释（说明命中了哪条/为什么被排除）
- 行为：被判无效的研报可通过人工裁定恢复为有效，且持久生效
- 行为：判据阈值改动后无需改代码（配置驱动）

---

## Wave 2 整体完成判据

- [ ] 4 张内容表上线；**用户状态不在事实层**（schema 测试锁定）
- [ ] 公告 + 快讯两个源稳定采集，5 个交易日对账无缺口
- [ ] 抽取一致率 ≥95%（≥30 条人工核对），`source_span` 非空率 100%
- [ ] 每条内容可点回原文（抽样 50 条可达率 100%）
- [ ] 人工裁定队列可用，且人工终态不被自动任务覆盖（测试锁定）
- [ ] 「有效研报」三条判据配置化且有解释
- [ ] `bash scripts/doc_gate.sh`（新增 QUEUES 已登记）+ `bash scripts/self_review.sh --full` 全绿
