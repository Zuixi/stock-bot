# 认证微服务、API Gateway 与多用户数据归属实施计划 (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建企业级身份认证微服务（auth-service）、API Gateway（BFF 与短时签名断言）、Stock API 细粒度权限控制与多用户数据归属隔离体系，将 stock_bot 升级为具备高安全性、可审计、多端会话同步的金融投研平台。

**Architecture & References:**
- 架构设计文档：`docs/architecture/authentication-and-gateway.md`
- 数据模型与安全算法：`docs/architecture/auth-data-model.md`
- 既有数据库架构：`docs/architecture/database-architecture.md`
- 规范与最佳实践：`docs/references/best-practices.md`

**Tech Stack:**
- **auth-service**: FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL (auth_* schema) + Redis + Argon2id (passlib) + PyJWT / cryptography (JWKS).
- **API Gateway**: Nginx / FastAPI BFF + Cookie Session 解析 + CSRF 防御 + 短时 RS256 Principal Assertion 签名.
- **Stock API**: FastAPI + JWKS 本地验签依赖注入 + SQLAlchemy 2.0 + PostgreSQL + Redis + RabbitMQ.
- **Frontend**: React 18 + TypeScript + Ant Design 5 + Zustand + TanStack React Query + Axios.

---

## 阶段划分与里程碑 (Stages Overview)

| 阶段 | 核心任务 | 交付物 | 状态 |
| :--- | :--- | :--- | :---: |
| **Stage 0** | 架构拓扑、安全契约、数据模型与追踪计划设计 | 架构与数据模型设计文档、实施计划 | **[x] 已完成** |
| **Stage 1** | auth-service 独立微服务开发与凭证/JWKS体系 | auth-service 微服务、Alembic 迁移、Argon2id、Redis Session、JWKS 端点 | **[x] 已完成** |
| **Stage 2** | API Gateway / BFF 会话代理与 CSRF 拦截 | Gateway 配置/代码、Cookie-Session 转换、Principal Assertion 签名 | **[x] 已完成** |
| **Stage 3** | Stock API 接入 JWKS 本地验签与统一错误契约 | 验签依赖注入、零信任未签名头过滤、全局统一 Error/Trace 中间件 | **[x] 已完成** |
| **Stage 4** | 业务数据归属改造（自选股/标签/任务/缓存隔离与零信任保护） | 验签与权限保护矩阵、Alembic 迁移、自选股服务端 API、标签 user_id 隔离 | **[x] 已完成** |
| **Stage 5** | 前端认证状态机、路由守卫与自选股云端化 | Auth Store、登录/注册/个人中心 UI、CSRF 自动注入、自选股云同步 | **[x] 已完成** |
| **Stage 6** | 多容器 Compose 编排、全链路集成测试与红蓝验收 | docker-compose.yml 扩展、E2E 测试套件、渗透/越权对抗测试 | **[x] 已完成** |

---

## 详细实施任务与清单

### Stage 0: 架构与数据模型设计（已完成）

- [x] **0.1 编写认证与网关架构设计** (`docs/architecture/authentication-and-gateway.md`)
  - 目标拓扑图与组件网络隔离规范
  - 同源 BFF、HttpOnly 不透明 Cookie 会话与 CSRF 防护策略
  - Gateway 与下游 API 的零信任 Principal Assertion (RS256) 签名与 JWKS 验签机制
  - 全局统一错误契约 (`code`, `message`, `details`, `trace_id`)
  - 路由保护矩阵（公开 / 受保护 / 管理员端点）
- [x] **0.2 编写认证数据模型与安全存储设计** (`docs/architecture/auth-data-model.md`)
  - `auth-service` 独立数据库表结构 (`auth_users`, `auth_credentials`, `auth_roles`, `auth_permissions`, `auth_sessions`, `auth_refresh_token_families`, `auth_audit_events`)
  - Argon2id 密码哈希安全参数与重哈希升级机制
  - Redis 会话与 CSRF 存储模型、滑动过期与多端踢出
  - Refresh Token 家族轮换 (RTR) 与重放攻击阻断
  - 业务数据模型改造设计（`stock_custom_sw_tags` 加 `user_id`、自选股服务端表设计、`tasks` 加 `requested_by`、Redis 缓存 Key 用户级命名空间隔离）
