import { Alert, Empty, Skeleton, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  fetchTriage,
  type TriageItem,
  type TriageResponse,
  type TriageSeverity,
} from "@/shared/api/triage";
import { formatAsOfDate } from "@/shared/ui/date";
import "./TriagePanel.css";

/**
 * `/me?tab=triage`「今日体检」：把后端探测器产出的**事实清单**原样摆出来。
 *
 * 三条不可越界的纪律（spec §S8.1/§S8.4/§S8.5）：
 * 1. **只陈述已发生的事实**——页面不改写 `headline`、不给"该不该"的判断，分级只有
 *    风险/事件/关注/背景四枚举（`SEVERITY_ORDER` 是唯一真相，多一种就无处可放）；
 * 2. **三态各有明确文案**（有命中 / 全静默 / 无自选）。全静默与"接口挂了"必须长得不一样：
 *    前者是 `items=[]` + `quiet_count>0`，后者走 error 分支，绝不拿空态糊过去；
 * 3. `unavailable` **不得被静默吞掉**（§S8.4）——缺数据的探测器要在页面上留痕，否则用户会把
 *    "没查"读成"没事"。
 *
 * 数据侧不变量由后端保证（单股合并、≤8 条、`folded` 折叠数），故这里**不做**前端的二次过滤/合并：
 * 前端一过滤，e2e 的"条目数 = 响应条目数"（§S8.4 门禁）就失去意义。
 */

/** 分级次序 = spec 的固定次序（risk > event > attention > background）。 */
const SEVERITY_ORDER: readonly TriageSeverity[] = ["risk", "event", "attention", "background"];

const SEVERITY_LABEL: Record<TriageSeverity, string> = {
  risk: "风险",
  event: "事件",
  attention: "关注",
  background: "背景",
};

/**
 * 「复核」去处：由后端 `verify` 语义决定，**不在前端猜**。
 * - `kline` → 该股 K 线（`/stock/:symbol`，路由清单里是 redirect 到 `/company/:symbol`）；
 * - `announcement` → 公告原文（`/news`）；
 * - 其余（`dragon_tiger` / `sector` / 未知值）→ 该股 K 线，保证每张卡片**永远有**复核入口。
 */
function verifyHref(item: TriageItem): string {
  if (item.verify === "announcement") return "/news";
  return `/stock/${encodeURIComponent(item.symbol)}`;
}

function TriageCard({ item }: { item: TriageItem }) {
  return (
    <li className={`triage__card triage__card--${item.severity}`} data-testid="triage-item">
      <div className="triage__card-head">
        <Tag className={`triage__tag triage__tag--${item.severity}`}>
          {SEVERITY_LABEL[item.severity] ?? SEVERITY_LABEL.background}
        </Tag>
        <span className="triage__name">{item.name}</span>
        <span className="triage__symbol num">{item.symbol}</span>
      </div>

      <p className="triage__headline">{item.headline}</p>

      {item.facts.length > 0 && (
        <dl className="triage__facts">
          {item.facts.map((fact, idx) => (
            <div className="triage__fact" key={`${fact.label}-${idx}`}>
              <dt className="triage__fact-label">{fact.label}</dt>
              <dd className="triage__fact-value num">{`${fact.value}${fact.unit}`}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="triage__card-foot">
        <span className="triage__asof">数据截至 {formatAsOfDate(item.as_of)}</span>
        <Link className="triage__verify" to={verifyHref(item)}>
          复核
        </Link>
      </div>
    </li>
  );
}

function TriageView({ data }: { data: TriageResponse }) {
  const hasItems = data.items.length > 0;
  const hasWatchlist = data.quiet_count > 0 || data.items.length > 0;

  return (
    <div className="triage">
      <div className="triage__meta">
        <span>数据截至 {formatAsOfDate(data.as_of)}</span>
        {data.quiet_count > 0 && <span className="triage__quiet">今日平静 {data.quiet_count} 只</span>}
        {data.folded > 0 && (
          <span className="triage__folded">另有 {data.folded} 项未展示</span>
        )}
      </div>

      {data.unavailable.length > 0 && (
        <Alert
          className="triage__unavailable"
          type="info"
          showIcon
          message={`${data.unavailable.length} 项检查因数据暂缺未执行`}
          // 不把 `unavailable` 的原始 key（`dragon_tiger` 之类）贴到屏幕上：那是内部标识，
          // 用户读不懂，且与本仓库"机器串不上屏"的同一条纪律冲突（见 shared/ui/date.ts）。
          description="这些检查今天没有取到数据，相关事实可能不完整——不等于「没有发生」。"
        />
      )}

      {!hasItems &&
        (hasWatchlist ? (
          // 全静默：明确语句，不是空态占位（spec §S8.1 验收判据）
          <Empty
            className="triage__empty"
            description={`今天你的自选都平静（${data.quiet_count} 只）`}
          />
        ) : (
          // 无自选：给入口，不留白页（spec §S8.1 验收判据）
          <Empty
            className="triage__empty"
            description={
              <span>
                还没有自选股，先添加自选，这里会列出每天需要知道的事实。
                <Link className="triage__add" to="/me?tab=watchlist">
                  添加自选
                </Link>
              </span>
            }
          />
        ))}

      {hasItems &&
        SEVERITY_ORDER.map((severity) => {
          const group = data.items.filter((item) => item.severity === severity);
          if (group.length === 0) return null;
          return (
            <section className="triage__group" key={severity} aria-label={SEVERITY_LABEL[severity]}>
              <h3 className="triage__group-title">
                {SEVERITY_LABEL[severity]}
                <span className="triage__group-count">{group.length}</span>
              </h3>
              <ul className="triage__list">
                {group.map((item) => (
                  <TriageCard item={item} key={`${item.symbol}-${item.detector}`} />
                ))}
              </ul>
            </section>
          );
        })}

      {data.disclaimer && <p className="triage__disclaimer">{data.disclaimer}</p>}
    </div>
  );
}

export function TriagePanel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["triage"],
    queryFn: fetchTriage,
    staleTime: 30_000,
  });

  if (isLoading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }

  // error 与空态必须分开：把"接口挂了"画成"今天没事"是这类页面最坏的谎（§S8.4 同一理由）。
  if (isError || !data) {
    return (
      <Alert
        className="triage__error"
        type="warning"
        showIcon
        message="体检数据暂不可用"
        description="接口暂时没能返回数据，请稍后刷新重试；这不代表今天没有需要知道的事。"
      />
    );
  }

  return <TriageView data={data} />;
}
