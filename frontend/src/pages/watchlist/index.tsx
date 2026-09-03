import { Typography, Button } from "antd";
import { DeleteOutlined } from "@ant-design/icons";
import { WatchlistTable } from "@/features/watchlist/components";
import { useWatchlistStore } from "@/features/watchlist/store";

export default function WatchlistPage() {
  const { items, clear } = useWatchlistStore();

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          我的自选（{items.length}）
        </Typography.Title>
        {items.length > 0 && (
          <Button size="small" icon={<DeleteOutlined />} danger onClick={clear}>
            清空
          </Button>
        )}
      </div>
      <WatchlistTable />
    </div>
  );
}
