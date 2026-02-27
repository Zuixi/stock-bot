# 股票数据分析系统 - 数据库架构设计文档

> 版本：1.0 | 日期：2026-02-27

---

## 1. 项目背景与里程碑

| 里程碑 | 状态 | 核心能力 |
|--------|------|----------|
| M0 | 已完成 | 三大交易所股票列表抓取、规范化、JSONL 落盘 |
| M1 | 规划中 | 日频行情抓取、增量更新 |
| M2 | 规划中 | 特征工程、聚类分析 |
| M3 | 规划中 | LLM 聚类解释 |
| M4 | 规划中 | 定时任务、全量覆盖 |

**技术栈**：FastAPI + PostgreSQL + Redis + RabbitMQ + Docker（后端）；React 18 + TypeScript + Ant Design + ECharts/Recharts + Zustand + React Query + Vite（前端）

---

## 2. PostgreSQL 数据库 Schema 设计

### 2.1 核心表 DDL

#### 2.1.1 交易所与股票池

```sql
-- 交易所维度表（可选，用于枚举约束与扩展）
CREATE TABLE dim_exchange (
    id          SMALLINT PRIMARY KEY,
    code        VARCHAR(32) NOT NULL UNIQUE,  -- Shanghai_Stocks, Shenzen_Stocks, Beijing_Stocks
    name_cn     VARCHAR(64) NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO dim_exchange (id, code, name_cn) VALUES
(1, 'Shanghai_Stocks', '上海证券交易所'),
(2, 'Shenzen_Stocks', '深圳证券交易所'),
(3, 'Beijing_Stocks', '北京证券交易所');

-- 股票主表（当前有效快照，用于查询）
CREATE TABLE stocks (
    id              BIGSERIAL PRIMARY KEY,
    exchange        VARCHAR(32) NOT NULL,
    symbol          VARCHAR(16) NOT NULL,
    name            VARCHAR(64) NOT NULL,
    full_name       VARCHAR(256),
    category        VARCHAR(128) NOT NULL,
    list_date       DATE,
    csrc_code       VARCHAR(16),
    csrc_desc       VARCHAR(128),
    province        VARCHAR(64),
    status          VARCHAR(32),
    asof            TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (exchange, symbol)
);

CREATE INDEX idx_stocks_exchange ON stocks(exchange);
CREATE INDEX idx_stocks_category ON stocks(exchange, category);
CREATE INDEX idx_stocks_symbol ON stocks(symbol);
CREATE INDEX idx_stocks_asof ON stocks(asof);

COMMENT ON TABLE stocks IS '股票主表，存储当前有效快照';

-- 股票历史快照表（用于审计与回溯）
CREATE TABLE stocks_history (
    id              BIGSERIAL PRIMARY KEY,
    exchange        VARCHAR(32) NOT NULL,
    symbol          VARCHAR(16) NOT NULL,
    name            VARCHAR(64) NOT NULL,
    full_name       VARCHAR(256),
    category        VARCHAR(128) NOT NULL,
    list_date       DATE,
    csrc_code       VARCHAR(16),
    csrc_desc       VARCHAR(128),
    province        VARCHAR(64),
    status          VARCHAR(32),
    source_url      TEXT,
    asof            TIMESTAMPTZ NOT NULL,
    raw             JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_stocks_history_exchange_symbol_asof ON stocks_history(exchange, symbol, asof DESC);
CREATE INDEX idx_stocks_history_asof ON stocks_history(asof);

COMMENT ON TABLE stocks_history IS '股票历史快照，用于审计与迁移回溯';
```

#### 2.1.2 日频行情数据（分区表）

