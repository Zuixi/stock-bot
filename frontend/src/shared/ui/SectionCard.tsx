import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { FreshnessNote } from "./FreshnessNote";
import "./SectionCard.css";

export interface SectionCardProps {
  id?: string;
  title: string;
  /** 应用内路由（如 `/market`）；用 router Link 导航，避免整页刷新 */
  moreHref?: string;
  moreText?: string;
  /** 「数据截至 …」行（复用既有 .section-card__asof 样式，不新增 class） */
  asof?: string | null;
  /** 日级完整性口径（后端 `as_of_quality`）：`complete`→收盘 / `partial`→未完整 / `fallback`→按最近完整日 */
  quality?: string | null;
  /** 快照陈旧度（后端 `stale_days`）：> 0 时显示「N 天前」 */
  staleDays?: number | null;
  /** 上游数据源状态：`discontinued` → 「数据源已停更」 */
  sourceStatus?: string | null;
  /**
   * 标题右侧的**原样**标注（如盘中口径的 `as_of_label`）。
   * 不经过格式化：调用方给什么就渲染什么——回落文案（`盘中不可用，已回落收盘`）
   * 必须如实上屏，不能在 UI 层被美化成一个更「好看」的措辞。
   */
  note?: string | null;
  children: ReactNode;
}

/** 三段式卡片壳：标题栏（可选「查看全部」）+ 内容槽。 */
export function SectionCard({
  id,
  title,
  moreHref,
  moreText = "查看全部",
  asof,
  quality,
  staleDays,
  sourceStatus,
  note,
  children,
}: SectionCardProps) {
  return (
    <section className="section-card" id={id} data-testid={`section-${id ?? title}`}>
      <header className="section-card__head">
        <h3 className="section-card__title">
          {title}
          {note ? <span className="section-card__asof-label">{note}</span> : null}
        </h3>
        {moreHref ? (
          <Link className="section-card__more" to={moreHref}>
            {moreText} ›
          </Link>
        ) : null}
      </header>
      <FreshnessNote asOf={asof} quality={quality} staleDays={staleDays} sourceStatus={sourceStatus} />
      <div className="section-card__body">{children}</div>
    </section>
  );
}
