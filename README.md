# stock bot

**A 股市场数据分析工具**——采集三大交易所全量股票数据，按**申万行业分类**与**概念板块**组织，提供行情台、个股详情、市场情绪与**行业投研工作台**。

产品化方向：**行业投研工作台**（首个实例：生猪养殖「猪智投」）——把行业产业指标 + 规则引擎信号做成可回测的投研看板，接入新行业应"零新表、零新页面"。

> 文档入口：[`docs/index.md`](./docs/index.md) · **首次运行：** [`docs/deployment/first-run.md`](./docs/deployment/first-run.md) · 概览：[`docs/overview.md`](./docs/overview.md) · 功能：[`docs/features.md`](./docs/features.md) · 部署：[`docs/deployment/`](./docs/deployment/) · 测试：[`docs/testing/`](./docs/testing/) · 决策：[`docs/decisions/`](./docs/decisions/)

## 功能

- **行情台**：主要指数、涨跌分布、榜单、板块、资金流、热门板块（免登录可用）
- **申万行业**：一级/二级/三级分类树、行业成分与表现
- **概念板块**：板块列表/详情/成分股、次新股追踪
- **市场情绪**：涨停梯队、板块涨停、情绪日历与盘中口径
- **个股详情**：日线 K 线（含复权）、资金流、财务摘要与估值历史
- **市场面**：北向、龙虎榜、大宗交易、解禁、回购、公告
- **投研工作台**：指标注册表驱动的行业看板 + 规则引擎信号（可回测、可审计）
- **多用户**：独立认证服务 + 网关鉴权 + 按 `user_id` 的数据隔离（自选股、标签）

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI · SQLAlchemy 2.0 (async) · Alembic · PostgreSQL 15 · Redis 7 · RabbitMQ 4.2 · APScheduler |
| 前端 | React 18 · TypeScript · Vite · Ant Design 5 · ECharts · TanStack Query · Zustand |
| 安全 | Traefik（路由/限流/安全头）+ auth-service（JWKS）+ forward-auth（Principal Assertion） |
| 部署 | Docker Compose · GitHub Actions → ghcr.io · Caddy（TLS）+ Traefik（两跳边缘） |
| 数据源 | TuShare Pro（主源）· 东财 / 同花顺 / 巨潮（板块与公告） |

## 快速开始

完整 Day 1 单线见 [`docs/deployment/first-run.md`](./docs/deployment/first-run.md)。摘要：

```bash
# 1. 环境变量：两个文件角色不同（见 docs/deployment/index.md）
cp .env.docker.example .env
cp backend/.env.example backend/.env        # 填入真实 TUSHARE_TOKEN

# 2. 网络受限时先本地预构建前端（Dockerfile 的 runtime 阶段只打包 dist/）
cd frontend && npm ci && npm run build && cd ..

# 3. 构建并启动全部服务
docker compose up --build -d
```

启动后（**Docker 栈对外只有网关一个入口**）：

| 访问 | 地址 | 说明 |
|---|---|---|
| 站点（前端 + API） | http://localhost（gateway :80） | 唯一入口，`/` → 前端、`/api` → API、`/auth` → 认证服务 |
| API 在线文档 | 本地直起后端时 http://localhost:8000/docs | 容器栈内 API **未绑定宿主机**，经网关只有 `/api/...` 路径 |
| 数据库 / Redis | `127.0.0.1:5433` / `127.0.0.1:6380`（仅回环） | 供宿主机工具与测试使用 |
| RabbitMQ 管理面板 | 未绑定宿主机 | 需临时端口转发或 `docker exec` |

服务按依赖顺序启动：`postgres / redis / rabbitmq` → `migrate` → `api` / `worker` / `scheduler` → `frontend` → `gateway`。

## 本地开发

```bash
# 后端（backend/ 下，uv 管理）
cd backend && uv pip install -e ".[dev]"
docker compose up -d postgres redis rabbitmq
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端（frontend/ 下，npm 管理）
cd frontend && npm ci && npm run dev        # http://localhost:3000（Vite 代理 /api → 8000）
```

详见 [`docs/deployment/local-dev.md`](./docs/deployment/local-dev.md)。

## 常用命令