```sql
-- 日频行情主表（按交易日期分区）
CREATE TABLE daily_quotes (
    id              BIGSERIAL,
    stock_id        BIGINT NOT NULL,
    trade_date      DATE NOT NULL,
    open            NUMERIC(12, 4),
    high            NUMERIC(12, 4),
    low             NUMERIC(12, 4),
    close           NUMERIC(12, 4) NOT NULL,
    volume          BIGINT,
    amount          NUMERIC(20, 2),
    adj_factor      NUMERIC(12, 6) DEFAULT 1.0,
    source          VARCHAR(32),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, trade_date),
    UNIQUE (stock_id, trade_date)
) PARTITION BY RANGE (trade_date);

-- 分区示例：按年分区，可按需创建
CREATE TABLE daily_quotes_2023 PARTITION OF daily_quotes
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE daily_quotes_2024 PARTITION OF daily_quotes
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE daily_quotes_2025 PARTITION OF daily_quotes
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE daily_quotes_2026 PARTITION OF daily_quotes
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE INDEX idx_daily_quotes_stock_date ON daily_quotes(stock_id, trade_date DESC);
CREATE INDEX idx_daily_quotes_date ON daily_quotes(trade_date);

COMMENT ON TABLE daily_quotes IS '日频行情，按交易日期分区';
```

#### 2.1.3 特征工程数据

```sql
-- 特征元数据（窗口、指标定义）
CREATE TABLE feature_definitions (
    id              SERIAL PRIMARY KEY,
    code            VARCHAR(64) NOT NULL UNIQUE,
    name_cn         VARCHAR(128),
    window_days     INT NOT NULL,
    formula         TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 特征值表（按计算日期分区，可选）
CREATE TABLE stock_features (
    id              BIGSERIAL,
    stock_id        BIGINT NOT NULL,
    asof_date       DATE NOT NULL,
    window_days     INT NOT NULL,
    -- 收益类
    total_return    NUMERIC(12, 6),
    return_percentile NUMERIC(8, 4),
    -- 风险类
    annual_volatility NUMERIC(12, 6),
    max_drawdown    NUMERIC(12, 6),
    downside_vol    NUMERIC(12, 6),
    -- 趋势类
    trend_slope     NUMERIC(12, 6),
    trend_r2        NUMERIC(8, 4),
    ma_bullish      BOOLEAN,
    trend_reversals INT,
    -- 流动性类
    avg_volume      NUMERIC(20, 2),
    volume_volatility NUMERIC(12, 6),
    -- 扩展字段
    extra           JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, asof_date),
    UNIQUE (stock_id, asof_date, window_days)
) PARTITION BY RANGE (asof_date);

CREATE TABLE stock_features_2024 PARTITION OF stock_features
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE stock_features_2025 PARTITION OF stock_features
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE stock_features_2026 PARTITION OF stock_features
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');

CREATE INDEX idx_stock_features_stock_date ON stock_features(stock_id, asof_date DESC);
CREATE INDEX idx_stock_features_window ON stock_features(asof_date, window_days);

COMMENT ON TABLE stock_features IS '特征工程结果，窗口 20/60/120 交易日';
```

#### 2.1.4 聚类与版本控制

```sql
-- 聚类运行元数据（版本控制）
CREATE TABLE clustering_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(128),
    algorithm       VARCHAR(32) NOT NULL,
    params          JSONB NOT NULL,
    asof_date       DATE NOT NULL,
    window_days     INT NOT NULL,
    n_clusters      INT,
    silhouette      NUMERIC(8, 4),
    metrics         JSONB,
    status          VARCHAR(32) DEFAULT 'completed',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_clustering_runs_asof ON clustering_runs(asof_date DESC);
CREATE INDEX idx_clustering_runs_created ON clustering_runs(created_at DESC);

COMMENT ON TABLE clustering_runs IS '聚类运行版本，支持多版本共存';

-- 聚类成员（股票 -> 聚类标签）
CREATE TABLE clustering_members (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES clustering_runs(id) ON DELETE CASCADE,
    stock_id        BIGINT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    cluster_label   INT NOT NULL,
    distance        NUMERIC(12, 6),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (run_id, stock_id)
);

CREATE INDEX idx_clustering_members_run ON clustering_members(run_id);
CREATE INDEX idx_clustering_members_stock ON clustering_members(stock_id);
CREATE INDEX idx_clustering_members_label ON clustering_members(run_id, cluster_label);

COMMENT ON TABLE clustering_members IS '聚类成员关系';
```

#### 2.1.5 LLM 解释

