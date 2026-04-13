import { Select, Space } from "antd";
import { EXCHANGE_LABELS } from "@/shared/types";
import type { Exchange } from "@/shared/types";

interface FilterValues {
  exchange?: Exchange;
  category?: string;
}

interface Props {
  value: FilterValues;
  categories: string[];
  onChange: (v: FilterValues) => void;
}

const EXCHANGE_OPTIONS = [
  { label: "全部交易所", value: "" },
  ...Object.entries(EXCHANGE_LABELS).map(([k, v]) => ({ label: v, value: k })),
];

export function MarketFilters({ value, categories, onChange }: Props) {
  const categoryOptions = [
    { label: "全部分类", value: "" },
    ...categories.map((category) => ({ label: category, value: category })),
  ];

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
        value={value.category ?? ""}
        options={categoryOptions}
        onChange={(v) => onChange({ ...value, category: v || undefined })}
      />
    </Space>
  );
}
