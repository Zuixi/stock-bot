# Frontend
这是stock bot服务的web前端，负责从backend 获取数据，并且进行详细展示。

技术栈：React 18 + TypeScript + Vite + Ant Design 5 + ECharts（echarts-for-react）+ TanStack React Query + Zustand。
结构：`src/app`（路由/布局/主题）、`src/pages/<路由>/`、`src/features/<域>/`、`src/shared/`（api/ui/config）。

IMPORTANT:
- 每次完成任务时，结合业内最佳实践，总结经验教训，用一句话沉淀到 [this document](../../docs/references/best-practices.md)