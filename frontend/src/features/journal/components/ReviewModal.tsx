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

const { Text } = Typography;

/**
 * 复盘弹窗：**自动结果只读 + 归因五选一必选**（spec §S9.4）。
 *
 * 三段固定次序：
 * 1. **系统算的**（只读）：结果 / 基准 / 超额 / 失效条件是否触发。这四个数是后端
 *    `evaluate_entry` 用 `snapshot.price` 到 `due_on` 收盘算的，前端**不重算也不改写**；
 *    取不到（`null`）就显示 `—`，不补 0——"没有数据"和"结果正好是 0"是两件事。
 * 2. **用户选的**（必选）：五个归因之一。未选时「确认复盘」**禁用**（不是点了再报错）；
 *    归因是用户自己的，产品不替他分类，也不给倾向性结论。
 * 3. **可选备注**（≤500 字）：用户自己的话，原样保存。
 *
 * 一次复盘只写一条（后端 `unique(entry_id)`，重复提交 409）——故弹窗提交成功即关闭，
 * 不做"再次复盘"的入口。
 */

/** 百分比口径：保留符号（可以为负），取不到时 `—` 而不是 0。 */
function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

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
            <dl className="journal-review__result" data-testid="journal-review-result">
              <div className="journal-review__cell">
                <dt>结果</dt>
                <dd className="num">{formatSignedPct(evaluation.outcome_pct)}</dd>
              </div>
              <div className="journal-review__cell">
                <dt>基准</dt>
                <dd className="num">{formatSignedPct(evaluation.bench_pct)}</dd>
              </div>
              <div className="journal-review__cell">
                <dt>超额</dt>
                <dd className="num">{formatSignedPct(evaluation.excess_pct)}</dd>
              </div>
              <div className="journal-review__cell">
                <dt>失效条件</dt>
                <dd>{formatInvalidationHit(evaluation.invalidation_hit)}</dd>
              </div>
            </dl>
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
