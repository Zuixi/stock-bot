import React, { useState } from "react";
import { Card, Tabs, Form, Input, Button, Alert, Typography, message } from "antd";
import { UserOutlined, LockOutlined, MailOutlined, IdcardOutlined } from "@ant-design/icons";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login, register, type UserLoginPayload, type UserRegisterPayload } from "@/shared/api/auth";
import { useAuth } from "@/features/auth/context";
import { ApiError } from "@/shared/api/client";

export default function LoginPage() {
  const [activeTab, setActiveTab] = useState<"login" | "register">("login");
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [registerSuccess, setRegisterSuccess] = useState(false);
  const [loginForm] = Form.useForm();
  const [registerForm] = Form.useForm();

  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { refetchUser } = useAuth();

  const returnTo = searchParams.get("returnTo") || "/";

  const handleLogin = async (values: UserLoginPayload) => {
    setErrorMsg(null);
    setLoading(true);
    try {
      await login(values);
      message.success("登录成功");
      await refetchUser();
      navigate(returnTo, { replace: true });
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setErrorMsg(err.message || "登录失败，请检查账号密码");
      } else {
        setErrorMsg("登录时发生未知错误，请重试");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (values: UserRegisterPayload) => {
    setErrorMsg(null);
    setLoading(true);
    try {
      await register(values);
      message.success("注册成功，请使用新账号登录");
      setRegisterSuccess(true);
      registerForm.resetFields();
      setActiveTab("login");
      loginForm.setFieldsValue({ username_or_email: values.username });
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setErrorMsg(err.message || "注册失败，请检查填写内容");
      } else {
        setErrorMsg("注册时发生未知错误，请重试");
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
          <Typography.Title level={3} style={{ margin: 0, color: "#1677ff" }}>
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

        {registerSuccess && activeTab === "login" && (
          <Alert
            message="账号注册成功，请登录"
            type="success"
            showIcon
            closable
            onClose={() => setRegisterSuccess(false)}
            style={{ marginBottom: 16 }}
          />
        )}

        <Tabs
          activeKey={activeTab}
          onChange={(k) => {
            setActiveTab(k as "login" | "register");
            setErrorMsg(null);
          }}
          centered
          items={[
            {
              key: "login",
              label: "用户登录",
              children: (
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

                  <Form.Item
                    name="password"
                    rules={[{ required: true, message: "请输入密码" }]}
                  >
                    <Input.Password
                      prefix={<LockOutlined style={{ color: "#bfbfbf" }} />}
                      placeholder="密码"
                      size="large"
                    />
                  </Form.Item>

                  <Form.Item style={{ marginBottom: 8, marginTop: 24 }}>
                    <Button
                      type="primary"
                      htmlType="submit"
                      size="large"
                      block
                      loading={loading}
                    >
                      登录
                    </Button>
                  </Form.Item>
                </Form>
              ),
            },
            {
              key: "register",
              label: "新用户注册",
              children: (
                <Form
                  form={registerForm}
                  layout="vertical"
                  onFinish={handleRegister}
                  requiredMark={false}
                  autoComplete="off"
                >
                  <Form.Item
                    name="username"
                    rules={[
                      { required: true, message: "请输入用户名" },
                      { min: 3, max: 64, message: "用户名长度为 3-64 个字符" },
                      {
                        pattern: /^[a-zA-Z0-9_-]+$/,
                        message: "用户名仅支持字母、数字、下划线及减号",
                      },
                    ]}
                  >
                    <Input
                      prefix={<UserOutlined style={{ color: "#bfbfbf" }} />}
                      placeholder="用户名 (英文字符、数字)"
                      size="large"
                    />
                  </Form.Item>

                  <Form.Item
                    name="email"
                    rules={[
                      { required: true, message: "请输入邮箱地址" },
                      { type: "email", message: "请输入有效的邮箱格式" },
                    ]}
                  >
                    <Input
                      prefix={<MailOutlined style={{ color: "#bfbfbf" }} />}
                      placeholder="电子邮箱"
                      size="large"
                    />
                  </Form.Item>

                  <Form.Item name="display_name">
                    <Input
                      prefix={<IdcardOutlined style={{ color: "#bfbfbf" }} />}
                      placeholder="显示昵称 (选填)"
                      size="large"
                    />
                  </Form.Item>

                  <Form.Item
                    name="password"
                    rules={[
                      { required: true, message: "请输入密码" },
                      { min: 8, message: "密码至少需要 8 个字符" },
                    ]}
                  >
                    <Input.Password
                      prefix={<LockOutlined style={{ color: "#bfbfbf" }} />}
                      placeholder="密码 (至少 8 个字符)"
                      size="large"
                    />
                  </Form.Item>

                  <Form.Item style={{ marginBottom: 8, marginTop: 24 }}>
                    <Button
                      type="primary"
                      htmlType="submit"
                      size="large"
                      block
                      loading={loading}
                    >
                      立即注册
                    </Button>
                  </Form.Item>
                </Form>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
