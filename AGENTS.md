# stock bot

stock bot 是一个记录跟踪股票数据的分析工具，支持三大交易所的股票数据获取，并且按照申万分类进行统计显示。
stock bot 能够查看当前市场行情，股票类别，每个分类的具体股票信息和个股详情展示。

## 项目结构
- backend 数据服务后端，提供前端数据
  - FastAPI + Postgre SQL + Redits + RabbitMQ
- frontend 服务前端，数据可视化
  - React + Tailwind CSS v4 + shadcn/ui + Vite
  - Echarts for charting

前后端都需要使用Docker Compose 进行部署。
整体服务通过 docker compose build 和 docker compose up -d 进行构建和启动。
项目组件具体信息可以参考组件的AGENTS.md文件。

IMPORTANT:
- 每次完成任务时，结合业内最佳实践，总结经验教训，用一句话沉淀到 [this document](./docs/references/best-practices.md)
- 每次添加特性或修改代码后，都需要一句话总结Change Log，更新到 [this document](./docs/Changelog.md)