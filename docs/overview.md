# 项目概览

> 当前态文档。最早的意图记录见 [`./archive/product.md`](./archive/product.md)（已归档，描述的是 v0 CLI 阶段的目标）。

## 是什么

一个 **A 股市场数据分析工具**：采集上交所 / 深交所 / 北交所全量股票数据，按**申万行业分类**与**概念板块**组织，提供行情台、个股详情、市场情绪与行业投研看板。

**stock bot** 的差异化在第二层：不只是行情展示，而是把**某个具体行业的产业指标 + 规则引擎信号**做成可回测的投研工作台。首个实例是生猪养殖（"猪智投"）。

## 给谁用

| 角色 | 用它做什么 |
|---|---|
| 投资者 / 研究员 | 看行情台与市场情绪、下钻个股与板块、在行业工作台里看产业指标与信号 |
| 项目维护者（人 + agent） | 通过本仓库的可执行文档系统理解并演进系统：`docs/index.md` 是入口，`AGENTS.md` 是铁律 |

## 当前阶段

| 维度 | 状态 |
|---|---|
| 数据面 | TuShare Pro 单一主源 + 东财/同花顺/巨潮补充；对账式自愈数据面（缺口自动收敛，见 `plans/2026-09-17-data-sync-self-healing.md`） |
| 功能面 | 行情台、申万分类、概念板块、个股详情、涨停情绪、行业投研工作台均已落地（逐项代码入口见 [`features.md`](./features.md)） |
| 访问控制 | 多用户：Traefik gateway + auth-service + forward-auth，业务库与认证库分离（[ADR 0002](./decisions/0002-split-auth-service-and-forward-auth.md)） |
| 部署 | GitHub Actions 构建镜像 → ghcr.io → 服务器只拉取不构建；Caddy + Traefik 两跳边缘（[`deployment/`](./deployment/)） |
| 产品化 | 行业投研工作台是主方向；扩展第二个行业应"零新表、零新页面"（写配置 + 写采集器） |

## 明确的非目标

- **不做投资建议**：输出定位为"数据分析与解释"，信号与阶段判定必须附口径与降级标记，不做收益承诺
- **不做券商交易下单**：系统不接交易通道
- **不做历史成分回算**：分类/成分类快照数据只做"当日/向前"，避免前视与幸存者偏差（[ADR 0004](./decisions/0004-current-snapshot-not-backfilled.md)）
- **不引入 eslint/prettier**：前端静态校验以 `tsc` 为准（[ADR 0006](./decisions/0006-no-eslint-tsc-as-static-check.md)）
- **当前不追多源降级链**：以单一主源保证口径一致（[ADR 0001](./decisions/0001-single-primary-source-tushare.md)）

## 仓库构成

| 目录 | 内容 | 状态 |
|---|---|---|
| `backend/` | FastAPI + SQLAlchemy + PostgreSQL/Redis/RabbitMQ 数据服务 | 主项目 |
| `frontend/` | React 18 + TS + Ant Design 5 + ECharts | 主项目 |
| `auth-service/` · `forward-auth/` | 认证微服务与 Traefik forward-auth sidecar | 主项目 |
| `docs/` · `plans/` | 文档系统与执行计划（见 [`index.md`](./index.md)） | 主项目 |
| `src/` · `tests/`（根目录） | 早期 CLI 原型（交易所 crawler + 聚类），已被 `backend/` 取代 | **历史遗留**，勿在此扩展新功能 |

## 从哪里开始

| 我想 | 去 |
|---|---|
| 找文档 / 按任务找入口 | [`index.md`](./index.md) · [`authority.md`](./authority.md) |
| 第一次把环境跑起来 | [`deployment/first-run.md`](./deployment/first-run.md) |
| 看有哪些功能、代码在哪 | [`features.md`](./features.md) |
| 看架构与演进 | [`ARCHITECTURE.md`](./ARCHITECTURE.md) · [`evolution.md`](./evolution.md) |
| 部署 / 测试 | [`deployment/index.md`](./deployment/index.md) · [`testing/index.md`](./testing/index.md) |
| 知道坑在哪 | [`references/best-practices.md`](./references/best-practices.md) |
