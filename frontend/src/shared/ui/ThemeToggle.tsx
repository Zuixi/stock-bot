import { Button, Tooltip } from "antd";
import { MoonFilled, SunFilled } from "@ant-design/icons";
import { useTheme } from "@/app/theme-context";

/** 明暗主题切换按钮：放 MainLayout Header 右侧 / Landing 导航右侧（契约 §1） */
export function ThemeToggle() {
  const { mode, toggle } = useTheme();
  const dark = mode === "dark";
  return (
    <Tooltip title={dark ? "切换到浅色模式" : "切换到深色模式"}>
      <Button
        type="text"
        aria-label="切换主题"
        data-testid="theme-toggle"
        icon={dark ? <SunFilled /> : <MoonFilled />}
        onClick={toggle}
      />
    </Tooltip>
  );
}
