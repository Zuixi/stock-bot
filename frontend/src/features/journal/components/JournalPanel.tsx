import { useState, type ReactNode } from "react";
import { Alert, Button, Empty, Skeleton, Tag, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchJournalEntries,
  fetchJournalStats,
  type JournalEntry,
  type JournalStats,
} from "@/shared/api/journal";
import { formatAsOfDate } from "@/shared/ui/date";
import { EntryForm } from "./EntryForm";
import { ReviewModal } from "./ReviewModal";
import { VERDICT_META, VERDICT_ORDER, verdictLabel } from "../verdicts";
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
 * 分块（不新增路由，挂在 `/me` 的 tab 内）：
 * - 顶部一行：「待复盘 N 条」（N = `due` 条数，页面角标语义，v1 不做推送）；
 * - 「记一笔」表单；
 * - 「待复盘」= `due`（每条带"复盘"按钮）+ 其下的「未到期（M）」= `open`；
 * - 「已复盘」= `reviewed`（每条带归因标签、没有编辑/删除入口）+ 统计卡；
 * - 页脚固定免责（逐字照 spec §S9.5）。
 */

/** 百分比口径：保留符号（可以为负），取不到时 `—` 而不是 0。 */
function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** 占比（后端给 0–1 的小数）：`0.375` → `37.5%`；`null` → `—`。 */
function formatShare(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

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

/** 一条记录（`due` / `open` / `reviewed` 共用骨架，差别只在标签与操作区）。 */
function EntryRow({
  entry,
  onReview,
}: {
  entry: JournalEntry;
  onReview?: (entry: JournalEntry) => void;
}) {
  const reviewed = entry.status === "reviewed";
  const review = entry.review;
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
        <span className="journal-entry__due-on">{`到期 ${formatAsOfDate(entry.due_on)}`}</span>
        {reviewed && (
          <span className="journal-entry__excess num" data-testid="journal-reviewed-excess">
            {`超额 ${formatSignedPct(review?.excess_pct)}`}
          </span>
        )}
      </div>

      <p className="journal-entry__thesis">{thesisFirstLine(entry.thesis)}</p>
      <p className="journal-entry__snapshot">{`快照：${snapshotLine(entry)}`}</p>

      {reviewed && review?.note && (
        <p className="journal-entry__note">{`你的备注：${review.note}`}</p>
      )}

      {onReview && entry.status === "due" && (
        <div className="journal-entry__actions">
          <Button
            type="primary"
            size="small"
            data-testid="journal-review-open"
            onClick={() => onReview(entry)}
          >
            复盘
          </Button>
        </div>
      )}
    </li>
  );
}

/**
 * 统计卡（spec §S9.4/§S9.5）。
 *
 * - `reviewed_n < 5` → 只写「样本不足（<5 条）」，**不显示任何占比**；
 * - 样本够 → 五类归因各自的条数与占比、平均超额、没按计划执行的占比；
 * - 任何 `null` → `—`（"没有数据"不是"0"）。
 */
function StatsCard({ stats }: { stats: JournalStats }) {
  const insufficient = stats.reviewed_n < 5;
  const counts = stats.by_verdict ?? {};
  const reviewedN = stats.reviewed_n;

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
              <span className="num">{` ${formatSignedPct(stats.avg_excess_pct)}`}</span>
            </span>
            <span>
              没按计划执行占比
              <span className="num">{` ${formatShare(stats.discipline_break_share)}`}</span>
            </span>
          </div>
        </>
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
              <EntryRow key={entry.id} entry={entry} onReview={setReviewing} />
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
          <StatsCard stats={statsQuery.data} />
        )}
      </section>

      {/* spec §S9.5 的固定免责，逐字上屏；页面不另写一份"投资建议"类文案。 */}
      <p className="journal__disclaimer">
        决策日志记录的是你自己的判断，不构成投资建议；结果统计不代表未来表现。
      </p>

      <ReviewModal entry={reviewing} onClose={() => setReviewing(null)} />
    </div>
  );
}
