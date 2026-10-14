import { Layout } from "antd";
import type { ReactNode } from "react";
import { NavTree } from "./NavTree";
import type { NavNode } from "./types";

const { Sider } = Layout;

/**
 * 模块内左侧折叠树（IA 文档 §5 的第三层之一）。
 *
 * 只做壳：**树的节点全部来自 `nodes` prop**（由各模块的配置下发，如申万行业树、
 * 产业链节点，见 `app/router/route-manifest.ts` 的 `MODULE_TREES`）。
 *
 * Phase 1 §W1.1 起它**兼任一级导航的容器**（owner 裁定 A1：一级导航从 Header 移入左侧栏）。
 * 为什么复用这个壳而不是新建组件：左栏要同时承载「一级导航」与「模块内二级树」两层
 * （IA §3.2 的三层布局），二者是**同一个 Sider 的两段**，拆成两个 Sider 会得到两条边框
 * 与两个滚动条。`primary` 由调用方传入**已渲染好的节点树**——本组件仍不认识任何业务知识
 * （不知道有哪些一级导航，也不知道图标从哪来）。
 */
export interface ModuleSideNavProps {
  /** 模块内二级/三级树的节点；空数组时不渲染这一层（只留 `primary`） */
  nodes: NavNode[];
  /**
   * 一级导航的**已渲染节点树**（`MainLayout` 传进来，外壳带 `data-testid="primary-nav"`）。
   * 以 `ReactNode` 而非节点数组接收：本组件不该关心一级导航的可见性/图标/路由合成逻辑。
   */
  primary?: ReactNode;
  width?: number;
  /** `true` = 折叠成图标栏（`1024–1279` 档），一级导航与二级树都收成图标/浮层 */
  collapsed?: boolean;
  defaultOpenKeys?: string[];
}

export function ModuleSideNav({
  nodes,
  primary,
  width = 240,
  collapsed = false,
  defaultOpenKeys,
}: ModuleSideNavProps) {
  // 一层都没有才整块不渲染；只有一级导航、没有模块树时仍要渲染（否则左栏会消失）
  if (!nodes.length && !primary) return null;
  return (
    <Sider
      width={width}
      collapsedWidth={64}
      collapsed={collapsed}
      collapsible={false}
      theme="light"
      style={{
        borderInlineEnd: "1px solid var(--border)",
        background: "var(--bg-panel)",
        // ⚠️ **不要在这里写 `position: sticky` + `height: calc(100vh - N)`**（Phase 1 实测的坑）：
        // Sider 一旦有了显式高度，`Layout` 的 flex 行高就变成"最高的那个子项"，而 `Content`
        // 是 `flex: auto` ⇒ **它会被拉到与 Sider 等高**，页脚被顶到视口外 → 整页多出约 106px
        // 的纯浪费高度（实测：`数据面` Tab 内容不足一屏，scrollHeight 却从 900 涨到 973）。
        // 正确做法是让这一列**自然高度**（等于内容高度），再把滚动交给它自己的
        // `overflowY` —— 配合 `MainLayout` 里 `Layout` 的 `alignItems: flex-start`，
        // 侧栏既不会撑高页面，也不会被内容列拉长。
        maxHeight: "calc(100vh - 64px)",
        overflowY: "auto",
        overscrollBehavior: "contain",
      }}
    >
      {primary}
      {nodes.length ? (
        <NavTree
          nodes={nodes}
          mode="inline"
          inlineCollapsed={collapsed}
          defaultOpenKeys={defaultOpenKeys}
          style={{ border: "none", background: "transparent" }}
          data-testid="module-side-nav"
        />
      ) : null}
    </Sider>
  );
}
