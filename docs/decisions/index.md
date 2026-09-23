# 决策记录索引

状态表。**新增 ADR 必须同时在此登记**（由 `scripts/doc_gate.sh` 校验）。

| 编号 | 标题 | 状态 | 日期 | 主题 |
|---|---|---|---|---|
| [0001](./0001-single-primary-source-tushare.md) | 数据源收敛为单一主源 TuShare Pro | accepted | 2026-09-03 | 数据采集 |
| [0002](./0002-split-auth-service-and-forward-auth.md) | 拆出 auth-service + forward-auth，认证与业务库分离 | accepted | 2026-09-09 | 架构 / 安全 |
| [0003](./0003-concept-membership-symbol-as-key.md) | 概念成分以 symbol 为业务键，stock_id 降级为可空解析列 | accepted | 2026-09-18 | 数据建模 |
| [0004](./0004-current-snapshot-not-backfilled.md) | 快照型分类数据只做"当日/向前"，不回算历史 | accepted | 2026-09-18 | 数据口径 |
| [0005](./0005-overlay-seed-files.md) | 人工策展数据用 overlay 种子文件，不改自动生成的种子 | accepted | 2026-09-18 | 数据工程 |
| [0006](./0006-no-eslint-tsc-as-static-check.md) | 前端不引入 eslint/prettier，静态校验以 tsc 为准 | accepted | 2026-09-23 | 前端 / 门禁 |
| [0007](./0007-tiered-bench-gate-and-e2e-excluded.md) | 性能门禁分级：Tier 1 硬门禁 + e2e/bench 与常规单测隔离 | accepted | 2026-09-09 | 门禁 / 性能 |

状态取值：`accepted`（现行）· `superseded`（被新决策取代，须填 `superseded-by` 并双向引用）· `rejected`（评审未采纳，保留以阻止重复提议）

## 与其它文档的分工

| 想知道 | 去哪 |
|---|---|
| 现在是什么样 | `docs/architecture/**`、`docs/overview.md`、`docs/features.md` |
| 当时为什么这么选 | 本目录 |
| 怎么一步步变成这样 | `docs/evolution.md` |
| 某次改动的具体内容 | `docs/Changelog.md` |
| 某项任务的执行过程 | `plans/**` |
| 反复踩过的坑 | `docs/references/best-practices.md` |
