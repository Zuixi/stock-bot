import { useEffect } from "react";
import { Form, Input, Modal, Typography, message } from "antd";
import { useMutation } from "@tanstack/react-query";
import { updateProfile, type UpdateProfilePayload } from "@/shared/api/auth";
import { ApiError } from "@/shared/api/client";
import { useAuth } from "../context";

/**
 * 账号设置：改自己的用户名 / 邮箱 / 显示名（部分更新，留空即不改）。
 * 改用户名或邮箱会走服务端唯一性校验（占用则 409）。
 */
export function ProfileModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm<UpdateProfilePayload>();
  const { user, refetchUser } = useAuth();

  useEffect(() => {
    if (open && user) {
      form.setFieldsValue({
        username: user.username,
        email: user.email,
        display_name: user.display_name ?? "",
      });
    }
  }, [open, user, form]);

  const { isPending, mutate } = useMutation({
    mutationFn: (values: UpdateProfilePayload) => updateProfile(values),
    onSuccess: async () => {
      message.success("账号信息已更新");
      await refetchUser();
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        // 唯一性冲突要指明是哪个字段，否则用户只能猜
        form.setFields([
          {
            name: err.message.includes("邮箱") ? "email" : "username",
            errors: [err.message],
          },
        ]);
        return;
      }
      message.error(err instanceof ApiError ? err.message : "更新失败，请重试");
    },
  });

  return (
    <Modal
      title="账号设置"
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText="保存"
      confirmLoading={isPending}
      destroyOnClose
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        用户名与邮箱用于登录；显示名只影响界面展示。
      </Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={(v) => mutate(v)} requiredMark="optional">
        <Form.Item
          name="username"
          label="用户名"
          rules={[
            { required: true, message: "请输入用户名" },
            { pattern: /^[a-zA-Z0-9_-]{3,64}$/, message: "3-64 位字母/数字/下划线/中划线" },
          ]}
        >
          <Input autoComplete="username" />
        </Form.Item>
        <Form.Item
          name="email"
          label="邮箱"
          rules={[
            { required: true, message: "请输入邮箱" },
            { type: "email", message: "邮箱格式不正确" },
          ]}
        >
          <Input autoComplete="email" />
        </Form.Item>
        <Form.Item name="display_name" label="显示名">
          <Input maxLength={64} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
