# stock bot — 整体架构

> 当前态文档。文档入口见 [`index.md`](./index.md)；架构演进史见 [`evolution.md`](./evolution.md)；决策理由见 [`decisions/`](./decisions/)。
> **端口与服务的权威副本在 [`deployment/index.md`](./deployment/index.md)**（本节只描述“是什么/干什么”，不与它争家）。

## overview

Stock bot is an automated tool for fetching data from the exchanges and using a web service to display important info for the users.

## architecture

Backend Service:
- Python 3.13, FastAPI, SQLAlchemy 2.0 (async), Alembic
- PostgreSQL 15, Redis 7, RabbitMQ 4.2
- TuShare Pro / 东财 / 同花顺 / CNINFO 数据源（主源选型见 [ADR 0001](./decisions/0001-single-primary-source-tushare.md)）

Frontend Service:
- React 18, TypeScript, Vite 6
- Ant Design 5, ECharts, TanStack Query, Zustand
- nginx（仅托管静态资源；`/api` 由 Traefik 转发，不由 nginx 反代）

后端分层与依赖方向、API 前缀清单、队列注册表见 [`architecture/backend/ARCHITECTURE.md`](./architecture/backend/ARCHITECTURE.md)；前端目录、数据获取与路由见 [`architecture/frontend/ARCHITECTURE.md`](./architecture/frontend/ARCHITECTURE.md)。

## 容器化部署架构

项目使用 Docker Compose 统一编排，所有服务通过 Docker 内部网络通信：

| 服务 | 镜像 | 职责 |
|------|------|------|
| caddy | caddy:2-alpine | **对外唯一入口**（宿主机 `0.0.0.0:80/443`）：TLS 自动签发续期 + 反代到 gateway（仅生产，见 `docker-compose.prod.yml` + `gateway/caddy/Caddyfile`） |
| gateway | traefik:v3.6 | 路由/鉴权/限流/安全头（本地开发对外 `:80`；生产仅 compose 网络内 `:80`），按 `PathPrefix` 分发 `/`→frontend、`/api`→api、`/auth`→auth-service，并挂 `forward-auth` 中间件 |
| frontend | nginx:alpine（镜像由 `frontend/Dockerfile` 的 runtime 阶段产出，非上游 nginx 镜像） | 静态资源托管（SPA 由 Traefik 转发，不由 nginx 反代 `/api`） |
| api | python:3.13-alpine（多阶段构建） | FastAPI REST API |
| worker | 同 api 镜像 | RabbitMQ 消费者，执行数据抓取/计算任务 |
| scheduler | 同 api 镜像 | APScheduler 定时采集（SSE 指数快照、概念成分刷新等） |
| migrate | 同 api 镜像 | 一次性容器，启动时执行 `alembic upgrade head` |
| auth-service | 同 auth-service 镜像 | 注册/登录/会话/JWKS（`/auth`，内部 :8001） |
| forward-auth | 同 forward-auth 镜像 | Traefik forward-auth sidecar：会话 cookie → Principal Assertion 注入 + CSRF 校验（内部 :9000） |
| migrate-auth | 同 auth-service 镜像 | 一次性容器，auth 库 `alembic upgrade head` |
| redis-init | alpine | 一次性容器，修正 redis 数据卷权限 |
| postgres | postgresql-15-c9s | 主业务库（`127.0.0.1:5433`，仅回环） |
| auth-db | postgresql-15-c9s | 账号/会话库（无宿主机映射） |
| redis | redis:v7 | 缓存层 + 会话存储（`127.0.0.1:6380`，仅回环） |
| rabbitmq | rabbitmq:4.2-management | 异步任务队列 |

服务启动顺序：postgres/auth-db/redis(+redis-init)/rabbitmq → migrate(+migrate-auth) → api/worker/scheduler/auth-service/forward-auth → gateway/frontend →（生产）caddy

> 说明：`forward-auth` 对「带 `stockbot_session` 但解析不出 assertion」的请求 **fail-closed 返 503**（不是 401）——浏览器残留过期 cookie 时表现为「全站接口不可用、页面全空」，排查方法见 [references/best-practices.md](references/best-practices.md)。

## 发布与运维

发版链路（ghcr.io + `docker-compose.prod.yml`）、边缘层两跳、数据迁移与日常排障见 [`deployment/`](./deployment/)：

- 发布流程与镜像构建： [`deployment/production.md`](./deployment/production.md)
- 首次上线数据迁移： [`deployment/data-migration.md`](./deployment/data-migration.md)
- 运维命令与排障清单： [`deployment/operations.md`](./deployment/operations.md)
