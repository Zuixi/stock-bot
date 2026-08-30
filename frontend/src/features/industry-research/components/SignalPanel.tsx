import { Timeline, Tooltip } from "antd";
import type { Signal } from "@/shared/api/industryResearch";

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

/** 交易信号面板：当前信号徽章 + 历史信号时间线 */
export function SignalPanel({ current, history }: Props) {
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
        items={history.slice(0, 6).map((s) => {
          const m = metaOf(s.signalType);
          return {
            color: m.color,
            children: (
              <div>
                <div style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
                  <b style={{ fontSize: 14, color: m.color }}>{s.signalType}</b>
                  <span style={{ fontSize: 12, color: "#86909c", fontFamily: '"Bahnschrift","Segoe UI",sans-serif' }}>
                    {s.effectiveDate}
                  </span>
                </div>
                {s.reason && (
                  <div style={{ fontSize: 12, color: "#86909c", marginTop: 2, lineHeight: 1.55 }}>
                    {s.reason}
                  </div>
                )}
              </div>
            ),
          };
        })}
      />
    </div>
  );
}
