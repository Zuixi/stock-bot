import { useEffect, useState } from "react";
import { Alert, Input, Modal, Radio, Space, Typography, message } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  reviewJournalEntry,
  type JournalEntry,
  type JournalEvaluation,
  type JournalVerdict,
} from "@/shared/api/journal";
import { formatAsOfDate } from "@/shared/ui/date";
import { VERDICT_META, VERDICT_ORDER } from "../verdicts";
import {
  GROSS_FALLBACK_LABEL,
  formatSignedPct,
  netBasisLabel,
  netExcessOf,
  netOutcomeOf,
} from "../metrics";

const { Text } = Typography;

/**
 * 复盘弹窗：**自动结果只读 + 归因五选一必选**（spec §S9.4；S9.7.2/§S9.7.3 的净口径与基准）。
 *
 * 三段固定次序：
 * 1. **系统算的**（只读）：结果 / 基准 / 超额 / 失效条件。这四个数是后端
 *    `evaluate_entry` 用 `snapshot.price` 到 `due_on` 收盘算的，前端**不重算也不改写**；
 *    取不到（`null`）就显示 `—`，不补 0——"没有数据"和"结果正好是 0"是两件事。
 *    **结果与超额默认净口径**（`net_outcome_pct` / `net_excess_pct`，毛 − 12bp 往返成本），
 *    毛口径只作为括注出现；净字段缺省（后端 Phase A 未上线）时回退毛口径并明确标注。
 * 2. **基准口径**（只读）：`bench_basis_note` 原文（"同行业成分股等权平均收益，
 *    不是指数行情"），`bench_fallback = true` 时补一句"无行业归属，基准退化为沪深300"。
 * 3. **路径**（只读，spec §S9.7.4）：`mae_pct` / `first_touch_invalidation` /
 *    `days_to_invalidation` 有就展示；`touched_then_reversed = true` 时在归因选择**上方**
 *    给一句「你先触发了失效条件，之后才反弹——这更像纪律问题」——
 *    这是**提示**，不预选归因、不强制选择（归因仍是用户自己的）。
 * 4. **用户选的**（必选）：五个归因之一。未选时「确认复盘」**禁用**（不是点了再报错）；
 *    归因是用户自己的，产品不替他分类，也不给倾向性结论。
 *
 * 一次复盘只写一条（后端 `unique(entry_id)`，重复提交 409）——故弹窗提交成功即关闭，
 * 不做"再次复盘"的入口。
 */

/** 失效条件是否触发：三态（true/false/未知），未知不编。 */
function formatInvalidationHit(hit: boolean | null | undefined): string {
  if (hit === true) return "已触发";
  if (hit === false) return "未触发";
  return "—";
}

/**
 * 只读区数据：`evaluation` 是契约字段；已复盘条目若只带 `review`（同形），
 * 用它兜底——两者都是后端算的同一组数，前端不重算。
 */
function pickEvaluation(entry: JournalEntry): JournalEvaluation | null {
  return entry.evaluation ?? entry.review ?? null;
}

/** 基准的人话说明：`evaluation` 优先，其次创建时的快照（Phase A 可能只写在一处）。 */
function benchBasisNoteOf(
  entry: JournalEntry,
  evaluation: JournalEvaluation | null
): string | null {
  return evaluation?.bench_basis_note ?? entry.snapshot?.bench_basis_note ?? null;
}

/** 基准是否退化为沪深300：`evaluation.bench_fallback` 优先，其次快照里的 `bench_spec`。 */
function benchFallbackOf(entry: JournalEntry, evaluation: JournalEvaluation | null): boolean {
  if (typeof evaluation?.bench_fallback === "boolean") return evaluation.bench_fallback;
  return entry.snapshot?.bench_spec?.fallback === true;
}

interface ReviewModalProps {
  /** 要复盘的条目；`null` = 关闭。 */
  entry: JournalEntry | null;
  onClose: () => void;
}

