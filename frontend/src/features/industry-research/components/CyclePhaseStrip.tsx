import { Tag } from "antd";
import type { Cycle } from "@/shared/api/industryResearch";

interface Props {
  cycle: Cycle;
}

/** 猪周期四阶段相位条（繁荣→衰退→萧条→复苏），当前阶段高亮 */
export function CyclePhaseStrip({ cycle }: Props) {
  const basis = cycle.basis as {
    ratio?: number | null;
    price?: number | null;
    cost?: number | null;
    sowConsecutiveDecline?: number;
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
        {cycle.phases.map((p, i) => {
          const active = p.active || p.key === cycle.phase;
          return (
            <div
              key={p.key}
              style={{
                flex: 1,
                position: "relative",
                border: `1px solid ${active ? "#ffe58f" : "transparent"}`,
                background: active ? "linear-gradient(180deg,#fffbeb,#fffefa)" : "transparent",
                borderRadius: 8,
                padding: "9px 12px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <span
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 11,
                    fontWeight: 700,
                    color: active ? "#d48806" : "#86909c",
                    background: active ? "#ffe58f" : "#f2f3f5",
                  }}
                >
                  {i + 1}
                </span>
                <span
                  style={{
                    fontSize: 14,
                    fontWeight: 600,
                    color: active ? "#d48806" : "#4e5969",
                  }}
                >
                  {p.label}
                </span>
                {active && (
                  <Tag color="gold" style={{ marginInlineStart: 0, fontSize: 10.5, lineHeight: "16px" }}>
                    当前
                  </Tag>
                )}
                {i < cycle.phases.length - 1 && (
                  <span
                    style={{
                      position: "absolute",
                      right: -10,
                      top: "50%",
                      transform: "translateY(-50%)",
                      color: "#c9cdd4",
                      fontSize: 14,
                      zIndex: 1,
                    }}
                  >
                    ›
                  </span>
                )}
              </div>
              <div style={{ fontSize: 12, color: "#86909c", marginTop: 3 }}>{p.desc}</div>
            </div>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 12,
          paddingTop: 10,
          borderTop: "1px dashed #f0f0f0",
          fontSize: 12,
          color: "#86909c",
          lineHeight: 1.8,
        }}
      >
        判定依据：
        {typeof basis.ratio === "number" && (
          <> 猪粮比 <b style={{ color: "#4e5969" }}>{basis.ratio.toFixed(2)}</b> ·</>
        )}
        {typeof basis.sowConsecutiveDecline === "number" && basis.sowConsecutiveDecline > 0 && (
          <> 能繁存栏环比<b style={{ color: "#4e5969" }}>连续 {basis.sowConsecutiveDecline} 个月回落</b> ·</>
        )}
        {typeof basis.price === "number" && typeof basis.cost === "number" && (
          <> 价格 vs 成本{" "}
            <b style={{ color: basis.price >= basis.cost ? "#ef4444" : "#22c55e" }}>
              {basis.price >= basis.cost ? "+" : ""}
              {(basis.price - basis.cost).toFixed(2)} 元/kg
            </b></>
        )}
        {cycle.reasons.length > 0 && <div>规则结论：{cycle.reasons.join("；")}</div>}
      </div>
    </div>
  );
}