```sql
-- 聚类解释（LLM 生成）
CREATE TABLE cluster_explanations (
    id              BIGSERIAL PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES clustering_runs(id) ON DELETE CASCADE,
    cluster_label   INT NOT NULL,
    summary         TEXT NOT NULL,
    input_summary   TEXT,
    model_version   VARCHAR(64),
    disclaimer      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (run_id, cluster_label)
);

CREATE INDEX idx_cluster_explanations_run ON cluster_explanations(run_id);

COMMENT ON TABLE cluster_explanations IS 'LLM 聚类解释，含输入摘要与免责声明';
```

#### 2.1.6 任务与抓取状态

```sql
-- 异步任务表
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            VARCHAR(64) NOT NULL,
    payload         JSONB,
    status          VARCHAR(32) DEFAULT 'pending',
    progress        INT DEFAULT 0,
    result          JSONB,
    error           TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_type ON tasks(type);
CREATE INDEX idx_tasks_created ON tasks(created_at DESC);

COMMENT ON TABLE tasks IS '异步任务状态，对接 RabbitMQ Worker';
```

### 2.2 主键、外键与索引策略

| 表名 | 主键 | 外键 | 索引策略 |
|------|------|------|----------|
| stocks | id (BIGSERIAL) | - | exchange, category, symbol, asof |
| stocks_history | id | - | (exchange, symbol, asof DESC) |
| daily_quotes | (id, trade_date) | stock_id | (stock_id, trade_date), trade_date |
| stock_features | (id, asof_date) | stock_id | (stock_id, asof_date), (asof_date, window_days) |
| clustering_runs | UUID | - | asof_date, created_at |
| clustering_members | id | run_id, stock_id | run_id, stock_id, (run_id, cluster_label) |
| cluster_explanations | id | run_id | run_id |
| tasks | UUID | - | status, type, created_at |

### 2.3 分区策略

- **daily_quotes**：按 `trade_date` 年分区，便于历史数据归档与查询裁剪
- **stock_features**：按 `asof_date` 年分区，与行情分区对齐
- 分区创建脚本可自动化，根据当前年份动态创建下一年分区

### 2.4 版本控制策略（聚类）

- `clustering_runs` 存储每次聚类运行的元数据，`id` 为 UUID，支持多版本共存
- 前端通过 `asof_date` + `created_at` 或 `name` 选择展示版本
- 建议增加 `is_default` 布尔字段，标记当前默认展示版本

### 2.5 数据完整性约束

```sql
-- 检查约束示例
ALTER TABLE stocks ADD CONSTRAINT chk_exchange
    CHECK (exchange IN ('Shanghai_Stocks', 'Shenzen_Stocks', 'Beijing_Stocks'));

ALTER TABLE daily_quotes ADD CONSTRAINT chk_ohlc
    CHECK (high >= low AND open >= 0 AND close >= 0);

ALTER TABLE tasks ADD CONSTRAINT chk_status
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'));
```

---

## 3. Redis 缓存策略

### 3.1 缓存 Key 命名规范

```
{domain}:{entity}:{identifier}:{suffix}
```

- `domain`：业务域，如 `stock`、`quote`、`cluster`、`task`
- `entity`：实体类型
- `identifier`：主键或组合键
- `suffix`：可选，如 `v1`、`meta`

### 3.2 缓存内容与 TTL

| Key 模式 | 说明 | TTL | 示例 |
|----------|------|-----|------|
| `stock:list:{exchange}:{category}:{page}` | 股票列表分页 | 5 min | `stock:list:Shanghai_Stocks:STOCK_TYPE_1:1` |
| `stock:detail:{symbol}` | 单只股票详情 | 1 hour | `stock:detail:600105` |
| `quote:kline:{symbol}:{start}:{end}` | K 线数据 | 10 min | `quote:kline:600105:20250101:20250227` |
| `cluster:run:{run_id}` | 聚类运行元数据 | 1 hour | `cluster:run:uuid-xxx` |
| `cluster:members:{run_id}:{label}` | 聚类成员列表 | 30 min | `cluster:members:uuid:0` |
| `cluster:dist:{run_id}` | 聚类分布统计 | 30 min | `cluster:dist:uuid` |
| `feature:radar:{symbol}:{asof_date}` | 特征雷达数据 | 10 min | `feature:radar:600105:20250227` |
| `task:status:{task_id}` | 任务实时状态 | 任务结束后 1 hour | `task:status:uuid` |

