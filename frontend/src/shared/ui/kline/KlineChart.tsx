import ReactECharts from "echarts-for-react";
import { AimOutlined } from "@ant-design/icons";
import { Button, Card, Empty, Segmented, Space, Spin, Tooltip as AntTooltip } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "@/app/theme-context";
import { useIsNarrowViewport } from "@/shared/ui";
import type { AdjustMode, KLinePoint } from "@/shared/types";
import { buildKlineOption } from "./klineOption";
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
}

export function KlineChart({ title, queryKey, fetcher, showAdjust = false }: KlineChartProps) {
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

  // ⚠️ 依赖里**没有** hoveredIndex：option 与光标解耦（hover 只驱动下面的 DOM 行）。
  const option = useMemo(
    () => buildKlineOption({ points, maSeries, visibleMas, colors, narrow }),
    [points, maSeries, visibleMas, colors, narrow],
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
      const raw = (event as AxisPointerEvent | undefined)?.axesInfo?.[0]?.value;
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
    return () => {
      instance.off("updateAxisPointer", onAxisPointer);
      instance.off("globalout", onLeave);
      if (hoverRaf.current != null) {
        cancelAnimationFrame(hoverRaf.current);
        hoverRaf.current = null;
      }
      pendingHover.current = null;
    };
  }, [points, chartEpoch]);

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
        <div style={{ height: 400, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin />
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
