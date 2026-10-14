# 0010 邀请制开户与管理员控制台（关闭自助注册）

```
status:        accepted
date:          2026-10-14
supersedes:    -
superseded-by: -
```

## 背景

RBAC 权限 `users:manage` / `roles:manage` / `audit:read` 早已定义并授予 admin，`RoleOut` / `UserRoleUpdateRequest` 等 Schema 早已写好，架构文档 §6.1 早已把 `/auth/admin/*` 写成契约——但**零端点**。结果是角色分配只能改 SQL，且 [ADR 0008](./0008-research-read-requires-permission.md) 记为已知遗留。同时 `POST /auth/register` 全开、建号即 `active`、默认角色硬编码 `trader`，任何人可自助注册并立即登录。

替代方案：①开放注册 + 管理员审批队列（异步流程，待审队列要清、用户名会被占位）；②保留自助注册但只给 `viewer`（等于现状 + admin API，注册入口仍敞开）；③邀请制 + 管理员预开户。owner 拍板 ③。

## 决策

- **邀请制**：`POST /auth/register` 恒 `403 AUTH_REGISTRATION_DISABLED`。开户唯一路径是 `POST /auth/admin/users`（需 `users:manage`）与 `bootstrap-admin` CLI（二者共用 `provision_user()` 单一代码路径）。**开户即授权**：`roles` 在开户请求必填非空——不存在"注册后待审"异步流程。
- **临时密码 + 强制首登改密**：服务端生成初始密码（仅响应体返回一次），`must_change_password=true` 经五段门控强制改密（redis 会话 → `/auth/*` 白名单 → 断言签发拒绝 → forward-auth 403 透传 → 前端跳转）。不改断言 claims 契约。
- **登录先验密码后判状态**：密码错一律 `401`，密码正确才暴露 `suspended` / 临时密码过期等具体原因；未知用户跑 dummy hash 抹平时序——消除账号枚举。
- **不硬删除**：只有 `suspend` / `reactivate`。硬删会 CASCADE 凭据会话、置空审计 `user_id`、破坏业务库 `watchlists` / `requested_by` 归属。归因走 `auth_audit_events`（故 `auth_users` 不加 `created_by` 列）。
- **角色矩阵只读**：`GET /auth/admin/roles` 挂 `users:manage`（开户下拉依赖它）。角色→权限映射由代码 + 迁移定义，不做在线编辑——否则 `require_permissions(...)` 的代码假设会静默漂移。
- **权限实时读 DB + 变更即吊销会话**：admin 端点鉴权现查角色权限（不用会话快照）；suspend/改角色/重置密码后 `revoke_all_user_sessions`。`/api/*` 传播窗口 ≤25s（forward-auth 断言缓存），明确接受。
- **拒绝在线编辑角色矩阵、邀请码、邮件、SSO、多租户**（本轮）；后四者是既定演进方向，契约预留见计划 §9.2（不占用表名、`auth_type` 保持多值、租户化是加列不是重构）。
- 本决策关闭 ADR 0008 的「已知遗留」。

## 影响

- 自助注册消失：登录页删注册 Tab，着陆页「注册只增加个性化能力」叙事改为邀请制口径，`public-homepage.spec.ts` 文案断言同步。
- 存量自注册用户一律 grandfather 为 `active`，迁移 0002 不触碰既有行。
- auth-service 自判权限与 CSRF（`/auth/*` 不过 forward-auth）；参数化守卫测试遍历 admin 路由表，新端点忘挂守卫即红。
- 首个 admin 由 CLI 引导（幂等：已存在只补角色绝不重置密码），部署步骤进 `docs/deployment/`。
- 回归由 `auth-service` pytest（状态矩阵 / 守卫矩阵 / CLI 三态幂等）、`forward-auth` 403 透传测试、e2e `admin-users.spec.ts` 全链路锁定。
