import { useQuery } from "@tanstack/react-query";
import { Card, Skeleton, Space } from "antd";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { IndexCard } from "./IndexCard";
import { fetchMarketIndices, fetchSseLatestSnapshots } from "@/shared/api/market";
import type { MarketIndex, SseSnapshot } from "@/shared/types";

const INDEX_STALE_TIME = 5 * 60 * 1000;
const SSE_STALE_TIME = 60 * 1000;
const SSE_REFETCH_INTERVAL = 60 * 1000;
const SKELETON_COUNT = 6;

function mergeWithSse(indices: MarketIndex[], snapshots: SseSnapshot[]): MarketIndex[] {
  if (!snapshots.length) return indices;

  const sseMap = new Map<string, SseSnapshot>();
  for (const s of snapshots) {
    sseMap.set(s.code, s);
  }

  const merged = indices.map((idx) => {
    const sse = sseMap.get(idx.code);
    if (!sse) return idx;
    const change = sse.prev_close ? +(sse.last - sse.prev_close).toFixed(2) : idx.change;
    const changePercent = sse.chg_rate ?? idx.changePercent;
    return {
      ...idx,
      value: sse.last,
      change,
      changePercent,
      asof: sse.collect_time,
    };
  });

  // Append SSE-only indices not present in the original list
  const existingCodes = new Set(indices.map((i) => i.code));
  for (const s of snapshots) {
    if (existingCodes.has(s.code)) continue;
    const change = s.prev_close ? +(s.last - s.prev_close).toFixed(2) : 0;
    merged.push({
      code: s.code,
      tsCode: `${s.code}.SH`,
      name: s.name,
      value: s.last,
      change,
      changePercent: s.chg_rate ?? 0,
      exchange: "Shanghai_Stocks",
      asof: s.collect_time,
    });
  }

  return merged;
}

export function MarketOverview() {
  const navigate = useNavigate();

  const { data: baseIndices = [], isLoading: baseLoading } = useQuery({
    queryKey: ["market-indices"],
    queryFn: fetchMarketIndices,
    staleTime: INDEX_STALE_TIME,
  });

  const { data: sseSnapshots = [] } = useQuery({
    queryKey: ["sse-snapshots-latest"],
    queryFn: fetchSseLatestSnapshots,
    staleTime: SSE_STALE_TIME,
    refetchInterval: SSE_REFETCH_INTERVAL,
  });

  const indices = useMemo(
    () => mergeWithSse(baseIndices, sseSnapshots),
    [baseIndices, sseSnapshots],
  );

  if (baseLoading) {
    return (
      <div style={{ overflowX: "auto", paddingBottom: 4 }}>
        <Space size={12} style={{ display: "flex", flexWrap: "nowrap" }}>
          {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
            <Card key={i} size="small" style={{ minWidth: 160 }} styles={{ body: { padding: "12px 16px" } }}>
              <Skeleton active paragraph={{ rows: 2, width: ["60%", "80%"] }} title={{ width: "40%" }} />
            </Card>
          ))}
        </Space>
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <Space size={12} style={{ display: "flex", flexWrap: "nowrap" }}>
        {indices.map((idx) => (
          <IndexCard
            key={idx.code}
            index={idx}
            onClick={() => navigate(`/index/${idx.tsCode}`)}
          />
        ))}
      </Space>
    </div>
  );
}
