import { Card, Col, Row, Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchGlobalIndices } from "@/shared/api/marketData";
import type { GlobalIndexCard } from "@/shared/api/marketData";
import { GlobalIndexCardView } from "./GlobalIndexCardView";
import { CORE_TS_CODES, pickCoreIndices } from "./coreIndices";
import { useMarketPolling } from "../hooks/useMarketPolling";

const STALE_TIME = 60 * 1000;

/**
 * A股核心指数卡自己的 query key（`GlobalMarketBoard` 用 `["market","global-indices"]`）。
 *
 * 为什么要拆：两者读同一端点，但**刷新口径不同**——本卡是 A 股指数，随 A 股时段
 * （开市 30s / 休市停）；全球指数盘含美股/欧股，必须常驻 300s。react-query 的
 * `refetchInterval` 是**每个 observer 各起一个定时器**（query-core `#updateRefetchInterval`），
 * 但缓存只有一份：同 key 下两个 observer 共用同一份 data，休市时本卡仍会被全球卡的
 * 300s 带着刷新（「休市不轮询」名存实亡），fix round 1 的 300s 也正是这样把盘中 A 股
 * 核心指数卡从 30s 拖慢到 300s。拆 key 后代价可测（见 e2e/marketPolling.spec.ts）：
 * 930s 盘中窗口多约 4 次请求（挂载 1 次 + 300/600/900s 与 30s 节奏撞点各 1 次），
 * 休市窗口 0 次（本 query 不轮询）。
 */
const CN_CORE_KEY = ["market", "global-indices", "cn"] as const;
const CN_CORE_COUNT = CORE_TS_CODES.length;
const isCnIndex = (i: GlobalIndexCard) => i.market === "CN";
/** 选择器提到模块级：每次 render 新建函数会让 react-query 重跑 `select`、给出新数组引用 */
const selectCnCore = (rows: GlobalIndexCard[]) => pickCoreIndices(rows, CN_CORE_COUNT, isCnIndex);

/**
 * A股核心指数卡：六个核心指数的 TV ticker 卡阵列（Stage C 指数总览 Tab）。
 *
 * 轮询用默认的 `"session"` 模式（A 股时段：开市 30s，其余不轮询）——它是市场页主
 * Tab 的 A 股口径卡片，不该跟着全球指数盘的 300s 慢节奏。
 */
export function CoreIndexCards() {
  const { refetchInterval } = useMarketPolling();
  const { data: core = [], isLoading } = useQuery({
    queryKey: CN_CORE_KEY,
    queryFn: fetchGlobalIndices,
    staleTime: STALE_TIME,
    refetchInterval,
    // 缺谁用其余 A 股指数顺位补齐（共享选择器；与宣传页指数条的差异在过滤条件）
    select: selectCnCore,
  });

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
