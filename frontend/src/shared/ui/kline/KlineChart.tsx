import ReactECharts from "echarts-for-react";
import { AimOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Empty, Segmented, Space, Spin, Tooltip as AntTooltip } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "@/app/theme-context";
import { useIsNarrowViewport } from "@/shared/ui";
import type { AdjustMode, KLinePoint } from "@/shared/types";
import { buildKlineOption, type KlineEventMarker, type KlineFocusRequest } from "./klineOption";
import { maValuesAt, ribbonRow } from "./ribbon";
import {
  MA_DEFS,
  aggregateDaily,
  movingAverage,
  type KlineFreq,
  type MaKey,
} from "./klineMath";

const STALE_TIME = 5 * 60 * 1000;
const DEFAULT_VISIBLE_MAS: MaKey[] = ["MA5", "MA10", "MA20"];
/** 「定位到某日」时左右各留 20 根（brief 指定 `idx-20 … idx+20`）。 */
const FOCUS_HALF_WINDOW = 20;

/**
 * ribbon 单行高度（px）：**实测 19**（1440×900 下 `boundingBox().height`），
 * 不等于 CSS 里的 `line-height: 18px` —— 「开 1311.57」这类格子混排了 PingFang（中文）
 * 与 `--font-num` 等宽数字，两种字体的基线对齐后行盒多出 1px。
 *
 * 它是画布之外**实实在在多出来的一行**：画布 400 + ribbon 19 = 实测卡片 482（与
 * `pages/stock-detail/index.tsx` 的 `KLINE_CARD_MIN_HEIGHT` 同口径）。所以加载态占位
 * 必须把这一行一起预留，否则数据落地时卡片会从 463 长到 482、下方各块整体下移。
 *
 * 只定义这一处：ribbon 的 `minHeight` 与加载态高度都引用它，两边不会漂。
 * 窄屏（`<768`）不渲染 ribbon（计划 §5），故窄屏不预留。
 */
const RIBBON_HEIGHT = 19;

/**
 * `updateAxisPointer` 的事件体只承诺"轴上有值"，不承诺形态。
 *
 * 类目轴（本图两条 xAxis 都是 `type: "category"`）实测给的是 **dataIndex 数字**
 * （echarts 5.6：`e.axesInfo[0].value === 744`），但同一个事件在别的配置下可能是
 * 类目名/字符串；取值一律防御（见 `readHoverIndex`），坏值不写 state。
 */
interface AxisPointerEvent {
  axesInfo?: Array<{ axisDim?: string; axisIndex?: number; value?: unknown }>;
}

export interface KlineChartProps {
  title: string;
  queryKey: string;
  fetcher: (days: number, adjust: AdjustMode) => Promise<{ points: KLinePoint[]; adjustAvailable: boolean }>;
  showAdjust?: boolean;
  /** 事件气泡（领域无关；stock-detail 层负责把公告/龙虎榜等映射到这里） */
  eventMarkers?: KlineEventMarker[];
  /** 定位请求：`nonce` 变化时把视窗移到第 `index` 根并高亮（不重新取数） */
  focusRequest?: KlineFocusRequest;
}

