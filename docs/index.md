# 文档索引（按任务找文档）

本文件是文档系统的**入口**。`AGENTS.md` 是给 agent 的铁律与命令；本文件是"我要做 X → 先读什么"的路由表。

> 命名约定：`docs/**` 内部相对路径；`AGENTS.md` 指根目录。
> **单点权威：** [`authority.md`](./authority.md)

## 我想知道…

| 我想知道 | 读 |
|---|---|
| 这个项目是干什么的、给谁用、不做什么 | [`overview.md`](./overview.md) |
| 现在有哪些功能，代码在哪 | [`features.md`](./features.md) |
| 当前系统架构（服务、库表、鉴权链路） | [`ARCHITECTURE.md`](./ARCHITECTURE.md) → [`architecture/`](./architecture/) |
| 架构是怎么一步步演变成现在这样的 | [`evolution.md`](./evolution.md) |
| 当初为什么不用 X | [`decisions/index.md`](./decisions/index.md) |
| 某次具体改了什么 | [`Changelog.md`](./Changelog.md) |
| 某项任务是怎么执行的 | [`../plans/index.md`](../plans/index.md) |
| 反复踩过的坑 | [`references/best-practices.md`](./references/best-practices.md) |
| 怎么部署、怎么排障 | [`deployment/index.md`](./deployment/index.md) |
| 第一次把环境跑起来 | [`deployment/first-run.md`](./deployment/first-run.md) |
| 有哪些测试、门禁怎么跑、失败了怎么办 | [`testing/index.md`](./testing/index.md) |
| 外部数据源字段与口径 | [`references/tushare/`](./references/tushare/) · [`references/sw/`](./references/sw/) · [`references/cninfo/`](./references/cninfo/) |

## 我要做…

| 我要做 | 先读（≤3 篇） | 关键约束 |
|---|---|---|
| 接入一个新数据源 | `references/` 对应源 · `best-practices/data-source.md` | 先实机验证再写适配器；链路 client → ingest → model → repo → service → worker → API |
| 新增 RabbitMQ 队列 / 定时任务 | `architecture/backend/ARCHITECTURE.md` | 队列必须在 `app/core/mq.py` 的 `QUEUES` 注册（doc_gate #8） |
| 加/改数据库表 | `architecture/database-architecture.md` · `deployment/local-dev.md` | Alembic；并列分支可能 merge revision |
| 改前端页面或图表 | `architecture/frontend/ARCHITECTURE.md` · `frontend-ux-spec.md` · `best-practices/frontend.md` | 栈以 ADR 0006 + 当前态架构为准；勿读 `frontend-architecture.md` 转发文 |
| 改指标 / 规则引擎 | `best-practices/metrics-rule-engine.md` | `metric_key` 同步；热点改后 `bash scripts/bench.sh` |
| 改端口 / 服务名 / 镜像名 | `deployment/index.md` · `deployment/production.md` | 同步 `ARCHITECTURE.md` · `README.md`；`doc_gate` #5 |
| 改鉴权 / 受保护接口 | `architecture/authentication-and-gateway.md` · [ADR 0002](./decisions/0002-split-auth-service-and-forward-auth.md) | 不信未签名 Header；非幂等需 CSRF |
| 排查数据缺失 | `deployment/operations.md` · `best-practices/data-source.md` | 逐日 count + 关键列非空率，勿只看 `max(trade_date)` |
| 写测试 / 加门禁 | `testing/index.md` | 改 CI job 必须同步矩阵（doc_gate #4） |
| 上线 / 数据迁移 | `deployment/production.md` · `deployment/data-migration.md` | 先数据后应用；勿复制 override 到服务器 |
| 加文档 / 新计划 | 本文件 · `plans/README.md` | 新计划登记 `plans/index.md`；决策写新 ADR |

## 文档系统自身的规则

五层结构见 [`plans/2026-09-23-documentation-system.md`](../plans/2026-09-23-documentation-system.md) · 二期 [`plans/2026-09-24-docs-system-phase2.md`](../plans/2026-09-24-docs-system-phase2.md)：

| 层 | 位置 | 维护规则 |
|---|---|---|
| 入口 | `AGENTS.md` · 本文件 · `plans/index.md` · `authority.md` | 必须最新，被机械校验，不写细节 |
| 说明 | `overview.md` · `features.md` · `ARCHITECTURE.md` · `architecture/` | 只写**当前态**；禁止写历史与观点 |
| 历史 | `evolution.md` · `decisions/` · `plans/` · `Changelog.md` | **只增不改**（ADR 正文落地后禁编辑） |
| 操作 | `deployment/` · `testing/` | 怎么做与排障；**不复制命令，指向脚本** |
| 参考 | `references/` · `design/` | 外部资料；不参与交叉引用校验 |

### 变更写哪里

| 你改了什么 | 写哪里 | 不要写哪里 |
|---|---|---|
| 合并的功能/修复（一句话） | `Changelog.md` | — |
| 架构取舍、否掉方案 X | **新 ADR** + `decisions/index.md` | 不改旧 ADR 正文 |
| 里程碑级阶段变化 | `evolution.md` 一段 | `architecture/` 正文 |
| 端口/路由/队列/功能清单 | `deployment/index` · `features.md` · 架构文档 | 多处重复描述 |
| 可被 lint/doc_gate/测试强制的行为 | **升级门禁并删** best-practices 条 | 不要只追加长文 |
| 纯人工判断的坑 | `best-practices/<分类>.md`（≤3 行） | — |

文档侧门禁：`bash scripts/doc_gate.sh`（pre-commit / CI / `self_review.sh` [3/4]）。
