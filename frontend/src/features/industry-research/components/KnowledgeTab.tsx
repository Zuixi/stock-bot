import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Col, Empty, Row, Spin } from "antd";
import {
  fetchIndustryKnowledge,
  type IndustryKnowledge,
  type KnowledgeOrg,
  type MindmapNode,
} from "@/shared/api/industryResearch";
import { EChart } from "@/shared/ui/EChart";
import { SourceBadge } from "@/features/industry-research/components/SourceBadge";

/** 机构图谱四分组：payload.group → 展示标题（顺序即后端 sort 顺序） */
const GROUPS: { key: string; title: string }[] = [
  { key: "官方", title: "官方基准" },
  { key: "协会", title: "行业协会" },
  { key: "数据平台", title: "数据平台" },
  { key: "期货", title: "期货市场" },
];

function OrgItem({ org }: { org: KnowledgeOrg }) {
  const url = org.urls?.[0];
  return (
    <div style={{ padding: "7px 0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {url ? (
          <a href={url} target="_blank" rel="noreferrer" style={{ fontSize: 13, fontWeight: 600 }}>
            {org.name}
          </a>
        ) : (
          <span style={{ fontSize: 13, fontWeight: 600 }}>{org.name}</span>
        )}
        <SourceBadge tier={org.tier} />
      </div>
      <div style={{ fontSize: 12, color: "#86909c", marginTop: 3, lineHeight: 1.6 }}>{org.desc}</div>
    </div>
  );
}

function mindmapOption(root: MindmapNode): Record<string, unknown> {
  return {
    tooltip: { trigger: "item", triggerOn: "mousemove" },
    series: [
      {
        type: "tree",
        data: [root],
        orient: "LR",
        left: 24,
        right: 240,
        top: 8,
        bottom: 8,
        symbol: "circle",
        symbolSize: 7,
        edgeShape: "curve",
        initialTreeDepth: -1,
        expandAndCollapse: true,
        itemStyle: { color: "#1677ff" },
        lineStyle: { color: "#c9d4e4", width: 1.2 },
        label: {
          position: "left",
          verticalAlign: "middle",
          align: "right",
          fontSize: 12.5,
          color: "#4e5969",
        },
        leaves: {
          label: {
            position: "right",
            verticalAlign: "middle",
            align: "left",
            fontSize: 12.5,
            color: "#1d2129",
          },
        },
        animationDuration: 400,
        animationDurationUpdate: 500,
      },
    ],
  };
}

function KnowledgeContent({ data }: { data: IndustryKnowledge }) {
  const hasContent =
    data.org.length > 0 || data.principle !== null || data.mindmap !== null;
  if (!hasContent) {
    return <Empty description="知识库内容待录入 — 该行业暂无机构图谱 / 原则 / 思维导图" />;
  }

  return (
    <Row gutter={[16, 16]}>
      {/* ── 机构图谱：四分组卡片（官方基准 / 行业协会 / 数据平台 / 期货市场） ── */}
      {GROUPS.map((g) => {
        const orgs = data.org.filter((o) => o.group === g.key);
        return (
          <Col xs={24} sm={12} xl={6} key={g.key}>
            <Card
              size="small"
              title={g.title}
              extra={
                <span style={{ fontSize: 11.5, color: "#86909c" }}>{orgs.length} 机构</span>
              }
            >
              {orgs.length ? (
                orgs.map((o) => <OrgItem key={o.name} org={o} />)
              ) : (
                <span style={{ fontSize: 12, color: "#c9cdd4" }}>待补充</span>
              )}
            </Card>
          </Col>
        );
      })}

      {/* ── 数据权威性使用原则 ── */}
      {data.principle && (
        <Col xs={24} lg={10}>
          <Card size="small" title={data.principle.title}>
            <ul
              style={{
                listStyle: "none",
                margin: 0,
                padding: 0,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              {data.principle.items.map((item, i) => (
                <li
                  key={i}
                  style={{ display: "flex", gap: 10, alignItems: "flex-start", lineHeight: 1.7 }}
                >
                  <span
                    style={{
                      flexShrink: 0,
                      width: 18,
                      height: 18,
                      borderRadius: 9,
                      background: "#e8f1ff",
                      color: "#1256c4",
                      fontSize: 11,
                      fontWeight: 600,
                      display: "inline-flex",
                      alignItems: "center",
                      justifyContent: "center",
                      marginTop: 2,
                    }}
                  >
                    {i + 1}
                  </span>
                  <span style={{ fontSize: 13, color: "#4e5969" }}>{item}</span>
                </li>
              ))}
            </ul>
          </Card>
        </Col>
      )}

      {/* ── 行业思维导图（EChart tree，节点可点击折叠/展开） ── */}
      {data.mindmap && (
        <Col xs={24} lg={14}>
          <Card
            size="small"
            title="行业思维导图"
            extra={
              <span style={{ fontSize: 11.5, color: "#86909c" }}>点击节点折叠 / 展开</span>
            }
          >
            <EChart option={mindmapOption(data.mindmap)} height={420} />
          </Card>
        </Col>
      )}
    </Row>
  );
}

/**
 * 行业知识库（P6）：机构图谱分组卡片 + 数据权威性原则 + EChart tree 思维导图。
 * 内容全部来自后端 industry_knowledge 内容表（迁移内 seed），组件零行业知识；
 * 挂在 antd Tabs 惰性 pane 下，首次激活才发起请求。
 */
export function KnowledgeTab({ industryKey }: { industryKey: string }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["industry-knowledge", industryKey],
    queryFn: () => fetchIndustryKnowledge(industryKey),
    staleTime: 5 * 60 * 1000, // 内容表低频变更
    refetchOnWindowFocus: false,
  });

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="知识库加载失败"
        description={(error as Error).message}
        action={
          <a onClick={() => refetch()} style={{ cursor: "pointer" }}>
            重试
          </a>
        }
      />
    );
  }
  if (isLoading || !data) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "60px 0" }}>
        <Spin />
      </div>
    );
  }
  return <KnowledgeContent data={data} />;
}
