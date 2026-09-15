import { useState, type ReactNode } from "react";
import { Empty, Typography } from "antd";
import { Link } from "react-router-dom";
import type { LadderStock } from "@/shared/api/limitUp";
import { fmtYi } from "./format";
import "./LimitUpLadder.css";

interface Props {
  echelons: Array<{ streak: number; label: string; stocks: LadderStock[] }>;
  /** 端点降级（如 partial_day）时渲染短占位文案，不展示不完整数据。 */
  degraded?: boolean;
}

/** 档内默认展示只数（首板常见 40+ 家，折叠避免淹没连板层）。 */
const COLLAPSE_LIMIT = 12;

/** 板高热度档：streak 1-4，≥4 归 h4（涨色 alpha 随板高加深，见 CSS）。 */
function heatClass(streak: number): string {
  return `ladder-tier--h${Math.min(Math.max(streak, 1), 4)}`;
}

/** 档内按封板时间升序（越早封越强），缺失排最后（本地路径恒 null）。 */
function sealComparator(a: LadderStock, b: LadderStock): number {
  if (!a.sealTime && !b.sealTime) return 0;
  if (!a.sealTime) return 1;
  if (!b.sealTime) return -1;
  return a.sealTime.localeCompare(b.sealTime);
}

function StockItem({ s }: { s: LadderStock }) {
  const badges: ReactNode[] = [];
  // `{daysSpan}天{boardsInWindow}板` 仅在 `boardsInWindow !== streak` 时显示（避免冗余）
  if (s.boardsInWindow !== s.streak) {
    badges.push(
      <span key="nb" className="ladder-badge">
        {s.daysSpan}天{s.boardsInWindow}板
      </span>,
    );
  }
  // 停牌披露：文案保持「缺少 N 个交易日」（e2e 契约断言「缺少」）
  if (s.missingDays > 0) {
    badges.push(
      <span key="miss" className="ladder-badge ladder-badge--warn">
        缺少 {s.missingDays} 个交易日
      </span>,
    );
  }
  // 炸板次数只在 >0 时渲染为警示徽标（风险披露；0/null 都不占位，封板质量由封单额表达）
  if (s.breakCount != null && s.breakCount > 0) {
    badges.push(
      <span key="brk" className="ladder-badge ladder-badge--warn">
        炸板 {s.breakCount} 次
      </span>,
    );
  }
  if (s.sealFund != null) {
    badges.push(
      <span key="fund" className="ladder-badge">
        封单 {fmtYi(s.sealFund)}
      </span>,
    );
  }
  return (
    <li>
      <Link className="ladder-stock" to={`/stock/${s.symbol}`}>
        <span className="ladder-stock__row">
          <span className="ladder-stock__name">{s.name}</span>
          <span className="ladder-stock__seal">封板 {s.sealTime ?? "--"}</span>
        </span>
        {badges.length > 0 ? <span className="ladder-stock__badges">{badges}</span> : null}
      </Link>
    </li>
  );
}

function Tier({
  label,
  streak,
  stocks,
  maxCount,
  expanded,
  onToggle,
}: {
  label: string;
  streak: number;
  stocks: LadderStock[];
  maxCount: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const sorted = [...stocks].sort(sealComparator);
  const collapsed = !expanded && sorted.length > COLLAPSE_LIMIT;
  const shown = collapsed ? sorted.slice(0, COLLAPSE_LIMIT) : sorted;
  return (
    <article className={`ladder-tier ${heatClass(streak)}`}>
      <header className="ladder-tier__head">
        <span className="ladder-tier__name">{label}</span>
        <span className="ladder-tier__count">{sorted.length} 家</span>
      </header>
      {/* 档内家数占比条：各档并排即梯队金字塔 */}
      <div className="ladder-tier__bar" aria-hidden>
        <span style={{ width: `${maxCount ? (sorted.length / maxCount) * 100 : 0}%` }} />
      </div>
      <ul className="ladder-tier__list">
        {shown.map((s) => (
          <StockItem key={s.symbol} s={s} />
        ))}
      </ul>
      {sorted.length > COLLAPSE_LIMIT ? (
        <button type="button" className="ladder-tier__toggle" onClick={onToggle}>
          {collapsed ? `展开全部 ${sorted.length} 家` : "收起"}
        </button>
      ) : null}
    </article>
  );
}

/**
 * 连板梯队（分档列重设计）：每档一张卡（板高热度色阶 + 家数占比条），
 * 档内按封板时间排序，行可点跳个股页；首板档默认折叠。
 * 汇总 pill（空间板/连板/首板家数）是对已展示 echelons 的呈现级聚合，不新造口径。
 */
export function LimitUpLadder({ echelons, degraded = false }: Props) {
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(new Set());

  if (degraded) {
    return (
      <div className="sentiment-ladder">
        <Typography.Text
          type="secondary"
          style={{ display: "block", padding: "24px 0", textAlign: "center" }}
        >
          数据不完整，暂不展示梯队
        </Typography.Text>
      </div>
    );
  }

  if (echelons.length === 0) {
    return (
      <div className="sentiment-ladder">
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当日无连板梯队" />
      </div>
    );
  }

  const tiers = [...echelons].sort((a, b) => b.streak - a.streak);
  const maxCount = Math.max(...tiers.map((t) => t.stocks.length), 1);
  const topStreak = tiers[0]?.streak ?? 0;
  const lianbanCount = tiers
    .filter((t) => t.streak >= 2)
    .reduce((sum, t) => sum + t.stocks.length, 0);
  const shoubanCount = tiers
    .filter((t) => t.streak === 1)
    .reduce((sum, t) => sum + t.stocks.length, 0);

  const toggle = (streak: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(streak)) {
        next.delete(streak);
      } else {
        next.add(streak);
      }
      return next;
    });

  return (
    <div className="sentiment-ladder">
      <div className="ladder-summary">
        <span className="ladder-pill ladder-pill--accent">空间板 {topStreak} 板</span>
        <span className="ladder-pill">连板 {lianbanCount} 家</span>
        <span className="ladder-pill">首板 {shoubanCount} 家</span>
      </div>
      <div className="ladder">
        {tiers.map((e) => (
          <Tier
            key={e.streak}
            label={e.label}
            streak={e.streak}
            stocks={e.stocks}
            maxCount={maxCount}
            expanded={expanded.has(e.streak)}
            onToggle={() => toggle(e.streak)}
          />
        ))}
      </div>
    </div>
  );
}
