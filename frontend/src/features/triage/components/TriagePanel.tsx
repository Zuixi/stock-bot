import { useState } from "react";
import { Alert, Button, Descriptions, Drawer, Empty, Skeleton, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  fetchTriage,
  fetchTriageRules,
  type TriageItem,
  type TriageResponse,
  type TriageRule,
  type TriageRuleStats,
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
 * 2. **状态显式**：文案只看后端的 `state`（`ok` / `no_data` / `no_watchlist`），
 *    不从 `items=[] && quiet_count=0` 反推——那个组合既可能是"没自选"也可能是
 *    "今天的数据还没就绪"，猜错就把"查不了"说成"你没自选"。接口挂了走 error 分支，
 *    与任何空态都不一样（§S8.4 同一条理由：不许把"没查"读成"没事"）；
 * 3. `unavailable` **不得被静默吞掉**（§S8.4）——缺数据的检查要在页面上留痕，否则用户会把
 *    "没查"读成"没事"；上屏只用后端的**人话 label**，`unavailable_keys` 永远不渲染。
 *
 * 数据侧不变量由后端保证（单股合并、≤8 条、`folded` 折叠数），故这里**不做**前端的二次过滤/合并：
 * 前端一过滤，e2e 的"条目数 = 响应条目数"（§S8.4 门禁）就失去意义。
 *
 * **规则出处（provenance）**：卡片脚上方一行只写「规则：人话阈值 · 版本 · 数据表」——
 * 它是**出处**，不是结论；想知道"这条规则怎么算、什么时候会算错、历史统计如何"，
 * 点「规则说明」进抽屉。抽屉内容全部来自后端（`GET /api/v1/watchlists/triage/rules`），
 * 前端不改写、不补算、不把 `stats=null` 显成 0（"没有样本"必须与"样本是 0"分开说）。
 */

/** 百分比口径的统计值：**保留符号**（可为负），取不到时用 `—` 而不是 0。 */
function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  // 负号由 `toFixed` 自带；正数显式补 `+`，否则"正/负"要靠读者自己比，违反"数字要能读"。
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

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
 * - `kline` → 该股 K 线（`/stock/:symbol`；该路由 redirect 到 `/company/:symbol`，
 *   终点是真页面 `StockDetailPage`，不是占位页——故保持 `/stock/{symbol}`，不自作主张改目标）；
 * - `announcement` → 公告原文（`/news`）；
 * - 其余（`dragon_tiger` / `sector` / 未知值）→ 该股 K 线，保证每张卡片**永远有**复核入口。
 */
function verifyHref(item: TriageItem): string {
  if (item.verify === "announcement") return "/news";
  return `/stock/${encodeURIComponent(item.symbol)}`;
}

function TriageCard({ item, onOpenRule }: { item: TriageItem; onOpenRule: () => void }) {
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

      {/* 规则出处：**出处不是结论**——这里只回答"按哪条规则、哪个版本、哪张表算出来的"，
          细节（阈值/失效场景/历史统计）一律进抽屉，避免卡片变成长文。 */}
      <div className="triage__rule" data-testid="triage-rule">
        <span className="triage__rule-text">
          {`规则：${item.rule_text} · ${item.rule_version} · 数据 ${item.source_table}`}
        </span>
        <Button
          type="link"
          size="small"
          className="triage__rule-open"
          data-testid="triage-rule-open"
          onClick={onOpenRule}
        >
          规则说明
        </Button>
      </div>

      <div className="triage__card-foot">
        <span className="triage__asof">数据截至 {formatAsOfDate(item.as_of)}</span>
        <Link className="triage__verify" to={verifyHref(item)}>
          复核
        </Link>
      </div>
    </li>
  );
}

