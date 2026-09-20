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
├── .env.docker.example         # 环境变量模板
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

1. 从模板创建后端环境变量文件：

```bash
cp .env.docker.example backend/.env
```

2. 编辑 `backend/.env`，填入真实的 `TUSHARE_TOKEN`（从 https://tushare.pro 获取）。

其他默认值已适配容器内部网络（服务名 `postgres`、`redis`、`rabbitmq`）。

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

| 服务 | 端口 | 说明 |
|------|------|------|
| frontend | http://localhost:3000 | Web 前端 + API 反向代理 |
| api | http://localhost:8000 | FastAPI 后端（含 /docs Swagger UI） |
| postgres | localhost:5433 | PostgreSQL（映射到 5433 避免冲突） |
| redis | localhost:6380 | Redis 缓存（映射到 6380 避免冲突） |
| rabbitmq | localhost:5672 / 15672 | RabbitMQ（15672 为管理面板） |

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

# 2. 打 tag 并推送（`0.0.1` 与 `v0.0.1` 等价，二选一）
git tag 0.0.1          # 或 git tag v0.0.1
git push origin 0.0.1
```

`.github/workflows/cd.yml` 的 tag filters 是 `["v*", "0.*"]`，推 tag 后为 4 个构建上下文各发一个镜像：

| 镜像 | 构建上下文 | 对应的 compose 服务 |
|------|-----------|-------------------|
| `ghcr.io/zuixi/stock-bot/backend` | `./backend` | `migrate` / `api` / `worker` / `scheduler` |
| `ghcr.io/zuixi/stock-bot/auth-service` | `./auth-service` | `migrate-auth` / `auth-service` |
| `ghcr.io/zuixi/stock-bot/forward-auth` | `./forward-auth` | `forward-auth` |
| `ghcr.io/zuixi/stock-bot/frontend` | `./frontend`（`target: runtime`） | `frontend` |

每个镜像会打三个 tag：`0.0.1`、`0.0`（major.minor）与 `<short-sha>`。服务器部署**只用 `<version>`**（如 `0.0.1`），不要用 `0.0` 这类浮动 tag。发布后在 GitHub Actions 运行记录 / 仓库 Packages 页确认**四个**镜像都出现，再上服务器。

> 坑：旧 filters 只有 `v*`，裸 tag `0.0.1` 不触发 CD —— 表现为「tag 推上去了但 ghcr 里没有镜像，服务器 `pull` 报 manifest unknown」。改 glob 后先推一个预发布 tag（如 `0.0.2-rc1`，同样命中 `0.*`）验证 4 个镜像齐全，再发正式版。

### 服务器准备

```bash
# 1. GHCR 拉取凭据（PAT 只需 read:packages）
docker login ghcr.io -u <github-user> -p <PAT-with-read:packages>

# 2. 环境变量模板（默认值已适配容器内部网络；真实 TUSHARE_TOKEN 见下「开机回填 footgun」）
cp .env.docker.example backend/.env
```

若服务器无法访问 `ghcr.io`，可在能同时访问 ghcr 与华为 SWR 的机器上转推（`docker pull` → `docker tag` → `docker push`）到现有 `swr.cn-north-4.myhuaweicloud.com` 命名空间，再把 `docker-compose.prod.yml` 里的 `image:` 前缀换成 SWR 地址，其余不变。

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
