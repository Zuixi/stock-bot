# 服务构建与部署指南

## 前提条件

- Docker >= 24.0
- Docker Compose V2（`docker compose` 命令）
- Node.js >= 22（仅本地开发或预构建前端时需要）
- Python >= 3.11 + uv（仅本地开发时需要）

## 项目结构

```
stock_bot/
├── docker-compose.yml          # 根目录统一编排（本地：up --build）
├── docker-compose.prod.yml     # 服务器 override：应用镜像改走 ghcr.io 发布产物（见「服务器部署」）
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
> curl -s localhost/api/v1/health | head -c 200   # ？见下「镜像构建」：/health 会回报 version/commit
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

## 容器化部署

### 一键启动

```bash
# 首次启动前，先在本地构建前端
cd frontend && npm ci && npm run build && cd ..

# 启动所有服务
docker compose up --build -d
```

### 启动流程说明

`docker-compose.yml` 编排了 9 个服务，按依赖关系自动启动：

1. **基础设施层**：`postgres`、`redis`（含一次性 `redis-init` 修正数据卷权限）、`rabbitmq` 并行启动，等待健康检查通过
2. **数据库迁移**：`migrate` 容器运行 `alembic upgrade head`，创建/更新表结构后退出
3. **应用层**：`api`（FastAPI）、`worker`（RabbitMQ 消费者）、`scheduler`（APScheduler 定时采集）在迁移完成后启动
4. **前端层**：`frontend`（nginx）在 API 健康检查通过后启动
5. **网关层**：`gateway`（Traefik）在应用/前端容器就绪后启动，其健康检查断言的是**路由表已加载**（`/api/rawdata` 里出现 `frontend@docker`），而不只是进程存活——`docker compose up --wait` 因此会真正等到网关可路由；否则 `--wait` 在网关容器刚 Running 时就返回，紧接着的请求会撞上 Traefik 默认 404（body 恰 19 字节 `404 page not found`）。

### 服务端口

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

### 分步构建

如果需要单独构建某个服务：

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

docker-compose.yml 中 `target: runtime` 使 Docker 跳过 Node.js 构建阶段，直接使用 `dist/` 目录。

## 服务器部署（发布镜像 + 数据迁移）

服务器上**不构建任何镜像**：应用镜像由 CI 发布到 `ghcr.io`，服务器只 `pull` 发布产物。源码树在服务器上仍需存在，但只用于提供 `docker-compose.yml` / `docker-compose.prod.yml` 与 `gateway/` 配置。服务清单、端口与启动顺序见 [ARCHITECTURE.md](./ARCHITECTURE.md)（此处不重复该表）。

### 发布流程（本地打 tag → CI → ghcr.io）

```bash
# 1. 合并到 main，等 CI 绿（main 的 CI 与发布是两条流程，tag 只触发 CD）
git checkout main && git pull

# 2. 打 tag 并推送（**git tag 带 `v` 前缀**，与镜像 tag 区分开）
git tag v0.0.1
git push origin v0.0.1
```

> 命名约定：**git tag 带 `v`**（`v0.0.1`），**镜像 tag 不带 `v`**（`0.0.1`）。`metadata-action` 的 `type=semver` 用 loose 语义解析，会自动把 `v` 去掉（源码 `semver.parse(tag, {loose:true})`），所以 compose 侧 `IMAGE_TAG=0.0.1`。裸数字 git tag（`0.0.1`）**不会触发 CD**——`cd.yml` 的 filters 是 `["v*"]`，同一版本只允许一种写法，避免两套镜像。

`.github/workflows/cd.yml` 的 tag filters 是 `["v*"]`，推 tag 后为 4 个构建上下文各发一个镜像：

| 镜像 | 构建上下文 | 对应的 compose 服务 |
|------|-----------|-------------------|
| `ghcr.io/zuixi/stock-bot/backend` | `./backend` | `migrate` / `api` / `worker` / `scheduler` |
| `ghcr.io/zuixi/stock-bot/auth-service` | `./auth-service` | `migrate-auth` / `auth-service` |
| `ghcr.io/zuixi/stock-bot/forward-auth` | `./forward-auth` | `forward-auth` |
| `ghcr.io/zuixi/stock-bot/frontend` | `./frontend`（`target: runtime`） | `frontend` |

每个镜像会打三个 tag：`0.0.1`、`0.0`（major.minor）与 `<short-sha>`（即 git tag `v0.0.1` → 镜像 tag `0.0.1`）。服务器部署**只用 `<version>`**（如 `0.0.1`），不要用 `0.0` 这类浮动 tag。发布后在 GitHub Actions 运行记录 / 仓库 Packages 页确认**四个**镜像都出现，再上服务器。

> 坑：tag 必须在 `v*` 形状内 —— 推了裸数字 tag（如 `0.0.1`）CD **不会触发**，表现为「tag 推上去了但 ghcr 里没有镜像，服务器 `pull` 报 manifest unknown」。首次发版前建议先推一个预发布 tag（如 `v0.0.2-rc1`）走通全流程、确认 4 个镜像齐全，再发正式版。

### 服务器准备

```bash
# 1. GHCR 拉取凭据（PAT 只需 read:packages）
docker login ghcr.io -u <github-user> -p <PAT-with-read:packages>

