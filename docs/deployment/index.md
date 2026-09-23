# 部署总览

本目录是**部署与运维的权威文档**。架构"是什么"见 [`../ARCHITECTURE.md`](../ARCHITECTURE.md)；本目录只讲**怎么起、怎么发、怎么迁、怎么排障**。

> 原 `docs/build.md`（512 行）已拆入本目录，转发说明见 [`../build.md`](../build.md)。

| 我要做的事 | 读 |
|---|---|
| **第一次** clone 后把栈跑起来 | [`first-run.md`](./first-run.md) |
| 本地把栈跑起来、改代码 | [`local-dev.md`](./local-dev.md) |
| 发版上服务器、改 Caddy/Traefik、改 Dockerfile | [`production.md`](./production.md) |
| 迁移数据到新库 / 首次上线 | [`data-migration.md`](./data-migration.md) |
| 日常运维、查数据、迁移版本、排障 | [`operations.md`](./operations.md) |

## 前提条件

- Docker ≥ 24.0、Docker Compose V2（`docker compose`）
- Node.js ≥ 22（仅本地开发或预构建前端时需要）
- Python ≥ 3.11 + uv（仅本地开发时需要）

## 项目结构与构建产物

```
stock_bot/
├── docker-compose.yml          # 根目录统一编排（本地：up --build）
├── docker-compose.prod.yml     # 服务器 override：应用镜像改走 ghcr.io 发布产物
├── .env.docker.example         # 环境变量模板（本地开发）
├── .env.production.example     # 环境变量模板（生产，带上线清单与自证命令）
├── data/                       # JSONL 数据备份（挂载卷）
├── backend/
│   ├── Dockerfile              # 多阶段构建（uv 安装 → python:alpine）
│   ├── .env                    # 后端环境变量（实际使用）
│   └── docker-compose.yml      # 仅后端开发用（可选）
└── frontend/
    ├── Dockerfile              # 多阶段构建（node build → nginx 托管）
    ├── nginx.conf              # API 反向代理 + SPA 路由
    └── dist/                   # 预构建产物（本地构建后生成）
```

## 环境变量配置

**两个 env 文件，角色不同，别混用一个**（混用会让鉴权/网关类变量静默回落到开发默认值）：

| 文件 | 谁读它 | 放什么 |
|------|-------|--------|
| 根目录 `.env` | **docker compose 的 `${VAR}` 插值**（**只有这些键真正生效**）：`APP_ENV`、`AUTH_JWT_PRIVATE_KEY_PEM`/`AUTH_JWT_PUBLIC_KEY_PEM`、`INTERNAL_API_TOKEN`、`AUTH_COOKIE_SECURE`、`AUTH_TRUST_FORWARDED_FOR`、`ASSERTION_CACHE_TTL`、`JWT_ISSUER`/`JWT_AUDIENCE`、`RABBITMQ_DEFAULT_USER`/`_PASS` | 鉴权/网关/消息队列契约（其余键如 `POSTGRES_*`/`REDIS_*`/`SESSION_*` 在 compose 里**并未引用**，填了也不生效） |
| `backend/.env` | api / worker / scheduler 容器的 `env_file`（`docker-compose.yml` 四处 `env_file: ./backend/.env`），以及本机 `uv run` 直起 | 后端运行时密钥与本地覆盖：`TUSHARE_TOKEN`、`INDUSTRY_DATA_SOURCE`、`APP_ENV`/`APP_DEBUG`、生产域名的 `CORS_ORIGINS`、本机直连用的 `DATABASE_URL`/`REDIS_URL`… |

```bash
# —— 生产（推荐，带占位符与上线清单）——
cp .env.production.example .env                    # compose 插值
cp backend/.env.production.example backend/.env    # 后端运行时（填 TUSHARE_TOKEN）

# —— 本地开发 ——
cp .env.docker.example .env
cp backend/.env.example backend/.env
```

