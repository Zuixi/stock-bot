import { List, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchAnnouncements } from "@/shared/api/marketData";

const CATEGORY_META: Record<string, { label: string; color: string }> = {
  report: { label: "财报", color: "blue" },
  event: { label: "事项", color: "orange" },
};

export function AnnouncementFeed() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["announcements"],
    queryFn: () => fetchAnnouncements(undefined, 30),
    staleTime: 5 * 60 * 1000,
  });
  return (
    <List
      size="small"
      loading={isLoading}
      dataSource={data}
      style={{ maxHeight: 360, overflowY: "auto" }}
      locale={{ emptyText: "暂无公告快讯" }}
      renderItem={(a) => {
        const meta = CATEGORY_META[a.category] ?? { label: a.category, color: "default" };
        return (
          <List.Item style={{ padding: "6px 0" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", minWidth: 0 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12, flexShrink: 0 }}>
                {a.announceTime.slice(5, 16).replace("T", " ")}
              </Typography.Text>
              <Tag color={meta.color} style={{ marginRight: 0, flexShrink: 0 }}>{meta.label}</Tag>
              <Typography.Text style={{ fontSize: 13, flexShrink: 0 }}>{a.secName ?? a.secCode}</Typography.Text>
              <a href={a.pdfUrl ?? undefined} target="_blank" rel="noreferrer" style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {a.title}
              </a>
            </div>
          </List.Item>
        );
      }}
    />
  );
}
