import { Typography, Tag, Button, Space, Descriptions, Tooltip } from "antd";
import { StarOutlined, StarFilled } from "@ant-design/icons";
import { ChangeText, NumberText } from "@/shared/ui";
import { EXCHANGE_LABELS } from "@/shared/types";
import { useWatchlistStore } from "@/features/watchlist/store";
import type { StockRecord } from "@/shared/types";

interface Props {
  stock: StockRecord;
}

export function StockHeader({ stock }: Props) {
  const { items, toggle } = useWatchlistStore();
  const isWatched = items.includes(stock.symbol);

  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 16 }}>
      <div>
        <Space align="center" size={8}>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {stock.name}
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>
            {stock.symbol}
          </Typography.Text>
          <Tag>{EXCHANGE_LABELS[stock.exchange]}</Tag>
          {stock.industry && <Tag color="blue">{stock.industry}</Tag>}
        </Space>
        <div style={{ marginTop: 8, display: "flex", alignItems: "baseline", gap: 16 }}>
          <span style={{ fontSize: 28, fontWeight: 700 }}>
            <NumberText value={stock.latestPrice} />
          </span>
          <ChangeText value={stock.change} suffix="" style={{ fontSize: 16 }} />
          <ChangeText value={stock.changePercent} style={{ fontSize: 16 }} />
        </div>
        <Descriptions size="small" column={4} style={{ marginTop: 8 }}>
          <Descriptions.Item label="成交量">
            <NumberText value={stock.volume} unit="cap" />
          </Descriptions.Item>
          <Descriptions.Item label="成交额">
            <NumberText value={stock.turnover} unit="cap" />
          </Descriptions.Item>
        </Descriptions>
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          数据截至 {stock.asof}
        </Typography.Text>
      </div>
      <Button
        type={isWatched ? "primary" : "default"}
        icon={isWatched ? <StarFilled /> : <StarOutlined />}
        onClick={() => toggle(stock.symbol)}
      >
        {isWatched ? "已自选" : "加入自选"}
      </Button>
    </div>
  );
}
