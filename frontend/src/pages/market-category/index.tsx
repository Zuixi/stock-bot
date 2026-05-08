import { Typography, Card, Alert } from "antd";
import { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import type { TableProps } from "antd";
import { MarketFilters } from "@/features/market/components/MarketFilters";
import { StockTable } from "@/features/market/components/StockTable";
import { fetchCategories, fetchStocksMerged, fetchStocksMergedEnriched } from "@/shared/api/stocks";
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
  const [pagination, setPagination] = useState<{ page: number; pageSize: number }>({
    page: 1,
    pageSize: 20,
  });

  useEffect(() => {
    setPagination({ page: 1, pageSize: 20 });
  }, [filters.exchange, filters.category]);

  // ── Fast: basic stock metadata (renders skeleton immediately) ──
  const { data: remoteStocks, isLoading, error } = useQuery({
    queryKey: ["market-category-stocks", filters.exchange, filters.category, pagination.page, pagination.pageSize],
    queryFn: () =>
      fetchStocksMerged({
        exchange: filters.exchange,
        category: filters.category,
        page: pagination.page,
        page_size: pagination.pageSize,
      }),
  });

  // ── Deferred: quote + daily_basic (fills financial columns async) ──
  const { data: enrichedStocks } = useQuery({
    queryKey: ["market-category-stocks-enriched", filters.exchange, filters.category, pagination.page, pagination.pageSize],
    queryFn: () =>
      fetchStocksMergedEnriched({
        exchange: filters.exchange,
        category: filters.category,
        page: pagination.page,
        page_size: pagination.pageSize,
      }),
    enabled: !!(remoteStocks?.items?.length),
    staleTime: 30_000,
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

  // Merge basic (skeleton) with enriched (financial fields)
  const data = useMemo(
    () => {
      const enrichedMap = new Map((enrichedStocks?.items ?? []).map((s) => [s.symbol, s]));
      const listSource = remoteStocks?.items ?? [];
      const list = listSource.map((s) => enrichedMap.get(s.symbol) ?? s);
      if (!sort.sortBy) return list;
      const direction = sort.sortOrder === "asc" ? 1 : -1;
      list.sort((a, b) => {
        const av = a[sort.sortBy!] ?? 0;
        const bv = b[sort.sortBy!] ?? 0;
        return av > bv ? direction : av < bv ? -direction : 0;
      });
      return list;
    },
    [remoteStocks?.items, enrichedStocks?.items, sort]
  );
  const total = remoteStocks?.total ?? 0;

  const handleTableChange: TableProps<StockRecord>["onChange"] = (tablePagination, _filters, sorter) => {
    const nextPage = tablePagination.current ?? pagination.page;
    const nextPageSize = tablePagination.pageSize ?? pagination.pageSize;
    setPagination((prev) =>
      prev.page === nextPage && prev.pageSize === nextPageSize
        ? prev
        : { page: nextPage, pageSize: nextPageSize }
    );
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof StockRecord,
        sortOrder: sorter.order === "ascend" ? "asc" : "desc",
      });
    }
  };

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
        total={total}
        current={pagination.page}
        pageSize={pagination.pageSize}
        loading={isLoading}
        onChange={handleTableChange}
      />
    </div>
  );
}
