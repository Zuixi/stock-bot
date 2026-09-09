# 股票数据分析系统 - 认证微服务与 API Gateway 架构设计

> 文档版本：1.0 | 实施阶段：Stage 0 | 生效日期：2026-09-09  
> 目标系统：stock_bot 认证与安全访问控制体系（Gateway + Auth Microservice + Stock API）

---

## 1. 架构目标与设计原则

在引入多用户支持、投研工作台协作与安全合规体系的背景下，stock_bot 需从单体无状态公开服务演进为**具备统一身份认证、细粒度权限控制与多租户数据隔离**的企业级金融投研平台。

核心设计原则：
1. **零信任内网传输（Zero-Trust Internal Assertion）**：下游业务服务（Stock API / Tasks / Research）严禁信任任何未签名的入站 Header（如裸 `X-User-Id`）；所有身份上下文必须由 API Gateway 转换并签署短时加密凭证（Principal Assertion JWT），业务服务通过 JWKS 公钥验签。
2. **同源 BFF 与 HttpOnly Cookie（BFF-Pattern & XSS Immunity）**：前端 SPA 完全运行于同源沙箱内，不接触任何原始 Access Token / Refresh Token，认证凭据以 `HttpOnly`, `Secure`, `SameSite` Cookie 形式由 Gateway/BFF 统一托管，彻底免疫 XSS 窃取。
3. **全链路 CSRF 与重放防御（Defense in Depth）**：对所有非幂等操作（POST / PUT / DELETE / PATCH）推行双重防御（SameSite Cookie + 显式 `X-CSRF-Token` 校验 + Origin/Referer 白名单检查）。
4. **统一错误与可观测性契约（Unified Contract & Traceability）**：全系统（Gateway、Auth Service、Stock API）推行统一结构化错误响应规范，全局透传 `trace_id`，实现全链路请求审计与链路追踪。

---

## 2. 目标系统拓扑与网络边界

### 2.1 整体拓扑图

```
                         ┌─────────────────────────────────────────┐
                         │               Client Browser            │
                         │    (React 18 SPA + AntD + ECharts)      │
                         └────────────────────┬────────────────────┘
                                              │ HTTPS / Same-Origin
                                              │ Cookie (stockbot_session, stockbot_csrf)
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 API Gateway / BFF                                      │
│  - 静态资源路由 / 反向代理                                                            │
│  - SSL/TLS 终结 & HTTP/2                                                               │
│  - 会话识别与 Cookie <-> Redis Session 校验                                            │
│  - CSRF 令牌提取与校验                                                                 │
│  - 签名生成短时 Principal Assertion JWT (RS256, TTL: 60s)                             │
│  - 全局速率限制 (Rate Limiting) & 请求 ID (trace_id) 注入                              │
└──────────────┬──────────────────────────────┬───────────────────────────┬──────────────┘
               │                              │                           │
  /auth/* 请求 │                 /api/* 请求  │                           │ /api/v1/tasks
               ▼                              ▼                           ▼
┌──────────────────────────────┐ ┌───────────────────────────┐ ┌───────────────────────────┐
│     auth-service (微服务)    │ │   Stock API (业务主服务)  │ │   Task & Ingest (任务API) │
│ - 用户注册/登录/登出         │ │ - 股票行情/分类/指标      │ │ - 异步任务状态查询        │
│ - 凭证校验 (Argon2id)        │ │ - 投研工作台数据          │ │ - 手动数据回填触发        │
│ - 会话与 Token 生命周期管理  │ │ - 用户自选股 / 自定义标签 │ │ - 聚类计算触发            │
│ - RBAC 权限与角色管理        │ │ - 本地 JWKS 验签          │ │ - 本地 JWKS 验签          │
│ - JWKS 端点 (/auth/jwks.json)│ │ - 数据归属 (user_id) 隔离 │ │ - requested_by 审计       │
│ - 审计日志落库               │ │                           │ │                           │
└──────────────┬───────────────┘ └─────────────┬─────────────┘ └─────────────┬─────────────┘
               │                               │                             │
               │                               │                             │ RabbitMQ 任务派发
               │                               │                             ▼
               │                               │               ┌───────────────────────────┐
               │                               │               │      Worker 集群          │
               │                               │               │ - Quotes Worker           │
               │                               │               │ - Daily Basic Worker      │
               │                               │               │ - Industry Ingest Worker  │
               │                               │               │ - Cluster Worker          │
               │                               │               └─────────────┬─────────────┘
               │                               │                             │
               ▼                               ▼                             │
┌──────────────────────────────┐ ┌───────────────────────────┐               │
│       Redis Cluster / DB     │ │    PostgreSQL (业务数据库) │               │
│ - Session 状态 (hash)        │ │ - stocks / daily_quotes   │◄──────────────┘
│ - CSRF Token 白名单          │ │ - sw_industry_*           │
│ - Refresh Token 家族黑名单   │ │ - user_watchlists (新)    │
│ - 业务缓存 (带 user_id 隔离) │ │ - stock_custom_sw_tags    │
│ - JWKS 公钥缓存              │ │   (增加 user_id)          │
└──────────────────────────────┘ └───────────────────────────┘
```