# 2. 环境变量：用**生产模板**（带占位符与上线清单），两个文件角色不同；见上文「环境变量配置」
cp .env.production.example .env                    # compose 插值：APP_ENV / JWT 密钥 / INTERNAL_API_TOKEN / Cookie 与 XFF 开关 / MQ 口令
cp backend/.env.production.example backend/.env    # 后端运行时：TUSHARE_TOKEN / 业务侧 APP_ENV / CORS_ORIGINS

# 3. 生成 JWT 密钥对（两个模板里都有同样的命令，二选一填）
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out private.pem
openssl pkey -in private.pem -pubout -out public.pem
```

**上线前必须处理的两个默认值**（模板末尾也列了）：

1. **数据库口令硬编码**：`postgres` / `auth-db` 服务的 `POSTGRESQL_*` 与 api/worker/scheduler/migrate 的 `DATABASE_URL` 都写死在 `docker-compose.yml`（`stock_user/stock_pass`、`auth_user/auth_pass`）。根 `.env` 里的 `POSTGRES_*` **不会被消费** —— 要换口令得同时改这些行（新库部署时安全）。
2. **对外暴露面**：只有 `gateway` 绑 `0.0.0.0:80`（TLS 就绪后加 443）。`postgres`/`redis` 已改为**只绑回环**（`127.0.0.1:5433` / `127.0.0.1:6380`），宿主机上的本地开发与 `pytest` 照常可用，外部网络访问不到；若要彻底不绑，删掉 `docker-compose.yml` 里那两行 `ports:` 即可。核验：`docker compose ps` 应只见 gateway 有 `0.0.0.0:` 映射。

若服务器无法访问 `ghcr.io`，可在能同时访问 ghcr 与华为 SWR 的机器上转推（`docker pull` → `docker tag` → `docker push`）到现有 `swr.cn-north-4.myhuaweicloud.com` 命名空间，再把 `docker-compose.prod.yml` 里的 `image:` 前缀换成 SWR 地址，其余不变。

### 边缘层：Caddy 自动 HTTPS + Traefik 路由（**两跳**，不是三跳）

生产拓扑：

```
Internet :80/:443 → caddy（TLS 终结、自动签发/续期）→ gateway:80（Traefik：路由/鉴权/限流）
                  → api / frontend / auth-service / forward-auth …
```

**为什么这么分**：路由与鉴权规则（`forward-auth` 注入 Principal Assertion、`strip-assertion` 防伪造、三组限流、安全头）全在 Traefik 的 labels/动态配置里；Caddy 只做 TLS 与透传，**不重复任何路由规则**（两个代理各写一套规则必然长期漂移）。也曾评估过"Caddy → Nginx → Traefik"三跳：多一跳只增加 XFF/真实 IP 传歪的机会，且 Nginx 在本栈里的角色已经由 `frontend` 容器内的静态服务占据，收益为零。

改配置时注意三处耦合：

| 位置 | 作用 | 漏掉的后果 |
|---|---|---|
| `gateway/caddy/Caddyfile` | 站点块 + `reverse_proxy gateway:80` | 证书签发失败/站点 502 |
| `gateway/traefik.yml` 的 `entryPoints.web.forwardedHeaders.trustedIPs` | 信任来自 Caddy 的 `X-Forwarded-*`（列了 compose 网段 `172.16.0.0/12` 与 Docker Desktop `192.168.0.0/16`） | 审计日志与限流记录的是 **Caddy 容器 IP**，协议退化成 http |
| `docker-compose.prod.yml` 的 `gateway.ports: !override []` | 把宿主机 80/443 让给 Caddy | 两个容器抢同一端口，`up` 直接失败 |
| `gateway/caddy/Caddyfile` 的 `header Strict-Transport-Security` | HSTS 在**终结 TLS 的这一层**下发 | 放到 Traefik 的 `stsSeconds` 不生效（Traefik 只见明文 HTTP，实测无 header）；改用 `forceSTSHeader: true` 又会在本地 `http://localhost` 上发 HSTS，把开发机浏览器钉到 HTTPS |

