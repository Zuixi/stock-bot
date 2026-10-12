import { Space, Tag, Tooltip, Typography } from "antd";
import { DeltaText } from "@/shared/ui/DeltaText";
import { formatCnDate } from "@/shared/ui/date";
import type { IndustryQuote } from "@/shared/api/swDrilldown";

/**
 * S1.4 · 一个申万行业节点的 7 项行情字段条。
 *
 * **缺失一律渲染 `--`，绝不 `?? 0`**（后端不填 0，前端不许造 0）。
 * 每项都带"非空覆盖数 / 成分数"的提示——缺数必须看得见，否则 `--` 会被读成"这一项没意义"，
 * 而不是"这一项没采到"。
 *
 * 单位：`amount` 后端已换算为**元**（源 TuShare 千元 ×1000）。
 */

function fmtPrice(v: number | null): string {
  return v == null ? "--" : v.toFixed(2);
}

function fmtVolume(v: number | null): string {
  if (v == null) return "--";
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿股`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万股`;
  return `${v.toFixed(0)}股`;
}

function fmtAmountYuan(v: number | null): string {
  if (v == null) return "--";
  if (Math.abs(v) >= 1e12) return `${(v / 1e12).toFixed(2)}万亿`;
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
  return `${v.toFixed(0)}`;
}

function fmtRatio(v: number | null): string {
  return v == null ? "--" : v.toFixed(2);
}

function coverageTip(field: string, covered: number, total: number): string {
  return `${field}非空 ${covered}/${total} 只成分股${covered < total ? "（缺数不填 0）" : ""}`;
}

export function IndustryMetricStrip({
  metrics,
  compact = false,
}: {
  metrics: IndustryQuote | null;
  compact?: boolean;
}) {
  if (!metrics) {
    return <Typography.Text type="secondary">暂无该行业聚合数据</Typography.Text>;
  }
  const total = metrics.member_count;
  const k = metrics.kline_median;

  const cells: { key: string; label: string; tip: string; node: React.ReactNode }[] = [
    {
      key: "kline",
      label: "K 线 开/高/低/收",
      tip: `成分股 OHLC 的**中位数**（不是行业指数点位）：${k.member_count} 只参与`,
      node: (
        <Typography.Text>
          {fmtPrice(k.open)} / {fmtPrice(k.high)} / {fmtPrice(k.low)} /{" "}
          <b>{fmtPrice(k.close)}</b>
        </Typography.Text>
      ),
    },
    {
      key: "pct",
      label: "涨跌幅",
      tip: coverageTip("涨跌幅", metrics.pct_chg_covered, total) + "；成交额加权",
      node: <DeltaText value={metrics.pct_chg_weighted} />,
    },
    {
      key: "turnover",
      label: "换手率",
      tip: coverageTip("换手率", metrics.turnover_rate_covered, total),
      node: <Typography.Text>{fmtRatio(metrics.turnover_rate_weighted)}%</Typography.Text>,
    },
    {
      key: "volume",
      label: "成交量",
      tip: coverageTip("成交量", metrics.volume_covered, total),
      node: <Typography.Text>{fmtVolume(metrics.volume)}</Typography.Text>,
    },
    {
      key: "volume_ratio",
      label: "量比",
      tip: coverageTip("量比", metrics.volume_ratio_covered, total) + "；成交量加权",
      node: <Typography.Text>{fmtRatio(metrics.volume_ratio_weighted)}</Typography.Text>,
    },
    {
      key: "amplitude",
      label: "振幅",
      tip: coverageTip("振幅", metrics.amplitude_covered, total) + "；(高−低)/昨收 的成分股均值",
      node: <Typography.Text>{fmtRatio(metrics.amplitude)}%</Typography.Text>,
    },
    {
      key: "amount",
      label: "成交额",
      tip: coverageTip("成交额", metrics.amount_covered, total) + "；单位元",
      node: <Typography.Text strong>{fmtAmountYuan(metrics.amount)}</Typography.Text>,
    },
  ];

  return (
    <Space wrap size={compact ? 8 : 16} data-testid="industry-metric-strip">
      {cells.map((c) => (
        <Tooltip key={c.key} title={c.tip}>
          <Space direction="vertical" size={0}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {c.label}
            </Typography.Text>
            {c.node}
          </Space>
        </Tooltip>
      ))}
      <Tooltip
        title={`成分股 ${total} 只，当日真有行情 ${metrics.quoted_count} 只；上涨 ${metrics.up_count} / 下跌 ${metrics.down_count}`}
      >
        <Tag color="blue">{total} 只</Tag>
      </Tooltip>
    </Space>
  );
}

/**
 * 分类归属口径条（**必须**与行情口径日分开展示）。
 *
 * 后端把两条时间轴分开返回：`as_of` = 行情口径日；`scd2_as_of` = 最近一次成功
 * ingest（= 分类归属可还原能力的**上界**）。把两者合成一句"数据截至 X"会让用户
 * 以为成分也更新到了 X。
 */
export function IndustryAsOfBar({
  asOf,
  asOfQuality,
  asOfReason,
  scd2AsOf,
  scd2Available,
  scd2Note,
}: {
  asOf: string | null;
  asOfQuality: string;
  asOfReason: string | null;
  scd2AsOf: string | null;
  scd2Available: boolean;
  scd2Note: string | null;
}) {
  const qualityLabel: Record<string, string> = {
    complete: "完整",
    fallback: "回退日",
    partial: "不完整",
  };
  return (
    <Space wrap size={8} data-testid="industry-asof-bar">
      <Tag color={asOfQuality === "complete" ? "green" : "orange"}>
        行情口径日 {asOf ? formatCnDate(asOf) : "--"}（{qualityLabel[asOfQuality] ?? asOfQuality}）
      </Tag>
      <Tooltip title={scd2Note ?? "分类归属按 SCD2 区间还原（effective_from / effective_to）"}>
        <Tag color={scd2Available ? "geekblue" : "red"} data-testid="scd2-asof-tag">
          分类归属口径日 {scd2AsOf ? formatCnDate(scd2AsOf) : "无数据"}
        </Tag>
      </Tooltip>
      {asOfReason ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {asOfReason}
        </Typography.Text>
      ) : null}
    </Space>
  );
}
