import { Skeleton } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchCapitalFlow, fetchSectors, fetchSwPerformance } from "@/shared/api/market";
import { DataRow } from "@/shared/ui";
import { BreadthBar } from "./BreadthBar";
import { fmtAmountParts } from "./format";
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
 * 首页板块区：左列申万一级行业行情、右列板块资金流，两列各自独立查询与降级。
 *
 * 左列走 `/market/sw-industry/performance`（申万一级口径），行内含涨跌家数比例条
 * （`BreadthBar`）。**口径诚实**：请求失败时回退 `/market/sectors`（证监会口径），
 * 并把标注如实切回「证监会」——标注永远指向实际展示的数据源，绝不静默错标。
 * 右列 `/market/capital-flow` 是按「上涨股成交额 / 下跌股成交额」聚合的**近似口径**，
 * 非主力资金真实净流入（`/market/sector-moneyflow` 当前返回空数组，无法支撑该列）。
 * 外层由首页 `<SectionCard id="sectors">` 提供卡片壳与标题。
 */
export function SectorFlow() {
  const swQuery = useQuery({
    queryKey: ["landing", "sw-performance"],
    queryFn: fetchSwPerformance,
    staleTime: STALE_TIME,
    // 首次失败即回退 CSRC 口径，不做指数退避重试（避免口径长时间悬空）
    retry: 0,
  });
  const usingCsrc = swQuery.isError;
  const sectorsQuery = useQuery({
    queryKey: ["landing", "sectors"],
    queryFn: fetchSectors,
    staleTime: STALE_TIME,
    enabled: usingCsrc,
  });
  const flowQuery = useQuery({
    queryKey: ["landing", "capital-flow"],
    queryFn: fetchCapitalFlow,
    staleTime: STALE_TIME,
  });

  const swRows = (swQuery.data?.items ?? []).slice(0, ROWS); // 服务端已按 avg_pct_chg 降序
  const csrcRows = [...(sectorsQuery.data ?? [])]
    .sort((a, b) => b.changePercent - a.changePercent)
    .slice(0, ROWS);
  const flows = (flowQuery.data ?? []).slice(0, ROWS);
  const maxAbs = Math.max(1, ...flows.flatMap((f) => [Math.abs(f.inflow), Math.abs(f.outflow)]));

  const leftLoading = usingCsrc ? sectorsQuery.isLoading : swQuery.isLoading;
  const hasLeft = usingCsrc ? csrcRows.length > 0 : swRows.length > 0;

  return (
    <div className="sector-flow">
      <div className="sector-flow__basis">
        {`行业口径：${usingCsrc ? "证监会" : "申万一级"}`}
      </div>
      <div className="sector-flow__cols">
        <div className="sector-flow__col">
          <div className="sector-flow__sub">行业涨跌</div>
          {leftLoading ? (
            <Skeleton active paragraph={{ rows: 4 }} title={false} />
          ) : !hasLeft ? (
            <div className="sector-flow__empty">行业涨跌暂不可用</div>
          ) : usingCsrc ? (
            csrcRows.map((s) => <DataRow key={s.name} title={s.name} delta={s.changePercent} />)
          ) : (
            swRows.map((s) => {
              const amount = fmtAmountParts(s.total_amount);
              return (
                <div className="sector-flow__industry" key={s.code}>
                  <DataRow
                    title={s.name}
                    // member_count 是「当日有行情（pct_chg 非空）的成员数」，不是静态成分总数
                    ticker={`当日有行情 ${s.member_count}只`}
                    value={amount.value}
                    unit={amount.unit}
                    valuePlaceholder="--"
                    delta={s.avg_pct_chg}
                  />
                  <BreadthBar
                    up={s.up_count}
                    down={s.down_count}
                    label={`${s.name} 涨${s.up_count} · 跌${s.down_count}`}
                  />
                </div>
              );
            })
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
