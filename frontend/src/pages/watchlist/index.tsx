import { Typography, Button, Alert, Space } from "antd";
import { DeleteOutlined, CloudSyncOutlined, LoginOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { WatchlistTable } from "@/features/watchlist/components";
import { useWatchlist } from "@/features/watchlist/useWatchlist";
import { useAuth } from "@/features/auth";

export default function WatchlistPage() {
  const { items, clear, isMutating } = useWatchlist();
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();

  return (
    <div>
      {!isAuthenticated && (
        <Alert
          type="info"
          showIcon
          icon={<CloudSyncOutlined />}
          message="当前为本地自选模式"
          description={
            <Space align="center">
              <span>登录账户后，自选股将自动保存至云端并在多端同步。</span>
              <Button size="small" type="primary" ghost icon={<LoginOutlined />} onClick={() => navigate("/login")}>
                立即登录
              </Button>
            </Space>
          }
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          我的自选（{items.length}）
        </Typography.Title>
        {items.length > 0 && (
          <Button
            size="small"
            icon={<DeleteOutlined />}
            danger
            loading={isMutating}
            onClick={clear}
          >
            清空
          </Button>
        )}
      </div>
      <WatchlistTable />
    </div>
  );
}
