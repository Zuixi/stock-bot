import { Card, List, Tag, Typography } from "antd";
import { RiseOutlined, FallOutlined } from "@ant-design/icons";
import { MOCK_SECTORS } from "@/shared/mocks/market";
import { ChangeText, NumberText } from "@/shared/ui";
import { useNavigate } from "react-router-dom";

export function HotSectors() {
  const navigate = useNavigate();
  const sorted = [...MOCK_SECTORS].sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent));

  return (
    <Card title="A股热门板块" size="small">
      <List
        size="small"
        dataSource={sorted}
        renderItem={(item) => (
          <List.Item
            style={{ cursor: "pointer", padding: "8px 0" }}
            onClick={() => navigate(`/market/category?industry=${item.name}`)}
          >
            <div style={{ display: "flex", alignItems: "center", width: "100%", gap: 12 }}>
              <Typography.Text strong style={{ width: 80 }}>{item.name}</Typography.Text>
              <ChangeText value={item.changePercent} style={{ width: 70 }} />
              <Typography.Text type="secondary" style={{ fontSize: 12, flex: 1 }}>
                市值 <NumberText value={item.totalMarketCap} unit="cap" />
              </Typography.Text>
              <div style={{ display: "flex", gap: 4 }}>
                {item.topStocks.map((s) => (
                  <Tag key={s.symbol} color={s.changePercent > 0 ? "red" : "green"} style={{ margin: 0, fontSize: 11 }}>
                    {s.name} {s.changePercent > 0 ? "+" : ""}{s.changePercent.toFixed(2)}%
                  </Tag>
                ))}
              </div>
            </div>
          </List.Item>
        )}
      />
    </Card>
  );
}
