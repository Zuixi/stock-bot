import "./DistributionBars.css";

export interface DistributionBucket {
  label: string; // ">5%" / "跌停" 等
  count: number;
  direction: "up" | "down" | "flat";
}

/**
 * 涨跌分布轻量柱状条：逐桶家数横向条，上涨桶红色向右、下跌桶绿色向左。
 *
 * 与 `/market` 页 `DistributionChart` 共用同一份后端 11 桶口径，但形态更轻，
 * 供首页脉搏区等窄容器复用（不引入 ECharts）。
 */
export function DistributionBars({
  buckets,
  max,
}: {
  buckets: DistributionBucket[];
  max?: number;
}) {
  const peak = max ?? Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="distribution-bars" data-testid="distribution-bars">
      {buckets.map((b) => (
        <div className={`distribution-bars__row distribution-bars__row--${b.direction}`} key={b.label}>
          <span className="distribution-bars__label">{b.label}</span>
          <span className="distribution-bars__track">
            <span
              className={`distribution-bars__fill distribution-bars__fill--${b.direction}`}
              style={{ width: `${Math.round((b.count / peak) * 100)}%` }}
            />
          </span>
          <span className="distribution-bars__count">{b.count}</span>
        </div>
      ))}
    </div>
  );
}