/** 历史统计（`stats` 非空时）：只列事实与口径，不写"表现如何"之类评价。 */
function TriageRuleStatsView({ stats }: { stats: TriageRuleStats }) {
  return (
    <>
      <Descriptions className="triage-rules__stats-table" column={1} size="small" bordered>
        <Descriptions.Item label="信号次数">{stats.signals}</Descriptions.Item>
        <Descriptions.Item label="覆盖交易日">{stats.days}</Descriptions.Item>
        <Descriptions.Item label="日均条数">{`每日约 ${stats.per_day} 条`}</Descriptions.Item>
        <Descriptions.Item label="1 日超额中位">
          {formatSignedPct(stats.t1_excess_med)}
        </Descriptions.Item>
        <Descriptions.Item label="5 日超额中位">
          {formatSignedPct(stats.t5_excess_med)}
        </Descriptions.Item>
        <Descriptions.Item label="20 日超额中位">
          {formatSignedPct(stats.t20_excess_med)}
        </Descriptions.Item>
        <Descriptions.Item label="5 日最差 5% 分位">{formatSignedPct(stats.t5_p05)}</Descriptions.Item>
      </Descriptions>
      {stats.basis && <p className="triage-rules__basis">{`口径：${stats.basis}`}</p>}
      {/* 「历史统计不代表未来表现」是硬要求：统计再好看也是过去的事，这句不能省。 */}
      <p className="triage-rules__stats-foot">
        {`统计截至 ${formatAsOfDate(stats.as_of)}，样本 ${stats.signals} 次；历史统计不代表未来表现。`}
      </p>
    </>
  );
}

