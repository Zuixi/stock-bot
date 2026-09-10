# stock bot

一个记录与跟踪股票数据的分析工具：支持**上交所 / 深交所 / 北交所**三大交易所的股票数据获取，并按**申万行业分类**统计展示。可查看当前市场行情、股票类别、每个分类的具体股票信息与个股详情。

正在产品化方向：**行业投研工作台**（首个实例：生猪养殖「猪智投」），实施计划见 [`plans/industry-research-workbench.md`](./plans/industry-research-workbench.md)。

> 本文档是项目总览；具体部署步骤见 [`docs/build.md`](./docs/build.md)，架构设计见 [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)，变更记录见 [`docs/Changelog.md`](./docs/Changelog.md)。

## 功能特性

- **数据采集**：TuShare Pro 为主数据源（股票列表、日线、涨跌停、财务指标等），备用源包括 AKShare、yfinance、交易所爬虫（上交所 CNINFO）
- **大盘行情**：上证指数、深证成指、创业板指、北证50、沪深300 等主要指数行情，SSE 指数快照、资金流、北向、龙虎榜、大宗交易等市场面数据
- **个股详情**：日线 K 线、复权因子、资金流向、财务摘要与估值（PE/PB/市值）历史
- **申万行业分类**：申万一级/二级/三级分类树、个股行业归属、行业成分统计
- **行业投研工作台**：面向具体行业的指标看板（投产 / 价格 / 存栏等），支持指标注册表 + 源适配器扩展新行业
- **定时任务**：APScheduler 定时增量回填 + RabbitMQ 队列手动触发，双轨制保证数据闭环
- **聚类与标签**：股票行为相似性聚类，以及用户自定义标签分组

## 技术栈

**后端** `backend/`
- FastAPI + SQLAlchemy 2.0（async）+ Alembic 迁移
- PostgreSQL 15（存储）、Redis 7（缓存，默认 TTL 300s）、RabbitMQ 4.2（异步任务队列）
- uv 管理依赖；ruff（line-length 100）+ mypy（pydantic plugin）静态检查

**前端** `frontend/`
- React 18 + TypeScript + Vite
- Ant Design 5 + ECharts（echarts-for-react）数据可视化
- TanStack React Query 数据获取 + Zustand 本地持久化
- 生产环境由 nginx 托管静态资源并反向代理 `/api`

## 项目结构

```
stock_bot/
├── docker-compose.yml        # 根目录统一编排
├── .env.docker.example       # Docker 部署环境变量模板
├── backend/                  # FastAPI 数据服务后端
│   ├── app/
│   │   ├── api/v1/          # RESTful 路由（exchanges/stocks/market/financials/...）
│   │   ├── core/            # 基础设施：DB、Redis、MQ、数据源客户端
│   │   ├── models/          # SQLAlchemy ORM 模型
│   │   ├── migrations/      # Alembic 迁移
│   │   ├── repositories/    # 数据访问层
│   │   ├── schemas/         # Pydantic 请求/响应模型
│   │   ├── services/        # 业务逻辑层（ingest / market / industry ...）
│   │   ├── scheduler/       # APScheduler 定时任务
│   │   └── workers/         # RabbitMQ 异步任务 Worker
│   ├── Dockerfile
│   └── AGENTS.md
├── frontend/                 # React 前端
│   ├── src/app/             # 路由 / 布局 / 主题
│   ├── src/pages/           # 页面
│   ├── src/features/        # 功能模块
│   ├── src/shared/          # api / ui / config
│   ├── Dockerfile
│   └── AGENTS.md
├── docs/                     # 文档（架构 / 构建部署 / 设计 / 数据源调研）
├── plans/                    # 功能实施计划（tracer-bullet 分阶段）
└── AGENTS.md
```

> 说明：根目录 `src/`、`tests/` 与根 `pyproject.toml` 为早期 CLI 原型遗留，主项目在 `backend/` 与 `frontend/`。

## 快速开始

### 环境要求
- Docker >= 24.0 与 Docker Compose V2（推荐，部署）
- Python >= 3.11 + uv（仅本地开发）
- Node.js >= 22（仅本地开发或预构建前端）