export function KlineChart({
  title, queryKey, fetcher, showAdjust = false, eventMarkers, focusRequest,
}: KlineChartProps) {
  const { colors } = useTheme();
  // 契约 §5.1 的 `<768`：窄屏形态 = 最近 30 根 + 手势缩放（**不隐藏**，见 §4.4）
  const narrow = useIsNarrowViewport();
  const [freq, setFreq] = useState<KlineFreq>("day");
  const [adjust, setAdjust] = useState<AdjustMode>(showAdjust ? "qfq" : "raw");
  const [visibleMas, setVisibleMas] = useState<MaKey[]>(DEFAULT_VISIBLE_MAS);
  /**
   * 光标所在的数据下标（`null` = 没在悬停 ⇒ 回落最后一根）。
   *
   * ⚠️ 这个 state **只驱动 DOM**（ribbon + MA 行），绝不进入 `buildKlineOption` 的
   * useMemo 依赖：option 含 2400+ 根点，`notMerge` 下每次 hover 重建会让图表掉帧。
   * 有 `e2e/unit-kline-option.spec.ts` 的源码门禁盯着这条约束。
   */
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  /**
   * `onChartReady` 的代数（不是布尔）。
   *
   * ⚠️ 不是多余的 setState 舞蹈：echarts-for-react 3.x 的 `initEchartsInstance()` 是
   * **异步两段式** —— 先 `echarts.init()` 一个临时实例，等 `finished` 事件拿到真实尺寸后
   * `dispose()` 它、再 init 一个**新实例**。因此父组件 effect 里
   * `chartRef.current.getEchartsInstance()` 在首帧拿到的可能是那个**将被销毁**的临时实例，
   * `instance.on(...)` 订阅随之蒸发（实测现象：hover 完全不更新 ribbon，且
   * `instance._$handlers` 为空）。`onChartReady` 在换实例之后才回调，用它把 effect 再
   * 触发一次，订阅才挂在活实例上。
   */
  const [chartEpoch, setChartEpoch] = useState(0);
  const chartRef = useRef<InstanceType<typeof ReactECharts> | null>(null);
  /** rAF 节流：mousemove 远快于一帧，只把**本帧最后一次**索引交给 React。 */
  const hoverRaf = useRef<number | null>(null);
  const pendingHover = useRef<number | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["kline", queryKey, adjust],
    queryFn: () => fetcher(3650, adjust), // 10年窗口 = 库内全量，频率切换纯前端聚合
    staleTime: STALE_TIME,
    // 复权因子后台回补中：禁用态提示"稍后自动可用"，10s 轮询直至就绪后停止
    refetchInterval: (q) => (q.state.data?.adjustAvailable === false ? 10_000 : false),
  });

  /**
   * 失败 ≠ 无记录（计划 §6 对**每一块**成立）。
   *
   * 旧实现只解构 `{ data, isLoading }`：`/quotes/daily` 500/断网时 `data` 仍是 `undefined`，
   * 于是落到 `<Empty description="暂无K线数据"/>` —— 一次请求失败被页面写成"这只票没有 K 线"
   * 这一条事实。同一屏的报价条（共用同一缓存键）此时显示「加载失败 · 重试」，两处自相矛盾。
   *
   * 重试在途时用本地 `retrying` 撑住错误态：React Query v5 的 `fetchState()` 在 refetch
   * 一开始就把 `error` 清成 `null`、`status` 回落 `pending`，只看 `isError` 的话点「重试」
   * 会立刻闪成空态（又回到"看起来像没有数据"）。与 `RelatedEvents` / `GrowthCard` 同一手法。
   */
  const [retrying, setRetrying] = useState(false);
  const handleRetry = async () => {
    setRetrying(true);
    try {
      await refetch();
    } finally {
      setRetrying(false);
    }
  };
  const failed = isError || retrying;

  const points = useMemo(() => aggregateDaily(data?.points ?? [], freq), [data, freq]);

  const maSeries = useMemo(() => {
    const closes = points.map((p) => p.close);
    const out: Partial<Record<MaKey, (number | null)[]>> = {};
    for (const def of MA_DEFS) out[def.key] = movingAverage(closes, def.window);
    return out;
  }, [points]);

  // ⚠️ 依赖里**没有** hoveredIndex：option 与光标解耦（光标只驱动下面的 DOM 行）。
  // `eventMarkers` 是"画什么"的一部分（数据结构化后由上层 memo 稳定引用），
  // 因此它进依赖；每次光标移动都不会重建 option。
  const option = useMemo(
    () => buildKlineOption({ points, maSeries, visibleMas, colors, narrow, eventMarkers }),
    [points, maSeries, visibleMas, colors, narrow, eventMarkers],
  );

  /**
   * 订阅 ECharts 的 axisPointer 事件 → `hoveredIndex`。
   *
   * 为什么是「effect + chartEpoch」而不是在 `onChartReady` 里直接一次性订阅：
   * `points` 变化（频率/复权切换）后必须重订阅并清掉旧索引（旧索引对新序列无意义），
   * 而 `onChartReady` 只在换实例时回调；卸载时也必须 `off` + `cancelAnimationFrame`，
   * 否则图表被销毁后 rAF 回调仍会 setState。`chartEpoch` 负责"实例换代"这一路触发，
   * `points` 负责"数据换代"这一路（见上一条注释：首帧拿到的是临时实例）。
   */
  useEffect(() => {
    const instance = chartRef.current?.getEchartsInstance();
    if (!instance) return;
    // 数据换了：上一份序列的 hover 索引不再指向同一根 K 线
    setHoveredIndex(null);
    const dateIndex = new Map(points.map((p, i) => [p.date, i]));

    const readHoverIndex = (event: unknown): number | null => {
      /**
       * 事件体只承诺"某条轴上有值"，**不承诺是哪条轴**。两个 y 轴的 `value` 是**价格**
       * （如 1340），`Math.trunc` 之后会变成一个语法上完全合法的下标 —— ribbon 会静默
       * 显示第 1340 根（或任意相邻一根），而不是光标下的那根，且不抛任何错。所以：
       * 只要事件里**声明了**轴身份，就只认 candlestick 所在的 x 轴
       * （`axisDim === "x"`，`axisIndex === 0`；`axisIndex` 缺失时按 x 轴接受）；
       * 一条都不符合 ⇒ 返回 null，宁可回落最后一根也不写下坏值。
       * 字段整体缺失（其它 echarts 版本 / 合成事件）才退回原来的 `axesInfo[0]`。
       */
      const axes = (event as AxisPointerEvent | undefined)?.axesInfo ?? [];
      const identitiesDeclared = axes.some((axis) => axis.axisDim !== undefined);
      const entry = identitiesDeclared
        ? axes.find((axis) => axis.axisDim === "x" && (axis.axisIndex == null || axis.axisIndex === 0))
        : axes[0];
      const raw = entry?.value;
      if (typeof raw === "number" && Number.isFinite(raw)) return Math.trunc(raw);
      if (typeof raw === "string" && raw !== "") {
        const numeric = Number(raw);
        if (Number.isFinite(numeric)) return Math.trunc(numeric);
        // 同一事件在别的配置下可能给类目名（日期串）而不是下标
        return dateIndex.get(raw) ?? null;
      }
      return null;
    };

    const flush = () => {
      hoverRaf.current = null;
      const next = pendingHover.current;
      pendingHover.current = null;
      setHoveredIndex(next);
    };
    const schedule = (index: number | null) => {
      pendingHover.current = index;
      if (hoverRaf.current == null) {
        hoverRaf.current = window.requestAnimationFrame(flush);
      }
    };

    const onAxisPointer = (event: unknown) => {
      const index = readHoverIndex(event);
      // 索引收敛到 [0, points.length - 1]；取不到 = 光标已离开有效区 ⇒ 回落最后一根
      schedule(index == null ? null : Math.min(Math.max(index, 0), points.length - 1));
    };
    // 光标移出画布（不是每条 axisPointer 都带 leave），显式清掉 hover 态
    const onLeave = () => schedule(null);

    instance.on("updateAxisPointer", onAxisPointer);
    instance.on("globalout", onLeave);

    /**
     * 仅 dev：把实例挂到 `window`，供 e2e 读**真实**的 option / dataZoom 状态。
     *
     * 为什么必须这样：事件气泡与缩放窗口都画在 canvas 里，DOM 没有任何对应节点；
     * 而 echarts 的实例注册表（`getInstanceByDom`）是模块私有的，`page.evaluate`
     * 里拿不到 `echarts` 模块，也就没法断言 `markPoint` 与 `startValue/endValue`。
     * 生产构建下 `import.meta.env.DEV === false`，整段是 no-op（与
     * `features/market/heatmapPerfProbe.ts` 同一手法，不污染线上 window）。
     */
    if (import.meta.env.DEV) {
      (window as unknown as { __klineEchartsInstance?: unknown }).__klineEchartsInstance = instance;
    }
    return () => {
      instance.off("updateAxisPointer", onAxisPointer);
      instance.off("globalout", onLeave);
      if (hoverRaf.current != null) {
        cancelAnimationFrame(hoverRaf.current);
        hoverRaf.current = null;
      }
      pendingHover.current = null;
      if (import.meta.env.DEV) {
        const devWindow = window as unknown as { __klineEchartsInstance?: unknown };
        if (devWindow.__klineEchartsInstance === instance) delete devWindow.__klineEchartsInstance;
      }
    };
  }, [points, chartEpoch]);

  /**
   * 「点列表某行 → 图定位到该日」（P1-2）。
   *
   * 只做两件事：`dataZoom`（把视窗移到 `idx±20` 根）+ `showTip`（该日蜡烛高亮/出 tooltip），
   * **不重新取数**（数据早已在）。`nonce` 是"这次点击"的代数：同一根 K 线连点两次
   * 也要重新执行（否则第二次点击毫无反馈）。
   *
   * ⚠️ **只在日K生效**：`focusRequest.index` 是上层用**日线**日期解析出来的（`useKlineDates`），
   * 而周/月K 画的是 `aggregateDaily(points, freq)` 聚合后的、**更短**的序列。把日线下标
   * 直接夹到聚合序列上会落到错误的蜡烛上，ribbon 随后显示一个**错误日期** —— 那比"不跳"
   * 更糟（静默的事实错误）。因此非日K 时直接 no-op；这一限制由事件列表脚注披露。
   * `freq` 刻意**不进依赖**：定位只在"这次点击发生时的频率"下判定一次，切频率不该
   * 让上一次点击复活。
   *
   * 索引来自上层（`indexOfDate(dates, date)`），越界在此收敛；`points` 用 ref 读，
   * 是为了**不把 points 放进依赖**——否则数据刷新（复权因子回补触发的 10s 轮询）
   * 会把用户刚定位的视窗又拽回去。
   */
  const pointsRef = useRef(points);
  pointsRef.current = points;
  const focusIndex = focusRequest?.index ?? null;
  const focusNonce = focusRequest?.nonce ?? null;

  useEffect(() => {
    if (focusIndex == null) return;
    // 日线下标 ≠ 聚合序列下标（见上方注释）：周/月下宁可不跳，也不跳到错误日期
    if (freq !== "day") return;
    const instance = chartRef.current?.getEchartsInstance();
    if (!instance) return;
    const total = pointsRef.current.length;
    if (total === 0) return;
    const idx = Math.min(Math.max(focusIndex, 0), total - 1);
    instance.dispatchAction({
      type: "dataZoom",
      startValue: Math.max(0, idx - FOCUS_HALF_WINDOW),
      endValue: Math.min(total - 1, idx + FOCUS_HALF_WINDOW),
    });
    instance.dispatchAction({ type: "showTip", seriesIndex: 0, dataIndex: idx });
    // ribbon / MA 行跟到该日：canvas 上的高亮肉眼可见，但读数是 DOM，这里让两处同源
    setHoveredIndex(idx);
  }, [focusIndex, focusNonce, chartEpoch]);

  // 当前展示哪一根：悬停优先，否则最后一根（越界一律收敛，避免 fraction 索引）
  const dataIndex =
    points.length === 0
      ? -1
      : hoveredIndex == null
        ? points.length - 1
        : Math.min(Math.max(hoveredIndex, 0), points.length - 1);
  // MA 行与 ribbon **同一个**索引源，保证两行说的永远是同一根 K 线
  const maValues = useMemo(
    () => maValuesAt(maSeries, visibleMas, dataIndex),
    [maSeries, visibleMas, dataIndex],
  );
  const ribbon = useMemo(() => ribbonRow(points, dataIndex), [points, dataIndex]);
  // 涨跌色沿用 tooltip 的口径：与前收比较，首根中性（flat）
  const changeColor =
    dataIndex > 0 && points[dataIndex] && points[dataIndex - 1]
      ? points[dataIndex].close >= points[dataIndex - 1].close
        ? colors.up
        : colors.down
      : colors.flat;
  // 图表网格的左留白（窄屏收窄，§4.4）：ribbon 与 MA 行都必须跟它对齐，否则两行会
  // 相对画布"往左偏 14px"，看起来像没对齐的表格。
  const axisLeft = narrow ? 46 : 60;

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
        // 400（画布）+ 19（宽屏 ribbon 行，`RIBBON_HEIGHT` 实测值）= 落地后的真实高度：
        // 不预留这一行，数据一到卡片就长一截，下面的块整体下移（CLS 只在事件卡上量过，
        // 漏了这里 —— 左栏比右栏矮，事件卡的位置被侧栏挡住，量不到左栏自己的增长）。
        <div
          style={{
            height: 400 + (narrow ? 0 : RIBBON_HEIGHT),
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <Spin />
        </div>
      ) : failed ? (
        // 高度与画布同为 400：失败态不改变卡片高度，下面的块不会因为一次 500 而搬家
        <div
          style={{
            height: 400,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "0 16px",
          }}
        >
          <Alert
            type="error"
            showIcon
            data-testid="kline-error"
            style={{ width: "100%" }}
            message="加载失败 · 不代表没有K线数据"
            description="本次请求未能完成，请重试。"
            action={
              <Button
                size="small"
                loading={retrying}
                disabled={retrying}
                data-testid="kline-retry"
                onClick={() => void handleRetry()}
              >
                重试
              </Button>
            }
          />
        </div>
      ) : points.length > 0 ? (
        <div>
          {/*
            F10 ribbon：`日期 开 高 低 收 涨跌幅 量 额`，跟随 hover，未悬停时回落最后一根。
            窄屏（`<768`）**不渲染**：320px 画布里两行 DOM 会吃掉 60px，信息回落到 tooltip
            （计划 §5 移动端专章）。宽屏放在 canvas 之上（普通文档流，不遮挡 K 线）。
          */}
          {!narrow && ribbon && (
            <div
              data-testid="kline-ribbon"
              style={{
                display: "flex",
                flexWrap: "wrap",
                alignItems: "baseline",
                gap: 12,
                paddingLeft: axisLeft,
                paddingRight: 20,
                fontSize: 11,
                lineHeight: "18px",
                // 行盒高度钉成与加载态预留值一致（`RIBBON_HEIGHT`）：改了这一行的高度，
                // 加载态的占位必须同步，否则就是一次位移。
                minHeight: RIBBON_HEIGHT,
                color: colors.flat,
              }}
            >
              <span data-testid="kline-ribbon-date" style={{ color: colors.textPrimary, fontWeight: 600 }}>
                {ribbon.date}
              </span>
              <span>
                开 <span className="num" style={{ color: colors.textPrimary }}>{ribbon.open}</span>
              </span>
              <span>
                高 <span className="num" style={{ color: colors.textPrimary }}>{ribbon.high}</span>
              </span>
              <span>
                低 <span className="num" style={{ color: colors.textPrimary }}>{ribbon.low}</span>
              </span>
              <span>
                收 <span className="num" style={{ color: colors.textPrimary }}>{ribbon.close}</span>
              </span>
              <span>
                涨跌幅 <span className="num" style={{ color: changeColor }}>{ribbon.changePct}</span>
              </span>
              <span>
                量 <span className="num" style={{ color: colors.textPrimary }}>{ribbon.volume}</span>
              </span>
              <span>
                额 <span className="num" style={{ color: colors.textPrimary }}>{ribbon.amount}</span>
              </span>
            </div>
          )}
          <div style={{ position: "relative" }}>
            {points.length > 0 && (
              // MA 数值行：窄屏必须 `flexWrap` + 左偏移跟随网格（`narrow ? 46 : 60`）。
              // 实测成因：写死 `left: 66` 且不换行时，四个 MA（`MA5 12.34` 这类）在 308px 宽里
              // 排成一行会溢出卡片右侧，并把上面的 K 线图区域挤窄（§5.1「不横向溢出」）。
              // 数值同样跟随 hoveredIndex（`maValues`），未悬停回落最后一根。
              <div
                data-testid="kline-ma-legend"
                style={{
                  position: "absolute",
                  top: 6,
                  left: axisLeft,
                  right: narrow ? 12 : 20,
                  display: "flex",
                  gap: narrow ? 6 : 12,
                  flexWrap: "wrap",
                  fontSize: narrow ? 10 : 11,
                  zIndex: 5,
                }}
              >
                {MA_DEFS.map((d) => {
                  const on = visibleMas.includes(d.key);
                  const v = maValues[d.key];
                  return (
                    <span
                      key={d.key}
                      onClick={() =>
                        setVisibleMas((prev) => (on ? prev.filter((k) => k !== d.key) : [...prev, d.key]))
                      }
                      style={{ color: on ? d.color : colors.flat, cursor: "pointer", userSelect: "none" }}
                    >
                      {d.key} {on ? (v ?? "--") : ""}
                    </span>
                  );
                })}
              </div>
            )}
            <ReactECharts
              ref={chartRef}
              option={option}
              notMerge
              lazyUpdate
              style={{ height: 400 }}
              // 临时实例被 dispose、真实例就绪后再触发一次订阅（见 chartEpoch 的注释）
              onChartReady={() => setChartEpoch((n) => n + 1)}
            />
          </div>
        </div>
      ) : (
        <div style={{ height: 400, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Empty description="暂无K线数据" />
        </div>
      )}
    </Card>
  );
}
