import type { JournalPositionSize } from "@/shared/api/journal";

/**
 * 仓位粗档四枚举的**显示层唯一真相**（spec §S9.7.1；表单四选一与统计 `by_size` 分栏共用）。
 *
 * 与 `kinds.ts` / `verdicts.ts` 同一套纪律：同一个 `position_size` 在表单、列表行、
 * 统计卡三处上屏，三处各写一份中文必然漂移成"表单写轻仓、列表写小仓"这种最难查的错，
 * 故标签只此一份。
 *
 * `weight` 是 spec §S9.7.1 的 `size_weight`（`none:0 / light:0.5 / normal:1 / heavy:2`），
 * **只用于写清"只观察不计收益"这句口径说明**——加权超额是后端算的，前端不自己乘。
 *
 * 缺字段（Phase B 后端未上线）时：表单默认 `none`，列表**不显示**仓位标签、统计**不显示**
 * `by_size` 分栏——把"没记录"显示成"只观察"是编数据。
 */
export interface PositionSizeMeta {
  label: string;
  /** spec §S9.7.1 的 `size_weight`。 */
  weight: number;
}

export const POSITION_SIZE_META: Record<JournalPositionSize, PositionSizeMeta> = {
  none: { label: "只观察", weight: 0 },
  light: { label: "轻仓", weight: 0.5 },
  normal: { label: "标准", weight: 1 },
  heavy: { label: "重仓", weight: 2 },
};

/** 固定次序：与后端 `by_size` 的枚举次序一致（`none` 在最前）。 */
export const POSITION_SIZE_ORDER: readonly JournalPositionSize[] = [
  "none",
  "light",
  "normal",
  "heavy",
];

/** 取标签；未知值原样返回（后端将来加枚举而前端没更新时，不把数据吞成空白）。 */
export function positionSizeLabel(size: string): string {
  return POSITION_SIZE_META[size as JournalPositionSize]?.label ?? size;
}

/**
 * 这条记录该显示哪种仓位标签：缺省/`null` → `null`（不显示）。
 *
 * 与 `positionSizeLabel` 的分工：这里回答"有没有"，那里回答"怎么写"——
 * 混在一起时最容易把 `undefined` 渲染成"只观察"。
 */
export function positionSizeOf(size: string | null | undefined): string | null {
  return size === null || size === undefined || size === "" ? null : size;
}
