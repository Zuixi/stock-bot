import { NumberText } from "@/shared/ui/NumberText";

import { ProvenanceLink } from "./ProvenanceLink";

interface Props {
  value: number | undefined | null;
  metricKey: string;
  asOf?: string | null;
  scopeKey?: string | null;
  unit?: "yuan" | "cap";
  precision?: number;
}

/**
 * 数字 + 溯源入口的组合（P7）。
 *
 * 适用于"这个数字就是页面的主角"的位置；自定义格式化（百分比/家数/n 值）继续用
 * `ProvenanceLink` 单独挂在标题旁，避免为了溯源改掉既有排版。
 */
export function DataPoint({ value, metricKey, asOf, scopeKey, unit, precision }: Props) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
      <NumberText value={value} unit={unit} precision={precision} />
      <ProvenanceLink metricKey={metricKey} asOf={asOf} scopeKey={scopeKey} />
    </span>
  );
}
