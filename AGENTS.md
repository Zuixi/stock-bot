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
- 前端（frontend/ 下，npm 管理）：`npm ci`（首次）、`npx tsc --noEmit`（静态校验唯一口径，**仓库无 eslint，`npm run lint` 是失效脚本**，见 [ADR 0006](./docs/decisions/0006-no-eslint-tsc-as-static-check.md)）、`npm run build`
- 整体构建启动：`docker compose build && docker compose up -d`

前后端都需要使用Docker Compose 进行部署。
整体服务通过 docker compose build 和 docker compose up -d 进行构建和启动。
项目组件具体信息可以参考组件的AGENTS.md文件。

## 服务管理约定（强制的 agent 行为规则）
Agent 反复踩坑：起新服务不关旧服务，端口一路漂移堆积（实测同一 worktree 的 vite 在 3000/3001 各挂一个、TaskStop 只杀父进程留下 vite 孤儿）。因此：

1. **重启必先清旧**：启动任何长驻服务（`vite dev`/`uvicorn`/`docker compose up` 等）前，先查目标端口并清掉旧实例：
   - 查占用：`netstat -ano | grep :<port> | grep LISTEN`
   - 杀前确认进程身份（避免误杀）：`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"ProcessId=<pid>\" | Select-Object CommandLine"`
   - 杀进程树（`//T` 必加，否则只杀父进程留孤儿）：`taskkill //F //T //PID <pid>`
2. **优先复用**：目标端口已有健康且属于本项目的服务时，直接复用，不再新起。
3. **收尾不留孤儿**：会话结束前关掉自己启动的服务；确需保留给用户查看的，必须在收尾输出写明端口、PID 与关闭命令。

## 关键文档

**先读 [docs/index.md](./docs/index.md)** —— 它是"我要做 X → 读哪些文件"的路由表（本文件只保留铁律与命令）。

- [docs/index.md](./docs/index.md) — 文档入口 / 按任务找文档 · [docs/authority.md](./docs/authority.md) — 单点权威矩阵
- [docs/overview.md](./docs/overview.md) · [docs/features.md](./docs/features.md) — 项目是什么 · 有哪些功能（含代码入口，被门禁校验）
- [docs/deployment/](./docs/deployment/) — **部署权威文档**（Docker 指令、端口表、迁移、运维排障均以它为准；`docs/build.md` 已拆入本目录，原件仅作历史转发）
- [docs/testing/](./docs/testing/) — 测试与门禁矩阵（什么检查 / 何时跑 / 失败怎么办；含 e2e 术语消歧）
- [docs/decisions/](./docs/decisions/) — 决策记录（**只增不改**，机械校验）；[docs/evolution.md](./docs/evolution.md) — 架构演进史
- [plans/](./plans/) — 实施计划（本目录 [plans/README.md](./plans/README.md) 有文件头契约；**新计划必须登记 [plans/index.md](./plans/index.md)**）
- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) · [docs/design/](./docs/design/) — 当前架构 · 设计原型与数据源调研
- [docs/Changelog.md](./docs/Changelog.md) / [docs/references/best-practices.md](./docs/references/best-practices.md) — 见下方约定

## 部署约定
- Docker 部署用根目录 `docker-compose.yml` 统一编排；环境变量分**两个**文件（见 [docs/deployment/index.md](./docs/deployment/index.md)「环境变量配置」）：根目录 `.env`（从 `.env.docker.example` 复制，供 compose `${VAR}` 插值：`APP_ENV`/JWT 密钥/`INTERNAL_API_TOKEN`/Cookie 与 XFF 开关/RabbitMQ 口令）与 `backend/.env`（从 `backend/.env.example` 复制，填真实 `TUSHARE_TOKEN`，供 api/worker/scheduler 的 `env_file`）
- 前端 Dockerfile 的 `runtime` 阶段只打包 `dist/`（`target: runtime`），网络受限时需先本地 `cd frontend && npm ci && npm run build`
- 修改 compose 的服务名、端口、镜像名、表名或 `metric_key` 时，须同步核对 `docs/deployment/index.md`（端口权威表）、`docs/ARCHITECTURE.md`、`README.md` 与相关组件 `AGENTS.md`，保持交叉引用一致——`bash scripts/doc_gate.sh` 会机械校验端口表/服务名/队列名/`metric_key` 的漂移

## 完成前自检门禁（每次收尾强制）
每次改动文件后、结束回合前，**必须**按序执行以下自检，不得跳过；CI 与 pre-commit 只是兜底，不靠它们才发现问题：

1. **列出改动**：`git status --porcelain` + `git diff --stat`，明确本次改动范围
2. **对照已知错误**：跑 `bash scripts/slop_scan.sh`（或按 [best-practices.md](./docs/references/best-practices.md) 探测器映射表对 `git diff` grep），命中即复核对应分类，确认未重复已沉淀的错误
3. **跑客观门禁**：`bash scripts/self_review.sh`（默认快检：空白/冲突标记 + 改动文件 ruff + 文档同步告警 + 文档系统门禁 `doc_gate.sh`：ADR 只增不改 / features 契约 / 门禁矩阵 / 端口表 / 口径一致）；后端或前端改动较多且环境就绪时加 `--full`（追加 mypy / pytest / tsc，pytest 自动排除 e2e 与 bench）。改了基准对象（规则引擎/rollup/归一化 mapper 等 Tier 1 纯计算热点）或基线契约时，另跑 `bash scripts/bench.sh` 确认性能门禁绿
4. **查文档同步**：涉及 `.md` 改动时确认 `docs/Changelog.md` 已补记，交叉引用（端口/服务名/表名/`metric_key`）一致
5. **反馈闭环（修环境，不只是修这次任务）**：agent 卡住时，缺的往往是工具/文档/门禁而不是代码——发现的重复或新教训按分类追加进 [best-practices.md](./docs/references/best-practices.md) 对应章节，**且必须回答"能否机械检测"：能则升级为 lint/测试/`doc_gate.sh` 检查并从文档删除该条**（详见该文件「写入门槛」）
6. **固定格式收尾**：结束语输出 `自检：改动 N 处 / 探测器命中 M / 门禁 ✓|✘ / Changelog ✓|✘`

准则：门禁"✘"或探测器命中的问题未修正前，不得结束回合。

IMPORTANT:
- 每次完成任务时，**仅当无法机械化**（lint/doc_gate/测试覆盖不了）时，才用一句话沉淀到 [best-practices](./docs/references/best-practices.md)；能机械化的须升级门禁并从文档删除该条（见 docs/index.md「变更写哪里」）
- 每次添加特性或修改代码后，都需要一句话总结Change Log，更新到 [this document](./docs/Changelog.md)
- 修改文档时保持各文档间的交叉引用一致（metric_key、路由、表名等命名对齐）
