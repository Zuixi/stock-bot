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
| 内存吃紧 / 服务随机 502 | 先看**宿主机非容器进程**（`ps -eo rss,etime,args --sort=-rss`），再看容器 cgroup `anon` | 实测宿主机遗留的 agent 进程曾占 951MB，比所有业务容器之和还多；`docker stats` 的 `MemUsage` 含页缓存，据此判"泄漏"会误判（见下节） |
| 某容器循环重启、日志却无异常 | `docker inspect <c> --format '{{.State.OOMKilled}}'` + `dmesg -T \| grep -i oom` | 触到 `mem_limit` 会被 OOM kill，叠加 `restart: unless-stopped` 就变成"随机 502 + 反复重启" |

## 观察数据新鲜度

数据面健康度的权威入口是 **`/api/v1/...` 的新鲜度端点**（由对账式自愈数据面提供，见 `plans/2026-09-17-data-sync-self-healing.md`）：

```bash
curl -s localhost/api/v1/market/data-freshness | head -c 400   # 各表最新业务时间 / 缺口 / 上游状态
curl -s localhost/api/v1/health | head -c 200                  # version / commit / 依赖健康
```

对账式自愈的设计目标：宿主睡眠、Docker Desktop Resource Saver、容器重启、任务异常之后，系统**在恢复点自动收敛到完整状态**，不依赖"定时任务准点跑"。因此排查数据缺口的第一步不是找那条失败日志，而是看新鲜度端点报了哪些缺口。

## 内存与 OOM 排障

先分清**三层**，别一上来就"清缓存"：

| 层 | 怎么看 | 说明 |
|---|---|---|
| 宿主机非容器进程 | `ps -eo rss,pmem,etime,args --sort=-rss \| head -20` | **最容易出意外的一层**：实测曾有 4 个与业务无关的 agent 进程（`hermes`/`pi`，跑了 4~6 天）合计 951MB，比全部业务容器加起来还多。回收后 `used 3011→2060MB` |
| 容器真实占用 | `grep -m1 '^anon ' /sys/fs/cgroup/system.slice/docker-<id>.scope/memory.stat` | `docker stats` 的 `MemUsage` **含页缓存**：实测 postgres 显示 596MB，其中 374MB 是可回收页缓存（真实 `anon` 仅 207MB，`shared_buffers` 才 32MB）。判泄漏只看 `anon` |
| 页缓存 | `free -h` 的 `buff/cache`（容器内同理） | **健康缓存，不要清** —— `drop_caches` 只是丢掉再慢慢读回来，反而更慢；`docker image prune` 只省磁盘不省内存 |

容器 `mem_limit` 的取值口径与限值表见 [`production.md`](./production.md#内存上限服务器专用改之前先读)。

```bash
# OOM 是否发生过 / 谁被杀
dmesg -T | grep -iE "oom|killed process" | tail -20
docker inspect <container> --format '{{.State.OOMKilled}} restarts={{.RestartCount}}'

# 各容器 当前 / 峰值 / 真实 anon（cgroup v2）
for d in /sys/fs/cgroup/system.slice/docker-*.scope; do
  id=$(basename "$d" | sed 's/docker-\(.*\)\.scope/\1/')
  [ -e "$d/memory.current" ] || continue
  printf '%s cur=%s peak=%s anon=%s\n' \
    "$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null | tr -d /)" \
    "$(awk '{printf "%.0fM", $1/1048576}' "$d/memory.current")" \
    "$(awk '{printf "%.0fM", $1/1048576}' "$d/memory.peak")" \
    "$(grep -m1 '^anon ' "$d/memory.stat" | awk '{printf "%.0fM", $2/1048576}')"
done
```

**没有 swap 是这台机器最大的结构性风险**：`Committed_AS` 曾达 6.6GB 而 `CommitLimit` 仅 1.9GB，一次内存尖峰会**直接 OOM kill**（而不是变慢）。加 2GB swap（需 root）：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile
sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swap.conf
```

容器侧 `memswap_limit` 与 `mem_limit` **同值** —— 容器内可用 swap 为 0，宿主机即使加了 swap，业务热数据也不会被换出（避免 P99 抖动），触顶时只 OOM kill 该容器自己。⚠️ 不要用 `mem_swappiness`：compose 会**静默丢弃**该键（无告警、`docker inspect` 仍是 nil），已由 `scripts/doc_gate.sh` 第 13 项机械拦截。
