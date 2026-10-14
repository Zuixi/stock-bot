import { useState } from "react";
import { Avatar, Dropdown, Space, Tag, Modal, Typography, Button } from "antd";
import type { MenuProps } from "antd";
import {
  UserOutlined,
  LogoutOutlined,
  CrownOutlined,
  LoginOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context";
import { useAuthStore } from "../store";
import { ProfileModal } from "./ProfileModal";

export function UserMenu() {
  const { user, isAuthenticated, logout } = useAuth();
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const [loggingOut, setLoggingOut] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const navigate = useNavigate();

  const handleLogoutConfirm = () => {
    Modal.confirm({
      title: "确认退出登录",
      content: "退出后需要重新输入账号密码登录系统，是否确定？",
      okText: "退出",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        setLoggingOut(true);
        try {
          await logout();
          navigate("/login");
        } finally {
          setLoggingOut(false);
        }
      },
    });
  };

  if (!isAuthenticated || !user) {
    return (
      <Button
        type="primary"
        ghost
        icon={<LoginOutlined />}
        onClick={() => navigate("/login")}
        size="middle"
      >
        登录
      </Button>
    );
  }

  const roleColor = (role: string) => {
    switch (role) {
      case "admin":
        return "gold";
      case "researcher":
        return "blue";
      case "trader":
        return "green";
      default:
        return "default";
    }
  };

  const menuItems: MenuProps["items"] = [
    {
      key: "user-info",
      label: (
        <div style={{ padding: "4px 0" }}>
          <Typography.Text strong>{user.display_name || user.username}</Typography.Text>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>{user.email}</div>
          <div style={{ marginTop: 4, display: "flex", gap: 4, flexWrap: "wrap" }}>
            {user.is_superuser && (
              <Tag color="volcano" icon={<CrownOutlined />}>
                Superuser
              </Tag>
            )}
            {user.roles?.map((role) => (
              <Tag color={roleColor(role)} key={role}>
                {role}
              </Tag>
            ))}
          </div>
        </div>
      ),
      disabled: true,
    },
    {
      key: "profile",
      icon: <SettingOutlined />,
      label: "账号设置",
      onClick: () => setProfileOpen(true),
    },
    // 管理台入口按权限出现（口径见 ADR 0008/0015）。**刻意不放进顶栏一级导航**：
    // IA §1 的顶栏是 10 个一级导航，且 `check-routes` 机械断言 `NAV_ROOTS.length === 10`。
    ...(hasPermission("users:manage")
      ? [
          {
            key: "admin-users",
            icon: <TeamOutlined />,
            label: "用户管理",
            onClick: () => navigate("/admin/users"),
          } satisfies NonNullable<MenuProps["items"]>[number],
        ]
      : []),
    {
      type: "divider",
    },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      danger: true,
      label: "退出登录",
      onClick: handleLogoutConfirm,
    },
  ];

  return (
    <>
    <Dropdown menu={{ items: menuItems }} placement="bottomRight" arrow>
      <Space style={{ cursor: "pointer", userSelect: "none" }}>
        <Avatar
          style={{ backgroundColor: user.is_superuser ? "var(--up)" : "var(--accent)" }}
          icon={<UserOutlined />}
        />
        <Typography.Text style={{ maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {user.display_name || user.username}
        </Typography.Text>
      </Space>
    </Dropdown>
    <ProfileModal open={profileOpen} onClose={() => setProfileOpen(false)} />
    </>
  );
}
