import { Layout, Typography } from "antd";
import {
  BarChartOutlined,
  FundOutlined,
  ReadOutlined,
  BankOutlined,
  GlobalOutlined,
  DashboardOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  SafetyCertificateOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { Outlet, useNavigate } from "react-router-dom";
import { SearchBar } from "@/features/search/components/SearchBar";
import { UserMenu, useAuthStore } from "@/features/auth";
import { ThemeToggle } from "@/shared/ui";
import { ModuleSideNav, NavTree } from "@/features/nav";
import type { NavItem, NavNode, NavVisibility } from "@/features/nav";
import { useCallback, useMemo, type ReactNode } from "react";
import { NAV_ROOTS } from "@/app/router/route-manifest";

const { Header, Content, Footer } = Layout;

/**
 * 顶栏 10 个一级导航的**展示层配置**（图标 + 可见性）。
 *
 * 导航的 **path 与标题不在这里**——它们是路由契约的一部分，唯一真相在
 * `@/app/router/route-manifest` 的 `NAV_ROOTS`（门禁 `npm run check:routes` 直接核对那份数据，
 * 与生成 `<Route>` 的是同一份，故"顶栏少一个导航""标题被改"都能被机械发现）。
 *
 * 权限可见性三态照抄 IA 文档 §1（口径来源 ADR 0008），**不新增任何 scope**：
 * - `public`        —— 市场 `/market`（含全部子路由）、公司分析 `/company`
 * - `permission`    —— 行业分析 `/industry` 与情绪分析 `/sentiment`，沿用既有 `research:read`
 * - `authenticated` —— 其余 6 个（资讯/宏观/价值网/策略/风险/交易）：后端尚不存在，
 *                      按裁定「对已登录用户可见 + 不加门禁」，写死权限名就是假契约
 *
 * ⚠️ `/sentiment` 曾经写成 `authenticated`，而它的路由守卫要 `research:read` —— 结果是
 * **普通登录用户看得到入口、点进去撞 403**。可见性必须与清单里的 `permission` 一致。
 */
const NAV_PRESENTATION: Record<string, { icon: ReactNode; visibility: NavVisibility }> = {
  "/market": { icon: <BarChartOutlined />, visibility: { kind: "public" } },
  "/news": { icon: <ReadOutlined />, visibility: { kind: "authenticated" } },
  "/industry": { icon: <FundOutlined />, visibility: { kind: "permission", permission: "research:read" } },
  "/company": { icon: <BankOutlined />, visibility: { kind: "public" } },
  "/macro": { icon: <GlobalOutlined />, visibility: { kind: "authenticated" } },
  "/sentiment": { icon: <DashboardOutlined />, visibility: { kind: "permission", permission: "research:read" } },
  "/valuegraph": { icon: <DeploymentUnitOutlined />, visibility: { kind: "authenticated" } },
  "/strategy": { icon: <ExperimentOutlined />, visibility: { kind: "authenticated" } },
  "/risk": { icon: <SafetyCertificateOutlined />, visibility: { kind: "authenticated" } },
  "/trade": { icon: <SwapOutlined />, visibility: { kind: "authenticated" } },
};

/** 由清单的 `NAV_ROOTS`（path + 标题）+ 上表的图标/可见性合成顶栏数据 */
const TOP_NAV: NavItem[] = NAV_ROOTS.map((nav) => {
  const presentation = NAV_PRESENTATION[nav.path];
  if (!presentation) {
    // 清单加了导航但忘了配图标/可见性：显式抛错，不静默用一个默认值掩盖
    throw new Error(`NAV_ROOTS 里的 ${nav.path} 缺少 NAV_PRESENTATION 配置`);
  }
  return {
    nav: { key: nav.path, title: nav.title, path: nav.path, icon: presentation.icon },
    visibility: presentation.visibility,
  };
});

/**
 * 模块内二级/三级树的 payload。本任务**不下发任何模块树**（后续由各模块配置 / 后端下发），
 * 空数组 → 左侧树整块不渲染（`ModuleSideNav` 的契约）。提到模块级是为了避免每次渲染
 * 新建数组字面量（无意义的 re-render 依赖）。
 */
const MODULE_TREE: NavNode[] = [];

export function MainLayout() {
  const navigate = useNavigate();
  const { hasPermission } = useAuthStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  const visible = useCallback(
    (visibility: NavVisibility) => {
      switch (visibility.kind) {
        case "public":
          return true;
        case "authenticated":
          return isAuthenticated;
        case "permission":
          // 沿用既有口径（docs/decisions/0008）：无权者不展示入口，点了也只会撞 403
          return hasPermission(visibility.permission);
      }
    },
    [hasPermission, isAuthenticated]
  );

  const navNodes = useMemo(
    () => TOP_NAV.filter((item) => visible(item.visibility)).map((item) => item.nav),
    [visible]
  );

  return (
    <Layout style={{ minHeight: "100vh", background: "var(--bg-page)" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 20,
          padding: "0 24px",
          borderBottom: "1px solid var(--border)",
          position: "sticky",
          top: 0,
          zIndex: 100,
          background: "var(--bg-panel)",
        }}
      >
        <Typography.Title
          level={4}
          style={{ margin: 0, whiteSpace: "nowrap", color: "var(--accent)", cursor: "pointer" }}
          onClick={() => navigate("/market")}
        >
          Stock Bot
        </Typography.Title>
        {/* 10 个一级导航（IA §1 顺序）；节点数据来自上方 TOP_NAV，组件本身不含业务知识。
            ⚠️ 外壳带 `data-testid="primary-nav"`：`e2e/nav.spec.ts` 以它为作用域断言
            「10 个一级项全部可见」。W1（§0-A 裁定 A1）会把导航从 Header 移入左侧图标栏——
            那个外壳**必须保留本 testid**，否则该 E2E 从"断言可见性"退化成"找不到锚点"。 */}
        <div data-testid="primary-nav" style={{ flex: 1, minWidth: 0, display: "flex" }}>
          <NavTree
            nodes={navNodes}
            mode="horizontal"
            style={{ flex: 1, border: "none", background: "transparent", minWidth: 0 }}
            data-testid="top-nav"
          />
        </div>
        {/* 右侧全局工具（IA §3）：搜索 / 主题 / 用户菜单——三者均为既有组件，本任务只保留位置 */}
        <SearchBar />
        <ThemeToggle />
        <UserMenu />
      </Header>

      <Layout>
        <ModuleSideNav nodes={MODULE_TREE} />
        <Content style={{ padding: "16px 24px", maxWidth: 1400, width: "100%", margin: "0 auto" }}>
          <Outlet />
        </Content>
      </Layout>

      <Footer style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>
        本站数据仅供参考，不构成投资建议。Stock Bot ©{new Date().getFullYear()}
      </Footer>
    </Layout>
  );
}
