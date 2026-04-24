import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Breadcrumb, Card, Empty, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import { fetchStocksByTag } from "@/shared/api/userTags";

type SortState = {
  sortBy?: keyof StockRecord;
  sortOrder?: "asc" | "desc";
};

function applySort(stocks: StockRecord[], sort: SortState): StockRecord[] {
  if (!sort.sortBy) return stocks;
  const sorted = [...stocks];
  const direction = sort.sortOrder === "asc" ? 1 : -1;
  sorted.sort((a, b) => {
    const av = a[sort.sortBy!] ?? 0;
    const bv = b[sort.sortBy!] ?? 0;
    return av > bv ? direction : av < bv ? -direction : 0;
  });
  return sorted;
}

export default function TagsDetailPage() {
  const navigate = useNavigate();
  const { tagName = "" } = useParams();
  const decodedTagName = decodeURIComponent(tagName);
  const [sort, setSort] = useState<SortState>({ sortBy: "symbol", sortOrder: "asc" });

  const { data: stocks = [], isLoading } = useQuery({
    queryKey: ["tag-stocks", decodedTagName],
    queryFn: () => fetchStocksByTag(decodedTagName),
    enabled: Boolean(decodedTagName),
    staleTime: 5 * 60 * 1000,
  });

  const displayStocks = useMemo(() => applySort(stocks, sort), [stocks, sort]);

  const onTableChange: TableProps<StockRecord>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof StockRecord,
        sortOrder: sorter.order === "ascend" ? "asc" : "desc",
      });
    }
  };

  if (!decodedTagName) {
    return (
      <Card>
        <Empty description="标签不存在" />
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb
        items={[
          { title: <a onClick={() => navigate("/tags")}>标签</a> },
          { title: decodedTagName },
        ]}
      />

      <Card
        title={
          <Space>
            <Typography.Text strong>{decodedTagName}</Typography.Text>
            <Tag color="green">{stocks.length} 只个股</Tag>
          </Space>
        }
        size="small"
      >
        {stocks.length === 0 && !isLoading ? (
          <Empty description="该标签下暂无股票" />
        ) : (
          <StockTable data={displayStocks} onChange={onTableChange} loading={isLoading} />
        )}
      </Card>
    </Space>
  );
}
