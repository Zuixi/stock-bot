# stock bot

## Overview

stock bot 是一个 A 股行情分析工具。后端通过 TuShare Pro API 采集股票数据存入 PostgreSQL，提供 FastAPI RESTful 接口；前端使用 React + ECharts 展示大盘行情、个股 K 线、申万行业分类等信息。

## Features

- **数据采集**：TuShare Pro API 作为主要数据源（股票列表、行情、涨跌停等），备用数据源包括 AKShare、yfinance、交易所爬虫
- **数据存储**：PostgreSQL 持久化存储，Redis 缓存加速查询，RabbitMQ 异步任务队列
- **大盘行情**：上证指数、深证成指、创业板指、北证50、沪深300 等主要指数实时行情
- **个股 K 线**：日线行情、复权因子、资金流向
- **申万行业分类**：申万行业一级/二级/三级分类，个股行业归属
- **定时任务**：自动定时从 TuShare 拉取最新数据

## Tech Stack

### Backend
- **FastAPI** — RESTful API
- **SQLModel** — 数据库 ORM（PostgreSQL）
- **Redis** — 热点数据缓存（300s TTL）
- **RabbitMQ** — 异步任务队列
- **TuShare Pro API** — 主数据源（`stock_basic`、`daily`、`trade_cal` 等）
- **AKShare / yfinance** — 备用数据源

### Frontend
- **React 18 + Vite** — 前端框架
- **Tailwind CSS v4 + shadcn/ui** — UI 组件库
- **ECharts** — 图表可视化
- **React Router** — 页面路由

## Project Structure

```
stock-bot/
├── backend/               # FastAPI 后端服务
│   ├── app/
│   │   ├── api/          # API 路由（v1）
│   │   ├── core/        # 核心模块（DB、Redis、MQ、TuShare 客户端）
│   │   ├── models/      # SQLModel 数据模型
│   │   ├── repositories/ # 数据访问层
│   │   ├── schemas/     # Pydantic 请求/响应模型
│   │   ├── services/    # 业务逻辑层
│   │   ├── workers/      # 异步任务 Worker
│   ├── crons/           # 定时任务脚本
│   └── tests/           # 后端测试
├── frontend/            # React 前端服务
│   └── src/
│       ├── pages/       # 页面组件
│       ├── features/    # 功能模块
│       └── app/         # 应用布局
├── docs/                # 项目文档
│   ├── architecture/    # 架构设计
│   └── references/      # 接口文档、数据源说明
└── docker-compose.yml   # 容器编排
```

## Setup

### 环境要求

- Python 3.11+
- Node.js 20+
- Docker & Docker Compose（推荐）

### 后端启动

```bash
cd backend
pip install -e .
# 配置 .env 或环境变量（TuShare Pro token 等）
python -m app.main
```

### 前端启动

```bash
cd frontend
npm install
npm run dev
```

### Docker Compose 部署（推荐）

```bash
docker compose build
docker compose up -d
```

## API Endpoints

主要接口：

| 模块 | 说明 |
|------|------|
| `/api/v1/stocks` | 股票列表、搜索、分类 |
| `/api/v1/quotes` | 个股 K 线、日线行情 |
| `/api/v1/market` | 大盘指数、板块行情、资金流 |
| `/api/v1/sw-industry` | 申万行业分类 |
| `/health` | 健康检查 |

完整接口文档：`http://localhost:8000/docs`（后端启动后）

## Roadmap

See product requirements and milestones in `product.md`.
