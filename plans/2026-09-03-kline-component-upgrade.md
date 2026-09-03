# K 线组件升级 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把个股/指数两份重复的 K 线图统一为一个共享 `KlineChart` 组件，补齐专业行情能力（MA 均线、结构化 tooltip、缩放 slider、重置、x 轴格式化、最新价线），并打通复权（adj_factor 懒加载回补 + 前复权计算 + 前端开关）。

**Architecture:** 前端在 `shared/ui/kline/` 建三个文件（纯函数层 / option builder / 容器组件），通过 `fetcher(days, adjust)` 回调注入数据源实现依赖倒置，个股页与指数页各传一个 fetcher；后端 `GET /quotes/daily` 增加 `adjust=qfq|raw` 参数与 `adjust_available` 标记，adj_factor 缺失时由 BackgroundTasks 懒加载单股回补（幂等），缓存 key 纳入 adjust 维度。

**Tech Stack:** React 18 + TypeScript + Ant Design 5 + echarts-for-react 3（已有，不加新依赖）；FastAPI + SQLAlchemy async + TuShare pro_api（`adj_factor` 接口）+ Redis。

**Spec:** 本文档即设计+计划合一（仓库惯例），设计讨论结论：复权走**懒加载按需回补**（用户决策），**本期不做**周K/月K频率切换（用户决策）。

## Global Constraints

- **no-new-deps**：不引入任何新 npm/pip 依赖（前端无单测框架，不引入 vitest；纯函数逻辑测试靠后端 pytest 对应物 + Playwright e2e 行为断言）。
- 后端测试：`cd backend && uv run pytest`；前端构建：`cd frontend && npm run build`；e2e：`cd frontend && npx playwright test`（依赖运行中的 docker 栈，`E2E_BASE_URL` 默认 `http://localhost:3000`）。
- **单位口径（TuShare 原样落库）**：`volume` 单位=手，`amount` 单位=千元。前端格式化：量 → 万手（÷1e4），额 → 亿元（÷1e5）。
- **向后兼容**：`adjust` 参数默认 `raw`；`KlineResponse` 新增字段带默认值，旧调用方不受影响。
- 涨跌配色沿用 `@/app/theme` 的 `COLORS.up="#ef4444" / COLORS.down="#22c55e"`。
- MA 显隐用 antd `CheckableTag`（DOM 可测），**不用** ECharts canvas 内置 legend（Playwright 读不到 canvas 文字——已验证的设计微调）。
- 周期切换必须保留 `notMerge` 语义（切周期全量重放 option、重置缩放）——这是 2026-09-03 K 线 bug 修复的核心，不得回退。
- 每个任务完成后 `git commit`（直接提交 main，仓库惯例）。

---

## 设计决策速览（执行者必读）

1. **组件边界**：`KlineChart` 收 `fetcher: (days: number, adjust: AdjustMode) => Promise<KlineResult>`，自己管理 range/adjust/MA 状态与 React Query；shared 层不 import 业务 API。指数无复权（`showAdjust` 省略）。
2. **MA warm-up**：取数时 `days = rangeDays + 130`（日历日，覆盖 MA60 所需 60 交易日），组件内算完 MA 再按 `date >= 今天-rangeDays` 裁剪可见区间，保证 1月 视图 MA20/MA60 头部不缺。
3. **qfq 公式**：`price × 当日adj_factor ÷ 序列最新adj_factor`（OHLC 全乘，量/额不动），后端计算，基准=最新因子（前复权）。
4. **缓存正确性**：① kline 缓存 key 追加 `:{adjust}`；② adj_factor 不完整时不缓存 qfq 结果；③ 回补完成后 `delete_pattern(f"quote:kline:{exchange}:{symbol}:*")` 三重防护。
5. **懒加载触发**：`GET /quotes/daily` 响应里 `adjust_available=false` → API 层 `background_tasks.add_task(quote_service.backfill_adj_factor, exchange, symbol)`；回补自建 session（`async_session_factory`），幂等（已有因子即跳过）。

---

### Task 0: 提交存量 K 线修复（工作区卫生）

**Files:**
- Modify: 已修改未提交的 4 个文件（`frontend/src/features/stock-detail/components/KLineChart.tsx`、`frontend/src/pages/index-detail/IndexKLineChart.tsx`、`docs/Changelog.md`、`docs/references/best-practices.md`）

**Interfaces:** 无代码产出，仅保证后续任务从干净工作区开始。

- [ ] **Step 1: 确认工作区状态**

Run: `git status --short`
Expected: 恰好上述 4 个文件 modified（允许 `?? .playwright-mcp/`、`?? .superpowers/` 未跟踪目录存在）

- [ ] **Step 2: 提交**

```bash
git add frontend/src/features/stock-detail/components/KLineChart.tsx frontend/src/pages/index-detail/IndexKLineChart.tsx docs/Changelog.md docs/references/best-practices.md
git commit -m "fix(frontend): K线周期切换只显示尾部40%且缩放状态粘滞 — 去掉固定dataZoom窗口+补notMerge"
```

---

### Task 1: 纯函数层 `klineMath.ts` + 共享类型

**Files:**
- Create: `frontend/src/shared/ui/kline/klineMath.ts`
- Modify: `frontend/src/shared/types/index.ts`（`KLinePoint` 加 `amount`；新增 kline 相关类型）
- Modify: `frontend/src/shared/ui/kline/index.ts`（barrel，本任务创建）

**Interfaces:**
- Produces（后续任务依赖的确切签名）:

