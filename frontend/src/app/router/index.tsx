import { Routes, Route } from "react-router-dom";
import { ROUTES, type AppRoute } from "./route-manifest";
import { renderRoute } from "./route-elements";

/**
 * 应用路由表。
 *
 * **本文件不再手写任何 `<Route>`**：逐条路由由 `./route-manifest.ts`（纯数据清单）生成，
 * 元素由 `./route-elements.tsx`（懒加载 + `RequireAuth` + 占位壳）渲染。
 * `parent: "main"` 的条目嵌进 MainLayout 那条 layout 路由下，与改造前的嵌套结构逐条等价。
 *
 * 为什么（Ruling 33）：`frontend/scripts/check-routes.mjs` 此前靠**解析本文件的 JSX 源码**做门禁，
 * 两轮里连续假绿（占位判定失效、`/` 与 `/login` 被扫描器吞掉）。现在门禁直接 import 那份清单，
 * 断言的是数据而不是源码——`/`、`/login` 与 layout 路由不可能再被"吞掉"。
 *
 * 权限三态（IA §1 / ADR 0008，**不新增 scope**）写在清单的 `requiresAuth` / `permission` 字段里；
 * 逐条对照的机械校验见 `npm run check:routes`。
 */
function renderRouteElement(route: AppRoute, key: string) {
  return <Route key={key} path={route.path || undefined} element={renderRoute(route)} />;
}

export function AppRouter() {
  return (
    <Routes>
      {ROUTES.filter((route) => !route.parent).map((route) =>
        route.kind === "layout" ? (
          <Route key={route.key} element={renderRoute(route)}>
            {ROUTES.filter((child) => child.parent === route.key).map((child) =>
              renderRouteElement(child, child.key)
            )}
          </Route>
        ) : (
          renderRouteElement(route, route.key)
        )
      )}
    </Routes>
  );
}
