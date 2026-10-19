import { Typography } from "antd";
import type { JournalEntry, JournalPeerHistory, JournalPeerHistorySelf } from "@/shared/api/journal";
import { verdictLabel } from "@/features/journal/verdicts";

const { Text } = Typography;

/**
 * 「同类情形」卡（spec §S9.6.5）：**创建成功后立刻**把两段历史还给用户。
 *
 * 为什么这张卡必须存在：S9 前面所有小节都只在**到期**时给回报——写完那一刻用户什么都
 * 没换回来（计划里叫"留存杀手"）。这里补的是"刚刚这一笔，像不像你以前遇到过的情形"。
 *
 * 三条展示纪律（逐条对应 spec）：
 *
 * 1. **两层分开**（§S9.6.2）：`你自己` 与 `全市场同桶` 各占一行，**不合并**成一个"胜率"
 *    ——用户历史是自我参照，市场是背景，混在一起就是一句不可解释的话；
 * 2. **样本不足就给"样本不足"**（§S9.6.2）：`n < 5`（自己）/ `n < 30`（市场）只写那句，
 *    不给点估计、不补 0；后端已经把不够的 `t5/t20` 置空，这里不再二次计算；
 * 3. **文案只描述过去**：`notes` 原样上屏（后端给的"只描述过去，不预测结果"），
 *    不出现任何判断词（§S9.5 的 24 词表）。
 *
 * 降级：`peer_history` 缺省（老条目/后端未上线）→ **整卡不渲染**；算不出来时后端给的是
 * `self=null, market=null, notes=["pipeline_error"]`，这里只显示那句原因——不显示 0。
 */

const SENTIMENT_TEXT: Record<string, string> = {
  lt_20: "20 分位以下",
  "20_40": "20–40 分位",
  "40_60": "40–60 分位",
  "60_80": "60–80 分位",
  ge_80: "80 分位以上",
};

const SECTOR_TEXT: Record<string, string> = {
  "below_-2": "板块 ≤−2%",
  mid: "板块 −2%~2%",
  above_2: "板块 ≥2%",
};

const MA20_TEXT: Record<string, string> = {
  neg_5: "距 MA20 <−5%",
  mid: "距 MA20 −5%~5%",
  pos_5: "距 MA20 >5%",
};

function pct(
  value: number | null | undefined,
  digits = 2,
  { signed = true }: { signed?: boolean } = {}
): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

/** 三个分桶维度的**人话**：优先用快照里的真实数值，缺数才退回桶标签。 */
function bucketText(entry: JournalEntry, history: JournalPeerHistory): string[] {
  const snapshot = entry.snapshot ?? null;
  const bucket = history.bucket;
  const sentimentPct = pct(snapshot?.sentiment_percentile, 0, { signed: false });
  const sentiment =
    sentimentPct !== null
      ? `情绪 ${sentimentPct} 分位`
      : (bucket && SENTIMENT_TEXT[bucket.sentiment]) || "情绪 未知";
  const sectorPct = snapshot?.sector_flow_state?.pct;
  const sector =
    pct(sectorPct) !== null
      ? `板块 ${pct(sectorPct)}`
      : (bucket && SECTOR_TEXT[bucket.sector]) || "板块 未知";
  const ma20 =
    pct(snapshot?.ma20_dist_pct) !== null
      ? `距 MA20 ${pct(snapshot?.ma20_dist_pct)}`
      : (bucket && MA20_TEXT[bucket.ma20]) || "距 MA20 未知";
  return [sentiment, sector, ma20];
}

/** 你自己那一行：`6 条 → 4 对 / 2 错，错的 2 次都是「时机错」`（错在哪比错几次有用）。 */
function selfLine(group: JournalPeerHistorySelf | null | undefined, minSample: number): string {
  if (!group) return "你自己：暂时算不出（同类历史缺失）";
  const n = group.n ?? 0;
  if (n === 0) return "你自己：还没有同类复盘记录";
  if (n < minSample || group.insufficient) {
    return `你自己：同类情形下你只有 ${n} 条，样本不足（<${minSample}）`;
  }
  const wrong = group.wrong ?? 0;
  const wrongBy = group.wrong_by ?? {};
  const parts = Object.entries(wrongBy)
    .filter(([, count]) => count > 0)
    .map(([verdict, count]) => `${verdictLabel(verdict)}×${count}`);
  const wrongText =
    wrong > 0 && parts.length > 0
      ? `，错的 ${wrong} 次是 ${parts.join("、")}`
      : "";
  const special: string[] = [];
  if ((group.right_for_wrong_reason ?? 0) > 0) special.push("结果对但理由不成立");
  if ((group.luck ?? 0) > 0) special.push("运气成分");
  const specialText = special.length > 0 ? `（另有 ${special.join("、")}）` : "";
  return `你自己：${n} 条 → ${group.right ?? 0} 对 / ${wrong} 错${wrongText}${specialText}`;
}

/** 全市场那一行：`截至 2026-10-16，812 条：5 日超额中位 −0.42%`；不足 30 只写样本不足。 */
function marketLine(history: JournalPeerHistory): string {
  const market = history.market;
  if (!market) return "全市场同桶：暂无可用统计";
  const minSample = market.min_sample ?? 30;
  if (market.insufficient || (market.n ?? 0) < minSample) {
    return `全市场同桶（截至 ${market.as_of}）：样本不足（${market.n ?? 0} 条 < ${minSample}）`;
  }
  const t5 = pct(market.t5_excess_med);
  const t20 = pct(market.t20_excess_med);
  const parts: string[] = [];
  if (t5 !== null) parts.push(`5 日超额中位 ${t5}`);
  if (t20 !== null) parts.push(`20 日超额中位 ${t20}`);
  return `全市场同桶（截至 ${market.as_of}，${market.n} 条）：${parts.join("，") || "暂无中位"}`;
}

export interface PeerHistoryCardProps {
  entry: JournalEntry;
}

export function PeerHistoryCard({ entry }: PeerHistoryCardProps) {
  const history = entry.peer_history;
  // 缺省/为空 → 整卡不渲染（老条目、后端未上线都不该出现"0 对 0 错"这种假数据）
  if (!history) return null;

  const self = history.self;
  const minSample = self?.min_sample ?? 5;
  // 优先同标的（更相似），不足再退到同桶（spec §S9.6.2 的分层）
  const sameSymbol = self?.same_symbol ?? null;
  const picked =
    sameSymbol && (sameSymbol.n ?? 0) > 0 ? sameSymbol : (self?.same_bucket ?? null);
  const dimensions = history.bucket ? bucketText(entry, history) : [];

  return (
    <div className="journal-peer" data-testid="journal-peer-history">
      <Text className="journal-peer__lead" data-testid="journal-peer-bucket">
        {dimensions.length > 0 ? `同类情形（${dimensions.join(" · ")}）` : "同类情形"}
      </Text>
      <Text className="journal-peer__line" data-testid="journal-peer-self">
        {selfLine(picked, minSample)}
      </Text>
      <Text className="journal-peer__line" data-testid="journal-peer-market">
        {marketLine(history)}
      </Text>
      {history.notes.length > 0 && (
        <Text className="journal-peer__note" data-testid="journal-peer-note">
          {history.notes.join("；")}
        </Text>
      )}
    </div>
  );
}
