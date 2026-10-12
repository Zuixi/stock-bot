import type { ReactNode } from "react";

/**
 * 导航树原语的数据契约（Task 5 / S1.1）。
 *
 * **本文件只描述形状，不描述内容**：节点从配置或后端 payload 下发，组件不做任何
 * 分类体系推断。同一份 `NavNode[]` 同时喂给顶栏一级导航与左侧折叠树。
 */

/**
 * 徽标计数的两套语义口径（唯一定义见 `docs/design/metric-dictionary.md` §4.1）。
 *
 * - `ingested` = 已接入数（该入口下已有真实数据可展示的条目数）
 * - `curated`  = 已策展数（该入口下已有策展产物的条目数）
 *
 * 组件只负责把该字段渲染成**带标签**的徽标（裸数字不出现），
 * **不判断数值是否合理、不做 `curated ≤ ingested` 校正**——那是数据侧的事。
 */
export type NavCountKind = "ingested" | "curated";

export interface NavBadge {
  /** 计数数值，由配置 / payload 下发；组件不自己算 */
  count: number;
  /** 计数语义，决定徽标标签；语义定义见 metric-dictionary §4.1 */
  countKind: NavCountKind;
}

export interface NavNode {
  /** 稳定标识：antd Menu 的 key，也用于默认展开项 */
  key: string;
  title: string;
  /** 目标路由；缺省表示该节点只用于分组（不可跳转） */
  path?: string;
  /** 徽标；缺省不渲染徽标。**语义由 countKind 承载，不由组件推断** */
  badge?: NavBadge;
  /** 可选前置图标（由调用方传入元素，组件不认识具体图标含义） */
  icon?: ReactNode;
  /** 子节点；空数组与缺省等价（无子节点） */
  children?: NavNode[];
}

/** 顶栏一级导航的权限可见性三态（口径见 IA 文档 §1 与 ADR 0008） */
export type NavVisibility =
  /** 公开：未登录也可见 */
  | { kind: "public" }
  /** 登录即可：未登录不可见 */
  | { kind: "authenticated" }
  /** 需具体权限：无权者不可见（权限名沿用既有口径，本层不新增 scope） */
  | { kind: "permission"; permission: string };

export interface NavItem {
  nav: NavNode;
  visibility: NavVisibility;
}
