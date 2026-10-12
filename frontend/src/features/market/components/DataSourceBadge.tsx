import { Tooltip } from "antd";

/**
 * 全球标的的**数据源代码徽章**（S1.3 交付物「数据源代码映射」的可视化）。
 *
 * 为什么不用 `industry-research` 的 `SourceBadge`：那个组件的 `tier` 是**发布方权威性**
 * 词典（official / highfreq / calc / manual / derived），与"这一格价来自哪个数据源"
 * 是两套词汇。硬塞进去会让两边的取值互相污染（下一个加源的人不知道该往哪本词典里放），
 * 故这里复用它的**视觉语言**（同尺寸/同圆角/同 Tooltip 模式）另起一个小组件，
 * 与 `MetricBadge` 复用 `SourceBadge` 视觉的做法一致。
 *
 * 词典必须与后端登记表 `market_data_service.GLOBAL_INDICES[].source` 对齐；
 * 取不到时**原样显示 source 字符串**（而不是兜底成某个已知源的标签——那会把新源伪装成旧源）。
 */
const SOURCE_META: Record<string, { label: string; color: string; bg: string; border: string; tip: string }> = {
  index_daily: {
    label: "TuShare 指数日线",
    color: "#1256c4",
    bg: "#e8f1ff",
    border: "#b8d4ff",
    tip: "TuShare Pro `index_daily`（A 股宽基日线，单一主源；实时快照由东财延迟行情补齐）",
  },
  index_global: {
    label: "TuShare 全球指数",
    color: "#1f6d45",
    bg: "#ecfdf3",
    border: "#b7ebc9",
    tip: "TuShare Pro `index_global`（已接入且在用的全球指数日线源）",
  },
  sina_index: {
    label: "新浪美股指数",
    color: "#8c6a00",
    bg: "#fffbe6",
    border: "#ffe58f",
    tip: "新浪财经 `hq.sinajs.cn`（美股指数行情）。TuShare `index_global` 无此标的，故为补位源；日线回补适配器待独立任务接入",
  },
};

interface Props {
  source: string;
}

export function DataSourceBadge({ source }: Props) {
  const meta = SOURCE_META[source];
  const label = meta?.label ?? source;
  return (
    <Tooltip title={meta?.tip ?? `数据源代码：${source}`}>
      <span
        style={{
          fontSize: 10.5,
          lineHeight: 1,
          padding: "3px 6px",
          borderRadius: 4,
          color: meta?.color ?? "#6b7686",
          background: meta?.bg ?? "#f7f8fa",
          border: `1px solid ${meta?.border ?? "#e5e9ef"}`,
          whiteSpace: "nowrap",
          cursor: "help",
        }}
      >
        {label}
      </span>
    </Tooltip>
  );
}

/**
 * 「价来自哪一路」徽章。`none` 必须显式说「数据源待接入」——**不得**用 0 或空字符串装作有数据。
 */
const PRICE_SOURCE_META: Record<string, { label: string; color: string; tip: string }> = {
  realtime: { label: "实时", color: "#1f6d45", tip: "东财延迟行情实时快照" },
  realtime_sina: { label: "实时", color: "#1f6d45", tip: "新浪美股指数实时行情" },
  eod: { label: "收盘", color: "#6b7686", tip: "实时快照不可用时回落库内最近一根日线" },
  none: {
    label: "数据源待接入",
    color: "#c2410c",
    tip: "该标的尚无可用行情（既无实时快照也无日线）。本页**不显示占位数**，待数据源适配器落地后自动出现",
  },
};

export function PriceSourceBadge({ priceSource }: { priceSource: string }) {
  const meta = PRICE_SOURCE_META[priceSource] ?? {
    label: priceSource,
    color: "#6b7686",
    tip: priceSource,
  };
  return (
    <Tooltip title={meta.tip}>
      <span style={{ fontSize: 11, color: meta.color, cursor: "help" }}>{meta.label}</span>
    </Tooltip>
  );
}