- [x] **0.3 编写 Stage 追踪实施计划** (`plans/2026-09-09-auth-gateway-implementation.md`)
- [x] **0.4 提交 Stage 0 阶段性文档与规范**

---

### Stage 1: auth-service 独立微服务构建（已完成）

- [x] **1.1 项目脚手架与依赖配置**
  - 新建 `auth-service/` 目录结构（FastAPI、SQLAlchemy 2.0 async、alembic、argon2-cffi、cryptography、pyjwt、redis、pydantic v2）
  - 数据库配置与独立会话池（`auth-service/app/core/database.py`）
  - Redis 客户端配置（`auth-service/app/core/redis.py`）
- [x] **1.2 数据库模型与 Alembic 初始迁移**
  - 编写 ORM 模型：`AuthUser`, `AuthCredential`, `AuthRole`, `AuthPermission`, `AuthUserRole`, `AuthSession`, `AuthRefreshTokenFamily`, `AuthAuditEvent`
  - 生成并执行初始化迁移脚本，预置默认角色 (`admin`, `researcher`, `analyst`, `trader`, `operator`, `viewer`) 与基础权限项
- [x] **1.3 核心密码学与安全服务**
  - 实现 Argon2id 密码哈希与校验器 (`auth-service/app/core/crypto.py`)
  - 实现 RSA 秘钥对管理与 JWKS 导出、短时断言签名 (`auth-service/app/core/jwt_signer.py`)
  - 实现 Redis Session 管理器（创建、查询、续期、下线、多端销毁、DB 快照同步）
- [x] **1.4 认证与权限 API 路由实现**
  - `POST /auth/register`：用户注册（参数校验、防重、默认角色分配、审计日志）
  - `POST /auth/login`：用户登录（密码比对、失败计数锁定、会话写入 Redis/DB、签发 HttpOnly Cookie）
  - `POST /auth/logout`：登出并销毁会话
  - `GET /auth/session` / `GET /auth/me`：获取当前登录用户信息与权限列表
  - `GET /auth/csrf`：获取或刷新 CSRF 令牌
  - `GET /auth/sessions` & `DELETE /auth/sessions/{id}`：会话列表与远程下线
  - `POST /internal/session/introspect` & `POST /internal/principal/assertion`：网关内网会话内省与断言签名
  - `GET /health/live` & `GET /health/ready`：微服务就绪与存活探针
  - `GET /.well-known/jwks.json` & `GET /auth/jwks.json`：分发公钥集供网关和下游服务验签
- [x] **1.5 纯单元与集成测试套件**
  - 编写密码哈希参数单测、Session 轮换单测、JWKS 导出有效性单测、RBAC 权限检查单测、HTTP 路由全链路测试（16 项离线测试 100% 通过）

---

### Stage 2: API Gateway / BFF 会话代理与断言签名（已完成）

- [x] **2.1 Gateway 基础设施与容器拓扑配置 (Stage 3)**
  - 建立 Traefik API Gateway 静态配置 (`gateway/traefik.yml`：entryPoints web:80/traefik:8080, docker/file providers, JSON 日志与 Prometheus 指标)
  - 建立 Traefik 动态中间件配置 (`gateway/dynamic/middlewares.yml`：security headers, rateLimit, compress, stripPrefix)
  - 改造根 `docker-compose.yml`：编排 `gateway`, `auth-service`, `auth-db`, `migrate-auth`, `api`, `frontend` 等容器
  - 完成外部端口收敛：移除 `api:8000` 与 `frontend:3000` 外部端口直接暴露，仅由 Gateway 80/8080 统筹入口
  - 配置路由分发规则与 Traefik labels（`/*` -> frontend, `/auth/*` & `/.well-known/jwks.json` -> auth-service, `/api/*` -> api，tasks 写接口挂载独立 rateLimit 中间件）
  - 新增 `.env.docker.example` 环境变量配置模板与 `backend/docker-compose.yml` 容错适配