> **宿主侧键名 ≠ 容器内键名**（自证时别再搞错，否则会得出假结论）：`AUTH_JWT_PRIVATE_KEY_PEM` → 容器内 `JWT_PRIVATE_KEY_PEM`；`AUTH_COOKIE_SECURE` → `COOKIE_SECURE`；`AUTH_TRUST_FORWARDED_FOR` → `TRUST_FORWARDED_FOR`。
>
> 为什么必须分两个：`auth-service` / `forward-auth` 的密钥是通过 **compose 插值** 注入的，不是 `env_file`。把模板复制错位置时 compose 插值取不到值，于是（**一个错都不会报**）：
> - `APP_ENV` 回落 `development` → auth-service **跳过**生产强校验（不再要求 HTTPS + `AUTH_COOKIE_SECURE=true`）
> - JWT 私钥为空 → 开发模式自动生成密钥对（重启轮换，已签发断言失效）
> - `INTERNAL_API_TOKEN` 为空 → forward-auth→auth-service 的 `X-Internal-Token` 不再发送
> - RabbitMQ 回落 `stockbot/stockbot_pass`
> - 反面提醒：`AUTH_TRUST_FORWARDED_FOR` 的 compose 默认值是 **`true`**（Trusted Proxy 形态），不是空；只有显式设 `false` 才会不信任 `X-Forwarded-For`
> - **`APP_ENV` 要写两处**：auth-service 读根 `.env`，api / worker / scheduler 只能读 `backend/.env`（compose 的 `environment:` 未注入它）——漏了后者会出现「网关侧 production、业务侧 development」的分裂
>
> 启动后必须核对一遍（与部署时同一套 `-f` 参数；第二行用的是**容器内**键名）：
>
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.prod.yml config \
>   | grep -E "APP_ENV|COOKIE_SECURE|INTERNAL_API_TOKEN|JWT_PRIVATE_KEY_PEM|TRUST_FORWARDED_FOR|RABBITMQ_DEFAULT_USER"
> docker exec auth_service sh -c 'for v in APP_ENV COOKIE_SECURE TRUST_FORWARDED_FOR INTERNAL_API_TOKEN JWT_PRIVATE_KEY_PEM JWT_PUBLIC_KEY_PEM; do eval "val=\$$v"; echo "$v=${val:+<set>}"; done'
> docker exec backend_api sh -c 'echo "APP_ENV=$APP_ENV TUSHARE_TOKEN=${TUSHARE_TOKEN:+<set>} CORS_ORIGINS=$CORS_ORIGINS"'
> curl -s localhost/api/v1/health | head -c 200   # /health 会回报 version/commit
> ```
>
> 生产要求：`APP_ENV=production` + `AUTH_COOKIE_SECURE=true` + HTTPS，并用 `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048` / `openssl pkey -in private.pem -pubout` 生成并填好 JWT 密钥对（模板里已写命令）。
>
> **PEM 是多行值，写进 `.env` 的两种可行写法**（已实测 `docker compose config` 均能解析成真实换行，选一种即可）：
>
> ```dotenv
> # 推荐：单行 + 转义换行（不易被编辑器搞坏，整体可复制）
> AUTH_JWT_PRIVATE_KEY_PEM="-----BEGIN PRIVATE KEY-----\nMIIEv...\n-----END PRIVATE KEY-----\n"
> ```
>
> ```dotenv
> # 也可以：双引号内直接换行
> AUTH_JWT_PRIVATE_KEY_PEM="-----BEGIN PRIVATE KEY-----
> MIIEv...
> -----END PRIVATE KEY-----
> "
> ```
>
> 根目录 `.env` 已被 `.gitignore` 忽略（`.env` / `.env.*`，仅 `!.env.example` / `!.env.docker.example` 白名单）—— 密钥不会被提交。

## 服务与端口权威表

> 本节是端口/服务名的**唯一权威副本**。改 `docker-compose*.yml` 时的同步义务与校验见 [`production.md`](./production.md) 与 `scripts/doc_gate.sh`。

**本地开发**（`docker compose up`，只用 base compose）：

| 服务 | 端口 | 说明 |
|------|------|------|
| gateway | http://localhost（:80） | Traefik 唯一入口，路由到前端/api/auth |
| api | 容器内 :8000（未绑宿主机） | FastAPI 后端；经网关 `/api` 访问 |
| postgres | localhost:5433（仅回环） | PostgreSQL（映射到 5433 避免冲突；`127.0.0.1` 绑定，外部不可达） |
| redis | localhost:6380（仅回环） | Redis 缓存（映射到 6380 避免冲突；`127.0.0.1` 绑定，外部不可达） |
| rabbitmq | 容器内 :5672 / :15672（未绑宿主机） | RabbitMQ（管理面板走 `docker exec` 或临时端口转发） |

**服务器（生产）**（`-f docker-compose.yml -f docker-compose.prod.yml`）：

| 服务 | 端口 | 说明 |
|------|------|------|
| caddy | **0.0.0.0:80 / 0.0.0.0:443（+443/udp）** | **唯一对外入口**：TLS 自动签发续期 + 反代到 `gateway:80` |
| gateway | 仅 compose 网络内 :80 | Traefik：路由 / 鉴权（forward-auth）/ 限流 / 安全头（prod override 用 `!override` 清空宿主机端口） |
| postgres / redis | 127.0.0.1:5433 / 6380 | 仅回环，供宿主机本地工具与 pytest 使用 |

服务职责与依赖关系（13 个服务）见 [`../ARCHITECTURE.md`](../ARCHITECTURE.md)。

## 一键启动与启动顺序

```bash
# 首次启动前，先在本地构建前端
cd frontend && npm ci && npm run build && cd ..

