import { Tabs, Typography } from "antd";
import { type ReactNode } from "react";
import {
  StarOutlined,
  TagsOutlined,
  FundOutlined,
  SafetyCertificateOutlined,
  BookOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { PlaceholderPage } from "@/shared/ui/PlaceholderPage";
import { fetchJournalSummary, summaryCount } from "@/shared/api/journal";
import { fetchTriageBadge, triageHitCount } from "@/shared/api/triage";
import { TagsOverview, TagsPanel, WatchlistPanel } from "@/features/watchlist/components";
import { TriagePanel } from "@/features/triage/components/TriagePanel";
import { JournalPanel } from "@/features/journal/components/JournalPanel";
import "./index.css";

/**
 * `/me`：我的（顶栏全局工具的目标页，IA 文档 §3；权限 = 登录即可，沿用既有 `RequireAuth`）。
 *
 * 承载五个 tab，query 契约逐字照抄 IA 文档 §4（**Ruling 31：已建成的能力一律保留，不许用占位覆盖**）：
 * - `?tab=triage`（**默认**）—— 今日体检：自选「今天哪几只有事、是什么事、怎么复核」的事实清单
 *                              （S8；**不新增路由**：`/today` 推迟到 v2，见 spec §S8.1 与 Task 7）
 * - `?tab=watchlist`        —— 自选：`WatchlistPanel`（原 `pages/watchlist` 内容提取，
 *                              含「我的自选（N）」标题与「清空」按钮——`WatchlistTable` 自己没有清空）
 * - `?tab=tags`             —— 标签总览：`TagsOverview`（原 `pages/tags` 内容提取，含每组个股数）
 * - `?tab=tags&name=:tagName` —— 该标签下的个股列表：`TagsPanel`
 *                              （与 `/tags/:tagName` 同一实现，redirect 后行为逐字一致）
 * - `?tab=journal`          —— 决策日志（S9）：记一笔 / 待复盘 / 已复盘（含归因与统计）。
 *                              **不新增路由**，也不改 `/market`——入口就是这个 tab。
 * - `?tab=industry`         —— 我的行业：占位（本任务只交付骨架）
 *
 * 默认 tab 从 `watchlist` 改为 `triage` 是 S8 的入口决策（体检在自选之前：先看"今天有没有事"，
 * 再管清单本身）。**旧 redirect 逐字不变**：`/watchlist` → `/me?tab=watchlist`，
 * 故既有深链与 e2e（`userIsolation.spec.ts` 断言「我的自选（N）」）行为不受影响。
 *
 * 这页是 redirect 目标，**必须保留 query 参数**：各 tab 变体都要能渲染。
 * 单一真相 = URL：tab 高亮完全由 `?tab=` 决定，点击只改 URL，不另存 state。
 */
type MeTab = "triage" | "watchlist" | "tags" | "journal" | "industry";

const TAB_KEYS: MeTab[] = ["triage", "watchlist", "tags", "journal", "industry"];

const DEFAULT_TAB: MeTab = "triage";

/**
 * Tab 标题 + 角标（spec §S12.2）。
 *
 * 角标的数字是**事实**（今天有几条判断到期 / 今日体检命中几条），不是未读：没有"点过就
 * 消失"的语义，故不带已读状态，也不靠颜色/动画说话——数字本身就是全部信息。
 *
 * 渲染规则只有一条：**`null`（取不到）与 `0`（确实没有）都不显示数字**。
 * 两者在视觉上合并，但在数据层绝不合并（`summaryCount` / `triageHitCount` 把"取不到"
 * 返回成 `null`）——否则一旦兜成 0，页面就再也没法区分"今天没事"与"今天不知道"，
 * 而这正是 spec §S12.5 要求守住的那条线。
 */
function TabLabel({
  icon,
  text,
  count,
  testId,
  title,
}: {
  icon: ReactNode;
  text: string;
  count: number | null;
  testId: string;
  title: string;
}) {
  return (
    <span className="me-tab">
      {icon} {text}
      {count !== null && count > 0 && (
        <span className="me-tab__badge num" data-testid={testId} title={`${title} ${count}`}>
          {count}
        </span>
      )}
    </span>
  );
}

export default function MePage() {
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();

  const tabParam = searchParams.get("tab");
  const activeTab: MeTab = TAB_KEYS.includes(tabParam as MeTab)
    ? (tabParam as MeTab)
    : DEFAULT_TAB;
  const tagName = searchParams.get("name") ?? "";

  // 到期数（「决策日志」角标 + triage 顶部那行的同一个读面）：`fetchJournalSummary` 自带
  // `skipAuth`，故后端这个新端点还没上线 / 失败时只是**不显示角标**，不影响登录态。
  const summaryQuery = useQuery({
    queryKey: ["journal", "summary"],
    queryFn: fetchJournalSummary,
    staleTime: 60_000,
    retry: false,
  });
  const dueCount = summaryCount(summaryQuery.data, "due");

  /**
   * 今日体检命中数（「今日体检」角标）。
   *
   * 与 `TriagePanel` **共用 `["triage"]` 这个 cache key**（同一个读面、同一份数据），
   * 但只在**不在**体检 tab 时自己发请求：体检 tab 上 `TriagePanel` 已经在取，
   * 再发一次就是同一个聚合接口打两遍。反过来在日志 tab 上没人取，角标就自己取一份。
   *
   * `fetchTriageBadge` 是带 `skipAuth` 的同一接口：角标是装饰性读面，它失败不该触发全局
   * 清态（`decisionJournal` 这类只 mock journal 的用例里，triage 走的是真接口）。
   */
  const triageBadgeQuery = useQuery({
    queryKey: ["triage"],
    queryFn: fetchTriageBadge,
    enabled: activeTab !== "triage",
    staleTime: 30_000,
    retry: false,
  });
  const triageHits = triageHitCount(triageBadgeQuery.data);

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
            key: "triage",
            label: (
              <TabLabel
                icon={<SafetyCertificateOutlined />}
                text="今日体检"
                count={triageHits}
                testId="me-tab-badge-triage"
                title="今日命中"
              />
            ),
            children: <TriagePanel />,
          },
          {
            key: "watchlist",
            label: (
              <span>
                <StarOutlined /> 自选
              </span>
            ),
            children: <WatchlistPanel />,
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
              // Ruling 31：`/tags` 的标签总览在这里渲染（原 pages/tags 的内容提取为面板），
              // 不得用「规划中」占位覆盖已建成的能力
              <TagsOverview />
            ),
          },
          {
            key: "journal",
            label: (
              <TabLabel
                icon={<BookOutlined />}
                text="决策日志"
                count={dueCount}
                testId="me-tab-badge-journal"
                title="待复盘"
              />
            ),
            children: <JournalPanel />,
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
