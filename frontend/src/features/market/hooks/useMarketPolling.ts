import { useEffect, useState } from "react";
import { marketStatus, type MarketStatus } from "./marketStatus";

/** 开市时的刷新节奏：与后端按日缓存（300s TTL）搭配时，30s 已足够「盘中可见」。 */
export const OPEN_POLL_INTERVAL_MS = 30_000;

/**
 * 全球指数盘（`GlobalMarketBoard` + 首页指数条）的常驻慢轮询节奏：**不问 A 股时段**，全天 300s。
 *
 * 为什么不能跟 A 股时段：它们读的 `/market/global-indices` 含美股/欧股，
 * 美股时段在 A 股收盘之后；若跟着 A 股时段停摆，夜盘整晚不更新（此前是全天 60s，
 * 收盘后被 Task 9 的 A 股时段判据误伤成 false）。300s 是「仍能跟上美股」与
 * 「不必整夜 60s 打后端」之间的折中。
 *
 * A 股核心指数卡（`CoreIndexCards`）**不用本常量**：它读同一端点但只取 CN 子集、是市场页
 * 主 Tab 的 A 股口径卡片，用默认 `"session"` 模式（开市 30s / 休市停）并持自己的 query key
 * 与全球盘隔离（见 `CoreIndexCards.tsx` 顶部注释：同 key 会让两者的 data 共享，休市也照刷）。
 */
export const INDEX_POLL_INTERVAL_MS = 300_000;

/** 状态自身的复查节奏（开/收盘两个边界：09:30、15:00）。 */
const STATUS_TICK_MS = 60_000;

/**
 * 轮询模式：
 * - `"session"`（默认）：A 股口径卡（涨跌分布/板块/资金/情绪/数据面 + **A股核心指数卡**）
 *   ——开市 30s，其余 `false`（不轮询）；
 * - `"global-index"`：全球指数盘（全球指数卡 + 首页指数条）——常驻 300s（见
 *   {@link INDEX_POLL_INTERVAL_MS}），不受 A 股时段影响。
 */
export type MarketPollMode = "session" | "global-index";

/**
 * 市场状态驱动的共享轮询间隔。
 *
 * 用法：`const { refetchInterval } = useMarketPolling()` 后交给任意 `useQuery`。
 * 页面长开时状态会跨过 09:30 / 15:00，故用**低频 tick 复查状态**（每 60s 一次），
 * 状态变化才触发重渲染——比每次 render 都调 `marketStatus()` 更稳定，也比
 * 「按到期时间 setTimeout」更简单可解释；最坏情况下开/收盘切换晚 1 分钟生效，
 * 相对数据本身 T+1 的口径完全可接受。
 *
 * 返回 `false` 表示「不轮询」——离开交易时段后页面停止后台请求，避免收盘后整夜
 * 空转（此前各卡各自 60s 轮询，收盘后依旧打后端）。
 *
 * `"global-index"` 模式不跟踪 A 股状态，也就没有 tick（状态与它无关）。
 *
 * 注意：`refetchInterval` 由每个 observer 各起一个定时器，但**缓存按 query key 共享**
 * ——所以「同一端点两种节奏」必须同时拆 key，否则慢节奏的 observer 会看到快节奏
 * observer 刷出来的新 data（`CoreIndexCards` 与 `GlobalMarketBoard` 即为此拆 key）。
 *
 * @param mode 轮询模式，见 {@link MarketPollMode}
 */
export function useMarketPolling(mode: MarketPollMode = "session"): { refetchInterval: number | false } {
  const [status, setStatus] = useState<MarketStatus>(() => marketStatus());

  useEffect(() => {
    if (mode !== "session") return; // 常驻模式无状态可跟，不装 tick
    const id = window.setInterval(() => {
      setStatus((prev) => {
        const next = marketStatus();
        return next === prev ? prev : next; // 同值返回原引用，不触发重渲染
      });
    }, STATUS_TICK_MS);
    return () => window.clearInterval(id);
  }, [mode]);

  if (mode === "global-index") return { refetchInterval: INDEX_POLL_INTERVAL_MS };
  return { refetchInterval: status === "open" ? OPEN_POLL_INTERVAL_MS : false };
}
