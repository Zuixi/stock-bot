# Frontend Design

- UX设计参考[this document](../../docs/frontend-ux-spec.md)
- PRD设计参考[this document](../../docs/frontend-service-prd.md)
- 架构设计参考[this document](../../docs/frontend-architecture.md)

## 已实现模块结构（M1 MVP）

```
src/
  app/              # 应用壳：路由、布局、主题、Provider
  pages/            # 页面组装（市场首页、分类、详情、自选）
  features/
    market/         # 市场总览与分类模块（指数卡片、图表、筛选表格）
    stock-detail/   # 个股详情模块（K线、基本面卡片）
    watchlist/      # 自选模块（Zustand 持久化 store）
    search/         # 全局搜索组件
  shared/
    api/            # API client（预留后端接入）
    config/         # 缓存、分页等全局配置
    mocks/          # Mock 数据（市场指数、股票列表、行业板块）
    types/          # TypeScript 类型定义
    ui/             # 通用组件（StateWrapper、ChangeText、NumberText）
```

## 关键技术选型

- 状态管理：Zustand（客户端持久化）+ TanStack Query（服务端缓存）
- 图表：ECharts via echarts-for-react
- UI 框架：Ant Design 5
- 路由：React Router v7（lazy load 分包）

IMPORTANT:
- 需要记住的事情应该及时更新到这篇文档中。
- 当文档存在错误或功能更新时，需要及时更新文档。
