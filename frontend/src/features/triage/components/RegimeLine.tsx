import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  REGIME_STATE_ORDER,
  fetchRegime,
  type MarketRegime,
  type RegimeForwardBucket,
} from "@/shared/api/regime";
import { formatAsOfDate } from "@/shared/ui/date";

/**
 * 市场状态一行（`/me?tab=triage` 顶部，spec §S10.3）。
 *
 * 形态：一行字 `市场状态：修复（已 7 天）｜历史同态 5 日中位 +0.8%（n=37）`，点开看
 * **转移比例**（五态各一行）与**20 日分布**（中位 / 最差 5% / n）。
 *
 * 四条纪律：
 *
 * 1. **不预测**（spec §S10.5）：只写"当前处于"与"历史上同态（之后怎么样）"。文案里不出现
 *    "即将/预测/建议"这类词——状态机天然滞后，写成前瞻句就是撒谎。
 * 2. **失败即隐身**：接口挂了/`state` 为空 → **整行不渲染**，不给"未知"占位。这行字是
 *    页面顶部的附加信息，不是"今天能不能体检"的判据；用一句"未知"占位只会让人以为
 *    "今天的市场状态是未知"，那比不显示更糟（与 `TriagePanel` 顶部"不静默把没查说成没事"
 *    同一条纪律：这里的答案是"这次没有这一行"，而不是"状态未知"）。
 * 3. **缺数不补 0**：`n < 30` 时后端给 `median_pct = null` → 只写样本量并明示"样本不足"；
 *    转移概率缺某个去向 → 该行写 `—`（不是 0%）。
 * 4. **五态永远五行**：按 `REGIME_STATE_ORDER`（spec §S10.2 的判定次序）排，不按概率
 *    重排——排序会让人以为那是"最可能的下一步"，而这里只是"历史上曾经的比例"。
 *
 * 与 `/market` 无关：本组件只挂在体检 tab，**不动** `/market` 的任何读面。
 */

/** 百分比（带符号、1 位小数）：`+0.8%` / `-3.1%`；缺值 `—`（不是 0%）。 */
function signedPct(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** 概率（0–1 → 百分数，1 位小数）；缺值 `—`（`0` 是合法值，必须显式成 `0.0%`）。 */
function probabilityText(value: number | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

/** 样本量文案：`n=37`；缺值 `n —`（后端没给样本数时也不编一个 0）。 */
function sampleText(n: number | null | undefined): string {
  return typeof n === "number" && Number.isFinite(n) ? `n=${n}` : "n —";
}

/**
 * 摘要行的第二段："历史同态 5 日中位 +0.8%（n=37）"。
 *
 * 三种情形各有各的说法（**不许合并**）：
 * - 有中位 → `历史同态 5 日中位 +0.8%（n=37）`；
 * - 有样本但 `median_pct = null`（`n < 30`，spec §S10.4）→ `历史同态 5 日样本 n=12（不足 30，不给分布）`；
 * - 后端没给 `5d` 这一档 / 样本是 0 → **这一段整体不写**（不写"0 条样本"这种伪信息）。
 */
function forwardSummary(bucket: RegimeForwardBucket | null | undefined): string | null {
  if (!bucket || bucket.n === null || bucket.n <= 0) return null;
  if (bucket.median_pct === null) {
    return `历史同态 5 日样本 ${sampleText(bucket.n)}（不足 30，不给分布）`;
  }
  return `历史同态 5 日中位 ${signedPct(bucket.median_pct)}（${sampleText(bucket.n)}）`;
}

/** 20 日分布的一格（`dt` 标签 + `dd` 数值；缺值走 `—`）。 */
function ForwardCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="triage__regime-cell">
      <dt className="triage__regime-cell-label">{label}</dt>
      <dd className="triage__regime-cell-value num">{value}</dd>
    </div>
  );
}

