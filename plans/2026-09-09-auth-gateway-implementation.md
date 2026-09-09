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

## 评审修复 P0（2026-09-09，feature/p7-auth-gateway）

评审发现两个 P0 缺陷并修复：① Traefik 未把用户身份断言注入给 backend，登录后所有受保护 API 401；② auth-service 与 backend 的 JWT issuer/audience 默认值不一致（`stock-auth-service`/`stock-api` vs `stock-bot-auth`/`urn:stock-bot:api`）。

- [x] **P0.1 统一 JWT 契约**
  - `auth-service/app/config.py`：`jwt_issuer` 默认 `stock-bot-auth`、`jwt_audience` 默认 `urn:stock-bot:api`（与 backend 默认对齐）
  - `docker-compose.yml`：auth-service 显式注入 `JWT_ISSUER`/`JWT_AUDIENCE`；api 显式注入 `AUTH_ISSUER`/`AUTH_AUDIENCE`/`AUTH_JWKS_URL`（引用同一组 `${JWT_*}` 变量）
  - `.env.docker.example`：更新 `JWT_ISSUER`，新增 `JWT_AUDIENCE` 与 `INTERNAL_API_TOKEN`
  - 新增交叉契约测试：`auth-service/tests/test_jwt_signer.py::test_assertion_cross_service_contract` 与 `backend/tests/test_auth_guard.py::test_cross_service_assertion_contract`（同 claim 结构手签 JWT 过 backend 验签；篡改 iss/aud 必须失败）
- [x] **P0.2 新建 forward-auth sidecar（断言注入 + CSRF 强制）**
  - 新增 `forward-auth/`（FastAPI + httpx，端口 9000）：`POST /verify` 解析 `stockbot_session` Cookie → 内存 TTL 缓存（25s，锁防穿透）→ auth-service `/internal/principal/assertion` 换取断言（非 200/连接失败对已有会话 fail closed 503）→ 非幂等方法经 `/internal/session/introspect` 强制 CSRF（失败 403 `AUTH_CSRF_FAILED`）→ 成功置 `X-Principal-Assertion` 响应头；匿名（无 Cookie）直接放行；`GET /healthz` 健康探针
  - 注意：Traefik v3 forwardAuth 固定以 GET 调用 /verify，原始方法经 `X-Forwarded-Method` 读取（11 项离线测试全覆盖）
- [x] **P0.3 Traefik 接线**
  - `gateway/dynamic/middlewares.yml`：新增 `strip-assertion`（customRequestHeaders 置空剥除客户端伪造断言头，置于链首）与 `forward-auth`（authResponseHeaders: [X-Principal-Assertion]）
  - **有意偏离**：`trustForwardHeader: false`——若为 true，客户端可伪造 `X-Forwarded-Method: GET` 绕过 CSRF 强制；关闭后 Traefik 用真实原始方法覆写该头（理由已沉淀至架构文档 4.4.4）
  - `docker-compose.yml`：新增 forward-auth 服务（无宿主机端口、healthcheck /healthz）；`api` 与 `api-tasks` 路由中间件链加 `strip-assertion@file` 与 `forward-auth@file`；gateway depends_on 增加 forward-auth
- [x] **P0.4 CI 登录闭环 smoke**
  - `.github/workflows/ci.yml` docker-smoke job 新增：注册 → GET /auth/csrf → 登录（cookie jar）→ `GET /api/v1/watchlists` 必须登录态 200 → 未登录访问必须 401
- [x] **P0.5 文档**
  - `docs/architecture/authentication-and-gateway.md` 新增「4.4 Principal Assertion 契约」：claims 表、默认 iss/aud、forward-auth 流程、strip-assertion 与 authResponseHeaders/trustForwardHeader 说明；4.2 节 iss/aud 示例同步更新为新契约

---

## 评审修复 P1（2026-09-09，feature/p7-auth-gateway）

针对 P0 之后的安全评审结论，修复 6 项 P1 缺陷（P1-3/4/5/6/7/9）：

