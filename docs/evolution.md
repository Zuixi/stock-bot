# 架构演进史

> 历史层文档。回答"怎么一步步变成今天这样"。**当前态**见 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 与 [`architecture/`](./architecture/)；**单次改动明细**见 [`Changelog.md`](./Changelog.md)；**决策理由**见 [`decisions/`](./decisions/)。
>
> 来源：`Changelog.md`（162 条）与 `plans/`（20 份）的一次性提炼。此后只在**里程碑级**变化时追加段落，日常改动由 `Changelog` 与 ADR 承担。
>
> 每段固定四问：**触发（为什么不得不变） / 变化前后 / 留下的残骸 / 相关 ADR**。

---

## v0 · CLI 原型（交易所爬虫 + 聚类）

**触发**：最初目标只是"把三大交易所的股票列表抓下来并做行为聚类"，不存在 Web 服务。

**变化前后**：无 → 根目录 `src/`（typer CLI + `fetchers/{sse,sze,bse}` JSONP 客户端 + normalizer + JSONL snapshot 存储）+ 根 `tests/`。

**残骸**：根 `src/`、根 `tests/`、根 `pyproject.toml` 仍在仓库中，但**已不是主项目**（主项目在 `backend/`、`frontend/`）。`product.md` 描述的是这一阶段的意图。新增功能不要往这里加。

**相关 ADR**：无（此阶段未做记录）。

## v1 · 后端服务化 + 数据源收敛

**触发**：CLI 形态无法支撑"持续跟踪 + 多页展示"，且多源降级链带来口径不一致与维护负担。

**变化前后**：
- 建立 `backend/`（FastAPI + SQLAlchemy async + PostgreSQL + Redis + RabbitMQ + Alembic）
- 数据源从"交易所 crawler → AKShare → yfinance 降级链"收敛为 **TuShare Pro 单一主源**
- 采集双轨制成型：APScheduler 定时增量 + RabbitMQ Worker 手动触发，共用同一 ingest 方法
- 申万分类改由本地 XLS/XLSX 解析入库（离线可用、避免限流）

**残骸**：AKShare 兜底路径仍在代码里但实测容器内不可用；根 `src/` 的 crawler 保留为历史参考。

**相关 ADR**：[0001 单一主源 TuShare](./decisions/0001-single-primary-source-tushare.md)

## v2 · 前端产品化

**触发**：数据面可用后，展示与交互成为瓶颈（K 线不可用、单位口径混乱、无明暗主题）。

**变化前后**：
- 前端成型：React 18 + TS + Vite + Ant Design 5 + ECharts + TanStack Query + Zustand，feature-sliced 目录
- K 线组件升级、**单位口径统一**（万元/元、万股/股等跨源差异收敛到展示层与 mapper）
- TradingView 风格明暗双主题基础设施 + Landing 宣传页 + 设计令牌门禁（`check:design`）
- 首页公开化：免登录行情台 IA（指数/榜单/板块/资金/日历/资讯）

**残骸**：`package.json` 的 `lint` 脚本（`eslint .`）在"不引 eslint"的决策后失效，至今是死脚本。

**相关 ADR**：[0006 不引 eslint，静态校验用 tsc](./decisions/0006-no-eslint-tsc-as-static-check.md)

## v3 · 行业投研工作台（猪智投）

**触发**：项目产品化方向确定——不只是行情展示，而是把某个行业的产业指标 + 规则引擎信号做成可回测的投研工作台。

**变化前后**：
- 引入行业无关的资产层：`industry_metrics` **单表**（行业级 `stock_id` 为 NULL、公司级带 `stock_id`）、指标注册表（`metric_key → {单位,频率,源优先级,层级,参考区间}`，代码即配置，经 API 下发给前端渲染）、政策锚点表（参考区间随生效日切换）、信号表
- 规则引擎：周期阶段判定 + 左侧信号；rollup（日度→月度派生）；多频指标"最新值"仲裁
- 前端 `/research` + `/research/:industryKey` 四 Tab 工作台；组件 props 驱动、内容后端驱动
- 采集基建 DRY 抽取（限流/重试/`asyncio.to_thread` 包装为通用基类）

**残骸**：`INDUSTRY_DATA_SOURCE=mock` 的演示源（必须永远垫底，真实源落库后需连派生行一起清）；AKShare 有意未进 `pyproject.toml`。

**相关 ADR**：暂无（其持久决策以"Architectural decisions"形式记在 `plans/industry-research-workbench.md`）。

## v4 · 认证与网关拆分（多用户）

**触发**：引入多用户与工作台协作后需要身份认证、权限与数据隔离，且不能把凭据体系耦合进业务服务。

