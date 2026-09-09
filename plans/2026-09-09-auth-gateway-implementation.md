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
| **Stage 1** | auth-service 独立微服务开发与凭证/JWKS体系 | auth-service 微服务、Alembic 迁移、Argon2id、Redis Session、JWKS 端点 | [ ] 待开始 |
| **Stage 2** | API Gateway / BFF 会话代理与 CSRF 拦截 | Gateway 配置/代码、Cookie-Session 转换、Principal Assertion 签名 | [ ] 待开始 |
| **Stage 3** | Stock API 接入 JWKS 本地验签与统一错误契约 | 验签依赖注入、零信任未签名头过滤、全局统一 Error/Trace 中间件 | [ ] 待开始 |
| **Stage 4** | 业务数据归属改造（自选股/标签/任务/缓存隔离） | Alembic 迁移、自选股服务端 API、标签 user_id 隔离、Task requested_by 审计 | [ ] 待开始 |
| **Stage 5** | 前端认证状态机、路由守卫与自选股云端化 | Auth Store、登录/注册/个人中心 UI、CSRF 自动注入、自选股云同步 | [ ] 待开始 |
| **Stage 6** | 多容器 Compose 编排、全链路集成测试与红蓝验收 | docker-compose.yml 扩展、E2E 测试套件、渗透/越权对抗测试 | [ ] 待开始 |

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

### Stage 1: auth-service 独立微服务构建

- [ ] **1.1 项目脚手架与依赖配置**
  - 新建 `auth_service/` 目录结构（FastAPI、SQLAlchemy 2.0 async、alembic、argon2-cffi、cryptography、pyjwt、redis、pydantic v2）
  - 数据库配置与独立会话池（`auth_service/core/database.py`）
  - Redis 客户端配置（`auth_service/core/redis.py`）
- [ ] **1.2 数据库模型与 Alembic 初始迁移**
  - 编写 ORM 模型：`AuthUser`, `AuthCredential`, `AuthRole`, `AuthPermission`, `AuthUserRole`, `AuthSession`, `AuthRefreshTokenFamily`, `AuthAuditEvent`
  - 生成并执行初始化迁移脚本，预置默认角色 (`admin`, `researcher`, `trader`, `viewer`) 与基础权限项
- [ ] **1.3 核心密码学与安全服务**
  - 实现 Argon2id 密码哈希与校验器 (`auth_service/core/security/password.py`)
  - 实现 RSA/ECDSA 秘钥对生成、轮换与 JWKS 导出 (`auth_service/core/security/keys.py`)
  - 实现 Redis Session 管理器（创建、查询、续期、下线、多端销毁）
- [ ] **1.4 认证与权限 API 路由实现**
  - `POST /auth/register`：用户注册（参数校验、防重、默认角色分配、审计日志）
  - `POST /auth/login`：用户登录（密码比对、失败计数锁定、会话写入 Redis/DB、签发 HttpOnly Cookie）
  - `POST /auth/logout`：登出并销毁会话
  - `GET /auth/me`：获取当前登录用户信息与权限列表
  - `GET /auth/.well-known/jwks.json`：分发公钥集供网关和下游服务验签
  - `GET /auth/sessions` & `DELETE /auth/sessions/{id}`：会话列表与远程下线
  - `GET/POST /auth/admin/*`：管理员用户与角色分配端点
- [ ] **1.5 纯单元与集成测试套件**
  - 编写密码哈希参数单测、Session 轮换单测、JWKS 导出有效性单测、RBAC 权限检查单测

---

### Stage 2: API Gateway / BFF 会话代理与断言签名

- [ ] **2.1 Gateway 基础设施配置**
  - 配置网关路由分发规则（`/*` -> Frontend, `/auth/*` -> auth-service, `/api/*` -> Stock API）
- [ ] **2.2 Cookie 解析与会话中间件**
  - 从入站请求提取 `stockbot_session` Cookie
  - 高速校验 Redis 中的 Session 状态与有效性；若无效则对受保护接口返回 `401 AUTH_UNAUTHORIZED`
- [ ] **2.3 CSRF 防御拦截中间件**
  - 对非幂等方法（POST/PUT/DELETE/PATCH）提取并校验 `X-CSRF-Token` 请求头与 Session 绑定值
  - 检查 Origin / Referer 合法性
- [ ] **2.4 短时 Principal Assertion JWT 签名注入**
  - 对通过鉴权的请求，使用 Gateway/Auth 私钥签发短时 JWT（TTL: 60s）
  - 注入 `X-Principal-Assertion` 请求头并转发至下游 Stock API
  - 过滤并强制剥离客户端可能伪造的任何 `X-User-*` 头部

