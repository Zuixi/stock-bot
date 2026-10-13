import { Card, Spin, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchNorthbound } from "@/shared/api/marketData";
import type { NorthboundAmountKind, NorthboundWindowKind } from "@/shared/api/marketEnvelope";
import { EChart } from "@/shared/ui/EChart";
import { FreshnessNote } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { ThemePalette } from "@/app/theme";
import { fmtNorthYi, fmtTurnoverYi } from "./format";
import { useMarketPolling } from "../hooks/useMarketPolling";

const STALE_TIME = 5 * 60 * 1000;

/**
 * 口径 → 数值怎么念（Ruling 62/63）。上游 `moneyflow_hsgt.north_money` 在 **2024-08-19**
 * 换过语义：之前是**净买入**（可负），之后是**成交额**（恒正）。本卡据 `amount_kind` 决定
 * 文案、正负号与着色——**不再**把断点后的成交额念成"净流入"。
 */
const KIND_LABEL: Record<NorthboundAmountKind, string> = {
  net_buy: "净买入",
  turnover: "成交额",
  unknown: "口径未知",
};

/** 卡片副标题（窗口口径）。 */
const WINDOW_HEADER: Record<NorthboundWindowKind, string> = {
  net_buy: "盘后净买入 · 亿元",
  turnover: "盘后成交额 · 亿元",
  mixed: "盘后口径混合 · 亿元",
  unknown: "盘后口径未知 · 亿元",
};

/** 累计标题：口径不同，累加出来的量名不同（成交额之和是"累计成交额"，不是净流入）。 */
const WINDOW_TOTAL_LABEL: Record<NorthboundWindowKind, string> = {
  net_buy: "近30日累计净买入",
  turnover: "近30日累计成交额",
  mixed: "近30日累计",
  unknown: "近30日累计",
};

/** 不可累计时说明原因（`null` = 可累计）。 */
const WINDOW_TOTAL_BLOCKED: Record<NorthboundWindowKind, string | null> = {
  net_buy: null,
  turnover: null,
  mixed: "口径跨 2024-08-19 断点，不与净买入相加",
  unknown: "口径未标注，不累计",
};

/** 累计值的格式化器按**窗口口径**选：净买入带正负号，成交额不带（流量无方向）。 */
const fmtByWindow = (kind: NorthboundWindowKind) =>
  kind === "net_buy" ? fmtNorthYi : fmtTurnoverYi;

function buildOption(
  points: Array<{ date: string; amount: number | null }>,
  c: ThemePalette,
  windowKind: NorthboundWindowKind,
) {
  const dates = points.map((p) => p.date.slice(5));
  const values = points.map((p) => (p.amount == null ? null : p.amount / 1e4));
  const signed = windowKind === "net_buy";
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number | null) =>
        v == null ? "—" : `${signed && v > 0 ? "+" : ""}${v.toFixed(2)}亿`,
    },
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
        // 零轴只在**净买入**口径下有意义（正=净流入 / 负=净流出）；成交额恒正，零轴是噪音。
        ...(signed
          ? {
              markLine: {
                silent: true,
                symbol: "none",
                lineStyle: { color: c.border, type: "dashed" },
                data: [{ yAxis: 0 }],
                label: { show: false },
              },
            }
          : {}),
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
  // 缺值不得渲染成 0：`amount` 逐日是**可空**的（`_map_hsgt_rows` 把上游 NaN 映射为
  // null = "该日缺失"）。原先 `acc + (p.amount ?? 0)` 把缺失并成 0，于是**30 天全缺**
  // 时会显示"近30日累计 0.00亿"——一个看起来正常的数字，实际是"没有数据"。
  // 现在逐日缺失即整段未知（`null`），由格式化器渲染成 `—`。
  // 本卡其余部分早就是这个口径：`buildOption` 里 `p.amount == null ? null : …`
  // 让折线在该日**断开**，tooltip 也走 `v == null ? "—"`。
  const windowKind: NorthboundWindowKind = northbound?.amountKind ?? "unknown";
  // **只有同一口径才累计**（Ruling 62/63）：断点后 30 日全是成交额 → 「近30日累计成交额」
  // （"把 30 天成交额加起来"本身是真的）；断点前的净买入窗口 → 「累计净买入」也是真的；
  // 跨断点 `mixed` / 口径未知 → **不给数**。旧实现把成交额加起来叫「近30日累计（净流入）」，
  // 那是一个没有意义的数——本卡不再产出它。
  const couldSum = windowKind === "net_buy" || windowKind === "turnover";
  const total =
    !couldSum || data.length === 0
      ? null
      : data.reduce<number | null>(
          (acc, p) => (p.amount == null || acc == null ? null : acc + p.amount),
          0,
        );
  const lastKind: NorthboundAmountKind = last?.amountKind ?? "unknown";
  const lastAmount = last?.amount ?? null;
  const lastColor =
    lastKind !== "net_buy"
      ? colors.primary
      : lastAmount == null
        ? colors.flat
        : lastAmount > 0
          ? colors.up
          : lastAmount < 0
            ? colors.down
            : colors.flat;
  const totalBlocked = WINDOW_TOTAL_BLOCKED[windowKind];
  return (
    <Card
      title="北向资金"
      size="small"
      extra={
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {WINDOW_HEADER[windowKind]}
        </Typography.Text>
      }
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
          <span>
            当日{KIND_LABEL[lastKind]}{" "}
            <b style={{ color: lastColor, fontSize: 16 }}>
              {lastKind === "net_buy" ? fmtNorthYi(lastAmount) : fmtTurnoverYi(lastAmount)}
            </b>
          </span>
          <span>
            {WINDOW_TOTAL_LABEL[windowKind]}{" "}
            <b style={{ color: total == null ? colors.flat : colors.primary }}>
              {total == null ? "—" : fmtByWindow(windowKind)(total)}
            </b>
            {totalBlocked ? (
              <Typography.Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
                {totalBlocked}
              </Typography.Text>
            ) : null}
          </span>
        </div>
        {data.length > 0 ? (
          <EChart option={buildOption(data, colors, windowKind)} height={216} />
        ) : (
          <div style={{ height: 216, display: "flex", alignItems: "center", justifyContent: "center", color: colors.flat }}>
            暂无北向数据（盘后自动更新）
          </div>
        )}
      </Spin>
    </Card>
  );
}
