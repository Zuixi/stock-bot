import { Timeline, Tooltip } from "antd";
import type { Signal } from "@/shared/api/industryResearch";
import {
  buildSignalHistoryRows,
  type SignalChange,
  type SignalHistoryRow,
} from "./signalHistoryDiff";
import "./SignalPanel.css";

interface Props {
  current: Signal;
  history: Signal[];
}

const SIGNAL_META: Record<string, { color: string; desc: string }> = {
  买入: { color: "#ef4444", desc: "右侧趋势确认，做多" },
  卖出: { color: "#22c55e", desc: "过热/见顶，兑现收益" },
  关注: { color: "#faad14", desc: "左侧布局窗口临近，建仓观察" },
  空仓: { color: "#8c8c8c", desc: "防守等待，回避亏损期" },
};

function metaOf(type: string) {
  return SIGNAL_META[type] ?? { color: "#8c8c8c", desc: "" };
}

const HISTORY_LIMIT = 6;

/** 变化项的一行：`猪粮比 4.65 → 4.34`（同一指标换了值）或 `+ 新出现的判据`。 */
function ChangeLine({ change }: { change: SignalChange }) {
  if (change.kind === "value") {
    return (
      <div className="signal-change" data-change-kind="value">
        <span className="signal-change__from">{change.from}</span>
        <span className="signal-change__arrow">→</span>
        <span className="signal-change__to">{change.text}</span>
      </div>
    );
  }
  return (
    <div className="signal-change" data-change-kind="changed">
      <span className="signal-change__new" aria-label="新增判据">
        ＋
      </span>
      {change.text}
    </div>
  );
}

/** 历史行正文（Phase 4 §5.5：首条给完整触发依据，后续只给变化项，连续无变化折叠） */
function RowBody({ row, index }: { row: SignalHistoryRow; index: number }) {
  if (row.kind === "collapsed") {
    return (
      <div
        className="signal-history__collapsed"
        data-testid={`signal-history-collapsed-${index}`}
        data-collapsed-count={row.entries}
      >
        连续 {row.entries} 条无变化
        {row.effectiveDate !== row.untilDate ? (
          <span className="signal-history__range">
            {" "}
            （{row.effectiveDate} ~ {row.untilDate}）
          </span>
        ) : null}
      </div>
    );
  }
  if (row.changes.length === 0) {
    return <div className="signal-history__nochange">与上一条无变化</div>;
  }
  return (
    <div className="signal-history__changes" data-testid={`signal-history-changes-${index}`}>
      {row.changes.map((c, i) => (
        <ChangeLine key={`${c.kind}-${i}-${c.text}`} change={c} />
      ))}
    </div>
  );
}

/**
 * 交易信号面板：当前信号徽章 + 历史信号时间线。
 *
 * **Phase 4 §5.5 把历史从"每条抄一遍全集"改成 diff**：规则引擎的 `触发依据` 是规则全集，
 * 相邻两条往往只差一个数——原先一屏 6 条重复同一句话，要逐字找差异。现在：
 * - 首条给**完整** `触发依据`（`RowBody` 的 `index === 0` 分支渲染 `full` 段）；
 * - 自第 2 条起只渲染**变化项**（`猪粮比 4.65 → 4.34`、`＋ 新判据`）；
 * - 连续 ≥2 条完全无变化 → 折叠成一行 `连续 N 条无变化`（**条目数不丢**：折叠行带
 *   `data-collapsed-count`，且信号日期区间照旧上屏）。
 *
 * diff 算法在 `./signalHistoryDiff.ts`（纯函数，边界与对齐规则写在那个文件里）。
 */
export function SignalPanel({ current, history }: Props) {
  const meta = metaOf(current.signalType);
  const rows = buildSignalHistoryRows(history.slice(0, HISTORY_LIMIT));
  const firstFull = history.length > 0 ? (history[0].reason ?? "") : "";

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
        <span style={{ fontSize: 12.5, color: "#4e5969" }}>当前信号</span>
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

      {current.reason && (
        <div style={{ marginTop: 8, fontSize: 12, color: "#86909c", lineHeight: 1.7 }}>
          {current.reason}
        </div>
      )}

      <Timeline
        style={{ marginTop: 16, marginBottom: 0 }}
        items={rows.map((row, index) => {
          const m = metaOf(row.signalType);
          return {
            color: row.kind === "collapsed" ? "#d9d9d9" : m.color,
            children: (
              <div data-testid={`signal-history-row-${index}`} data-row-kind={row.kind}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
                  <b style={{ fontSize: 14, color: m.color }}>{row.signalType}</b>
                  <span style={{ fontSize: 12, color: "#86909c" }} className="num">
                    {row.effectiveDate}
                  </span>
                </div>
                {/* 首条：完整触发依据（其余条不再重复这段文本——这就是 diff 化的全部意义） */}
                {index === 0 && firstFull ? (
                  <div
                    className="signal-history__full"
                    data-testid="signal-history-full"
                    style={{ fontSize: 12, color: "#86909c", marginTop: 2, lineHeight: 1.55 }}
                  >
                    {firstFull}
                  </div>
                ) : (
                  <RowBody row={row} index={index} />
                )}
              </div>
            ),
          };
        })}
      />
    </div>
  );
}