| 目的 | 命令 |
|---|---|
| 完成前自检（**每次收尾必跑**） | `bash scripts/self_review.sh`（加 `--full` 追加 mypy/pytest/tsc） |
| 后端测试 | `cd backend && uv run pytest`（默认已排除 `e2e` 与 `bench`） |
| 后端 lint / 类型 | `cd backend && uv run --extra dev ruff check . && uv run --extra dev ruff format --check app/ tests/ && uv run --extra dev mypy app` |
| 前端静态校验 | `cd frontend && npx tsc --noEmit`（**无 eslint**；`npm run lint` 是失效脚本，见 [ADR 0006](./docs/decisions/0006-no-eslint-tsc-as-static-check.md)） |
| 前端构建 / 前端 e2e | `cd frontend && npm run build` / `npm run test:e2e`（Playwright，手动档） |
| 性能基准（Tier 1 硬门禁） | `bash scripts/bench.sh`（`--save-baseline` 刷新基线 / `--quick` 冒烟） |
| 文档系统门禁 | `bash scripts/doc_gate.sh` |
| 整体构建启动 | `docker compose build && docker compose up -d` |

## 文档地图

| 想知道 | 去 |
|---|---|
| 按任务找文档 | [`docs/index.md`](./docs/index.md) |
| 项目是什么 / 有哪些功能 | [`docs/overview.md`](./docs/overview.md) · [`docs/features.md`](./docs/features.md) |
| 当前架构 / 怎么演进来的 | [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) · [`docs/architecture/`](./docs/architecture/) · [`docs/evolution.md`](./docs/evolution.md) |
| 当初为什么这么选 | [`docs/decisions/`](./docs/decisions/) |
| 部署 / 测试与门禁 | [`docs/deployment/`](./docs/deployment/) · [`docs/testing/`](./docs/testing/) |
| 变更记录 / 实施计划 | [`docs/Changelog.md`](./docs/Changelog.md) · [`plans/`](./plans/) |
| 踩过的坑 | [`docs/references/best-practices.md`](./docs/references/best-practices.md) |

> 根目录 `src/`、`tests/` 与根 `pyproject.toml` 为早期 CLI 原型遗留（见 [`docs/evolution.md`](./docs/evolution.md) v0 段），主项目在 `backend/` 与 `frontend/`。

---

# stock bot (English)

**An A-share market data analysis tool.** It ingests the full stock universe of the three Chinese exchanges, organizes everything by **Shenwan industry classification** and **concept boards**, and ships a market dashboard, stock detail pages, market-sentiment views, and an **industry research workbench**.

Productization direction: the **Industry Research Workbench** (first instance: hog farming, "猪智投"). It turns industry fundamentals plus a rule-engine signal layer into an auditable research dashboard; adding a new industry should require **no new tables and no new pages** — just configuration and a fetch adapter.

> Entry point for docs: [`docs/index.md`](./docs/index.md) · Overview: [`docs/overview.md`](./docs/overview.md) · Features: [`docs/features.md`](./docs/features.md) · Deployment: [`docs/deployment/`](./docs/deployment/) · Testing & gates: [`docs/testing/`](./docs/testing/) · Decision records: [`docs/decisions/`](./docs/decisions/)

## Features

- **Market dashboard**: major indices, advance/decline distribution, rankings, sectors, capital flow, hot boards (usable without login)
- **Shenwan industries**: level-1/2/3 classification tree, constituents and performance
- **Concept boards**: board list/detail/constituents, plus recently-listed stock tracking
- **Market sentiment**: limit-up ladder, sector limit-ups, sentiment calendar and intraday basis
- **Stock detail**: daily K-line (with adjustment), money flow, financial summary and valuation history
- **Market-wide data**: northbound flow, dragon-tiger list, block trades, share unlocks, buybacks, announcements
- **Research workbench**: metric-registry-driven industry dashboard plus a rule engine whose signals are backtestable and auditable
- **Multi-user**: dedicated auth service, gateway-level authorization, per-`user_id` data isolation (watchlists, tags)

## Tech stack

