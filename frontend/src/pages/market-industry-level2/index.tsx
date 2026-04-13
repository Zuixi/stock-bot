import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Breadcrumb, Card, Col, Empty, Row, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import { fetchSwIndustryTree, fetchSwLevel1Stocks } from "@/shared/api/swIndustry";

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

export default function IndustryLevel2Page() {
  const navigate = useNavigate();
  const { level1Code = "" } = useParams();
  const [sort, setSort] = useState<SortState>({ sortBy: "symbol", sortOrder: "asc" });

  const { data: tree = [], isLoading: treeLoading } = useQuery({
    queryKey: ["sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
  });
  const { data: level1Stocks = [], isLoading: stocksLoading } = useQuery({
    queryKey: ["sw-level1-stocks", level1Code],
    queryFn: () => fetchSwLevel1Stocks(level1Code),
    enabled: Boolean(level1Code),
  });
  const level1 = useMemo(() => tree.find((node) => node.code === level1Code), [tree, level1Code]);
  const stocks = useMemo(() => applySort(level1Stocks, sort), [level1Stocks, sort]);

  const onTableChange: TableProps<StockRecord>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof StockRecord,
        sortOrder: sorter.order === "ascend" ? "asc" : "desc",
      });
    }
  };

  if (!level1) {
    return (
      <Card>
        {treeLoading ? <Typography.Text type="secondary">行业数据加载中...</Typography.Text> : <Empty description="未找到对应的一级行业" />}
      </Card>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Breadcrumb
        items={[
          { title: <a onClick={() => navigate("/market")}>市场</a> },
          { title: "申万一级" },
          { title: level1.name },
        ]}
      />

      <Card
        title={`${level1.name} · 二级行业`}
        size="small"
        extra={<Typography.Text type="secondary">点击二级行业进入三级页面</Typography.Text>}
      >
        <Row gutter={[12, 12]}>
          {level1.children.map((level2) => {
            const level2Symbols = level2.children.flatMap((level3) => level3.symbols);
            const level2Stocks = level1Stocks.filter((stock) => level2Symbols.includes(stock.symbol));
            const topMoveStocks = [...level2Stocks]
              .sort((a, b) => Math.abs(b.changePercent ?? 0) - Math.abs(a.changePercent ?? 0))
              .slice(0, 3);
            return (
              <Col key={level2.code} xs={24} sm={12} lg={8}>
                <Card hoverable size="small" onClick={() => navigate(`/market/industry/${level1.code}/${level2.code}`)}>
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Typography.Text strong>{level2.name}</Typography.Text>
                    <Typography.Text type="secondary">{level2Stocks.length} 只个股</Typography.Text>
                    <Space wrap>
                      {topMoveStocks.length > 0 ? (
                        topMoveStocks.map((stock) => (
                          <Tag key={stock.symbol} color={(stock.changePercent ?? 0) >= 0 ? "red" : "green"}>
                            {stock.name} {(stock.changePercent ?? 0) > 0 ? "+" : ""}
                            {(stock.changePercent ?? 0).toFixed(2)}%
                          </Tag>
                        ))
                      ) : (
                        <Tag>暂无样本</Tag>
                      )}
                    </Space>
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      </Card>

      <Card
        title={`${level1.name} · 个股最新信息`}
        size="small"
        extra={<Tag color="blue">一级</Tag>}
      >
        <StockTable data={stocks} onChange={onTableChange} loading={stocksLoading} />
      </Card>
    </Space>
  );
}
