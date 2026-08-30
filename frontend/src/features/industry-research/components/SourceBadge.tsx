import { Tooltip } from "antd";

const TIER_META: Record<string, { label: string; color: string; bg: string; border: string; tip: string }> = {
  official: {
    label: "官方基准",
    color: "#1256c4",
    bg: "#e8f1ff",
    border: "#b8d4ff",
    tip: "以农业农村部 / 发改委 / 交易所口径为准，多源分歧时以官方为准",
  },
  highfreq: {
    label: "高频参考",
    color: "#6b7686",
    bg: "#f7f8fa",
    border: "#e5e9ef",
    tip: "市场化高频数据（生意社/搜猪等），用于跟踪边际变化，不作为基准",
  },
  calc: {
    label: "测算",
    color: "#5b4fc4",
    bg: "#f5f3ff",
    border: "#ddd6fe",
    tip: "基于财报与出栏量等公开数据推算",
  },
  manual: {
    label: "人工录入",
    color: "#8c6a00",
    bg: "#fffbe6",
    border: "#ffe58f",
    tip: "来自公告/纪要/年报的人工整理数据",
  },
  derived: {
    label: "派生",
    color: "#1f6d45",
    bg: "#ecfdf3",
    border: "#b7ebc9",
    tip: "由基础指标计算得出（如猪粮比 = 生猪价 ÷ 玉米价）",
  },
};

interface Props {
  tier: string;
}

/** 数据源权威性徽章：官方基准 / 高频参考 / 测算 / 人工录入 / 派生 */
export function SourceBadge({ tier }: Props) {
  const meta = TIER_META[tier] ?? TIER_META.highfreq;
  return (
    <Tooltip title={meta.tip}>
      <span
        style={{
          fontSize: 10.5,
          lineHeight: 1,
          padding: "3px 6px",
          borderRadius: 4,
          color: meta.color,
          background: meta.bg,
          border: `1px solid ${meta.border}`,
          whiteSpace: "nowrap",
          cursor: "help",
        }}
      >
        {meta.label}
      </span>
    </Tooltip>
  );
}

export function tierLabel(tier: string): string {
  return (TIER_META[tier] ?? TIER_META.highfreq).label;
}