export function ReviewModal({ entry, onClose }: ReviewModalProps) {
  const queryClient = useQueryClient();
  const [verdict, setVerdict] = useState<JournalVerdict | undefined>(undefined);
  const [note, setNote] = useState("");

  // 每次换条目（或重新打开）都从"未选"开始：上一条选过的归因绝不能顺手带到下一条上，
  // 那是最坏的一种误写（用户以为没选，实际提交了上一条的归因）。
  useEffect(() => {
    if (entry) {
      setVerdict(undefined);
      setNote("");
    }
  }, [entry]);

  const mutation = useMutation({
    mutationFn: (payload: { id: number; verdict: JournalVerdict; note?: string }) =>
      reviewJournalEntry(payload.id, { verdict: payload.verdict, note: payload.note }),
    onSuccess: () => {
      message.success("复盘已记录");
      queryClient.invalidateQueries({ queryKey: ["journal"] });
      onClose();
    },
    onError: () => {
      message.error("复盘没能提交，请稍后重试");
    },
  });

  const evaluation = entry ? pickEvaluation(entry) : null;
  const canConfirm = verdict !== undefined && !mutation.isPending;

  // 路径（spec §S9.7.4）：四个派生指标都**只是事实**，缺省就不显示（不补 0）。
  const mae = typeof evaluation?.mae_pct === "number" ? evaluation.mae_pct : null;
  const firstTouch = evaluation?.first_touch_invalidation ?? null;
  const daysTo = typeof evaluation?.days_to_invalidation === "number" ? evaluation.days_to_invalidation : null;
  const hasPath = mae !== null || !!firstTouch || daysTo !== null;
  // 先触发失效、到期反而盈利 → 显示"更像纪律问题"的提示（**不预选**归因、不强制选择）。
  const touchedThenReversed = evaluation?.touched_then_reversed === true;

  // 净/毛口径：默认净；净字段缺省时回退毛（`basis = "gross"`）并明确标注。
  const outcome = netOutcomeOf(evaluation);
  const excess = netExcessOf(evaluation);
  const usesNet = outcome.basis === "net" || excess.basis === "net";
  const benchNote = entry ? benchBasisNoteOf(entry, evaluation) : null;
  const benchFallback = entry ? benchFallbackOf(entry, evaluation) : false;

  const confirm = () => {
    if (!verdict || !entry) return;
    mutation.mutate({ id: entry.id, verdict, note: note.trim() ? note.trim() : undefined });
  };

  return (
    <Modal
      open={entry !== null}
      title={entry ? `复盘：${entry.symbol ?? "市场"}` : "复盘"}
      onCancel={onClose}
      destroyOnClose
      width={560}
      okText="确认复盘"
      cancelText="取消"
      okButtonProps={{ disabled: !canConfirm, "data-testid": "journal-review-confirm" }}
      onOk={confirm}
    >
      {entry && (
        <div className="journal-review" data-testid="journal-review-modal">
          <p className="journal-review__lead">
            {`到期日 ${formatAsOfDate(entry.due_on)}，以下是系统按到期日收盘价算出的结果（只读）：`}
          </p>

          {evaluation ? (
            <>
              <dl className="journal-review__result" data-testid="journal-review-result">
                <div className="journal-review__cell">
                  <dt>{outcome.basis === "net" ? "结果（净）" : "结果"}</dt>
                  <dd className="num" data-testid="journal-review-outcome">
                    {formatSignedPct(outcome.value)}
                  </dd>
                  {outcome.basis === "net" && (
                    <dd className="journal-review__gross num">
                      {`毛 ${formatSignedPct(evaluation.outcome_pct)}`}
                    </dd>
                  )}
                </div>
                <div className="journal-review__cell">
                  <dt>基准</dt>
                  <dd className="num" data-testid="journal-review-bench">
                    {formatSignedPct(evaluation.bench_pct)}
                  </dd>
                </div>
                <div className="journal-review__cell">
                  <dt>{excess.basis === "net" ? "超额（净）" : "超额"}</dt>
                  <dd className="num" data-testid="journal-review-excess">
                    {formatSignedPct(excess.value)}
                  </dd>
                  {excess.basis === "net" && (
                    <dd className="journal-review__gross num">
                      {`毛 ${formatSignedPct(evaluation.excess_pct)}`}
                    </dd>
                  )}
                </div>
                <div className="journal-review__cell">
                  <dt>失效条件</dt>
                  <dd>{formatInvalidationHit(evaluation.invalidation_hit)}</dd>
                </div>
              </dl>
              <p className="journal-review__basis" data-testid="journal-review-basis">
                {usesNet
                  ? `${netBasisLabel(null)}：结果与超额为净口径，括注为毛口径。`
                  : `${netBasisLabel(null)}：${GROSS_FALLBACK_LABEL}。`}
              </p>
            </>
          ) : (
            // 没有自动结果时**不画 0**：让用户知道"这次没有可展示的结果"，
            // 而不是误以为"结果是 0%"。
            <Alert
              className="journal-review__no-result"
              type="info"
              showIcon
              message="这次没有可展示的自动结果"
              description="到期后的收盘数据暂不可用，不影响你记录自己的归因。"
            />
          )}

          {/* 基准必须匹配「赌的是什么」（spec §S9.7.3）：说明是后端写的人话，原样上屏。 */}
          {benchNote && (
            <p className="journal-review__bench" data-testid="journal-bench-basis-note">
              {benchNote}
            </p>
          )}
          {benchFallback && (
            <p className="journal-review__bench" data-testid="journal-bench-fallback">
              无行业归属，基准退化为沪深300。
            </p>
          )}

          {/* 路径（spec §S9.7.4）：入场到期的中途发生了什么——终点对错之外的另一层事实。 */}
          {hasPath && (
            <dl className="journal-review__path" data-testid="journal-review-path">
              {mae !== null && (
                <div className="journal-review__cell">
                  <dt>最大不利偏移</dt>
                  <dd className="num" data-testid="journal-review-mae">
                    {formatSignedPct(mae)}
                  </dd>
                </div>
              )}
              {firstTouch && (
                <div className="journal-review__cell">
                  <dt>首次触达失效</dt>
                  <dd data-testid="journal-review-first-touch">{formatAsOfDate(firstTouch)}</dd>
                </div>
              )}
              {daysTo !== null && (
                <div className="journal-review__cell">
                  <dt>首次触达距入场</dt>
                  <dd className="num" data-testid="journal-review-days-to">
                    {`第 ${daysTo} 个交易日`}
                  </dd>
                </div>
              )}
            </dl>
          )}

          {/* 先触失效、之后反弹 → 提示"更像纪律问题"（**提示**不是结论，也不预选任何归因）。 */}
          {touchedThenReversed && (
            <p className="journal-review__path-hint" data-testid="journal-path-hint">
              你先触发了失效条件，之后才反弹——这更像<strong>纪律</strong>问题
            </p>
          )}

          <div className="journal-review__section">
            <Text className="journal-review__label">归因（必选，选一个最接近的）</Text>
            <Radio.Group
              className="journal-review__verdicts"
              value={verdict}
              onChange={(event) => setVerdict(event.target.value as JournalVerdict)}
            >
              <Space direction="vertical" size={6}>
                {VERDICT_ORDER.map((key) => (
                  <Radio key={key} value={key} data-testid={`journal-verdict-${key}`}>
                    <span className="journal-review__verdict-label">{VERDICT_META[key].label}</span>
                    <span className="journal-review__verdict-hint">{VERDICT_META[key].hint}</span>
                  </Radio>
                ))}
              </Space>
            </Radio.Group>
            {!verdict && (
              <Text className="journal-review__warn" data-testid="journal-review-need-verdict">
                还没有选归因，选完才能确认。
              </Text>
            )}
          </div>

          <div className="journal-review__section">
            <Text className="journal-review__label">备注（可选，≤500 字）</Text>
            <Input.TextArea
              data-testid="journal-review-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="当时是怎么想的、下次要改什么"
              maxLength={500}
              showCount
              autoSize={{ minRows: 2, maxRows: 5 }}
            />
          </div>
        </div>
      )}
    </Modal>
  );
}
