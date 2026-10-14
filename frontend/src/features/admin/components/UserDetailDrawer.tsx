import { useState } from "react";
import { Descriptions, Drawer, Modal, Space, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  reactivateUser,
  resetPassword,
  suspendUser,
  type AdminUser,
  type ProvisionResult,
} from "@/shared/api/admin";
import { InitialPasswordModal } from "./InitialPasswordModal";
import { RoleAssignModal } from "./RoleAssignModal";
import { RoleTags, StatusTag } from "./StatusTag";

/** 用户详情抽屉：画像 + 角色 + 生命周期操作（含改角色）。 */
export function UserDetailDrawer({
  user,
  onClose,
}: {
  user: AdminUser | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [passwordResult, setPasswordResult] = useState<ProvisionResult | null>(null);
  const [roleModalOpen, setRoleModalOpen] = useState(false);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin", "users"] });

  const suspend = useMutation({
    mutationFn: () => suspendUser(user!.id),
    onSuccess: () => {
      message.success("已停用，其所有会话已退出");
      invalidate();
    },
    onError: () => message.error("停用失败"),
  });

  const reactivate = useMutation({
    mutationFn: () => reactivateUser(user!.id),
    onSuccess: () => {
      message.success("已重新启用");
      invalidate();
    },
    onError: () => message.error("启用失败"),
  });

  const reset = useMutation({
    mutationFn: () => resetPassword(user!.id),
    onSuccess: (r) => {
      invalidate();
      setPasswordResult(r);
    },
    onError: () => message.error("重置失败"),
  });

  if (!user) return null;

  const confirmSuspend = () => {
    Modal.confirm({
      title: `停用 ${user.username}？`,
      content: "停用后该用户立即无法登录，所有设备会退出。",
      okText: "停用",
      okType: "danger",
      cancelText: "取消",
      onOk: () => suspend.mutate(),
    });
  };

  return (
    <>
      <Drawer title={user.username} open onClose={onClose} width={420}>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="状态">
            <StatusTag user={user} />
          </Descriptions.Item>
          <Descriptions.Item label="显示名">{user.display_name || "—"}</Descriptions.Item>
          <Descriptions.Item label="邮箱">{user.email}</Descriptions.Item>
          <Descriptions.Item label="角色">
            <RoleTags roles={user.roles} />
          </Descriptions.Item>
          <Descriptions.Item label="最近登录">
            {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : "从未登录"}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {new Date(user.created_at).toLocaleString()}
          </Descriptions.Item>
          {user.status_reason && (
            <Descriptions.Item label="停用原因">{user.status_reason}</Descriptions.Item>
          )}
        </Descriptions>
        <Space style={{ marginTop: 16, width: "100%", justifyContent: "flex-start" }}>
          {user.status === "active" && (
            <a onClick={confirmSuspend} style={{ color: "var(--up)" }}>
              停用
            </a>
          )}
          {user.status === "suspended" && (
            <a onClick={() => reactivate.mutate()} style={{ color: "var(--down)" }}>
              重新启用
            </a>
          )}
          <a onClick={() => setRoleModalOpen(true)}>改角色</a>
          <a onClick={() => reset.mutate()}>重置密码</a>
        </Space>
      </Drawer>
      <InitialPasswordModal result={passwordResult} onClose={() => setPasswordResult(null)} />
      {roleModalOpen && (
        <RoleAssignModal user={user} onClose={() => setRoleModalOpen(false)} />
      )}
    </>
  );
}