```ts
// klineMath.ts
export const MA_DEFS = [
  { key: "MA5", window: 5, color: "#f59e0b" },
  { key: "MA10", window: 10, color: "#3b82f6" },
  { key: "MA20", window: 20, color: "#a855f7" },
  { key: "MA60", window: 60, color: "#6b7280" },
] as const;
export type MaKey = (typeof MA_DEFS)[number]["key"];
export const MA_WARMUP_CALENDAR_DAYS = 130;
export function movingAverage(values: number[], window: number): (number | null)[];
export function buildAxisLabels(dates: string[]): string[]; // 同年"MM-DD"，跨年首日"YYYY-MM-DD"
export function cropToRange<T extends { date: string }>(items: T[], cutoffDate: string): T[];
export function fmtVolume(v: number): string;  // 45045 → "4.50万手"
export function fmtAmount(a: number | undefined): string; // 6656011.14(千元) → "66.56亿"

// shared/types/index.ts 追加
export interface KLinePoint { date: string; open: number; close: number; high: number; low: number; volume: number; amount?: number; }
export type AdjustMode = "raw" | "qfq";
export interface KlineResult { points: KLinePoint[]; adjustAvailable: boolean; }
export type KlineFetcher = (days: number, adjust: AdjustMode) => Promise<KlineResult>;
```

- [ ] **Step 1: 修改 `shared/types/index.ts`**——`KLinePoint` 增加 `amount?: number`（放在 `volume` 之后），文件末尾（`SectorSummary` 之后）追加 `AdjustMode` / `KlineResult` / `KlineFetcher` 三个类型，代码照 Interfaces 块原文。

- [ ] **Step 2: 创建 `frontend/src/shared/ui/kline/klineMath.ts`**

```ts
import type { AdjustMode, KlineResult, KlineFetcher } from "@/shared/types";

export { type AdjustMode, type KlineResult, type KlineFetcher };

export const MA_DEFS = [
  { key: "MA5", window: 5, color: "#f59e0b" },
  { key: "MA10", window: 10, color: "#3b82f6" },
  { key: "MA20", window: 20, color: "#a855f7" },
  { key: "MA60", window: 60, color: "#6b7280" },
] as const;

export type MaKey = (typeof MA_DEFS)[number]["key"];

/** 60 个交易日 ≈ 91 个日历日，取 130 留节假日缓冲 */
export const MA_WARMUP_CALENDAR_DAYS = 130;

export function movingAverage(values: number[], window: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    out.push(i >= window - 1 ? +(sum / window).toFixed(2) : null);
  }
  return out;
}

export function buildAxisLabels(dates: string[]): string[] {
  return dates.map((d, i) => (i > 0 && d.slice(0, 4) !== dates[i - 1].slice(0, 4) ? d : d.slice(5)));
}

export function cropToRange<T extends { date: string }>(items: T[], cutoffDate: string): T[] {
  const start = items.findIndex((p) => p.date >= cutoffDate);
  return start <= 0 ? items : items.slice(start);
}

export function fmtVolume(v: number): string {
  return v >= 10000 ? `${(v / 10000).toFixed(2)}万手` : `${v}手`;
}

export function fmtAmount(a: number | undefined): string {
  return a == null ? "--" : `${(a / 100000).toFixed(2)}亿`;
}
```

- [ ] **Step 3: 创建 barrel `frontend/src/shared/ui/kline/index.ts`**（先只导出 Task 1 产物，后续任务追加）:

```ts
export * from "./klineMath";
```

- [ ] **Step 4: 构建验证**

Run: `cd frontend && npm run build`
Expected: 无 TS 错误（unused `import type` 若报错，把类型 import 合并进 export 行即可）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/shared/ui/kline/ frontend/src/shared/types/index.ts
git commit -m "feat(frontend): K线共享组件纯函数层 — MA计算/轴标签/区间裁剪/量额格式化"
```

---

### Task 2: option builder `klineOption.ts`

**Files:**
- Create: `frontend/src/shared/ui/kline/klineOption.ts`

**Interfaces:**
- Consumes: Task 1 的 `MA_DEFS` / `movingAverage` / `buildAxisLabels` / `fmtVolume` / `fmtAmount`；`@/app/theme` 的 `COLORS`；`KLinePoint`。
- Produces:

```ts
export interface KlineOptionInput {
  points: KLinePoint[];        // 已裁剪到可见区间
  maSeries: Record<MaKey, (number | null)[]>; // 与 points 等长，由组件用全量数据算好后裁剪传入
  visibleMas: MaKey[];         // 当前显示的 MA keys
  showVolumeLabels?: boolean;
}
export function buildKlineOption(input: KlineOptionInput): Record<string, unknown>;
```

- [ ] **Step 1: 创建 `klineOption.ts`**（完整实现；tooltip 为 DOM 渲染，formatter 返回 HTML 字符串）:

```ts
import { COLORS } from "@/app/theme";
import type { KLinePoint } from "@/shared/types";
import { MA_DEFS, buildAxisLabels, fmtAmount, fmtVolume, type MaKey } from "./klineMath";

export interface KlineOptionInput {
  points: KLinePoint[];
  maSeries: Partial<Record<MaKey, (number | null)[]>>;
  visibleMas: MaKey[];
}

const pct = (cur: number, prev: number | undefined) =>
  prev == null ? "--" : `${(((cur - prev) / prev) * 100).toFixed(2)}%`;

