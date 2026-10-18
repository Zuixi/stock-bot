import { Tooltip } from "antd";
import { useTheme } from "@/app/theme-context";
import { formatCnDate } from "@/shared/ui";
import type { CharacterStats } from "../lib/characterStats";
import type { CharacterStatsState } from "../hooks/useCharacterStats";
import "./CharacterFacts.css";

/**
 * 「股性事实行」（P2-3 / 计划 D2）：**一行事实 + 一句人话**，不是雷达、不是评分。
 *
 * 为什么不是雷达（D2，取舍而非不可行）：雷达的前提是每根轴"同量纲可比"（全市场分位），
 * 而分位数据不存在；画出来的"面积"会被读成一个不存在的合成分。所以这里只给六件
 * 有明确定义、能直接从窗口内日线算出来的事实。
 *
 * 为什么整行可以消失（与全站"失败 ≠ 空"的纪律不冲突）：
 * - `characterStats` 返回 `null` = 样本不足/价格不可用 ⇒ 没有可陈述的事实，整行不渲染
 *   （设计稿与 brief 都要求"新股不足 60 根时股性行不出现"，**不显示 0**）；
 * - 取数失败时本行同样不渲染，但**失败不会被藏起来**：同一份 qfq 日线还被报价条行 4
 *   消费，那里会显示「加载失败 · 重试」（`PositionStrip`，设计稿 §3）。同一个查询在
 *   同一屏上只需要一枚重试入口，这里再放一枚就是两个按钮抢同一个 refetch。
 *
 * 颜色纪律：只有**收益**跟随涨跌色（`--up` / `--down` / `--flat`，A 股口径红涨绿跌）；
 * 波动/回撤/R²/量能一律中性文本色 —— 它们不是涨跌，上色会被读成方向。
 */

interface ChipDef {
  key: string;
  /** 标签（12px `textSecondary`）；留空 = 这一格是**纯文字事实**（如「均线多头」） */
  label: string;
  /** 值（12px `textPrimary`，`.num` 等宽） */
  value: string;
  /** tooltip = 定义（brief 要求"tooltips carry the definitions"） */
  tip: string;
  /** 收益那一格要跟随涨跌色，其余一律中性 */
  colored?: boolean;
}

