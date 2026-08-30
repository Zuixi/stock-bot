import type { PositionSlice } from "@/shared/api/industryResearch";

interface Props {
  positions: PositionSlice[];
}

/** 仓位管理建议：堆叠比例条 + 三档说明（随信号联动） */
export function PositionAdviceBar({ positions }: Props) {
  return (
    <div>
      <div
        style={{
          display: "flex",
          height: 20,
          borderRadius: 10,
          overflow: "hidden",
          marginTop: 14,
          boxShadow: "inset 0 0 0 1px rgba(31,35,41,.06)",
        }}
      >
        {positions.map((p, i) => (
          <div
            key={p.name}
            style={{
              flex: `0 0 ${p.pct}%`,
              background: p.color,
              boxShadow: i > 0 ? "inset 2px 0 0 #fff" : undefined,
              position: "relative",
              display: "grid",
              placeItems: "center",
            }}
          >
            {p.pct >= 10 && (
              <span style={{ color: "#fff", fontWeight: 600, fontSize: 11, letterSpacing: 0.5 }}>
                {p.pct}%
              </span>
            )}
          </div>
        ))}
      </div>

      <div style={{ marginTop: 12 }}>
        {positions.map((p) => (
          <div
            key={p.name}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
              padding: "10px 4px",
              borderBottom: "1px dashed #f0f0f0",
            }}
          >
            <span
              style={{
                flexShrink: 0,
                width: 9,
                height: 9,
                borderRadius: 3,
                background: p.color,
                marginTop: 6,
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div>
                <b style={{ fontSize: 13.5 }}>{p.name}</b>
                <span style={{ fontSize: 11.5, color: "#86909c", marginLeft: 6 }}>{p.role}</span>
              </div>
              <div style={{ fontSize: 12, color: "#86909c", marginTop: 1 }}>{p.desc}</div>
            </div>
            <span
              style={{
                fontFamily: '"Bahnschrift","Segoe UI",sans-serif',
                fontWeight: 700,
                fontSize: 20,
                color: p.color,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {p.pct}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
