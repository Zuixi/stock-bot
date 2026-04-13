import { Typography, Card, Alert } from "antd";
import { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { MarketFilters } from "@/features/market/components/MarketFilters";
import { StockTable } from "@/features/market/components/StockTable";
import { fetchCategories, fetchStocksMerged } from "@/shared/api/stocks";
import type { Exchange } from "@/shared/types";
import type { StockRecord } from "@/shared/types";

export default function CategoryPage() {
  const [searchParams] = useSearchParams();

  const [filters, setFilters] = useState<{ exchange?: Exchange; category?: string }>({
    category: searchParams.get("industry") ?? undefined,
  });

  useEffect(() => {
    setFilters((prev) => ({
      ...prev,
      category: searchParams.get("industry") ?? undefined,
    }));
  }, [searchParams]);
  const [sort, setSort] = useState<{ sortBy?: keyof StockRecord; sortOrder?: "asc" | "desc" }>({
    sortBy: "symbol",
    sortOrder: "asc",
  });

  const { data: remoteStocks = [], isLoading, error } = useQuery({
    queryKey: ["market-category-stocks", filters.exchange, filters.category],
    queryFn: () =>
      fetchStocksMerged({
        exchange: filters.exchange,
        category: filters.category,
      }),
  });

  const { data: categoryRows = [] } = useQuery({
    queryKey: ["market-categories", filters.exchange],
    queryFn: () => fetchCategories(filters.exchange),
  });

  const categories = useMemo(
    () =>
      Array.from(
        new Set(
          categoryRows
            .filter((row) => (!filters.exchange ? true : row.exchange === filters.exchange))
            .map((row) => row.category)
        )
      ),
    [categoryRows, filters.exchange]
  );

  const data = useMemo(
    () => {
      const list = [...remoteStocks];
      if (!sort.sortBy) return list;
      const direction = sort.sortOrder === "asc" ? 1 : -1;
      list.sort((a, b) => {
        const av = a[sort.sortBy!] ?? 0;
        const bv = b[sort.sortBy!] ?? 0;
        return av > bv ? direction : av < bv ? -direction : 0;
      });
      return list;
    },
    [remoteStocks, sort]
  );

  return (
    <div>
      <Typography.Title level={4}>分类市场</Typography.Title>

      <Card size="small" style={{ marginBottom: 16 }}>
        <MarketFilters value={filters} categories={categories} onChange={setFilters} />
      </Card>

      {error ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message="后端数据读取失败"
          description={error instanceof Error ? error.message : "请检查 backend 服务是否正常"}
        />
      ) : null}

      <StockTable
        data={data}
        loading={isLoading}
        onChange={(_pagination, _filters, sorter) => {
          if (!Array.isArray(sorter) && sorter.field) {
            setSort({
              sortBy: sorter.field as keyof StockRecord,
              sortOrder: sorter.order === "ascend" ? "asc" : "desc",
            });
          }
        }}
      />
    </div>
  );
}