- [x] **2.2 Cookie 解析与会话中间件**
  - 从入站请求提取 `stockbot_session` Cookie
  - 高速校验 Redis 中的 Session 状态与有效性；若无效则对受保护接口返回 `401 AUTH_UNAUTHORIZED`
- [x] **2.3 CSRF 防御拦截中间件**
  - 对非幂等方法（POST/PUT/DELETE/PATCH）提取并校验 `X-CSRF-Token` 请求头与 Session 绑定值
  - 检查 Origin / Referer 合法性
- [x] **2.4 短时 Principal Assertion JWT 签名注入**
  - 对通过鉴权的请求，使用 Gateway/Auth 私钥签发短时 JWT（TTL: 60s）
  - 注入 `X-Principal-Assertion` 请求头并转发至下游 Stock API
  - 过滤并强制剥离客户端可能伪造的任何 `X-User-*` 头部

---

### Stage 3: Stock API 接入 JWKS 本地验签与统一错误契约（已完成）

- [x] **3.1 Stock API 安全依赖与 JWKS 验签器**
  - 建立 `backend/app/core/auth/` 模块（`principal.py`, `jwks.py`, `verifier.py`），实现 JWKS 异步加载与本地公钥缓存（单飞刷新机制）
  - 实现 FastAPI 依赖项 `CurrentUserDep`、`OptionalUserDep`、`require_roles(*roles)` 与 `require_permissions(*perms)`
  - 严格校验 `X-Principal-Assertion` 签名 (RS256)、时效性 (`exp`)、受众 (`aud`)、发行方 (`iss`)
  - 严格遵守零信任原则，拦截并忽略所有入站的原始未签名 `X-User-*` 伪造头
- [x] **3.2 敏感与写接口权限矩阵保护**
  - 将 `/api/v1/tasks/fetch-*`、`/api/v1/tasks/run-clustering` 等任务触发接口附加 `tasks:trigger` 权限保护
  - 将 `/api/v1/industries/{key}/metrics/batch` 行业数据导入接口附加 `research:manage` 权限保护
  - 将 `/api/v1/market/sse-snapshots/backfill` 历史回补接口附加 `tasks:trigger` 权限保护
  - 保持基础公开行情接口免登可读并适配 `OptionalUserDep`
- [x] **3.3 前端异常传播与状态分流修复**
  - 修复 `frontend/src/shared/api/quotes.ts` 与 `stocks.ts` 中跨交易所 fallback 逻辑（仅对 404 降级，401/403/500 立即向上抛出）
  - 优化 `stock-detail` 页面状态机，精准分流 loading、error 与 404 not-found

---

### Stage 4: 业务数据模型多用户归属与自选股服务端化（已完成）

- [x] **4.1 数据库迁移 (Alembic)**
  - `stock_user_tags` 增加 `user_id` 列，更新唯一约束为 `(user_id, symbol, tag_name)`
  - 新增 `user_watchlists` 与 `user_watchlist_items` 表
  - `tasks` 表增加 `requested_by` (UUID, nullable)
- [x] **4.2 自选股服务端 API 实现**
  - 新增 `app/api/v1/watchlists.py` 路由
  - 实现 `GET /api/v1/watchlists`、`POST /api/v1/watchlists/items`、`DELETE /api/v1/watchlists/items/{symbol}`、`PUT /api/v1/watchlists/items/reorder`
  - 数据操作自动绑定当前 `principal.user_id`
