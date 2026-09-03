# K线 P4 UI 迭代 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按主流行情终端（东方财富式）重构 K 线区交互——MA 参数从工具栏移入图表左上角彩色数值行、频率 Tab（日K/周K/月K）替换范围选择（1月/3月/6月/1年 删除）、去掉成交量区日期轴，并把个股头部增强为 8 项指标网格（含今开/最高/最低/昨收/换手率/总市值）。

**Architecture:** 数据全量加载 + 前端聚合（周/月由日线聚合，零后端改动）；默认视图定位末尾 ~120 根（dataZoom startValue/endValue by index，slider 全程可见）；头部新增今开/最高/最低由后端 enriched 查询补 3 字段下发。

**Tech Stack:** React 18 + antd 5 + echarts-for-react 3（不加新依赖）；FastAPI + SQLAlchemy（头部 3 字段）。

**Spec:** 本文档设计+计划合一。用户三项决策（2026-09-03）：头部 8 项含总市值；默认视图末尾 ~120 根；子代理驱动执行。

## Global Constraints

- **notMerge 语义保留**：切频率/复权时 option 全量重放、重置缩放——P1 bug 修复核心，不得回退。
- **no-new-deps**：不引入任何新 npm/pip 依赖。
- e2e 定位器惯例：Segmented 用 `label.ant-segmented-item` + `ant-segmented-item-selected` 类（antd 5.24 radio input 零尺寸，已在 best-practices 沉淀）。
- 单位口径不变：volume 手（前端 ÷1e4 万手）、amount 千元（÷1e5 亿）、total_mv 万元（原样）。
- 后端 `StockEnrichedOut` 新字段全部带默认 `None`（Redis 旧缓存 300s TTL 内反序列化兼容）。
- 图表 e2e 旧用例（MA chips `.ant-tag`、1月/1年 radio）在 Task 1 内**必须同步重写**，任何时点全量 e2e 不许留红。
- 验证门：前端 `cd frontend && npm run build` + `npx playwright test`（docker 栈 localhost:3000 在跑）；后端 `cd backend && uv run pytest`（基线 19 个 httpx.ConnectError 环境失败为正常）。
- 每任务 git commit 到 main（仓库惯例）；`frontend/tsconfig.tsbuildinfo` 提交前 `git checkout --` 还原；工作区可能有用户并行改动，只 add 自己任务的文件。

---

## 设计决策速览（执行者必读）

1. **MA 数值行**（图内左上角）：显示所有 MA5/10/20/60，选中的用线色+区间末值（`MA5 10.23`），未选中灰色仅名字；点击切换显隐。白底彩字对比度达标（替代 CheckableTag——其 checked 实底与 inline 彩字撞色，对比度仅 ~1.2:1）。
2. **频率切换**：`freq: "day"|"week"|"month"` 纯前端状态（不进 queryKey，切换不重取数）；`aggregateDaily` 聚合（周=ISO 周一起算，月=YYYY-MM 前7位；open=组首 open、close=组末 close、high/low 极值、volume/amount 求和）。MA 在聚合后序列上重算（周K 的 MA5=5周线，主流行为）。
3. **默认视图**：`DEFAULT_TAIL_BARS=120`；dataZoom 用 `startValue=Math.max(0,N-120), endValue=N-1`（index 语义，随数据长度动态计算——区别于 P1 修复掉的"固定百分比裁剪"，slider 全程可见、数据全量在内存）。
4. **日期轴移除**：两 grid xAxis `axisLabel.show:false`，x data 用原始 ISO 日期串；slider `labelFormatter` 原样返回（拖动手柄时显示日期）；tooltip 已含全日期。
5. **取数**：`fetcher(3650, adjust)`（10 年窗口=库内全量）；`cropToRange`/`rangeCutoff`/`MA_WARMUP_CALENDAR_DAYS` 随之删除（warm-up 不再需要——数据全量）。

---

### Task 1: 图表区重构（频率 Tab + 图内 MA 行 + 去日期轴）

**Files:**
- Modify: `frontend/src/shared/ui/kline/klineMath.ts`
- Modify: `frontend/src/shared/ui/kline/klineOption.ts`
- Modify: `frontend/src/shared/ui/kline/KlineChart.tsx`
- Modify: `frontend/e2e/kline.spec.ts`

**Interfaces:**
- Consumes: 现有 `KlineChartProps`（title/queryKey/fetcher/showAdjust/defaultRange）、`KlineFetcher`、`MA_DEFS`、`movingAverage`、`buildKlineOption`、复权禁用态逻辑（refetchInterval + adjustAvailable）——全部保留语义。
- Produces:

