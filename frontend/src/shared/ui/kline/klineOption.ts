import { THEME_COLORS, type ThemePalette } from "@/app/theme";
import type { KLinePoint } from "@/shared/types";
import {
  DEFAULT_TAIL_BARS,
  MA_DEFS,
  NARROW_TAIL_BARS,
  fmtAmount,
  fmtChangePct,
  fmtSignedDiff,
  fmtVolume,
  type MaKey,
} from "./klineMath";

/**
 * 图上一枚事件气泡（P1-2）。**领域无关**：`shared/ui/kline` 不认识公告/龙虎榜，
 * 只知道"某一天有一条带颜色和标签的事件"；映射由 stock-detail 层完成。
 */
export interface KlineEventMarker {
  /** 必须与某个 bar 的 `date` **精确**相等；不在已加载区间的不画 */
  date: string;
  label: string;
  /** 由调用方按当前主题取色（本层不 import 业务色板） */
  color: string;
}

/** 「把图定位到第 index 根」的请求；`nonce` 变化才算一次新请求（同一根可重复点击）。 */
export interface KlineFocusRequest {
  index: number;
  nonce: number;
}

export interface KlineOptionInput {
  points: KLinePoint[]; // 聚合后的全量序列
  maSeries: Partial<Record<MaKey, (number | null)[]>>;
  visibleMas: MaKey[];
  /** 当前主题色板（KlineChart 绕过 EChart 封装直连 ReactECharts，需自带主题色） */
  colors?: ThemePalette;
  /**
   * 事件气泡（可选）。日期不在 `points` 里的一律丢弃 —— 上市前/停牌/周月聚合后
   * 不存在的日子在 x 轴上没有位置，硬画只会落到错误的蜡烛上。
   */
  eventMarkers?: KlineEventMarker[];
  /**
   * 窄屏形态（Phase 3 §4.4，`<768` 由调用方按视口断点判定）。
   *
   * 影响三项，**都不改变"画什么"**（全量序列、双 `dataZoom`、MA、成交量一个不少）：
   * - 初始视窗：`NARROW_TAIL_BARS`(30) 而不是 `DEFAULT_TAIL_BARS`(120)，单根蜡烛可辨；
   * - 左轴留白：60 → 46px（308px 画布里 60px 是 1/5 宽度）；
   * - 网格底部：`slider` 占 22px，底部留白随之收窄，避免滑块压住成交量柱。
   */
  narrow?: boolean;
}

/** tooltip 列式行：灰标签左、语义色数值右（tabular 对齐） */
const row = (label: string, value: string, flat: string, color?: string) =>
  `<div style="display:flex;justify-content:space-between;gap:16px">` +
  `<span style="color:${flat}">${label}</span>` +
  `<span class="num"${color ? ` style="color:${color};font-weight:600"` : ""}>${value}</span>` +
  `</div>`;

/**
 * 轴级 axisPointer（两个 xAxis 共用）。
 *
 * ⚠️ `type` 这里**只能是 `"line"`**（或 `"shadow"`），不能写 `"cross"`：
 * `cross` 只属于 `tooltip.axisPointer` / 顶层 axisPointer。写在这里会被
 * `modelHelper.makeAxisPointerModel` 的模型链（`axis.model.getModel('axisPointer',
 * <tooltip 派生模型>)`，轴级优先级最高）覆盖掉 tooltip 派生出来的 `"line"`，
 * 随后 `CartesianAxisPointer.makeElOption` 执行 `pointerShapeBuilder["cross"](...)`
 * ——该表只有 line/shadow/none ⇒ 抛 `pointerShapeBuilder[axisPointerType] is not a
 * function`（PAGEERROR，整张图的十字光标与 tooltip 全灭，实测 echarts 5.6.0）。
 * 跨 pane 的"cross 形"由顶层 `axisPointer.link` + `tooltip.axisPointer.type` 给出：
 * 竖线贯穿两个 pane（link），横线只在本 pane（tooltip 的 cross）。
 *
 * `snap` 吸附蜡烛中心；`triggerOn` 带 `click` 才有触屏十字（ECharts 实际从顶层
 * axisPointer 读 triggerOn，这里保留以满足轴级语义/可读性，顶层也有同名兜底）。
 */
const AXIS_POINTER_PER_AXIS = {
  type: "line",
  snap: true,
  triggerOn: "mousemove|click",
} as const;

