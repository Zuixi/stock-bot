import { Empty, Space, Tag, Timeline, Tooltip, Typography } from "antd";
import type {
  Signal,
  SignalEvaluation,
  SignalEvent,
  VerificationSummary,
} from "@/shared/api/industryResearch";

interface Props {
  current: Signal | null;
  events: SignalEvent[];
  signalIsStale: boolean;
  verificationSummary: VerificationSummary;
}

const SIGNAL_META: Record<string, { color: string; desc: string }> = {
  买入: { color: "#ef4444", desc: "右侧趋势确认，做多" },
  卖出: { color: "#22c55e", desc: "过热/见顶，兑现收益" },
  关注: { color: "#faad14", desc: "左侧布局窗口临近，建仓观察" },
  空仓: { color: "#8c8c8c", desc: "防守等待，回避亏损期" },
};

const VERDICT_META: Record<string, { text: string; color: string }> = {
  pending: { text: "待验证", color: "default" },
  confirmed: { text: "已确认", color: "success" },
  partially_confirmed: { text: "部分确认", color: "processing" },
  invalidated: { text: "未确认", color: "error" },
  inconclusive: { text: "证据不足", color: "warning" },
};

function metaOf(type: string) {
  return SIGNAL_META[type] ?? { color: "#8c8c8c", desc: "" };
}

function EvaluationBadge({ evaluation }: { evaluation: SignalEvaluation }) {
  const verdict = VERDICT_META[evaluation.status] ?? {
    text: evaluation.status,
    color: "default",
  };
  const evidence = evaluation.insufficientReasons.length > 0
    ? evaluation.insufficientReasons.join("；")
    : evaluation.criteriaResults
      .filter((criterion) => criterion.status === "met")
      .slice(0, 2)
      .map((criterion) => criterion.metricKey)
      .join("、");

  return (
    <div
      data-testid={`signal-evaluation-${evaluation.horizonDays}`}
      style={{ fontSize: 12, color: "#4e5969" }}
    >
      <Space size={6} wrap>
        <Tag color={verdict.color}>{evaluation.horizonDays}天 {verdict.text}</Tag>
        {evaluation.score !== null && <b>{evaluation.score}分</b>}
        {evaluation.status === "pending" && <span>目标日期 {evaluation.targetDate}</span>}
        {evidence && <span>{evidence}</span>}
      </Space>
    </div>
  );
}

/** 交易信号面板：当前有效信号 + 去重后的信号事件及后端验证摘要 */
export function SignalPanel({ current, events, signalIsStale, verificationSummary }: Props) {
  if (!current) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无有效信号" />;
  }

  const meta = metaOf(current.signalType);

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          marginTop: 12,
          background: "linear-gradient(135deg,#fffbeb,#fffdf4)",
          border: "1px solid #ffe58f",
          borderRadius: 10,
          padding: "12px 16px",
        }}
      >
        <span
          style={{
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: meta.color,
            boxShadow: `0 0 0 4px ${meta.color}2e`,
          }}
        />
        <span style={{ fontSize: 12.5, color: "#4e5969" }}>
          {signalIsStale ? "最近一次有效信号" : "当前信号"}
        </span>
        <span style={{ fontWeight: 700, fontSize: 22, lineHeight: 1, color: meta.color }}>
          {current.signalType}
        </span>
        <span style={{ fontSize: 12, color: "#86909c" }}>{current.effectiveDate} 生效</span>
        <Tooltip title={meta.desc}>
          <span style={{ marginLeft: "auto", fontSize: 12, color: "#86909c", cursor: "help" }}>
            信号说明 ⓘ
          </span>
        </Tooltip>
      </div>

      {signalIsStale && (
        <Typography.Text type="warning" style={{ display: "block", marginTop: 8, fontSize: 12 }}>
          本次因数据不足未更新
        </Typography.Text>
      )}
      {current.reason && (
        <div style={{ marginTop: 8, fontSize: 12, color: "#86909c", lineHeight: 1.7 }}>
          {current.reason}
        </div>
      )}

      <Space size={[4, 6]} wrap style={{ marginTop: 12 }}>
        <Typography.Text type="secondary">验证样本：</Typography.Text>
        <Tag>已验证 {verificationSummary.completedDirectionalEvaluations}</Tag>
        <Tag color="success">确认 {verificationSummary.confirmed}</Tag>
        <Tag color="processing">部分确认 {verificationSummary.partiallyConfirmed}</Tag>
        <Tag color="error">未确认 {verificationSummary.invalidated}</Tag>
        <Tag color="warning">证据不足 {verificationSummary.inconclusive}</Tag>
        <Tag>待验证 {verificationSummary.pending}</Tag>
        {verificationSummary.accuracyPct !== null && (
          <Tag color="blue">后端准确率 {verificationSummary.accuracyPct}%</Tag>
        )}
      </Space>

      <div data-testid="signal-event-timeline">
        {events.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无信号事件" />
        ) : (
          <Timeline
            style={{ marginTop: 16, marginBottom: 0 }}
            items={events.slice(0, 6).map((event) => {
              const eventMeta = metaOf(event.signalType);
              return {
                color: eventMeta.color,
                children: (
                  <div>
                    <div style={{ display: "flex", alignItems: "baseline", gap: 9, flexWrap: "wrap" }}>
                      <b style={{ fontSize: 14, color: eventMeta.color }}>{event.signalType}</b>
                      <span style={{ fontSize: 12, color: "#86909c" }}>{event.eventDate}</span>
                      {event.previousSignalType && (
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {event.previousSignalType} → {event.signalType}
                        </Typography.Text>
                      )}
                    </div>
                    {event.verificationSupported ? (
                      <Space direction="vertical" size={4} style={{ marginTop: 6 }}>
                        {event.evaluations.map((evaluation) => (
                          <EvaluationBadge
                            key={`${event.eventDate}-${evaluation.horizonDays}`}
                            evaluation={evaluation}
                          />
                        ))}
                      </Space>
                    ) : (
                      <Tag style={{ marginTop: 6 }}>当前信号类型暂不验证</Tag>
                    )}
                  </div>
                ),
              };
            })}
          />
        )}
      </div>
    </div>
  );
}
