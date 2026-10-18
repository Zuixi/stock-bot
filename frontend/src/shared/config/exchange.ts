import type { Exchange } from "@/shared/types";

/**
 * 三大交易所 —— 顺序即"兜底顺序"，与改造前串行探活 `["Shanghai_Stocks",
 * "Shenzen_Stocks", "Beijing_Stocks"]` 完全一致（不回归的前提）。
 */
export const EXCHANGES: Exchange[] = ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"];

/**
 * 6 位 A 股代码 → 交易所候选（首个最可能；无法判定时返回全部三所，保持旧行为）。
 *
 * 依据代码段（不含指数/基金等非 A 股代码，本函数只服务于个股详情）：
 * - 6 开头：上交所 600/601/603/605/688/689
 * - 0 / 3 开头：深交所 000/001/002/003、创业板 300/301
 * - 4 / 8 开头、92 开头：北交所（430/830/831/832/833/834/835/836/837/838/839/870/871/872/873，新代码段 920）
 */
export function exchangeCandidates(symbol: string): Exchange[] {
  const s = symbol.padStart(6, "0");
  const head = s[0];
  if (head === "6") return ["Shanghai_Stocks"];
  if (head === "0" || head === "3") return ["Shenzen_Stocks"];
  if (head === "4" || head === "8" || s.startsWith("92")) return ["Beijing_Stocks"];
  // 未知前缀：全部试（返回副本，避免调用方改到共享常量）
  return [...EXCHANGES];
}

/**
 * 候选在前、其余交易所按 `EXCHANGES` 原序追加（R9）——"先猜后兜底"，不丢交易所、不重复。
 *
 * 调用方照旧逐个 try：命中即返回，候选全失败（如代码根本不存在）才走到剩下两所，
 * 因此"未知代码仍 404 不无限重试"的语义不变。
 */
export function exchangeOrder(symbol: string): Exchange[] {
  const candidates = exchangeCandidates(symbol);
  return [...candidates, ...EXCHANGES.filter((exchange) => !candidates.includes(exchange))];
}