```ts
// klineMath.ts 变更后导出
export type KlineFreq = "day" | "week" | "month";
export const DEFAULT_TAIL_BARS = 120;
export function aggregateDaily(points: KLinePoint[], freq: KlineFreq): KLinePoint[];
// 删除导出：MA_WARMUP_CALENDAR_DAYS、cropToRange、buildAxisLabels（不再被消费）
// 保留：MA_DEFS、MaKey、movingAverage、fmtVolume、fmtAmount

// klineOption.ts 变更后签名
export interface KlineOptionInput {
  points: KLinePoint[];        // 聚合后的全量序列
  maSeries: Partial<Record<MaKey, (number | null)[]>>;
  visibleMas: MaKey[];
}
export function buildKlineOption(input: KlineOptionInput): Record<string, unknown>;
// 内部行为变化：x data=原始日期串、两轴 axisLabel.show=false、
// dataZoom(inside+slider) startValue/endValue 按 DEFAULT_TAIL_BARS 计算、slider labelFormatter 原样返回、
// 删除 buildAxisLabels 依赖

// KlineChart.tsx 变更后
export interface KlineChartProps {   // defaultRange 删除，其余不变
  title: string;
  queryKey: string;
  fetcher: (days: number, adjust: AdjustMode) => Promise<{ points: KLinePoint[]; adjustAvailable: boolean }>;
  showAdjust?: boolean;
}
```

- [x] **Step 1: `klineMath.ts` 重构**——删除 `MA_WARMUP_CALENDAR_DAYS`、`cropToRange`、`buildAxisLabels` 及其实现；新增：

```ts
export type KlineFreq = "day" | "week" | "month";

/** 默认初始视图显示的末尾K线根数（TradingView 式：数据全量、视图局部、slider 漫游） */
export const DEFAULT_TAIL_BARS = 120;

function weekKey(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // 回到本周周一
  return d.toISOString().slice(0, 10);
}

/** 日线 → 周/月K聚合：open=组首、close=组末、high/low=极值、volume/amount=求和 */
export function aggregateDaily(points: KLinePoint[], freq: KlineFreq): KLinePoint[] {
  if (freq === "day" || points.length === 0) return points;
  const out: KLinePoint[] = [];
  let cur: KLinePoint | null = null;
  let curKey = "";
  for (const p of points) {
    const key = freq === "week" ? weekKey(p.date) : p.date.slice(0, 7);
    if (cur && key === curKey) {
      cur.high = Math.max(cur.high, p.high);
      cur.low = Math.min(cur.low, p.low);
      cur.close = p.close;
      cur.volume += p.volume;
      cur.amount = (cur.amount ?? 0) + (p.amount ?? 0);
    } else {
      if (cur) out.push(cur);
      cur = { ...p, amount: p.amount ?? 0 };
      curKey = key;
    }
  }
  if (cur) out.push(cur);
  return out;
}
```

- [x] **Step 2: `klineOption.ts` 布局改版**——`buildKlineOption` 内部修改（签名不变）：
  1. `const dates = points.map((p) => p.date);`（原始 ISO 串，删除 buildAxisLabels 调用与 import）
  2. 两 xAxis：`data: dates`，`axisLabel: { show: false }`（主图与成交量均不显示日期）
  3. dataZoom 改为：

```ts
const tailStart = Math.max(0, points.length - DEFAULT_TAIL_BARS);
// ...
dataZoom: [
  { type: "inside", xAxisIndex: [0, 1], startValue: tailStart, endValue: points.length - 1 },
  {
    type: "slider", xAxisIndex: [0, 1], height: 16, bottom: 6,
    startValue: tailStart, endValue: points.length - 1,
    labelFormatter: (v: unknown) => String(v),
  },
],
```

  4. grid 微调释放日期轴空间：grid1 `{ left: 60, right: 20, top: "68%", height: "16%" }`（原 66%/13%）；import 改从 `./klineMath` 引 `DEFAULT_TAIL_BARS`，删 `buildAxisLabels` import。
  5. tooltip formatter 逻辑不动（`p.date` 已是全日期）。

- [x] **Step 3: `KlineChart.tsx` 容器改版**：
  1. import 清理：删 `MA_WARMUP_CALENDAR_DAYS/cropToRange`，加 `aggregateDaily/DEFAULT_TAIL_BARS/KlineFreq`；删 `RANGES` 常量与 `rangeCutoff` 函数；删 `Tag` import（MA chips 移除）。
  2. state：`const [freq, setFreq] = useState<KlineFreq>("day");` 替换 range（adjust/visibleMas 保留）。
  3. 查询（freq 不进 key——聚合纯前端）：

