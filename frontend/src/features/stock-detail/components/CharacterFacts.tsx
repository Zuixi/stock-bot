import { Tooltip } from "antd";
import { useTheme } from "@/app/theme-context";
import { formatCnDate } from "@/shared/ui";
import { CHARACTER_WINDOW_BARS, type CharacterStats } from "../lib/characterStats";
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
 * - **加载中**不是"没有事实"：这一行照常占位，六个 chip 显示 `--` 骨架（见组件内三态注释）；
 * - **取数失败**也不是"没有事实"：行内直接给「加载失败 · 重试」（理由与"同一查询在报价条
 *   行 4 已有一枚重试"的关系见组件内注释：两枚按钮打同一个缓存键，不会多取一次）。
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

  // 5) 量能。`volumeRatio === null` = 成交量不可用 ⇒ **整句省略**：既不写"萎缩"
  //    也不写"平稳"（review round 1：过去 0/0 被记成 0，于是这一句会写"量能萎缩"，
  //    而真相是"没有成交量这回事"，等于用一句人话把缺失说成了一个事实）。
  if (stats.volumeRatio !== null) {
    if (stats.volumeRatio >= 1.5) parts.push("最近 5 日量能明显放大");
    else if (stats.volumeRatio >= 1.1) parts.push("最近 5 日量能温和放大");
    else if (stats.volumeRatio <= 0.7) parts.push("最近 5 日量能萎缩");
    else parts.push("最近 5 日量能与窗口平均相当");
  }

  return `${parts.join("，")}。`;
}

/**
 * 骨架用的六个 chip 标签：与正式行**一一对应**（第 5 格是纯文字事实「均线…」，无标签）。
 *
 * 骨架必须复用同一套标签与同一个容器类，否则"加载中 → 有数据"会换一套排版，
 * 高度和位置都会跳。顺序 / 文案与下面 `chips` 数组逐字一致。
 */
const SKELETON_CHIP_LABELS: Array<string | null> = [
  windowLabel(CHARACTER_WINDOW_BARS),
  "年化波动",
  "最大回撤",
  "趋势 R²",
  null,
  "量能",
];

/**
 * 本块的**实测自然高度**（1440×900 侧栏，P2-1 用 Playwright `boundingBox().height` 量得）：
 * `padding-top 10` + 1px 上边框 + chip 行 40（六枚在 1/3 页宽里折两行）+ 6 gap + 一句人话 36。
 *
 * **三态都钉住**（`min-height`）：加载骨架、失败行、成功行。加载中 chip 值是短 `--`、
 * 成功行在更宽的侧栏里六枚 chip 可能排成一行 —— 谁矮了都会让下面的公司概况与事件卡
 * 上下跳（骨架 → 成功是向下长，成功 → 骨架是向上抽）。钉住后三态高度一致。
 */
const CHARACTER_ROW_MIN_HEIGHT = 93;

/**
 * 股性事实行的**三态**（P2-1）：
 * - **加载中** → 六个 chip 的 `--` 骨架（`min-height` 钉在实测自然高度 93px）。
 *   不写「暂无…」：还没到 ≠ 没有；
 * - **成功但样本不足** → `stats === null` ⇒ 整行不渲染（设计稿与 P2-3 brief 都要求
 *   "新股不足 60 根时股性行不出现"，**不显示 0**）；
 * - **成功** → 六枚 chip + 一句人话，同样 `min-height: 93px`：chip 少一枚（量能不可用）
 *   或宽到六枚排成一行时，自然高度会低于骨架，不钉住就是一次**向上**跳；
 * - **失败** → 行内「加载失败 · 重试」。**这里为什么可以再放一枚重试**：同一份 qfq 日线
 *   还被报价条行 4（`PositionStrip`）消费，那里也有一枚「重试」；两枚按钮打的是**同一个**
 *   React Query 缓存键，`refetch()` 由查询层去重，不会多取一次。反过来若失败时本行直接
 *   消失，读者就无法判断"这一块是还没加载、还是取数失败了"——那正是本任务要消灭的含混。
 */
export function CharacterFacts({ stats: state }: { stats: CharacterStatsState }) {
  const { colors } = useTheme();
  const stats = state.stats;

  if (state.isError) {
    return (
      <div
        className="character-facts"
        data-testid="character-facts-error"
        // 失败也占本块的自然高度：错误卡比正式行矮，不钉住就会把下面两块往上抽
        style={{ minHeight: CHARACTER_ROW_MIN_HEIGHT }}
      >
        <div className="character-facts__chips">
          <span className="character-facts__chip" style={{ color: colors.textSecondary }}>
            加载失败
          </span>
          <button
            type="button"
            data-testid="character-facts-retry"
            onClick={state.retry}
            style={{
              padding: 0,
              border: 0,
              background: "none",
              fontSize: 12,
              lineHeight: "18px",
              color: colors.accent,
              cursor: "pointer",
            }}
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  // null = 样本不足（或价格不可用）：一行事实都不成立，整块不渲染；
  // 但"还在加载"不是"没有事实"，那是有骨架的（见下）。
  if (!stats) {
    if (!state.isLoading) return null;
    return (
      <div
        className="character-facts"
        data-testid="character-facts-loading"
        aria-busy="true"
        style={{ minHeight: CHARACTER_ROW_MIN_HEIGHT }}
      >
        <div className="character-facts__chips">
          {SKELETON_CHIP_LABELS.map((label, index) => (
            <span className="character-facts__chip" key={label ?? `chip-${index}`}>
              {label && <span className="character-facts__label">{label}</span>}
              <span className="character-facts__value num" style={{ color: colors.flat }}>
                --
              </span>
            </span>
          ))}
        </div>
        {/* 一句人话的位置也要占住（它和 chip 行一样是数据落地后才有的内容） */}
        <p className="character-facts__reading">--</p>
      </div>
    );
  }

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
  ];

  // 量能这一格**按可用性出现**（review round 1）：`volumeRatio === null` = 成交量不可用，
  // 这一格整项不渲染 —— 不渲染 `0.0×`、也不渲染 `--`（`--` 仍是在断言"有个值，只是没取到"）。
  // 少一格后 chip 行会自己收窄（flex-wrap），读者看到的是"没有这一项"，而不是"它是 0"。
  if (stats.volumeRatio !== null) {
    chips.push({
      key: "volumeRatio",
      label: "量能",
      value: formatRatio(stats.volumeRatio),
      tip: "最近 5 日均量 ÷ 整窗口均量：1.0× = 与窗口平均持平，>1 放量，<1 缩量",
    });
  }

  return (
    // 成功态也钉在骨架的实测高度：chip 少一枚或宽到排成一行时自然高度会低于 93px，
    // 不钉住就会在数据落地后把下面两块**往上抽**（与加载态的向下长是同一类位移）。
    <div
      className="character-facts"
      data-testid="character-facts"
      style={{ minHeight: CHARACTER_ROW_MIN_HEIGHT }}
    >
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