| Layer | Stack |
|---|---|
| Backend | FastAPI · SQLAlchemy 2.0 (async) · Alembic · PostgreSQL 15 · Redis 7 · RabbitMQ 4.2 · APScheduler |
| Frontend | React 18 · TypeScript · Vite · Ant Design 5 · ECharts · TanStack Query · Zustand |
| Security | Traefik (routing/rate-limiting/security headers) + auth-service (JWKS) + forward-auth (Principal Assertion) |
| Deployment | Docker Compose · GitHub Actions → ghcr.io · Caddy (TLS) + Traefik (two-hop edge) |
| Data sources | TuShare Pro (primary) · Eastmoney / Tonghuashun / CNINFO (boards and announcements) |

## Quick start

```bash
# 1. Environment: two files with different roles (see docs/deployment/index.md)
cp .env.docker.example .env
cp backend/.env.example backend/.env        # fill in a real TUSHARE_TOKEN

# 2. On restricted networks, pre-build the frontend first (the runtime stage only packs dist/)
cd frontend && npm ci && npm run build && cd ..

# 3. Build and start everything
docker compose up --build -d
```

Once up (**the gateway is the only externally exposed entry point**):

| What | Where | Notes |
|---|---|---|
| Site (frontend + API) | http://localhost (gateway :80) | `/` → frontend, `/api` → API, `/auth` → auth service |
| Interactive API docs | http://localhost:8000/docs when running the backend locally | Inside the Compose stack the API binds no host port; via the gateway only `/api/...` is reachable |
| Postgres / Redis | `127.0.0.1:5433` / `127.0.0.1:6380` (loopback only) | For host-side tooling and tests |
| RabbitMQ management UI | not bound to the host | Use a temporary port-forward or `docker exec` |

Startup order follows dependencies: `postgres / redis / rabbitmq` → `migrate` → `api` / `worker` / `scheduler` → `frontend` → `gateway`.

## Local development

```bash
# Backend (backend/, managed by uv)
cd backend && uv pip install -e ".[dev]"
docker compose up -d postgres redis rabbitmq
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Frontend (frontend/, managed by npm)
cd frontend && npm ci && npm run dev        # http://localhost:3000 (Vite proxies /api → 8000)
```

See [`docs/deployment/local-dev.md`](./docs/deployment/local-dev.md).

## Common commands

| Goal | Command |
|---|---|
| Pre-completion self-review (**required before every hand-off**) | `bash scripts/self_review.sh` (add `--full` for mypy/pytest/tsc) |
| Backend tests | `cd backend && uv run pytest` (excludes `e2e` and `bench` by default) |
| Backend lint / types | `cd backend && uv run --extra dev ruff check . && uv run --extra dev ruff format --check app/ tests/ && uv run --extra dev mypy app` |
| Frontend static check | `cd frontend && npx tsc --noEmit` (**no eslint**; `npm run lint` is a dead script, see [ADR 0006](./docs/decisions/0006-no-eslint-tsc-as-static-check.md)) |
| Frontend build / frontend e2e | `cd frontend && npm run build` / `npm run test:e2e` (Playwright, manual tier) |
| Performance benchmarks (Tier 1 hard gate) | `bash scripts/bench.sh` (`--save-baseline` to refresh the baseline, `--quick` for a smoke run) |
| Documentation gate | `bash scripts/doc_gate.sh` |
| Full stack build & start | `docker compose build && docker compose up -d` |

## Documentation map

| You want to know | Read |
|---|---|
| Find docs by task | [`docs/index.md`](./docs/index.md) |
| What the project is / what it does | [`docs/overview.md`](./docs/overview.md) · [`docs/features.md`](./docs/features.md) |
| Current architecture / how it evolved | [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) · [`docs/architecture/`](./docs/architecture/) · [`docs/evolution.md`](./docs/evolution.md) |
| Why a choice was made | [`docs/decisions/`](./docs/decisions/) |
| Deployment / testing & gates | [`docs/deployment/`](./docs/deployment/) · [`docs/testing/`](./docs/testing/) |
| Change log / implementation plans | [`docs/Changelog.md`](./docs/Changelog.md) · [`plans/`](./plans/) |
| Pitfalls we have already hit | [`docs/references/best-practices.md`](./docs/references/best-practices.md) |

> The root-level `src/`, `tests/` and `pyproject.toml` are leftovers from an early CLI prototype (see the v0 section of [`docs/evolution.md`](./docs/evolution.md)); the main project lives in `backend/` and `frontend/`.
