import type { KLinePoint } from "@/shared/types";

/**
 * 「股性事实行」的**纯函数层**（P2-3 / D2：先做事实行，不画雷达）。
 *
 * 分成 `lib/`（纯函数）与 `hooks/useCharacterStats.ts`（查询编排）两个文件，理由与
 * `hooks/rangeStats.ts` 逐字相同：`shared/api/client.ts` 在模块求值期读 `import.meta.env`，
 * 纯函数单测（`e2e/unit-character-stats.spec.ts`，Playwright 直跑 TS）一旦顺手 import
 * 带网络层的模块就会在 Node 里炸掉 —— 于是"能单测"退化成"只能 e2e"。
 *
 * ## 量纲（写死在这里，消费方不要再猜）
 *
 * `totalReturn` / `annualVol` / `maxDrawdown` 三个是**比率（小数）**，不是百分数：
 * `0.184` 的意思是 `+18.4%`，格式化（× 100）只发生在 `CharacterFacts` 里。
 * 这样做是因为 brief 里的公式是 `last/first - 1` 与 `stdev × √252`，两者都天然出小数；
 * 在函数内乘 100、在组件里再拼 `%`，等于把同一个换算写两遍，必然有一天只改了一处。
 * `trendR2` 与 `volumeRatio` 是**无量纲比值**（R² 落在 0–1，量能倍数约 0–10）。
 *
 * ## D2 的边界（为什么不是雷达）
 *
 * 雷达要求每根轴"同量纲可比"（全市场分位），而当前没有分位数据（见计划更正 1）。
 * 这里只输出**能直接由窗口内日线算出的、每个数都有明确定义的事实**，不做跨股票排序，
 * 也不给"好/坏"的合成分。任何看起来像评分的东西都不属于本函数。
 */

/** 默认窗口 = 60 个交易日（约一个季度；brief 逐字要求 `windowBars = 60`）。 */
export const CHARACTER_WINDOW_BARS = 60;

/** 年化交易日数（A 股每年约 242–245 个交易日，业界口径统一取 252）。 */
export const TRADING_DAYS_PER_YEAR = 252;

/** 短均量窗口（`volumeRatio` 的分子：最近 5 日均量）。 */
const VOLUME_SHORT_BARS = 5;

/** 均线多头判据里最长的一根（MA20）——窗口短于它就没法判，见下面 `null` 分支。 */
const MA_LONGEST_BARS = 20;

export interface CharacterStats {
  /** 窗口累计收益（小数，`0.184` = +18.4%）：`最后收盘 / 首个收盘 - 1` */
  totalReturn: number;
  /** 年化波动率（小数，`0.321` = 32.1%）：日收益样本标准差 × √252 */
  annualVol: number;
  /** 最大回撤（小数，**恒 ≤ 0**，`-0.092` = -9.2%）：峰到谷的最差跌幅 */
  maxDrawdown: number;
  /** 最小二乘趋势线的 R²（0–1）：收盘价对时间序号回归的决定系数 */
  trendR2: number;
  /** 均线多头：MA5 > MA10 > MA20（末尾 20 根收盘价的简单均值） */
  maBullish: boolean;
  /**
   * 量能倍数：`mean(volume, 最近 5 根) / mean(volume, 整个窗口)`。
   *
   * `null` = **成交量不可用**（整窗均量为 0，或窗口里有非有限/负的 volume）⇒ 这一项
   * 无从谈起，渲染层整格不出现、人话里也不提量能（缺失 ≠ 0，**不渲染 `0.0×`**）。
   * 详见 `computeVolumeRatio` 的注释。
   */
  volumeRatio: number | null;
  /** 实际参与计算的 bar 数（= 传入的 `windowBars`） */
  windowBars: number;
  /** 窗口最后一根 bar 的交易日（`YYYY-MM-DD`），供 UI 写「截至」 */
  asOf: string;
}

/** 算术均值（调用方保证 `values` 非空，否则返回 `0`，避免 NaN 往上游跑）。 */
function mean(values: number[]): number {
  if (values.length === 0) return 0;
  let sum = 0;
  for (const value of values) sum += value;
  return sum / values.length;
}

/**
 * 样本标准差（分母 `n - 1`）。
 *
 * 用样本而不是总体：窗口是"这一段历史"，但我们想要的是对它背后那个收益分布的
 * 波动率估计；`n - 1` 是估计量口径。样本量很小时差异明显，60 根下差异 < 2%，
 * 选哪个都不会改变"波动大/小"的结论 —— 但必须在代码里钉死一个，否则两处实现会漂移。
 */
function sampleStdev(values: number[]): number {
  if (values.length < 2) return 0;
  const avg = mean(values);
  let sq = 0;
  for (const value of values) {
    const diff = value - avg;
    sq += diff * diff;
  }
  return Math.sqrt(sq / (values.length - 1));
}