- [x] **4.3 自定义标签按用户隔离改造**
  - 改造 `app/services/user_tag_service.py` 与标签全局端点，所有自定义标签查询与更新均按 `user_id` 过滤
- [x] **4.4 任务审计与 Redis 缓存 Key 隔离**
  - 任务触发写入 `requested_by = principal.user_id`
  - 用户私有缓存统一前缀 `user:{user_id}:*`

---

### Stage 5: 前端 React 认证体系与自选股云端化（已完成）

- [x] **5.1 认证状态机与统一请求层**
  - 重构 `frontend/src/shared/api/client.ts`，支持 `credentials: include`、CSRF 单飞并发获取与自动注入、结构化 `ApiError` 提取、401 事件派发与 `skipAuth`
  - 新建 `frontend/src/shared/api/auth.ts`（封装 login, register, logout, getSession, getCsrf）
  - 新建 `frontend/src/features/auth/store.ts` & `context.tsx`（Zustand + React Query 管理用户会话、登录状态、角色与权限校验）
- [x] **5.2 认证相关界面与路由守卫**
  - 新增 `/login` 登录/注册切换页 (`frontend/src/pages/login/index.tsx`)，支持 Ant Design 5 表单校验与 returnTo 自动跳转
  - 顶部导航栏 `UserMenu`：展示登录用户信息、角色徽章、退出登录确认弹窗与缓存清理
  - 实现 `RequireAuth` 路由守卫包装器，支持 authReady 门控防闪烁、权限不足 403 与未登录重定向
- [x] **5.3 自选股云端化迁移**
  - 新建 `frontend/src/shared/api/watchlist.ts` 对接服务端自选股 API
  - 重构 `frontend/src/features/watchlist`（`store.ts` + `useWatchlist.ts`）：数据源由纯 localStore 升级为 TanStack Query 调取服务端 API 与本地降级，登出全量清理
  - 更新自选股页面 `frontend/src/pages/watchlist/index.tsx`、`WatchlistTable.tsx` 与股票详情页 `UserTags.tsx` 支持按登录态隔离读写展示

---

### Stage 6: 多容器编排、全链路集成测试与验收（已完成）

- [x] **6.1 Docker Compose 编排集成与 CI/CD 自动化门禁**
  - 完善 `docker-compose.yml`，编排 gateway, auth-service, backend, frontend, workers, postgres, redis, rabbitmq
  - 配置容器间内部 DNS 与网络隔离，收敛外部端口至 Gateway:80/8080
  - 扩展 `.github/workflows/ci.yml` 与 `cd.yml`，增加 auth-service 门禁 job、多架构构建及网关烟雾测试
- [x] **6.2 自动化测试与越权对抗验证**
  - 编写端到端认证与用户隔离 Playwright 测试套件（`frontend/e2e/auth.spec.ts` 与 `frontend/e2e/userIsolation.spec.ts`）
  - 编写安全对抗测试用例：
    - 伪造未签名 `X-User-Id` 越权攻击验证（必须 401/403 拦截）
    - 篡改 CSRF Token 伪造写请求验证（必须 403 拦截）
    - 横向越权读取其他用户自定义标签与自选股验证（必须数据隔离）
    - Refresh Token 重放攻击触发全家族吊销验证
- [x] **6.3 文档更新与经验沉淀**
  - 更新 `docs/references/best-practices.md`
  - 记录 `docs/Changelog.md`

---

## 质量门禁与验证命令

- **后端 Lint & Typecheck**:
  ```bash
  cd backend && uv run --extra dev ruff check .
  cd backend && uv run --extra dev mypy app
  ```
- **auth-service Lint & Test**:
  ```bash
  cd auth_service && uv run --extra dev ruff check .
  cd auth_service && uv run pytest tests/ -v
  ```
- **前端 Lint & Build**:
  ```bash
  cd frontend && npm run lint
  cd frontend && npm run build
  ```
- **全系统 Docker 构建**:
  ```bash
  docker compose build && docker compose up -d
  ```