- [x] **P1.3 登录响应体去除凭据**
  - `AuthResponse` 仅保留 `user` + `expires_in`；`session_id`/`csrf_token` 只经 Set-Cookie 下发，不再出现在 JSON body（`auth-service/app/schemas/auth.py`、`app/api/auth.py`）
  - 前端 `AuthResponseData` 类型同步删字段；登录页/features/auth 本就未消费这两个字段，`client.ts` CSRF 注入只依赖 Cookie，无回归
- [x] **P1.4 移除 X-Session-Id Header 旁路**
  - logout、/session(/me)、/csrf、/sessions、/sessions/{id} 五个外部端点删除 `x_session_id` Header 参数与 fallback，会话识别仅认 `stockbot_session` Cookie；全仓 grep 确认前端从未发送该 Header
- [x] **P1.5 服务端 CSRF 强制（认证面）**
  - 新增 `_require_csrf_double_submit` 依赖：register/login 强制「先 GET /auth/csrf → Cookie + X-CSRF-Token Header 常数时间比对」，失败 `403 AUTH_CSRF_FAILED`
  - logout 升级为会话绑定校验（`crypto.verify_csrf_token`）；无有效会话的登出仅清 Cookie 跳过校验
  - 前端 `client.ts` 对非 GET 自动注入 token（login/register/logout 均未 skipCsrf），CI smoke 补「注册前先取 CSRF」步骤