### Docker Compose 部署（推荐）

完整流程见 [`docs/build.md`](./docs/build.md)，核心步骤：

```bash
# 1. 准备后端环境变量（从模板复制）
cp .env.docker.example backend/.env

# 2. 编辑 backend/.env，填入真实的 TuShare Token（https://tushare.pro）
#    TUSHARE_TOKEN=your_token

# 3. 网络受限时，先本地预构建前端（Dockerfile 的 runtime 阶段只打包 dist/）
cd frontend && npm ci && npm run build && cd ..

# 4. 构建并启动全部服务（前后端 + 数据库 + Redis + RabbitMQ）
docker compose up --build -d
```

启动后访问：
| 服务 | 地址 |
|------|------|
| 前端 | http://localhost:3000 |
| API 文档（Swagger） | http://localhost:8000/docs |
| RabbitMQ 管理面板 | http://localhost:15672（guest/guest） |

服务编排会自动按依赖顺序启动：`postgres / redis / rabbitmq` → `migrate`（`alembic upgrade head`，一次性）→ `api` / `worker` / `scheduler` → `frontend`。

### 本地开发

```bash
# 后端（backend/ 下，uv 管理）
cd backend
uv pip install -e ".[dev]"
docker compose up -d postgres redis rabbitmq   # 仅启动基础设施
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端（frontend/ 下，npm 管理）
cd frontend
npm ci
npm run dev   # http://localhost:3000，Vite 自动代理 /api → localhost:8000
```

### 常用命令

- 后端测试：`uv run pytest`（backend/ 下）
- 后端 lint / 类型检查：`uv run --extra dev ruff check .`、`uv run --extra dev mypy app`
- 前端 lint / 构建：`npm run lint`、`npm run build`（frontend/ 下）
- 整体构建启动：`docker compose build` + `docker compose up -d`

## API 概览

所有接口前缀为 `/api/v1`，完整在线文档见后端启动后的 `http://localhost:8000/docs`。

| 模块 | 路径 | 说明 |
|------|------|------|
| 交易所/股票 | `/exchanges`、`/exchanges/{exchange}/stocks` | 交易所与类别、股票列表/搜索/详情（含 enriched 行情+基本面） |
| 行情 | `/exchanges/{exchange}/stocks/{symbol}/quotes/daily`、`/quotes/latest` | 个股日线 K 线、最新价 |
| 财务/估值 | `/exchanges/{exchange}/stocks/{symbol}/financial-*`、`/valuation-history` | 财务摘要、报表、估值历史 |
| 大盘 | `/market` | 指数、分布、板块、资金流、热门板块、SSE 快照 |
| 市场面 | `/market/global-indices`、`/sector-moneyflow`、`/northbound`、`/dragon-tiger`、`/block-trades`、`/announcements` 等 | 全球指数、板块资金、北向、龙虎榜、大宗、回购、公告 |
| 申万行业 | `/market/sw-industry/*` | 申万一级/二级/三级树与成分股 |
| 投研工作台 | `/industries/{industry_key}/...` | 行业看板、指标最新/历史、成分公司 |
| 聚类 | `/clusters` | 股票聚类运行与解释 |
| 标签 | `/tags` | 用户自定义标签 |
| 任务 | `/tasks` | 数据抓取/回填任务查询与手动触发 |
| 健康检查 | `/health` | 服务健康状态 |

## 文档地图

- [`docs/build.md`](./docs/build.md) — 服务构建与部署指南（端口表、分步构建、迁移、数据验证、运维命令）
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — 整体架构与容器化部署架构
- [`docs/design/`](./docs/design/) — 设计文档、原型与数据源调研（含 `data-source.md`、猪智投产品文档）
- [`docs/Changelog.md`](./docs/Changelog.md) — 变更记录
- [`plans/`](./plans/) — 功能实施计划

## Roadmap

产品需求与里程碑见 `product.md`；当前聚焦方向为**行业投研工作台**（[`plans/industry-research-workbench.md`](./plans/industry-research-workbench.md)）。
