import type { JournalKind } from "@/shared/api/journal";

/**
 * 归档标签五枚举的**显示层唯一真相**（表单下拉、统计分栏共用）。
 *
 * 与 `verdicts.ts` 同一套纪律：同一个 `kind` 在表单与统计卡两处上屏，两处各写一份中文
 * 必然漂移，故标签只此一份。
 *
 * `counts_outcome = false` 的只有 `pass`（spec §S9.7.6）：它**计入判断、不计入收益**，
 * 所以列表收益列显示 `—（不计收益）` 而不是某个数——尤其不能显示 0（0 会被读成
 * "这次收益正好是 0"）。
 */
export interface KindMeta {
  label: string;
  /** 是否参与收益统计（`pass` 在 spec §S9.7.6 里被排除在收益之外）。 */
  countsOutcome: boolean;
}

export const KIND_META: Record<JournalKind, KindMeta> = {
  watch: { label: "观察", countsOutcome: true },
  buy: { label: "买", countsOutcome: true },
  sell: { label: "卖", countsOutcome: true },
  pass: { label: "放弃", countsOutcome: false },
  hold: { label: "持有", countsOutcome: true },
};

/** 固定次序：与后端 `by_kind` 的枚举次序一致（`pass` 在中间，便于对照）。 */
export const KIND_ORDER: readonly JournalKind[] = ["watch", "buy", "sell", "pass", "hold"];

/** 取标签；未知值（后端将来加枚举而前端没更新）原样返回，不吞成空白。 */
export function kindLabel(kind: string): string {
  return KIND_META[kind as JournalKind]?.label ?? kind;
}

/** 这条标签是否参与收益统计（未知标签按参与处理，避免把数据藏起来）。 */
export function kindCountsOutcome(kind: string): boolean {
  return KIND_META[kind as JournalKind]?.countsOutcome ?? true;
}
