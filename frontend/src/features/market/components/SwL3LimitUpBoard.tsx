import { useMemo, useState } from "react";
import { Switch, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import type { SectorLimitUp } from "@/shared/api/limitUp";
import "./SwL3LimitUpBoard.css";

interface Props {
  data: SectorLimitUp | null | undefined;
  /** 端点降级（如 partial_day）时渲染短占位文案，不展示不完整数据。 */
  degraded?: boolean;
}

const ALL = "all";

/** 板高热档：与连板梯队同一色阶语义（streak 1-4，≥4 归 h4）。 */
function heatClass(streak: number): string {
  return `sw3-row--h${Math.min(Math.max(streak, 1), 4)}`;
}

/**
 * 申万三级最高板（热度榜重设计）：一级行业 CheckableTag 过滤（wrap 不截断，替代
 * 21 项挤一行只显示单字的 block Segmented）+「仅看 ≥2 板」开关 + 自绘行列表。
 * 行内：细分行业 / 板高格（4 格热度色阶，与梯队同语言）/ 龙头 / 涨停家数占比条。
 * 排序 板高 desc → 家数 desc → l3Code（确定性，避免同板高行抖动）；行点击跳龙头个股。
 */
export function SwL3LimitUpBoard({ data, degraded = false }: Props) {
  const navigate = useNavigate();
  const [l1, setL1] = useState<string>(ALL);
  const [lianzOnly, setLianzOnly] = useState(false);

  const items = data?.items ?? [];

  const l1Options = useMemo(() => {
    const seen = new Map<string, { name: string; count: number }>();
    for (const item of items) {
      if (!item.l1Code) continue;
      const prev = seen.get(item.l1Code);
      if (prev) {
        prev.count += 1;
      } else {
        seen.set(item.l1Code, { name: item.l1Name ?? item.l1Code, count: 1 });
      }
    }
    return [...seen.entries()].sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0]));
  }, [items]);

  const rows = useMemo(
    () =>
      items
        .filter((i) => (l1 === ALL || i.l1Code === l1) && (!lianzOnly || i.maxStreak >= 2))
        .sort(
          (a, b) =>
            b.maxStreak - a.maxStreak ||
            b.ztCount - a.ztCount ||
            a.l3Code.localeCompare(b.l3Code),
        ),
    [items, l1, lianzOnly],
  );

  const maxZt = Math.max(...rows.map((r) => r.ztCount), 1);

  if (degraded) {
    return (
      <div className="sector-limit-up">
        <Typography.Text
          type="secondary"
          style={{ display: "block", padding: "24px 0", textAlign: "center" }}
        >
          数据不完整，暂不展示板块板高
        </Typography.Text>
      </div>
    );
  }

  return (
    <div className="sector-limit-up">
      <div className="sw3-filter">
        <div className="sw3-filter__tags">
          <Tag.CheckableTag className="sw3-tag" checked={l1 === ALL} onChange={() => setL1(ALL)}>
            全部 <span className="sw3-tag__count">{items.length}</span>
          </Tag.CheckableTag>
          {l1Options.map(([code, { name, count }]) => (
            <Tag.CheckableTag
              key={code}
              className="sw3-tag"
              checked={l1 === code}
              onChange={() => setL1(l1 === code ? ALL : code)}
            >
              {name} <span className="sw3-tag__count">{count}</span>
            </Tag.CheckableTag>
          ))}
        </div>
        <label className="sw3-lianz-toggle">
          <Switch size="small" checked={lianzOnly} onChange={setLianzOnly} />
          仅看 ≥2 板
        </label>
      </div>

      <div className="sw3-head" aria-hidden>
        <span>细分行业</span>
        <span>最高板</span>
        <span>龙头</span>
        <span className="sw3-head__right">涨停家数</span>
      </div>

      {rows.length === 0 ? (
        <Typography.Text
          type="secondary"
          style={{ display: "block", padding: "24px 0", textAlign: "center" }}
        >
          {lianzOnly ? "当前筛选下无连板行业" : "无申万三级涨停数据"}
        </Typography.Text>
      ) : (
        <ul className="sw3-list">
          {rows.map((r) => (
            <li key={r.l3Code}>
              <div
                className={`sw3-row ${heatClass(r.maxStreak)}`}
                onClick={() => navigate(`/stock/${r.leaderSymbol}`)}
                title={`${r.l3Name ?? r.l3Code} · 龙头 ${r.leaderName ?? "--"} → 个股页`}
              >
                <span className="sw3-row__names">
                  <span className="sw3-row__l3">{r.l3Name ?? "--"}</span>
                  {r.l1Name && l1 === ALL ? <span className="sw3-row__l1">{r.l1Name}</span> : null}
                </span>
                <span className="sw3-row__streak">
                  <span className="sw3-cells" aria-hidden>
                    {[1, 2, 3, 4].map((n) => (
                      <i key={n} className={n <= r.maxStreak ? "sw3-cells__on" : undefined} />
                    ))}
                  </span>
                  {r.maxStreak}板
                </span>
                <span className="sw3-row__leader">{r.leaderName ?? "--"}</span>
                <span className="sw3-row__zt">
                  <span className="sw3-row__zt-bar" aria-hidden>
                    <span style={{ width: `${(r.ztCount / maxZt) * 100}%` }} />
                  </span>
                  {r.ztCount}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
