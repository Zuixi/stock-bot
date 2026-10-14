import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { changePassword } from "@/shared/api/auth";
import { useAuth } from "@/features/auth/context";

/**
 * 强制改密页（ADR 0010 五段门控的第五段落点）。
 * 临时密码会话只能到这里：/api/* 与多数 /auth/* 均 403。
 */
export default function ChangePasswordPage() {
  const [form] = Form.useForm();
  const navigate = useNavigate();
  const { refetchUser } = useAuth();

  useEffect(() => {
    document.title = "设置新密码 · Stock Bot";
  }, []);

  const onFinish = async (values: {
    current_password: string;
    new_password: string;
  }) => {
    try {
      await changePassword(values);
      message.success("密码已修改，其他设备已退出登录");
      await refetchUser();
      navigate("/", { replace: true });
    } catch {
      // ApiError message 已由全局处理展示
    }
  };

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "flex-start",
        paddingTop: "12vh",
        minHeight: "calc(100vh - 200px)",
      }}
    >
      <Card style={{ width: "100%", maxWidth: 400 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          设置新密码
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          修改后其他设备将退出登录。
        </Typography.Paragraph>
        <Form form={form} layout="vertical" onFinish={onFinish} requiredMark="optional">
          <Form.Item
            name="current_password"
            label="当前（临时）密码"
            rules={[{ required: true, message: "请输入当前密码" }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 8, message: "至少 8 位" },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm"
            label="确认新密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, v) {
                  return v === getFieldValue("new_password")
                    ? Promise.resolve()
                    : Promise.reject(new Error("两次输入不一致"));
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            完成修改并继续
          </Button>
        </Form>
      </Card>
    </div>
  );
}
