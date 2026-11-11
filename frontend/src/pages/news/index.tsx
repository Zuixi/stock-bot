import { Empty, Tabs, Typography } from "antd";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ContentList } from "@/features/content/ContentList";

/**
 * `/news`：资讯（IA 文档 §2「资讯」的四个子模块：公告 / 政策 / 研报 / 快讯）。
 *
 * **单一真相 = URL**：tab 高亮完全由 `?tab=` 决定，点击只改 URL，不另存 state
 * （与 `/me`、`/market` 同一口径；深链可分享、可刷新、可后退）。
 * 默认 tab 是 `announcements`（公告）。公告与研报（Plan 2）已接真实数据管道；
 * 政策 / 快讯仍在 Wave 2 的后续计划里落地。这里**不造假数据、也不写成"规划中"占位**——
 * 造数据会让用户以为今天没有政策，而真相是这个读面还不存在；
 * 占位页文案（`PlaceholderNotice`）又会被读成"整个资讯模块没做完"，
 * 故逐 tab 给出「该类别尚未接入」，并指明是哪一批计划补上。
 */

type NewsTab = "announcements" | "policy" | "research" | "flash";

const TAB_KEYS: NewsTab[] = ["announcements", "policy", "research", "flash"];

const DEFAULT_TAB: NewsTab = "announcements";

/** 未接入 tab 的统一空态文案（与 Wave 2 计划批次对应，别在这里含糊成"敬请期待"）。 */
const UNWIRED_TEXT = "该类别尚未接入（Wave 2 Plan 3–4）";

export default function NewsPage() {
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();

  const tabParam = searchParams.get("tab");
  const activeTab: NewsTab = TAB_KEYS.includes(tabParam as NewsTab)
    ? (tabParam as NewsTab)
    : DEFAULT_TAB;

  const onTabChange = (key: string) => {
    const nextParams = new URLSearchParams();
    nextParams.set("tab", key);
    navigate(`${location.pathname}?${nextParams.toString()}`);
  };

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        资讯 · 带状态、可溯源的内容流
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        每条内容都可以顺着标题回到 <strong>原文</strong>
        （公告存 URL、不存正文，PDF 在交易所侧）。已读 / 忽略按当前用户独立记录，匿名访问时不显示状态按钮。
      </Typography.Paragraph>
      <Tabs
        activeKey={activeTab}
        onChange={onTabChange}
        items={[
          {
            key: "announcements",
            label: "公告",
            children: <ContentList contentType="announcement" />,
          },
          {
            key: "policy",
            label: "政策",
            children: <Empty description={UNWIRED_TEXT} />,
          },
          {
            key: "research",
            label: "研报",
            children: <ContentList contentType="report" />,
          },
          {
            key: "flash",
            label: "快讯",
            children: <Empty description={UNWIRED_TEXT} />,
          },
        ]}
      />
    </div>
  );
}