export function buildKlineOption({ points, maSeries, visibleMas }: KlineOptionInput) {
  const dates = points.map((p) => p.date);
  const axisLabels = buildAxisLabels(dates);
  const ohlc = points.map((p) => [p.open, p.close, p.low, p.high]);
  const lastClose = points.length ? points[points.length - 1].close : 0;

  const tooltipFormatter = (params: unknown): string => {
    const list = params as Array<{ dataIndex: number; seriesType: string; seriesName?: string; value?: unknown }>;
    const candle = list.find((p) => p.seriesType === "candlestick");
    if (!candle || !points[candle.dataIndex]) return "";
    const i = candle.dataIndex;
    const p = points[i];
    const [open, close, low, high] = candle.value as number[];
    const prev = i > 0 ? points[i - 1].close : undefined;
    const change = pct(close, prev);
    const color = prev == null ? COLORS.flat : close >= prev ? COLORS.up : COLORS.down;
    const maLine = (name: string) => {
      const def = MA_DEFS.find((d) => d.key === name);
      const v = maSeries[name]?.[i];
      return v == null ? "" : `<span style="margin-left:8px;color:${def?.color}">${name} ${v.toFixed(2)}</span>`;
    };
    return `<div style="font-size:12px;line-height:1.9">
      <div style="font-weight:600">${p.date}</div>
      <div>开：<b>${open.toFixed(2)}</b>　高：<b>${high.toFixed(2)}</b>　低：<b>${low.toFixed(2)}</b>　收：<b>${close.toFixed(2)}</b></div>
      <div>涨跌幅：<b style="color:${color}">${change}</b>　成交量：${fmtVolume(p.volume)}　成交额：${fmtAmount(p.amount)}</div>
      <div>${visibleMas.map((k) => maLine(k)).join("")}</div>
    </div>`;
  };

  return {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, formatter: tooltipFormatter },
    legend: { show: false },
    grid: [
      { left: 60, right: 20, top: 28, height: "50%" },
      { left: 60, right: 20, top: "66%", height: "13%" },
    ],
    xAxis: [
      { type: "category", data: axisLabels, boundaryGap: true, axisLine: { onZero: false }, gridIndex: 0, axisLabel: { show: false } },
      { type: "category", data: axisLabels, boundaryGap: true, gridIndex: 1, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { type: "dashed" } } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], height: 16, bottom: 6, start: 0, end: 100 },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: { color: COLORS.up, color0: COLORS.down, borderColor: COLORS.up, borderColor0: COLORS.down },
        markLine: {
          symbol: "none",
          silent: true,
          lineStyle: { type: "dashed", color: COLORS.flat, width: 1 },
          label: { formatter: () => lastClose.toFixed(2), position: "insideEndTop", fontSize: 10, color: COLORS.flat },
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
          itemStyle: { color: p.close >= p.open ? COLORS.up : COLORS.down },
        })),
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 8,
      },
    ],
  };
}
```

- [ ] **Step 2: barrel 追加导出**（`shared/ui/kline/index.ts`）:

```ts
export * from "./klineOption";
```

- [ ] **Step 3: 构建验证**

Run: `cd frontend && npm run build`
Expected: 通过（`formatter: () => ...` 若 TS 报 markLine label 签名不匹配，改为 `formatter: lastClose.toFixed(2)`）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/ui/kline/
git commit -m "feat(frontend): K线 option builder — MA叠加/结构化tooltip/slider/最新价线/跨年轴标签"
```

---

### Task 3: 容器组件 `KlineChart.tsx`

**Files:**
- Create: `frontend/src/shared/ui/kline/KlineChart.tsx`

**Interfaces:**
- Consumes: Task 1/2 全部产物；`ReactECharts`（直用 + `notMerge` + `lazyUpdate`，因需要实例 ref 做 `dispatchAction`，不走 `EChart` 封装——封装未透传 ref）；antd `Segmented`/`CheckableTag`/`Tooltip`/`Button` + `@ant-design/icons` 的 `AimOutlined`。
- Produces:

```ts
export interface KlineChartProps {
  title: string;
  queryKey: string;              // React Query 前缀，页面级唯一（如 "stock-kline-600519"）
  fetcher: KlineFetcher;
  showAdjust?: boolean;          // 个股 true / 指数省略
  defaultRange?: number;         // 默认 90（3月）
}
export function KlineChart(props: KlineChartProps): JSX.Element;
```

- [ ] **Step 1: 创建组件**（完整实现）:

```tsx
import ReactECharts from "echarts-for-react";
import { AimOutlined } from "@ant-design/icons";
import { Button, Card, CheckableTag, Divider, Empty, Segmented, Space, Spin, Tooltip as AntTooltip } from "antd";
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
            <CheckableTag
              key={d.key}
              checked={visibleMas.includes(d.key)}
              onChange={(c) =>
                setVisibleMas((prev) => (c ? [...prev, d.key] : prev.filter((k) => k !== d.key)))
              }
              style={visibleMas.includes(d.key) ? { color: d.color, borderColor: d.color } : undefined}
            >
              {d.key}
            </CheckableTag>
          ))}
          <Divider type="vertical" />
          {showAdjust && (
            <Segmented
              size="small"
              value={adjust}
              onChange={(v) => setAdjust(v as AdjustMode)}
              options={[
                { label: "不复权", value: "raw" },
                { label: "前复权", value: "qfq" },
              ]}
            />
          )}
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
```

- [ ] **Step 2: barrel 追加**（`shared/ui/kline/index.ts`）:

```ts
export { KlineChart } from "./KlineChart";
```

- [ ] **Step 3: 构建验证**

