# 0008 投研板块读接口需 research:read 权限（公开行情不变）

```
status:        accepted
date:          2026-09-28
supersedes:    -
superseded-by: -
```

## 背景

`plans/2026-09-11-public-market-homepage.md` 拍板"登录只解锁个性化（自选/标签/提醒），不解锁数据可见性"，
据此 `/market`、`/research`、`/stock/:symbol` 全部免登录。但 `/research` 承载的是**推断结论**：周期阶段、买卖信号、
仓位建议、标的对比、知识库——且 `/api/v1/industries` 的列表响应本身就带 `phase / signal_type / signal_date`。

替代方案有四个：前端路由隐藏（不做服务端门禁）、边缘层（Traefik/forward-auth）加受保护路径白名单、
**只要求登录**、**要求 `research:read` 权限**。

关键事实：`POST /auth/register` 对外完全开放（无邀请码/白名单/开关），新注册账号固定是 `trader` 角色。
因此"只要求登录"等于**任何人在公网自助注册即可读投研**——达不到"投研才可以读"的产品口径。
而 RBAC 里 `research:read` 早已存在且定义就是本功能（`docs/architecture/auth-data-model.md`： "允许查看生猪等行业投研工作台"），
授予 `researcher` / `analyst` / `admin`，刻意不给 `trader` / `operator` / `viewer`。

## 决策

- **读接口整体需 `research:read`**：`backend/app/api/v1/industries.py` 的
  `APIRouter(dependencies=[require_permissions("research:read")])`——**router 级一处依赖**覆盖全部端点且新增端点自动继承；
  匿名经内层 `CurrentUserDep` 得 401，已登录但无权限得 403；写接口的 `research:manage` 在其上叠加。
- **前端按同一权限收敛体验**（非安全边界）：`/research*` 路由用 `RequireAuth permissions={["research:read"]}` 出 403 页；
  顶部导航「投研」项对无权限账号隐藏；公开的申万三级页（`market-industry-level3`）以
  `enabled: hasPermission("research:read")` 让投研 banner 不发请求、不渲染。
- **公开行情不受影响**：`/market/**`、`/stock/:symbol`、`/index/:tsCode` 维持免登录。
- **拒绝**在边缘层写受保护路径白名单：那是第二个事实来源，必与后端注解漂移；边缘职责是身份注入（见 ADR 0002）。
- 已知遗留：仓内**没有**分配角色的接口（`users:manage` 权限已定义但无端点），新增投研用户目前需改库挂
  `researcher`/`analyst` 角色；这是后续要补的 admin 能力，不是本决策的一部分。

## 影响

- 匿名 `GET /api/v1/industries*` → 401；**普通注册账号（trader）→ 403**；researcher/analyst/admin → 200。
- 本决策推翻 `plans/2026-09-11-public-market-homepage.md` 的"登录不解锁数据可见性"原则（该原则对公开行情台仍成立）。
- 回归由 `backend/tests/test_auth_guard.py::test_stock_api_endpoints_protected`（401 / 403 / 放行三态）与
  `frontend/e2e/research.spec.ts`（凭据经 `E2E_RESEARCH_USER/PASSWORD` 注入，缺失即跳过）锁定。
