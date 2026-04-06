import { useParams, useNavigate } from "react-router-dom";
import { Typography, Result, Button, Divider, Row, Col } from "antd";
import { StockHeader, KLineChart, FundamentalCards } from "@/features/stock-detail/components";
import { getStockBySymbol } from "@/shared/mocks/stocks";

export default function StockDetailPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const navigate = useNavigate();

  const stock = symbol ? getStockBySymbol(symbol) : undefined;

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
      <StockHeader stock={stock} />

      <Divider style={{ margin: "16px 0" }} />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <KLineChart symbol={stock.symbol} />
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