/** 末尾 `n` 根的简单移动均值（`n > values.length` 时按全部算，调用方已保证长度）。 */
function movingAverage(values: number[], n: number): number {
  return mean(values.slice(Math.max(0, values.length - n)));
}

/**
 * 量能倍数，**不可用时返回 `null`**（review round 1 的修正：这里曾经返回 `0`）。
 *
 * 为什么 `0` 是错的（实测后果）：窗口零成交时 `mean(window) === 0`，`0/0` 被记成 `0`，
 * 于是 UI 印出「量能 0.0×」，而人话那一段还会写「最近 5 日量能萎缩」—— 一边是"没成交"，
 * 一边是"缩量"，两个都不成立：**分母为 0 时"量能倍数"这个量根本不存在**。
 * 只把数字改成 `--` 也不够（那仍然在断言"有一个值，只是没取到"），正确做法是整项缺席：
 * 渲染层不渲染这一格、人话不提量能（`CharacterFacts` 里逐条对应）。
 *
 * 三条守卫：
 * 1. 任一 volume 非有限或为负 ⇒ `null`（脏数据不该被当成 0 掺进均值 —— 那会把均量压低，
 *    于是量能比被**放大**，比"少一个数"更糟）；
 * 2. 整窗均量 `<= 0`（零成交）⇒ `null`（分母为 0）；
 * 3. 其余情况：`mean(末尾 5 根) / mean(整窗)`。**分子为 0 是合法的**：整窗有量、最近 5 日
 *    突然没成交，`0.0×` 是一句真话（"最近 5 日没有成交"），故只有分母退化才返回 `null`。
 */
function computeVolumeRatio(volumes: number[]): number | null {
  for (const volume of volumes) {
    if (!Number.isFinite(volume) || volume < 0) return null;
  }
  const windowMeanVolume = mean(volumes);
  if (windowMeanVolume <= 0) return null;
  return mean(volumes.slice(Math.max(0, volumes.length - VOLUME_SHORT_BARS))) / windowMeanVolume;
}

/**
 * 从日线序列算「股性事实」。
 *
 * @param bars 日线序列，**按时间升序**（与 `fetchKlineBySymbol` 返回的 `points` 同序）。
 * @param windowBars 窗口长度，只用**最后** `windowBars` 根；更早的 bar 不参与。
 * @returns 算不出来时返回 `null`（**绝不是一份填 0 的对象**——缺失 ≠ 0）。
 *
 * ## 返回 `null` 的三种情况（每一处都是"宁可不显示，也不编一个数"）
 *
 * 1. **样本不足**：`bars.length < windowBars`。新股上市不满 60 根时整行不渲染；
 *    绝不"用现有的 23 根凑一个近 60 日收益"——那会把两个不同的窗口写在同一个标签下。
 *    同理 `windowBars < 20`（连 MA20 都凑不出）也返回 `null`，而不是把 `maBullish`
 *    谎报成 `false`（"不是多头"和"判不了"是两件事）。
 * 2. **价格不可用**：窗口里任何一根 `close` 非有限数或 `<= 0`。除权/停牌/脏数据会让
 *    `closes[i-1]` 或首根成为 0，收益率与回撤的分母就为 0 —— 直接放弃整行，不返回 Infinity。
 * 3. **数据不是"一根一根"的**：窗口长度为 0（理论上被 1 拦掉，防御性保留）。
 *
 * ## 每一处除法的守卫（brief 要求逐个说明"返回什么"）
 *
 * - `totalReturn = last / first - 1`：`first <= 0` → 已被情况 2 拦掉（返回 `null`）。
 * - `annualVol`：`returns.length < 2` 时 `sampleStdev` 返回 `0`（不是 NaN）——
 *   只有 1 个日收益时标准差无定义，写 0 表示"估不出波动"，但没有除零。
 *   注意 `0` 在这里是**合法且常见**的值：横盘序列的日收益全 0 ⇒ 年化波动 0。
 * - `maxDrawdown = (close - peak) / peak`：`peak` 是至今最大收盘价，恒 `>= closes[0] > 0`
 *   （情况 2 已保证），故分母不会为 0。窗口内没有任何回撤时结果是 `0`（不是 `-0`）。
 * - `trendR2 = 1 - SSres / SStot`：`SStot === 0`（完全横盘，`closes` 全相等）时分母为 0，
 *   **返回 `0`**。不返回 1：横盘时"拟合完美"在数学上成立（SSres 也是 0），但用户读到
 *   R²=1 会理解成"趋势非常明确"，恰好是事实的反面。R²=0 才是"这条水平线解释不了什么"
 *   的正确读法。回归本身的分母 `Sxx = Σ(x - x̄)²` 在 `n >= 2` 时恒 `> 0`（x 是 0..n-1）。
 * - `volumeRatio = mean(last5) / mean(window)`：**成交量不可用时返回 `null`**（不是 0）——
 *   整窗均量为 0（零成交）或窗口里有非有限/负的 volume 时，这个量不存在；`null` 由渲染层
 *   表达为"这一格整项不出现 + 人话不提量能"（见 `computeVolumeRatio`）。注意**其余五项照常**
 *   返回：收益率/波动/回撤/趋势都还能算，不因为一个量能指标缺席把整行藏掉。
 *   分子为 0 而分母 > 0 时返回 `0`（"最近 5 日没有成交"是一句真话）。
 * - `movingAverage`：窗口长度恒 `>= 20`（情况 1 已拦），不会出现空数组除零。
 */