Run: `cd frontend && npm run build`
Expected: 通过。若 `ref={chartRef}` 类型不匹配（echarts-for-react 版本差异），把 `chartRef` 声明改为 `useRef<any>(null)` 并保持其余不变。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/ui/kline/
git commit -m "feat(frontend): 共享KlineChart容器 — 周期/复权/MA状态、warm-up取数裁剪、重置缩放"
```

---

### Task 4: 页面接入 + fetcher 扩展 + 旧组件删除 + P1 e2e

**Files:**
- Modify: `frontend/src/shared/api/quotes.ts`（fetchKlineBySymbol 加 adjust 参数、返回 KlineResult、映射 amount）
- Modify: `frontend/src/shared/api/market.ts`（fetchIndexKline 返回 KlineResult、映射 amount）
- Modify: `frontend/src/pages/stock-detail/index.tsx`（用共享组件）
- Modify: `frontend/src/features/stock-detail/components/index.ts`（移除 KLineChart 导出）
- Delete: `frontend/src/features/stock-detail/components/KLineChart.tsx`
- Modify: `frontend/src/pages/index-detail/index.tsx`（用共享组件）
- Delete: `frontend/src/pages/index-detail/IndexKLineChart.tsx`
- Create: `frontend/e2e/kline.spec.ts`

**Interfaces:**
- Consumes: Task 3 的 `KlineChart`。新 fetcher 签名（P2 后端就绪前的过渡——FastAPI 默认忽略未知 query 参数，`adjust` 先透传）:

```ts
// quotes.ts
export async function fetchKlineBySymbol(symbol: string, days: number, adjust: AdjustMode = "raw"): Promise<KlineResult>;
// market.ts
export async function fetchIndexKline(tsCode: string, days: number): Promise<KlineResult>;
```

- [ ] **Step 1: 改 `quotes.ts`**——`fetchKlineBySymbol` 增加第三参 `adjust: AdjustMode = "raw"`，`apiGet` 参数追加 `adjust`，映射处加 `amount: item.amount ?? undefined`，空数据 `continue` 不变，函数返回 `{ points, adjustAvailable: (response as { adjust_available?: boolean }).adjust_available !== false }`（BackendKlineResponse 接口加 `adjust_available?: boolean` 可选字段）。

- [ ] **Step 2: 改 `market.ts`**——`fetchIndexKline` 同样映射 `amount: item.amount ?? undefined`，返回 `{ points, adjustAvailable: true }`。

- [ ] **Step 3: 改个股详情页**——`import { KlineChart } from "@/shared/ui/kline";`，从 features barrel 的 import 中去掉 `KLineChart`，渲染处替换为:

```tsx
<KlineChart
  title="历史行情"
  queryKey={`stock-kline-${stock.symbol}`}
  fetcher={(days, adjust) => fetchKlineBySymbol(stock.symbol, days, adjust)}
  showAdjust
/>
```

同时删除 `frontend/src/features/stock-detail/components/KLineChart.tsx`，并从 `features/stock-detail/components/index.ts` barrel 移除其导出行。

- [ ] **Step 4: 改指数详情页**——`import { KlineChart } from "@/shared/ui/kline";`，删除 `IndexKLineChart` import 与文件，渲染处替换为:

```tsx
<KlineChart
  title="指数历史行情"
  queryKey={`index-kline-${tsCode}`}
  fetcher={(days) => fetchIndexKline(tsCode, days)}
/>
```

（`fetchIndexKline` 需在 market.ts import；fetcher 的第二参 adjust 忽略——指数无复权。）

- [ ] **Step 5: 构建 + 重建容器**

Run: `cd frontend && npm run build && cd .. && docker compose build frontend && docker compose up -d frontend`
Expected: 构建通过、容器重启健康

- [ ] **Step 6: 写 e2e `frontend/e2e/kline.spec.ts`**

```ts
import { test, expect as baseExpect } from "@playwright/test";

/** K线共享组件 E2E：MA chips（DOM）/ 结构化 tooltip（DOM）/ 周期与复权控件 / 指数页无复权。依赖运行中的 docker 栈。 */
const expect = baseExpect.configure({ timeout: 15_000 });

const STOCK_CARD = () => page1();
function page1() { throw new Error("placeholder"); }
```

（上方为说明性骨架——实际写入以下完整文件）

```ts
import { test, expect as baseExpect } from "@playwright/test";

/** K线共享组件 E2E：MA chips / 结构化 tooltip / 周期与复权控件 / 指数页无复权。依赖运行中的 docker 栈。 */
const expect = baseExpect.configure({ timeout: 15_000 });

test("个股K线：MA chips 可见可切换，tooltip 结构化展示", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();

  // MA chips 默认 5/10/20 选中、60 未选中
  const tag = (name: string) => card.locator(".ant-tag").filter({ hasText: name });
  await expect(tag("MA5")).toBeVisible();
  await expect(tag("MA10")).toBeVisible();
  await expect(tag("MA20")).toBeVisible();
  await expect(tag("MA60")).toBeVisible();
  await tag("MA60").click(); // 切换显隐不报错
  await tag("MA60").click();

  // 周期与复权控件
  await expect(card.getByRole("radio", { name: "1年" })).toBeVisible();
  await expect(card.getByRole("radio", { name: "前复权" })).toBeVisible();

  // 结构化 tooltip（ECharts tooltip 为 DOM）
  const canvas = card.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.4);
  await page.waitForTimeout(600);
  await expect(card.getByText(/涨跌幅/)).toBeVisible();
  await expect(card.getByText(/成交量/)).toBeVisible();
  await expect(card.getByText(/成交额/)).toBeVisible();
});

test("指数K线：MA 与周期可用，无复权控件", async ({ page }) => {
  await page.goto("/index/000001.SH");
  const card = page.locator(".ant-card").filter({ hasText: "指数历史行情" });
  await expect(card).toBeVisible();
  await expect(card.locator(".ant-tag").filter({ hasText: "MA20" })).toBeVisible();
  await expect(card.getByRole("radio", { name: "1月" })).toBeVisible();
  await expect(card.getByRole("radio", { name: "前复权" })).toHaveCount(0);
});