/** 百分数（`0.184` → `+18.4%`）：比率 ×100 只发生在这一个格式化器里。 */
function formatPct(ratio: number, digits = 1, signed = false): string {
  const pct = ratio * 100;
  const sign = signed && pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(digits)}%`;
}

/** 量能倍数（`1.4` → `1.4×`）。 */
function formatRatio(ratio: number): string {
  return `${ratio.toFixed(1)}×`;
}

/** 窗口名（`60` → `近60日`；brief 的示例文案逐字为「近60日」，不留空格）。 */
function windowLabel(bars: number): string {
  return `近${bars}日`;
}

/**
 * 一句人话（不评价、不预测、不带方向词）。
 *
 * 措辞纪律（与全站合规口径一致）：只陈述**已经发生**的窗口事实，不出现
 * 买入/卖出/推荐/看好/机会/布局一类判断词，也不写"接下来"。每个分句都直接对应
 * 上面某一个数字，读者能回查。
 */
function plainReading(stats: CharacterStats): string {
  const parts: string[] = [];

  // 1) 累计涨跌（阈值 ±1% 内算横盘：噪声不该被读成方向）
  if (stats.totalReturn > 0.01) parts.push(`窗口内整体上行 ${formatPct(stats.totalReturn, 1)}`);
  else if (stats.totalReturn < -0.01) parts.push(`窗口内整体下行 ${formatPct(stats.totalReturn, 1)}`);
  else parts.push("窗口内基本横盘");

  // 2) 波动（年化口径的三档；阈值只为分档，不构成评价）
  if (stats.annualVol >= 0.45) parts.push("波动偏大");
  else if (stats.annualVol >= 0.25) parts.push("波动中等");
  else parts.push("波动偏小");

  // 3) 回撤深度
  if (stats.maxDrawdown <= -0.25) parts.push("期间回撤较深");
  else if (stats.maxDrawdown <= -0.1) parts.push("期间回撤中等");
  else parts.push("期间回撤较浅");

  // 4) 趋势强度（R² 与均线只看形态，不给方向建议）
  if (stats.trendR2 >= 0.5) parts.push("走势接近一条直线");
  else if (stats.trendR2 < 0.2) parts.push("走势比较反复");
  else parts.push("走势有一定方向性");
  parts.push(stats.maBullish ? "均线呈多头排列" : "均线未成多头排列");

  // 5) 量能
  if (stats.volumeRatio >= 1.5) parts.push("最近 5 日量能明显放大");
  else if (stats.volumeRatio >= 1.1) parts.push("最近 5 日量能温和放大");
  else if (stats.volumeRatio <= 0.7) parts.push("最近 5 日量能萎缩");
  else parts.push("最近 5 日量能与窗口平均相当");

  return `${parts.join("，")}。`;
}

export function CharacterFacts({ stats: state }: { stats: CharacterStatsState }) {
  const { colors } = useTheme();
  const stats = state.stats;
  // null = 样本不足（或价格不可用）：一行事实都不成立，整块不渲染。
  if (!stats) return null;

  const trendColor =
    Math.abs(stats.totalReturn) < 1e-9
      ? colors.flat
      : stats.totalReturn > 0
        ? colors.up
        : colors.down;

  const chips: ChipDef[] = [
    {
      key: "totalReturn",
      label: windowLabel(stats.windowBars),
      value: formatPct(stats.totalReturn, 1, true),
      tip: "窗口内最后一个收盘价相对第一个收盘价的涨跌（前复权日线，与上方 K 线同源）",
      colored: true,
    },
    {
      key: "annualVol",
      label: "年化波动",
      value: formatPct(stats.annualVol, 1),
      tip: "日收益的样本标准差 × √252（年化按 252 个交易日）",
    },
    {
      key: "maxDrawdown",
      label: "最大回撤",
      value: formatPct(stats.maxDrawdown, 1),
      tip: "窗口内从任一高点到之后最低点的最大跌幅，恒 ≤ 0（0 表示期间没有回撤）",
    },
    {
      key: "trendR2",
      label: "趋势 R²",
      value: stats.trendR2.toFixed(2),
      tip: "收盘价对时间序号做最小二乘直线回归的决定系数：1 = 完美直线，0 = 看不出趋势（完全横盘记 0）",
    },
    {
      key: "maBullish",
      // 均线是**纯文字事实**（没有数字），故不拆「标签 值」两段：拆开渲染成
      // 「均线 多头」会与示例文案「均线多头」不一致，也让这一格看着像缺了数值。
      label: "",
      value: stats.maBullish ? "均线多头" : "均线未成多头",
      tip: "MA5 > MA10 > MA20（窗口末尾 20 根收盘价的简单移动平均）",
    },
    {
      key: "volumeRatio",
      label: "量能",
      value: formatRatio(stats.volumeRatio),
      tip: "最近 5 日均量 ÷ 整窗口均量：1.0× = 与窗口平均持平，>1 放量，<1 缩量",
    },
  ];

  return (
    <div className="character-facts" data-testid="character-facts">
      {/* ONE chip row（brief 第 7 条）+ 每个 chip 一枚定义 tooltip */}
      <div className="character-facts__chips">
        {chips.map((chip) => (
          <Tooltip key={chip.key} title={chip.tip}>
            <span className="character-facts__chip" data-fact={chip.key}>
              {chip.label && <span className="character-facts__label">{chip.label}</span>}
              <span
                className="character-facts__value num"
                style={chip.colored ? { color: trendColor } : undefined}
              >
                {chip.value}
              </span>
            </span>
          </Tooltip>
        ))}
      </div>
      <p className="character-facts__reading">
        {plainReading(stats)}
        <span className="character-facts__asof">
          （窗口截至 {formatCnDate(stats.asOf)}）
        </span>
      </p>
    </div>
  );
}
