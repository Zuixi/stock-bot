import type { MetricHistory } from "@/shared/api/financial";

/** 一条对齐到公共期间轴的序列：`values[i]` 恒对应 `periods[i]` */
export interface AlignedMetricSeries {
  /** 展示名（取 `metric_key`，纯数据层不做中文映射：图例文案由调用方决定） */
  name: string;
  /** 与 `periods` 等长；该指标在该期没有数据点为 `null`（**不是 0**） */
  values: (number | null)[];
}

export interface AlignedMetrics {
  /** 所有指标的期间**并集**，升序 */
  periods: string[];
  /** 顺序与入参 `histories` 一致（图例顺序 = 请求顺序） */
  series: AlignedMetricSeries[];
}

/**
 * 把多个指标的 `points` 对齐到**同一根期间轴**上（F12）。
 *
 * 为什么必须有（旧实现的缺陷）：`MetricsHistoryChart` 直接把 `histories[0].points`
 * 当作 x 轴，各序列按**位置**贴值。只要任何一个指标缺一期（停牌、单季未披露、
 * 派生指标未算出），后面所有值就**整体左移一格**：ROE 的 2025-06-30 被画在
 * 2025-03-31 的刻度上。图看起来平滑合理，读数全错。
 *
 * 现在的口径：期间 = 所有指标 period 的**并集**并升序；每条序列按 period 查表贴值，
 * 缺的期留 `null`（配合调用方 `connectNulls: false` 渲染成**断口**）。缺失 ≠ 0。
 *
 * 排序：period 是后端约定的 `YYYY-MM-DD`，ISO 字符串的字典序 == 时间序，故直接用
 * 字符串比较（不走 `new Date()`：日期解析受时区/local 格式影响，反而是不确定性来源）。
 * **若非 ISO 字符串混入**（如 `2025Q3`、`2025/09/30`），这里退化为按同一规则的**防御性
 * 字典序**排序 —— 顺序稳定、可复现，但不保证与真实时间序一致；真出现这种数据应先
 * 在接口层归一成 ISO，而不是靠本函数猜。
 *
 * 同一指标内 period 重复时按 **Map 后写覆盖**（与后端"后到的修订值替换旧值"的语义一致）。
 */
export function alignMetricSeries(histories: MetricHistory[]): AlignedMetrics {
  const periodSet = new Set<string>();
  for (const history of histories) {
    for (const point of history.points) periodSet.add(point.period);
  }
  const periods = [...periodSet].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));

  const indexOf = new Map(periods.map((period, i) => [period, i]));
  const series = histories.map((history) => {
    const values: (number | null)[] = periods.map(() => null);
    const byPeriod = new Map<string, number | null>();
    for (const point of history.points) byPeriod.set(point.period, point.value);
    for (const [period, value] of byPeriod) {
      const index = indexOf.get(period);
      if (index !== undefined) values[index] = value;
    }
    return { name: history.metric_key, values };
  });

  return { periods, series };
}
