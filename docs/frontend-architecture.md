# Stock Bot 前端架构设计文档（Architecture）

## 1. 文档信息

- 文档版本：v1.0
- 日期：2026-04-07
- 对齐文档：`docs/frontend-service-prd.md`、`docs/frontend-ux-spec.md`
- 技术栈：React 18、Vite、TypeScript、ECharts、Tailwind CSS v4、shadcn/ui

## 2. 架构目标

1. 支撑 P0 页面与功能快速落地（市场、分类、个股详情、自选）。
2. 保证后续 P1/P2 扩展时不重构主干（资讯、价值网、对比分析）。
3. 建立可维护的数据层与 UI 层边界，避免页面直接耦合后端字段。
4. 保障前端性能、稳定性、可观测性与可测试性。

## 3. 总体架构视图

前端采用“分层 + 分域”架构：

1. **App Layer**
   - 路由、布局、全局 Provider、主题与错误边界
2. **Feature Layer**
   - 按业务域组织（market / stock-detail / watchlist / search）
3. **Entity Layer**
   - 领域模型与字段映射（Stock、MarketIndex、Fundamental）
4. **Shared Layer**
   - API Client、缓存、工具函数、通用 UI 组件、配置常量

核心原则：页面不直接消费“原始 API 响应”，统一通过 Entity Adapter 映射成前端领域模型。

## 4. 建议目录结构

```text
frontend/
  src/
    app/
      providers/
      router/
      layouts/
      styles/
    pages/
      market/
      market-category/
      stock-detail/
      watchlist/
      news/
    features/
      market/
        components/
        hooks/
        services/
      stock-detail/
      watchlist/
      search/
    entities/
      stock/
        model.ts
        adapter.ts
      market-index/
      fundamental/
    shared/
      api/
        client.ts
        endpoints.ts
        errors.ts
      config/
      hooks/
      lib/
      ui/
      types/
    tests/
      e2e/
      integration/
      unit/
```

## 5. 路由与页面模块设计

### 5.1 路由建议

- `/market`：市场首页（P0）
- `/market/category`：分类市场页（P0）
- `/stock/:symbol`：个股详情页（P0）
- `/watchlist`：自选页（P0）
- `/news`：资讯页（P1）
- `/tools`：工具页（P1）

### 5.2 路由规则

1. 分类页筛选条件通过 query 参数维护（可分享、可恢复）。
2. 详情页通过 `symbol + exchange` 保证定位唯一性。
3. 未匹配路由统一进入 404 页面，提供返回市场首页入口。

## 6. 数据层设计

### 6.1 API Client 规范

1. 统一使用 `shared/api/client.ts` 处理请求、超时、重试、错误标准化。
2. 所有请求携带 `x-request-id`（如后端支持）用于链路追踪。
3. 统一错误模型：
   - `code`
   - `message`
   - `details`
   - `requestId`（可选）

### 6.2 领域模型与适配器（Adapter）

1. `StockEntity`：基础标识 + 行情摘要字段
2. `FundamentalEntity`：估值、盈利、成长指标
3. `MarketIndexEntity`：指数名称、点位、涨跌、更新时间

适配器职责：

1. 后端字段转前端统一字段（例如命名与单位归一化）
2. 缺失值兜底（`null -> --` 在 view model 层处理）
3. 数据时间戳透传（`asof` 必带）

### 6.3 缓存策略（建议）

1. 市场总览：30-60s
2. 分类列表：60-300s
3. 个股快照：15-60s
4. 历史行情：长缓存 + 条件失效

建议使用支持 SWR/缓存失效能力的 Query 库（如 TanStack Query），避免手写请求状态机。

## 7. 状态管理策略

### 7.1 状态分类

1. **服务端状态**：列表、详情、图表数据（由 Query 库管理）。
2. **客户端状态**：筛选器开关、表格列配置、弹窗状态（本地状态管理）。
3. **持久化状态**：自选列表、最近搜索、主题偏好（LocalStorage + 可选服务端同步）。

### 7.2 原则

1. 能放 URL 的状态不放全局 store。
2. 能放 Query Cache 的不放 Redux/全局状态。
3. 全局 store 仅用于跨页面共享且与 URL 不强绑定的 UI 状态。