**改完 Caddyfile 怎么生效**：`docker exec caddy caddy reload --config /etc/caddy/Caddyfile`（热加载，不断连、不重签证书）。`gateway/caddy/` 是**目录挂载**，所以宿主侧替换文件也能被容器看到（单文件挂载会被 inode 替换坑到：tar 解开后容器仍读旧文件）。

```bash
export IMAGE_TAG=0.0.3
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
$COMPOSE up -d caddy gateway
docker logs caddy 2>&1 | grep -iE "certificate obtained|error|challenge"   # 证书签发结果
curl -sI https://qstock.tsingyen.site/ | head -3                          # 200/301 皆正常（301 是 HTTP→HTTPS）
curl -s "https://qstock.tsingyen.site/api/v1/concepts?limit=1" | head -c 120
```

**前置条件与坑**：

- DNS：站点域名必须有 **A 记录指向本机公网 IP**（Let's Encrypt 不给裸 IP 签证书）；`80` 必须开着（HTTP-01 挑战 + 跳转），`443` 也要在安全组/防火墙放行
- **证书必须持久化**：存在 compose 卷 `stock-bot_caddy_data`，删了会触发重新签发，而 Let's Encrypt 有「同域名每周重复签发」上限；`docker compose down -v` 会连它一起删
- HTTP/3 走 `443/udp`，安全组若只放行 TCP 不影响可用性（浏览器自动回落 HTTP/2）
- **再加一个站点**：在 `gateway/caddy/Caddyfile` 追加一个块（`other.example.com { encode zstd gzip; reverse_proxy <那个栈的网关服务名>:80 }`）。跨栈要求同一 docker 网络（把该栈的网络指向本栈网络即可，或把 Caddy 抽成独立 edge 项目 —— 文件可原样搬走）
- 上线后把 `APP_ENV` 翻成 `production`、`AUTH_COOKIE_SECURE=true`（两处 env 都要，见「环境变量配置」），并把 `CORS_ORIGINS` 改成 `https://<域名>`

### 镜像构建（做了哪些优化，改 Dockerfile 前请先读）

四个应用镜像（`backend` / `auth-service` / `forward-auth` / `frontend`）统一遵循下面几条，改构建时别把它们回退：