```ts
const { data, isLoading } = useQuery({
  queryKey: ["kline", queryKey, adjust],
  queryFn: () => fetcher(3650, adjust), // 10年窗口 = 库内全量
  staleTime: STALE_TIME,
  refetchInterval: (q) => (q.state.data?.adjustAvailable === false ? 10_000 : false),
});
```

  4. 派生序列（全量聚合，无裁剪）：

```ts
const points = useMemo(() => aggregateDaily(data?.points ?? [], freq), [data, freq]);
const maSeries = useMemo(() => {
  const closes = points.map((p) => p.close);
  const out: Partial<Record<MaKey, (number | null)[]>> = {};
  for (const def of MA_DEFS) out[def.key] = movingAverage(closes, def.window);
  return out;
}, [points]);
```

  5. MA 数值行（图内左上角，替代 Card extra 的 CheckableTag 块）+ 图表容器相对定位：

```tsx
{!isLoading && points.length > 0 && (
  <div style={{ position: "absolute", top: 6, left: 66, right: 20, display: "flex", gap: 12, fontSize: 11, zIndex: 5 }}>
    {MA_DEFS.map((d) => {
      const on = visibleMas.includes(d.key);
      const v = maSeries[d.key]?.[points.length - 1] ?? null;
      return (
        <span
          key={d.key}
          onClick={() =>
            setVisibleMas((prev) => (on ? prev.filter((k) => k !== d.key) : [...prev, d.key]))
          }
          style={{ color: on ? d.color : "#9ca3af", cursor: "pointer", userSelect: "none" }}
        >
          {d.key} {on ? (v == null ? "--" : v.toFixed(2)) : ""}
        </span>
      );
    })}
  </div>
)}
```

  6. 图表区包一层 `<div style={{ position: "relative" }}>`（MA 行与 ReactECharts 同层叠加；Spin/Empty 分支不渲染 MA 行）。
  7. Card extra 精简为：频率 Segmented + 复权 Segmented（含现有禁用态三分支，逻辑不动）+ 重置按钮：

```tsx
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
```

  8. `KlineChartProps` 删除 `defaultRange` 字段；resetZoom 保留 `dispatchAction({ type: "dataZoom", start: 0, end: 100 })`。

- [x] **Step 4: 构建验证**

Run: `cd frontend && npm run build`
Expected: 通过（若 `KlineFreq` 从 barrel `export *` 透出与现有导出冲突则调整 barrel）

- [x] **Step 5: 重写 e2e 图表用例**（`frontend/e2e/kline.spec.ts` 全文件替换）：

```ts
import { test, expect as baseExpect } from "@playwright/test";

/** K线共享组件 E2E（P4）：频率Tab/图内MA数值行/去日期轴与范围选择/结构化tooltip/复权开关。依赖运行中的 docker 栈。 */
const expect = baseExpect.configure({ timeout: 15_000 });

const segItem = (card: ReturnType<typeof page2>, text: string) => card.locator(".ant-segmented-item").filter({ hasText: text });
function page2() { throw new Error("unused helper placeholder removed below"); }

test("个股K线：频率Tab切换、图内MA数值行可读可切换", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();

  // MA 数值行：默认 MA5/10/20 带数值（线色），MA60 灰色无名值
  await expect(card.getByText(/^MA5\s\d/)).toBeVisible();
  await expect(card.getByText(/^MA20\s\d/)).toBeVisible();
  await expect(card.getByText(/^MA60$/)).toBeVisible();

  // 点击 MA60 出现数值（切换显隐可逆）
  await card.getByText(/^MA60$/).click();
  await expect(card.getByText(/^MA60\s\d/)).toBeVisible();

  // 频率 Tab：日K 默认选中，切周K 控件状态跟随
  await expect(segItem(card, "日K")).toHaveClass(/ant-segmented-item-selected/);
  await segItem(card, "周K").click();
  await expect(segItem(card, "周K")).toHaveClass(/ant-segmented-item-selected/);
  await segItem(card, "月K").click();
  await expect(segItem(card, "月K")).toHaveClass(/ant-segmented-item-selected/);

  // 范围选择已移除
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "1月" })).toHaveCount(0);
});

test("个股K线：tooltip 结构化展示（无可见日期轴）", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  const canvas = card.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas not visible");
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.35);
  await page.waitForTimeout(600);
  await expect(card.getByText(/涨跌幅/)).toBeVisible();
  await expect(card.getByText(/成交量/)).toBeVisible();
  await expect(card.getByText(/成交额/)).toBeVisible();
});

test("指数K线：频率Tab可用，无复权控件", async ({ page }) => {
  await page.goto("/index/000001.SH");
  const card = page.locator(".ant-card").filter({ hasText: "指数历史行情" });
  await expect(card).toBeVisible();
  await expect(card.getByText(/^MA20\s\d/)).toBeVisible();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "周K" })).toBeVisible();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "前复权" })).toHaveCount(0);
});

test("复权开关：数据就绪后可切换前复权/不复权", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: /前复权/ })).toBeVisible({ timeout: 30_000 });
  await card.locator(".ant-segmented-item").filter({ hasText: "不复权" }).click({ timeout: 30_000 });
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "不复权" })).toHaveClass(/ant-segmented-item-selected/);
  await card.locator(".ant-segmented-item").filter({ hasText: "前复权" }).click();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "前复权" })).toHaveClass(/ant-segmented-item-selected/);
});
```

