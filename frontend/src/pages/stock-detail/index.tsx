import { useParams, useNavigate, Link } from "react-router-dom";
import { Typography, Result, Button, Divider, Row, Col, Spin, Breadcrumb } from "antd";
import type { BreadcrumbProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import { StockHeader, FundamentalCards, CustomSwTags, UserTags } from "@/features/stock-detail/components";
import { KlineChart } from "@/shared/ui/kline";
import { fetchKlineBySymbol } from "@/shared/api/quotes";
import { fetchStockEnrichedBySymbol } from "@/shared/api/stocks";
import type { StockRecord } from "@/shared/types";

/**
 * 层级式面包屑：市场 / 申万L1 / L2 / L3 / {股票名}。
 * 链条取自个股自身的申万映射（与入口页无关）；无映射降级为 市场 / 其他 / {股票名}。
 * 层级标签（申万一级等）省略以保持紧凑；末项为当前页不可点击。
 */
function buildBreadcrumbItems(stock: StockRecord): BreadcrumbProps["items"] {
  const chain = stock.swChain ?? [];
  const l1 = chain.find((node) => node.level === 1);
  const l2 = chain.find((node) => node.level === 2);
  const l3 = chain.find((node) => node.level === 3);

  const items: BreadcrumbProps["items"] = [
    { title: <Link to="/market">市场</Link> },
  ];

  if (l1) {
    items.push({ title: <Link to={`/market/industry/${l1.code}`}>{l1.name}</Link> });
    if (l2) {
      // L3 板块挂在二级行业页内（页内标签切换），故 L2/L3 链接指向同一路由
      const l2Route = `/market/industry/${l1.code}/${l2.code}`;
      items.push({ title: <Link to={l2Route}>{l2.name}</Link> });
      if (l3) {
        items.push({ title: <Link to={l2Route}>{l3.name}</Link> });
      }
    }
  } else {
    items.push({ title: <Link to="/market/industry/OTHER">其他</Link> });
  }

  items.push({ title: stock.name });
  return items;
}

export default function StockDetailPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const navigate = useNavigate();

  const { data: stock, isLoading } = useQuery({
    queryKey: ["stock-detail", symbol],
    queryFn: async () => (symbol ? fetchStockEnrichedBySymbol(symbol) : null),
    enabled: Boolean(symbol),
  });

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "64px 0" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!stock) {
    return (
      <Result
        status="404"
        title="未找到该股票"
        subTitle={`代码 ${symbol ?? ""} 不存在或暂无数据`}
        extra={
          <Button type="primary" onClick={() => navigate("/market")}>
            返回市场
          </Button>
        }
      />
    );
  }

  return (
    <div>
      <Breadcrumb style={{ marginBottom: 8 }} items={buildBreadcrumbItems(stock)} />
      <StockHeader stock={stock} />
      <div style={{ marginTop: 8 }}>
        <CustomSwTags exchange={stock.exchange} symbol={stock.symbol} />
      </div>
      <div style={{ marginTop: 8 }}>
        <UserTags exchange={stock.exchange} symbol={stock.symbol} />
      </div>

      <Divider style={{ margin: "16px 0" }} />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <KlineChart
            title="历史行情"
            queryKey={`stock-kline-${stock.symbol}`}
            fetcher={(days, adjust) => fetchKlineBySymbol(stock.symbol, days, adjust)}
            showAdjust
          />
        </Col>
        <Col xs={24} lg={10}>
          <FundamentalCards stock={stock} />
        </Col>
      </Row>

      <Divider style={{ margin: "16px 0" }} />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        本页数据仅供参考，不构成投资建议。数据来源可能存在延迟，请以交易所实时数据为准。
      </Typography.Text>
    </div>
  );
}
