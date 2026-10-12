import { Space, Typography } from "antd";

/**
 * 「规划中」通告条（占位路由的统一标记）。
 *
 * **由路由清单注入**（见 `app/router/routes.tsx` 的 `placeholderNotice`）：某条路由在 `ROUTES` 里
 * 标了 `isPlaceholder: true`，它的页面就一定会渲染本组件——判定不再依赖"页面文件里有没有某个 import"
 * 或"页面自己的文件里有没有某个字符串"（Ruling 33：那两种推断都出过假绿）。
 *
 * `data-testid="placeholder-route-params"` 是**对外可见的行为标记**：运行时验证（Playwright）
 * 与人工核对都以它为准。
 */
export function PlaceholderNotice({
  title,
  hint,
}: {
  /** 模块名（逐字取自 IA 文档 §1） */
  title: string;
  /** 该模块的一行说明；缺省用通用措辞 */
  hint?: string;
}) {
  return (
    <div data-testid="placeholder-route-params" style={{ textAlign: "center" }}>
      <Space direction="vertical" size={4}>
        <Typography.Text strong>规划中</Typography.Text>
        <Typography.Text type="secondary">
          {hint ?? `${title}模块尚未落地，页面结构已就位（本页为占位路由，无 404）。`}
        </Typography.Text>
      </Space>
    </div>
  );
}
