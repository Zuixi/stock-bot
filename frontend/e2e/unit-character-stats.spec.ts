/**
 * `characterStats` 的**纯函数单测**（P2-3 brief：「`characterStats(bars, 60)` 纯函数 + 单测」）。
 *
 * 为什么放在 `e2e/` 且用 Playwright 跑：仓库没有 vitest/jest，Playwright 自带 TypeScript
 * 转译 + tsconfig `paths` 解析，`@/` 别名可直接用 —— 零新依赖（与
 * `unit-stock-range-stats.spec.ts` / `unit-kline-option.spec.ts` 同款）。
 *
 * 为什么 import `lib/characterStats` 而不是 hook 文件：hook 文件 import 了
 * `shared/api/client`，后者在模块求值期读 `import.meta.env` —— 在 Node 里会直接抛。
 * 纯函数拆出来后才"可以被单测"，这条边界是刻意留的。
 *
 * ## 量纲（与实现同一条契约，别把小数当百分数）
 *
 * `totalReturn` / `annualVol` / `maxDrawdown` 是**小数**（`0.184` = 18.4%），
 * `trendR2` / `volumeRatio` 是无量纲比值。下面的期望值全部按小数写。
 */
import { test, expect } from "@playwright/test";
import {
  CHARACTER_WINDOW_BARS,
  characterStats,
} from "@/features/stock-detail/lib/characterStats";
import type { KLinePoint } from "@/shared/types";

/** 造一根日线（open/high/low 与本函数无关，只影响别处的区间统计，这里随手给）。 */
function bar(close: number, volume = 100, date = "2026-01-01"): KLinePoint {
  return { date, open: close, high: close, low: close, close, volume, amount: close * volume };
}

/** 造 n 根同价日线。 */
function flat(n: number, close: number, volume = 100): KLinePoint[] {
  return Array.from({ length: n }, () => bar(close, volume));
}

/**
 * 60 根「100 / 102 隔日交替」的确定性序列（本文件的主力 fixture）。
 *
 * 为什么用交替而不是"随便一串数"：交替序列每一处都能手算，且不是单调序列，
 * 能同时压到回撤、波动、R² 三个口径。
 *
 * 手算（n = 60，偶数索引 100、奇数索引 102，末根是奇数索引 102）：
 * - `totalReturn = 102 / 100 - 1 = 0.02`（+2.0%）
 * - 日收益只取两个值：上涨日 `+1/50 = 0.02`（30 个）、下跌日 `-2/102 = -1/51`（29 个）
 * - 样本方差（两组值的闭式）：`n1·n2/(n1+n2) · (a-b)² / (n-1)`
 *   = `30·29/59 · (1/50 + 1/51)² / 58` = `3.9884268762e-4`
 *   ⇒ 样本标准差 `0.0199710462` ⇒ `annualVol = ×√252 = 0.3170305305`
 * - `maxDrawdown`：峰值 102 之后跌到 100 ⇒ `-2/102 = -0.0196078431`
 * - 整窗成交量恒 100 ⇒ `volumeRatio = 1`
 */
function alternating60(): KLinePoint[] {
  return Array.from({ length: 60 }, (_, i) =>
    bar(i % 2 === 0 ? 100 : 102, 100, `2026-03-${String((i % 28) + 1).padStart(2, "0")}`)
  );
}

test("已知 60 根 fixture：totalReturn / annualVol / maxDrawdown 手算值", () => {
  const bars = alternating60();
  const stats = characterStats(bars);
  expect(stats, "满窗 60 根必须算得出来（返回 null 说明窗口判据写错了）").not.toBeNull();

  expect(stats!.totalReturn).toBeCloseTo(0.02, 12);
  expect(stats!.annualVol).toBeCloseTo(0.3170305305, 9);
  expect(stats!.maxDrawdown).toBeCloseTo(-2 / 102, 12);
  expect(stats!.volumeRatio).toBeCloseTo(1, 12);

  // 窗口元信息：窗口长度与"截至"必须来自**最后一根** bar，不是第一根
  expect(stats!.windowBars).toBe(CHARACTER_WINDOW_BARS);
  expect(stats!.asOf).toBe(bars[bars.length - 1].date);

  // 交替序列不是"多头排列"：MA5 = (3×102+2×100)/5 = 101.2、MA10 = MA20 = 101
  // ⇒ 101.2 > 101 成立，但 101 > 101 不成立（判据是**严格**大于）
  expect(stats!.maBullish).toBe(false);
});

