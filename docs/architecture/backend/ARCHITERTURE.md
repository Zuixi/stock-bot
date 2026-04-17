# Backend Architecture

## 1. System Overview

stock-bot 是一个A股市场数据采集、存储和查询分析的后端服务，采用 FastAPI 异步框架构建，集成 PostgreSQL、Redis、RabbitMQ 实现数据存储、缓存和异步任务处理。

```mermaid
graph LR
    Frontend[Frontend React] -->|HTTP/WS| FastAPI[FastAPI Server]
    FastAPI --> Redis[Redis Cache]
    FastAPI --> PostgreSQL[(PostgreSQL)]
    FastAPI -->|publish| RabbitMQ[RabbitMQ]
    RabbitMQ --> Workers[Async Workers]
    Workers --> PostgreSQL
    Workers -->|fetch| TuShare[TuShare API]
    TuShare -->|raw data| Workers
```

## 2. Component Architecture

### 2.1 API Layer

```mermaid
graph TD
    Request[Client Request] --> Router[API Router]
    Router --> Service[Service Layer]
    Service --> Repository[(Repository)]
    Service --> Cache[Redis Cache]
    Service --> MQ[RabbitMQ]
```

- **Router**: FastAPI 路由层，负责请求校验和路由分发
- **Service**: 业务逻辑层，编排缓存、数据库和消息队列
- **Repository**: 数据访问层，封装 SQLAlchemy 异步操作

### 2.2 Data Model

```mermaid
erDiagram
    Stock ||--o{ DailyQuote : "has"
    Stock ||--o{ StockFeature : "has"
    Stock ||--o{ ClusteringMember : "belongs to"
    ClusteringRun ||--o{ ClusteringMember : "contains"
    ClusteringRun ||--o{ ClusterExplanation : "explains"
    Task ||--o{ Stock : "tracks"
    Task ||--o{ DailyQuote : "tracks"
```

| Model | Purpose |
|-------|---------|
| Stock | 股票基本信息快照 |
| DailyQuote | 日线 OHLCV 数据（按日期分区） |
| StockFeature | 股票特征指标 |
| ClusteringRun | 聚类运行记录 |
| Task | 后台任务状态跟踪 |

## 3. Data Flow

### 3.1 Sync Data Flow (TuShare Ingestion)

```mermaid
sequenceDiagram
    participant API as FastAPI
    participant MQ as RabbitMQ
    participant Worker as Worker
    participant TS as TuShare API
    participant DB as PostgreSQL
    participant Cache as Redis

    API->>MQ: publish fetch task
    Worker->>TS: fetch data
    TS-->>Worker: raw data
    Worker->>DB: upsert data
    Worker->>Cache: invalidate cache
    Worker->>API: task completed
```

### 3.2 Query Data Flow

```mermaid
sequenceDiagram
    participant F as Frontend
    participant API as FastAPI
    participant Cache as Redis
    participant DB as PostgreSQL

    F->>API: get stocks
    API->>Cache: check cache
    alt Cache Hit
        Cache-->>API: cached data
    else Cache Miss
        API->>DB: query database
        DB-->>API: raw data
        API->>Cache: store cache
    end
    API-->>F: JSON response
```

## 4. Async Task Processing

```mermaid
flowchart LR
    subgraph Topic Exchange
        direction TB
        UF[universe.fetch]
        QF[quotes.fetch]
        FC[features.compute]
        CR[clustering.run]
        LE[llm.explain]
    end

    UF --> UniverseWorker
    QF --> QuotesWorker
    FC --> FeatureWorker
    CR --> ClusterWorker
    LE --> LLMWorker
```

| Routing Key | Queue | Worker |
|-------------|-------|--------|
| `universe.fetch` | stock_bot.universe.fetch | UniverseWorker |
| `quotes.fetch` | stock_bot.quotes.fetch | QuotesWorker |
| `features.compute` | stock_bot.features.compute | FeatureWorker |
| `clustering.run` | stock_bot.clustering.run | ClusterWorker |
| `llm.explain` | stock_bot.llm.explain | LLMWorker |

## 5. API Structure

```mermaid
graph TD
    /api/v1 --> Exchanges[Exchanges]
    /api/v1 --> Market[Market]
    /api/v1 --> Clusters[Clusters]
    /api/v1 --> Tasks[Tasks]

    Exchanges --> /exchanges
    Exchanges --> /exchanges/categories
    Exchanges --> /exchanges/:exchange/stocks
    Exchanges --> /exchanges/:exchange/stocks/:symbol/quotes
    Exchanges --> /exchanges/:exchange/stocks/:symbol/features

    Market --> /market/indices
    Market --> /market/distribution
    Market --> /market/sectors
    Market --> /market/sw-industry/tree

    Clusters --> /clusters
    Tasks --> /tasks
```

## 6. Caching Strategy

```mermaid
flowchart LR
    subgraph Cache Keys
        SL["stock:list:{exchange}:{category}:{keyword}:{page}"]
        SD["stock:detail:{exchange}:{symbol}"]
        TS["task:status:{task_id}"]
        SW["sw:industry:tree"]
    end

    subgraph TTL
        SL --> |5min| Expired
        SD --> |10min| Expired
        SW --> |1day| Expired
    end
```

- **Read-Through**: 查询时优先读缓存，缓存命中则返回，未命中则查库并回填缓存
- **Write-Through**: 数据更新时同时更新缓存和数据库
- **Fault Tolerance**: Redis 不可用时自动降级，不影响主业务流程

## 7. Tech Stack

| Component | Technology |
|-----------|------------|
| Web Framework | FastAPI (async) |
| Database | PostgreSQL + SQLAlchemy (async) |
| Cache | Redis |
| Message Queue | RabbitMQ |
| ORM Migrations | Alembic |
| Data Source | TuShare API |
| Container | Docker Compose |
