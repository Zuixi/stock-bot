import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Spin, Result, Button, Space } from "antd";
import { useAuth } from "../context";
import { useAuthStore } from "../store";

interface RequireAuthProps {
  children: React.ReactNode;
  roles?: string[];
  permissions?: string[];
}

export function RequireAuth({ children, roles, permissions }: RequireAuthProps) {
  const location = useLocation();
  const { isAuthenticated, isAuthReady, sessionError, retrySession } = useAuth();
  const { hasRole, hasPermission } = useAuthStore();

  if (!isAuthReady) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "50vh" }}>
        <Spin size="large" tip="正在验证身份..." />
      </div>
    );
  }

  // 登录态未知（会话探测遇 429/5xx/网络故障）且无上一次已知用户。
  // 绝不能按"未登录"跳登录页——那会把一次网络抖动画成"被踢下线"，
  // 而且登录页同样依赖后端，跳过去也解决不了问题。给可重试的提示。
  if (!isAuthenticated && sessionError) {
    return (
      <Result
        status="warning"
        title="无法确认登录状态"
        subTitle="网络或服务暂时不可用，请稍后重试。"
        extra={
          <Space>
            <Button type="primary" onClick={retrySession}>
              重试
            </Button>
            <Button onClick={() => window.location.assign("/login")}>重新登录</Button>
          </Space>
        }
      />
    );
  }

  if (!isAuthenticated) {
    const returnTo = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?returnTo=${returnTo}`} replace />;
  }

  if (roles && roles.length > 0 && !hasRole(roles)) {
    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，您暂无访问该资源的权限。"
        extra={
          <Button type="primary" onClick={() => window.history.back()}>
            返回上一页
          </Button>
        }
      />
    );
  }

  if (permissions && permissions.length > 0 && !hasPermission(permissions)) {
    return (
      <Result
        status="403"
        title="403"
        subTitle="抱歉，您缺少必要的操作权限。"
        extra={
          <Button type="primary" onClick={() => window.history.back()}>
            返回上一页
          </Button>
        }
      />
    );
  }

  return <>{children}</>;
}
