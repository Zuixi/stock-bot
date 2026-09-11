import { Card, Col, Row, Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchGlobalIndices } from "@/shared/api/marketData";
import { GlobalIndexCardView } from "./GlobalIndexCardView";
import { CORE_TS_CODES, pickCoreIndices } from "./coreIndices";

const STALE_TIME = 60 * 1000;
const REFETCH_INTERVAL = 60 * 1000;

/** A股核心指数卡：六个核心指数的 TV ticker 卡阵列（Stage C 指数总览 Tab） */
export function CoreIndexCards() {
  const { data: indices = [], isLoading } = useQuery({
    queryKey: ["global-indices"],
    queryFn: fetchGlobalIndices,
    staleTime: STALE_TIME,
    refetchInterval: REFETCH_INTERVAL,
  });

  // 缺谁用其余 A 股指数顺位补齐（共享选择器；与宣传页指数条的差异在过滤条件）
  const core = pickCoreIndices(indices, CORE_TS_CODES.length, (i) => i.market === "CN");

  return (
    <Card title="A股核心指数" size="small">
      {isLoading ? (
        <Row gutter={[12, 12]}>
          {Array.from({ length: 6 }, (_, i) => (
            <Col key={i} xs={12} sm={8} xl={4}>
              <Skeleton active paragraph={{ rows: 2 }} />
            </Col>
          ))}
        </Row>
      ) : core.length === 0 ? (
        <div style={{ height: 96, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-secondary)" }}>
          暂无A股指数数据
        </div>
      ) : (
        <Row gutter={[12, 12]}>
          {core.map((i) => (
            <Col key={i.tsCode} xs={12} sm={8} xl={4}>
              <GlobalIndexCardView index={i} />
            </Col>
          ))}
        </Row>
      )}
    </Card>
  );
}
