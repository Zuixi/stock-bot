import type { CSSProperties, ReactNode } from "react";
import { Children } from "react";
import { Link } from "react-router-dom";
import { DeltaAbsText, DeltaText } from "./DeltaText";
import { TRIPLET_GRID_TEMPLATE, TRIPLET_ORDER } from "./instrumentTriplet";
import "./DataRow.css";

export interface DataRowProps {
  logo?: ReactNode;
  title: string;
  ticker?: string;
  value?: string;
  unit?: string;
  /** 应用内路由（如 `/company/600519`）；用 router Link 导航，避免整页刷新 */
  href?: string;
  delta?: number | null;
  /**
   * 数值缺失时的占位符（如 `--`）。省略则不渲染数值槽——纯涨跌幅行（无 value）
   * 保持原样；需要「缺失显示 `--`」的数值消费方显式传入。
   */
  valuePlaceholder?: string;
  /** 涨跌额（绝对变动，单位由清单口径决定，默认元）。缺失时渲染 `--`，**绝不回落 0**。 */
  change?: number | null;
  /**
   * 涨跌额缺失时的占位符（如 `--`）。省略则**不渲染涨跌额槽**——没有该成员的载荷
   * （如板块行的行业均涨幅）保持改造前的 DOM；需要"槽位留空、右边的涨跌幅列不许跳"
   * 的标的清单显式传入（见 `instrumentTriplet.ts`）。
   */
  changePlaceholder?: string;
  /**
   * 行内数值槽的排布：
   * - `stack`（默认）：最新价在上、涨跌幅在下（板块/行业清单的既有形态，改造前逐节点一致）；
   * - `triplet`：**标的清单**的固定三列（最新价 → 涨跌额 → 涨跌幅），列宽取自
   *   `instrumentTriplet.ts`。列宽固定 + `grid-column` 显式占位，才能跨行、跨表竖读。
   */
  variant?: "stack" | "triplet";
}

/** 标准数据行：左标识/名称，右数值/涨跌幅；有 `href` 时整行可点。 */
export function DataRow({
  logo,
  title,
  ticker,
  value,
  unit,
  href,
  delta,
  valuePlaceholder,
  change,
  changePlaceholder,
  variant = "stack",
}: DataRowProps) {
  const hasValue = value !== undefined && value !== "";
  const displayValue = hasValue ? value : valuePlaceholder;
  const hasChange = change !== null && change !== undefined && !Number.isNaN(change);
  const showChange = hasChange || changePlaceholder !== undefined;
  const body = (
    <>
      <span className="datarow__id">
        {logo}
        <span className="datarow__names">
          <span className="datarow__title">{title}</span>
          {ticker ? <span className="datarow__ticker">{ticker}</span> : null}
        </span>
      </span>
      <span
        className={`datarow__val num${variant === "triplet" ? " datarow__val--triplet" : ""}`}
        // 三元组列宽是单一真相（`instrumentTriplet.ts`）：内联注入而不是让 CSS 再抄一套。
        style={
          variant === "triplet"
            ? ({ gridTemplateColumns: TRIPLET_GRID_TEMPLATE } as CSSProperties)
            : undefined
        }
      >
        {displayValue !== undefined && (
          <span className={`datarow__price datarow__slot--${TRIPLET_ORDER[0]}`}>
            {displayValue}
            {hasValue && unit ? <span className="datarow__unit"> {unit}</span> : null}
          </span>
        )}
        {showChange && (
          <span className={`datarow__chg datarow__slot--${TRIPLET_ORDER[1]}`}>
            {hasChange ? (
              <DeltaAbsText value={change} />
            ) : (
              <span className="delta-abs delta--flat num">{changePlaceholder}</span>
            )}
          </span>
        )}
        <span className={`datarow__pct datarow__slot--${TRIPLET_ORDER[2]}`}>
          <DeltaText value={delta} />
        </span>
      </span>
    </>
  );
  return href ? (
    <Link className="datarow" to={href}>
      {body}
    </Link>
  ) : (
    <div className="datarow">{body}</div>
  );
}

export interface DataRowListProps {
  /** 同 `DataRowProps.columns`；`1` = 直接透传 children（**不新增任何 DOM 层**，原调用方零影响） */
  columns?: 1 | 2 | 3;
  children: ReactNode;
}

/**
 * `DataRow` 的多列栅格容器（Phase 4 §5.3）。
 *
 * ⚠️ **`columns === 1` 时不渲染包裹元素**，直接返回 `children`：这样既有单列调用方
 * （`SectorFlow` 的两处）拿到的 DOM 与改造前**逐节点相同**——「多列」是纯增量能力，
 * 不是把所有人都塞进一层新 div（那会改变既有的 flex/边框继承，且需要重测所有消费方）。
 *
 * 列数按**容器宽度**退档（`@container`，不是视口）：同一份 `columns={3}` 在 1120px 的
 * landing 容器里是 3 列，在窄屏卡片里自动降成 1 列，调用方不必再关心断点
 * （`useElementWidth` 那类 JS 测量在这里是多余的——纯 CSS 容器查询就够）。
 */
export function DataRowList({ columns = 1, children }: DataRowListProps) {
  if (columns === 1) return <>{children}</>;
  return (
    <div
      className={`datarow-grid datarow-grid--${columns}`}
      data-columns={columns}
      data-rows={Children.count(children)}
    >
      {children}
    </div>
  );
}
