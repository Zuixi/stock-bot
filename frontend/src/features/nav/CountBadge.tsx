import { Tag } from "antd";
import type { NavBadge, NavCountKind } from "./types";

/**
 * 徽标标签文案：`countKind` → 文案。
 * 两种语义的**唯一定义**在 `docs/design/metric-dictionary.md` §4.1，本文件只负责上屏措辞。
 * 徽标必须带标签——裸数字不出现（同一入口可能同时存在两套语义）。
 */
const COUNT_KIND_LABEL: Record<NavCountKind, string> = {
  ingested: "已接入",
  curated: "已策展",
};

/** 两种语义用不同颜色，避免"已接入 31 / 已策展 16"被读成同一个数 */
const COUNT_KIND_COLOR: Record<NavCountKind, string> = {
  ingested: "blue",
  curated: "green",
};

export function CountBadge({ badge }: { badge: NavBadge }) {
  return (
    <Tag
      color={COUNT_KIND_COLOR[badge.countKind]}
      style={{ marginInlineStart: 6, fontSize: 11, lineHeight: "16px", padding: "0 4px" }}
      data-testid="nav-count-badge"
      data-count-kind={badge.countKind}
    >
      {COUNT_KIND_LABEL[badge.countKind]} {badge.count}
    </Tag>
  );
}