# 启动所有服务
docker compose up --build -d
```

`docker-compose.yml` 编排的服务按依赖关系自动启动：

1. **基础设施层**：`postgres`、`redis`（含一次性 `redis-init` 修正数据卷权限）、`rabbitmq` 并行启动，等待健康检查通过
2. **数据库迁移**：`migrate` 容器运行 `alembic upgrade head`，创建/更新表结构后退出
3. **应用层**：`api`（FastAPI）、`worker`（RabbitMQ 消费者）、`scheduler`（APScheduler 定时采集）在迁移完成后启动
4. **前端层**：`frontend`（nginx）在 API 健康检查通过后启动
5. **网关层**：`gateway`（Traefik）在应用/前端容器就绪后启动，其健康检查断言的是**路由表已加载**（`/api/rawdata` 里出现 `frontend@docker`），而不只是进程存活——`docker compose up --wait` 因此会真正等到网关可路由；否则 `--wait` 在网关容器刚 Running 时就返回，紧接着的请求会撞上 Traefik 默认 404（body 恰 19 字节 `404 page not found`）。

### 分步构建

```bash
# 仅构建后端
docker compose build api

# 仅构建前端（需先本地构建 dist/）
cd frontend && npm ci && npm run build && cd ..
docker compose build frontend

# 仅构建 worker（与 api 共享同一镜像）
docker compose build worker
```

### 前端构建说明

前端 Dockerfile 支持两种构建模式：

- **完整多阶段构建**（需要 Docker 构建环境能访问外网）：
  ```bash
  docker compose build frontend    # 自动下载依赖、构建、打包
  ```

- **预构建模式**（当前默认，适用于网络受限环境）：
  ```bash
  cd frontend
  npm ci
  VITE_API_BASE="" npm run build   # 设置空字符串使用相对路径
  cd ..
  docker compose build frontend    # 仅打包 dist/ 到 nginx 镜像
  ```

`docker-compose.yml` 中 `target: runtime` 使 Docker 跳过 Node.js 构建阶段，直接使用 `dist/` 目录。