### 2.2 内部网络与隔离边界

| 组件名称 | 暴露网络 | 监听端口 | 通信协议 | 访问控制策略 |
| :--- | :--- | :--- | :--- | :--- |
| **API Gateway** | 外部公网 / 内网前端 | 80, 443 | HTTP/1.1, HTTP/2 | 暴露公网入口，终结 TLS，配置严格 CORS 与安全响应头 |
| **auth-service** | 仅内部网络 | 8001 | HTTP/1.1 (JSON) | 仅允许来自 API Gateway 的路由转发和内部组件的 JWKS 查询 |
| **Stock API** | 仅内部网络 | 8000 | HTTP/1.1 (JSON) | 仅允许来自 API Gateway 转发，强制校验 `X-Principal-Assertion` |
| **PostgreSQL** | 仅内部网络 | 5432 | TCP (PostgreSQL) | 仅允许 auth-service, Stock API, Scheduler, Worker 访问 |
| **Redis** | 仅内部网络 | 6379 | TCP (RESP) | 独立 DB 或 Key 前缀分片隔离（Session: db 0, Cache: db 1） |
| **RabbitMQ** | 仅内部网络 | 5672 (AMQP), 15672 | AMQP 0-9-1 | 仅允许 Stock API (生产者) 与 Worker (消费者) 接入 |
| **Scheduler** | 仅内部网络 | 无入站端口 | 内部定时器 | 单向访问 PostgreSQL 与 Stock API / RabbitMQ |

---

## 3. 会话管理、同源 BFF 与 CSRF 防护策略

### 3.1 同源 BFF（Backend-For-Frontend）交互模型

为了彻底杜绝将 JWT / Refresh Token 存储在浏览器 `localStorage` 或 `sessionStorage` 中带来的 XSS 攻击面，本架构采用 **Same-Origin BFF + Opaque Session Cookie** 方案：

1. **同源映射**：浏览器仅与 API Gateway（如 `https://stock.example.com`）通信：
   - 静态资源与前端路由：`/*`
   - 认证 API：`/auth/*`
   - 业务 API：`/api/v1/*`
2. **凭据形态**：
   - 浏览器与 Gateway 之间传输**不透明会话标识符（Opaque Session ID）**，通过 `Set-Cookie` 写入客户端。
   - 客户端 JavaScript 代码**无法**通过 `document.cookie` 读取会话标识符（`HttpOnly`）。
   - 真正的用户信息、角色权限、关联的 Refresh Token 均加密持久化于 Redis / Auth DB。

### 3.2 Cookie 安全规范

```http
Set-Cookie: stockbot_session=s_9f8c2e1b4a7d3c5e8f0a1b2c3d4e5f6a; Path=/; Domain=.example.com; Secure; HttpOnly; SameSite=Lax; Max-Age=86400
Set-Cookie: stockbot_csrf=c_1a2b3c4d5e6f7a8b9c0d; Path=/; Domain=.example.com; Secure; SameSite=Lax; Max-Age=86400
```

