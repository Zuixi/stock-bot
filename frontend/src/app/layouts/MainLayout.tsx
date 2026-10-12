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
import { useCallback, useMemo } from "react";

const { Header, Content, Footer } = Layout;

/**
 * 顶栏 10 个一级导航（顺序 = IA 文档 §1 的表序，逐字）。
 *
 * **这是数据**：NavTree 只渲染它，不认识"市场""行业分析"是什么。
 * 权限可见性三态照抄 IA 文档 §1（口径来源 ADR 0008），**不新增任何 scope**：
 * - `public`        —— 市场 `/market`（含全部子路由）、公司分析 `/company`
 * - `permission`    —— 行业分析 `/industry`，沿用既有 `research:read`
 * - `authenticated` —— 其余 7 个（资讯/宏观/情绪/价值网/策略/风险/交易）：后端尚不存在，
 *                      按裁定「对已登录用户可见 + 不加门禁」，写死权限名就是假契约
 */
const TOP_NAV: NavItem[] = [
  { nav: { key: "/market", title: "市场", path: "/market", icon: <BarChartOutlined /> }, visibility: { kind: "public" } },
  { nav: { key: "/news", title: "资讯", path: "/news", icon: <ReadOutlined /> }, visibility: { kind: "authenticated" } },
  {
    nav: { key: "/industry", title: "行业分析", path: "/industry", icon: <FundOutlined /> },
    visibility: { kind: "permission", permission: "research:read" },
  },
  { nav: { key: "/company", title: "公司分析", path: "/company", icon: <BankOutlined /> }, visibility: { kind: "public" } },
  { nav: { key: "/macro", title: "宏观分析", path: "/macro", icon: <GlobalOutlined /> }, visibility: { kind: "authenticated" } },
  {
    nav: { key: "/sentiment", title: "情绪分析", path: "/sentiment", icon: <DashboardOutlined /> },
    visibility: { kind: "authenticated" },
  },
  {
    nav: { key: "/valuegraph", title: "价值网图谱", path: "/valuegraph", icon: <DeploymentUnitOutlined /> },
    visibility: { kind: "authenticated" },
  },
  {
    nav: { key: "/strategy", title: "策略引擎", path: "/strategy", icon: <ExperimentOutlined /> },
    visibility: { kind: "authenticated" },
  },
  {
    nav: { key: "/risk", title: "风险管理", path: "/risk", icon: <SafetyCertificateOutlined /> },
    visibility: { kind: "authenticated" },
  },
  { nav: { key: "/trade", title: "交易执行", path: "/trade", icon: <SwapOutlined /> }, visibility: { kind: "authenticated" } },
];

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
        {/* 10 个一级导航（IA §1 顺序）；节点数据来自上方 TOP_NAV，组件本身不含业务知识 */}
        <NavTree
          nodes={navNodes}
          mode="horizontal"
          style={{ flex: 1, border: "none", background: "transparent", minWidth: 0 }}
          data-testid="top-nav"
        />
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
