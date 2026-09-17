import { formatCnDate } from "./date";
import "./SectionCard.css";

/**
 * 「数据截至 X · 口径徽标」行 —— 全站按日聚合卡片的单一渲染点。
 *
 * 后端 Task 2/5 起，按日聚合端点返回 `as_of (+ as_of_quality / stale_days / source_status)`；
 * 本组件把这四类元数据翻成同一句人话，卡片（`SectionCard` 或 antd `Card`）只需把
 * 信封里的字段透传进来，避免每张卡各写一套措辞。
 *
 * 诚实原则：
 * - `asOf` 为空（库内无行情）→ **整行不渲染**：没有判据日就谈不上「数据截至」，
 *   更不得回落到「今天」（缺失 ≠ 今天）。
 * - `quality` 未知（非 complete/partial/fallback）→ 不加徽标，绝不默认「收盘」。
 * - `staleDays <= 0`（当天或表内有未来日）→ 不加陈旧徽标。
 */
export interface FreshnessNoteProps {
  /** 判据日 / 快照日（ISO `YYYY-MM-DD`）；空则不渲染整行。 */
  asOf?: string | null;
  /** 日级完整性口径（后端 `as_of_quality`）。 */
  quality?: string | null;
  /** 快照陈旧度（自然日，后端 `stale_days`）。 */
  staleDays?: number | null;
  /** 上游数据源状态（后端 `source_status`）。 */
  sourceStatus?: string | null;
}

/** 口径徽标文案；未知口径返回 `null`（不猜、不默认「收盘」）。 */
export function qualityBadgeText(quality?: string | null): string | null {
  switch (quality) {
    case "complete":
      return "收盘";
    case "partial":
      return "未完整";
    case "fallback":
      return "回落至";
    default:
      return null;
  }
}

/** 组装完整徽标文案列表（`fallback` 需要判据日才能补出「回落至 X」）。 */
export function freshnessBadges({ asOf, quality, staleDays, sourceStatus }: FreshnessNoteProps): string[] {
  const badges: string[] = [];
  const qualityText = qualityBadgeText(quality);
  if (qualityText === "回落至") {
    badges.push(asOf ? `回落至 ${formatCnDate(asOf)}` : "回落");
  } else if (qualityText) {
    badges.push(qualityText);
  }
  if (staleDays != null && staleDays > 0) badges.push(`${staleDays} 天前`);
  if (sourceStatus === "discontinued") badges.push("数据源已停更");
  return badges;
}

/** 「数据截至 …」行（复用既有 `.section-card__asof` 版式）。无 `asOf` 时渲染 `null`。 */
export function FreshnessNote({ asOf, quality, staleDays, sourceStatus }: FreshnessNoteProps) {
  if (!asOf) return null;
  const badges = freshnessBadges({ asOf, quality, staleDays, sourceStatus });
  return (
    <div className="section-card__asof">
      数据截至 {formatCnDate(asOf)}
      {badges.map((text) => (
        <span className="section-card__quality" key={text}>
          {text}
        </span>
      ))}
    </div>
  );
}
