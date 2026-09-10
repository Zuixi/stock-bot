import { Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchCapitalFlow, fetchSectors } from "@/shared/api/market";
import { DataRow } from "@/shared/ui";
import "./SectorFlow.css";

const STALE_TIME = 60_000;
const ROWS = 8;

/** 近似资金流净额（亿元）：inflow 为上涨股成交额、outflow 为下跌股成交额（负值）。 */
function flowNet(inflow: number, outflow: number): { net: number; label: string } {
  const net = inflow + outflow;
  if (net > 0) return { net, label: "净流入" };
  if (net < 0) return { net, label: "净流出" };
  return { net, label: "净额" };
}

/**
 * 首页板块区：左列行业涨跌、右列板块资金流，两列各自独立查询与降级。
 *
 * 口径诚实标注：左列走 `/market/sectors`（证监会 csrc_desc，Phase 3 Task 3.2 切申万）；
 * 右列 `/market/capital-flow` 是按「上涨股成交额 / 下跌股成交额」聚合的**近似口径**，
 * 不是主力资金真实净流入——`/market/sector-moneyflow` 当前返回空数组，无法支撑该列。
 * 外层由首页 `<SectionCard id="sectors">` 提供卡片壳与标题。
 */
export function SectorFlow() {
  const sectorsQuery = useQuery({
    queryKey: ["landing", "sectors"],
    queryFn: fetchSectors,
    staleTime: STALE_TIME,
  });
  const flowQuery = useQuery({
    queryKey: ["landing", "capital-flow"],
    queryFn: fetchCapitalFlow,
    staleTime: STALE_TIME,
  });

  const sectors = [...(sectorsQuery.data ?? [])]
    .sort((a, b) => b.changePercent - a.changePercent)
    .slice(0, ROWS);
  const flows = (flowQuery.data ?? []).slice(0, ROWS);
  const maxAbs = Math.max(1, ...flows.flatMap((f) => [Math.abs(f.inflow), Math.abs(f.outflow)]));

  return (
    <div className="sector-flow">
      <div className="sector-flow__basis">行业口径：证监会（申万版即将上线）</div>
      <div className="sector-flow__cols">
        <div className="sector-flow__col">
          <div className="sector-flow__sub">行业涨跌</div>
          {sectorsQuery.isLoading ? (
            <Skeleton active paragraph={{ rows: 4 }} title={false} />
          ) : sectors.length > 0 ? (
            sectors.map((s) => <DataRow key={s.name} title={s.name} delta={s.changePercent} />)
          ) : (
            <div className="sector-flow__empty">行业涨跌暂不可用</div>
          )}
        </div>

        <div className="sector-flow__col">
          <div className="sector-flow__sub">
            板块资金流
            <span className="sector-flow__note">近似口径：涨/跌股成交额估算，非主力净流入</span>
          </div>
          {flowQuery.isLoading ? (
            <Skeleton active paragraph={{ rows: 4 }} title={false} />
          ) : flows.length > 0 ? (
            flows.map((f) => {
              const { net, label } = flowNet(f.inflow, f.outflow);
              const tone = net > 0 ? "is-up" : net < 0 ? "is-down" : "is-flat";
              return (
                <div className="sector-flow__flow" key={f.name}>
                  <span className="sector-flow__flow-name" title={f.name}>
                    {f.name}
                  </span>
                  <span className="sector-flow__flow-bar" aria-hidden>
                    <span
                      className="sector-flow__flow-in"
                      style={{ width: `${(Math.abs(f.inflow) / maxAbs) * 50}%` }}
                    />
                    <span
                      className="sector-flow__flow-out"
                      style={{ width: `${(Math.abs(f.outflow) / maxAbs) * 50}%` }}
                    />
                  </span>
                  <span className={`sector-flow__flow-net ${tone}`}>
                    {label} {net > 0 ? "+" : net < 0 ? "−" : ""}
                    {Math.abs(net).toFixed(2)}亿
                  </span>
                </div>
              );
            })
          ) : (
            <div className="sector-flow__empty">资金流暂不可用</div>
          )}
        </div>
      </div>
    </div>
  );
}