export function buildKlineOption({
  points, maSeries, visibleMas, colors, narrow = false, eventMarkers,
}: KlineOptionInput) {
  const c = colors ?? THEME_COLORS.light;
  const dates = points.map((p) => p.date); // 原始 ISO 日期串（tooltip 直接消费）
  const ohlc = points.map((p) => [p.open, p.close, p.low, p.high]);
  const lastClose = points.length ? points[points.length - 1].close : 0;
  // 窄屏只收**初始视窗**根数（§4.4）：数据仍是全量，dataZoom 可漫游/手势缩放
  const tailBars = narrow ? NARROW_TAIL_BARS : DEFAULT_TAIL_BARS;
  const tailStart = Math.max(0, points.length - tailBars);
  // 窄屏左轴留白收窄：308px 画布下 60px 是 1/5 宽度，K 线区被压得太窄
  const axisLeft = narrow ? 46 : 60;

  /**
   * 事件 → markPoint 数据（P1-2）。
   *
   * 为什么是 `markPoint` 而不是独立的 scatter series：气泡与蜡烛必须**同 pane 同坐标**，
   * scatter 需要自己的 x/y 轴映射，很容易在 dataZoom / 双 pane 下与蜡烛错位。
   *
   * 默认**不显示文字标签**（`label.show: false`）：8 个气泡在 120 根视窗里，`公告+1`
   * 这类 3–4 个汉字的标签必然互相压住（brief 的"不重叠"验收）。标签放到悬停
   * emphasis 上；列表里本来就能看到完整标题。
   */
  const markPointAt = (marker: KlineEventMarker) => {
    const index = dates.indexOf(marker.date);
    if (index < 0) return null; // 不在已加载 bar 区间：不产生标记
    const p = points[index];
    const y = Number.isFinite(p.high) ? p.high : Math.max(p.open, p.close);
    return {
      name: marker.label,
      // 类目轴用日期串定位（与 xAxis.data 同源），y 取当日最高价：气泡落在 K 线上方
      coord: [marker.date, y] as [string, number],
      value: marker.label,
      itemStyle: { color: marker.color, borderColor: c.bgPanel, borderWidth: 1 },
      label: { show: false },
    };
  };
  const markPointData = (eventMarkers ?? []).flatMap((m) => markPointAt(m) ?? []);

  const tooltipFormatter = (params: unknown): string => {
    const list = params as Array<{ dataIndex: number; seriesType: string; seriesName?: string; value?: unknown }>;
    // ⚠️ **任意 series 定位**，不能只认 candlestick：ECharts 的 axis 触发只收集
    // **当前悬停 axis 上的 series**，悬停成交量 pane（xAxisIndex 1）时 params 里
    // 只有 `成交量` 这条 bar、没有 candlestick ⇒ 旧写法 `find(seriesType ===
    // "candlestick")` 返回 undefined ⇒ 成交量区 tooltip 空白（用户报的 F5）。
    // `dataIndex` 在所有 axis 上语义相同，故取 `list[0]` 即可，OHLC 一律回查 points。
    const i = list[0]?.dataIndex;
    if (i == null || !points[i]) return "";
    const p = points[i];
    const { open, close, low, high } = p;
    const prev = i > 0 ? points[i - 1].close : undefined;
    // A股习惯：开/收/高/低/涨跌额/涨跌幅 随当日涨跌着色（首日中性）
    const color = prev == null ? c.flat : close >= prev ? c.up : c.down;
    const change = fmtChangePct(close, prev);
    const diff = fmtSignedDiff(close, prev);
    const maRow = (name: MaKey) => {
      const def = MA_DEFS.find((d) => d.key === name);
      const v = maSeries[name]?.[i];
      return v == null ? "" : row(name, v.toFixed(2), c.flat, def?.color);
    };
    return `<div style="font-size:12px;line-height:1.8;min-width:150px">
      <div style="font-weight:600;margin-bottom:2px">${p.date}</div>
      ${row("开盘", open.toFixed(2), c.flat, color)}
      ${row("收盘", close.toFixed(2), c.flat, color)}
      ${row("最高", high.toFixed(2), c.flat, color)}
      ${row("最低", low.toFixed(2), c.flat, color)}
      ${row("涨跌额", diff, c.flat, color)}
      ${row("涨跌幅", change, c.flat, color)}
      ${row("成交量", fmtVolume(p.volume), c.flat)}
      ${row("成交额", fmtAmount(p.amount), c.flat)}
      ${visibleMas.map((k) => maRow(k)).join("")}
    </div>`;
  };

  return {
    textStyle: { color: c.textSecondary },
    // 顶层 axisPointer 是跨 pane 联动的**唯一**开关（F4）：`link.xAxisIndex: "all"`
    // 把两个 grid 的 x 轴绑成一条十字光标 —— 竖线同时贯穿 K 线 pane 与成交量 pane，
    // 横线只留在当前 pane（ECharts cross 的语义）。缺这一行就只剩当前 pane 内的十字。
    axisPointer: {
      link: [{ xAxisIndex: "all" as const }],
      lineStyle: { color: c.flat, type: "dashed" as const, width: 1 },
      label: { backgroundColor: c.textPrimary, color: c.bgPage, fontSize: 10 },
      // 触屏点按也出十字（ECharts 的 triggerOn 只认顶层/global，轴级那份是文档性的）
      triggerOn: "mousemove|click" as const,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" as const },
      formatter: tooltipFormatter,
      // 给 tooltip 一个稳定类名：图上方 ribbon 与 tooltip 现在**同时**展示
      // 成交量/涨跌幅等同名标签，e2e 若不 scope 到 tooltip 会在两个 DOM 分支间撞
      // strict-mode（多个匹配）。类名也让"读 tooltip"的断言与版式解耦。
      className: "kline-tooltip",
      // 窄屏（308px）下 tooltip 默认会溢出视口被裁掉；confine 关进画布内（§5.1）
      ...(narrow ? { confine: true } : {}),
    },
    legend: { show: false },
    grid: [
      { left: axisLeft, right: narrow ? 12 : 20, top: 28, height: "50%" },
      { left: axisLeft, right: narrow ? 12 : 20, top: "68%", height: "16%" },
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { onZero: false, lineStyle: { color: c.border } },
        axisLabel: { show: false },
        gridIndex: 0,
        axisPointer: AXIS_POINTER_PER_AXIS,
      },
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: c.border } },
        axisLabel: { show: false },
        gridIndex: 1,
        axisPointer: AXIS_POINTER_PER_AXIS,
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        axisLabel: { color: c.textSecondary },
        splitLine: { lineStyle: { type: "dashed", color: c.border } },
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        // F9：成交量此前**没有任何刻度**（`show: false`），量能只能靠 hover 读。
        // 单位口径与 tooltip 一致（`fmtVolume`：手 / 万手）—— 两个入口两套单位
        // 会让"轴上 3.2万手"与"tooltip 32000手"看起来像两个数（同 F5 那类口径病）。
        // 两个 pane 的 y 轴标签列**纵向不相交**（grid 0 top:28 / grid 1 top:68%），
        // 故窄屏收窄左轴到 46px 也不会压到价格标签；窄屏再降一档字号 + margin，
        // 保证「1234.57万手」这类最长标签仍落在 46px 留白内（400px 画布实测见报告）。
        axisLabel: {
          show: true,
          color: c.textSecondary,
          fontSize: narrow ? 9 : 10,
          margin: narrow ? 2 : 4,
          formatter: (v: number) => fmtVolume(v),
        },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], startValue: tailStart, endValue: points.length - 1 },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        height: 16,
        bottom: 6,
        startValue: tailStart,
        endValue: points.length - 1,
        borderColor: c.border,
        textStyle: { color: c.textSecondary },
        labelFormatter: (v: unknown) => String(v),
      },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
        // 事件气泡：一根 K 线最多一个（同日合并由调用方保证）；空数组时保持结构稳定
        markPoint: {
          symbol: "circle",
          symbolSize: 6,
          animation: false,
          data: markPointData,
          label: { show: false },
          // 文字只在悬停该气泡时出现，避免 8 个标签互相压住（见上方注释）
          emphasis: {
            label: {
              show: true,
              formatter: "{b}",
              position: "top",
              fontSize: 10,
              color: c.textPrimary,
              backgroundColor: c.bgPanel,
              padding: [2, 4],
              borderRadius: 2,
            },
            itemStyle: { borderColor: c.textPrimary, borderWidth: 1 },
          },
        },
        markLine: {
          symbol: "none",
          silent: true,
          lineStyle: { type: "dashed", color: c.flat, width: 1 },
          label: { formatter: () => lastClose.toFixed(2), position: "insideEndTop", fontSize: 10, color: c.flat },
          data: [{ yAxis: lastClose }],
        },
      },
      ...MA_DEFS.filter((d) => visibleMas.includes(d.key)).map((d) => ({
        name: d.key,
        type: "line",
        data: maSeries[d.key] ?? [],
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "none",
        smooth: false,
        lineStyle: { width: 1, color: d.color },
        itemStyle: { color: d.color },
        emphasis: { disabled: true },
      })),
      {
        name: "成交量",
        type: "bar",
        data: points.map((p) => ({
          value: p.volume,
          itemStyle: { color: p.close >= p.open ? c.up : c.down },
        })),
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 8,
      },
    ],
  };
}
