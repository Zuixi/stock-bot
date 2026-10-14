import { useMemo, useState } from "react";
import { Form, Input, Modal, Select, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listRoles, provisionUser, type ProvisionResult } from "@/shared/api/admin";
import { InitialPasswordModal } from "./InitialPasswordModal";

/** 开户弹窗：角色必选（ADR 0010 开户即授权），成功后接一次性密码展示。 */
export function ProvisionUserModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm();
  const [passwordResult, setPasswordResult] = useState<ProvisionResult | null>(null);
  const queryClient = useQueryClient();

  const { data: roles } = useQuery({
    queryKey: ["admin", "roles"],
    queryFn: () => listRoles(),
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });

  const { isPending, mutate } = useMutation({
    mutationFn: (payload: { username: string; email: string; display_name?: string; roles: string[] }) =>
      provisionUser(payload),
    onSuccess: (result) => {
      message.success(`已为 ${result.user.username} 开户`);
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      // 临时密码交接交给一次性弹窗；本弹窗复位供连续开户
      setPasswordResult(result);
      form.resetFields();
    },
    onError: () => message.error("开户失败，请检查填写与角色"),
  });

  const roleOptions = useMemo(
    () =>
      (roles ?? []).map((r) => ({
        value: r.id,
        label: `${r.name}（${r.id}）`,
      })),
    [roles]
  );

  return (
    <>
    <Modal
      title="开户"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="开户并生成临时密码"
      confirmLoading={isPending}
      destroyOnClose
    >
      <Form form={form} layout="vertical" onFinish={(v) => mutate(v)} requiredMark="optional">
        <Form.Item
          name="username"
          label="用户名"
          rules={[
            { required: true, message: "请输入用户名" },
            { pattern: /^[a-zA-Z0-9_-]{3,64}$/, message: "3-64 位字母/数字/下划线/中划线" },
          ]}
        >
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="email"
          label="邮箱"
          rules={[
            { required: true, message: "请输入邮箱" },
            { type: "email", message: "邮箱格式不正确" },
          ]}
        >
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item name="display_name" label="显示名（可选）">
          <Input />
        </Form.Item>
        <Form.Item
          name="roles"
          label="角色（必选）"
          rules={[{ required: true, message: "开户必须指定至少一个角色" }]}
        >
          <Select mode="multiple" options={roleOptions} placeholder="选择该账号的角色" />
        </Form.Item>
      </Form>
    </Modal>
    <InitialPasswordModal result={passwordResult} onClose={() => setPasswordResult(null)} />
    </>
  );
}
