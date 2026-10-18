import type { JournalVerdict } from "@/shared/api/journal";

/**
 * 归因五枚举的**显示层唯一真相**（标签 + 一句话解释）。
 *
 * 为什么单独一份而不是散在弹窗/列表里：同一个 `verdict` 在两处上屏——复盘弹窗的
 * 单选项、已复盘列表的标签——两处各写一份中文，必然漂移成"弹窗里叫没按计划、
 * 列表里叫破纪律"。故：key 只有这五个（与后端 `check (verdict in (...))` 逐字一致），
 * 文案只有这一份。
 *
 * 文案纪律（spec §S9.5）：只说"错在哪一类"，不做评价、不出现判断词。用户读到的
 * 是**自己当时的那一笔**归到哪一类，不是产品对他的评价。
 */
export interface VerdictMeta {
  label: string;
  hint: string;
}

export const VERDICT_META: Record<JournalVerdict, VerdictMeta> = {
  thesis_wrong: {
    label: "理由错了",
    hint: "结果和当初写下的理由都不成立。",
  },
  timing_wrong: {
    label: "时机错了",
    hint: "理由本身还成立，但入场或观察的时点错了。",
  },
  discipline_break: {
    label: "没按计划执行",
    hint: "写的失效条件到了，但当时没有按自己写的做。",
  },
  right_for_wrong_reason: {
    label: "结果对、理由不成立",
    hint: "这次结果是对的，但当初的理由并不成立——赚到不等于看懂。",
  },
  luck: {
    label: "运气成分",
    hint: "结果主要由运气决定，方法本身既没错也没对。",
  },
};

/** 固定次序：与 spec §S9.2 的枚举次序一致（`discipline_break` 在中间，便于阅读）。 */
export const VERDICT_ORDER: readonly JournalVerdict[] = [
  "thesis_wrong",
  "timing_wrong",
  "discipline_break",
  "right_for_wrong_reason",
  "luck",
];

/** 取标签；未知值（后端将来加枚举而前端没更新）原样返回，不吞成空白。 */
export function verdictLabel(verdict: string): string {
  return VERDICT_META[verdict as JournalVerdict]?.label ?? verdict;
}
