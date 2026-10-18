import { useState, type ReactNode } from "react";
import { Alert, Button, Empty, Skeleton, Tag, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchJournalEntries,
  fetchJournalStats,
  journalExportUrl,
  type JournalEntry,
  type JournalStats,
} from "@/shared/api/journal";
import { formatAsOfDate } from "@/shared/ui/date";
import { EntryForm } from "./EntryForm";
import { ReviewModal } from "./ReviewModal";
import { VERDICT_META, VERDICT_ORDER, verdictLabel } from "../verdicts";
import { KIND_META, KIND_ORDER, kindCountsOutcome } from "../kinds";
import { POSITION_SIZE_META, POSITION_SIZE_ORDER, positionSizeLabel, positionSizeOf } from "../sizes";
import {
  GROSS_FALLBACK_LABEL,
  formatBrier,
  formatCalibrationRate,
  formatConsistency,
  formatShare,
  formatShareOrSample,
  formatSignedPct,
  hasCalibrationCurve,
  isEvaluationReady,
  netBasisLabel,
  netExcessOf,
  pickBasisValue,
} from "../metrics";
import "./JournalPanel.css";

const { Text } = Typography;

/**
 * `/me?tab=journal`「决策日志」：三块 —— **记一笔 / 待复盘 / 已复盘（含统计）**。
 *
 * 三条纪律（spec §S9.1/§S9.4/§S9.5）：
 * 1. **只记录，不给判断**：页面不改写用户写的 thesis、不评价结果好坏、不出现任何
 *    判断类或带倾向的文案；结果与超额是后端算的，原样上屏（正文只说"结果/基准/超额"）。
 * 2. **状态以后端为准**：三份列表分别按 `status=open|due|reviewed` 取，页面**不**用
 *    `due_on` 和本地时钟自己算"到期了没"——各算一份必然漂移，跨时区/跨天就是错。
 * 3. **不可篡改**：条目创建后没有编辑/删除入口（接口层也没有 PATCH/PUT/DELETE），
 *    已复盘条目同样只有"看"，没有"再评一次"。这不是漏做。
 *
 * 统计卡的口径：`reviewed_n < 5` 时**只说"样本不足"**、不给任何占比（4 条里的 50%
 * 是噪声，读起来却像结论）；`null` 一律显示 `—` 而不是 0。
 *
 * S9.7 的三处口径（都走 `metrics.ts` 的单一实现）：
 * - **净成本**（§S9.7.2）：收益/超额默认净口径，毛只在括注里出现；净字段缺省时回退毛
 *   并明确标"净字段缺失"，绝不把毛当净；
 * - **`pass` 不计收益**（§S9.7.6）：「我决定不动」的条目收益列是 `—（不计收益）`，
 *   不是 0（0 会被读成"这次收益正好是 0"）；
 * - **比例带区间**（§S9.7.7）：有 `share_ci` 时写 `4/6（95% 区间 30%–90%）`，
 *   `n < 5` 只写"样本不足"。
 *
 * Phase B 的四处（同样只做加法，字段缺省时退化）：
 * - **仓位**（§S9.7.1）：列表行显示 `position_size` 标签；统计卡加 `by_size` 分栏与
 *   `avg_weighted_excess_pct`（`none` 排除，缺省不显示而非补 0）；
 * - **信心校准**（§S9.7.5）：独立校准卡，每档 `n >= 10` 才给命中率，总 `n < 20` 只显示
 *   `calibration_note`；
 * - **不可复盘**（§S9.7.8）：`reviewable === false` 的到期条目**没有**「复盘」按钮；
 * - **导出/隐私**（§S9.7.9）：页底「导出我的日志」链接 + 「不提供单条删除」原文告知。
 *
 * S9 深度优化（契约 §3，同样只做加法，字段缺省时整块退化）：
 * - **`by_kind_median`**：统计卡的归档标签格在条数旁给该类净超额中位（`null` → `—`）；
 * - **归因自洽**：统计卡新增一行（`n < 5` 只写「样本不足」，末尾原样展示后端 `note`）；
 * - **结果未就绪**：`evaluation` 为空 / 结果三要素缺数 / `missing_reason` 记着结果缺数时
 *   「复盘」**禁用** +
 *   「重新检查」（重拉列表查询），不给"先记归因"的绕过入口。
 *
 * 分块（不新增路由，挂在 `/me` 的 tab 内）：
 * - 顶部一行：「待复盘 N 条」（N = `due` 条数，页面角标语义，v1 不做推送）；
 * - 「记一笔」表单；
 * - 「待复盘」= `due`（每条带"复盘"按钮）+ 其下的「未到期（M）」= `open`；
 * - 「已复盘」= `reviewed`（每条带归因标签、没有编辑/删除入口）+ 统计卡 + 校准卡；
 * - 页脚：导出链接、隐私告知（S9.7.9）、固定免责（逐字照 spec §S9.5）。
 */