test("只看最后 60 根：更早的 bar 不参与任何一项（含「截至」日期）", () => {
  // 前置 100 根极值（收 10、量 100000）：若窗口写错成"全量"，totalReturn 会变成 9.2、
  // 年化波动会被 10→100 的跳变放大、量能比会被 100000 均量压成 0.001。
  const ancient = Array.from({ length: 100 }, () => bar(10, 100000, "2020-01-02"));
  const bars = [...ancient, ...alternating60()];
  const stats = characterStats(bars);

  expect(stats!.windowBars).toBe(60);
  expect(stats!.totalReturn).toBeCloseTo(0.02, 12);
  expect(stats!.annualVol).toBeCloseTo(0.3170305305, 9);
  expect(stats!.volumeRatio).toBeCloseTo(1, 12);
  expect(stats!.asOf).toBe(bars[bars.length - 1].date);
});

test("横盘序列（零方差 + 零成交量）：除零守卫给出 0 / null，不是 NaN/Infinity", () => {
  // 收 100 全等、量 0 全等 —— 年化波动的方差分母、量能比的分母同时为 0
  const stats = characterStats(flat(60, 100, 0));
  expect(stats).not.toBeNull();

  expect(stats!.totalReturn).toBe(0);
  expect(stats!.annualVol).toBe(0);
  expect(stats!.maxDrawdown).toBe(0);
  // 横盘 SStot = 0：按约定记 0（不是 1 —— R²=1 会被读成"趋势完美"，恰好是事实的反面）
  expect(stats!.trendR2).toBe(0);
  expect(stats!.maBullish).toBe(false);
  // 整窗均量为 0 ⇒ 分母为 0 ⇒ **null，不是 0**（review round 1 的修正）：
  // 零成交时"量能倍数"这个量不存在，0 会让 UI 印出「量能 0.0×」并把缺失说成事实。
  expect(stats!.volumeRatio).toBeNull();

  // 每一项都必须是有限数：任何 NaN 都会让 UI 把 "NaN%" 印到屏幕上
  for (const [key, value] of Object.entries(stats!)) {
    if (typeof value === "number") expect(Number.isFinite(value), `${key} 不是有限数`).toBe(true);
  }
});

test("成交量不可用（NaN / 负值 / 整窗零成交）→ volumeRatio 为 null，其余五项照常", () => {
  // 1) 单根 NaN 成交量：**不许**把它当 0 掺进均量（那会压低分母 → 量能比被放大）
  const nanVolume = [...flat(59, 100, 100), { ...bar(100, 0), volume: Number.NaN }];
  const nanStats = characterStats(nanVolume);
  expect(nanStats).not.toBeNull();
  expect(nanStats!.volumeRatio).toBeNull();
  // 其余指标不受影响：收益/波动/回撤/趋势/R² 仍然算得出来
  expect(nanStats!.totalReturn).toBe(0);
  expect(Number.isFinite(nanStats!.annualVol)).toBe(true);

  // 2) 负成交量（脏数据，没有物理意义）
  const negative = [...flat(59, 100, 100), bar(100, -5)];
  expect(characterStats(negative)!.volumeRatio).toBeNull();

  // 3) 整窗零成交（0/0）
  expect(characterStats(flat(60, 100, 0))!.volumeRatio).toBeNull();

  // 4) **反向**：整窗有量、最近 5 日归零 → 这是**合法**的 0.0×（"最近 5 日没有成交"是一句真话），
  //    不是 null。这条防"为了消掉 0/0 把所有 0 都当缺失"的过度修正。
  const driedUp = Array.from({ length: 60 }, (_, i) => bar(100, i >= 55 ? 0 : 100));
  expect(characterStats(driedUp)!.volumeRatio).toBe(0);
});

test("样本不足 60 根 → null（缺失 ≠ 0，整行不渲染）", () => {
  expect(characterStats([])).toBeNull();
  expect(characterStats(flat(59, 100))).toBeNull();
  // 恰好 60 根就必须能算（下界是闭区间，别把 60 写成 61）
  expect(characterStats(flat(60, 100))).not.toBeNull();
});

test("窗口参数本身放不下 MA20（< 20）→ null，而不是把 maBullish 谎报成 false", () => {
  expect(characterStats(flat(30, 100), 19)).toBeNull();
  expect(characterStats(flat(30, 100), 0)).toBeNull();
  expect(characterStats(flat(30, 100), 20)).not.toBeNull();
});