- **`stockbot_session`**：
  - `HttpOnly: true`：防止任何 XSS 脚本读取会话 Cookie。
  - `Secure: true`：强制仅在 HTTPS 连接中传输（本地开发环境在 localhost 宽松模式）。
  - `SameSite: Lax`：防御绝大多数跨站请求伪造，同时允许用户通过外部链接点击进入站点时保持登录状态。
  - `Path: /`：整站共享。
- **`stockbot_csrf`**：
  - `HttpOnly: false`：允许前端 JavaScript 读取此 Cookie 值，并在发起非幂等请求时提取并放入请求头 `X-CSRF-Token`。
  - `Secure: true`、`SameSite: Lax`。

### 3.3 CSRF 双重提交与校验流程

```
[Browser Client]                      [API Gateway]                     [Auth / Stock API]
       │                                     │                                  │
       │ 1. 登录成功 POST /auth/login         │                                  │
       │ ──────────────────────────────────> │ 校验账号密码/生成 Session           │
       │                                     │ 写入 Redis: sess_id -> user_id   │
       │ 2. 返回 Set-Cookie                   │                                  │
       │    (stockbot_session, stockbot_csrf)│                                  │
       │ <────────────────────────────────── │                                  │
       │                                     │                                  │
       │ 3. 发起写操作 (POST/PUT/DELETE)      │                                  │
       │    Headers:                         │                                  │
       │      Cookie: stockbot_session=...   │                                  │
       │      X-CSRF-Token: c_1a2b...        │                                  │
       │ ──────────────────────────────────> │ 4. CSRF 校验：                    │
       │                                     │    - 检查 Origin / Referer       │
       │                                     │    - 比对 X-CSRF-Token           │
       │                                     │      与 Session 绑定的 CSRF Token│
       │                                     │    - 若失败：403 AUTH_CSRF_INVALID│
       │                                     │ 5. 生成短时 Principal Assertion  │
       │                                     │ ─────────────────────────────────> 6. 处理业务
```

**CSRF 豁免规则**：
- 安全幂等方法：`GET`, `HEAD`, `OPTIONS`, `TRACE` 豁免 CSRF 头部校验（但依然受 SameSite Cookie 保护）。
- 公开免登接口（如 `POST /auth/login`, `POST /auth/register`）不校验 `X-CSRF-Token`，但受 Gateway IP 速率限制与 CAPTCHA/防暴力破解机制保护。

---

## 4. Gateway 与下游 API 信任模型 (Zero-Trust Principal Assertion)

### 4.1 传统伪造漏洞与防范

在许多微服务架构中，Gateway 校验完用户身份后简单透传裸 Header（例如 `X-User-Id: 10001`、`X-User-Role: admin`）。一旦内网中存在未经过滤的请求路径、内部 SSR 服务代理转发、或请求头混淆漏洞，攻击者可直接伪造该 Header 越权访问任意用户数据。

**stock_bot 严格禁止向下游透传未签名的身份 Header。**

### 4.2 短时签名 Principal Assertion JWT

API Gateway 校验会话有效后，使用自身或 Auth Service 的**私钥（RS256 / ES256）**生成一份短时不可篡改的 **Principal Assertion Token**，放入请求头发送给下游 Stock API：

```http
GET /api/v1/user/watchlists HTTP/1.1
Host: stock-api:8000
X-Principal-Assertion: eyJhbGciOiJSUzI1NiIsImtpZCI6ImF1dGgta2V5LTIwMjYtMDEifQ...
X-Request-Id: req-6e5428a2-1bf3-4f9e-a89e-2dc9f3a9e661
```

#### Principal Assertion Payload 规范：

```json
{
  "iss": "stock-bot-auth",
  "sub": "usr_9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "aud": "urn:stock-bot:api",
  "session_id": "sess_8f7e6d5c4b3a210f",
  "username": "trader_jack",
  "roles": ["trader", "researcher"],
  "permissions": ["stocks:read", "watchlists:write", "tags:write"],
  "iat": 1773129600,
  "exp": 1773129660,
  "jti": "ast_4a3b2c1d-0e9f-8a7b-6c5d-4e3f2a1b0c9d"
}
```