### 3.3 缓存失效触发

| 场景 | 失效动作 |
|------|----------|
| 股票池快照更新 | 删除 `stock:list:*`、`stock:detail:*` |
| 行情数据写入 | 删除 `quote:kline:{symbol}:*` |
| 特征重算 | 删除 `feature:radar:{symbol}:*` |
| 聚类重跑 | 删除 `cluster:*` 相关 key |
| 任务完成/失败 | 更新 `task:status:{id}`，设置短期 TTL |

### 3.4 实现建议

- 使用 Redis Hash 存储复杂对象，减少序列化开销
- 列表类数据使用 JSON 序列化，便于前端直接消费
- 考虑使用 Redis Stream 做任务进度推送（可选）

---

## 4. FastAPI 接口设计（RESTful）

### 4.1 核心 API 端点

#### 股票池

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/stocks` | 股票列表（分页、过滤） |
| GET | `/api/v1/stocks/{symbol}` | 单只股票详情 |
| GET | `/api/v1/exchanges` | 交易所列表 |
| GET | `/api/v1/categories` | 按交易所返回类别列表 |

#### 行情

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/quotes/{symbol}/daily` | 日频 K 线（时间范围过滤） |
| GET | `/api/v1/quotes/{symbol}/latest` | 最新行情 |

#### 特征

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/features/{symbol}` | 某股票特征（含雷达图数据） |
| GET | `/api/v1/features/{symbol}/history` | 特征历史序列 |

#### 聚类

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/clusters/runs` | 聚类运行版本列表 |
| GET | `/api/v1/clusters/{run_id}` | 聚类运行详情 |
| GET | `/api/v1/clusters/{run_id}/distribution` | 聚类分布统计 |
| GET | `/api/v1/clusters/{run_id}/members/{label}` | 某聚类成员股票 |
| GET | `/api/v1/clusters/{run_id}/explanations` | 聚类 LLM 解释 |

#### 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/tasks/fetch-universe` | 触发股票池抓取 |
| POST | `/api/v1/tasks/fetch-quotes` | 触发行情抓取 |
| POST | `/api/v1/tasks/run-clustering` | 触发聚类任务 |
| GET | `/api/v1/tasks/{task_id}` | 任务状态与进度 |

### 4.2 分页与过滤参数规范

**分页**（通用）：
```
?page=1&page_size=20
```
响应：
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

**股票列表过滤**：
```
GET /api/v1/stocks?exchange=Shanghai_Stocks&category=STOCK_TYPE_1_主板A股&page=1&page_size=20
```

**K 线时间范围**：
```
GET /api/v1/quotes/600105/daily?start=2025-01-01&end=2025-02-27
```

### 4.3 请求/响应示意

**GET /api/v1/stocks**
```json
// Response
{
  "items": [
    {
      "id": 1,
      "exchange": "Shanghai_Stocks",
      "symbol": "600105",
      "name": "永鼎股份",
      "category": "STOCK_TYPE_1_主板A股",
      "list_date": "1997-09-29"
    }
  ],
  "total": 1500,
  "page": 1,
  "page_size": 20
}
```

**GET /api/v1/quotes/600105/daily**
```json
// Response
{
  "symbol": "600105",
  "data": [
    {"date": "2025-02-27", "open": 5.12, "high": 5.25, "low": 5.08, "close": 5.20, "volume": 1234567, "amount": 6400000}
  ]
}
```

**POST /api/v1/tasks/fetch-universe**
```json
// Request
{"exchange": "sse", "stock_type": "1"}

// Response
{"task_id": "uuid-xxx", "status": "pending"}
```

---

## 5. RabbitMQ 消息队列设计

### 5.1 Exchange 与 Queue 命名

| 类型 | 名称 | 说明 |
|------|------|------|
| Exchange | `stock_bot.topic` | 主题交换机 |
| Queue | `stock_bot.universe.fetch` | 股票池抓取 |
| Queue | `stock_bot.quotes.fetch` | 行情抓取 |
| Queue | `stock_bot.features.compute` | 特征计算 |
| Queue | `stock_bot.clustering.run` | 聚类任务 |
| Queue | `stock_bot.llm.explain` | LLM 解释 |

