# 数据迁移与首次上线

顺序铁律：**基建起 → 恢复数据 → 再起应用**（理由见下方「开机回填 footgun」）。反过来（应用先跑完 `migrate` 建表，再全量 `pg_restore`）会与既有 schema/数据冲突而失败。

实测规模（本机 `stock_bot` 库，`-Fc` 自定义格式）：全量 **501 MB / 45 s**；`pg_restore` 进空库 **71 s**。

## 1. 源端导出

```bash
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

> **两个库要分别迁移**：业务主库（`postgres`）与认证库（`auth-db`）是**独立数据库、独立 Alembic 版本线**（[ADR 0002](../decisions/0002-split-auth-service-and-forward-auth.md)）。只迁主库会让登录态全失效。

## 2. 传输（二选一）

```bash
# a) 大文件可续传
rsync -P stock_bot.dump server:/srv/stock-bot/

# b) 不经中间文件：管道直送服务器容器
docker exec postgres pg_dump -Fc -U stock_user -d stock_bot \
  | ssh server 'docker exec -i postgres pg_restore -U stock_user -d stock_bot --no-owner'
```

## 3. 目标端恢复（在服务器仓库根目录）

```bash
# 3a. 只起基建（不要带应用）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres auth-db redis rabbitmq

# 3b. 恢复主库与认证库（目标库为空，postgres/auth-db 已 healthy）
docker exec -i postgres pg_restore -U stock_user -d stock_bot --no-owner < stock_bot.dump
docker exec -i auth-db  pg_restore -U auth_user  -d stock_bot_auth --no-owner < stock_bot_auth.dump
```

## 4. 对账（必做）

```bash
docker exec postgres psql -U stock_user -d stock_bot -c "
SELECT 'daily_quotes' t, count(*) FROM daily_quotes
UNION ALL SELECT 'stocks', count(*) FROM stocks
UNION ALL SELECT 'concept_members', count(*) FROM concept_members
UNION ALL SELECT 'index_dailies', count(*) FROM index_dailies
UNION ALL SELECT 'daily_basic_indicators', count(*) FROM daily_basic_indicators
UNION ALL SELECT 'financial_metrics', count(*) FROM financial_metrics;
SELECT version_num FROM alembic_version;"
```

全量 dump 的预期对账值（实测恢复后逐项一致）：

| 表 | 行数 |
|---|---|
| `daily_quotes` | 4,377,954 |
| `stocks` | 5,579 |
| `concept_members` | 71,928 |
| `index_dailies` | 16,979 |
| `daily_basic_indicators` | 1,960,362 |
| `financial_metrics` | 3,567,191 |

且 `alembic_version = 2614ed9a9ab4`。用精简版 dump 时 `financial_*` 计数为 0 属预期。

**不要**手工 `alembic stamp`：dump 里已带 `alembic_version`，版本一致时随后启动的 `migrate` 服务会直接 no-op。

## 5. 数据就位后再起应用

```bash
IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
```

## `redis`/`rabbitmq` 卷不随库迁移

Redis 只是缓存 + 会话热数据（会话在 `stock_bot_auth` 有持久化快照），RabbitMQ 只承载在途任务。丢了只是「缓存冷启动 + 在途任务需重发」，不是权威数据丢失；反而把 `rabbitmq_data` 一起搬要额外处理节点身份（mnesia / erlang cookie），得不偿失。

## 开机回填 footgun（新服务器必读）

API 启动钩子（`backend/app/main.py` → `backend/app/services/data_init.py` 的 `maybe_seed_on_startup()`）是**无条件**执行的，没有任何 env 开关：

- `stocks` 表为空 → 后台任务拉全量股票名录 + 回补近 3 年 `daily_quotes` + 近 1 年 `daily_basic` + 近 1 年指数日线
- `stocks` 非空 → 只做**覆盖度检查**并补缺口，数据完整时什么都不拉

所以「新服务器 + 真实 `TUSHARE_TOKEN` + 空库」首次开机就会开始烧 TuShare 额度。两个安全做法：

1. **先恢复数据再起应用**（推荐，即上面的顺序）：覆盖度判定发现无缺口 → 回填是 no-op
2. 若必须先起应用，把 `backend/.env` 的 `TUSHARE_TOKEN` **留空**让 seed 任务失败退出，等数据恢复完再填入并重启 `api`

卷名挂错也会触发同一路径（挂到新空卷 → `initdb` 出空库 → 被当成全新安装），见 [`production.md`](./production.md#不要复制-docker-composeoverrideyml-到服务器)。