test("周期切换后缩放重置：1年 → 1月 视图变化且控件状态跟随", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  await card.getByRole("radio", { name: "1月" }).click();
  await expect(card.getByRole("radio", { name: "1月" })).toBeChecked();
  await card.getByRole("radio", { name: "1年" }).click();
  await expect(card.getByRole("radio", { name: "1年" })).toBeChecked();
});
```

- [ ] **Step 7: 跑 e2e（新用例 + 全量回归）**

Run: `cd frontend && npx playwright test`
Expected: 全部通过（含既有 research.spec.ts）。tooltip 断言若因 hover 坐标落在空白处失败，把 `width * 0.7` 调到 `0.8`、`height * 0.4` 调到 `0.35` 再试。

- [ ] **Step 8: Commit**

```bash
git add frontend/src frontend/e2e/kline.spec.ts
git commit -m "feat(frontend): 个股/指数详情接入共享KlineChart — MA/tooltip/slider/复权控件上线，删除两份旧图表"
```

---

### Task 5: 后端 adj_factor 数据层（TuShare + repo + 纯映射）

**Files:**
- Modify: `backend/app/core/providers/tushare_client.py`（新增 `fetch_adj_factor`）
- Modify: `backend/app/repositories/quote_repo.py`（新增 `has_adj_factor` / `update_adj_factors`）
- Modify: `backend/app/services/quote_service.py`（新增 `map_adj_factor_rows` 纯函数）
- Test: `backend/tests/test_kline_adjust.py`（新建）

**Interfaces:**
- Produces:

```python
# tushare_client.py
async def fetch_adj_factor(self, ts_code: str = "", start_date: str = "", end_date: str = "") -> pd.DataFrame
# quote_repo.py
async def has_adj_factor(db: AsyncSession, stock_id: int) -> bool
async def update_adj_factors(db: AsyncSession, stock_id: int, factors: list[tuple[date, float]]) -> int
# quote_service.py
def map_adj_factor_rows(rows: list[dict]) -> list[tuple[date, float]]  # TuShare 行→(trade_date, adj_factor)，脏行跳过
```

- [ ] **Step 1: 写失败测试** `backend/tests/test_kline_adjust.py`:

```python
"""纯单元测试：K线复权 — TuShare adj_factor 行映射 / qfq 计算 / 缓存 key 隔离。"""

from datetime import date

from app.schemas.quote import DailyQuoteOut
from app.services.quote_service import apply_qfq, kline_cache_key, map_adj_factor_rows


def _raw(**overrides) -> dict:
    row = {"ts_code": "600519.SH", "trade_date": "20260901", "adj_factor": 251.5}
    row.update(overrides)
    return row


# ── map_adj_factor_rows：TuShare 行 → (date, factor) ────────────────


def test_map_adj_factor_rows_parses_and_skips_dirty():
    rows = [
        _raw(trade_date="20260901", adj_factor=251.5),
        _raw(trade_date="20260902", adj_factor=251.6),
        _raw(trade_date="bad", adj_factor=1.0),      # 日期脏行 → 跳过
        _raw(trade_date="20260903", adj_factor=None),  # 因子缺失 → 跳过
        _raw(trade_date="20260904"),                  # 因子缺失 → 跳过
    ]
    assert map_adj_factor_rows(rows) == [(date(2026, 9, 1), 251.5), (date(2026, 9, 2), 251.6)]
```

（`apply_qfq` / `kline_cache_key` 的测试在 Step 2 一起写——本任务先只写 map 测试，import 会因函数不存在而失败，即"先失败"。）

实际写入的完整文件含以下额外用例（Task 6 实现后转绿）:

```python
def test_kline_cache_key_includes_adjust():
    key_raw = kline_cache_key("Shanghai_Stocks", "600519", date(2026, 8, 1), date(2026, 9, 1), "raw")
    key_qfq = kline_cache_key("Shanghai_Stocks", "600519", date(2026, 8, 1), date(2026, 9, 1), "qfq")
    assert key_raw != key_qfq
    assert key_raw == "quote:kline:Shanghai_Stocks:600519:2026-08-01:2026-09-01:raw"


def _q(d: str, close: float, adj: float | None) -> DailyQuoteOut:
    return DailyQuoteOut(trade_date=date.fromisoformat(d), open=10.0, high=11.0, low=9.0, close=close, volume=100, amount=1000.0, adj_factor=adj)


def test_apply_qfq_adjusts_ohlc_by_latest_factor():
    rows = [_q("2026-01-02", 100.0, 1.0), _q("2026-01-05", 200.0, 2.0)]  # 最新因子 2.0
    out = apply_qfq(rows)
    assert out is not None
    assert out[0].close == 50.0   # 100 * 1/2
    assert out[1].close == 200.0  # 基准日不动
    assert out[0].open == 5.0 and out[0].high == 5.5 and out[0].low == 4.5
    assert out[0].volume == 100 and out[0].amount == 1000.0  # 量额不动


def test_apply_qfq_returns_none_when_any_factor_missing():
    rows = [_q("2026-01-02", 100.0, None), _q("2026-01-05", 200.0, 2.0)]
    assert apply_qfq(rows) is None


