# 本地开发

环境变量（两个 env 文件的分工、宿主↔容器键名映射、自证命令）见 [`index.md`](./index.md#环境变量配置)。

## 后端

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

启动 scheduler 的定时任务见 `backend/app/scheduler/runner.py`（第 5 步的服务清单中有其容器形态）。

## 前端

```bash
cd frontend

# 安装依赖
npm ci

# 启动开发服务器（Vite 自动代理 /api → localhost:8000）
npm run dev
```

开发服务器地址：http://localhost:3000

> 直连 `:8000` 不经过 gateway，因此**没有身份上下文**——需要登录态的功能必须在 gateway（http://localhost）下验证，见 [ADR 0002](../decisions/0002-split-auth-service-and-forward-auth.md)。

## 静态校验的唯一口径

与 CI 完全一致（[`../testing/index.md`](../testing/index.md) 的门禁矩阵是权威）：

```bash
# 后端：check 与 format 都要跑，只跑 check 会出现本地绿、CI 红
cd backend
uv run --extra dev ruff check .
uv run --extra dev ruff format --check app/ tests/
uv run --extra dev mypy app

# 前端：静态校验 = tsc（本仓库不引入 eslint，见 ADR 0006）
cd frontend
npx tsc --noEmit
```

> **`npm run lint` 不要用**：`package.json` 里保留了该脚本但它调用 `eslint .`，而本仓库既未安装 eslint 也无配置文件，必然失败。静态校验以 `npx tsc --noEmit` 为准（[ADR 0006](../decisions/0006-no-eslint-tsc-as-static-check.md)）。

一次性把上面全部 + 测试跑完：

```bash
bash scripts/self_review.sh --full
```

## 本地起栈的注意点

- **本机缓存/DB 测试需要显式 `REDIS_URL`**：本机 redis 映射在 **6380**，而代码默认 `localhost:6379`；`backend/.env` 未设 `REDIS_URL` 时 `CacheClient` 会把连接错误静默降级为 cache miss，导致任何"缓存命中/热路径"的测量全部失真。跑本地性能脚本时先 `export REDIS_URL=redis://localhost:6380/0`
- **跑 pytest 前清空 `TUSHARE_TOKEN`**（`self_review.sh --full` 已代劳）：本机 `backend/.env` 有真实 token 时，会掩盖"单测偷偷依赖真 client / 真网络"的缺口，表现为本地全绿、CI 全红
- **写侧时间窗口不要用 `date.today()`**：任何"窗口上界 = 今天"的任务会把当日半截行情写入，`max(trade_date)` 被顶到今天后全站退化。写侧按"上一个已完成交易日"收口——这是本仓库反复踩过的坑，见 [`../references/best-practices.md`](../references/best-practices.md)「数据源与采集」

## 数据验证

```bash
# 查看所有表
docker compose exec postgres psql -U stock_user -d stock_bot -c "\dt"

# 查看股票数据量
docker compose exec postgres psql -U stock_user -d stock_bot \
  -c "SELECT count(*) FROM stocks;"

# 逐日检查行情完整性（只看 max(trade_date) 会漏掉中间空洞）
docker compose exec postgres psql -U stock_user -d stock_bot \
  -c "SELECT trade_date, count(*) FROM daily_quotes GROUP BY 1 ORDER BY 1 DESC LIMIT 10;"

# 查看任务状态
docker compose exec postgres psql -U stock_user -d stock_bot \
  -c "SELECT id, type, status, progress FROM tasks ORDER BY created_at DESC LIMIT 10;"
```

### 触发数据抓取

```bash
# 触发上交所股票列表抓取（经本地 gateway）
curl -X POST http://localhost/api/v1/tasks/fetch-universe \
  -H "Content-Type: application/json" \
  -d '{"exchange": "SSE", "source": "tushare"}'

# 查看任务状态
curl http://localhost/api/v1/tasks
```

### 检查 JSONL 备份

```bash
# Worker 会将原始 API 响应写入 data/ 目录
ls -la data/
```

原始响应 JSONL 是对账"库里的行数与上游实际给了什么"的最终依据（多条数据缺失类 bug 都是靠它定位的）。
