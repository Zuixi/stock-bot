---
status:      completed
scope:       身份治理从"改 SQL"变成"管理员控制台"，并把注册模式由开放自助注册改为邀请制（管理员预开户 + 服务端生成临时密码 + 强制首登改密）；邀请码/邮件/SSO/多租户**预留设计不启用**
touches:     auth-service/app/, auth-service/migrations/versions/, auth-service/tests/, forward-auth/app/, forward-auth/tests/, frontend/src/features/admin/, frontend/src/pages/admin-users/, frontend/src/pages/change-password/, frontend/src/pages/login/, frontend/src/pages/landing/sections/, frontend/src/features/auth/, frontend/src/shared/api/, frontend/src/app/router/, frontend/src/app/layouts/, frontend/e2e/, docs/decisions/, docs/architecture/, docs/deployment/, docs/testing/frontend-e2e.md, docs/features.md, docs/Changelog.md, plans/index.md
updated:     2026-10-14
next-action: -
---

# 邀请制开户与管理员控制台 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking。

**Goal:** 让"谁能进这个系统、进来是什么角色"由**管理员在控制台里**决定，而不是由运维改 SQL；同时把身份入口从"任何人自助注册即 `active`"收紧为"管理员预开户 + 临时密码 + 强制改密"。

**Why now（2026-10-14 逐文件核实，非推断）：**

