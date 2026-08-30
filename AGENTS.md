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

前后端都需要使用Docker Compose 进行部署。
整体服务通过 docker compose build 和 docker compose up -d 进行构建和启动。
项目组件具体信息可以参考组件的AGENTS.md文件。

## 关键文档
- [plans/](./plans/) — 功能实施计划（tracer-bullet 分阶段）
- [docs/design/](./docs/design/) — 设计文档、原型与数据源调研（data-source.md）
- [docs/Changelog.md](./docs/Changelog.md) / [docs/references/best-practices.md](./docs/references/best-practices.md) — 见下方约定

IMPORTANT:
- 每次完成任务时，结合业内最佳实践，总结经验教训，用一句话沉淀到 [this document](./docs/references/best-practices.md)
- 每次添加特性或修改代码后，都需要一句话总结Change Log，更新到 [this document](./docs/Changelog.md)
- 修改文档时保持各文档间的交叉引用一致（metric_key、路由、表名等命名对齐）
