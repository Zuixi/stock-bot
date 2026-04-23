# Project Changelog
项目所有重大更新必须记录在这里，补充在文档最后面，采用如下形式：
```markdown
## {{日期}} - {{更新模块}}
- 一句话总结更新的内容
- 涉及模块有哪些，不需要列出具体文件，只需要列出模块名

## ChangeLog List

## 2026-04-20 - 申万行业分类
- 基于本地 XLS/XLSX 文件实现申万三级行业分类与成分股联动，替代原 TuShare API 内存缓存方案
- 涉及模块：backend/models、backend/services、backend/api、frontend/features/market、frontend/pages/market-industry-*

## 2026-04-20 - 申万导入链路优化
- 新增“SQL 种子优先、XLS 解析兜底”的双路径导入机制：首次解析后自动导出 `sw_seed.sql`，后续部署可直接导入 SQL，显著降低启动导入耗时
- 涉及模块：backend/services、backend/config、docker-compose、backend/.dockerignore、backend/data

## 2026-04-21 - 分类市场分页修复
- 修复分类市场表格切换“条/页”后因固定 pageSize 被重渲染覆盖导致显示条数不变化的问题，并将分页状态改为受控持久化。
- 涉及模块：frontend/features/market、docs

## 2026-04-21 - 分类市场总数口径修复
- 修复分类市场“总条数固定 300”的问题：新增跨交易所分页接口并改为服务端分页，前端分页总数改用后端 `total`，确保 20/50/100 条切换与页码显示一致。
- 涉及模块：backend/api、frontend/shared/api、frontend/pages/market-category、frontend/features/market、docs

## 2026-04-21 - 申万分类缺失兜底修复
- 修复申万行业树总数与股票总数不一致问题：行业树计数统一按 `stocks` 口径统计，并新增 `OTHER(其他)` 一级分类承接未映射到有效三级行业的股票。
- 涉及模块：backend/services/market、frontend/pages/market-industry-*、docs

## 2026-04-21 - 前端代理 502 修复
- 修复 frontend 经 Nginx 代理后端接口偶发全量 502 的问题：启用 Docker DNS 动态解析，避免 backend 容器重建后 frontend 继续使用过期 upstream IP。
- 涉及模块：frontend/nginx、docker-compose、docs

## 2026-04-22 - SSE 交易所指数实时数据采集
- 整合 SSE 官方 JSONP 接口爬虫到 backend 服务：新建 `sse_index_snapshots` 表存储盘中快照，异步 httpx 爬取服务含完整反爬策略（UA 轮换/cookie 持久化/随机 jitter/指数退避），APScheduler 定时调度（交易时段 9:30-15:00 每 10min + 15:30 收盘补采），支持历史时间戳回填 4/1-4/21 数据，前端 MarketOverview 自动合并 SSE 实时数据优先展示
- 涉及模块：backend/models、backend/repositories、backend/services、backend/schemas、backend/api、backend/scheduler（新增）、docker-compose、frontend/shared/types、frontend/shared/api、frontend/features/market

## 2026-04-22 - 三年日K启动补齐
- 启动初始化由“固定近30交易日”升级为“按股票检查近3年覆盖并仅回补缺口”，采用小并发分批逐股拉取 TuShare 日线，兼顾补齐效率与服务启动稳定性。
- 涉及模块：backend/services、backend/repositories、backend/core/providers、backend/tests、docs

## 2026-04-22 - 股票自定义申万分类标签
- 新增 stock_custom_sw_tags 表支持每只股票自定义多个 SW 二级/三级行业标签；"其他"一级行业按股票自带 industry 字段自动分组为二级子分类；股票详情页新增分类标签展示与编辑功能。
- 涉及模块：backend/models、backend/services、backend/api、backend/migrations、frontend/shared/api、frontend/shared/types、frontend/features/stock-detail、frontend/pages/market-industry-level2、frontend/pages/stock-detail

## 2026-04-23 - 申万“其他”二级下钻交互统一
- 修复申万“其他”一级分类下二级卡片点击行为与其余分类不一致的问题，统一为路由下钻到独立详情页，避免在一级页内混合展示子分组个股。
- 涉及模块：frontend/pages/market-industry-level2、docs

## 2026-04-23 - 自定义申万三级联动修正
- 修正股票详情“编辑自定义申万分类”弹窗联动失效问题：三级下拉严格按已选二级实时过滤，未选二级时禁用三级下拉并提示“请先选择二级行业”。
- 涉及模块：frontend/features/stock-detail、docs

## 2026-04-23 - 自定义申万标签与行业详情联动修复
- 修复行业详情页未包含股票自定义申万二/三级标签的问题：行业树统计、层级个股列表与 OTHER 兜底口径统一合并官方成分与自定义标签。
- 涉及模块：backend/services/market、docs
