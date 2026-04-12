import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Breadcrumb, Card, Col, Empty, Row, Space, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { StockTable } from "@/features/market/components/StockTable";
import type { StockRecord } from "@/shared/types";
import { getLevel1Node, getLevel1Stocks, getLevel2Stocks } from "@/shared/mocks/swIndustry";

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
  const [sort, setSort] = useState<SortState>({ sortBy: "marketCap", sortOrder: "desc" });

  const level1 = useMemo(() => getLevel1Node(level1Code), [level1Code]);
  const stocks = useMemo(() => applySort(getLevel1Stocks(level1Code), sort), [level1Code, sort]);

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
        <Empty description="未找到对应的一级行业" />
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
            const level2Stocks = getLevel2Stocks(level1.code, level2.code);
            const topMoveStocks = [...level2Stocks]
              .sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent))
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
                          <Tag key={stock.symbol} color={stock.changePercent >= 0 ? "red" : "green"}>
                            {stock.name} {stock.changePercent > 0 ? "+" : ""}
                            {stock.changePercent.toFixed(2)}%
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
        <StockTable data={stocks} onChange={onTableChange} />
      </Card>
    </Space>
  );
}
