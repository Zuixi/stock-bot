import { Spin, Empty, Button, Result } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";

interface Props {
  loading?: boolean;
  error?: Error | null;
  empty?: boolean;
  emptyText?: string;
  onRetry?: () => void;
  children: ReactNode;
}

export function StateWrapper({ loading, error, empty, emptyText, onRetry, children }: Props) {
  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return (
      <Result
        status="error"
        title="数据加载失败"
        subTitle={error.message}
        extra={
          onRetry && (
            <Button icon={<ReloadOutlined />} onClick={onRetry}>
              重试
            </Button>
          )
        }
      />
    );
  }

  if (empty) {
    return <Empty description={emptyText ?? "暂无数据"} />;
  }

  return <>{children}</>;
}