### 5.2 消息结构

```json
{
  "task_id": "uuid",
  "type": "fetch_universe",
  "payload": {
    "exchange": "sse",
    "stock_type": "1"
  },
  "created_at": "2026-02-27T10:00:00Z"
}
```

### 5.3 消费者（Worker）角色划分

| Worker | 消费队列 | 职责 |
|--------|----------|------|
| universe_worker | stock_bot.universe.fetch | 调用 fetcher，写入 PostgreSQL，更新 Redis |
| quotes_worker | stock_bot.quotes.fetch | 拉取日频行情，增量写入 daily_quotes |
| features_worker | stock_bot.features.compute | 读取行情，计算特征，写入 stock_features |
| clustering_worker | stock_bot.clustering.run | 读取特征，执行聚类，写入 clustering_* |
| llm_worker | stock_bot.llm.explain | 读取聚类结果，调用 LLM，写入 cluster_explanations |

### 5.4 路由 Key 规范

```
stock_bot.universe.{exchange}
stock_bot.quotes.{symbol}
stock_bot.features.{asof_date}
stock_bot.clustering.{run_id}
stock_bot.llm.{run_id}
```

---

## 6. 数据迁移策略

### 6.1 JSONL → PostgreSQL 迁移流程

1. **扫描快照目录**：遍历 `data/universe/snapshot=*/`，解析 `manifest.json`
2. **选择最新快照**：按 `asof` 取最新，或由用户指定
3. **逐文件读取**：按 `{exchange}/class=*.jsonl` 读取，每行解析为 `StockRecord`
4. **批量插入**：
   - 先插入 `stocks_history`（保留原始）
   - 再 upsert 到 `stocks`（以 exchange+symbol 为唯一键，取最新 asof）

### 6.2 幂等性保证

- **stocks**：`ON CONFLICT (exchange, symbol) DO UPDATE SET ...`，根据 asof 决定是否更新
- **stocks_history**：每次插入新记录，不冲突
- **daily_quotes**：`ON CONFLICT (stock_id, trade_date) DO UPDATE`，覆盖写入
- **迁移脚本**：支持 `--dry-run`，可重复执行，相同输入产生相同结果

### 6.3 迁移脚本伪代码

```python
def migrate_universe_snapshot(snapshot_path: Path) -> int:
    manifest = load_manifest(snapshot_path / "manifest.json")
    asof = manifest["asof"]
    count = 0
    for exchange_dir in snapshot_path.iterdir():
        if not exchange_dir.is_dir():
            continue
        for jsonl_file in exchange_dir.glob("class=*.jsonl"):
            for line in jsonl_file.read_text().strip().split("\n"):
                record = StockRecord.model_validate_json(line)
                upsert_stock(record)
                insert_stocks_history(record)
                count += 1
    return count
```

---

## 7. 系统架构图（ASCII）

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               Frontend (React + Vite)                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │
│  │ 股票列表  │ │ K线图表  │ │ 特征雷达  │ │ 聚类分布  │ │ 任务状态  │               │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘               │
└───────┼────────────┼────────────┼────────────┼────────────┼───────────────────────┘
        │            │            │            │            │
        └────────────┴────────────┴────────────┴────────────┘
                                     │
                              React Query / REST
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            FastAPI (Backend API)                                  │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                │
│  │ /stocks     │ │ /quotes     │ │ /features   │ │ /clusters   │ │ /tasks       │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘ │
└─────────┼───────────────┼───────────────┼───────────────┼───────────────┼────────┘
          │               │               │               │               │
          ▼               ▼               ▼               ▼               ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Data Layer                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                        │