| 约定 | 原因 / 实测效果 |
|---|---|
| **依赖层先于源码层**（先 `COPY pyproject.toml uv.lock` → 装依赖 → 最后 `COPY . .`） | 改业务代码只失效最后一层，依赖缓存命中 |
| **runtime 只拷白名单 console script**（backend/auth-service：`uvicorn`+`alembic`；forward-auth：仅 `uvicorn`） | 整目录拷贝会把 builder 的 `uv`/`uvx`/`pip` 一起带进 runtime；实测 backend **513MB → 351MB**、auth-service 163→131MB、forward-auth 112→80.6MB |
| **runtime 删 pip**、非 root `app` 用户、`PYTHONUNBUFFERED=1`、`PYTHONDONTWRITEBYTECODE=1` | 不写无用 `.pyc`、日志实时、降权限运行 |
| **`TZ=Asia/Shanghai`** | 只影响**日志时间戳**（业务时间判断在代码里显式用 `ZoneInfo("Asia/Shanghai")`，别改成读容器时区） |
| **OCI labels + `ARG VERSION/COMMIT/BUILD_DATE`**，由 CD 传入 `github.ref_name` / `github.sha` | 服务器上可回答“跑的是哪个版本”：`docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' stock-bot-backend:local`；`/health` 也会回报 `version`/`commit`（`backend/app/config.py` 的 `APP_VERSION`/`APP_COMMIT`） |
| **基础镜像用官方上游源**（`python:3.13-alpine` / `node:22` / `nginx:alpine` / `ghcr.io/astral-sh/uv` / `caddy:2-alpine`，**不指定第三方镜像源**） | 与业内一致、来源可审计，且官方源是真多架构（第三方源常只有 amd64）。代价：国内本机构建需配 registry mirror（见上）；CI 在 GitHub 上直连 Docker Hub，无需处理 |
| **基础镜像按 digest 固定**（`FROM <image>:<tag>@sha256:…`，见四个 Dockerfile 与 `docker-compose.prod.yml` 的 caddy） | 上游浮动 tag 一动，依赖层整层失效 —— 实测 v0.0.5 在服务器上重下了 ~300MB、耗时 14 分钟。固定后只要 lock 不变，依赖层 digest 就稳定，发版拉取只下几 MB。**升级姿势**：`docker buildx imagetools inspect <image> --format '{{.Manifest.Digest}}'` 取新 digest 替换（建议每季度或跟安全更新时做一次） |
| **服务级 `.dockerignore`** 排掉本地缓存/产物（`.mypy_cache`/`.pytest_cache`/`dist`/`e2e`/`test-results`…）与 `Dockerfile`/`docker-compose.yml` | 避免它们污染 `COPY . .` 的层缓存，也避免把构建文件塞进镜像 |

> **多架构现状（2026-09-21 起）**：四个 Dockerfile 的基础镜像已换成**官方上游**（`python:3.13-alpine` / `node:22` / `nginx:alpine` / `ghcr.io/astral-sh/uv`）+ digest 固定，官方源是**真多架构**（实测 digest 的 index 同时含 `linux/amd64` 与 `linux/arm64`），故 CD 的 arm64 产物是真 arm64。
>
> ⚠️ **但基建镜像仍来自华为 SWR 的 `ddn-k8s` 源**（`traefik` / `postgres` / `redis` / `rabbitmq`）+ `compose.prod` 之外的其它 image：实测这些源仍可能只有 amd64。所以**"整栈支持 arm64"这句话目前不成立** —— 若要部署到 arm64 服务器，需把基建镜像也换成官方多架构源（本次刻意没动：它们由服务器直接拉取，国内走 SWR 才快）。
>
> 国内**本机**构建官方基础镜像时 Docker Hub 通常不通，需要在 Docker Desktop → Settings → Docker Engine 配 `"registry-mirrors": ["https://docker.1ms.run"]`（digest 内容寻址，镜像源同样能命中同一份内容；服务器端不需要，因为服务器只拉 ghcr 的应用镜像与 SWR 的基建镜像，从不构建）。

**评估过但故意不做**（改回前请先测）：

- 删 `py_mini_racer`（47.8MB）、`pandas`/`numpy`/`curl_cffi`（共 ~137MB）：它们是 `akshare` 的运行时传递依赖，删了会让行业投研取数断掉。
- 预编译字节码（`compileall` / `UV_COMPILE_BYTECODE`）：启动略快但镜像涨约 10–20MB，而服务器部署的主要成本是 pull 体积，故不做。
- 删 frontend 的 `npm i @rollup/rollup-linux-x64-gnu --no-save`：它是为绕开“npm 未安装平台专属 optional 依赖”的已知坑而加的，删掉本地能过但 arm64 双架构构建风险未知，保留。

### 拉取与启动（prod override）

```bash
IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
```

`docker-compose.prod.yml` 只给 8 个应用服务指定 registry 镜像 + `pull_policy: always`（`migrate`/`migrate-auth`/`auth-service`/`forward-auth`/`api`/`worker`/`scheduler`/`frontend`），基建镜像仍走 `docker-compose.yml` 的 SWR 源，卷/网络/healthcheck/env 一律不动。`IMAGE_TAG` 是必填的（`${IMAGE_TAG:?…}`）：未设置时 compose 直接报 `required variable IMAGE_TAG is missing a value` 退出，避免静默停在旧镜像上。`--no-build` 保证服务器只消费镜像。

