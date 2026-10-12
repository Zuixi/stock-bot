import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Breadcrumb, Button, Card, Empty, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { LoginOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import { fetchStocksByTag } from "@/shared/api/userTags";
import { useAuth } from "@/features/auth";

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

/**
 * 单个标签下的股票列表面板。
 *
 * 由 `/tags/:tagName` 与 `/me?tab=tags&name=:tagName` **共用同一实现**——
 * 后者的 redirect 目标必须与前者的既有行为逐字一致，所以只允许一份实现（不许复制）。
 */
export function TagsPanel({ tagName }: { tagName: string }) {
  const navigate = useNavigate();
  const { user, isAuthenticated } = useAuth();
  const decodedTagName = decodeURIComponent(tagName ?? "");
  const [sort, setSort] = useState<SortState>({ sortBy: "symbol", sortOrder: "asc" });

  const { data: stocks = [], isLoading } = useQuery({
    queryKey: ["tag-stocks", user?.id, decodedTagName],
    queryFn: () => fetchStocksByTag(decodedTagName),
    enabled: Boolean(decodedTagName && isAuthenticated && user?.id),
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

  const goTags = () => navigate("/me?tab=tags");

  if (!isAuthenticated) {
    return (
      <Card>
        <Alert
          type="warning"
          showIcon
          message="请先登录"
          description={
            <Space align="center" style={{ marginTop: 8 }}>
              <span>自定义标签为私有数据，请登录后查看对应个股。</span>
              <Button type="primary" size="small" icon={<LoginOutlined />} onClick={() => navigate("/login")}>
                立即登录
              </Button>
            </Space>
          }
        />
      </Card>
    );
  }

  if (!decodedTagName) {
    return (
      <Card>
        <Empty description="标签不存在" />
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb items={[{ title: <a onClick={goTags}>我的</a> }, { title: decodedTagName }]} />

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
          <StockTable
            data={displayStocks}
            onChange={onTableChange}
            sortBy={sort.sortBy}
            sortOrder={sort.sortOrder === "asc" ? "ascend" : "descend"}
            loading={isLoading}
          />
        )}
      </Card>
    </Space>
  );
}