export function characterStats(
  bars: KLinePoint[],
  windowBars: number = CHARACTER_WINDOW_BARS
): CharacterStats | null {
  // 窗口长度本身非法（非整数 / 连 MA20 都放不下）→ 判不了，不编。
  if (!Number.isInteger(windowBars) || windowBars < MA_LONGEST_BARS) return null;

  // 情况 1：样本不足。注意判据是"能给满一个窗口"，不是"非空"。
  if (bars.length < windowBars) return null;

  const window = bars.slice(-windowBars);
  if (window.length === 0) return null;

  const closes: number[] = [];
  const volumes: number[] = [];
  for (const bar of window) {
    // 情况 2：价格必须可用于做分母（收益率/回撤/均线都要求正数收盘价）。
    if (!Number.isFinite(bar.close) || bar.close <= 0) return null;
    closes.push(bar.close);
    // 成交量**原样**收集：合法性由 `computeVolumeRatio` 统一判定。此前这里把脏值改成 0，
    // 既污染了均量（分母被压低 → 量能比被放大），又让"不可用"伪装成一个合法的 0。
    volumes.push(bar.volume);
  }

  const first = closes[0];
  const last = closes[closes.length - 1];

  // 1) 累计收益
  const totalReturn = last / first - 1;

  // 2) 年化波动：日收益 → 样本标准差 → × √252
  const returns: number[] = [];
  for (let i = 1; i < closes.length; i += 1) {
    returns.push(closes[i] / closes[i - 1] - 1);
  }
  const annualVol = sampleStdev(returns) * Math.sqrt(TRADING_DAYS_PER_YEAR);

  // 3) 最大回撤：一路记录峰值，取最差的一次（结果恒 <= 0）
  let peak = closes[0];
  let maxDrawdown = 0;
  for (const close of closes) {
    if (close > peak) peak = close;
    const drawdown = (close - peak) / peak;
    if (drawdown < maxDrawdown) maxDrawdown = drawdown;
  }

  // 4) 趋势 R²：收盘价对时间序号（0..n-1）做最小二乘直线回归
  const n = closes.length;
  const meanX = (n - 1) / 2;
  const meanY = mean(closes);
  let sxx = 0;
  let sxy = 0;
  for (let i = 0; i < n; i += 1) {
    const dx = i - meanX;
    sxx += dx * dx;
    sxy += dx * (closes[i] - meanY);
  }
  // n >= 20 ⇒ sxx > 0，这里仍然显式守卫：将来若放开窗口下限，除零会立刻变成 NaN 上屏。
  const slope = sxx > 0 ? sxy / sxx : 0;
  const intercept = meanY - slope * meanX;
  let ssRes = 0;
  let ssTot = 0;
  for (let i = 0; i < n; i += 1) {
    const residual = closes[i] - (slope * i + intercept);
    ssRes += residual * residual;
    const deviation = closes[i] - meanY;
    ssTot += deviation * deviation;
  }
  // ssTot === 0 ⇒ 完全横盘，见头注释「trendR2 的守卫」：返回 0，不返回 1。
  const trendR2 = ssTot > 0 ? Math.min(1, Math.max(0, 1 - ssRes / ssTot)) : 0;

  // 5) 均线多头：MA5 > MA10 > MA20（末尾 20 根收盘价，严格大于）
  const ma5 = movingAverage(closes, 5);
  const ma10 = movingAverage(closes, 10);
  const ma20 = movingAverage(closes, MA_LONGEST_BARS);
  const maBullish = ma5 > ma10 && ma10 > ma20;

  // 6) 量能倍数：最近 5 日均量 / 整窗均量。**不可用时是 `null`，不是 0**
  //    （零成交时 0/0 没有意义，渲染层据此整项不出现，见 `computeVolumeRatio`）。
  const volumeRatio = computeVolumeRatio(volumes);

  return {
    totalReturn,
    annualVol,
    maxDrawdown,
    trendR2,
    maBullish,
    volumeRatio,
    windowBars: window.length,
    asOf: window[window.length - 1].date,
  };
}
