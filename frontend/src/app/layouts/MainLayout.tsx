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

/** 由清单的 `NAV_ROOTS`（path + 标题）+ 上表的图标/可见性合成顶栏数据。
 *
 *  ⚠️ **顶栏不渲染图标**（`nav.icon` 留空）——这是**实测的空间预算结论，不是忘了加**：
 *  顶栏在 1440 下的固定开销 ≈ 品牌 112 + 搜索 220 + 工具 100 + 间距/内边距 ~170 = **~600px**；
 *  10 个**带图标**的导航项 ≈ **900px** ⇒ 合计 ~1500 > 1440 ⇒ **必然折进 `...`**
 *  （实测带图标时 @1440 只见 8 项、@1280 只见 6 项——而「情绪分析」正是第 6 项，
 *   owner 报的"这个 page 像消失了"就出在这里）。
 *  去掉图标后 10 项 ≈ **600px** ⇒ 合计 ~1200 ≤ 1280 ⇒ **1280/1440 下 10 项全可见**。
 *  `NAV_PRESENTATION.icon` 数据仍保留（供将来侧栏/抽屉等形态复用），只是顶栏不用。
 *  若要恢复顶栏图标：必须同时接受"窄屏折进 `...`"——两者不可兼得，上表就是预算。 */
const TOP_NAV: NavItem[] = NAV_ROOTS.map((nav) => {
  const presentation = NAV_PRESENTATION[nav.path];
  if (!presentation) {
    // 清单加了导航但忘了配图标/可见性：显式抛错，不静默用一个默认值掩盖
    throw new Error(`NAV_ROOTS 里的 ${nav.path} 缺少 NAV_PRESENTATION 配置`);
  }
  return {
    nav: { key: nav.path, title: nav.title, path: nav.path },
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
          StockBot
        </Typography.Title>
        {/* 全局搜索（IA §3）：**放在品牌与导航之间** —— 这样导航被推到右侧（owner 要求"靠右"），
            搜索同时成为顶栏里最宽的单个控件（对标竞品的同一布局：logo → 搜索 → 导航 → 工具 → 头像）。
            为什么不能靠"导航外壳 justify-end"实现靠右：AntD 的横向 Menu 会**折叠到恰好填满**
            自己的容器（实测 8 项 + `...` 正好铺满 904px、余量 <5px），所以 `flex-end` 无空间可推。
            搜索吃掉伸缩空间后，导航自然落在右侧。 */}
        <div style={{ flex: "0 1 220px", minWidth: 0, maxWidth: 560, display: "flex" }}>
          <SearchBar />
        </div>
        {/* 一级导航（IA §1 的 10 个目的地，平铺）。
            ⚠️ 外壳带 `data-testid="primary-nav"`：`e2e/nav.spec.ts` 以它为作用域断言导航项。
            该 testid 与 `data-nav-label` 是 **E2E 契约锚点**，换布局形态时必须保留，
            否则断言会从"查可见性"退化成"找不到锚点"。
            历史：设计整改 Phase 1 曾按 §0-A 裁定 A1 把导航移入左侧图标栏，owner 实测后
            **否决 A1、要求保持顶栏**；P0 又曾收敛为「4 平铺 + 2 分组」，owner 实测后
            「情绪分析」等入口在下拉里**看起来像页面消失了** ⇒ **回退为 10 项平铺**
            （宁可窄屏折进 `...`，也不把入口藏进下拉）。

            ⚠️ **Menu 必须保持 `flex: 1`（拉伸、有确定宽度）**：AntD 横向 Menu 用 `ResizeObserver`
            观察自身，若容器宽度按内容算且首次测量读到 ~0，它会把自己收缩成只剩 `...`
            并**永不恢复**（实测踩过两次：一次恒 130px、一次 1440 下 10 项只剩 1 项）。 */}
        <div
          data-testid="primary-nav"
          style={{ flex: "1 1 auto", minWidth: 0, display: "flex" }}
        >
          <NavTree
            nodes={navNodes}
            mode="horizontal"
            style={{
              flex: 1,
              minWidth: 0,
              border: "none",
              background: "transparent",
              // 项靠右：导航外壳拉伸后有富余（10 项 ≈600px vs 外壳 ≈858px），把项推到右端
              // ⇒ 品牌与首个导航项之间留出明显空白（"靠右"的可见判据）。
              // ⚠️ 不能靠"外壳 justify-end + Menu 内容宽度"实现：Menu 的容器宽若按内容算，
              //    它首次测量读到 ~0 会把自己收缩成只剩 `...` 且永不恢复（踩过两次）。
              justifyContent: "flex-end",
            }}
            data-testid="top-nav"
          />
        </div>
        {/* 右侧全局工具（IA §3）：主题 / 用户菜单——搜索已在品牌与导航之间渲染（见上）。
            ⚠️ 不要在里再加一个 `<SearchBar />`：本轮就因"加了外壳却没删旧行"导致顶栏
            渲染出**两个**搜索框，各占 220px，把导航压成 664px（实测 @1440 只剩 7 项可见）。 */}
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
        本站数据仅供参考，不构成投资建议。StockBot ©{new Date().getFullYear()}
      </Footer>
    </Layout>
  );
}