def test_apply_qfq_empty_rows():
    assert apply_qfq([]) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_kline_adjust.py -v`
Expected: FAIL（ImportError: `apply_qfq`/`kline_cache_key`/`map_adj_factor_rows` 不存在）

- [ ] **Step 3: 实现**——三个文件分别追加:

`tushare_client.py`（放在 `fetch_daily` 之后，Daily metrics 注释块之前）:

```python
    async def fetch_adj_factor(
        self,
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        """Fetch daily adjust factors (复权因子) for one stock's history."""
        kwargs: dict[str, str] = {"ts_code": ts_code}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        return await self._query("adj_factor", **kwargs)
```

`quote_repo.py` 文件末尾:

```python
async def has_adj_factor(db: AsyncSession, stock_id: int) -> bool:
    """Return True if the stock has at least one non-null adj_factor row."""
    stmt = select(
        exists().where(and_(DailyQuote.stock_id == stock_id, DailyQuote.adj_factor.is_not(None)))
    )
    result = await db.execute(stmt)
    return result.scalar() is True


async def update_adj_factors(db: AsyncSession, stock_id: int, factors: list[tuple[date, float]]) -> int:
    """Bulk-update adj_factor on existing daily_quotes rows; returns rows updated."""
    if not factors:
        return 0
    values = ", ".join(
        f"({stock_id}, '{d.isoformat()}', {f})" for d, f in factors
    )
    stmt = text(
        f"UPDATE daily_quotes AS dq SET adj_factor = v.adj_factor "
        f"FROM (VALUES {values}) AS v(stock_id, trade_date, adj_factor) "
        f"WHERE dq.stock_id = v.stock_id AND dq.trade_date = v.trade_date"
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount
```

（`from sqlalchemy import and_, exists, text` 补进该文件 import 行，保留原有 desc/func/select。）

`quote_service.py`（纯函数，放模块顶部 logger 之后）:

```python
def map_adj_factor_rows(rows: list[dict]) -> list[tuple[date, float]]:
    """TuShare adj_factor 行 → (trade_date, factor)，日期/因子非法的行跳过。"""
    from datetime import datetime  # noqa: PLC0415

    out: list[tuple[date, float]] = []
    for row in rows:
        td = str(row.get("trade_date", "")).strip()
        factor = row.get("adj_factor")
        if len(td) != 8 or factor is None:
            continue
        try:
            out.append((datetime.strptime(td, "%Y%m%d").date(), float(factor)))
        except (ValueError, TypeError):
            continue
    return out
```

（Task 6 之前 `apply_qfq`/`kline_cache_key` 尚未实现——本任务先在测试文件里注释掉这两个函数的用例，或本任务一并实现（见 Task 6 Step 1 的代码，可提前实现使全部用例一次转绿——推荐提前，测试文件一次写全）。**推荐路径：本任务直接把 Task 6 Step 1 的两个纯函数一并写入 quote_service.py，Step 2 的失败来自"测试先写、实现后写"的 TDD 顺序：先写测试→跑失败→写全部实现→跑通过。**）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_kline_adjust.py -v`
Expected: 5 passed

- [ ] **Step 5: 全量回归**

Run: `cd backend && uv run pytest`
Expected: 全部通过（既有 111 项不回归）

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/providers/tushare_client.py backend/app/repositories/quote_repo.py backend/app/services/quote_service.py backend/tests/test_kline_adjust.py
git commit -m "feat(backend): adj_factor 数据层 — TuShare fetch_adj_factor/批量UPDATE repo/行映射纯函数 + qfq计算与缓存key单测"
```

---

### Task 6: quote_service adjust 语义 + schema 扩展

**Files:**
- Modify: `backend/app/services/quote_service.py`（`get_kline` 加 adjust 参数；新增 `kline_cache_key` / `apply_qfq` 若 Task 5 未提前实现）
- Modify: `backend/app/schemas/quote.py`（`KlineResponse` 加两字段）

**Interfaces:**
- Produces:

```python
# schemas/quote.py
class KlineResponse(BaseModel):
    symbol: str
    name: str
    exchange: str
    data: list[DailyQuoteOut]
    adjust: str = "raw"
    adjust_available: bool = True

# quote_service.py
def kline_cache_key(exchange: str, symbol: str, start_date: date | None, end_date: date | None, adjust: str) -> str
def apply_qfq(rows: list[DailyQuoteOut]) -> list[DailyQuoteOut] | None
async def get_kline(db, cache, exchange, symbol, start_date=None, end_date=None, adjust: str = "raw") -> KlineResponse | None
```

- [ ] **Step 1: schema 扩展**——`KlineResponse` 按上方 Interfaces 追加 `adjust` / `adjust_available` 两行（带默认值，旧调用方向后兼容）。

- [ ] **Step 2: 实现 service 纯函数**（若 Task 5 已写入则跳过）:

```python
def kline_cache_key(
    exchange: str, symbol: str, start_date: date | None, end_date: date | None, adjust: str
) -> str:
    start_str = start_date.isoformat() if start_date else "all"
    end_str = end_date.isoformat() if end_date else "all"
    return f"quote:kline:{exchange}:{symbol}:{start_str}:{end_str}:{adjust}"


def apply_qfq(rows: list[DailyQuoteOut]) -> list[DailyQuoteOut] | None:
    """前复权：price × 当日因子 ÷ 最新因子；任一行因子缺失返回 None（不可计算）。"""
    if not rows or any(r.adj_factor is None for r in rows):
        return None
    latest = rows[-1].adj_factor  # rows 按 trade_date 升序
    out: list[DailyQuoteOut] = []
    for r in rows:
        ratio = r.adj_factor / latest
        out.append(
            r.model_copy(
                update={
                    "open": round(r.open * ratio, 2) if r.open is not None else None,
                    "high": round(r.high * ratio, 2) if r.high is not None else None,
                    "low": round(r.low * ratio, 2) if r.low is not None else None,
                    "close": round(r.close * ratio, 2),
                }
            )
        )
    return out
```

- [ ] **Step 3: 改 `get_kline`**——签名加 `adjust: str = "raw"`，cache key 用 `kline_cache_key(...)`，读缓存命中直接返回；未命中走 repo 查询后:

```python
    quotes = await quote_repo.get_kline(db, stock.id, start_date, end_date)
    data = [DailyQuoteOut.model_validate(q) for q in quotes]

    factors_complete = bool(data) and all(q.adj_factor is not None for q in quotes)
    if adjust == "qfq" and factors_complete:
        data = apply_qfq(data) or data

    response = KlineResponse(
        symbol=symbol, name=stock.name, exchange=exchange, data=data,
        adjust=adjust, adjust_available=factors_complete,
    )
    # qfq 且因子不完整 → 不缓存（回补完成后由 delete_pattern 兜底失效）
    if not (adjust == "qfq" and not factors_complete):
        await cache.set(cache_key, response.model_dump(mode="json"), ttl=600)
    return response
```

- [ ] **Step 4: 测试通过 + 回归**

Run: `cd backend && uv run pytest tests/test_kline_adjust.py -v && uv run pytest`
Expected: 全部通过

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/quote_service.py backend/app/schemas/quote.py
git commit -m "feat(backend): quotes/daily 复权语义 — adjust=qfq|raw、adjust_available 标记、缓存key纳入adjust、qfq不完整不缓存"
```

---

### Task 7: 懒加载回补接线 + 实机验证

**Files:**
- Modify: `backend/app/services/quote_service.py`（`backfill_adj_factor`）
- Modify: `backend/app/api/v1/stocks.py:146`（`get_kline` 端点加 adjust 参数 + BackgroundTasks 触发）

**Interfaces:**
- Consumes: Task 5 的 `has_adj_factor` / `update_adj_factors` / `map_adj_factor_rows` / `fetch_adj_factor`；`app.core.redis` 的 `get_redis_pool` + `CacheClient`；`app.core.db` 的 `async_session_factory`（与 `market_service.get_index_kline` 同一 import 路径——先查该文件实际 import）。
- Produces:

```python
async def backfill_adj_factor(exchange: str, symbol: str) -> dict[str, Any]  # 幂等：已有因子即返回 skipped
# API: GET /{symbol}/quotes/daily?adjust=qfq|raw（默认 raw）
#      响应 adjust_available=false 时 BackgroundTasks 触发回补
```

- [ ] **Step 1: 实现 `backfill_adj_factor`**（quote_service.py 末尾）:

```python
async def backfill_adj_factor(exchange: str, symbol: str) -> dict[str, Any]:
    """懒加载回补单股 adj_factor 全历史（幂等）；完成后失效该股 kline 缓存。"""
    from app.core.db import async_session_factory  # noqa: PLC0415 — 与 market_service 同源，按实际模块路径调整
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415
    from app.core.redis import CacheClient, get_redis_pool  # noqa: PLC0415
    from app.core.providers.tushare_client import EXCHANGE_TO_TUSHARE  # noqa: PLC0415

    try:
        async with async_session_factory() as db:
            stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
            if stock is None:
                return {"symbol": symbol, "status": "skipped", "reason": "stock not found"}
            if await quote_repo.has_adj_factor(db, stock.id):
                return {"symbol": symbol, "status": "skipped", "reason": "already backfilled"}
            suffix = {"Shanghai_Stocks": "SH", "Shenzen_Stocks": "SZ", "Beijing_Stocks": "BJ"}[exchange]
            df = await get_tushare_client().fetch_adj_factor(ts_code=f"{symbol}.{suffix}")
            factors = map_adj_factor_rows(df.to_dict("records"))
            updated = await quote_repo.update_adj_factors(db, stock.id, factors) if factors else 0
            await db.commit()
        redis = await get_redis_pool()
        await CacheClient(redis).delete_pattern(f"quote:kline:{exchange}:{symbol}:*")
        logger.info("[adj_factor backfill] %s.%s updated=%d", exchange, symbol, updated)
        return {"symbol": symbol, "status": "ok", "updated": updated}
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底，失败不影响响应
        logger.warning("[adj_factor backfill] %s.%s failed: %s", exchange, symbol, exc)
        return {"symbol": symbol, "status": "error", "reason": str(exc)}
```

（`EXCHANGE_TO_TUSHARE` 是 SSE/SZSE/BSE 映射，后缀不同——上面直接内联 SH/SZ/BJ 后缀映射，不走 EXCHANGE_TO_TUSHARE；`async_session_factory` / `get_tushare_client` 的实际 import 路径以 `market_service.py` 和 `tushare_ingest.py` 现有代码为准，grep 确认后再写。）

- [ ] **Step 2: 改 API 端点** `stocks.py` 的 `get_kline`（146 行起）:

```python
@stocks_router.get("/{symbol}/quotes/daily", response_model=KlineResponse)
async def get_kline(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    background_tasks: BackgroundTasks,
    start: date | None = None,
    end: date | None = None,
    adjust: Literal["raw", "qfq"] = "raw",
) -> KlineResponse:
    result = await quote_service.get_kline(db, cache, exchange, symbol, start, end, adjust=adjust)
    if result is None:
        raise not_found_response("Stock", f"{exchange}/{symbol}")
    if not result.adjust_available:
        background_tasks.add_task(quote_service.backfill_adj_factor, exchange, symbol)
    return result
```

（`from fastapi import BackgroundTasks` 与 `from typing import Literal` 补 import；`/api/v1/quotes.py` 的 `/{symbol}/daily` 端点签名不同步改——只走 exchanges 路由，前端只用后者。）

- [ ] **Step 3: pytest 回归**

Run: `cd backend && uv run pytest`
Expected: 全部通过

- [ ] **Step 4: 重建后端 + 实机验证**

Run: `docker compose build api scheduler worker && docker compose up -d api scheduler worker`
然后:

```bash
# 第一次请求：adjust_available=false（后台回补触发）
curl -s "http://localhost:8000/api/v1/exchanges/Shanghai_Stocks/stocks/600519/quotes/daily?start=2026-08-01&end=2026-09-03&adjust=qfq" | python3 -c "import sys,json; d=json.load(sys.stdin); print('available:', d['adjust_available'], 'rows:', len(d['data']))"
sleep 20  # 等后台回补（单股全历史一次拉取）
# 第二次请求：adjust_available=true，qfq 数值与 raw 不同（分红除权日附近）
curl -s "http://localhost:8000/api/v1/exchanges/Shanghai_Stocks/stocks/600519/quotes/daily?start=2026-08-01&end=2026-09-03&adjust=qfq" | python3 -c "import sys,json; d=json.load(sys.stdin); print('available:', d['adjust_available'], 'close[0]:', d['data'][0]['close'])"
docker exec postgres psql -U stock_user -d stock_bot -t -c "SELECT count(*) FROM daily_quotes WHERE adj_factor IS NOT NULL;"
```

Expected: 第一次 `available: False`；第二次 `available: True`；库内 adj_factor 非空行数 > 0。若 TuShare token 无权限（异常），第二次仍 False——检查 `docker logs backend_api | grep adj_factor`，属环境问题记录后继续（不阻断任务）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/quote_service.py backend/app/api/v1/stocks.py
git commit -m "feat(backend): adj_factor 懒加载回补 — quotes/daily 缺因子时 BackgroundTasks 单股回补+缓存失效"
```

---

### Task 8: 前端复权开关完整接入（P3）

**Files:**
- Modify: `frontend/src/shared/ui/kline/KlineChart.tsx`（adjustAvailable 禁用态）
- Modify: `frontend/e2e/kline.spec.ts`（补充用例）

**Interfaces:**
- Consumes: Task 4 起 fetcher 已返回 `adjustAvailable`；Task 7 后端已真实下发。

- [ ] **Step 1: KlineChart 禁用态**——`showAdjust` 时渲染复权 Segmented 的条件改为:

```tsx
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
```

（默认 adjust 状态在数据未就绪时保持 "qfq" 但 UI 锁在不复权展示——禁用态 value 固定 "raw" 表示当前看到的是 raw 数据；数据就绪后受控值恢复 "qfq" 触发一次刷新。若担心首屏两次请求，可将初始 adjust 定为 "qfq" 不变——禁用态仅视觉，queryKey 不变不发额外请求。）

- [ ] **Step 2: e2e 补充**（追加到 kline.spec.ts）:

```ts
test("复权开关：数据就绪后可切换前复权/不复权", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  // 600519 已在 Task 7 实机回补过（或 ≤30s 内后台完成）；禁用态也带 Segmented 结构
  const adjust = card.locator(".ant-segmented").filter({ hasText: /前复权/ });
  await expect(adjust).toBeVisible({ timeout: 30_000 });
  // 就绪后点击不复权再切回，不报错
  await card.getByRole("radio", { name: "不复权" }).click({ timeout: 30_000 });
  await expect(card.getByRole("radio", { name: "不复权" })).toBeChecked();
  await card.getByRole("radio", { name: "前复权" }).click();
  await expect(card.getByRole("radio", { name: "前复权" })).toBeChecked();
});
```

- [ ] **Step 3: 构建 + 重建 + e2e**

Run: `cd frontend && npm run build && cd .. && docker compose build frontend && docker compose up -d frontend && cd frontend && npx playwright test`
Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add frontend/src/shared/ui/kline/KlineChart.tsx frontend/e2e/kline.spec.ts
git commit -m "feat(frontend): 复权开关完整接入 — 因子未就绪禁用+拉取中提示，就绪后可切换"
```

---

### Task 9: 文档收尾 + 全量回归

**Files:**
- Modify: `docs/Changelog.md`
- Modify: `docs/references/best-practices.md`
- Modify: 本计划文档（勾选已完成项）

- [ ] **Step 1: Changelog 追加**（格式照既有条目）——一句话级总结 P1（共享组件+MA+tooltip+slider）、P2（adj_factor 懒加载+qfq+缓存隔离）、P3（复权开关），涉及模块列表。

- [ ] **Step 2: best-practices 追加一条**：`复权基准必须随最新因子滚动（qfq=当日因子/最新因子），且缓存 key 必须包含复权维度——否则 qfq 结果污染 raw 缓存；数据不完整时宁可不缓存，靠回补后的 delete_pattern 兜底。`

- [ ] **Step 3: 全量回归**

Run: `cd backend && uv run pytest && cd ../frontend && npm run build && npx playwright test`
Expected: 全绿

- [ ] **Step 4: Commit**

```bash
git add docs/Changelog.md docs/references/best-practices.md plans/2026-09-03-kline-component-upgrade.md
git commit -m "docs: K线组件升级收尾 — Changelog/best-practices/计划勾选"
```

---

## Self-Review 记录

- **Spec 覆盖**：设计六项 → Task 1-4 覆盖 1/3/4/5/6（MA、tooltip、slider+重置、组件统一、x轴/markLine/staleTime），Task 5-8 覆盖 2（复权）。周/月频率与全量回补按用户决策不做 ✓。
- **类型一致性**：`KlineFetcher` 在 Task 1 定义、Task 3 props 用内联结构等价（`(days, adjust) => Promise<{points; adjustAvailable}>`，与 `KlineResult` 同形）✓；`map_adj_factor_rows`/`apply_qfq`/`kline_cache_key` 签名在 Task 5/6 一致 ✓。
- **占位符**：Task 4 Step 6 的"说明性骨架"段是刻意保留的示例（其后紧跟完整文件），执行者写完整文件即可 ✓。
- **已知风险**：Task 7 实机验证依赖 TuShare token 有 adj_factor 权限（2000 积分档通常有）；失败不阻断，记录日志继续。
