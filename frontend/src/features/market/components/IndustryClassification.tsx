import { Link } from "react-router-dom";
import { Card, Col, Empty, Row, Skeleton, Space, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchSwIndustryTree } from "@/shared/api/swIndustry";
import { fetchIndustryLevel } from "@/shared/api/swDrilldown";
import { useMarketPolling } from "../hooks/useMarketPolling";
import { IndustryAsOfBar, IndustryMetricStrip } from "./IndustryMetricStrip";

const STALE_TIME = 5 * 60 * 1000;
const SKELETON_COUNT = 12;

/**
 * S1.4：申万一级行业列表（31 个），每个卡片带该行业的 **7 项行情聚合**。
 *
 * 层级与成分数仍来自 `sw-industry/tree`（静态树）；7 项字段来自
 * `/sw-industry/drill/levels/1`（SCD2 归属 + 行情口径）。两者分开取：
 * drill 失败时**只少一条指标带**，层级导航不受影响（Ruling 30：不得删既有能力）。
 */
export function IndustryClassification() {
  const { refetchInterval } = useMarketPolling();
  const { data: tree = [], isLoading } = useQuery({
    queryKey: ["market", "sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
    staleTime: STALE_TIME,
    refetchInterval,
  });
  const { data: drill } = useQuery({
    queryKey: ["sw-drill-levels", 1],
    queryFn: () => fetchIndustryLevel(1),
    staleTime: 60_000,
  });
  const metricsByCode = new Map((drill?.items ?? []).map((it) => [it.code, it.metrics]));

  return (
    <Card
      title="行业分类（申万）"
      size="small"
      extra={<Typography.Text type="secondary">当前页面仅展示一级行业，点击进入二级页面</Typography.Text>}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {drill ? (
          <IndustryAsOfBar
            asOf={drill.as_of}
            asOfQuality={drill.as_of_quality}
            asOfReason={drill.as_of_reason}
            scd2AsOf={drill.scd2_as_of}
            scd2Available={drill.scd2_available}
            scd2Note={drill.scd2_note}
          />
        ) : null}
        <Typography.Text strong>一级行业（{tree.length}）</Typography.Text>
        {isLoading ? (
          <Row gutter={[12, 12]}>
            {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
              <Col key={i} xs={12} sm={8} lg={6}>
                <Card size="small">
                  <Skeleton active paragraph={{ rows: 2 }} title={{ width: "50%" }} />
                </Card>
              </Col>
            ))}
          </Row>
        ) : tree.length === 0 ? (
          <Empty description="暂无申万行业数据" />
        ) : (
          <Row gutter={[12, 12]}>
            {tree.map((level1) => (
              <Col key={level1.code} xs={24} sm={12} lg={8}>
                {/* 整卡是跳转入口 → 用真 <Link>：可 Tab 聚焦、Enter 触发、可新开标签；
                    裸 <Card onClick> 键盘不可达（读屏也不会报成链接）（Task 16） */}
                <Link
                  to={`/market/industry/${level1.code}`}
                  className="industry-level1-link"
                >
                  <Card hoverable size="small">
                    <Space direction="vertical" size={4} style={{ width: "100%" }}>
                      <Typography.Text strong>{level1.name}</Typography.Text>
                      <Space size={8}>
                        <Tag color="blue">{level1.stockCount} 只个股</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {level1.children.length} 个二级行业
                        </Typography.Text>
                      </Space>
                      {metricsByCode.get(level1.code) ? (
                        <IndustryMetricStrip
                          metrics={metricsByCode.get(level1.code) ?? null}
                          compact
                        />
                      ) : null}
                    </Space>
                  </Card>
                </Link>
              </Col>
            ))}
          </Row>
        )}
      </Space>
    </Card>
  );
}
