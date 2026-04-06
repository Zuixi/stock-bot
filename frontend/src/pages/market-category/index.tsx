import { Typography, Card, Space } from "antd";
import { useState, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { MarketFilters } from "@/features/market/components/MarketFilters";
import { StockTable } from "@/features/market/components/StockTable";
import { filterStocks } from "@/shared/mocks/stocks";
import type { Exchange } from "@/shared/types";

export default function CategoryPage() {
  const [searchParams] = useSearchParams();

  const [filters, setFilters] = useState<{ exchange?: Exchange; industry?: string }>({
    industry: searchParams.get("industry") ?? undefined,
  });
  const [sort, setSort] = useState<{ sortBy?: string; sortOrder?: "asc" | "desc" }>({
    sortBy: "marketCap",
    sortOrder: "desc",
  });

  const data = useMemo(
    () => filterStocks({ ...filters, ...sort }),
    [filters, sort]
  );

  return (
    <div>
      <Typography.Title level={4}>分类市场</Typography.Title>

      <Card size="small" style={{ marginBottom: 16 }}>
        <MarketFilters value={filters} onChange={setFilters} />
      </Card>

      <StockTable
        data={data}
        onChange={(_pagination, _filters, sorter) => {
          if (!Array.isArray(sorter) && sorter.field) {
            setSort({
              sortBy: sorter.field as string,
              sortOrder: sorter.order === "ascend" ? "asc" : "desc",
            });
          }
        }}
      />
    </div>
  );
}
