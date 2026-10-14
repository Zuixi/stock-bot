import { Button, Drawer, Layout, Typography } from "antd";
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
  MenuOutlined,
} from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { SearchBar } from "@/features/search/components/SearchBar";
import { UserMenu, useAuthStore } from "@/features/auth";
import { ThemeToggle } from "@/shared/ui";
import { ModuleSideNav, NavTree } from "@/features/nav";
import type { NavItem, NavVisibility } from "@/features/nav";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { NAV_ROOTS, pickModuleTree } from "@/app/router/route-manifest";

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

/** 由清单的 `NAV_ROOTS`（path + 标题）+ 上表的图标/可见性合成侧栏数据 */
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
 * 四档断点（Phase 1 §W1.1，契约 docs/design/landing-market-theme.md 的断点节）：
 *
 * | 断点 | 一级导航形态 |
 * |---|---|
 * | `≥1280` | 左侧**常驻 208px** 完整栏（10 项全可见，这就是 §0.3 要的形态） |
 * | `1024–1279` | 左侧**折叠 64px 图标栏**，hover 展开浮层 |
 * | `<1024` | **抽屉**（`Drawer` + 汉堡按钮），不进内容流 |
 *
 * 为什么 `≥1280` 才常驻：1280 下"208px 侧栏 + 内容"仍能放下密集表格（§W1.3 把内容
 * `maxWidth` 收到 1280）；再窄就该让位给内容而不是继续挤。
 *
 * ⚠️ **AntD 的 `Grid.useBreakpoint()` 在 SSR/首帧返回空对象**，直接用会把首帧判成 `<1024`
 * 而闪一下抽屉。故用 `window.matchMedia` 真媒体查询（本页只在浏览器里跑），并给
 * `change` 事件挂监听——断点变化时重渲染，而不是只读一次。
 */
function useViewportTier(): "wide" | "mid" | "narrow" {
  const [tier, setTier] = useState<"wide" | "mid" | "narrow">(() => {
    if (typeof window === "undefined") return "wide";
    return window.matchMedia("(min-width: 1280px)").matches
      ? "wide"
      : window.matchMedia("(min-width: 1024px)").matches
        ? "mid"
        : "narrow";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    const wide = window.matchMedia("(min-width: 1280px)");
    const mid = window.matchMedia("(min-width: 1024px)");
    const sync = () => setTier(wide.matches ? "wide" : mid.matches ? "mid" : "narrow");
    wide.addEventListener("change", sync);
    mid.addEventListener("change", sync);
    sync();
    return () => {
      wide.removeEventListener("change", sync);
      mid.removeEventListener("change", sync);
    };
    // 只挂一次：两个 query 对象在组件生命周期内不变
  }, []);
  return tier;
}

export function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { hasPermission } = useAuthStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const tier = useViewportTier();
  const [drawerOpen, setDrawerOpen] = useState(false);

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

  /**
   * 模块内二级树（Phase 1 §W1.2）：按当前 pathname 的最长前缀从 `MODULE_TREES` 选树。
   * 选树逻辑在清单侧（纯数据 + 纯路径比较），这里只管下发——组件仍不含业务知识。
   */
  const moduleNodes = useMemo(() => pickModuleTree(location.pathname), [location.pathname]);

  /** 侧栏一级导航：`inline` 模式（树/图标栏两用），外壳保留 `data-testid="primary-nav"`。
   *
   *  ⚠️ **`data-testid="primary-nav"` 与 `data-nav-label` 是 E2E 契约锚点**
   *  （`e2e/nav.spec.ts` 用 `elementFromPoint` 命中测试断言"10 项对用户可见"）。
   *  形态从顶栏换成侧栏，这两个锚点**一字不动**——否则那条断言会从"断言可见性"
   *  退化成"找不到锚点"（Phase 0 的假绿坑之一）。
   */
  const primaryNav = (
    <div data-testid="primary-nav">
      <NavTree
        nodes={navNodes}
        mode="inline"
        inlineCollapsed={tier === "mid"}
        style={{ border: "none", background: "transparent" }}
        data-testid="primary-nav-tree"
      />
    </div>
  );

  return (
    <Layout style={{ minHeight: "100vh", background: "var(--bg-page)" }}>
      {/* Header 只留全局工具（IA §3）：搜索 / 主题 / 用户菜单。
          一级导航**不在**这里了——它是 §Why-now 表第一行的病灶（1440 下折叠 3 项）。 */}
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "0 16px 0 24px",
          borderBottom: "1px solid var(--border)",
          position: "sticky",
          top: 0,
          zIndex: 100,
          background: "var(--bg-panel)",
        }}
      >
        {tier === "narrow" ? (
          <Button
            type="text"
            aria-label="打开导航"
            data-testid="nav-hamburger"
            icon={<MenuOutlined />}
            onClick={() => setDrawerOpen(true)}
          />
        ) : null}
        <Typography.Title
          level={4}
          style={{ margin: 0, whiteSpace: "nowrap", color: "var(--accent)", cursor: "pointer" }}
          onClick={() => navigate("/market")}
        >
          Stock Bot
        </Typography.Title>
        <div style={{ flex: 1 }} />
        <SearchBar />
        <ThemeToggle />
        <UserMenu />
      </Header>

      {/* `alignItems: "flex-start"` 是必须的：AntD 的 `Layout` 默认让子项沿交叉轴**拉伸**
          等高，左栏一旦比正文高（或反过来）就会把对方拉到同一高度，页脚被顶出视口。
          实测坑：加了 `position: sticky + height` 的 Sider 曾让内容不足一屏的 Tab 也长出
          ~106px 的空白（`数据面` 从 900 → 973）。让两项各按自身内容高度布局即可消除。 */}
      <Layout style={{ alignItems: "flex-start" }}>
        {tier === "wide" ? (
          <ModuleSideNav nodes={moduleNodes} primary={primaryNav} width={208} />
        ) : tier === "mid" ? (
          <ModuleSideNav nodes={moduleNodes} primary={primaryNav} collapsed />
        ) : null}
        <Content
          style={{
            padding: "16px 24px",
            maxWidth: 1280,
            width: "100%",
            margin: "0 auto",
            minWidth: 0,
          }}
        >
          <Outlet />
        </Content>
      </Layout>

      {/* `<1024`：抽屉导航。**`primaryNav` 只在 `narrow` 时渲染在这里**——
          宽屏下由上面的 `ModuleSideNav` 承载，两处互斥，故 DOM 里 `primary-nav` 恒只有一个
          （若两处同时渲染，`nav.spec.ts` 的 `nav.locator('[data-nav-label="市场"]')`
          会撞上两个元素，断言从"可见性"变成 strict-mode 报错）。 */}
      {tier === "narrow" ? (
        <Drawer
          placement="left"
          width={260}
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          title="导航"
          styles={{ body: { padding: 0 } }}
          data-testid="nav-drawer"
        >
          {/* 点任意项即关抽屉：`NavTree` 的叶子 onClick 自己会 navigate，
              这里只负责收起抽屉（在冒泡阶段收，不拦导航）。 */}
          <div onClick={() => setDrawerOpen(false)}>{primaryNav}</div>
        </Drawer>
      ) : null}

      <Footer style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: 12 }}>
        本站数据仅供参考，不构成投资建议。Stock Bot ©{new Date().getFullYear()}
      </Footer>
    </Layout>
  );
}
