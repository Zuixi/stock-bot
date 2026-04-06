import { Select, Space } from "antd";
import { EXCHANGE_LABELS } from "@/shared/types";
import type { Exchange } from "@/shared/types";
import { MOCK_SECTORS } from "@/shared/mocks/market";

interface FilterValues {
  exchange?: Exchange;
  industry?: string;
}

interface Props {
  value: FilterValues;
  onChange: (v: FilterValues) => void;
}

const EXCHANGE_OPTIONS = [
  { label: "全部交易所", value: "" },
  ...Object.entries(EXCHANGE_LABELS).map(([k, v]) => ({ label: v, value: k })),
];

const INDUSTRY_OPTIONS = [
  { label: "全部行业", value: "" },
  ...MOCK_SECTORS.map((s) => ({ label: s.name, value: s.name })),
];

export function MarketFilters({ value, onChange }: Props) {
  return (
    <Space wrap>
      <Select
        style={{ width: 140 }}
        value={value.exchange ?? ""}
        options={EXCHANGE_OPTIONS}
        onChange={(v) => onChange({ ...value, exchange: (v || undefined) as Exchange | undefined })}
      />
      <Select
        style={{ width: 140 }}
        value={value.industry ?? ""}
        options={INDUSTRY_OPTIONS}
        onChange={(v) => onChange({ ...value, industry: v || undefined })}
      />
    </Space>
  );
}
