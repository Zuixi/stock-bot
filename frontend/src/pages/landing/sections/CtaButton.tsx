import { Button } from "antd";
import { ArrowRightOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { useLandingCta } from "../useLandingCta";

interface Props {
  testId?: string;
  size?: "middle" | "large";
}

/**
 * 宣传页统一主 CTA：已登录「进入工作台」→ /market，未登录「免费开始」→ /login。
 * 会话探测（isAuthReady）完成前隐藏文字，避免登录用户看到错标签闪现，按钮位保留防布局跳动。
 */
export function CtaButton({ testId, size = "middle" }: Props) {
  const { to, label, ready } = useLandingCta();
  const navigate = useNavigate();

  return (
    <Button
      type="primary"
      size={size}
      data-testid={testId}
      icon={<ArrowRightOutlined />}
      iconPosition="end"
      style={ready ? undefined : { visibility: "hidden" }}
      onClick={() => navigate(to)}
    >
      {label}
    </Button>
  );
}