| 现状事实 | 证据 | 后果 |
|---|---|---|
| `users:manage` / `roles:manage` / `audit:read` 三个权限**已定义且已授予 admin**，但**零端点** | [0001 迁移:406-425](auth-service/migrations/versions/0001_initial_auth_schema.py#L406)；`app/api/` 只有 `auth.py`/`internal.py`/`jwks.py`/`health.py` | 权限是死代码；授角色只能改 SQL |
| `RoleOut` / `PermissionOut` / `UserRoleUpdateRequest` / `AuditEventOut` 四个 Schema **已写好但无人 import** | [schemas/auth.py:95-140](auth-service/app/schemas/auth.py#L95)；`grep` 全仓无引用 | 契约先于实现，说明这是既定待补能力 |
| 端点契约**已写进架构文档** | [authentication-and-gateway.md:420](docs/architecture/authentication-and-gateway.md#L420) `/auth/admin/users` · `/auth/admin/roles` · `/auth/admin/audit` | 本轮是"补齐已声明的契约"，不是发明新面 |
| `POST /auth/register` 对外全开，建号即 `status="active"`，默认角色**硬编码** `trader` | [auth_service.py:117](auth-service/app/services/auth_service.py#L117)、`:85` | 任何人可注册并立即登录 |
| **没有改密端点**（全集仅 login/logout/session/me/csrf/sessions） | 通读 `app/api/auth.py` | 邀请制下管理员交付的初始密码**永久有效**，管理员永久知道每个人的密码 |
| auth-service **没有自己的权限依赖**，只从 Redis 读会话 | `grep require_permission` 在 auth-service 无命中 | `/auth/*` 在 Traefik 上**不过 forward-auth**（[docker-compose.yml:193](docker-compose.yml#L193)），admin 端点必须自判权限 + CSRF |
| 首个 admin **无任何引导方式** | 迁移 0001 无 admin 种子；`grep bootstrap` 无命中 | 这正是"只能改 SQL"的根因 |
| 授权归因与会话批量吊销**已具备** | `auth_user_roles.assigned_by`（[rbac.py:120](auth-service/app/models/rbac.py#L120)）；`SessionService.revoke_all_user_sessions`（[session_service.py:196](auth-service/app/services/session_service.py#L196)） | 不需要新表即可做审计归因与即时失效 |
| 着陆页主叙事建立在"自助注册"上，并被 e2e 断言锁定 | [AccountPerks.tsx:4-29](frontend/src/pages/landing/sections/AccountPerks.tsx#L4)「免注册 / 注册用户」、「注册只增加个性化能力」；[BottomCTA.tsx:8](frontend/src/pages/landing/sections/BottomCTA.tsx#L8)「现在注册…」；[public-homepage.spec.ts:314-333](frontend/e2e/public-homepage.spec.ts#L314) 断言该叙事 | 关闭注册**必须同步改造文案与 e2e**，否则页面在撒谎 |
| 现有回归**拿注册当建号 fixture**（`test_api_auth.py` 5 处、`test_api_internal.py` 1 处） | `grep "auth/register" auth-service/tests/` | 关注册前必须先落直插 DB 的建号 helper |
| 登录**先判状态后验密码** | [auth_service.py:222](auth-service/app/services/auth_service.py#L222) 在 `:240` 验密码之前 | 任何知道用户名的人都可探测"该账号被停用/待验证"→ 账号枚举 |
| 会话变更后**不失效**；断言缓存 TTL **25s** | [forward-auth/app/config.py:17](forward-auth/app/config.py#L17) `assertion_cache_ttl: int = 25` | 停用/改角色后旧会话仍可用 24h，除非显式吊销 |
| [ADR 0008](docs/decisions/0008-research-read-requires-permission.md) 把此事记为已知遗留 | 正文「已知遗留」段 | 本计划直接关闭该遗留 |

**Architecture:** 六条不变式，全部落在 **auth-service**（身份的唯一 owner；backend 禁写 `auth_users`）：

1. **开户即授权**：邀请制下不存在"注册后待审"异步流程——管理员创建账号的那一刻就是审批。`roles` 在开户请求里**必填非空**。
2. **状态机是纯函数**：`active` 是唯一的可登录态；`locked` 仍由登录失败计数自动维护；`pending_verification` 为既有死枚举，保留不启用。登录拒绝码由一张表集中产出，不再散落 `if status != "active"`。
3. **先验密码，再判状态**：密码错一律 `401 AUTH_INVALID_CREDENTIALS`；密码对才暴露 `suspended` / 临时密码过期等具体原因。未知用户走一次假 hash 抹平时序 → 消除账号枚举。
4. **权限实时读 DB，不用会话快照**：admin 端点鉴权时从库里现查角色/权限，被撤权后**下一个请求**即 403；同时任何改变权限的写操作都 `revoke_all_user_sessions`。`/api/*` 的传播窗口 = forward-auth 断言缓存 **≤25s**，`/auth/*` 为 0s。
5. **临时密码必须被强制换掉**：`must_change_password` 走完 redis 会话 → `/auth/*` 白名单 → 断言签发拒绝 → forward-auth 403 透传 → 前端全局跳转五段，否则"邀请制"只是把弱密码换成管理员知道的密码。
6. **不做硬删除**：只有 `suspend` / `reactivate`。`auth_users` 硬删会 CASCADE 掉凭据会话、把 `auth_audit_events.user_id` 置空、并影响业务库 `watchlists` / `requested_by` 的归属——等于销毁历史归因。

**Tech Stack:** 不新增任何运行时依赖。后端 FastAPI + SQLAlchemy 2.0 async + Alembic + Argon2id（既有）；测试 `uv run pytest`（SQLite in-memory + FakeRedis，见 [conftest.py](auth-service/tests/conftest.py)）；前端 React 18 + AntD 5 + TanStack Query（既有）+ Playwright。

**与既有计划的关系:** 关闭 [ADR 0008](docs/decisions/0008-research-read-requires-permission.md) 的「已知遗留」（无授角色接口 → 新增投研用户需改库）。ADR 正文冻结不可改，改由新 ADR 0015 承接并双向引用。与 `2026-10-12-investment-research-platform-ia.md`（投研 IA 主线）无耦合——本计划只动身份层与 landing 表达层，不新增业务页面。

---

## 0. owner 已裁定（6 项，无阻塞，不再回问）

| # | 裁定 | 结论 |
|---|---|---|
| A | 注册模式 | **邀请制 / 管理员预开户**；`POST /auth/register` 关闭（`403 AUTH_REGISTRATION_DISABLED`），同步改造着陆页 |
| B | 角色指定时机 | **开户时必须指定角色**（`roles` 必填非空），与建号同事务 |
| C | `/auth/admin/roles` 能力边界 | **只读**（GET 角色→权限矩阵）；角色-权限映射仍由代码 + 迁移定义，不做在线编辑 |
| D | 删除语义 | **不硬删**，只 `suspend` / `reactivate` |
| E | 注册闸门排期 | **本轮就关闭注册 + 改造着陆页**（含 e2e 文案断言） |
| F | 初始密码交接 | **服务端生成临时密码 + 强制首登改密**（不采用一次性激活码） |

---

## 1. 数据契约（Phase 1）

**迁移 `auth-service/migrations/versions/0002_invite_only_provisioning.py`：只加 3 列，0 新表，0 约束变更。**

| 表 | 列 | 类型 | 说明 |
|---|---|---|---|
| `auth_users` | `status_reason` | `VARCHAR(255) NULL` | 停用原因；登录被拒时展示给本人 |
| `auth_credentials` | `must_change_password` | `BOOLEAN NOT NULL DEFAULT false` | 临时密码未更换 |
| `auth_credentials` | `temp_password_expires_at` | `TIMESTAMPTZ NULL` | 临时密码失效时刻（默认 +72h） |

**为什么没有 `created_by`（ponytail 评审 #3）：** 开户归因已有唯一真相——`auth_audit_events` 里 `auth.user.provisioned` 的 actor（管理员操作本身就带 user_id/IP/trace_id），控制台详情抽屉展示审计即得。加列是同一事实第二份存储，且手工修库时会漂移。**不加。**

**为什么不改 `chk_auth_user_status`：** 邀请制下不需要新增状态值——账号由管理员直接建成 `active`，`locked` 沿用自动锁定，`suspended` 沿用既有，`pending_verification` 保留为死枚举（删它反而要重建约束，白担风险）。因此迁移是**纯加列**，可安全回滚。

**为什么 `temp_password_expires_at` 是独立列而不是 `password_updated_at + 72h` 推算：** 登录成功路径里的 argon2 rehash 会重置 `password_updated_at`（[auth_service.py:264-266](auth-service/app/services/auth_service.py#L264)），推算会把过期时刻悄悄续期。独立列不受 rehash 干扰。

**状态机 `auth-service/app/core/user_status.py`（新增，纯函数无 IO）——只做一件事：**

```
LOGINABLE = {"active"}                    # 唯一可登录态
LEGACY_UNUSED = {"pending_verification"}  # 保留枚举，不产生、不接受
```

- `login_denial(user, now) -> (http_status, code, message) | None`：集中产出拒绝三元组（`suspended` / `locked` / 临时密码过期 / `must_change_password` 由五段门控另行处理）
- **不做 `can_transition` 迁移表**（ponytail 评审 #2）：admin 侧只有 `active⇄suspended` 两条边且各有独立端点，端点内一行 `if user.status != "active": 409` 即堵死非法跳转。为 2 条边建迁移表是给不存在的自由度修栅栏。

**验收判据**
- 数据契约：迁移文件中**无 `create_table`**；`upgrade()` 后 3 列存在、`downgrade()` 后 3 列消失；`alembic upgrade head` 与 `downgrade -1` 均可跑
- 行为：`test_user_status.py` 覆盖 状态×临时密码过期 的**拒绝码矩阵**全组合；对非 `active` 用户 suspend → 409
- 门禁：`bash scripts/self_review.sh` 绿

**同时（Phase 1 内，必须先做，否则后面改不动）：** 在 `auth-service/tests/conftest.py` 增加 `create_user(db_session, *, username, status="active", roles=("trader",), password="...")` 直插 DB 的 helper，并替换 `test_api_auth.py`（5 处）与 `test_api_internal.py`（1 处）里把 `POST /auth/register` 当 fixture 的用法。

**存量用户处置（部署时必答）：** 所有现存自注册用户**一律 grandfather 为 `active`，不迁移、不降级**。迁移 0002 不触碰任何既有行。

---

## 2. 临时密码与强制改密（Phase 2）

**端点契约 `POST /auth/change-password`（新增，已登录 + CSRF）**
- 请求 `{current_password, new_password}`；校验当前密码 → 重新 Argon2id → 清 `must_change_password` 与 `temp_password_expires_at` → **吊销其余会话**（保留当前会话）→ 审计 `auth.user.password.changed`
- `new_password` 走与注册同口径的强度约束（8–128）

**五段强制门控（缺一段则形同虚设）**
1. 登录成功时把 `must_change_password` 写入 Redis 会话 hash（`SessionService.create_session` 增参）
2. auth-service 会话级依赖对白名单以外的路由返回 `403 AUTH_PASSWORD_CHANGE_REQUIRED`；白名单 = `/auth/change-password`、`/auth/logout`、`/auth/csrf`、`/auth/session`、`/auth/me`
3. `POST /internal/principal/assertion` 在标记为真时返回 `403 AUTH_PASSWORD_CHANGE_REQUIRED`（**不改断言 claims 契约**——该契约由跨服务契约测试锁定）
4. forward-auth：`_fetch_assertion` 增加 `403` 分支 → 新异常 `PasswordChangeRequiredError` → 以 `403` 透传（其余非 200 仍 fail-closed 503）
5. 前端：`auth.ts` 的全局响应处理新增 `AUTH_PASSWORD_CHANGE_REQUIRED` → `navigate("/change-password")`

`UserOut` / 前端 `UserProfile` 增加 `must_change_password: bool`，`/auth/me` 据此渲染强制改密页。

**配置新增**（`app/config.py`）：`temp_password_ttl_hours: int = 72`

**验收判据**
- 行为：开户 → 用临时密码登录 → 打 `/api/v1/market/*` 得 **403 且 code 正确（不是 503）** → 调 `/auth/change-password` 后同一会话可正常打 `/api/*`
- 行为：伪造 `temp_password_expires_at` 为过去 → 登录得 `AUTH_TEMP_PASSWORD_EXPIRED`
- 门禁：`forward-auth/tests/test_forward_auth.py` 新增"上游 403 原样透传 / 上游 500 仍 503"两条

---

## 3. admin API（Phase 3）

**新增 `app/api/deps.py`**（镜像 [backend/app/api/deps.py:57](backend/app/api/deps.py#L57) 工厂写法）
- `get_session_user`：Cookie → Redis 会话 → **DB 现查** `AuthUser`，**`status != "active"` → 401**（实时状态必须消费，吊销会话失败时它是兜底）
- `require_permission(*perms)`：从 **DB** 解析角色→权限（不用会话快照）→ 403 `AUTH_FORBIDDEN`
- `verify_session_csrf`：把 `logout` 里的会话绑定 CSRF 校验抽出来复用

**端点契约**（`app/api/admin.py`；每个写端点都挂 `verify_session_csrf`）

| 方法 | 路径 | 权限 | 要点 |
|---|---|---|---|
| GET | `/auth/admin/users` | `users:manage` | 状态筛选 + 关键字（username/email）+ 分页（复用 `PageParams`/`PagedResponse` 口径）；**badge/概览用 `?status=&page_size=1` 读 `total`，不设独立 summary 端点**（ponytail #1） |
| GET | `/auth/admin/users/{id}` | `users:manage` | 画像 + 角色；审计轨迹走 `/auth/admin/audit?user_id=`，**不做联查**（ponytail #4） |
| POST | `/auth/admin/users` | `users:manage` | **开户**：`{username,email,display_name?,roles(必填非空)}` → 服务端生成临时密码，**仅在响应体返回一次** + `temp_password_expires_at`；**未知 role_id → 404**（ponytail #9，信任边界校验） |
| POST | `/auth/admin/users/{id}/suspend` | `users:manage` | `{reason?}` → `status_reason`；前置 `status == "active"` 否则 409；**吊销全部会话** |
| POST | `/auth/admin/users/{id}/reactivate` | `users:manage` | 前置 `status == "suspended"` 否则 409；清 `status_reason` |
| PUT | `/auth/admin/users/{id}/roles` | `roles:manage` | **替换**角色集；写 `assigned_by`；**吊销全部会话**；审计 before/after |
| POST | `/auth/admin/users/{id}/reset-password` | `users:manage` | 重新签发临时密码（同样只返一次）+ **吊销全部会话** |
| GET | `/auth/admin/roles` | `users:manage` | **只读**角色→权限矩阵（裁定 C）；**挂 `users:manage` 而非 `roles:manage`**：开户弹窗的角色下拉靠它，只持 `users:manage` 的管理员也必须能开户（ponytail #11） |
| GET | `/auth/admin/audit` | `audit:read` | user_id / event_type / 时间范围 + 分页 |

**硬保护（`app/services/admin_service.py`）**
1. `_assert_not_self`：管理员不能对自己 `suspend` / 改角色
2. `_assert_active_status`：suspend/reactivate 的前置状态校验（替代迁移表，ponytail #2）

**单一开户代码路径（ponytail #6）：** CLI 与 `POST /auth/admin/users` 都调 `admin_service.provision_user()`，CLI 只是薄壳。**两套开户逻辑 = 修 bug 修一半的保证。**

**查重逻辑搬家（ponytail #8）：** `register_user` 删除前，先把 username/email 查重（[auth_service.py:88-106](auth-service/app/services/auth_service.py#L88)）抽成 `_ensure_unique_identity(db, username, email)` 供开户复用；删 `UserRegisterRequest` 时同步清 `schemas/__init__.py` 的 export。

**审计事件**：`auth.user.provisioned` / `.suspended` / `.reactivated` / `auth.user.roles.changed` / `auth.user.password.reset` —— `payload` 只放 before/after 与原因，**绝不放密码/哈希**。

**验收判据**
- 行为：无 `users:manage` 的会话打任一 admin 端点 → 403；开户后该账号**立即**能登录且角色正确；`suspend` 后该用户旧会话在 `/auth/*` **立即**失效、在 `/api/*` **≤25s** 失效；对自己 suspend → 409；对非 active 用户 suspend → 409；未知 role_id 开户 → 404
- 门禁（**本轮最有价值的机械化守卫**）：`test_api_admin.py` 用参数化遍历 admin 路由表，断言 ①每条路由在无权限会话下 403 ②每条写路由在缺 `X-CSRF-Token` 时 403 —— 任何人新增 admin 端点忘了挂守卫，测试立刻红

---

## 4. 首个 admin 引导（Phase 4）

`auth-service/app/cli.py`（argparse，无新依赖）：

```
python -m app.cli bootstrap-admin --username <name> --email <mail>
#  密码来源：ADMIN_BOOTSTRAP_PASSWORD 环境变量，或交互式 getpass（不接受命令行明文）
#  幂等：用户已存在 → 只确保其拥有 admin 角色，绝不重置密码，打印明确提示后 exit 0
#  用户不存在 → 调 admin_service.provision_user() 建为 active、挂 admin 角色、置 must_change_password=true（与 admin API 同一代码路径）
```

部署侧：`docker compose run --rm auth-service python -m app.cli bootstrap-admin ...`，步骤写入 `docs/deployment/`。

**验收判据**
- 行为：全新空库跑一次 → 该账号能登录且 `permissions` 含 `users:manage`；二次运行不改密码、改不了 `password_updated_at`
- 门禁：`test_cli.py` 覆盖「空库建号 / 已存在只补角色 / 已存在且已是 admin 时零变更」三态

---

## 5. 关闭自助注册 + 前端（Phase 5，最后一刀）

**顺序（ponytail #5）：关注册必须在替代品就位之后。** Phase 1–4 落地期间系统随时可用；本 Phase 把 `POST /auth/register` 关掉并同批改前端与着陆页，一个 PR 合并。

**后端**
- `POST /auth/register` → 恒定 `403 AUTH_REGISTRATION_DISABLED`（`"本系统为邀请制，请联系管理员开户"`）；CSRF double-submit 依赖保留
- 删除 `AuthService.register_user` 与 `UserRegisterRequest`（§3 查重搬家已完成），同步清 `schemas/__init__.py` export
- `authenticate_user` 顺序改为：查用户 → 锁定短路（时效内）→ **验密码** → 判状态
  - 未知用户：对一次性 dummy hash 跑一次 `verify_and_update_password` 再返 401（抹平时序）
  - 密码错：`401 AUTH_INVALID_CREDENTIALS`（**不区分账号是否存在/状态**）
  - 密码对 + `suspended`：`403 AUTH_ACCOUNT_SUSPENDED` + `status_reason`；临时密码过期：`403 AUTH_TEMP_PASSWORD_EXPIRED`
- 审计事件：`auth.login.suspended` / `auth.login.temp_password_expired`（`DENIED`），**密码永不进 `payload`**

**前端（先 UX 设计后实现，见 §5.1）**
- `shared/api/admin.ts`（typed client，复用 `apiGet/apiPost/apiPut`）
- `features/admin/`：`UserTable`（服务端分页/筛选）、`UserDetailDrawer`、`ProvisionUserModal`（角色必选）、`RoleAssignModal`、`InitialPasswordModal`（**一次性展示 + 复制按钮 + 明确的"仅显示一次"警告**）、`StatusTag`
- `pages/admin-users/index.tsx` → 路由 `/admin/users`，包 `<RequireAuth permissions={["users:manage"]}>`
- `pages/change-password/index.tsx` → 路由 `/change-password`（已登录即可达）

**修改**
- `app/layouts/MainLayout.tsx`：导航项「用户管理」按 `hasPermission("users:manage")` 显示
- `pages/login/index.tsx`：**删除注册 Tab**；处理 `AUTH_REGISTRATION_DISABLED` / `AUTH_ACCOUNT_SUSPENDED`（带 `status_reason`）/ `AUTH_TEMP_PASSWORD_EXPIRED` 三种文案
- `shared/api/auth.ts`：删 `register`；加 `changePassword`；`UserProfile` 加 `must_change_password`
- `features/auth/context.tsx`：全局监听 `AUTH_PASSWORD_CHANGE_REQUIRED` → 跳 `/change-password`（与既有 `auth:unauthorized` 监听并列）
- **着陆页改造**（裁定 E）：`AccountPerks.tsx` 三档横条从「游客 / 注册用户 / 建设中」改为「公开行情 / 受邀账号 / 建设中」，删「注册只增加个性化能力」；`BottomCTA.tsx` H2 去掉"现在注册"；`CtaButton.tsx` + `useLandingCta.ts` 未登录 CTA 由「免费开始」改为「登录」

**验收判据**
- 行为：注册 → 403 且 `code == "AUTH_REGISTRATION_DISABLED"`；`suspended` 用户登录 → 403 且仅当密码正确时才出现（密码错仍 401）；e2e `admin-users.spec.ts` 跑通完整链路「admin 开户 → 拿到临时密码 → 新用户登录被强制改密 → 改密后进工作台 → admin 改其角色 → 新角色生效」
- 行为：`/admin/users` 对无 `users:manage` 的账号出 403 页
- 门禁：`npx tsc --noEmit` 绿；`e2e/auth.spec.ts` 注册 Tab 用例重写为「断言不存在注册入口」；`e2e/public-homepage.spec.ts` 文案断言同步为邀请制口径

---

## 5.1 前端 UX 设计（frontend-design，实现前独立完成）

**流程约束（owner 指定）：** 先用 frontend-design skill 产出独立 UX 设计（管理台列表/详情/开户弹窗/初始密码一次性展示/强制改密页 + 登录页与着陆页的邀请制文案改造），**基于 UX 设计再开写组件**。

**设计输入**
- 既有设计系统：`theme.css` + `theme.ts`（AntD 5 token）、`MainLayout` 三层布局
- 信息密度参照：管理台是**内部工具**，优先表格密度与扫读效率，不做营销化卡片
- 文案原则：邀请制口径（"由管理员开户"/"临时密码仅显示一次"），错误文案可行动（登录被拒时告诉用户找谁）

**验收判据**：UX 设计稿（token + 布局 + 文案表）先于组件代码落盘；实现与设计稿的偏差在提交说明里逐条交代。

---

## 6. 容器 rebuild + Playwright 实测（Phase 6，owner 指定的最终验收）

1. `docker compose build auth-service forward-auth frontend`（迁移随 `migrate-auth` 容器自动跑）
2. `docker compose up -d`，`docker compose ps` 确认全 healthy
3. e2e 前置：`docker compose run --rm auth-service python -m app.cli bootstrap-admin ...` 建 admin，凭据经环境变量注入（不落盘）
4. `E2E_BASE_URL=http://localhost npx playwright test`（走 **gateway:80**，不是 Vite dev server——直连没有身份上下文）
5. 服务管理遵守 AGENTS.md：**重启必先清旧**、**收尾不留孤儿**；会话结束时容器状态与启动前一致或明确移交

**验收判据**：Playwright 全绿（含新增 `admin-users.spec.ts` 与改造后的 `auth.spec.ts`/`public-homepage.spec.ts`）。

---

## 7. 文档收口与门禁（Phase 7）

| 文档 | 改什么 |
|---|---|
| `docs/decisions/0015-invite-only-provisioning-and-admin-console.md`（新） | 六项裁定的为什么；引用 ADR 0008 声明其「已知遗留」关闭；写明"不硬删除"与"角色矩阵只读"的拒绝理由 |
| `docs/decisions/index.md` | 登记 0010（`doc_gate` 校验） |
| `docs/architecture/authentication-and-gateway.md` | §3 补邀请制开户与强制改密链路；§6.1 路由矩阵补 9 条 admin 路由、`/auth/register` 标为 Disabled、`/auth/admin/roles` 由 GET/PUT 改为 **GET** |
| `docs/architecture/auth-data-model.md` | DDL 补 3 列；状态注释标 `pending_verification` 为死枚举 |
| `docs/features.md` | 第 35 行「注册 / 登录 / 会话」改写；新增「用户管理（开户 · 角色 · 停用 · 审计）」行 |
| `docs/deployment/index.md` | 首次上线 `bootstrap-admin` 步骤；`/auth/register` 已关闭的说明 |
| `docs/testing/frontend-e2e.md` | 删除 `INSERT INTO auth_user_roles ...` 供号段落，改为经 admin API/控制台供号 |
| `docs/Changelog.md` | 一句话补记 |
| `plans/index.md` | 登记本计划 |

**验收判据（门禁类）**：`bash scripts/self_review.sh --full` + `bash scripts/doc_gate.sh` + `bash scripts/slop_scan.sh` 全绿。

---

## 8. 风险与取舍

| 风险 | 影响 | 缓解 |
|---|---|---|
| 关闭注册是**破坏性产品变更** | 任何外部自动化/收藏的注册链路失效 | 无外部消费方（全仓只有自家前端与测试）；**关注册被刻意排在 Phase 5 最后一刀**（ponytail #5）：Phase 1–4 期间建号能力已就位，不存在无法建号的窗口 |
| 在线撤销有**时间窗** | `/api/*` 最长 25s 仍可用（forward-auth 断言缓存） | 明确接受并写入 `docs/architecture/`；不为此给每个 `/api` 请求加 DB 状态查询 |
| 临时密码在响应体返回一次 | 可能被打进访问日志/APM | 不进审计 `payload`；`InitialPasswordModal` 只展示一次；`docs/deployment/` 提示 disable request-body logging |
| 强制改密门控**五段任一漏掉即失效** | 用户可绕过改密直接用 `/api/*` | Phase 2 的五段各配一条测试，其中 forward-auth 的 403 透传是硬断言 |
| 强行关闭注册会打断 **6 处测试 fixture** | 关注册（Phase 5）一动就大片红 | 建号 helper 前置于 Phase 1，与迁移同批落地 |
| `bootstrap-admin` 是一次性单向门 | 若实现成"已存在也重置密码"= 静默后门 | 幂等语义写进 CLI 帮助文案 + 三态测试锁定 |
| 着陆页改造牵动被锁定的 e2e 文案断言 | 可能连带红 3 个 spec | 先改断言再改文案（先红后绿），避免"改完正好绿"掩盖漏改 |

## 9. 非目标与预留设计

### 9.1 本轮不做、也不预留（明确拒绝）

角色-权限在线编辑（裁定 C）、用户硬删除（裁定 D）、审计日志导出与 SIEM 对接、细粒度字段级权限、`is_superuser` 的设置入口（保持 break-glass，仅由 DB/CLI 兜底）。

### 9.2 本轮不做、但**先设计后启用**的扩展面（owner 裁定：业务逻辑需要先设计好，可以不启用）

以下四项是身份体系的**既定演进方向**。本轮交付的表结构与端点契约必须不与它们冲突；启用时只加表/加端点，**不破坏既有契约**。

| 扩展 | 预留的设计约束（本轮就遵守） | 启用时的增量 |
|---|---|---|
| **邀请码/邀请链接** | `POST /auth/admin/users` 的响应体已含一次性凭据（初始密码）；将来邀请码 = 把"管理员当面交付密码"换成"带过期与次数限制的一次性 token 自助激活"，**开户端点不动**，只加 `auth_invitations` 表 + 公开激活端点 | `auth_invitations(code_hash, roles_json, expires_at, max_uses, uses, created_by)` + `POST /auth/activate`；届时 `pending_activation` 状态才真正启用 |
| **邮件通知与验证** | 所有"待通知"事件（开户/停用/改密）都已落 `auth_audit_events`，通知只是事件的**下游消费者**；`status_reason` 已可承载展示给本人的文案 | 邮件模板 + 发送 worker + `pending_verification` 状态启用；不新建事件源 |
| **SSO/OIDC** | `auth_credentials.auth_type` 已是多值列（`password`/`totp`/`oauth_github`），唯一约束是 `(user_id, auth_type)`；本地密码与 OIDC 可并存于同一用户 | 加 `auth_type="oidc_<provider>"` 的 credential 行 + provider 配置；`/auth/login` 不动，加 `/auth/oidc/<provider>/*` |
| **组织/租户与数据隔离** | 本轮所有 admin 端点与权限判断**不含任何租户概念**（刻意）；业务侧数据归属已按 `user_id` 隔离（watchlists/tags），租户化时 `user_id → (org_id, user_id)` 是加列不是重构 | `auth_organizations` + `auth_users.org_id` + 权限判断加 org 维度；**这是四项中破坏性最大的**，启用前须单独 ADR |

**验收判据**：本轮落地的迁移与端点不出现与上表冲突的命名/契约（例如：不占用 `auth_invitations` 表名、不把 `auth_type` 写死为只能 `password`、不把 `username` 唯一约束与租户绑死）。
