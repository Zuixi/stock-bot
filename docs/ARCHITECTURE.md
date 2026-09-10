# stock bot

## overview

Stock bot is a automated tool for fetching data from the exchanges and use web service to display important info for the users.

## architecture

Backend Service:
- Python 3.13, FastAPI, SQLAlchemy 2.0 (async), Alembic
- PostgreSQL 15, Redis 7, RabbitMQ 4.2
- TuShare Pro / AKShare / CNINFO 数据源

Frontend Service:
- React 18, TypeScript, Vite 6
- Ant Design 5, ECharts, TanStack Query, Zustand
- nginx (生产环境 API 反向代理)

## 容器化部署架构

项目使用 Docker Compose 统一编排，所有服务通过 Docker 内部网络通信：

| 服务 | 镜像 | 职责 |
|------|------|------|
| frontend | nginx:alpine | 静态资源托管 + `/api` 反向代理到 api 服务 |
| api | python:3.13-alpine (多阶段构建) | FastAPI REST API |
| worker | 同 api 镜像 | RabbitMQ 消费者，执行数据抓取/计算任务 |
| scheduler | 同 api 镜像 | APScheduler 定时采集（SSE 指数快照等） |
| migrate | 同 api 镜像 | 一次性容器，启动时执行 `alembic upgrade head` |
| redis-init | alpine | 一次性容器，修正 redis 数据卷权限 |
| postgres | postgresql-15-c9s | 持久化数据存储 |
| redis | redis:v7 | 缓存层 |
| rabbitmq | rabbitmq:4.2-management | 异步任务队列 |

服务启动顺序：postgres/redis(+redis-init)/rabbitmq → migrate → api/worker/scheduler → frontend

详细构建说明参见 [build.md](build.md)。
