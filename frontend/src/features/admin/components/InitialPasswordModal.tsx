import { Button, Modal, Typography } from "antd";
import type { ProvisionResult } from "@/shared/api/admin";

/**
 * 一次性初始密码展示（受控组件）。
 * 密码后端只返回这一次；关闭本弹窗后不再可见。
 */
export function InitialPasswordModal({
  result,
  onClose,
}: {
  result: ProvisionResult | null;
  onClose: () => void;
}) {
  if (!result) return null;
  const expires = new Date(result.expires_at).toLocaleString();

  return (
    <Modal
      title="初始密码（仅显示这一次）"
      open
      onCancel={onClose}
      footer={
        <Button type="primary" onClick={onClose}>
          我已转交，关闭
        </Button>
      }
      closable={false}
      maskClosable={false}
    >
      <Typography.Paragraph>
        账号 <Typography.Text strong>{result.user.username}</Typography.Text> 的初始密码：
      </Typography.Paragraph>
      <Typography.Paragraph copyable={{ text: result.initial_password }}>
        <code style={{ fontSize: 16, padding: "4px 8px", background: "var(--bg-panel)" }}>
          {result.initial_password}
        </code>
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        {expires} 前有效；首次登录时必须修改。
      </Typography.Paragraph>
    </Modal>
  );
}