/** thesis 的第一行（列表行只展示一行，全文留在条目里）。 */
function thesisFirstLine(thesis: string): string {
  return thesis.split(/\r?\n/)[0] ?? "";
}

/**
 * 列表行的一行快照摘要：优先用后端拼好的 `snapshot_summary`；
 * 没有时按 spec §S9.3 的固定字段名自己拼一行（只拼**已有的**值，缺数不补 0）。
 */
function snapshotLine(entry: JournalEntry): string {
  if (entry.snapshot_summary) return entry.snapshot_summary;
  const snapshot = entry.snapshot;
  if (!snapshot) return "创建时没有取到快照";
  const parts: string[] = [];
  if (snapshot.price !== null && snapshot.price !== undefined) {
    parts.push(`价格 ${snapshot.price.toFixed(2)} 元`);
  }
  if (snapshot.pct_chg !== null && snapshot.pct_chg !== undefined) {
    parts.push(`当日 ${formatSignedPct(snapshot.pct_chg)}`);
  }
  if (snapshot.sentiment_percentile !== null && snapshot.sentiment_percentile !== undefined) {
    parts.push(`情绪分位 ${Math.round(snapshot.sentiment_percentile)}`);
  }
  if (snapshot.events && snapshot.events.length > 0) {
    parts.push(`当日事件 ${snapshot.events.length} 条`);
  }
  return parts.length > 0 ? parts.join(" · ") : "创建时没有取到快照";
}

/** 标的列：`null` = 市场级判断。 */
function symbolText(entry: JournalEntry): string {
  return entry.symbol ?? "市场";
}

/**
 * 列表行的**收益列**（口径单一入口：`metrics.ts`）。
 *
 * - `pass`（我决定不动）：`—（不计收益）`——`pass` 计入判断、不计入收益（spec §S9.7.6），
 *   这里**不能**显示 0（0 会被读成"这次收益正好是 0"，是另一种假话）；
 * - 其余已复盘条目：默认净超额（`net_excess_pct`），净字段缺省时回退毛超额并标注
 *   「净字段缺失，显示毛口径」；净口径下用小字标「毛/净（成本 12bp）」；
 * - 未复盘（`open`/`due`）：不显示收益列——那时还没有到期结果（由复盘弹窗展示）。
 */
function EntryOutcome({ entry }: { entry: JournalEntry }) {
  if (!kindCountsOutcome(entry.kind)) {
    return (
      <span className="journal-entry__outcome" data-testid="journal-entry-outcome">
        <span className="journal-entry__excess num">{"收益 —（不计收益）"}</span>
      </span>
    );
  }
  if (entry.status !== "reviewed") return null;
  const evaluation = entry.evaluation ?? entry.review ?? null;
  const excess = netExcessOf(evaluation);
  return (
    <span className="journal-entry__outcome" data-testid="journal-entry-outcome">
      <span className="journal-entry__excess num" data-testid="journal-reviewed-excess">
        {`超额 ${formatSignedPct(excess.value)}`}
      </span>
      <span className="journal-entry__basis">
        {excess.basis === "net"
          ? netBasisLabel(null)
          : excess.basis === "gross"
            ? GROSS_FALLBACK_LABEL
            : null}
      </span>
    </span>
  );
}