- **`iss`**：固定为 `stock-bot-auth`（auth-service `JWT_ISSUER` 默认值，与 backend `AUTH_ISSUER` 默认值一致）。
- **`aud`**：固定为 `urn:stock-bot:api`（auth-service `JWT_AUDIENCE` 默认值，与 backend `AUTH_AUDIENCE` 默认值一致）。
- **`sub`**：统一用户 ID（UUIDv4 格式带 `usr_` 前缀）。
- **`exp`**：超短有效期，**默认 60 秒**（时钟容忍度 leeway: 5 秒），从根本上消除 Token 泄露长期有效的风险。
- **`jti`**：断言唯一标识，用于防重放与追踪。

### 4.3 Stock API 本地 JWKS 异步验签与缓存机制

下游 Stock API 不需要每次调用都向 Auth Service 发送 RPC 请求验证身份，而是通过 **JWKS (JSON Web Key Set)** 进行本地高速非对称验签：

1. **JWKS 初始化与更新**：
   - Stock API 启动时从 `http://auth-service:8001/.well-known/jwks.json` 加载公钥集。
   - 内存缓存 JWKS，TTL 为 24 小时。
   - 当遇到未知的 `kid`（Key ID）时，触发后台防抖刷新机制拉取最新公钥（支持平滑秘钥轮换）。
2. **FastAPI 依赖注入拦截器（`get_current_principal`）**：
   - 提取 `X-Principal-Assertion` 请求头。
   - 使用 JWKS 公钥验证签名（RS256/ES256）、`iss`、`aud`、`exp`。
   - 构建 `PrincipalContext(user_id, username, roles, permissions, session_id)` 对象并注入路由函数。
   - 剥离并丢弃所有入站的原始未签名 `X-User-*` 请求头，确保单一安全事实来源。

```python
# Stock API 拦截器伪代码示意
from fastapi import Request, Depends, HTTPException, status
from app.core.security import verify_principal_assertion, PrincipalContext

async def get_current_user(request: Request) -> PrincipalContext:
    assertion_token = request.headers.get("X-Principal-Assertion")
    if not assertion_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "缺失身份断言凭据"}
        )
    return await verify_principal_assertion(assertion_token)
```

### 4.4 Principal Assertion 契约（Gateway 实装：forward-auth sidecar）

#### 4.4.1 Claims 契约表

断言由 auth-service `KeyManager.sign_assertion`（RS256）签发，backend `AssertionVerifier` 验签。
两端默认值必须一致（已由交叉契约测试 `auth-service/tests/test_jwt_signer.py::test_assertion_cross_service_contract`
与 `backend/tests/test_auth_guard.py::test_cross_service_assertion_contract` 锁定）：

| Claim | 类型 | 必选 | 说明 |
| :--- | :--- | :---: | :--- |
| `iss` | string | 是 | 发行方，默认 `stock-bot-auth`（env: `JWT_ISSUER` / `AUTH_ISSUER`） |
| `sub` | string | 是 | 用户 ID（UUID），backend 据此做数据归属隔离 |
| `aud` | string | 是 | 受众，默认 `urn:stock-bot:api`（env: `JWT_AUDIENCE` / `AUTH_AUDIENCE`） |
| `session_id` | string | 是 | 来源会话 ID，支持服务端会话吊销联动 |
| `username` | string | 是 | 登录名（展示用） |
| `roles` | string[] | 是 | 角色列表（如 `trader` / `admin`），backend `require_roles` 消费 |
| `permissions` | string[] | 是 | 权限列表（如 `watchlists:write`），backend `require_permissions` 消费 |
| `iat` / `exp` | int | 是 | 签发时间 / 过期时间（默认 TTL 60s，leeway 5s） |
| `jti` | string | 是 | 断言唯一 ID（`ast_` 前缀），防重放与链路追踪 |

JWT Header 必须携带 `kid`（如 `auth-key-2026-01`），backend JWKS 客户端按 `kid` 选钥。

#### 4.4.2 forward-auth sidecar 流程（文字版）

forward-auth（`forward-auth/`，FastAPI + httpx，端口 9000）是 Traefik 的 forwardAuth
后端，把「不透明会话 Cookie」转换为「签名断言请求头」：

