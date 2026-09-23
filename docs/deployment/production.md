# 生产部署与发布

服务器上**不构建任何镜像**：应用镜像由 CI 发布到 `ghcr.io`，服务器只 `pull` 发布产物。源码树在服务器上仍需存在，但只用于提供 `docker-compose.yml` / `docker-compose.prod.yml` 与 `gateway/` 配置。

服务清单、端口与启动顺序见 [`index.md`](./index.md) 与 [`../ARCHITECTURE.md`](../ARCHITECTURE.md)（此处不重复表格）。

## 发布流程（本地打 tag → CI → ghcr.io）

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

每个镜像打三个 tag：`0.0.1`、`0.0`（major.minor）与 `<short-sha>`。服务器部署**只用 `<version>`**（如 `0.0.1`），不要用 `0.0` 这类浮动 tag。发布后在 GitHub Actions 运行记录 / 仓库 Packages 页确认**四个**镜像都出现，再上服务器。

> 坑：tag 必须在 `v*` 形状内 —— 推了裸数字 tag（如 `0.0.1`）CD **不会触发**，表现为「tag 推上去了但 ghcr 里没有镜像，服务器 `pull` 报 manifest unknown」。首次发版前建议先推一个预发布 tag（如 `v0.0.2-rc1`）走通全流程。

## 服务器准备

```bash
# 1. GHCR 拉取凭据（PAT 只需 read:packages）
docker login ghcr.io -u <github-user> -p <PAT-with-read:packages>

# 2. 环境变量：用**生产模板**（带占位符与上线清单），两个文件角色不同
cp .env.production.example .env                    # compose 插值：APP_ENV / JWT 密钥 / INTERNAL_API_TOKEN / Cookie 与 XFF 开关 / MQ 口令
cp backend/.env.production.example backend/.env    # 后端运行时：TUSHARE_TOKEN / 业务侧 APP_ENV / CORS_ORIGINS

# 3. 生成 JWT 密钥对（两个模板里都有同样的命令，二选一填）
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out private.pem
openssl pkey -in private.pem -pubout -out public.pem
```

**上线前必须处理的两个默认值**：

1. **数据库口令硬编码**：`postgres` / `auth-db` 服务的 `POSTGRESQL_*` 与 api/worker/scheduler/migrate 的 `DATABASE_URL` 都写死在 `docker-compose.yml`（`stock_user/stock_pass`、`auth_user/auth_pass`）。根 `.env` 里的 `POSTGRES_*` **不会被消费** —— 要换口令得同时改这些行。
2. **对外暴露面**：只有 `gateway` 绑 `0.0.0.0:80`（TLS 就绪后加 443）。`postgres`/`redis` 已改为**只绑回环**（`127.0.0.1:5433` / `127.0.0.1:6380`），宿主机上的本地开发与 `pytest` 照常可用，外部网络访问不到；若要彻底不绑，删掉 `docker-compose.yml` 里那两行 `ports:` 即可。核验：`docker compose ps` 应只见 gateway 有 `0.0.0.0:` 映射。

若服务器无法访问 `ghcr.io`，可在能同时访问 ghcr 与华为 SWR 的机器上转推（`docker pull` → `docker tag` → `docker push`）到现有 `swr.cn-north-4.myhuaweicloud.com` 命名空间，再把 `docker-compose.prod.yml` 里的 `image:` 前缀换成 SWR 地址，其余不变。

## 边缘层：Caddy 自动 HTTPS + Traefik 路由（**两跳**，不是三跳）

```
Internet :80/:443 → caddy（TLS 终结、自动签发/续期）→ gateway:80（Traefik：路由/鉴权/限流）
                  → api / frontend / auth-service / forward-auth …
```

**为什么这么分**：路由与鉴权规则（`forward-auth` 注入 Principal Assertion、`strip-assertion` 防伪造、三组限流、安全头）全在 Traefik 的 labels/动态配置里；Caddy 只做 TLS 与透传，**不重复任何路由规则**（两个代理各写一套规则必然长期漂移）。也曾评估过"Caddy → Nginx → Traefik"三跳：多一跳只增加 XFF/真实 IP 传歪的机会，且 Nginx 在本栈里的角色已经由 `frontend` 容器内的静态服务占据，收益为零。