/**
 * 一条记录（`due` / `open` / `reviewed` 共用骨架，差别只在标签与操作区）。
 *
 * S9.7 的两处展示纪律：
 * - **仓位标签**（§S9.7.1）：只在后端真给了 `position_size` 时显示——缺字段时**不补**
 *   「只观察」（那会把"没记录"说成"没下单"）；
 * - **不可复盘**（§S9.7.8）：`reviewable === false` 时**没有**「复盘」按钮，改成一句
 *   「不可复盘（失效条件不可观测）」。缺字段（`undefined`）按可复盘处理，旧条目照旧能复盘。
 *
 * 深度优化的第三处（契约 §3）：到期的条目如果**结果未就绪**（`evaluation` 为空、
 * 结果三要素缺数、或 `missing_reason` 记着结果缺数），「复盘」按钮**禁用**并说明原因，
 * 旁边给一个「重新检查」（重新拉列表/详情）。这里**不给**"先记归因"这种绕过入口：
 * 归因必须建立在看得见的结果上，否则记录的就是一句没依据的自我评价。
 */
function EntryRow({
  entry,
  onReview,
  onRecheck,
}: {
  entry: JournalEntry;
  onReview?: (entry: JournalEntry) => void;
  /** 「重新检查」：重新触发列表/详情查询（父级给，行内不自己造数据源）。 */
  onRecheck?: () => void;
}) {
  const reviewed = entry.status === "reviewed";
  const review = entry.review;
  const size = positionSizeOf(entry.position_size);
  const reviewable = entry.reviewable !== false;
  // 结果未就绪（契约 §3）：缺 `evaluation` / 带 `missing_reason` / 结果三要素缺数。
  const ready = isEvaluationReady(entry.evaluation);
  const testId =
    entry.status === "reviewed"
      ? "journal-reviewed-item"
      : entry.status === "due"
        ? "journal-due-item"
        : "journal-open-item";

  return (
    <li className="journal-entry" data-testid={testId}>
      <div className="journal-entry__head">
        {reviewed ? (
          <Tag className="journal-entry__verdict" data-testid="journal-reviewed-verdict">
            {review ? verdictLabel(review.verdict) : "已复盘"}
          </Tag>
        ) : (
          <Tag
            className={
              entry.status === "due" ? "journal-entry__status--due" : "journal-entry__status--open"
            }
          >
            {entry.status === "due" ? "待复盘" : "未到期"}
          </Tag>
        )}
        <span className="journal-entry__symbol num">{symbolText(entry)}</span>
        {size && (
          <Tag className="journal-entry__size" data-testid="journal-entry-size">
            {`仓位：${positionSizeLabel(size)}`}
          </Tag>
        )}
        <span className="journal-entry__due-on">{`到期 ${formatAsOfDate(entry.due_on)}`}</span>
        <EntryOutcome entry={entry} />
      </div>

      <p className="journal-entry__thesis">{thesisFirstLine(entry.thesis)}</p>
      <p className="journal-entry__snapshot">{`快照：${snapshotLine(entry)}`}</p>

      {reviewed && review?.note && (
        <p className="journal-entry__note">{`你的备注：${review.note}`}</p>
      )}

      {onReview && entry.status === "due" && (
        <div className="journal-entry__actions">
          {reviewable ? (
            <>
              <Button
                type="primary"
                size="small"
                data-testid="journal-review-open"
                disabled={!ready}
                onClick={() => onReview(entry)}
              >
                复盘
              </Button>
              {!ready && (
                <>
                  <span className="journal-entry__not-ready" data-testid="journal-review-not-ready">
                    结果未就绪（缺行情/基准），数据到位后即可复盘
                  </span>
                  <Button size="small" data-testid="journal-recheck" onClick={onRecheck}>
                    重新检查
                  </Button>
                </>
              )}
            </>
          ) : (
            <span className="journal-entry__not-reviewable" data-testid="journal-not-reviewable">
              不可复盘（失效条件不可观测）
            </span>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * 统计卡（spec §S9.4/§S9.5 + §S9.7.2/§S9.7.6/§S9.7.7）。
 *
 * - `reviewed_n < 5` → 只写「样本不足（<5 条）」，**不显示任何占比**；
 * - 样本够 → 五类归因各自的条数与占比、按归档标签的条数（`by_kind`）、平均超额（默认净）、
 *   没按计划执行的占比；比例有 `share_ci` 时写 `4/6（95% 区间 30%–90%）`，
 *   该比例 `n < 5` 时写"样本不足"（不给点估计）；
 * - 底部原样展示 `multiple_comparison_note`（后端给的那句话，前端不改写）；
 * - 任何 `null` → `—`（"没有数据"不是"0"）。
 *
 * 深度优化追加（契约 §3，全部只做加法）：
 * - `by_kind` 每格在条数旁显示该类 `by_kind_median[kind].median_net_excess_pct`
 *   （`null`/缺省 → `—`；`0.00%` 与 `—` 是两件事）；
 * - 「归因自洽」一行：`8/13（95% 区间 36%–82%）`，`consistency.n < 5` 只写「样本不足」，
 *   末尾**原样**展示 `consistency.note`。
 */
function StatsCard({ stats }: { stats: JournalStats }) {
  const insufficient = stats.reviewed_n < 5;
  const counts = stats.by_verdict ?? {};
  const reviewedN = stats.reviewed_n;
  const byKind = stats.by_kind ?? null;
  const byKindMedian = stats.by_kind_median ?? null;
  const consistency = stats.consistency ?? null;
  const consistencyText = formatConsistency(consistency);
  const bySize = stats.by_size ?? null;
  const shareCi = stats.share_ci ?? null;
  // 平均超额默认净口径；Phase A 未上线（缺 `avg_net_excess_pct`）时回退毛并标注。
  const avgExcess = pickBasisValue(stats.avg_excess_pct, stats.avg_net_excess_pct);
  // 加权超额（spec §S9.7.1，`none` 排除）：**只在后端给了数字时显示**——
  // 缺省补 0 会把"没有可加权的样本"说成"加权超额正好是 0"。
  const weighted = stats.avg_weighted_excess_pct;
  const hasWeighted = typeof weighted === "number" && Number.isFinite(weighted);

  return (
    <div className="journal-stats" data-testid="journal-stats">
      {/* 卡片里不再写一遍"统计"：外层 section 已有同名标题，重复一次只是噪声。
          这里只放事实口径（总条数 / 已复盘条数）。 */}
      <div className="journal-stats__head">
        <span className="journal-stats__total">{`共 ${stats.n} 条 · 已复盘 ${reviewedN} 条`}</span>
      </div>

      {insufficient ? (
        <p className="journal-stats__insufficient" data-testid="journal-stats-insufficient">
          {"样本不足（<5 条）"}
        </p>
      ) : (
        <>
          <ul className="journal-stats__list" data-testid="journal-stats-breakdown">
            {VERDICT_ORDER.map((key) => {
              const count = counts[key] ?? 0;
              return (
                <li className="journal-stats__row" key={key}>
                  <span className="journal-stats__label">{VERDICT_META[key].label}</span>
                  <span className="journal-stats__count num">{count}</span>
                  <span className="journal-stats__share num">
                    {formatShare(reviewedN > 0 ? count / reviewedN : null)}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="journal-stats__foot">
            <span>
              平均超额
              <span className="journal-stats__basis">
                {avgExcess.basis === "net" ? "（净）" : avgExcess.basis === "gross" ? "（毛口径）" : ""}
              </span>
              <span className="num">{` ${formatSignedPct(avgExcess.value)}`}</span>
            </span>
            {hasWeighted && (
              <span data-testid="journal-stats-weighted-excess">
                加权超额
                <span className="journal-stats__basis">（按仓位加权，只观察不计）</span>
                <span className="num">{` ${formatSignedPct(weighted)}`}</span>
              </span>
            )}
            <span data-testid="journal-stats-discipline">
              没按计划执行占比
              <span className="num">
                {` ${formatShareOrSample(shareCi?.discipline_break, stats.discipline_break_share)}`}
              </span>
            </span>
            {shareCi?.win && (
              <span data-testid="journal-stats-win">
                净超额为正
                <span className="num">{` ${formatShareOrSample(shareCi.win, null)}`}</span>
              </span>
            )}
          </div>
          {avgExcess.basis === "gross" && (
            <p className="journal-stats__basis-note" data-testid="journal-stats-basis-note">
              {GROSS_FALLBACK_LABEL}
            </p>
          )}
        </>
      )}

      {byKind && (
        <div className="journal-stats__kinds" data-testid="journal-stats-by-kind">
          {KIND_ORDER.map((key) => {
            // 中位净超额（契约 §3）：`by_kind_median` 整块缺省（旧后端）时**不渲染**这一列，
            // 有这块时每类都要占位——`null`/缺省显示 `—`，绝不补 0。
            const median = byKindMedian?.[key]?.median_net_excess_pct;
            return (
              <span className="journal-stats__kind" key={key}>
                <span className="journal-stats__kind-label">{KIND_META[key].label}</span>
                <span className="journal-stats__kind-count num">{byKind[key] ?? 0}</span>
                {byKindMedian && (
                  <span
                    className="journal-stats__kind-median num"
                    data-testid="journal-stats-kind-median"
                  >
                    {formatSignedPct(median)}
                  </span>
                )}
              </span>
            );
          })}
          <span className="journal-stats__kind-hint">
            「放弃」= 我决定不动：计入判断，不计入收益
          </span>
        </div>
      )}

      {/* 归因自洽率（契约 §2）：自己一条 n，故不受 `reviewed_n < 5` 的整块隐藏影响——
          它的 `n < 5` 时只写「样本不足」（不给比例、不给区间）。 */}
      {consistency && consistencyText && (
        <div className="journal-stats__consistency" data-testid="journal-stats-consistency">
          <span className="journal-stats__label">归因自洽</span>
          <span className="journal-stats__count num" data-testid="journal-stats-consistency-value">
            {consistencyText}
          </span>
          {consistency.note ? (
            <span
              className="journal-stats__consistency-note"
              data-testid="journal-stats-consistency-note"
            >
              {consistency.note}
            </span>
          ) : null}
        </div>
      )}

      {/* 仓位分栏（spec §S9.7.1）：只是**条数事实**，故与 `by_kind` 一样不受 `reviewed_n < 5` 限制；
          缺字段（Phase B 后端未上线）时整块不显示。 */}
      {bySize && (
        <div className="journal-stats__kinds" data-testid="journal-stats-by-size">
          {POSITION_SIZE_ORDER.map((key) => (
            <span className="journal-stats__kind" key={key}>
              <span className="journal-stats__kind-label">{POSITION_SIZE_META[key].label}</span>
              <span className="journal-stats__kind-count num">{bySize[key] ?? 0}</span>
            </span>
          ))}
          <span className="journal-stats__kind-hint">
            「只观察」= 只记判断：计入判断，不计入收益
          </span>
        </div>
      )}

      {stats.exclusion_note && (
        <p className="journal-stats__note" data-testid="journal-stats-exclusion-note">
          {stats.exclusion_note}
        </p>
      )}

      {stats.multiple_comparison_note && (
        <p className="journal-stats__note" data-testid="journal-stats-multiple-note">
          {stats.multiple_comparison_note}
        </p>
      )}
    </div>
  );
}

/**
 * 校准卡（spec §S9.7.5）：**「你的信心与实际结果的对照」**（固定文案，不评价用户）。
 *
 * 三条展示纪律：
 * - 总样本 `n < 20` 或后端没给 `calibration`（给的是 `null`）→ **只显示**
 *   `calibration_note`（后端给的原因）；两者都缺 → 整块不渲染（不补 0、不虚构曲线）；
 * - 每档 `n >= 10` 才显示"实际命中率"，否则该档写"样本不足（n=4）"——不给点估计；
 * - `brier` 有就给一位小数的三位值，没有就不显示这一项。
 */
function CalibrationCard({ stats }: { stats: JournalStats }) {
  const calibration = stats.calibration ?? null;
  const note = stats.calibration_note ?? null;
  const curve = hasCalibrationCurve(calibration) ? calibration : null;
  const bins = (curve?.bins ?? []).slice().sort((a, b) => a.confidence - b.confidence);
  const brier = formatBrier(curve?.brier);

  // 后端 Phase B 未上线：两个字段都没有 → 不画卡（画一张空卡等于暗示"你的校准是 0"）。
  if (!curve && !note) return null;

  return (
    <div className="journal-calibration" data-testid="journal-calibration">
      <p className="journal-calibration__lead">你的信心与实际结果的对照</p>
      {curve && bins.length > 0 ? (
        <>
          <ul className="journal-calibration__list">
            {bins.map((bin) => (
              <li
                className="journal-calibration__row"
                key={bin.confidence}
                data-testid="journal-calibration-bin"
              >
                <span className="journal-calibration__confidence">{`信心 ${bin.confidence}`}</span>
                <span className="journal-calibration__rate num">
                  {formatCalibrationRate(bin)}
                </span>
                <span className="journal-calibration__n num">{`n=${bin.n}`}</span>
              </li>
            ))}
          </ul>
          <p className="journal-calibration__foot" data-testid="journal-calibration-foot">
            {`共 ${curve.n} 条已复盘且填了信心`}
            {brier ? ` · Brier ${brier}` : ""}
          </p>
        </>
      ) : (
        <p className="journal-calibration__note" data-testid="journal-calibration-note">
          {note ?? "样本不足（已复盘且填了信心的样本太少）"}
        </p>
      )}
    </div>
  );
}

/** 一个状态分组的通用外壳：加载/异常各说各的，绝不互相顶替（空态由 children 自己给）。 */
function JournalSection({
  title,
  count,
  isLoading,
  isError,
  children,
  testId,
}: {
  title: string;
  count?: number;
  isLoading: boolean;
  isError: boolean;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section className="journal-section" data-testid={testId}>
      <h2 className="journal-section__title">
        {title}
        {typeof count === "number" && <span className="journal-section__count">{count}</span>}
      </h2>
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 2 }} />
      ) : isError ? (
        // 接口挂了 ≠ 没有记录：两种"看起来都空"的情形必须分开说。
        <Alert
          type="warning"
          showIcon
          message={`${title}暂不可用`}
          description="接口暂时没能返回数据，请稍后刷新重试；这不代表你没有记录。"
        />
      ) : (
        children
      )}
    </section>
  );
}

export function JournalPanel() {
  const queryClient = useQueryClient();
  const [reviewing, setReviewing] = useState<JournalEntry | null>(null);

  const openQuery = useQuery({
    queryKey: ["journal", "list", "open"],
    queryFn: () => fetchJournalEntries("open"),
  });
  const dueQuery = useQuery({
    queryKey: ["journal", "list", "due"],
    queryFn: () => fetchJournalEntries("due"),
  });
  const reviewedQuery = useQuery({
    queryKey: ["journal", "list", "reviewed"],
    queryFn: () => fetchJournalEntries("reviewed"),
  });
  const statsQuery = useQuery({ queryKey: ["journal", "stats"], queryFn: fetchJournalStats });

  const openItems = openQuery.data?.items ?? [];
  const dueItems = dueQuery.data?.items ?? [];
  const reviewedItems = reviewedQuery.data?.items ?? [];

  // 创建/复盘成功后统一刷新：面板只认这条数据路径，前端不往列表里"手动塞一条"
  // （塞进去的那条没有后端算的快照与到期日，第二天就和真数据不一致）。
  const refreshAll = () => {
    queryClient.invalidateQueries({ queryKey: ["journal"] });
  };

  // 「重新检查」（契约 §3）：结果未就绪时**只重拉数据**，不替用户补任何东西。
  // 拉的是列表查询（到期/未到期两份都在用同一条读面）；后端把行情/基准补齐后，
  // `evaluation` 自然带上，按钮随之可用。
  const recheckEntries = () => {
    void Promise.all([dueQuery.refetch(), openQuery.refetch()]);
  };

  return (
    <div className="journal" data-testid="journal-panel">
      <div className="journal__meta">
        <span className="journal__due-count" data-testid="journal-due-count">
          {dueQuery.isLoading ? "待复盘 …" : `待复盘 ${dueItems.length} 条`}
        </span>
        <Text className="journal__hint">
          写下你自己的判断；到期时系统自动附上结果，由你归类错在哪。
        </Text>
      </div>

      <section className="journal-section" data-testid="journal-form-section">
        <h2 className="journal-section__title">记一笔</h2>
        <EntryForm onCreated={refreshAll} />
      </section>

      <JournalSection
        title="待复盘"
        count={dueItems.length}
        isLoading={dueQuery.isLoading}
        isError={dueQuery.isError}
        testId="journal-due-section"
      >
        {dueItems.length === 0 ? (
          <Empty
            className="journal__empty"
            description="还没有到期的记录。到期那天的记录会出现在这里，等你写下归因。"
          />
        ) : (
          <ul className="journal__list">
            {dueItems.map((entry) => (
              <EntryRow
                key={entry.id}
                entry={entry}
                onReview={setReviewing}
                onRecheck={recheckEntries}
              />
            ))}
          </ul>
        )}

        {openItems.length > 0 && (
          <div className="journal__open" data-testid="journal-open-section">
            <h3 className="journal__open-title">{`未到期（${openItems.length}）`}</h3>
            <ul className="journal__list">
              {openItems.map((entry) => (
                <EntryRow key={entry.id} entry={entry} />
              ))}
            </ul>
          </div>
        )}
      </JournalSection>

      <JournalSection
        title="已复盘"
        count={reviewedItems.length}
        isLoading={reviewedQuery.isLoading}
        isError={reviewedQuery.isError}
        testId="journal-reviewed-section"
      >
        {reviewedItems.length === 0 ? (
          <Empty
            className="journal__empty"
            description="还没有复盘的记录。到期条目写完归因后会出现在这里。"
          />
        ) : (
          <ul className="journal__list">
            {reviewedItems.map((entry) => (
              <EntryRow key={entry.id} entry={entry} />
            ))}
          </ul>
        )}
      </JournalSection>

      <section className="journal-section" data-testid="journal-stats-section">
        <h2 className="journal-section__title">统计</h2>
        {statsQuery.isLoading ? (
          <Skeleton active paragraph={{ rows: 3 }} />
        ) : statsQuery.isError || !statsQuery.data ? (
          <Alert
            type="warning"
            showIcon
            message="统计暂不可用"
            description="接口暂时没能返回统计数据，请稍后刷新重试；这不代表你的记录有变化。"
          />
        ) : (
          <>
            <StatsCard stats={statsQuery.data} />
            <CalibrationCard stats={statsQuery.data} />
          </>
        )}
      </section>

      {/* 导出与隐私（spec §S9.7.9）：链接用后端给的 `export_hint`，缺省拼契约端点；
          隐私那句是**原文告知**（逐字上屏，不翻译、不改写）。 */}
      <div className="journal__footer" data-testid="journal-footer">
        <a
          className="journal__export"
          data-testid="journal-export"
          href={journalExportUrl(statsQuery.data?.export_hint)}
        >
          导出我的日志
        </a>
        <span className="journal__privacy" data-testid="journal-privacy">
          日志不提供单条删除，以保证复盘记录不可篡改；如需清除请删除账号
        </span>
      </div>

      {/* spec §S9.5 的固定免责，逐字上屏；页面不另写一份"投资建议"类文案。 */}
      <p className="journal__disclaimer">
        决策日志记录的是你自己的判断，不构成投资建议；结果统计不代表未来表现。
      </p>

      <ReviewModal entry={reviewing} onClose={() => setReviewing(null)} />
    </div>
  );
}