/** 展开区：转移比例（五态各一行）+ 20 日分布。 */
function RegimeDetail({ regime }: { regime: MarketRegime }) {
  const since = regime.since ? formatAsOfDate(regime.since) : null;
  const duration =
    typeof regime.duration_days === "number" && regime.duration_days >= 1
      ? `已 ${regime.duration_days} 天`
      : null;
  const currentMeta = [since ? `自 ${since} 起` : null, duration].filter(Boolean).join(" · ");
  const bucket = regime.forward["20d"] ?? null;
  const allTransitionMissing = REGIME_STATE_ORDER.every(
    (state) => typeof regime.transition[state] !== "number"
  );

  return (
    <div className="triage__regime-detail" data-testid="regime-detail">
      <p className="triage__regime-current">
        {currentMeta ? `当前处于「${regime.state}」，${currentMeta}` : `当前处于「${regime.state}」`}
      </p>

      <section className="triage__regime-section" data-testid="regime-transition">
        <h4 className="triage__regime-title">下一日的状态分布（历史上从同态出发的比例）</h4>
        {allTransitionMissing ? (
          // 该状态历史上还没有足够的出场样本：五态仍然逐行列出（形态稳定），但整块给一句原因，
          // 免得读者把五个 `—` 读成"概率都是 0"。
          <p className="triage__regime-muted" data-testid="regime-transition-empty">
            暂无可展示的转移比例（历史上该状态的出场样本不足）。
          </p>
        ) : null}
        <dl className="triage__regime-rows">
          {REGIME_STATE_ORDER.map((state) => (
            <div className="triage__regime-row" key={state} data-testid="regime-transition-row">
              <dt className="triage__regime-row-label">{state}</dt>
              <dd className="triage__regime-row-value num">
                {probabilityText(regime.transition[state])}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="triage__regime-section" data-testid="regime-forward-20d">
        <h4 className="triage__regime-title">历史上同态之后 20 个交易日的全市场中位收益</h4>
        {bucket && bucket.n !== null && bucket.n > 0 ? (
          bucket.median_pct === null ? (
            // `n < 30`：只给样本量，不给分布（spec §S10.4）。绝不补 0。
            <p className="triage__regime-muted" data-testid="regime-forward-insufficient">
              {`样本 ${sampleText(bucket.n)}（不足 30，不给分布）`}
            </p>
          ) : (
            <dl className="triage__regime-cells">
              <ForwardCell label="中位" value={signedPct(bucket.median_pct)} />
              <ForwardCell label="最差 5%" value={signedPct(bucket.p05_pct)} />
              <ForwardCell label="样本" value={sampleText(bucket.n)} />
            </dl>
          )
        ) : (
          <p className="triage__regime-muted" data-testid="regime-forward-missing">
            暂无同态样本。
          </p>
        )}
      </section>

      <p className="triage__regime-foot">
        以上只描述历史上同态之后的分布，不代表本次的结果；状态由固定规则判定，天然滞后。
      </p>
    </div>
  );
}

export function RegimeLine() {
  const [open, setOpen] = useState(false);
  const { data, isError } = useQuery({
    queryKey: ["market", "regime"],
    queryFn: fetchRegime,
    // 状态是日级产物：5 分钟内不必重复取（与 triage 的 30s 分开，各自按自己的节奏）。
    staleTime: 5 * 60_000,
    // 这一行失败就隐身：不重试，避免顶部挂一个一直转的请求。
    retry: false,
  });

  // 接口失败 / 还没回来 / `state` 为空 → 整行不渲染（不给"未知"占位，见文件头第 2 条）。
  if (isError || !data) return null;
  const state = (data.state ?? "").trim();
  if (!state) return null;

  const duration =
    typeof data.duration_days === "number" && data.duration_days >= 1
      ? `（已 ${data.duration_days} 天）`
      : "";
  const summaryParts = [`市场状态：${state}${duration}`, forwardSummary(data.forward["5d"])].filter(
    (part): part is string => typeof part === "string" && part.length > 0
  );

  return (
    <div className="triage__regime" data-testid="regime-line">
      <button
        type="button"
        className="triage__regime-toggle"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        data-testid="regime-toggle"
      >
        <span className="triage__regime-summary" data-testid="regime-summary">
          {summaryParts.join("｜")}
        </span>
        <span className="triage__regime-cue">{open ? "收起" : "展开"}</span>
      </button>
      {open && <RegimeDetail regime={data} />}
    </div>
  );
}