### 数据首次迁移（推荐先做数据、再起应用）

顺序铁律：**基建起 → 恢复数据 → 再起应用**（理由见「开机回填 footgun」）。反过来（应用先跑完 `migrate` 建表，再全量 `pg_restore`）会与既有 schema/数据冲突而失败。

实测规模（本机 `stock_bot` 库，`-Fc` 自定义格式）：全量 **501 MB / 45 s**；`pg_restore` 进空库 **71 s**。

```bash
# —— 1. 源端导出（在旧库所在机器执行）
# 全量：501 MB
docker exec postgres pg_dump -Fc -U stock_user -d stock_bot > stock_bot.dump

# 精简版：264 MB / 28 s，排除三张报表全量表与财务事实表（由 financial ingest 调度按需回补）
docker exec postgres pg_dump -Fc -U stock_user -d stock_bot \
  --exclude-table-data='financial_*' \
  --exclude-table-data='income_statement_facts' \
  --exclude-table-data='balance_sheet_facts' \
  --exclude-table-data='cash_flow_statement_facts' \
  > stock_bot.slim.dump

# 认证库很小（用户 + 会话快照），单独导出
docker exec auth-db pg_dump -Fc -U auth_user -d stock_bot_auth > stock_bot_auth.dump
```

```bash
# —— 2. 传输（二选一）
# a) 大文件可续传
rsync -P stock_bot.dump server:/srv/stock-bot/

# b) 不经中间文件：管道直送服务器容器
docker exec postgres pg_dump -Fc -U stock_user -d stock_bot \
  | ssh server 'docker exec -i postgres pg_restore -U stock_user -d stock_bot --no-owner'
```

```bash
# —— 3. 目标端恢复（在服务器仓库根目录）
# 3a. 只起基建（不要带应用）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres auth-db redis rabbitmq

# 3b. 恢复主库与认证库（目标库为空，postgres/auth-db 已 healthy）
docker exec -i postgres pg_restore -U stock_user -d stock_bot --no-owner < stock_bot.dump
docker exec -i auth-db  pg_restore -U auth_user  -d stock_bot_auth --no-owner < stock_bot_auth.dump

# 3c. 对账 + 迁移版本
docker exec postgres psql -U stock_user -d stock_bot -c "
SELECT 'daily_quotes' t, count(*) FROM daily_quotes
UNION ALL SELECT 'stocks', count(*) FROM stocks
UNION ALL SELECT 'concept_members', count(*) FROM concept_members
UNION ALL SELECT 'index_dailies', count(*) FROM index_dailies
UNION ALL SELECT 'daily_basic_indicators', count(*) FROM daily_basic_indicators
UNION ALL SELECT 'financial_metrics', count(*) FROM financial_metrics;
SELECT version_num FROM alembic_version;"
```

全量 dump 的预期对账值（实测恢复后逐项一致）：`daily_quotes` **4,377,954** / `stocks` **5,579** / `concept_members` **71,928** / `index_dailies` **16,979** / `daily_basic_indicators` **1,960,362** / `financial_metrics` **3,567,191**，且 `alembic_version = 2614ed9a9ab4`。用精简版 dump 时 `financial_*` 计数为 0 属预期。**不要**手工 `alembic stamp`：dump 里已带 `alembic_version`，版本一致时随后启动的 `migrate` 服务会直接 no-op。