test("单调上升序列 → maBullish === true 且 trendR2 ≈ 1", () => {
  // 等差数列 100, 101, … 159：收盘价与时间序号是**精确**线性关系 ⇒ R² = 1
  const bars = Array.from({ length: 60 }, (_, i) => bar(100 + i));
  const stats = characterStats(bars);

  expect(stats!.maBullish).toBe(true);
  expect(stats!.trendR2).toBeCloseTo(1, 10);
  expect(stats!.totalReturn).toBeCloseTo(159 / 100 - 1, 12);
  // 一路上涨没有回撤 ⇒ 0（不是 -0，也不是 undefined）
  expect(stats!.maxDrawdown).toBe(0);
});

test("volumeRatio：最近 5 日放量 → 5 日 spike 被正确放大", () => {
  // 前 55 根量 100、末 5 根量 300
  // 整窗均量 = (55×100 + 5×300)/60 = 116.6667，末 5 日均量 = 300
  // ⇒ ratio = 300 / 116.6667 = 2.5714286
  const bars = Array.from({ length: 60 }, (_, i) => bar(100, i >= 55 ? 300 : 100));
  expect(characterStats(bars)!.volumeRatio).toBeCloseTo(300 / (7000 / 60), 10);

  // 反向验证：缩量到 20 ⇒ 均量 = (55×100 + 5×20)/60 = 93.3333，ratio = 20/93.3333 = 0.2143
  const dry = Array.from({ length: 60 }, (_, i) => bar(100, i >= 55 ? 20 : 100));
  expect(characterStats(dry)!.volumeRatio).toBeCloseTo(20 / (5600 / 60), 10);

  // 边界：spike 只有 4 根时，"最近 5 日"里仍有一根是 100 ⇒ 分子是 (100+4×300)/5 = 260，
  // 而不是 300。这一条钉住"分子确实取末尾 5 根、且真的做了均值"（少算一根就会得 300）。
  const fourDaySpike = Array.from({ length: 60 }, (_, i) => bar(100, i >= 56 ? 300 : 100));
  const meanWindow = (56 * 100 + 4 * 300) / 60;
  expect(characterStats(fourDaySpike)!.volumeRatio).toBeCloseTo((100 + 4 * 300) / 5 / meanWindow, 10);
});

test("maxDrawdown 恒 ≤ 0；最差跌幅从**运行峰值**起算（不是全局最高点）", () => {
  // 1) 单边上涨：没有任何回撤 ⇒ 恰好 0
  expect(characterStats(Array.from({ length: 60 }, (_, i) => bar(100 + i)))!.maxDrawdown).toBe(0);

  // 2) 前 30 根 200、后 30 根 150：从 200 跌到 150 ⇒ -25%
  const drop = [...flat(30, 200), ...flat(30, 150)];
  expect(characterStats(drop)!.maxDrawdown).toBeCloseTo(-0.25, 12);

  // 3) 100 → 80 → 120：最差回撤是 -20%（100→80），**不是** 0（120 处虽创新高，
  //    但 120 之后没有再跌，不能把"后来涨回去了"当成没有回撤）
  const trough = [...flat(40, 100), ...flat(10, 80), ...flat(10, 120)];
  expect(characterStats(trough)!.maxDrawdown).toBeCloseTo(-0.2, 12);
  expect(characterStats(trough)!.maxDrawdown).toBeLessThanOrEqual(0);

  // 4) 兜底：一批形状各异的序列，结果都必须 ≤ 0（回撤是"最多亏多少"，符号写反是最常见的错）
  const shapes: KLinePoint[][] = [
    alternating60(),
    flat(60, 100),
    Array.from({ length: 60 }, (_, i) => bar(100 + i)),
    Array.from({ length: 60 }, (_, i) => bar(200 - i * 2)),
    Array.from({ length: 60 }, (_, i) => bar(100 + (i % 7) * 5)),
  ];
  for (const bars of shapes) {
    expect(characterStats(bars)!.maxDrawdown).toBeLessThanOrEqual(0);
  }
});

test("价格不可用（0 / NaN 收盘价）→ null，不返回 Infinity", () => {
  // 首根为 0：totalReturn 与回撤的分母都是 0
  const zeroFirst = [bar(0), ...flat(59, 100)];
  expect(characterStats(zeroFirst)).toBeNull();

  // 中段为 0：日收益分母为 0
  const zeroMid = [...flat(30, 100), bar(0), ...flat(29, 100)];
  expect(characterStats(zeroMid)).toBeNull();

  // NaN：任何一项算出来都会是 NaN，直接放弃整行
  const nanMid = [...flat(30, 100), bar(Number.NaN), ...flat(29, 100)];
  expect(characterStats(nanMid)).toBeNull();
});