**变化前后**：
- 从"单体无状态公开服务"→ **gateway（Traefik）+ auth-service + forward-auth** 三层
- 认证库独立（独立数据库 + 独立 Alembic 版本线 + 独立 `migrate-auth` 容器）
- 零信任内网：网关签发短时 Principal Assertion JWT（RS256, 60s），业务服务仅用 JWKS 验签；凭据只存 HttpOnly Cookie（BFF 同源）；非幂等请求 CSRF 双防御
- 自选股/标签服务端化，按 `user_id` 隔离

**残骸**：`conftest.py` 的 `client` fixture 仍指向 `host.docker.internal:8000` 的真实 API（旧形态遗留）；直连 `:8000` 的开发习惯会表现为"未登录"。

**相关 ADR**：[0002 拆出 auth-service + forward-auth](./decisions/0002-split-auth-service-and-forward-auth.md)

## v5 · 市场数据可信化

**触发**：功能铺开后暴露的不是功能缺失，而是**数据口径不可信**——涨停数算错、行情有中间空洞、"数据截至"标注滞后 4 个月、定时任务静默停摆。

**变化前后**：
- 日级完整性判据升级为三元组（行数 + 关键列非空率 + 依赖表存在）；脏源治理；"最新交易日"收敛为单一判据来源
- **权威原值落表**：限价用官方接口原值而非名称启发式（实测名称启发式把 75 只涨停误判成 83 只）；`pct_chg` 使用当日行 `pre_close`
- 连板梯队与市场情绪：本地 K 线自算主干 + 限价权威表 + 降级契约；盘中与收盘双口径并存不互相污染
- 对账式自愈数据面：宿主睡眠/容器重启/任务异常后自动收敛，`reconciliation_service` + 数据新鲜度端点
- 概念板块与次新股：三张表 + 东财成分采集（504 板 / 71928 成分）；成分以 `symbol` 为业务键；差分表积累真历史
- 名录冻结根治（`stocks` 5579 + 66 只次新股补齐）

**残骸**：`data/` 下 JSONL 原始响应（对账兜底依据，勿删）；部分概念/次新前端入口刻意延后（T17）。

**相关 ADR**：[0003 symbol 为业务键](./decisions/0003-concept-membership-symbol-as-key.md) · [0004 快照不回算历史](./decisions/0004-current-snapshot-not-backfilled.md) · [0005 overlay 种子](./decisions/0005-overlay-seed-files.md)

## v6 · 生产上线（发布链路 + 边缘层）

**触发**：需要真正对外提供 HTTPS 服务，且服务器不应承担构建。

**变化前后**：
- CI/CD 拆分：`ci.yml` 管质量门禁，`cd.yml` 由 `v*` tag 触发构建 4 个镜像推 ghcr.io；服务器只 `pull`（`IMAGE_TAG` 必填 + `--no-build`）
- 边缘层**两跳**：Caddy（TLS 自动签发/续期 + HSTS）→ Traefik（路由/鉴权/限流/安全头）；Caddyfile 改目录挂载
- 镜像瘦身（backend −162MB）与基础镜像按 digest 固定（治"每次发版重下 300MB"）
- 对外暴露面收敛：只留 gateway（后为 caddy）对外，postgres/redis 只绑回环
- 首次生产部署（腾讯云）+ 本地数据全量迁移（501MB / 对账 6 张表）

**残骸**：未入库的 `docker-compose.override.yml`（本机卷名修正，**禁止**复制到服务器）；基建镜像仍走华为 SWR（非多架构，故"整栈 arm64"不成立）。

**相关 ADR**：暂无（部署决策记录在 [`deployment/`](./deployment/)）。

## v7 · 工程门禁与文档系统

**触发**：改动速度上来后，**复发型错误**成为主要成本——同一类缺陷在不同采集域重演（映射表冻结、缓存键缺 `as_of`、naive 时间判断、`skipped` 静默丢数）。靠人记忆无法收敛。

**变化前后**：
- 完成前自检门禁（`scripts/self_review.sh` + pre-commit + CI 兜底），把"完成"的定义变成可执行命令
- Benchmark 分级：Tier 1 纯计算域硬门禁（同 runner A/B 相对基线），`e2e`/`bench` 用 marker 与常规单测隔离
- `best-practices.md` 建立"分类 + 探测器映射表"的检索方式
- 文档系统分层（入口/说明/历史/操作/参考）+ 决策记录（ADR，只增不改）+ 文档门禁（`scripts/doc_gate.sh`）

**残骸**：`docs/plan.md`（空文件，已删）、`docs/designs/`（与 `docs/design/` 重复，已归档）、两份拼写错误的 `ARCHITERTURE.md`（已修正）。

**相关 ADR**：[0007 性能门禁分级](./decisions/0007-tiered-bench-gate-and-e2e-excluded.md)
