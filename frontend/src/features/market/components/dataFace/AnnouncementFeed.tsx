import { List, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchAnnouncements } from "@/shared/api/marketData";
import { fmtRelativeTime } from "../format";

const CATEGORY_META: Record<string, { label: string; color: string }> = {
  report: { label: "财报", color: "blue" },
  event: { label: "事项", color: "orange" },
};

export interface AnnouncementFeedProps {
  /** 只展示该类别（首页快讯 Tab）；不传则混合展示（/market 数据面） */
  category?: "report" | "event";
  /** 拉取条数，默认 30（后端上限 100） */
  fetchLimit?: number;
  /** 过滤后最多展示条数；不传则不过滤条数上限 */
  maxRows?: number;
  /** 时间展示：absolute（默认 MM-DD HH:mm）/ relative（「2小时前」） */
  timeMode?: "absolute" | "relative";
}

/**
 * 公告流（数据源：巨潮资讯网，经 `/market/announcements` 落库）。
 *
 * 首页快讯与 /market 数据面共用同一份后端数据，零新数据源——TuShare 三个新闻
 * 接口均为积分门槛，故不做伪新闻源。行版式：标题（1 行截断）+ 时间/类别/证券
 * + 右侧来源署名「巨潮」。
 */
export function AnnouncementFeed({
  category,
  fetchLimit = 30,
  maxRows,
  timeMode = "absolute",
}: AnnouncementFeedProps) {
  const { data = [], isLoading, isError } = useQuery({
    // category 只做客户端过滤，故 key 不含 category：首页两个 Tab 共享一次请求
    queryKey: ["announcements", fetchLimit],
    queryFn: () => fetchAnnouncements(undefined, fetchLimit),
    staleTime: 5 * 60 * 1000,
  });

  // 口径诚实：接口故障 ≠「确实没有公告」。故障走独立错误占位，绝不落回空态文案
  // （否则公开区块会在数据源宕机时对外宣称「无新闻」）。
  if (isError) {
    return (
      <div
        className="announcement-feed__error"
        style={{
          padding: "24px 0",
          textAlign: "center",
          color: "var(--text-secondary)",
          fontSize: 13,
        }}
      >
        公告快讯暂不可用，请稍后重试
      </div>
    );
  }

  const rows = (category ? data.filter((a) => a.category === category) : data).slice(
    0,
    maxRows ?? data.length,
  );

  return (
    <List
      size="small"
      loading={isLoading}
      dataSource={rows}
      className="announcement-feed"
      style={{ maxHeight: 360, overflowY: "auto" }}
      locale={{ emptyText: "暂无公告快讯" }}
      renderItem={(a) => {
        const meta = CATEGORY_META[a.category] ?? { label: a.category, color: "default" };
        const time =
          timeMode === "relative"
            ? fmtRelativeTime(a.announceTime)
            : a.announceTime.slice(5, 16).replace("T", " ");
        return (
          <List.Item style={{ padding: "6px 0" }} extra={<Tag style={{ marginRight: 0 }}>巨潮</Tag>}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <a
                href={a.pdfUrl ?? undefined}
                target="_blank"
                rel="noreferrer"
                title={a.title}
                style={{
                  fontSize: 13,
                  display: "block",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {a.title}
              </a>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 2 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {time}
                </Typography.Text>
                <Tag color={meta.color} style={{ marginRight: 0 }}>
                  {meta.label}
                </Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {a.secName ?? a.secCode}
                </Typography.Text>
              </div>
            </div>
          </List.Item>
        );
      }}
    />
  );
}