## 8. UI 组件架构

### 8.1 组件分层

1. `shared/ui`：按钮、输入框、卡片、表格壳、空态、错误态
2. `features/*/components`：业务组件（行业热力图、自选表格、详情指标卡）
3. 页面层只做组合，不承载复杂业务逻辑

### 8.2 图表组件约束

1. 每个图表封装独立组件，统一接受：
   - `data`
   - `loading`
   - `error`
   - `onRetry`
2. 图表配置（颜色、tooltip、grid）抽离为可复用配置函数。
3. 图表错误不可向外抛出导致整页崩溃，必须被边界捕获。

## 9. 前后端接口协同规范

### 9.1 接口分组（建议）

1. `/api/market/*`：指数、分布、热力图、资金流
2. `/api/stocks/*`：列表、详情、历史行情
3. `/api/fundamentals/*`：基本面指标
4. `/api/watchlist/*`：自选（登录态）

### 9.2 参数约束

1. 列表统一支持 `page/page_size/sort_by/sort_order/filters`
2. 图表统一支持 `range/start/end`
3. 响应必须包含 `asof`

### 9.3 兼容策略

1. 后端新增字段不影响前端解析（宽松读取）。
2. 后端删除/改名字段需通过 API 版本或灰度期兼容。
3. 前端对关键字段做运行时校验和降级兜底。

## 10. 性能优化策略

1. 路由级懒加载（页面分包）。
2. 图表组件按需加载，避免首屏一次性加载全部图表库。
3. 列表使用分页，必要时虚拟滚动。
4. 控制重渲染：
   - 稳定 key
   - 记忆化重计算
   - 避免大对象透传 props
5. 搜索输入去抖（200ms），防止高频请求。

## 11. 可观测性与质量保障

### 11.1 日志与埋点

1. 用户行为埋点：页面访问、筛选、排序、搜索、加自选、详情访问。
2. 性能埋点：首屏耗时、接口耗时、图表渲染耗时。
3. 错误上报：JS Error、Promise Rejection、接口错误摘要。

### 11.2 测试策略

1. 单元测试：工具函数、adapter、关键业务 hooks。
2. 组件测试：表格、图表容器、状态组件（加载/空/错）。
3. E2E 测试（P0 必备）：
   - 市场首页 -> 分类页 -> 详情页 -> 加自选
4. 合同测试（建议）：前后端接口 schema 对齐验证。

## 12. 安全与合规

1. 不在前端保存任何敏感密钥。
2. 对外链/富文本内容进行安全处理（防 XSS）。
3. 风险提示固定展示在详情和分析区域。
4. 遵循“仅数据展示，不构成投资建议”文案规范。

## 13. 构建与发布建议

1. 环境分层：`dev` / `staging` / `prod`
2. `.env` 仅存前端可公开配置（API Base URL、埋点开关）
3. CI 建议流程：
   - lint
   - type-check
   - test（unit + e2e smoke）
   - build
4. 发布策略：灰度发布 + 回滚版本保留

## 14. 里程碑对应的架构任务拆分

### M1（P0）

1. 搭建路由与页面骨架
2. 完成 API client + Query 基础设施
3. 完成市场、分类、详情、自选四大模块
4. 完成关键路径 E2E

### M2（P1）

1. 加入搜索与资讯模块
2. 增强图表交互与缓存策略
3. 增加更多埋点与监控看板

### M3（稳定性）

1. 性能专项优化
2. 错误治理与告警闭环
3. 接口契约治理（schema 版本化）

## 15. 风险与架构应对

1. **数据模型频繁变化**
   - 采用 Adapter + Runtime 校验 + 版本策略
2. **图表性能瓶颈**
   - 分包、懒加载、数据采样、必要时 Web Worker
3. **跨页面状态复杂化**
   - URL 优先 + Query 优先，限制全局 store 范围
4. **需求边界扩散**
   - 以 PRD 的 P0/P1/P2 为迭代门禁，避免架构提前过度设计

---

本架构文档作为前端开发实现基线。后续如接口协议、状态管理方案或目录结构调整，需同步更新该文档并在 PR 中注明变更原因。
