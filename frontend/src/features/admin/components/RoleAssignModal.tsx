import { useEffect, useMemo, useState } from "react";
import { Modal, Select, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listRoles, replaceRoles, type AdminUser } from "@/shared/api/admin";

/**
 * 改角色弹窗：替换式（后端 PUT 语义，吊销该用户全部会话）。
 * 这是「角色分配不再改 SQL」的落点——开户时定角色，事后在这里改。
 */
export function RoleAssignModal({
  user,
  onClose,
}: {
  user: AdminUser | null;
  onClose: () => void;
}) {
  const [roles, setRoles] = useState<string[]>([]);
  const queryClient = useQueryClient();

  useEffect(() => {
    setRoles(user?.roles ?? []);
  }, [user]);

  const { data: allRoles } = useQuery({
    queryKey: ["admin", "roles"],
    queryFn: () => listRoles(),
    enabled: !!user,
    staleTime: 5 * 60 * 1000,
  });

  const options = useMemo(
    () => (allRoles ?? []).map((r) => ({ value: r.id, label: `${r.name}（${r.id}）` })),
    [allRoles]
  );

  const { isPending, mutate } = useMutation({
    mutationFn: () => replaceRoles(user!.id, roles),
    onSuccess: () => {
      message.success("角色已更新，该用户需重新登录");
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      onClose();
    },
    onError: () => message.error("角色更新失败"),
  });

  if (!user) return null;

  return (
    <Modal
      title={`修改 ${user.username} 的角色`}
      open
      onCancel={onClose}
      onOk={() => mutate()}
      okText="保存角色"
      confirmLoading={isPending}
      okButtonProps={{ disabled: roles.length === 0 }}
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        替换式：未勾选的角色会被移除。保存后该用户的所有设备会退出登录。
      </Typography.Paragraph>
      <Select
        mode="multiple"
        style={{ width: "100%" }}
        value={roles}
        onChange={setRoles}
        options={options}
        placeholder="选择角色（至少一个）"
      />
    </Modal>
  );
}
