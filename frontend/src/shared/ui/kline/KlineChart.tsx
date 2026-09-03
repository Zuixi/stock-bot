import ReactECharts from "echarts-for-react";
import { AimOutlined } from "@ant-design/icons";
import { Button, Card, Divider, Empty, Segmented, Space, Spin, Tag, Tooltip as AntTooltip } from "antd";
import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { AdjustMode, KLinePoint } from "@/shared/types";
import { buildKlineOption } from "./klineOption";
import { MA_DEFS, MA_WARMUP_CALENDAR_DAYS, cropToRange, movingAverage, type MaKey } from "./klineMath";

const RANGES = [
  { label: "1月", value: 30 },
  { label: "3月", value: 90 },
  { label: "6月", value: 180 },
  { label: "1年", value: 365 },
] as const;

const STALE_TIME = 5 * 60 * 1000;
const DEFAULT_VISIBLE_MAS: MaKey[] = ["MA5", "MA10", "MA20"];

function rangeCutoff(rangeDays: number): string {
  const d = new Date();
  d.setDate(d.getDate() - rangeDays);
  return d.toISOString().slice(0, 10);
}

export interface KlineChartProps {
  title: string;
  queryKey: string;
  fetcher: (days: number, adjust: AdjustMode) => Promise<{ points: KLinePoint[]; adjustAvailable: boolean }>;
  showAdjust?: boolean;
  defaultRange?: number;
}

export function KlineChart({ title, queryKey, fetcher, showAdjust = false, defaultRange = 90 }: KlineChartProps) {
  const [range, setRange] = useState<number>(defaultRange);
  const [adjust, setAdjust] = useState<AdjustMode>(showAdjust ? "qfq" : "raw");
  const [visibleMas, setVisibleMas] = useState<MaKey[]>(DEFAULT_VISIBLE_MAS);
  const chartRef = useRef<InstanceType<typeof ReactECharts> | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["kline", queryKey, range, adjust],
    queryFn: () => fetcher(range + MA_WARMUP_CALENDAR_DAYS, adjust),
    staleTime: STALE_TIME,
    // 复权因子后台回补中：禁用态提示"稍后自动可用"，10s 轮询直至就绪后停止
    refetchInterval: (q) => (q.state.data?.adjustAvailable === false ? 10_000 : false),
  });

  const points = useMemo(
    () => cropToRange(data?.points ?? [], rangeCutoff(range)),
    [data, range],
  );

  const maSeries = useMemo(() => {
    const full = data?.points ?? [];
    const closes = full.map((p) => p.close);
    const out: Partial<Record<MaKey, (number | null)[]>> = {};
    for (const def of MA_DEFS) {
      out[def.key] = cropToRange(movingAverage(closes, def.window).map((v, i) => ({ date: full[i].date, v })), rangeCutoff(range)).map((x) => x.v);
    }
    return out;
  }, [data, range]);

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
          {MA_DEFS.map((d) => (
            <Tag.CheckableTag
              key={d.key}
              checked={visibleMas.includes(d.key)}
              onChange={(c) =>
                setVisibleMas((prev) => (c ? [...prev, d.key] : prev.filter((k) => k !== d.key)))
              }
              style={visibleMas.includes(d.key) ? { color: d.color, borderColor: d.color } : undefined}
            >
              {d.key}
            </Tag.CheckableTag>
          ))}
          <Divider type="vertical" />
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
          <Segmented
            size="small"
            value={range}
            onChange={(v) => setRange(v as number)}
            options={RANGES.map((r) => ({ label: r.label, value: r.value }))}
          />
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
        <ReactECharts ref={chartRef} option={option} notMerge lazyUpdate style={{ height: 400 }} />
      ) : (
        <div style={{ height: 400, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Empty description="暂无K线数据" />
        </div>
      )}
    </Card>
  );
}
