import { useMemo, useState } from "react";
import { Card, List, Segmented, Space, Tag, Typography } from "antd";
import { ChangeText } from "@/shared/ui";
import { useNavigate } from "react-router-dom";
import {
  HOT_BOARD_CATEGORIES,
  HOT_BOARD_DATA,
  type HotBoardCategory,
  getHotBoardCategoryLabel,
} from "@/shared/mocks/hotBoards";

export function HotSectors() {
  const navigate = useNavigate();
  const [category, setCategory] = useState<HotBoardCategory>("industry");
  const rows = useMemo(
    () => [...HOT_BOARD_DATA[category]].sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent)).slice(0, 6),
    [category]
  );

  return (
    <Card
      title="A股热门板块"
      size="small"
      extra={
        <a onClick={() => navigate(`/market/hot-sectors/${category}`)}>
          查看全部
        </a>
      }
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Segmented
          block
          value={category}
          options={HOT_BOARD_CATEGORIES.map((item) => ({ label: item.label, value: item.key }))}
          onChange={(value) => setCategory(value as HotBoardCategory)}
        />
      <List
        size="small"
        dataSource={rows}
        renderItem={(item) => (
          <List.Item
            style={{ cursor: "pointer", padding: "8px 0" }}
            onClick={() => navigate(`/market/hot-sectors/${category}?board=${item.code}`)}
          >
            <div style={{ display: "flex", alignItems: "center", width: "100%", gap: 12 }}>
              <Typography.Text strong style={{ width: 96 }}>{item.name}</Typography.Text>
              <Typography.Text type="secondary" style={{ width: 52 }}>{item.code}</Typography.Text>
              <ChangeText value={item.changePercent} style={{ width: 76 }} />
              <Typography.Text type="secondary" style={{ fontSize: 12, flex: 1 }}>
                上涨 {item.upCount} | 平盘 {item.flatCount} | 下跌 {item.downCount}
              </Typography.Text>
              <div style={{ display: "flex", gap: 4 }}>
                {item.leaders.slice(0, 2).map((s) => (
                  <Tag key={s.symbol} color={s.changePercent > 0 ? "red" : "green"} style={{ margin: 0, fontSize: 11 }}>
                    {s.name} {s.changePercent > 0 ? "+" : ""}{s.changePercent.toFixed(2)}%
                  </Tag>
                ))}
              </div>
            </div>
          </List.Item>
        )}
      />
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {getHotBoardCategoryLabel(category)}：点击条目可查看该类别下全部细分板块。
        </Typography.Text>
      </Space>
    </Card>
  );
}
