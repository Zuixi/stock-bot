import type { CSSProperties } from "react";

import { Tooltip } from "antd";

import type { AsOfSource } from "@/shared/api/industryResearch";

const FREQ_LABELS: Record<string, string> = {
  daily: "日度",
  weekly: "周度",
  monthly: "月度",
  quarterly: "季度",
  yearly: "年度",
};

const SOURCE_LABELS: Record<string, string> = {
  manual: "人工",
  stats_gov: "统计局",
  caaa: "协会",
  xuantan: "玄田",
  akshare_soozhu: "搜猪网",
  akshare_sina: "新浪",
  derived: "派生",
  mock: "演示数据",
};

/** 四徽章的原始取值（后端 `/metrics/latest` 与 `/metrics/series` 一致） */
export interface MetricBadgeData {
  source: string | null;
  asOf: string | null;
  asOfSource: AsOfSource | null;
  freq: string | null;
  caliberVersion: string | null;
  caliberVersionRegistered: string | null;
}

interface Props extends MetricBadgeData {
  /** 是否展示口径版本徽章（未登记口径时不占位） */
  showCaliber?: boolean;
}

const chip = (color: string, bg: string, border: string): CSSProperties => ({
  fontSize: 10.5,
  lineHeight: 1,
  padding: "3px 6px",
  borderRadius: 4,
  color,
  background: bg,
  border: `1px solid ${border}`,
  whiteSpace: "nowrap",
  cursor: "help",
});

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

/**
 * 指标四徽章：`source / as_of / freq / 口径版本`（spec §S1.2）。
 *
 * 复用 [SourceBadge](./SourceBadge.tsx) 的视觉语言（同一组颜色/尺寸/圆角与 Tooltip
 * 模式），不新起组件体系；权威性（tier）徽章仍由 `SourceBadge` 承担，两者并排使用。
 *
 * `as_of_source === "period"` 时 as_of 是**回退值**（行情等"发布即连续可得"序列没有
 * published_at），徽章用中性色 + 明确提示「口径日期」，绝不把回退值伪装成实际可得日
 * （ADR 0011「不静默回退」）。
 */
export function MetricBadge({
  source,
  asOf,
  asOfSource,
  freq,
  caliberVersion,
  caliberVersionRegistered,
  showCaliber = false,
}: Props) {
  const isRealPit = asOfSource === "published_at";
  const caliberMismatch =
    caliberVersion !== null &&
    caliberVersionRegistered !== null &&
    caliberVersion !== caliberVersionRegistered;

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
      {source && (
        <Tooltip title={`数据源：${source}`}>
          <span style={chip("#6b7686", "#f7f8fa", "#e5e9ef")}>{sourceLabel(source)}</span>
        </Tooltip>
      )}
      {asOf && (
        <Tooltip
          title={
            isRealPit
              ? `实际可得日（PIT，published_at）：${asOf}`
              : asOfSource === "period"
                ? `实际可得日未登记，展示的是口径日期（period）：${asOf} —— 规则/回测不得引用`
                : `日期：${asOf}`
          }
        >
          <span style={chip(isRealPit ? "#1256c4" : "#8c6a00", isRealPit ? "#e8f1ff" : "#fffbe6", isRealPit ? "#b8d4ff" : "#ffe58f")}>
            {isRealPit ? "" : "口径 "}
            {asOf}
          </span>
        </Tooltip>
      )}
      {freq && (
        <Tooltip title={`频率：${FREQ_LABELS[freq] ?? freq}`}>
          <span style={chip("#5b4fc4", "#f5f3ff", "#ddd6fe")}>{FREQ_LABELS[freq] ?? freq}</span>
        </Tooltip>
      )}
      {showCaliber && caliberVersion && (
        <Tooltip
          title={
            caliberMismatch
              ? `行上口径 ${caliberVersion} ≠ registry 当前口径 ${caliberVersionRegistered}（上游口径断点）`
              : `口径版本：${caliberVersion}`
          }
        >
          <span style={chip(caliberMismatch ? "#a8071a" : "#1f6d45", caliberMismatch ? "#fff1f0" : "#ecfdf3", caliberMismatch ? "#ffa39e" : "#b7ebc9")}>
            {caliberVersion}
          </span>
        </Tooltip>
      )}
    </span>
  );
}
