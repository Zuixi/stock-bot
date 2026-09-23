# 日常运维与排障

## 常用运维命令

```bash
# 查看所有容器状态
docker compose ps -a

# 查看指定服务日志
docker compose logs -f api              # API 日志（跟踪模式）
docker compose logs --tail 50 worker    # Worker 最近 50 行
docker compose logs --tail 50 scheduler # 定时任务（注意：见下方"executed successfully 不是成功信号"）

# 重启单个服务
docker compose restart api

# 停止所有服务
docker compose down

# 停止并清除数据卷（谨慎！会删除数据库数据与 Caddy 证书卷）
docker compose down -v

# 重新构建并启动
docker compose up --build -d

# 生产：指定镜像版本重建
IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
```

## 数据库迁移

```bash
# 创建新迁移（在后端目录下）
cd backend
alembic revision --autogenerate -m "描述变更内容"

# 应用迁移（容器环境中 migrate 服务会自动执行）
alembic upgrade head

# 回滚一步
alembic downgrade -1
```

> 并列分支各自新增迁移时，需提前备好 **merge revision**（`down_revision=(a, b)`、空 `upgrade`），否则 `migrate` 服务会以 `Can't locate revision` 卡住整栈启动。手工建过表的库用 `alembic stamp <rev>` 记录版本。

## 排障清单

按"症状 → 先查什么"排列，全部是本仓库实际踩过的：

| 症状 | 先查 | 说明 |
|---|---|---|
| 数据停在某天、看不出报错 | `scheduler` 日志里 job 是否只有 `executed successfully` | **该字符串只表示 job 函数返回了**，不代表业务成功。曾经某 job 每交易日抛 `asyncpg ... cannot exceed 32767` 却仍打印成功，数据静默停更 13 天。失败必须以可查询的失败记录 + 数据新鲜度暴露 |
| 某天行情行数够但指标算不出来 | 关键列非空率：`SELECT count(*) FILTER (WHERE pct_chg IS NOT NULL) FROM daily_quotes WHERE trade_date=<d>` | "行数够"≠"数据可用"。完整性判据必须含列级检查 |
| 中间某天缺数据 | `SELECT trade_date, count(*) ... GROUP BY 1 ORDER BY 1 DESC` | 只看 `max(trade_date)` 会漏掉中间空洞 |
| 部分标的所有页面空白 | 名录表是否刷新（`stocks` 行数）、采集日志的 `skipped` 计数 | `ts_code → stock_id` 映射不到就静默 skip；映射表冻结时新股数据每天被丢弃 |
| 容器里读不到 `data/` 种子文件 | `backend/.dockerignore` 是否放了 `data/*` 豁免 | 漏豁免会让加载器**静默返回 0**（本地有、生产没有） |
| 前端某控件永久不可用 | 该控件 disabled 的判据是否有真实回补路径 | 曾有文案写"后台拉取中"而根本没有对应任务 |
| 卡片永久空白 | 读路径是否按"今天有数据吗"过滤 | 应改为"最近可用快照 + `stale_days` + `source_status`" |
| 本地缓存/性能测量失真 | `REDIS_URL` 是否指向 **6380** | 未设时 `CacheClient` 静默降级为 cache miss，测量全部失真 |
| 两个容器抢同一端口 | `docker compose.prod.yml` 的 `gateway.ports: !override []` | 见 [`production.md`](./production.md#边缘层caddy-自动-https--traefik-路由两跳不是三跳) |
| 证书重新签发/被限流 | `stock-bot_caddy_data` 卷是否被 `down -v` 删掉 | Let's Encrypt 同域名每周重复签发有限额 |
| 定时任务全部被跳过 | job 内时间判断是否用了 naive `datetime.now()` | 容器默认 UTC，必须显式 `ZoneInfo("Asia/Shanghai")` 且与 `CronTrigger` 同源 |

## 观察数据新鲜度

数据面健康度的权威入口是 **`/api/v1/...` 的新鲜度端点**（由对账式自愈数据面提供，见 `plans/2026-09-17-data-sync-self-healing.md`）：

```bash
curl -s localhost/api/v1/market/data-freshness | head -c 400   # 各表最新业务时间 / 缺口 / 上游状态
curl -s localhost/api/v1/health | head -c 200                  # version / commit / 依赖健康
```

对账式自愈的设计目标：宿主睡眠、Docker Desktop Resource Saver、容器重启、任务异常之后，系统**在恢复点自动收敛到完整状态**，不依赖"定时任务准点跑"。因此排查数据缺口的第一步不是找那条失败日志，而是看新鲜度端点报了哪些缺口。
