import { Layout } from "antd";
import { NavTree } from "./NavTree";
import type { NavNode } from "./types";

const { Sider } = Layout;

/**
 * 模块内左侧折叠树（IA 文档 §5 的第三层之一）。
 *
 * 只做壳：**树的节点全部来自 `nodes` prop**（后续由各模块的配置 / 后端 payload 下发，
 * 如行业三级树、产业链节点）。`nodes` 为空时整块 sider 不渲染——
 * 本任务只交付原语与壳，不下发任何模块树，故当前一律不渲染。
 */
export interface ModuleSideNavProps {
  nodes: NavNode[];
  defaultOpenKeys?: string[];
}

export function ModuleSideNav({ nodes, defaultOpenKeys }: ModuleSideNavProps) {
  if (!nodes.length) return null;
  return (
    <Sider
      width={240}
      theme="light"
      style={{ borderInlineEnd: "1px solid var(--border)", background: "var(--bg-panel)" }}
    >
      <NavTree
        nodes={nodes}
        mode="inline"
        defaultOpenKeys={defaultOpenKeys}
        style={{ border: "none", background: "transparent", height: "100%" }}
        data-testid="module-side-nav"
      />
    </Sider>
  );
}
