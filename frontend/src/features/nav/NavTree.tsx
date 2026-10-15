import { Menu } from "antd";
import type { MenuProps } from "antd";
import { useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { CountBadge } from "./CountBadge";
import type { NavNode } from "./types";

/**
 * 数据驱动的导航树原语。
 *
 * **职责边界（硬性，见 IA 文档 §5.1）**：只做「渲染 + 折叠状态 + 跳转」三件事。
 * 组件内**不得硬编码任何业务知识**——不出现具体分类体系的名录、层级数或名称，
 * 也不推断分类体系。节点、层级、徽标全部来自传入的 `nodes`（配置或后端 payload）。
 *
 * 复用方：顶栏一级导航（本任务）、模块内左侧折叠树（本任务已接壳、模块树后续下发）、
 * 后续 S1.4 的行业三级树、S5.1 的产业链节点——同一个组件，多份不同 payload。
 * 「零业务知识」由 S1.1 验收里的关键词 grep 机械核对——**该 grep 对本目录全部文件计数须为 0**，
 * 因此这里的注释也不得出现那些关键词的子串（写具体词就把注释本身变成命中）。
 */

type NavTreeNode = NonNullable<MenuProps["items"]>[number];

export interface NavTreeProps {
  /** 节点树，由配置 / payload 下发；空数组渲染为空菜单（不报错、不兜底造节点） */
  nodes: NavNode[];
  /** `horizontal` = 顶栏一级导航；`inline` = 左侧折叠树（可折展，切换一级导航时整棵树替换） */
  mode?: "horizontal" | "inline";
  /** 默认展开的节点 key（仅 `inline` 模式有意义） */
  defaultOpenKeys?: string[];
  /**
   * 折叠成图标栏（Phase 1 §W1.1 的 `1024–1279` 档）：`true` 时节点只剩图标，
   * hover 展开浮层显示标题。**只在 `mode="inline"` 下有意义**（AntD 的
   * `inlineCollapsed` 作用于 inline 菜单；`vertical`/`horizontal` 无此形态）。
   */
  inlineCollapsed?: boolean;
  /** 无子节点的节点被点击时是否跳转到 `path`（左树里分组节点也可能只想展开） */
  navigateOnLeafClick?: boolean;
  style?: React.CSSProperties;
  "data-testid"?: string;
}

/** 命中路径的最长前缀节点 —— 决定高亮项；纯路径比较，不含任何业务映射 */
export function matchActiveKey(nodes: NavNode[], pathname: string): string | undefined {
  let best: { key: string; length: number } | undefined;
  const walk = (list: NavNode[]) => {
    for (const node of list) {
      if (node.path && (pathname === node.path || pathname.startsWith(`${node.path}/`))) {
        if (!best || node.path.length > best.length) best = { key: node.key, length: node.path.length };
      }
      if (node.children?.length) walk(node.children);
    }
  };
  walk(nodes);
  return best?.key;
}

function toMenuItems(
  nodes: NavNode[],
  onLeafClick: (node: NavNode) => void,
  navigateOnLeafClick: boolean
): NavTreeNode[] {
  return nodes.map((node) => {
    // `data-nav-label` 是 **E2E 作用域锚点**（`e2e/nav.spec.ts`）：导航文案（如「市场」）
    // 在面包屑/页脚/卡片里重复出现，全局 `getByText` 必然 strict-mode violation。
    // 把标题同时写成属性 ⇒ 断言可以"按作用域 + 唯一锚点"计数与判可见性，且与布局形态解耦。
    const label = (
      <span data-nav-label={node.title}>
        {node.title}
        {node.badge ? <CountBadge badge={node.badge} /> : null}
      </span>
    );
    if (!node.children?.length) {
      // 叶子节点：有 path 才可跳转；无 path 的纯分组叶子不可点
      return {
        key: node.key,
        icon: node.icon,
        label,
        disabled: !node.path,
        onClick: navigateOnLeafClick && node.path ? () => onLeafClick(node) : undefined,
      };
    }
    return {
      key: node.key,
      icon: node.icon,
      label,
      // 分组节点自身也可能是可跳转入口（如 /market 既有子模块又要进自身页）
      onClick: node.path ? () => onLeafClick(node) : undefined,
      children: toMenuItems(node.children, onLeafClick, navigateOnLeafClick),
    };
  });
}

export function NavTree({
  nodes,
  mode = "horizontal",
  defaultOpenKeys,
  inlineCollapsed = false,
  navigateOnLeafClick = true,
  style,
  "data-testid": dataTestId,
}: NavTreeProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const items = useMemo(
    () =>
      toMenuItems(nodes, (node) => node.path && navigate(node.path), navigateOnLeafClick),
    [nodes, navigate, navigateOnLeafClick]
  );
  const selectedKey = useMemo(() => matchActiveKey(nodes, location.pathname), [nodes, location.pathname]);

  return (
    <Menu
      mode={mode}
      items={items}
      selectedKeys={selectedKey ? [selectedKey] : []}
      defaultOpenKeys={defaultOpenKeys}
      // `inlineCollapsed` 只在 inline 模式生效；传 `false` 给其它模式是 no-op。
      // ⚠️ AntD 要求 `inlineCollapsed` 与 `mode="inline"` 成对出现，否则折叠态不生效
      //    （`vertical` 模式没有"只剩图标"这一形态）。
      inlineCollapsed={mode === "inline" ? inlineCollapsed : undefined}
      style={style}
      data-testid={dataTestId}
    />
  );
}
