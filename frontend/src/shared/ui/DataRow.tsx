import type { ReactNode } from "react";
import { Children } from "react";
import { Link } from "react-router-dom";
import { DeltaText } from "./DeltaText";
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
}: DataRowProps) {
  const hasValue = value !== undefined && value !== "";
  const displayValue = hasValue ? value : valuePlaceholder;
  const body = (
    <>
      <span className="datarow__id">
        {logo}
        <span className="datarow__names">
          <span className="datarow__title">{title}</span>
          {ticker ? <span className="datarow__ticker">{ticker}</span> : null}
        </span>
      </span>
      <span className="datarow__val num">
        {displayValue !== undefined && (
          <span className="datarow__price">
            {displayValue}
            {hasValue && unit ? <span className="datarow__unit"> {unit}</span> : null}
          </span>
        )}
        <DeltaText value={delta} />
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
