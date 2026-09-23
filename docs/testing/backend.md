# 后端测试

## 分层与 marker

marker 定义在 `backend/pyproject.toml`，默认 `addopts` 已含 `-m 'not e2e and not bench'` —— **跑 `pytest` 不会碰 e2e 与基准**。

| 类型 | 位置 | 依赖 | 何时跑 |
|---|---|---|---|
| 纯单测 | `backend/tests/test_*.py`（无 marker） | 无（不碰网络、不碰真 DB） | 每次 commit（pre-commit） |
| 集成（需 DB/Redis） | 同上 | 本地 `postgres:5433` / `redis:6380` | PR CI（CI 起 services）/ 手动 `--full` |
| 后端 e2e（`e2e` marker） | 如 `tests/test_industry_e2e.py` | 运行中的 docker compose 栈（api+worker+rabbitmq） | **手动** `pytest -m e2e` |
| 性能基准（`bench` marker） | `backend/tests/benchmarks/` | 无 | `scripts/bench.sh`，见 [`benchmarks.md`](./benchmarks.md) |

## 两条硬约定

### 1. 纯单测不得依赖真实外部资源

不要为了"跑通"而让单测去调 TuShare / 东财。需要外部数据时**用快照 fixture**（下节）。

CI 口径与本机口径必须一致：`self_review.sh --full` 跑 pytest 时会显式 **`TUSHARE_TOKEN=`**（清空）。原因是本机 `backend/.env` 里有真实 token，会把"单测偷偷依赖真 client / 真网络"的缺口盖住，表现为**本地全绿、CI 全红**。曾有一轮 `test_reconciliation_service` 的 `_get_tushare()` seam 缺失就是这样藏了一整轮。

> 本地复现 CI 结论的标准动作：`cd backend && TUSHARE_TOKEN= uv run pytest -m "not e2e and not bench" -q --no-cov`

### 2. 需要 DB 的用例要能连到测试库

`conftest.py` 的默认 `TEST_DATABASE_URL` 指向 `stock_bot_test`（本机 postgres 映射在 **5433**）。CI 里由 service 容器提供，并用 `alembic upgrade head` 建表。

`conftest.py` 的 `client` fixture 打的是**真实 HTTP API**（`API_BASE_URL`，默认 `host.docker.internal:8000`），属于早期 e2e 形态的遗留。**新写用例优先直接调 service/repo 层**，不要为了用 `client` 而要求一个运行中的 API。

## 快照 fixture 规范

`backend/tests/fixtures/` 下按数据源分目录（如 `fixtures/eastmoney/`），存放**上游真实响应的原样快照**（`board_list_industry.json`、`board_stocks_bk1518.json`、`zt_pool_20260917.json` 等）。

作用：把"解析函数对真实页面形状的假设"离线钉死。上游改版时，快照更新本身就是一次可 review 的 diff。

约定：

- **网络抓取壳与纯解析函数分离**：解析函数吃快照做单测；抓取壳只做发现与容错（任何失败 log 后返回 None，不抛穿）
- 快照命名带上游标识与日期（如 `zt_pool_20260917.json`），不要用 `sample.json` 这类无信息名
- 接新源时**先实机验证、再写适配器**，然后把实机结论（日期、包版本、列名、单位）写进快照注释或表驱动规格

## 新增数据源时的最小测试清单

按"client → ingest → model/迁移 → repo → service → worker → API 端点"链路各留一层断言。优先锁死这两类**纯单测**不变量：

1. **`source` 名一致性**：fetcher 实际写入的 `source` 名 ⊆ `registry` 声明的集合。写错名会让源优先级裁决永远匹配不到真实行（且不报错）
2. **演示/mock 源排序**：mock 源必须永远垫底；真实源首次成功落库后应主动清除 mock 行，**且连同派生行一起删**（派生只 upsert 不删除，漏掉会让 mock 算出的 derived 序列继续喂给规则引擎）

## 断言时的常见陷阱

- **时间**：容器默认 UTC。涉及交易日/时段的用例要显式 `ZoneInfo("Asia/Shanghai")`；断言"上一交易日"必须查 `trade_cal` 而不是 `weekday()` 推算
- **缺失 ≠ 0**：停牌股要断言 `missing_days` 被回传，而不是折算成 0%
- **不要断言行数当完整性**：完整性判据是三元组（行数 ≥ 0.9×universe + 关键列非空率 ≥ 0.99 + 依赖表存在）
- **SQL 常量易错点**：gaps-and-islands 的 `PARTITION BY` 必须带分组列本体；候选集合取两日并集必须 `DISTINCT`