```
Browser ── Cookie: stockbot_session ──> Traefik
                                        │ ① strip-assertion（剥掉客户端伪造断言头）
                                        │ ② forwardAuth GET http://forward-auth:9000/verify
                                        │      （ Traefik v3 固定以 GET 调用，
                                        │        原始方法经 X-Forwarded-Method 传递 ）
                                        ▼
                                 forward-auth /verify
   ①解析 Cookie：无 session ──> 200 匿名放行（不加断言头，auth-service 零调用）
   ②有 session：查内存 TTL 缓存（session_id -> assertion，TTL 25s，asyncio 锁防并发穿透）
   ③缓存未命中：POST auth-service /internal/principal/assertion
        header: X-Internal-Token: ${INTERNAL_API_TOKEN}（空则不发送）
        body:   {"session_id": ...}
        非 200 / 连接失败 ──> 对已有会话 Cookie 的请求 fail closed 返回 503
   ④原始方法 ∈ {POST,PUT,PATCH,DELETE}（读 X-Forwarded-Method）：
        POST auth-service /internal/session/introspect
        body: {"session_id":..., "csrf_token": <X-CSRF-Token>}
        csrf_valid != true ──> 403 {"code":"AUTH_CSRF_FAILED",...}
   ⑤成功 ──> 200 + 响应头 X-Principal-Assertion: <JWT>
                                        │
                                        ▼ ③ Traefik authResponseHeaders 将断言头
                                        │    复制到上游请求（其余响应头丢弃）
                                  backend (Stock API)
                                   JWKS 本地验签 X-Principal-Assertion
```

配置项（env）：`AUTH_SERVICE_URL`（默认 `http://auth-service:8001`）、
`INTERNAL_API_TOKEN`（默认空 = 不发送）、`ASSERTION_CACHE_TTL`（默认 25s）。

#### 4.4.3 strip-assertion 防伪造说明

`strip-assertion` 中间件（见 `gateway/dynamic/middlewares.yml`）
置于 forward-auth **之前**，通过 `headers.customRequestHeaders` 把
`X-Principal-Assertion` 置为空串（Traefik 语义：空值 = 删除该请求头）。
若不剥离，客户端可自带伪造断言头直连网关；虽然 backend 会验签使其无法伪造身份，
剥离后可保证「断言只可能来自 forward-auth 管线」，并将无效签名攻击挡在业务服务之外。

#### 4.4.4 authResponseHeaders 与 trustForwardHeader

- **authResponseHeaders: [X-Principal-Assertion]**：Traefik 默认把 forwardAuth 的响应
  当作纯决策结果（2xx 放行 / 非 2xx 拒绝），**不会**把任何响应头透传给上游；
  显式声明 `authResponseHeaders` 后，Traefik 仅把列出的响应头复制到上游请求，其余丢弃。
- **trustForwardHeader: false（有意为之）**：Traefik v3 固定以 GET 调用 forward-auth，
  原始方法/路径经 `X-Forwarded-Method` / `X-Forwarded-Uri` 传递。若开启 trust，
  这两个头的**客户端原值**会被透传（可伪造 `X-Forwarded-Method: GET` 绕过 CSRF 强制）；
  关闭后 Traefik 用真实原始请求覆写这两个头，forward-auth 的方法判定不可伪造。

---

## 5. 统一错误响应契约 (Unified Error Contract)

无论错误发生在 API Gateway（如 401 未登录、403 CSRF 错误、429 限流）、Auth Service 还是 Stock API 业务层，系统必须返回符合 RFC 7807 精神的统一 JSON 错误结构。

### 5.1 标准错误数据结构

```json
{
  "code": "AUTH_FORBIDDEN",
  "message": "当前账户无权执行该数据回填操作",
  "details": {
    "required_permission": "tasks:trigger",
    "actual_permissions": ["stocks:read", "watchlists:write"]
  },
  "trace_id": "req-6e5428a2-1bf3-4f9e-a89e-2dc9f3a9e661"
}
```

### 5.2 字段说明