- [x] **P1.7 /internal/* 服务间令牌**
  - `INTERNAL_API_TOKEN` 非空时，/internal 路由（router 级 dependency）要求 `X-Internal-Token` 相等（hmac.compare_digest），否则 401；为空（本地/测试默认）不校验。forward-auth 侧上批已实现发送，compose 同一变量注入，无需改动
- [x] **P1.6/P1.9 JWT 密钥持久化与 Secure Cookie fail-fast**
  - compose：auth-service 注入 `JWT_PRIVATE_KEY_PEM`/`JWT_PUBLIC_KEY_PEM`（引用 `AUTH_JWT_*_PEM`）、`COOKIE_SECURE`（引用 `AUTH_COOKIE_SECURE`）、`APP_ENV`（`${APP_ENV:-development}`，替换原硬编码 production）
  - `.env.docker.example` 新增密钥对与 Secure 配置项（注释含 openssl 生成方法）；**有意将模板 `APP_ENV` 从 production 调整为 development**——新 fail-fast 校验下 production+Secure=false 无法启动，且 Gateway 仅暴露 80 明文入口（无 TLS 时浏览器会丢弃 Secure Cookie），生产部署需置 production + HTTPS + AUTH_COOKIE_SECURE=true
  - config.py 新增 `field_validator("cookie_secure")`：`app_env=production` 且 `cookie_secure=False` 启动即抛错
- [x] **P1.x 测试与文档**
  - 新增/改造 13 个用例：CSRF 403（缺失/不匹配）、double-submit 200、logout CSRF、X-Session-Id 旁路移除、internal token 三态、config fail-fast（`tests/test_api_auth.py`、`test_api_internal.py`、`test_config.py` 新建）
  - 架构文档 3.3 改为「CSRF 三层防御」，新增 4.5（internal token）/4.6（生产密钥与 Secure），错误码对齐 `AUTH_CSRF_FAILED`，路由矩阵 login/register 标注 double-submit

---

## 评审修复 P2（2026-09-09，feature/p7-auth-gateway）

针对前两批（P0/P1）之后的安全评审结论，修复 2 项 P1 基建遗留（P1-8/P1-10）与 8 项 P2 纵深缺陷（P2-11 ~ P2-18）：

- [x] **P1.8 RabbitMQ 替换 guest/guest 并收敛端口**
  - `docker-compose.yml`：rabbitmq 凭据改为 `${RABBITMQ_DEFAULT_USER:-stockbot}` / `${RABBITMQ_DEFAULT_PASS:-stockbot_pass}`；删除 5672/15672 宿主机映射（管理 UI 按需 docker exec 或临时端口转发访问）；migrate/api/worker 三处 `RABBITMQ_URL` 统一引用同组变量
  - `.env.docker.example` 新增凭据变量（注释生产必改）并更新 URL 示例；`backend/.env.example` localhost 场景同步 stockbot/stockbot_pass
- [x] **P1.10 Vite 开发代理补全**
  - `frontend/vite.config.ts` server.proxy 新增 `/auth` 与 `/.well-known` → `http://localhost:8001`，本地开发登录/会话/JWKS 与生产网关同源形态一致（Cookie 域与 CSRF 正常工作）
- [x] **P2.11 会话绝对过期上限**
  - auth-service `config.py` 新增 `absolute_session_ttl=604800`（7 天）；`SessionService.get_session` 滑动续期前检查 `now - created_at >= absolute_session_ttl`，超限则删除 `session:{id}`、从 `user_sessions:{user_id}` srem 并返回 None（登出语义；DB 快照由 revoke 流程/自然过期兜底）——滑动 TTL 不再能无限续命
  - 测试：超期会话返回 None 且 Redis key/set 成员被清理、introspect 报 inactive；未超期会话正常续期（`tests/test_session_service.py`）
- [x] **P2.12 审计事件脱敏 session_id**
  - 全仓 grep `record_audit_event`：`auth.logout` 与 `auth.login.success` payload 中的明文 `session_id` 改为 `session_id_hash = hash_token(session_id)`（SHA-256）；`auth_sessions.id` 列为运营必需保留明文，不动
- [x] **P2.13 X-Forwarded-For 信任边界**
  - auth-service `config.py` 新增 `trust_forwarded_for: bool = False`（默认不信任）；`_extract_client_meta` 仅在该开关开启时解析 XFF，否则取 socket 对端地址——客户端无法再伪造该头污染审计 IP
  - compose auth-service 注入 `TRUST_FORWARDED_FOR=${AUTH_TRUST_FORWARDED_FOR:-true}`（Traefik 在前的部署形态）；`.env.docker.example` 新增该变量及直连部署必须置 false 的注释
  - 测试：monkeypatch 两种取值验证 `tests/test_api_auth.py::test_extract_client_meta_*`
- [x] **P2.14 存量标签迁移归属策略文档**
  - 确认迁移 `5a1b2c3d4e5f` 以 server_default 全零 UUID（非随机 UUID）回填 `stock_user_tags.user_id`；`docs/architecture/auth-data-model.md` 新增「存量标签归属与认领策略」：幽灵/系统迁移用户语义、对真实用户不可见的隔离原理、管理员认领 SQL 模板与唯一约束冲突排查/事务/缓存失效注意事项
- [x] **P2.15 Traefik dashboard 端口收敛**
  - compose gateway 删除 `8080:8080` 宿主机映射（dashboard 仅内网可达）；`.env.docker.example` `GATEWAY_DASHBOARD_PORT` 注释更新为仅内网/按需映射
- [x] **P2.16 nginx 收敛后端文档暴露**
  - `frontend/nginx.conf` 删除 docs/redoc/openapi.json 透传，仅保留 `location = /health` 健康探针——生产后端 API 文档不再公开
- [x] **P2.17 CORS 收敛**
  - auth-service `cors_origins` 默认列表移除 8000/8001 端口项，仅保留前端源（3000/5173/80 及对应 127.0.0.1）；`.env.docker.example` `CORS_ORIGINS` 移除 `http://localhost:8080`；backend 默认值本已最小，不动
- [x] **P2.18 暴力破解防护文档**
  - `docs/architecture/authentication-and-gateway.md` 新增 4.7「暴力破解防护（双层纵深）」：Traefik auth-ratelimit（按 IP average 20 / burst 10 / period 1s）+ auth-service 账号级失败锁定（`failed_attempts >= 5` 锁定 15 分钟、过期/成功登录自动解锁），并标注后续可叠加按用户名维度限流

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
