# Best Practices

项目开发过程中沉淀的经验教训。**按主题分文件存放**，本文件是入口：检索方式（探测器映射表）+ 写入门槛。

> 2026-09-23：原 286 行单文件按 8 个分类拆入 `best-practices/`（内容原样搬移，未改写）。原因与文档系统分层设计见 `plans/2026-09-23-documentation-system.md`。

## 分类

| 分类 | 文件 | 条目 |
|---|---|---|
| 一、数据源与采集 | [`best-practices/data-source.md`](./best-practices/data-source.md) | 47 |
| 二、数据库与性能 | [`best-practices/database-performance.md`](./best-practices/database-performance.md) | 18 |
| 三、Docker 与部署 | [`best-practices/docker-deploy.md`](./best-practices/docker-deploy.md) | 11 |
| 四、前端（React / antd / ECharts） | [`best-practices/frontend.md`](./best-practices/frontend.md) | 66 |
| 五、测试与 E2E | [`best-practices/testing.md`](./best-practices/testing.md) | 27 |
| 六、架构与分层 | [`best-practices/architecture.md`](./best-practices/architecture.md) | 12 |
| 七、指标建模与规则引擎 | [`best-practices/metrics-rule-engine.md`](./best-practices/metrics-rule-engine.md) | 5 |
| 八、工程流程与文档（元经验） | [`best-practices/process-docs.md`](./best-practices/process-docs.md) | 27 |

## 自检探测器映射表

完成前自检第二步：跑 `bash scripts/slop_scan.sh`（或对 `git diff` 按下方映射 grep），命中即复核对应分类。命中本身不一定是错误，但必须确认没有重复踩雷。

| 分类 | 探测器关键词（`git diff | grep -i <key>`） |
|------|---------------------------------------------|
| [数据源与采集](./best-practices/data-source.md) | `source` · `registry` · `mock` · `trade_cal` · `ZoneInfo` · `Asia/Shanghai` · `to_thread` · `RUN_SCHEDULER` · `scheduler` · `worker` · `QUEUES` · `_get_tushare` · `TUSHARE_TOKEN` |
| [数据库与性能](./best-practices/database-performance.md) | `DISTINCT ON` · `LATERAL` · `N+1` · `index(` · `ON CONFLICT` · `COALESCE` · `::date` · `SELECT` |
| [Docker 与部署](./best-practices/docker-deploy.md) | `Dockerfile` · `dockerignore` · `COPY --from` · `resolver` · `target: runtime` · `seed` · `service_completed_successfully` |
| [前端](./best-practices/frontend.md) | `antd` · `EChart`/`notMerge` · `toFixed` · `formatCap` · `unit` · `rowKey` · `CheckableTag` · `Segmented` · `Tooltip` |
| [测试与 E2E](./best-practices/testing.md) | `playwright` · `getByText` · `toContainText` · `toBeVisible` · `getByRole("radio")` · `strict` |
| [架构与分层](./best-practices/architecture.md) | `schema` · `repository` · `_dispatch_task` · `QUEUES` · `JWT` · `mTLS` · `whitelist` |
| [指标建模与规则引擎](./best-practices/metrics-rule-engine.md) | `metric_key` · `freq` · `period` · `rollup` · `report_version` · `calc_method` · `match` · `source` |
| [工程流程与文档](./best-practices/process-docs.md) | `Alembic`/`revision` · `Changelog` · `AGENTS` · `README` · `best-practices` · `docker-compose` · 端口号 |

## 写入门槛（新条目必须满足）

1. **单条 ≤ 3 行**（长案例分析压到"现象 → 根因 → 动作"三段式）
2. **必须回答"能否机械检测"**：
   - **能** → 升级成 lint / 测试 / `doc_gate.sh` 检查，**并从本目录删除该条**（规则进机器，不进文档）
   - **不能**（纯人工判断类，如架构取舍、沟通约定）→ 才留在本目录
3. **写"动作"而不是"结论"**：留下"下次遇到该怎么做"，而不是"这次是什么"
4. 归入已存在的 8 个分类之一；不要新增分类，除非现有分类都不合适

> 目的：这个目录**只应随被机器接管的条目逐渐变短**。它一旦只增不减，就会退化成"墓碑文件"（Harness engineering 里反复警告的形态）——文档描述的事实一旦能被代码/测试强制，文档就该退场。

## 维护约定

- 每季度或大特性合入时通读对应分类：删除过时项、合并重复项、把能机械检测的项升级为门禁
- **同因不同域复发**是本目录最贵的成本信号（例：`ts_code → stock_id` 映射表冻结的教训曾在 4 个采集域各重演一次）。复发时优先把它变成自动化检查，而不是再加一条说明
- 与 `docs/decisions/` 的分工：本目录是"反复踩过的坑"（可操作），`decisions/` 是"当时为什么这么选"（只增不改）
