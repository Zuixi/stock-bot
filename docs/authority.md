# 文档权威矩阵（单点真相）

同一事实**只允许一处权威副本**；其余文档只做链接。Agent 与新人应优先读本表，再按 [`index.md`](./index.md) 任务表下钻。

| 主题 | 权威文件 | 勿用（已转发/历史） |
|---|---|---|
| 项目是什么 / 非目标 | [`overview.md`](./overview.md) | 根目录 `product.md`（[`archive/product.md`](./archive/product.md)） |
| 功能与代码入口 | [`features.md`](./features.md) | — |
| 容器与服务拓扑 | [`ARCHITECTURE.md`](./ARCHITECTURE.md) | — |
| 后端分层 / 队列 / API | [`architecture/backend/ARCHITECTURE.md`](./architecture/backend/ARCHITECTURE.md) | — |
| 前端当前态（栈与目录） | [`architecture/frontend/ARCHITECTURE.md`](./architecture/frontend/ARCHITECTURE.md) | [`frontend-architecture.md`](./frontend-architecture.md)（Tailwind/shadcn 时代，已转发；原文归档 [archive/frontend-architecture-tailwind-202604.md](./archive/frontend-architecture-tailwind-202604.md)） |
| 前端交互规格 | [`frontend-ux-spec.md`](./frontend-ux-spec.md) · [`frontend-service-prd.md`](./frontend-service-prd.md) | — |
| 部署 / 端口 / env | [`deployment/index.md`](./deployment/index.md) | [`build.md`](./build.md) |
| 首次搭环境（Tutorial） | [`deployment/first-run.md`](./deployment/first-run.md) | — |
| 测试与门禁矩阵 | [`testing/index.md`](./testing/index.md) | — |
| 架构取舍（为什么） | [`decisions/index.md`](./decisions/index.md) | 勿在 `architecture/` 写历史观点 |
| 计划状态（哪份仍有效） | [`../plans/index.md`](../plans/index.md) | `unverified` 计划勿当实施规格 |
| 踩坑（无法机械化时） | [`references/best-practices.md`](./references/best-practices.md) | 能进 lint/doc_gate 的规则不得长期留文档 |

维护：改 compose / 路由 / 队列 / 功能时同步上表对应权威文件；`scripts/doc_gate.sh` 校验其中多项。
