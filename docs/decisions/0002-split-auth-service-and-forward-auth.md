# 0002 拆出 auth-service + forward-auth，认证与业务库分离

```
status:        accepted
date:          2026-09-09
supersedes:    -
superseded-by: -
```

> 追认补录（2026-09-23）。来源：`docs/architecture/authentication-and-gateway.md`、`plans/2026-09-09-auth-gateway-implementation.md`。

## 背景

原系统是单用户、无状态的公开数据服务，全部流量直接进业务 API。引入多用户与投研工作台协作后，需要身份认证、细粒度权限与多租户数据隔离，同时不能把凭据体系耦合进业务服务。

## 决策

- **独立 auth-service**：注册/登录/会话/JWKS/RBAC/审计，**独立数据库与独立 Alembic 版本线**（`migrate-auth` 一次性容器）
- **Traefik 作 API Gateway**：按 `PathPrefix` 分发 `/`→frontend、`/api`→api、`/auth`→auth-service；统一限流、安全头、`trace_id` 注入
- **forward-auth sidecar**：会话 cookie → 签发短时 **Principal Assertion JWT（RS256, TTL 60s）**，业务服务仅用 JWKS 公钥验签
- **凭据只存 HttpOnly Cookie**（BFF 同源模式），SPA 不接触任何 Access/Refresh Token；非幂等请求走 CSRF 双防御
- 关键参数：`trustForwardHeader: false`（否则客户端可伪造 `X-Forwarded-Method: GET` 绕过 CSRF 强制）

## 影响

- 得到：业务库不再持有凭据材料；身份上下文有单一签发点且可验签；XSS 无法直接窃取 token
- 代价：**两库两迁移线**，部署与恢复流程变复杂（`docs/deployment/data-migration.md` 需分别导出/恢复/对账）；开发期多了 gateway 一跳
- 遗留：本地开发必须经 gateway 才能拿到身份上下文，直连 `:8000` 的旧习惯会表现为"未登录"

## 备选方案与否决原因

- **会话表塞进业务主库** —— 凭据材料与业务数据同一爆炸半径，且迁移/备份互相牵制
- **业务服务各自实现 JWT 校验** —— 校验逻辑与密钥轮换在 N 处重复，是最典型的安全漂移点
- **网关直接注入裸 `X-User-Id` 头** —— 内网任何一跳都能伪造身份，等价于没有认证