/** 抽屉正文：定义 / 阈值 / 数据来源 / 已知失效场景 / 历史统计，五段固定次序。 */
function TriageRuleDetail({ rule }: { rule: TriageRule }) {
  const thresholds = Object.entries(rule.thresholds ?? {});
  return (
    <div className="triage-rules__body">
      <section className="triage-rules__section">
        <h4 className="triage-rules__section-title">定义</h4>
        <p className="triage-rules__definition">{rule.definition}</p>
        <p className="triage-rules__rule-text">{`规则：${rule.rule_text}`}</p>
      </section>

      <section className="triage-rules__section">
        <h4 className="triage-rules__section-title">阈值</h4>
        {thresholds.length === 0 ? (
          <p className="triage-rules__muted">该规则没有可展示的阈值。</p>
        ) : (
          <Descriptions column={1} size="small" bordered>
            {thresholds.map(([key, value]) => (
              // key 是口径字段名（`day_pct`），阈值**可能为负**：原样展示，绝不取绝对值
              <Descriptions.Item key={key} label={key}>
                {String(value)}
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
      </section>

      <section className="triage-rules__section">
        <h4 className="triage-rules__section-title">数据来源</h4>
        <p className="triage-rules__rule-text">
          {rule.source_tables.length > 0 ? rule.source_tables.join("、") : "—"}
        </p>
        {rule.update_note && <p className="triage-rules__note">{rule.update_note}</p>}
      </section>

      <section className="triage-rules__section">
        <h4 className="triage-rules__section-title">已知失效场景</h4>
        {rule.failure_modes.length === 0 ? (
          <p className="triage-rules__muted">该规则暂无已知失效场景。</p>
        ) : (
          <ul className="triage-rules__failure">
            {rule.failure_modes.map((mode) => (
              <li key={mode}>{mode}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="triage-rules__section" data-testid="triage-rule-stats">
        <h4 className="triage-rules__section-title">历史统计</h4>
        {rule.stats ? (
          <TriageRuleStatsView stats={rule.stats} />
        ) : (
          // stats=null 是"样本/数据不足"，**不是 0**：只显示原因，不画空表也不补 0。
          <p className="triage-rules__muted" data-testid="triage-rule-stats-note">
            {rule.stats_note || "该规则暂不统计。"}
          </p>
        )}
      </section>
    </div>
  );
}

/**
 * 「规则说明」抽屉。
 *
 * - 数据**懒取**：`enabled: item !== null`，卡片渲染阶段不请求（进页面只该有 triage 一个请求）；
 * - 按 `key` 找当前规则（`detector` 是唯一关联字段）；找不到 / 接口挂了 / 还没回来，
 *   一律显示「规则说明暂不可用」——不许把"没取到说明"画成"没有规则"；
 * - 标题只带规则的 `label · version`；拿不到规则时退回中性标题「规则说明」。
 */
function TriageRuleDrawer({ item, onClose }: { item: TriageItem | null; onClose: () => void }) {
  const open = item !== null;
  const { data, isLoading } = useQuery({
    queryKey: ["triage", "rules"],
    queryFn: fetchTriageRules,
    enabled: open,
    // 规则说明随版本发布变化，不是秒级数据：5 分钟内不重复取。
    staleTime: 5 * 60_000,
  });

  const rule = item && data ? (data.rules.find((r) => r.key === item.detector) ?? null) : null;

  return (
    <Drawer
      title={rule ? `${rule.label} · ${rule.version}` : "规则说明"}
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
    >
      <div className="triage-rules" data-testid="triage-rule-drawer">
        {isLoading && <Skeleton active paragraph={{ rows: 5 }} />}
        {!isLoading && rule && <TriageRuleDetail rule={rule} />}
        {!isLoading && !rule && (
          // 接口挂了、还没回来（`isLoading` 已排除）、或响应里没有这条 key——三种情形
          // 对用户都是"现在看不到这条规则的说明"，故同一句话，不编一个确定的假原因。
          <p className="triage-rules__muted" data-testid="triage-rule-unavailable">
            规则说明暂不可用
          </p>
        )}
      </div>
    </Drawer>
  );
}

/** 空态/异常文案：三种"看起来都空"的情形各有各的说法，绝不复用同一句。 */
function TriageEmpty({ data }: { data: TriageResponse }) {
  if (data.state === "no_watchlist") {
    return (
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
    );
  }
  if (data.state === "no_data") {
    // 有自选但库内还没有最近完整交易日：这是"今天查不了"，不是"你没有自选"，
    // 更不是"今天没事"——所以既不出现「添加自选」，也不出现「都平静」。
    return (
      <Empty
        className="triage__empty"
        description="最近一个完整交易日的数据还没就绪，今天暂时无法体检——这不等于你的自选没有情况。"
      />
    );
  }
  if (data.quiet_count > 0) {
    return (
      // 全静默：明确语句，不是空态占位（spec §S8.1 验收判据）
      <Empty className="triage__empty" description={`今天你的自选都平静（${data.quiet_count} 只）`} />
    );
  }
  // state=ok 但既没有命中、也没有"平静"计数：自选里没有任何一只有可用行情，
  // 等于"一只都没判定"（停牌/新入库/代码对不上），不能说"都平静"。
  return (
    <Empty
      className="triage__empty"
      description="今天还没能判定你的自选（行情未覆盖），这不等于没有情况。"
    />
  );
}

function TriageView({ data }: { data: TriageResponse }) {
  const hasItems = data.items.length > 0;
  // 「数据截至」只在真的取到数据时才显示：no_data 的 as_of 是服务端当天兜底值，
  // 拿它当"数据日"会暗示"今天的行情已经算过了"。
  const hasData = data.state === "ok";
  // 抽屉挂在视图层：卡片只管"点开了哪条规则"，不各自持有一份抽屉/请求。
  const [ruleItem, setRuleItem] = useState<TriageItem | null>(null);

  return (
    <div className="triage">
      {hasData && (
        <div className="triage__meta">
          <span>数据截至 {formatAsOfDate(data.as_of)}</span>
          {data.quiet_count > 0 && (
            <span className="triage__quiet">今日平静 {data.quiet_count} 只</span>
          )}
          {data.folded > 0 && <span className="triage__folded">另有 {data.folded} 项未展示</span>}
        </div>
      )}

      {/* 缺数据留痕。no_data 状态本身已经说明"整轮体检没跑"，再列 8 条检查名只是噪声
          （那不是"部分缺失"，而是"今天全都没跑"），故只在 ok 状态下显示逐项提示。 */}
      {hasData && data.unavailable.length > 0 && (
        <Alert
          className="triage__unavailable"
          type="info"
          showIcon
          message={`${data.unavailable.length} 项检查因数据暂缺未执行：${data.unavailable.join("、")}`}
          // 只渲染后端给的**人话 label**（`所属板块在动`），绝不把 `unavailable_keys`
          // 里的机器串（`sector_move`）贴到屏幕上：那是内部标识，用户读不懂，
          // 也与本仓库"机器串不上屏"的同一条纪律冲突（见 shared/ui/date.ts）。
          description="这些检查今天没有取到数据，相关事实可能不完整——不等于「没有发生」。"
        />
      )}

      {!hasItems && <TriageEmpty data={data} />}

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
                  <TriageCard
                    item={item}
                    key={`${item.symbol}-${item.detector}`}
                    onOpenRule={() => setRuleItem(item)}
                  />
                ))}
              </ul>
            </section>
          );
        })}

      {data.disclaimer && <p className="triage__disclaimer">{data.disclaimer}</p>}

      <TriageRuleDrawer item={ruleItem} onClose={() => setRuleItem(null)} />
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