---

### Stage 3: Stock API 接入 JWKS 本地验签与统一错误契约

- [ ] **3.1 Stock API 安全依赖与 JWKS 验签器**
  - 新建 `backend/app/core/security.py`，实现 JWKS 客户端与异步公钥缓存
  - 实现 FastAPI 依赖项 `get_current_principal()` 与 `require_permissions(*perms)`
  - 严格校验 `X-Principal-Assertion` 签名、时效性 (`exp`)、受众 (`aud=stock-api`)
  - 拦截并丢弃所有入站的原始未签名 `X-User-*`
- [ ] **3.2 全局统一错误与链路追踪中间件**
  - 重构全局异常处理器，统一捕获 `HTTPException`, `RequestValidationError`, `Exception`
  - 返回符合规范的 `{ "code": "...", "message": "...", "details": ..., "trace_id": "..." }` 响应结构
  - 请求链路全程注入与回传 `X-Request-Id` / `trace_id`
- [ ] **3.3 路由保护重构**
  - 将 `/api/v1/tasks/fetch-*` 与 `/api/v1/tasks/run-clustering` 标记为 Admin 专属（校验 `tasks:trigger` 权限）
  - 保持基础公开行情接口免登可读

---

### Stage 4: 业务数据模型多用户归属与自选股服务端化

- [ ] **4.1 数据库迁移 (Alembic)**
  - `stock_custom_sw_tags` 增加 `user_id` 列，更新唯一约束为 `(user_id, symbol, industry_code)`
  - 新增 `user_watchlists` 与 `user_watchlist_items` 表
  - `tasks` 表增加 `requested_by` (UUID, nullable)
- [ ] **4.2 自选股服务端 API 实现**
  - 新增 `app/api/v1/watchlists.py` 路由
  - 实现 `GET /api/v1/user/watchlists`、`POST /api/v1/user/watchlists/items`、`DELETE /api/v1/user/watchlists/items/{symbol}`
  - 数据操作自动绑定当前 `principal.user_id`
- [ ] **4.3 自定义标签按用户隔离改造**
  - 改造 `app/services/market_service.py` 与 `sw_industry` 仓库，所有自定义标签查询与更新均按 `user_id` 过滤
- [ ] **4.4 任务审计与 Redis 缓存 Key 隔离**
  - 任务触发写入 `requested_by = principal.user_id`
  - 用户私有缓存统一前缀 `cache:user:{user_id}:*`

---

### Stage 5: 前端 React 认证体系与自选股云端化

- [ ] **5.1 认证状态机与 Axios 拦截器**
  - 新建 `frontend/src/features/auth/store.ts`（Zustand 管理用户会话、登录状态、权限清单）
  - Axios 拦截器：自动携带 Cookie、为写操作自动读取 `stockbot_csrf` 注入 `X-CSRF-Token`、统一提取 `trace_id`
  - 401 拦截处理：受保护路由自动重定向至登录页
- [ ] **5.2 认证相关界面与路由守卫**
  - 新增 `/login` 登录页与 `/register` 注册页
  - 顶部导航栏显示当前登录用户信息、角色徽章与退出登录按钮
  - 实现 `ProtectedRoute` 路由守卫包装器
- [ ] **5.3 自选股云端化迁移**
  - 重构 `frontend/src/features/watchlist`：数据源由纯 localStore 切换为 TanStack Query 调取服务端 API
  - 提供初次登录时“将本地未登录自选股一键合并上传至云端”的用户引导提示

---

### Stage 6: 多容器编排、全链路集成测试与验收

- [ ] **6.1 Docker Compose 编排集成**
  - 完善 `docker-compose.yml`，编排 gateway, auth-service, backend, frontend, workers, postgres, redis, rabbitmq
  - 配置容器间内部 DNS 与网络隔离
- [ ] **6.2 自动化测试与越权对抗验证**
  - 编写端到端认证测试（注册 -> 登录 -> 获取 Cookie -> 访问自选股 -> 登出）
  - 编写安全对抗测试用例：
    - 伪造未签名 `X-User-Id` 越权攻击验证（必须 401/403 拦截）
    - 篡改 CSRF Token 伪造写请求验证（必须 403 拦截）
    - 横向越权读取其他用户自定义标签与自选股验证（必须数据隔离）
    - Refresh Token 重放攻击触发全家族吊销验证
- [ ] **6.3 文档更新与经验沉淀**
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