**注意**：上方 `page2`/`segItem` 骨架是示意（helper 形态），写入文件时整合为文件内局部函数（`const segItem = (card: Locator, text: string) => card.locator(...)`，从 `@playwright/test` import `Locator` 类型），四个用例共用；不要保留 `page2` 占位函数。

- [x] **Step 6: 重建前端 + 全量 e2e**

Run: `cd frontend && npm run build && cd .. && docker compose build frontend && docker compose up -d frontend && cd frontend && npx playwright test`
Expected: 全部通过（含 research/navigation 既有用例）。周K 聚合后 MA 行断言若因 warm 数据不足显示 `--` 失败，检查正则（`/^MA5\s\d/` 要求有数值——600519 日线 800+ 根全量，MA 必有值；失败多半是定位器命中多个 span，用 `.first()` 收窄）。

- [x] **Step 7: Commit**

```bash
git checkout -- frontend/tsconfig.tsbuildinfo 2>/dev/null
git add frontend/src/shared/ui/kline/ frontend/e2e/kline.spec.ts
git commit -m "feat(frontend): K线P4 — 频率Tab(日/周/月K)替换范围选择、MA数值行入图修对比度、去成交量日期轴、默认末尾120根"
```

---

### Task 2: 头部 8 项指标网格（后端补今开/最高/最低 + 前端渲染）

**Files:**
- Modify: `backend/app/schemas/stock.py:46-63`（StockEnrichedOut 加 3 字段）
- Modify: `backend/app/services/market_service.py`（`get_stocks_enriched_by_symbols` 的 LATERAL 查询加 3 列——先 grep 该函数定位）
- Modify: `frontend/src/shared/api/stocks.ts`（BackendStockEnriched 接口 + `mapBackendStockEnriched` 映射）
- Modify: `frontend/src/shared/types/index.ts`（StockRecord 加 5 字段）
- Modify: `frontend/src/features/stock-detail/components/StockHeader.tsx`
- Modify: `frontend/e2e/kline.spec.ts`（追加头部用例）

**Interfaces:**
- Consumes: Task 1 完成后的 e2e 基线（追加不破坏）。
- Produces:

```python
# StockEnrichedOut 追加（Quote fields 区，紧邻 prev_close）
open: float | None = None
high: float | None = None
low: float | None = None
```

```ts
// StockRecord 追加
open?: number; high?: number; low?: number; prevClose?: number; turnoverRate?: number;
// mapBackendStockEnriched 追加映射
open: item.open ?? undefined, high: item.high ?? undefined, low: item.low ?? undefined,
prevClose: item.prev_close ?? undefined, turnoverRate: item.turnover_rate ?? undefined,
```

- [x] **Step 1: 后端 schema + 查询**——`StockEnrichedOut` 按上方追加 3 字段；grep `get_stocks_enriched_by_symbols`（market_service.py），在其 LATERAL/查询构造处为最新行情行补选 `open/high/low` 三列并填入出参（模式照同函数内 `prev_close` 的现有取法：同一条 daily_quotes 最新行）。改完跑：

Run: `cd backend && uv run pytest`
Expected: 既有全绿（基线 19 环境失败不变）

- [x] **Step 2: 前端类型 + 映射**——按 Interfaces 追加（`BackendStockEnriched` 接口同步加 `open?/high?/low?`）。

- [x] **Step 3: StockHeader 网格改版**——`Descriptions size="small" column={4}` 两行 8 项替换现有 2 项（数值全部 `NumberText`，undefined 显示维持现状能力；换手率无适配单位则内联 `?? "--"` 后 `toFixed(2) + "%"`）：