| 字段 | 类型 | 是否必选 | 说明 |
| :--- | :--- | :--- | :--- |
| **`code`** | `string` | 是 | 机器可读的大写蛇形业务错误码（枚举定义） |
| **`message`** | `string` | 是 | 人类可读的用户友好提示文案（中文） |
| **`details`** | `object \| array` | 否 | 结构化错误明细（如表单字段校验失败列表、缺失权限等） |
| **`trace_id`** | `string` | 是 | 全局请求链路追踪标识符（从 Gateway 到后端贯穿） |

### 5.3 标准错误码分类矩阵

| HTTP 状态码 | 业务错误码 (`code`) | 触发场景说明 |
| :--- | :--- | :--- |
| **400 Bad Request** | `INVALID_ARGUMENT` | 请求参数格式错误、类型不符、Query/Body 语法非法 |
| **401 Unauthorized** | `AUTH_UNAUTHORIZED` | 未携带有效 Session Cookie 或会话已失效 |
| **401 Unauthorized** | `AUTH_SESSION_EXPIRED` | 会话已过期，前端应引导刷新或重新登录 |
| **401 Unauthorized** | `AUTH_INVALID_CREDENTIALS` | 用户名或密码错误、账户已被锁定 |
| **403 Forbidden** | `AUTH_FORBIDDEN` | 用户已登录但权限不足（如非 Admin 用户触发 Task 回填） |
| **403 Forbidden** | `AUTH_CSRF_INVALID` | CSRF Token 缺失、格式错误或与会话不匹配 |
| **404 Not Found** | `RESOURCE_NOT_FOUND` | 请求的股票代码、行业分类、自选列表不存在 |
| **409 Conflict** | `RESOURCE_ALREADY_EXISTS` | 注册用户名冲突、自选股已存在、标签重复绑定 |
| **422 Unprocessable** | `VALIDATION_ERROR` | Pydantic Schema 字段业务校验失败（返回字段级 details） |
| **429 Too Many Req** | `RATE_LIMITED` | 触发 IP 或用户级别请求频率限流阈值 |
| **500 Internal Error**| `INTERNAL_ERROR` | 后端服务未捕获异常，details 中不暴露内部堆栈 |
| **502/503/504 Bad GW**| `UPSTREAM_UNAVAILABLE` | Gateway 连接下游 auth-service 或 stock-api 超时或宕机 |

---

## 6. 路由保护矩阵与访问控制分类

全系统端点划分为三大安全级别：
1. **Public（公开端点）**：无需登录，任何访客均可访问；
2. **Protected（受保护用户端点）**：必须携带有效 Session，普通登录用户可访问自己的数据；
3. **Admin（管理员端点）**：必须具备 `admin` 角色或对应操作权限。

### 6.1 详细路由矩阵表

