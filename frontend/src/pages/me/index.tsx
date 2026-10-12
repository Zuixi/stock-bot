import { Tabs, Typography } from "antd";
import { StarOutlined, TagsOutlined, FundOutlined } from "@ant-design/icons";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { PlaceholderPage } from "@/shared/ui";
import { TagsPanel, WatchlistTable } from "@/features/watchlist/components";

/**
 * `/me`：我的（顶栏全局工具的目标页，IA 文档 §3；权限 = 登录即可，沿用既有 `RequireAuth`）。
 *
 * 承载三个 tab，query 契约逐字照抄 IA 文档 §4：
 * - `?tab=watchlist`（默认）—— 自选：**复用既有 `WatchlistTable`**，不重写自选能力
 * - `?tab=tags`             —— 标签总览（骨架占位）
 * - `?tab=tags&name=:tagName` —— 该标签下的个股列表：**复用既有 `TagsPanel`**
 *                              （与 `/tags/:tagName` 同一实现，redirect 后行为逐字一致）
 * - `?tab=industry`         —— 我的行业：占位（本任务只交付骨架）
 *
 * 这页是 redirect 目标，**必须保留 query 参数**：三个 tab 变体都要能渲染。
 * 单一真相 = URL：tab 高亮完全由 `?tab=` 决定，点击只改 URL，不另存 state。
 */
type MeTab = "watchlist" | "tags" | "industry";

const TAB_KEYS: MeTab[] = ["watchlist", "tags", "industry"];

export default function MePage() {
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();

  const tabParam = searchParams.get("tab");
  const activeTab: MeTab = TAB_KEYS.includes(tabParam as MeTab) ? (tabParam as MeTab) : "watchlist";
  const tagName = searchParams.get("name") ?? "";

  const onTabChange = (key: string) => {
    const next = key as MeTab;
    const nextParams = new URLSearchParams();
    nextParams.set("tab", next);
    if (next === "tags" && tagName) nextParams.set("name", tagName);
    navigate(`${location.pathname}?${nextParams.toString()}`);
  };

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        我的
      </Typography.Title>
      <Tabs
        activeKey={activeTab}
        onChange={onTabChange}
        items={[
          {
            key: "watchlist",
            label: (
              <span>
                <StarOutlined /> 自选
              </span>
            ),
            children: <WatchlistTable />,
          },
          {
            key: "tags",
            label: (
              <span>
                <TagsOutlined /> 标签
              </span>
            ),
            children: tagName ? (
              // 带 name 时直接落该标签的个股列表（/tags/:tagName 的 redirect 目标）
              <TagsPanel tagName={tagName} />
            ) : (
              <PlaceholderPage
                title="标签"
                hint="标签总览挂在 /me?tab=tags 下尚未落地；既有 /tags 列表页仍可访问，个股列表见 /me?tab=tags&name=<标签名>。"
              />
            ),
          },
          {
            key: "industry",
            label: (
              <span>
                <FundOutlined /> 我的行业
              </span>
            ),
            children: (
              <PlaceholderPage title="我的行业" hint="我的行业（关注行业列表）尚未落地，本页为骨架占位。" />
            ),
          },
        ]}
      />
    </div>
  );
}