│  │   Redis     │     │ PostgreSQL  │     │  RabbitMQ   │                        │
│  │  (Cache)    │     │  (Primary)  │     │  (Queue)    │                        │
│  └──────┬──────┘     └──────┬──────┘     └──────┬──────┘                        │
└─────────┼───────────────────┼───────────────────┼───────────────────────────────┘
          │                   │                   │
          │                   │                   ▼
          │                   │     ┌─────────────────────────────────────────────┐
          │                   │     │              Workers (Consumers)             │
          │                   │     │  ┌─────────┐ ┌─────────┐ ┌─────────┐       │
          │                   │     │  │Universe │ │ Quotes  │ │Features │ ...   │
          │                   │     │  │ Worker  │ │ Worker  │ │ Worker  │       │
          │                   │     │  └────┬────┘ └────┬────┘ └────┬────┘       │
          │                   │     └───────┼───────────┼───────────┼────────────┘
          │                   │             │           │           │
          │                   │             ▼           ▼           ▼
          │                   │     ┌─────────────────────────────────────────────┐
          │                   │     │         External Data Sources                │
          │                   │     │  SSE / SZSE / BSE APIs | TuShare / AKShare   │
          │                   │     └─────────────────────────────────────────────┘
          │                   │
          └───────────────────┴───────────────────────────────────────────────────
```

---

## 8. Backend 目录结构建议

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置加载（环境变量、YAML）
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py             # 依赖注入（DB、Redis、MQ）
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── stocks.py       # 股票列表、详情
│   │       ├── quotes.py       # 行情 K 线
│   │       ├── features.py     # 特征、雷达图
│   │       ├── clusters.py     # 聚类、解释
│   │       └── tasks.py        # 任务触发、状态
│   ├── core/
│   │   ├── __init__.py
│   │   ├── database.py         # SQLAlchemy async / 连接池
│   │   ├── redis.py            # Redis 客户端
│   │   └── mq.py               # RabbitMQ 连接与发布
│   ├── models/                 # SQLAlchemy ORM（或保留 Pydantic 仅做 schema）
│   │   ├── __init__.py
│   │   ├── stock.py
│   │   ├── quote.py
│   │   ├── feature.py
│   │   ├── cluster.py
│   │   └── task.py
│   ├── schemas/                # Pydantic 请求/响应模型
│   │   ├── __init__.py
│   │   ├── stock.py
│   │   ├── quote.py
│   │   └── common.py           # 分页、过滤
│   ├── services/               # 业务逻辑层
│   │   ├── __init__.py
│   │   ├── stock_service.py
│   │   ├── quote_service.py
│   │   ├── feature_service.py
│   │   ├── cluster_service.py
│   │   └── task_service.py
│   ├── repositories/           # 数据访问层
│   │   ├── __init__.py
│   │   ├── stock_repo.py
│   │   ├── quote_repo.py
│   │   └── ...
│   ├── workers/                # RabbitMQ 消费者
│   │   ├── __init__.py
│   │   ├── universe_worker.py
│   │   ├── quotes_worker.py
│   │   ├── features_worker.py
│   │   ├── clustering_worker.py
│   │   └── llm_worker.py
│   └── migrations/             # Alembic 迁移
│       ├── env.py
│       └── versions/
├── scripts/
│   ├── migrate_jsonl_to_pg.py  # JSONL 迁移脚本
│   └── create_partitions.py   # 分区创建
├── tests/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

### 模块职责说明

| 模块 | 职责 |
|------|------|
| `api/v1/*` | 路由、参数校验、调用 service |
| `core/*` | 数据库/Redis/MQ 连接与配置 |
| `models/*` | ORM 映射 |
| `schemas/*` | 请求/响应 DTO |
| `services/*` | 业务逻辑、缓存策略 |
| `repositories/*` | 纯 SQL/ORM 封装 |

---

## 9. 附录

### A. 扩展字段建议

- `stocks` 可增加 `raw` JSONB 存储原始数据，便于调试
- `cluster_explanations` 可增加 `raw_prompt`、`raw_response` 用于审计

### B. 性能与扩展

- 行情数据量：约 5000 股 × 250 交易日/年 ≈ 125 万行/年，分区 + 索引可支撑
- 特征表：5000 × 3 窗口 × 250 ≈ 375 万行/年，分区必要
- 聚类成员：5000 × 版本数，单表可支撑
- 后续可考虑 TimescaleDB 做时序优化，或 ClickHouse 做分析查询

### C. 安全与合规

- API 密钥、数据库凭据使用环境变量
- 敏感配置不提交仓库
- 输出含免责声明，符合 product.md 要求