| 路由路径 | HTTP 方法 | 安全级别 | 归属服务 | 所需权限 / 角色 | CSRF 校验 | 业务功能说明 |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`/api/v1/health`** | GET | Public | Stock API | 无 | 否 | 服务存活健康检查 |
| **`/auth/jwks.json`** | GET | Public | Auth Service | 无 | 否 | 公钥集分发（供 Gateway/API 验签） |
| **`/auth/login`** | POST | Public | Auth Service | 无 | 否 | 用户名密码登录，签发会话 Cookie |
| **`/auth/register`** | POST | Public | Auth Service | 无 | 否 | 新用户注册 |
| **`/auth/logout`** | POST | Protected | Auth Service | 已登录 | **是** | 销毁当前会话，清除 Cookie |
| **`/auth/me`** | GET | Protected | Auth Service | 已登录 | 否 | 获取当前登录用户画像与权限清单 |
| **`/auth/sessions`** | GET | Protected | Auth Service | 已登录 | 否 | 查看当前用户活跃设备与会话列表 |
| **`/auth/sessions/{id}`**| DELETE | Protected | Auth Service | 已登录 | **是** | 强制踢出指定活跃会话 |
| **`/auth/admin/users`** | GET/POST | Admin | Auth Service | `admin` / `users:manage` | **是**(POST) | 用户管理、创建账户、重置密码 |
| **`/auth/admin/roles`** | GET/PUT | Admin | Auth Service | `admin` / `roles:manage` | **是**(PUT) | 角色与权限策略配置 |
| **`/auth/admin/audit`** | GET | Admin | Auth Service | `admin` / `audit:read` | 否 | 安全审计日志检索 |
| **`/api/v1/exchanges`** | GET | Public | Stock API | 无 | 否 | 交易所列表 |
| **`/api/v1/stocks/**`** | GET | Public | Stock API | 无 | 否 | 股票列表、股票详情、K线行情、估值指标 |
| **`/api/v1/market/**`** | GET | Public | Stock API | 无 | 否 | 申万行业树、行业行情、板块热力图 |
| **`/api/v1/industries/**`| GET | Public | Stock API | 无 | 否 | 行业投研工作台（猪周期/指标图谱/知识库）|
| **`/api/v1/user/watchlists`** | GET | Protected | Stock API | 已登录 (`sub` 隔离) | 否 | 查询当前用户的自选股列表 |
| **`/api/v1/user/watchlists`** | POST/DELETE | Protected | Stock API | 已登录 (`sub` 隔离) | **是** | 添加 / 移除当前用户的自选股 |
| **`/api/v1/stocks/{symbol}/user-tags`** | GET | Protected | Stock API | 已登录 (`sub` 隔离) | 否 | 获取该股票下当前用户打的自定义分类标签 |
| **`/api/v1/stocks/{symbol}/user-tags`** | PUT/POST/DELETE | Protected | Stock API | 已登录 (`sub` 隔离) | **是** | 更新/删除当前用户为个股自定义的标签 |
| **`/api/v1/tasks/{task_id}`** | GET | Protected | Stock API | 已登录 | 否 | 查询异步任务进度与状态 |
| **`/api/v1/tasks/fetch-*`** | POST | Admin | Stock API | `admin` / `tasks:trigger` | **是** | 手动触发全量股票池抓取 / 行情回补任务 |
| **`/api/v1/tasks/run-clustering`** | POST | Admin | Stock API | `admin` / `tasks:trigger` | **是** | 触发行业特征聚类算法任务 |

---

## 7. 前端 BFF 适配与状态同步

1. **全局 Auth Store（Zustand）**：
   - 存储：`user: UserProfile | null`, `isAuthenticated: boolean`, `permissions: string[]`。
   - 启动生命周期：应用加载时发起 `GET /auth/me`。若返回 200 则初始化用户信息；若返回 401 则标记未登录状态（不阻塞公开页面浏览）。
2. **Axios / Fetch 拦截器规范**：
   - 自动附带 `withCredentials: true`，确保浏览器自动带上 `stockbot_session` 与 `stockbot_csrf`。
   - 对非 GET 请求，自动从 `stockbot_csrf` Cookie 中读取值并填充到 Header `X-CSRF-Token`。
   - 统一捕获 401 响应：清除本地 Auth Store 状态，若当前处于受保护路由（如 `/watchlist`），重定向至 `/login?redirect=...` 并保留返回路径。
   - 统一提取 `trace_id`：在全局 Notification 提示中包含 `trace_id` 便于排查。

---

## 8. 演进路线与阶段划分 (Roadmap)

- **Stage 0 (当前)**：架构设计、安全拓扑与数据模型规格定义。
- **Stage 1**：创建独立微服务 `auth-service`（FastAPI + Argon2id + Redis Session + JWKS）。
- **Stage 2**：在 API Gateway / BFF 侧实现 Cookie 会话解析、CSRF 防护、短时 Principal Assertion JWT 签名。
- **Stage 3**：Stock API 接入 JWKS 验签依赖、重构统一错误处理中间件、统一注入 `trace_id`。
- **Stage 4**：业务模型数据归属改造（Watchlists 服务端持久化、`stock_custom_sw_tags` 增加 `user_id`、Tasks 增加 `requested_by`、Redis 键隔离）。
- **Stage 5**：前端 React 登录/注册/用户中心页面与路由守卫落地、自选股服务端同步改造。
- **Stage 6**：全链路端到端集成测试、安全红蓝对抗用例与容器编排上线。