改配置时注意四处耦合：

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
- 上线后把 `APP_ENV` 翻成 `production`、`AUTH_COOKIE_SECURE=true`（两处 env 都要，见 [`index.md`](./index.md#环境变量配置)），并把 `CORS_ORIGINS` 改成 `https://<域名>`

## 镜像构建（做了哪些优化，改 Dockerfile 前请先读）

四个应用镜像（`backend` / `auth-service` / `forward-auth` / `frontend`）统一遵循下面几条，改构建时别把它们回退：

| 约定 | 原因 / 实测效果 |
|---|---|
| **依赖层先于源码层**（先 `COPY pyproject.toml uv.lock` → 装依赖 → 最后 `COPY . .`） | 改业务代码只失效最后一层，依赖缓存命中 |
| **runtime 只拷白名单 console script**（backend/auth-service：`uvicorn`+`alembic`；forward-auth：仅 `uvicorn`） | 整目录拷贝会把 builder 的 `uv`/`uvx`/`pip` 一起带进 runtime；实测 backend **513MB → 351MB**、auth-service 163→131MB、forward-auth 112→80.6MB |
| **runtime 删 pip**、非 root `app` 用户、`PYTHONUNBUFFERED=1`、`PYTHONDONTWRITEBYTECODE=1` | 不写无用 `.pyc`、日志实时、降权限运行 |
| **`TZ=Asia/Shanghai`** | 只影响**日志时间戳**（业务时间判断在代码里显式用 `ZoneInfo("Asia/Shanghai")`，别改成读容器时区） |
| **OCI labels + `ARG VERSION/COMMIT/BUILD_DATE`**，由 CD 传入 `github.ref_name` / `github.sha` | 服务器上可回答"跑的是哪个版本"：`docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' stock-bot-backend:local`；`/health` 也会回报 `version`/`commit`（`backend/app/config.py` 的 `APP_VERSION`/`APP_COMMIT`） |
| **基础镜像用官方上游源**（`python:3.13-alpine` / `node:22` / `nginx:alpine` / `ghcr.io/astral-sh/uv` / `caddy:2-alpine`，**不指定第三方镜像源**） | 与业内一致、来源可审计，且官方源是真多架构（第三方源常只有 amd64）。代价：国内本机构建需配 registry mirror；CI 在 GitHub 上直连 Docker Hub，无需处理 |
| **基础镜像按 digest 固定**（`FROM <image>:<tag>@sha256:…`，见四个 Dockerfile 与 `docker-compose.prod.yml` 的 caddy） | 上游浮动 tag 一动，依赖层整层失效 —— 实测 v0.0.5 在服务器上重下了 ~300MB、耗时 14 分钟。固定后只要 lock 不变，依赖层 digest 就稳定，发版拉取只下几 MB。**升级姿势**：`docker buildx imagetools inspect <image> --format '{{.Manifest.Digest}}'` 取新 digest 替换（建议每季度或跟安全更新时做一次） |
| **服务级 `.dockerignore`** 排掉本地缓存/产物（`.mypy_cache`/`.pytest_cache`/`dist`/`e2e`/`test-results`…）与 `Dockerfile`/`docker-compose.yml` | 避免它们污染 `COPY . .` 的层缓存，也避免把构建文件塞进镜像 |

> **多架构现状（2026-09-21 起）**：四个 Dockerfile 的基础镜像已换成**官方上游** + digest 固定，官方源是**真多架构**（实测 digest 的 index 同时含 `linux/amd64` 与 `linux/arm64`），故 CD 的 arm64 产物是真 arm64。
>
> ⚠️ **但基建镜像仍来自华为 SWR 的 `ddn-k8s` 源**（`traefik` / `postgres` / `redis` / `rabbitmq`）+ `compose.prod` 之外的其它 image：实测这些源仍可能只有 amd64。所以**"整栈支持 arm64"这句话目前不成立** —— 若要部署到 arm64 服务器，需把基建镜像也换成官方多架构源（本次刻意没动：它们由服务器直接拉取，国内走 SWR 才快）。
>
> 国内**本机**构建官方基础镜像时 Docker Hub 通常不通，需要在 Docker Desktop → Settings → Docker Engine 配 `"registry-mirrors": ["https://docker.1ms.run"]`（digest 内容寻址，镜像源同样能命中同一份内容；服务器端不需要，因为服务器只拉 ghcr 的应用镜像与 SWR 的基建镜像，从不构建）。

**评估过但故意不做**（改回前请先测）：

- 删 `py_mini_racer`（47.8MB）、`pandas`/`numpy`/`curl_cffi`（共 ~137MB）：它们是 `akshare` 的运行时传递依赖，删了会让行业投研取数断掉。
- 预编译字节码（`compileall` / `UV_COMPILE_BYTECODE`）：启动略快但镜像涨约 10–20MB，而服务器部署的主要成本是 pull 体积，故不做。
- 删 frontend 的 `npm i @rollup/rollup-linux-x64-gnu --no-save`：它是为绕开"npm 未安装平台专属 optional 依赖"的已知坑而加的，删掉本地能过但 arm64 双架构构建风险未知，保留。

## 拉取与启动（prod override）

```bash
IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
```

`docker-compose.prod.yml` 只给 8 个应用服务指定 registry 镜像 + `pull_policy: always`（`migrate`/`migrate-auth`/`auth-service`/`forward-auth`/`api`/`worker`/`scheduler`/`frontend`），基建镜像仍走 `docker-compose.yml` 的 SWR 源，卷/网络/healthcheck/env 一律不动。`IMAGE_TAG` 是必填的（`${IMAGE_TAG:?…}`）：未设置时 compose 直接报 `required variable IMAGE_TAG is missing a value` 退出，避免静默停在旧镜像上。`--no-build` 保证服务器只消费镜像。

首次上线需要先迁数据，见 [`data-migration.md`](./data-migration.md)。

## 不要复制 `docker-compose.override.yml` 到服务器

本机有一份**未入库**的 `docker-compose.override.yml`（把 `postgres_data.name` 指到 `stock-bot_postgres_data`）。它只是本地开发机的卷名修正，**不要**复制到服务器：服务器上卷名应取 compose 项目默认，或与 `docker-compose.yml` 里 pin 的 `stock_bot_wt_p7_postgres_data` 一致。

卷名挂错的后果正好落进开机回填 footgun（[`data-migration.md`](./data-migration.md#开机回填-footgun新服务器必读)）：`postgres` 挂到新空卷 → `initdb` 出空库 → `data_init` 把"空 `stocks` 表"当成全新安装开始全量回补（烧一轮额度，而真正有数据的卷其实完好）。部署后先核卷名与数据量：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config | grep -A2 'postgres_data:'
docker volume ls | grep postgres
```

附带说明：显式带 `-f` 时 compose 不会自动加载 `docker-compose.override.yml`（本地那份不会悄悄改变服务器命令），但它是"环境假设"的典型来源，跨机复制前必须先对账卷名与数据量。

## 改端口 / 服务名 / 镜像名时的同步义务

`docs/deployment/index.md` 的端口表是**唯一权威副本**，改 `docker-compose*.yml` 时必须同步核对：

- [`index.md`](./index.md) 的服务与端口表（本文档）
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) 的服务职责表
- [`../../README.md`](../../README.md) 与相关 `AGENTS.md`（`backend/AGENTS.md`、`frontend/AGENTS.md`）
- [`../../docs/index.md`](../index.md) 的路由表（若涉及"改端口要读什么"）

`bash scripts/doc_gate.sh` 会校验端口表与 compose 实际映射一致；漏改会被门禁拦下。
