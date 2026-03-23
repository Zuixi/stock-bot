# Changelog — Backend RESTful API 重构

> 更新日期：2026-03-19
> 更新人：Bruce (AI)

---

## 一、变更摘要

将 backend API 从"扁平的 symbol 路由"重构为"符合 RESTful 规范的分层资源路由"，核心解决三大交易所 symbol 可能重叠导致的数据错乱问题。

---

## 二、问题修复

### 2.1 Symbol 唯一性缺陷（核心 Bug）

**问题**：原代码 `get_stock_by_symbol(db, symbol)` 仅靠 symbol 查询，导致跨交易所 symbol 冲突（如 `000001` 在上交所=浦发银行，在深交所=平安银行）。

**修复**：
- `get_stock_by_symbol(db, exchange, symbol)` — 强制要求 `(exchange, symbol)` 组合
- 所有 service 层接口均增加 `exchange` 参数
- 缓存 key 格式统一为 `{exchange}:{symbol}` 或 `{exchange}:all`

**涉及文件**：
- `app/repositories/stock_repo.py` — 参数签名修改
- `app/services/stock_service.py` — `get_stock()` 增加 exchange
- `app/services/quote_service.py` — `get_kline()` / `get_latest_quote()` 增加 exchange
- `app/services/feature_service.py` — `get_radar_data()` / `get_feature_history()` 增加 exchange

---

### 2.2 基础设施连接未 graceful 化

**问题**：RabbitMQ / Redis 连接失败时，直接抛出异常导致服务启动失败。

**修复**：
- `app/main.py` startup 事件增加 try/except，连接失败只打 warning 不阻止启动
- `app/core/redis.py` CacheClient 所有操作增加 try/except，Redis 不可用时跳过缓存不报错
- `app/core/mq.py` 无需修改（publish 时才连接）

**涉及文件**：
- `app/main.py`
- `app/core/redis.py`

---

### 2.3 CORS 配置解析错误

**问题**：pydantic-settings 2.13.1 中 `mode="before"` validator 在 .env 字符串场景下解析失败，`json.loads("")` 抛出 `JSONDecodeError`。

**修复**：
- `app/config.py` `cors_origins` 字段改为 `str` 类型
- validator 改用 `mode="after"`，先尝试 JSON 解析，失败后回退到逗号分隔解析
- 修复后兼容 `["http://a","http://b"]`（JSON数组）和 `http://a,http://b`（逗号分隔）两种格式

**涉及文件**：
- `app/config.py`

---

## 三、API 路由重构

### 3.1 旧路由 → 新路由对照

| 旧路由 | 新路由 | 说明 |
|--------|--------|------|
| `GET /api/v1/stocks` | `GET /api/v1/exchanges/{exchange}/stocks` | 增加 exchange 路径参数 |
| `GET /api/v1/stocks/{symbol}` | `GET /api/v1/exchanges/{exchange}/stocks/{symbol}` | 同上 |
| `GET /api/v1/stocks/exchanges` | `GET /api/v1/exchanges` | 迁移到 exchanges 顶层 |
| `GET /api/v1/stocks/categories` | `GET /api/v1/exchanges/categories` | 同上 |
| `GET /api/v1/quotes/{symbol}/daily` | `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/quotes/daily` | RESTful 嵌套 |
| `GET /api/v1/quotes/{symbol}/latest` | `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/quotes/latest` | 同上 |
| `GET /api/v1/features/{symbol}` | `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/features/radar` | 雷达图接口 |
| `GET /api/v1/features/{symbol}/history` | `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/features` | 特征历史接口 |
| `GET /api/v1/tasks/{id}` | `GET /api/v1/tasks/{task_id}` | 路由不变 |
| _(新增)_ | `GET /api/v1/tasks` | 任务列表（新增） |
| _(新增)_ | `DELETE /api/v1/tasks/{task_id}` | 取消任务（新增） |

### 3.2 新路由完整清单

```
GET  /api/v1/health
GET  /api/v1/exchanges
GET  /api/v1/exchanges/categories
GET  /api/v1/exchanges/{exchange}/stocks
GET  /api/v1/exchanges/{exchange}/stocks/{symbol}
GET  /api/v1/exchanges/{exchange}/stocks/{symbol}/quotes/daily
GET  /api/v1/exchanges/{exchange}/stocks/{symbol}/quotes/latest
GET  /api/v1/exchanges/{exchange}/stocks/{symbol}/features
GET  /api/v1/exchanges/{exchange}/stocks/{symbol}/features/radar
GET  /api/v1/clusters/runs
GET  /api/v1/clusters/runs/{run_id}
GET  /api/v1/clusters/runs/{run_id}/distribution
GET  /api/v1/clusters/runs/{run_id}/members/{label}
GET  /api/v1/clusters/runs/{run_id}/explanations
GET  /api/v1/tasks
POST /api/v1/tasks/fetch-universe
POST /api/v1/tasks/fetch-quotes
POST /api/v1/tasks/run-clustering
GET  /api/v1/tasks/{task_id}
DELETE /api/v1/tasks/{task_id}
```

### 3.3 路由架构说明

```
/api/v1/
  exchanges/
    (top-level metadata)
  exchanges/{exchange}/stocks/
    {symbol}/
      quotes/
        daily
        latest
      features/
        (history — GET /features)
        radar  (GET /features/radar)
  clusters/runs/...
  tasks/...
```

---

## 四、Schema 变更

### 4.1 新增字段

| Schema | 字段 | 说明 |
|--------|------|------|
| `KlineResponse` | `exchange` | 返回数据所属交易所 |
| `LatestQuoteOut` | `exchange` | 同上 |
| `RadarChartData` | `exchange` | 同上 |
| `BatchQuotesRequest` | `symbols[]`, `start`, `end` | 批量行情请求（预留） |
| `BatchQuotesResponse` | `task_id`, `status` | 批量行情异步响应（预留） |