```tsx
<Descriptions size="small" column={4} style={{ marginTop: 8 }}>
  <Descriptions.Item label="今开"><NumberText value={stock.open} /></Descriptions.Item>
  <Descriptions.Item label="最高"><NumberText value={stock.high} /></Descriptions.Item>
  <Descriptions.Item label="最低"><NumberText value={stock.low} /></Descriptions.Item>
  <Descriptions.Item label="昨收"><NumberText value={stock.prevClose} /></Descriptions.Item>
  <Descriptions.Item label="成交量"><NumberText value={stock.volume} unit="cap" /></Descriptions.Item>
  <Descriptions.Item label="成交额"><NumberText value={stock.turnover} unit="cap" /></Descriptions.Item>
  <Descriptions.Item label="换手率">
    {stock.turnoverRate == null ? "--" : `${stock.turnoverRate.toFixed(2)}%`}
  </Descriptions.Item>
  <Descriptions.Item label="总市值"><NumberText value={stock.marketCap} unit="cap" /></Descriptions.Item>
</Descriptions>
```

（若 NumberText 不接受 undefined，先 `grep -n "NumberText" frontend/src/shared/ui/index.ts` 读其实现按实际签名适配，保持 "--" 空态。）

- [x] **Step 4: e2e 追加头部用例**（kline.spec.ts 末尾）：

```ts
test("个股头部：8项指标网格含今开/昨收/换手率", async ({ page }) => {
  await page.goto("/stock/600519");
  await expect(page.locator(".ant-descriptions").first()).toBeVisible();
  for (const label of ["今开", "最高", "最低", "昨收", "成交量", "成交额", "换手率", "总市值"]) {
    await expect(page.locator(".ant-descriptions-item-label").filter({ hasText: label })).toBeVisible();
  }
});
```

- [x] **Step 5: 重建验证**——后端 `docker compose build api scheduler worker && docker compose up -d api scheduler worker`；前端 build + rebuild frontend；实机 curl 确认新字段下发（600519 enriched 含非空 open/high/low）；`cd frontend && npx playwright test` 全绿。

- [x] **Step 6: Commit**

```bash
git checkout -- frontend/tsconfig.tsbuildinfo 2>/dev/null
git add backend/app/schemas/stock.py backend/app/services/market_service.py frontend/src/shared/api/stocks.ts frontend/src/shared/types/index.ts frontend/src/features/stock-detail/components/StockHeader.tsx frontend/e2e/kline.spec.ts
git commit -m "feat: 个股头部8项指标网格 — 后端enriched补今开/最高/最低，前端渲染昨收/换手率/总市值"
```

---

### Task 3: 回归 + 文档收尾

**Files:**
- Modify: `docs/Changelog.md`
- Modify: `docs/references/best-practices.md`
- Modify: 本计划文档（勾选）

- [x] **Step 1: 全量回归**——`cd backend && uv run pytest`（基线对比零回归）+ `cd frontend && npm run build` + `npx playwright test`（全绿）。

- [x] **Step 2: 文档**——Changelog 追加 P4 条目（MA对比度根因/频率Tab语义/头部8项/默认120根，格式照既有）；best-practices 追加一条：`antd CheckableTag 选中态自带主题色实底，inline 彩色文字色会与之撞色（对比度~1.2:1）——彩色图例类控件用图内绝对定位文本行（线色文字 on 白底），不要用 CheckableTag 承载。`（先 grep 去重）。

- [x] **Step 3: 勾选计划 checkbox + Commit**

```bash
git add docs/Changelog.md docs/references/best-practices.md plans/2026-09-03-kline-p4-ui-iteration.md
git commit -m "docs: K线P4 UI迭代收尾 — Changelog/best-practices/计划勾选"
```

---

## Self-Review 记录

- **Spec 覆盖**：用户三个问题 → MA 对比度/配置（T1 图内数值行）、去范围选择+参考图频率Tab（T1）、成交量去日期轴（T1）、头部参考图2（T2 8项）。用户决策三项全部落实（8项/120根/子代理）。✓
- **类型一致性**：`KlineFreq`/`aggregateDaily`/`DEFAULT_TAIL_BARS` 在 T1 内定义消费闭环；`KlineChartProps` 删 defaultRange 后两页面调用（未传该参）无需改动；StockRecord 5 新字段与 mapper/StockHeader 用法对齐。✓
- **占位符**：T1 Step 5 的 `page2` 骨架已明确标注"写入时整合为局部函数，不保留占位"；其余步骤代码完整。✓
- **已知风险**：周K聚合的 ISO 周一计算用本地时区 `new Date(date+"T00:00:00")`（A股日期串无时区歧义，getDay 本地周一界定安全）；slider labelFormatter 返回 ISO 全串较长（拖动瞬时显示，可接受）。
