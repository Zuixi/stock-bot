import { Card, Col, Row, Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchGlobalIndices, type GlobalIndexCard } from "@/shared/api/marketData";
import { GlobalIndexCardView } from "./GlobalIndexCardView";

const STALE_TIME = 60 * 1000;
const REFETCH_INTERVAL = 60 * 1000;

/** A股核心指数（契约 §4：上证/深证/创业板/沪深300/中证500/科创50） */
const CORE_TS_CODES = [
  "000001.SH", // 上证指数
  "399001.SZ", // 深证成指
  "399006.SZ", // 创业板指
  "000300.SH", // 沪深300
  "000905.SH", // 中证500
  "000688.SH", // 科创50
];

/** 缺谁用其余 CN 指数顺位补齐，恒定 6 格 */
function pickCoreIndices(list: GlobalIndexCard[]): GlobalIndexCard[] {
  const core = CORE_TS_CODES
    .map((code) => list.find((i) => i.tsCode === code))
    .filter((i): i is GlobalIndexCard => !!i);
  const rest = list.filter((i) => i.market === "CN" && !CORE_TS_CODES.includes(i.tsCode));
  return [...core, ...rest].slice(0, CORE_TS_CODES.length);
}

/** A股核心指数卡：六个核心指数的 TV ticker 卡阵列（Stage C 指数总览 Tab） */
export function CoreIndexCards() {
  const { data: indices = [], isLoading } = useQuery({
    queryKey: ["global-indices"],
    queryFn: fetchGlobalIndices,
    staleTime: STALE_TIME,
    refetchInterval: REFETCH_INTERVAL,
  });

  const core = pickCoreIndices(indices);

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
