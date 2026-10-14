import React, { useState } from "react";
import { Card, Form, Input, Button, Alert, Typography } from "antd";
import { UserOutlined, LockOutlined } from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login, type UserLoginPayload } from "@/shared/api/auth";
import { useAuth } from "@/features/auth/context";
import { ApiError } from "@/shared/api/client";

/**
 * 邀请制登录页（ADR 0010）：注册入口已移除，账号由管理员开户。
 * 登录拒绝文案区分三种具体原因（仅密码正确时后端才返回它们）。
 */
export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [loginForm] = Form.useForm();

  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { refetchUser } = useAuth();

  const returnTo = searchParams.get("returnTo") || "/";

  const handleLogin = async (values: UserLoginPayload) => {
    setErrorMsg(null);
    setLoading(true);
    try {
      await login(values);
      await refetchUser();
      navigate(returnTo, { replace: true });
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        // 具体拒绝码（后端只在密码正确时返回）
        if (err.code === "AUTH_ACCOUNT_SUSPENDED") {
          setErrorMsg(err.message || "账号已被停用，请联系管理员");
        } else if (err.code === "AUTH_TEMP_PASSWORD_EXPIRED") {
          setErrorMsg("临时密码已过期，请联系管理员重新签发");
        } else {
          setErrorMsg(err.message || "登录失败，请检查账号密码");
        }
      } else {
        setErrorMsg("登录时发生未知错误，请重试");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "calc(100vh - 200px)",
      }}
    >
      <Card
        style={{
          width: "100%",
          maxWidth: 420,
          boxShadow: "0 4px 12px rgba(0, 0, 0, 0.08)",
          borderRadius: 8,
        }}
      >
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <Typography.Title level={3} style={{ margin: 0, color: "var(--accent)" }}>
            Stock Bot 投研工作台
          </Typography.Title>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            专业的量化选股与行业研究分析平台
          </Typography.Text>
        </div>

        {errorMsg && (
          <Alert
            message={errorMsg}
            type="error"
            showIcon
            closable
            onClose={() => setErrorMsg(null)}
            style={{ marginBottom: 16 }}
          />
        )}

        <Form
          form={loginForm}
          layout="vertical"
          onFinish={handleLogin}
          requiredMark={false}
          autoComplete="off"
        >
          <Form.Item
            name="username_or_email"
            rules={[{ required: true, message: "请输入用户名或邮箱" }]}
          >
            <Input
              prefix={<UserOutlined style={{ color: "#bfbfbf" }} />}
              placeholder="用户名 / 邮箱"
              size="large"
            />
          </Form.Item>

          <Form.Item name="password" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: "#bfbfbf" }} />}
              placeholder="密码"
              size="large"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 8, marginTop: 24 }}>
            <Button type="primary" htmlType="submit" size="large" block loading={loading}>
              登录
            </Button>
          </Form.Item>
        </Form>

        <Typography.Paragraph
          type="secondary"
          style={{ fontSize: 12, textAlign: "center", marginBottom: 0 }}
        >
          本系统采用邀请制，账号由管理员开通。忘记密码或需要开户，请联系管理员。
        </Typography.Paragraph>
      </Card>
    </div>
  );
}
