import type { ReactNode } from "react";
import { DeltaText } from "./DeltaText";
import "./DataRow.css";

export interface DataRowProps {
  logo?: ReactNode;
  title: string;
  ticker?: string;
  value?: string;
  unit?: string;
  href?: string;
  delta?: number | null;
}

/** 标准数据行：左标识/名称，右数值/涨跌幅；有 `href` 时整行可点。 */
export function DataRow({ logo, title, ticker, value, unit, href, delta }: DataRowProps) {
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
        {value !== undefined && (
          <span className="datarow__price">
            {value}
            {unit ? <span className="datarow__unit"> {unit}</span> : null}
          </span>
        )}
        <DeltaText value={delta} />
      </span>
    </>
  );
  return href ? (
    <a className="datarow" href={href}>
      {body}
    </a>
  ) : (
    <div className="datarow">{body}</div>
  );
}