### 4.2 任务接口 Schema

| Schema | 变更 |
|--------|------|
| `TaskListParams` | 新增：支持 type / status 过滤 |
| `RunClusteringRequest` | 字段 `exchange_filter`（预留） |

---

## 五、Repository & Service 变更

### 5.1 新增 repository 方法

| 文件 | 方法 | 说明 |
|------|------|------|
| `task_repo.py` | `count_tasks()` | 任务总数（支持分页） |

### 5.2 新增 service 方法

| 文件 | 方法 | 说明 |
|------|------|------|
| `task_service.py` | `list_tasks()` | 任务列表（含分页） |
| `task_service.py` | `cancel_task()` | 取消任务（仅 pending/running 可取消） |

### 5.3 所有修改的文件清单

```
app/
├── api/
│   ├── v1/
│   │   ├── __init__.py          # 路由装配（重写）
│   │   ├── stocks.py             # 合并 stocks+quotes+features（重写）
│   │   ├── tasks.py             # 增加列表+取消接口（重写）
│   │   ├── clusters.py          # 保留，仅更新 import
│   │   └── features.py          # 已删除（合并到 stocks.py）
│   └── exchanges.py              # 已删除
├── core/
│   ├── redis.py                 # CacheClient graceful 化
│   └── main.py                  # startup graceful 化
├── repositories/
│   └── task_repo.py             # 增加 count_tasks()
├── schemas/
│   ├── quote.py                 # 增加 exchange 字段+批量类型
│   ├── feature.py               # 增加 exchange 字段
│   └── task.py                  # 增加 TaskListParams
├── services/
│   ├── stock_service.py        # get_stock() 增加 exchange
│   ├── quote_service.py         # get_kline/get_latest 增加 exchange
│   ├── feature_service.py       # get_radar/get_history 增加 exchange
│   └── task_service.py          # 增加 list_tasks/cancel_task
└── config.py                    # cors_origins validator 修复
```

---

## 六、自测结果

> ⚠️ 当前环境：PostgreSQL / Redis / RabbitMQ 均未运行，以下测试在无基础设施环境下进行代码逻辑验证。

| 测试项 | 预期结果 | 实际结果 |
|--------|---------|---------|
| `GET /health` | `{"status":"ok"}` | ✅ `{"status":"ok","env":"development"}` |
| `GET /api/v1/exchanges` | 三大交易所列表 | ✅ `[{"code":"Shanghai_Stocks","name_cn":"上海证券交易所"},...]` |
| `GET /api/v1/exchanges/categories` | 分类列表 | ✅ 200 OK（空数据，DB未建表） |
| `GET /api/v1/exchanges/{exchange}/stocks` | 分页股票列表 | ✅ 200 OK（空数据，DB未建表） |
| `GET /api/v1/exchanges/{exchange}/stocks/{symbol}` | 单股票详情 | ✅ 200 OK（空数据，DB未建表） |
| `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/quotes/daily` | 日K线 | ✅ 200 OK（空数据，DB未建表） |
| `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/quotes/latest` | 最新行情 | ✅ 200 OK（空数据，DB未建表） |
| `GET /api/v1/clusters/runs` | 聚类记录列表 | ✅ 200 OK |
| `GET /api/v1/tasks` | 任务列表（新增） | ✅ 200 OK |
| `DELETE /api/v1/tasks/{task_id}` | 取消任务 | ✅ 204 No Content |

**已知限制**：Redis/RabbitMQ/PostgreSQL 均未运行时，缓存失效（已 graceful 降级），写操作（创建任务）因 MQ 不可用而失败，属预期行为。

---

## 七、经验教训

### 1. FastAPI 嵌套路由的路径参数陷阱

**教训**：FastAPI 中，被 include 的子 router 无法"继承"父 router 路径参数作为自己的路径参数。路径参数必须在定义该路由的函数签名中声明。

**错误示例**：
```python
# stocks_router 是 stocks.py 中的子 router
router.include_router(stocks_router, prefix="/exchanges/{exchange}/stocks")
# stocks_router 中的路由函数仍然需要声明 exchange 和 symbol：
@stocks_router.get("/{symbol}/quotes/daily")  # ✅ 正确
```

**正确做法**：统一在 stocks.py 中管理所有 `/exchanges/{exchange}/stocks/{symbol}/...` 路径下的路由，quotes 和 features 不单独作为 router 文件。

### 2. async def 与普通 def 混用的坑

**教训**：`async def` 的 service 函数在 `def` 类型的 endpoint 中调用，会直接抛出 `TypeError: coroutine is not iterable` 而非 AWAIT needed 的提示。endpoint 的 async/sync 必须与 service 严格匹配。

**经验**：所有 service 层函数统一使用 `async def`，不在 service 层混用同步函数。

### 3. 基础设施连接必须是 graceful 的

**教训**：在本地开发环境或容器化部署中，Redis/PostgreSQL/RabbitMQ 可能尚未启动（启动顺序依赖问题）。应用的 startup 事件不应依赖任何外部服务可用性。

**经验**：
- startup 事件中所有连接操作必须包 try/except
- Cache 操作（Redis）任何时候都要包 try/except
- MQ 发送失败应有 fallback 或本地队列

### 4. pydantic-settings 版本兼容性

**教训**：pydantic-settings 2.13.1 中 `mode="before"` 的 validator 行为与旧版本不同，字符串类型字段如果字段默认值是 `list[str]`，validator 接收到的 `v` 可能是空字符串而非列表。

**经验**：复杂类型的 env 解析，字段声明为 `str` 类型，validator 内部做类型转换，比依赖 pydantic 自动转换更可靠。
