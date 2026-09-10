# stock bot

stock bot 是一个记录跟踪股票数据的分析工具，支持三大交易所的股票数据获取，并且按照申万分类进行统计显示。
stock bot 能够查看当前市场行情，股票类别，每个分类的具体股票信息和个股详情展示。
正在产品化的方向：**行业投研工作台**（首个实例：生猪养殖"猪智投"），实施计划见 [plans/industry-research-workbench.md](./plans/industry-research-workbench.md)。

## 项目结构
- backend 数据服务后端，提供前端数据
  - FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL + Redis + RabbitMQ
  - 分层：`app/api/v1` 路由 → `app/services` → `app/repositories`；`app/models` + Alembic 迁移（`app/migrations`）
  - 外部数据采集双轨制：APScheduler 定时任务（`app/scheduler`）+ RabbitMQ Worker 手动触发（`app/workers`），共用同一 service ingest 方法；新增队列必须在 `app/core/mq.py` 的 `QUEUES` 注册
- frontend 服务前端，数据可视化
  - React 18 + TypeScript + Vite + **Ant Design 5** + ECharts（echarts-for-react）
  - TanStack React Query 数据获取 + Zustand 本地持久化
  - feature-sliced 结构：`src/app`（路由/布局/主题）、`src/pages/<路由>/`、`src/features/<域>/`、`src/shared/`（api/ui/config）
- 根目录 `src/` + `tests/` + 根 `pyproject.toml` 为早期 CLI 原型遗留，主项目在 `backend/` 与 `frontend/`

## 常用命令
- 后端（backend/ 下，uv 管理）：测试 `uv run pytest`（默认 addopts 已排除 e2e 与 bench）；lint/类型检查 `uv run --extra dev ruff check .`、`uv run --extra dev mypy app`
- 性能基准（Tier 1 硬门禁，[plans/2026-09-09-benchmark-tiers.md](./plans/2026-09-09-benchmark-tiers.md)）：`bash scripts/bench.sh`（对比基线门禁，CI bench-cpu 同款）/ `--save-baseline`（基线契约变更后刷新 benchmarks/baseline.json 并随 PR 入库）/ `--quick`（本地冒烟）
- 前端（frontend/ 下，npm 管理）：`npm install`（首次）、`npm run lint`、`npm run build`
- 整体构建启动：`docker compose build && docker compose up -d`

前后端都需要使用Docker Compose 进行部署。
整体服务通过 docker compose build 和 docker compose up -d 进行构建和启动。
项目组件具体信息可以参考组件的AGENTS.md文件。

## 关键文档
- [plans/](./plans/) — 功能实施计划（tracer-bullet 分阶段），重点 [industry-research-workbench.md](./plans/industry-research-workbench.md)（投研工作台 / 猪智投）
- [docs/design/](./docs/design/) — 设计文档、原型与数据源调研（data-source.md）
- [docs/build.md](./docs/build.md) — 服务构建与部署指南（**部署权威文档**，Docker 指令、端口、迁移、运维命令均以它为准）
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) — 整体与容器化部署架构
- [docs/Changelog.md](./docs/Changelog.md) / [docs/references/best-practices.md](./docs/references/best-practices.md) — 见下方约定

## 部署约定
- Docker 部署用根目录 `docker-compose.yml` 统一编排；环境变量从 `.env.docker.example` 复制为 `backend/.env` 后填写真实 `TUSHARE_TOKEN`，模板默认值已适配容器内部网络（服务名 `postgres`/`redis`/`rabbitmq`）
- 前端 Dockerfile 的 `runtime` 阶段只打包 `dist/`（`target: runtime`），网络受限时需先本地 `cd frontend && npm ci && npm run build`
- 修改 compose 的服务名、端口、镜像名、表名或 `metric_key` 时，须同步核对 `docs/build.md`、`docs/ARCHITECTURE.md`、README 与相关组件 `AGENTS.md`，保持交叉引用一致

## 完成前自检门禁（每次收尾强制）
每次改动文件后、结束回合前，**必须**按序执行以下自检，不得跳过；CI 与 pre-commit 只是兜底，不靠它们才发现问题：

1. **列出改动**：`git status --porcelain` + `git diff --stat`，明确本次改动范围
2. **对照已知错误**：用 [best-practices.md](./docs/references/best-practices.md) 顶部的「自检探测器映射表」对 `git diff` 做 grep，命中关键词即复核对应分类条目，确认未重复已沉淀的错误
3. **跑客观门禁**：`bash scripts/self_review.sh`（默认快检：空白/冲突标记 + 改动文件 ruff + 文档同步告警）；后端或前端改动较多且环境就绪时加 `--full`（追加 mypy / pytest / tsc，pytest 自动排除 e2e 与 bench）。改了基准对象（规则引擎/rollup/归一化 mapper 等 Tier 1 纯计算热点）或基线契约时，另跑 `bash scripts/bench.sh` 确认性能门禁绿
4. **查文档同步**：涉及 `.md` 改动时确认 `docs/Changelog.md` 已补记，交叉引用（端口/服务名/表名/`metric_key`）一致
5. **反馈闭环**：自检中发现的重复或新教训，按分类追加进 best-practices.md 对应章节
6. **固定格式收尾**：结束语输出 `自检：改动 N 处 / 探测器命中 M / 门禁 ✓|✘ / Changelog ✓|✘`

准则：门禁"✘"或探测器命中的问题未修正前，不得结束回合。

IMPORTANT:
- 每次完成任务时，结合业内最佳实践，总结经验教训，用一句话沉淀到 [this document](./docs/references/best-practices.md)
- 每次添加特性或修改代码后，都需要一句话总结Change Log，更新到 [this document](./docs/Changelog.md)
- 修改文档时保持各文档间的交叉引用一致（metric_key、路由、表名等命名对齐）
