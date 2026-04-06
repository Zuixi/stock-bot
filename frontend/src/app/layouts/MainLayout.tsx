import { Layout, Menu, Input, Typography } from "antd";
import {
  BarChartOutlined,
  StarOutlined,
  AppstoreOutlined,
} from "@ant-design/icons";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { SearchBar } from "@/features/search/components/SearchBar";

const { Header, Content, Footer } = Layout;

const NAV_ITEMS = [
  { key: "/market", icon: <BarChartOutlined />, label: "市场" },
  { key: "/market/category", icon: <AppstoreOutlined />, label: "分类" },
  { key: "/watchlist", icon: <StarOutlined />, label: "自选" },
];

export function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  const activeKey = NAV_ITEMS.find((n) => location.pathname.startsWith(n.key))?.key ?? "/market";

  return (
    <Layout style={{ minHeight: "100vh", background: "#f5f5f5" }}>
      <Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 24,
          padding: "0 24px",
          borderBottom: "1px solid #f0f0f0",
          position: "sticky",
          top: 0,
          zIndex: 100,
          background: "#fff",
        }}
      >
        <Typography.Title level={4} style={{ margin: 0, whiteSpace: "nowrap", color: "#1677ff" }}>
          Stock Bot
        </Typography.Title>
        <Menu
          mode="horizontal"
          selectedKeys={[activeKey]}
          items={NAV_ITEMS}
          onClick={({ key }) => navigate(key)}
          style={{ flex: 1, border: "none" }}
        />
        <SearchBar />
      </Header>

      <Content style={{ padding: "16px 24px", maxWidth: 1400, width: "100%", margin: "0 auto" }}>
        <Outlet />
      </Content>

      <Footer style={{ textAlign: "center", color: "#999", fontSize: 12 }}>
        本站数据仅供参考，不构成投资建议。Stock Bot ©{new Date().getFullYear()}
      </Footer>
    </Layout>
  );
}
