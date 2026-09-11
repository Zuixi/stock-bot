import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { DeltaText } from "./DeltaText";
import "./DataRow.css";

export interface DataRowProps {
  logo?: ReactNode;
  title: string;
  ticker?: string;
  value?: string;
  unit?: string;
  /** 应用内路由（如 `/stock/600519`）；用 router Link 导航，避免整页刷新 */
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
      <span className="datarow__val">
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