```bash
# —— 4. 数据就位后再起应用
IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

**`redis`/`rabbitmq` 卷不随库迁移**：Redis 只是缓存 + 会话热数据（会话在 `stock_bot_auth` 有持久化快照），RabbitMQ 只承载在途任务。丢了只是「缓存冷启动 + 在途任务需重发」，不是权威数据丢失；反而把 `rabbitmq_data` 一起搬要额外处理节点身份（mnesia / erlang cookie），得不偿失。

### 开机回填 footgun（新服务器必读）

API 启动钩子（`backend/app/main.py` → `backend/app/services/data_init.py` 的 `maybe_seed_on_startup()`）是**无条件**执行的，没有任何 env 开关：

- `stocks` 表为空 → 后台任务拉全量股票名录 + 回补近 3 年 `daily_quotes` + 近 1 年 `daily_basic` + 近 1 年指数日线；
- `stocks` 非空 → 只做**覆盖度检查**并补缺口，数据完整时什么都不拉。

所以「新服务器 + 真实 `TUSHARE_TOKEN` + 空库」首次开机就会开始烧 TuShare 额度。两个安全做法：

1. **先恢复数据再起应用**（推荐，即上面的顺序）：覆盖度判定发现无缺口 → 回填是 no-op；
2. 若必须先起应用，把 `backend/.env` 的 `TUSHARE_TOKEN` **留空**让 seed 任务失败退出，等数据恢复完再填入并重启 `api`。

### 不要复制 `docker-compose.override.yml` 到服务器

本机有一份**未入库**的 `docker-compose.override.yml`（把 `postgres_data.name` 指到 `stock-bot_postgres_data`）。它只是本地开发机的卷名修正，**不要**复制到服务器：服务器上卷名应取 compose 项目默认，或与 `docker-compose.yml` 里 pin 的 `stock_bot_wt_p7_postgres_data` 一致。

卷名挂错的后果正好落进上面的 footgun：`postgres` 挂到新空卷 → `initdb` 出空库 → `data_init` 把「空 `stocks` 表」当成全新安装开始全量回补（烧一轮额度，而真正有数据的卷其实完好）。部署后先核卷名与数据量：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config | grep -A2 'postgres_data:'
docker volume ls | grep postgres
```

附带说明：显式带 `-f` 时 compose 不会自动加载 `docker-compose.override.yml`（本地那份不会悄悄改变服务器命令），但它是「环境假设」的典型来源，跨机复制前必须先对账卷名与数据量。

## 本地开发

### 后端

```bash
cd backend

# 安装依赖
uv pip install -e ".[dev]"

# 仅启动基础设施
docker compose up -d postgres redis rabbitmq

# 运行数据库迁移
alembic upgrade head

# 启动 API 服务（热重载）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 启动 Worker（另一终端）
python -m app.workers.runner
```

### 前端

```bash
cd frontend

# 安装依赖
npm ci

# 启动开发服务器（Vite 自动代理 /api → localhost:8000）
npm run dev
```

开发服务器地址：http://localhost:3000

### Linting & 格式化

```bash
# 后端
cd backend
ruff check app/            # 检查
ruff check --fix app/      # 自动修复
mypy app/                  # 类型检查

# 前端
cd frontend
npm run lint
```

### 测试

```bash
# 后端
cd backend
pytest                     # 运行全部测试
pytest -v                  # 详细输出
pytest tests/test_health.py  # 指定文件
```

## 数据验证

### 检查数据库

```bash
# 查看所有表
docker compose exec postgres psql -U stock_user -d stock_bot -c "\dt"

# 查看股票数据量
docker compose exec postgres psql -U stock_user -d stock_bot \
  -c "SELECT count(*) FROM stocks;"

# 查看任务状态
docker compose exec postgres psql -U stock_user -d stock_bot \
  -c "SELECT id, type, status, progress FROM tasks ORDER BY created_at DESC LIMIT 10;"
```

### 触发数据抓取

```bash
# 触发上交所股票列表抓取
curl -X POST http://localhost:8000/api/v1/tasks/fetch-universe \
  -H "Content-Type: application/json" \
  -d '{"exchange": "SSE", "source": "tushare"}'

# 查看任务状态
curl http://localhost:8000/api/v1/tasks
```

### 检查 JSONL 备份

```bash
# Worker 会将原始 API 响应写入 data/ 目录
ls -la data/
```

## 常用运维命令

```bash
# 查看所有容器状态
docker compose ps -a

# 查看指定服务日志
docker compose logs -f api          # API 日志（跟踪模式）
docker compose logs --tail 50 worker  # Worker 最近 50 行

# 重启单个服务
docker compose restart api

# 停止所有服务
docker compose down

# 停止并清除数据卷（谨慎！会删除数据库数据）
docker compose down -v

# 重新构建并启动
docker compose up --build -d
```

## 数据库迁移

```bash
# 在后端目录下创建新迁移
cd backend
alembic revision --autogenerate -m "描述变更内容"

# 应用迁移（容器环境中 migrate 服务会自动执行）
alembic upgrade head

# 回滚一步
alembic downgrade -1
```
