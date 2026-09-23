# Frontend
这是stock bot服务的web前端，负责从backend 获取数据，并且进行详细展示。

技术栈：React 18 + TypeScript + Vite + Ant Design 5 + ECharts（echarts-for-react）+ TanStack React Query + Zustand。
结构：`src/app`（路由/布局/主题）、`src/pages/<路由>/`、`src/features/<域>/`、`src/shared/`（api/ui/config）。

IMPORTANT:
- 完成任务后：能机械化的规则进 lint/doc_gate/测试；**仅**无法机械化的教训才追加 [best-practices](../docs/references/best-practices.md)（见 docs/index.md「变更写哪里」）