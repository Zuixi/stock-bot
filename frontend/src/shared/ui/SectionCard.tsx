import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import "./SectionCard.css";

export interface SectionCardProps {
  id?: string;
  title: string;
  /** 应用内路由（如 `/market`）；用 router Link 导航，避免整页刷新 */
  moreHref?: string;
  moreText?: string;
  children: ReactNode;
}

/** 三段式卡片壳：标题栏（可选「查看全部」）+ 内容槽。 */
export function SectionCard({
  id,
  title,
  moreHref,
  moreText = "查看全部",
  children,
}: SectionCardProps) {
  return (
    <section className="section-card" id={id} data-testid={`section-${id ?? title}`}>
      <header className="section-card__head">
        <h3 className="section-card__title">{title}</h3>
        {moreHref ? (
          <Link className="section-card__more" to={moreHref}>
            {moreText} ›
          </Link>
        ) : null}
      </header>
      <div className="section-card__body">{children}</div>
    </section>
  );
}
