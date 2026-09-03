import ReactECharts from "echarts-for-react";
import { AimOutlined } from "@ant-design/icons";
import { Button, Card, Empty, Segmented, Space, Spin, Tooltip as AntTooltip } from "antd";
import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { AdjustMode, KLinePoint } from "@/shared/types";
import { buildKlineOption } from "./klineOption";
import {
  DEFAULT_TAIL_BARS,
  MA_DEFS,
  aggregateDaily,
  movingAverage,
  type KlineFreq,
  type MaKey,
} from "./klineMath";

const STALE_TIME = 5 * 60 * 1000;
const DEFAULT_VISIBLE_MAS: MaKey[] = ["MA5", "MA10", "MA20"];

export interface KlineChartProps {
  title: string;
  queryKey: string;
  fetcher: (days: number, adjust: AdjustMode) => Promise<{ points: KLinePoint[]; adjustAvailable: boolean }>;
  showAdjust?: boolean;
}

export function KlineChart({ title, queryKey, fetcher, showAdjust = false }: KlineChartProps) {
  const [freq, setFreq] = useState<KlineFreq>("day");
  const [adjust, setAdjust] = useState<AdjustMode>(showAdjust ? "qfq" : "raw");
  const [visibleMas, setVisibleMas] = useState<MaKey[]>(DEFAULT_VISIBLE_MAS);
  const chartRef = useRef<InstanceType<typeof ReactECharts> | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["kline", queryKey, adjust],
    queryFn: () => fetcher(3650, adjust), // 10年窗口 = 库内全量，频率切换纯前端聚合
    staleTime: STALE_TIME,
    // 复权因子后台回补中：禁用态提示"稍后自动可用"，10s 轮询直至就绪后停止
    refetchInterval: (q) => (q.state.data?.adjustAvailable === false ? 10_000 : false),
  });

  const points = useMemo(() => aggregateDaily(data?.points ?? [], freq), [data, freq]);

  const maSeries = useMemo(() => {
    const closes = points.map((p) => p.close);
    const out: Partial<Record<MaKey, (number | null)[]>> = {};
    for (const def of MA_DEFS) out[def.key] = movingAverage(closes, def.window);
    return out;
  }, [points]);

  const option = useMemo(
    () => buildKlineOption({ points, maSeries, visibleMas }),
    [points, maSeries, visibleMas],
  );

  const resetZoom = () =>
    chartRef.current?.getEchartsInstance()?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });

  return (
    <Card
      title={title}
      size="small"
      extra={
        <Space size={8} wrap style={{ justifyContent: "flex-end" }}>
          <Segmented
            size="small"
            value={freq}
            onChange={(v) => setFreq(v as KlineFreq)}
            options={[
              { label: "日K", value: "day" },
              { label: "周K", value: "week" },
              { label: "月K", value: "month" },
            ]}
          />
          {showAdjust &&
            (data?.adjustAvailable ?? false ? (
              <Segmented
                size="small"
                value={adjust}
                onChange={(v) => setAdjust(v as AdjustMode)}
                options={[
                  { label: "不复权", value: "raw" },
                  { label: "前复权", value: "qfq" },
                ]}
              />
            ) : (
              <AntTooltip title="复权数据后台拉取中，稍后自动可用">
                <Segmented
                  size="small"
                  disabled
                  value="raw"
                  options={[
                    { label: "不复权", value: "raw" },
                    { label: "前复权", value: "qfq" },
                  ]}
                />
              </AntTooltip>
            ))}
          <AntTooltip title="重置缩放">
            <Button size="small" type="text" icon={<AimOutlined />} onClick={resetZoom} />
          </AntTooltip>
        </Space>
      }
    >
      {isLoading ? (
        <div style={{ height: 400, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin />
        </div>
      ) : points.length > 0 ? (
        <div style={{ position: "relative" }}>
          {!isLoading && points.length > 0 && (
            <div style={{ position: "absolute", top: 6, left: 66, right: 20, display: "flex", gap: 12, fontSize: 11, zIndex: 5 }}>
              {MA_DEFS.map((d) => {
                const on = visibleMas.includes(d.key);
                const v = maSeries[d.key]?.[points.length - 1] ?? null;
                return (
                  <span
                    key={d.key}
                    onClick={() =>
                      setVisibleMas((prev) => (on ? prev.filter((k) => k !== d.key) : [...prev, d.key]))
                    }
                    style={{ color: on ? d.color : "#9ca3af", cursor: "pointer", userSelect: "none" }}
                  >
                    {d.key} {on ? (v == null ? "--" : v.toFixed(2)) : ""}
                  </span>
                );
              })}
            </div>
          )}
          <ReactECharts ref={chartRef} option={option} notMerge lazyUpdate style={{ height: 400 }} />
        </div>
      ) : (
        <div style={{ height: 400, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Empty description="暂无K线数据" />
        </div>
      )}
    </Card>
  );
}
