import { Typography, Card, Alert } from "antd";
import { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TableProps } from "antd";
import { MarketFilters } from "@/features/market/components/MarketFilters";
import { StockTable } from "@/features/market/components/StockTable";
import { fetchStocksMerged, fetchStocksMergedEnriched } from "@/shared/api/stocks";
import type { Exchange } from "@/shared/types";
import type { StockRecord } from "@/shared/types";

export default function CategoryPage() {
  const [filters, setFilters] = useState<{ exchange?: Exchange }>({});
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
  }, [filters.exchange]);

  useEffect(() => {
    setPagination((prev) => ({ ...prev, page: 1 }));
  }, [sort.sortBy, sort.sortOrder]);

  // When sorting by a financial column, the basic query returns a different
  // set of stocks than the enriched query (which is correctly sorted server-side).
  // In that case, skip the basic query and use enriched-only mode.
  const isFinancialSort = !!(sort.sortBy && sort.sortBy !== "symbol" && sort.sortBy !== "name");

  // ── Fast: basic stock metadata (renders skeleton immediately) ──
  const { data: remoteStocks, isLoading, error } = useQuery({
    queryKey: ["market-category-stocks", filters.exchange, pagination.page, pagination.pageSize],
    queryFn: () =>
      fetchStocksMerged({
        exchange: filters.exchange,
        page: pagination.page,
        page_size: pagination.pageSize,
      }),
    enabled: !isFinancialSort,
  });

  // ── Deferred: quote + daily_basic (fills financial columns async) ──
  const { data: enrichedStocks, isLoading: enrichedLoading } = useQuery({
    queryKey: [
      "market-category-stocks-enriched",
      filters.exchange,
      pagination.page,
      pagination.pageSize,
      ...(isFinancialSort ? [sort.sortBy, sort.sortOrder] : []),
    ],
    queryFn: () =>
      fetchStocksMergedEnriched({
        exchange: filters.exchange,
        page: pagination.page,
        page_size: pagination.pageSize,
        ...(isFinancialSort && { sort_by: sort.sortBy as string, sort_order: sort.sortOrder }),
      }),
    enabled: !!(isFinancialSort || remoteStocks?.items?.length),
    staleTime: 30_000,
  });

  // Merge basic (skeleton) with enriched (financial fields).
  // When sorting by financial columns, enriched data IS the source of truth.
  const data = useMemo(() => {
    if (isFinancialSort) return enrichedStocks?.items ?? [];

    const enrichedMap = new Map((enrichedStocks?.items ?? []).map((s) => [s.symbol, s]));
    const listSource = remoteStocks?.items ?? [];
    // No client-side sort — sorting is handled server-side or is default symbol order
    return listSource.map((s) => enrichedMap.get(s.symbol) ?? s);
  }, [remoteStocks?.items, enrichedStocks?.items, isFinancialSort]);

  const total = isFinancialSort
    ? (enrichedStocks?.total ?? 0)
    : (remoteStocks?.total ?? 0);

  const handleTableChange: TableProps<StockRecord>["onChange"] = (tablePagination, _filters, sorter) => {
    const nextPageSize = tablePagination.pageSize ?? pagination.pageSize;
    const hasSort = !Array.isArray(sorter) && sorter.field;
    const nextPage = hasSort ? 1 : (tablePagination.current ?? pagination.page);
    setPagination((prev) =>
      prev.page === nextPage && prev.pageSize === nextPageSize
        ? prev
        : { page: nextPage, pageSize: nextPageSize }
    );
    if (hasSort) {
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
        <MarketFilters value={filters} onChange={setFilters} />
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
        loading={isFinancialSort ? enrichedLoading : isLoading}
        sortBy={sort.sortBy}
        sortOrder={sort.sortOrder === "asc" ? "ascend" : "descend"}
        onChange={handleTableChange}
      />
    </div>
  );
}
