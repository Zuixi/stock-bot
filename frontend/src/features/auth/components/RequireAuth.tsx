import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Spin, Result, Button } from "antd";
import { useAuth } from "../context";
import { useAuthStore } from "../store";

interface RequireAuthProps {
  children: React.ReactNode;
  roles?: string[];
  permissions?: string[];
}

export function RequireAuth({ children, roles, permissions }: RequireAuthProps) {
  const location = useLocation();
  const { isAuthenticated, isAuthReady } = useAuth();
  const { hasRole, hasPermission } = useAuthStore();

  if (!isAuthReady) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "50vh" }}>
        <Spin size="large" tip="正在验证身份..." />
      </div>
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
