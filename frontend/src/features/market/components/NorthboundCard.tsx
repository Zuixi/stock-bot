import { Card, Spin, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchNorthbound } from "@/shared/api/marketData";
import { EChart } from "@/shared/ui/EChart";
import { FreshnessNote } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { ThemePalette } from "@/app/theme";
import { fmtNorthYi } from "./format";
import { useMarketPolling } from "../hooks/useMarketPolling";

const STALE_TIME = 5 * 60 * 1000;

function buildOption(points: Array<{ date: string; netAmount: number | null }>, c: ThemePalette) {
  const dates = points.map((p) => p.date.slice(5));
  const values = points.map((p) => (p.netAmount == null ? null : p.netAmount / 1e4));
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: (v: number | null) => (v == null ? "—" : `${v.toFixed(2)}亿`) },
    xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${v}亿` } },
    series: [
      {
        type: "line",
        data: values,
        symbol: "circle",
        symbolSize: 4,
        connectNulls: true,
        lineStyle: { width: 2, color: c.primary },
        itemStyle: { color: c.primary },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: c.border, type: "dashed" },
          data: [{ yAxis: 0 }],
          label: { show: false },
        },
      },
    ],
  };
}

export function NorthboundCard() {
  const { colors } = useTheme();
  const { refetchInterval } = useMarketPolling();
  const { data: northbound, isLoading } = useQuery({
    queryKey: ["market", "northbound", 30],
    queryFn: () => fetchNorthbound(30),
    staleTime: STALE_TIME,
    refetchInterval,
  });
  const data = northbound?.items ?? [];
  // 上游 `moneyflow_hsgt` 事实停更：卡片要给一句带**最新数据日**的结论，日期取 payload 的
  // `as_of`（不写死，换一天文案跟着变）。`FreshnessNote` 的徽标按契约不复述日期，所以本卡
  // 自己渲染整句，`source_status` 不再走徽标通道——同一句里已含「已停更」结论，不漏标注。
  const latestDay = northbound?.asOf ? northbound.asOf.slice(5) : null;
  const last = data.length > 0 ? data[data.length - 1] : undefined;
  const total = data.reduce((acc, p) => acc + (p.netAmount ?? 0), 0);
  const lastColor = (last?.netAmount ?? 0) > 0 ? colors.up : (last?.netAmount ?? 0) < 0 ? colors.down : colors.flat;
  const totalColor = total > 0 ? colors.up : total < 0 ? colors.down : colors.flat;
  return (
    <Card
      title="北向资金"
      size="small"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>盘后净流入 · 亿元</Typography.Text>}
    >
      <Spin spinning={isLoading}>
        {/* `stale_days` → 「N 天前」徽标；停更结论见下一行（带最新数据日） */}
        <FreshnessNote asOf={northbound?.asOf} staleDays={northbound?.staleDays} />
        {northbound?.sourceStatus === "discontinued" ? (
          <Typography.Text
            type="warning"
            data-testid="northbound-discontinued"
            style={{ display: "block", fontSize: 12, marginTop: 2 }}
          >
            数据源已停更{latestDay ? `（最新 ${latestDay}）` : ""}
          </Typography.Text>
        ) : null}
        <div style={{ display: "flex", gap: 24, marginBottom: 4, fontSize: 12 }}>
          <span>当日 <b style={{ color: lastColor, fontSize: 16 }}>{fmtNorthYi(last?.netAmount)}</b></span>
          <span>近30日累计 <b style={{ color: totalColor }}>{fmtNorthYi(total)}</b></span>
        </div>
        {data.length > 0 ? (
          <EChart option={buildOption(data, colors)} height={216} />
        ) : (
          <div style={{ height: 216, display: "flex", alignItems: "center", justifyContent: "center", color: colors.flat }}>
            暂无北向数据（盘后自动更新）
          </div>
        )}
      </Spin>
    </Card>
  );
}
