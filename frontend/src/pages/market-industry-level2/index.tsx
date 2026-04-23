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

  const displayStocks = useMemo(() => applySort(level1Stocks, sort), [level1Stocks, sort]);

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

  const handleL2Click = (l2Code: string) => {
    navigate(`/market/industry/${level1.code}/${l2Code}`);
  };

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
        extra={
          <Typography.Text type="secondary">
            点击二级行业进入详情页面
          </Typography.Text>
        }
      >
        {level1.children.length === 0 ? (
          <Empty description="暂无子分组" />
        ) : (
          <Row gutter={[12, 12]}>
            {level1.children.map((level2) => (
              <Col key={level2.code} xs={24} sm={12} lg={8}>
                <Card
                  hoverable
                  size="small"
                  onClick={() => handleL2Click(level2.code)}
                >
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Typography.Text strong>{level2.name}</Typography.Text>
                    <Space size={8}>
                      <Tag color="blue">{level2.stockCount} 只个股</Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {level2.children.length} 个三级行业
                      </Typography.Text>
                    </Space>
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Card>

      <Card
        title={`${level1.name} · 个股最新信息`}
        size="small"
        extra={<Tag color="blue">一级</Tag>}
      >
        <StockTable data={displayStocks} onChange={onTableChange} loading={stocksLoading} />
      </Card>
    </Space>
  );
}
