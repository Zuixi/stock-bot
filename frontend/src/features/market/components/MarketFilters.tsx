import { Select, Space } from "antd";
import { EXCHANGE_LABELS } from "@/shared/types";
import type { Exchange } from "@/shared/types";

interface Props {
  value: { exchange?: Exchange };
  onChange: (v: { exchange?: Exchange }) => void;
}

const EXCHANGE_OPTIONS = [
  { label: "全部交易所", value: "" },
  ...Object.entries(EXCHANGE_LABELS).map(([k, v]) => ({ label: v, value: k })),
];

export function MarketFilters({ value, onChange }: Props) {
  return (
    <Space wrap>
      <Select
        style={{ width: 140 }}
        value={value.exchange ?? ""}
        options={EXCHANGE_OPTIONS}
        onChange={(v) => onChange({ exchange: (v || undefined) as Exchange | undefined })}
      />
    </Space>
  );
}
