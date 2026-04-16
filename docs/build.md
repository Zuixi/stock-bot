# 服务构建与部署指南

## 前提条件

- Docker >= 24.0
- Docker Compose V2（`docker compose` 命令）
- Node.js >= 22（仅本地开发或预构建前端时需要）
- Python >= 3.11 + uv（仅本地开发时需要）

## 项目结构

```
stock_bot/
├── docker-compose.yml          # 根目录统一编排
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

`docker-compose.yml` 编排了 7 个服务，按依赖关系自动启动：

1. **基础设施层**：`postgres`、`redis`、`rabbitmq` 并行启动，等待健康检查通过
2. **数据库迁移**：`migrate` 容器运行 `alembic upgrade head`，创建/更新表结构后退出
3. **应用层**：`api`（FastAPI）和 `worker`（RabbitMQ 消费者）在迁移完成后启动
4. **前端层**：`frontend`（nginx）在 API 健康检查通过后启动

### 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| frontend | http://localhost:3000 | Web 前端 + API 反向代理 |
| api | http://localhost:8000 | FastAPI 后端（含 /docs Swagger UI） |
| postgres | localhost:5433 | PostgreSQL（映射到 5433 避免冲突） |
| redis | localhost:6379 | Redis 缓存 |
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
