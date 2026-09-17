import { useEffect, useState } from "react";
import { marketStatus, type MarketStatus } from "./marketStatus";

/** 开市时的刷新节奏：与后端按日缓存（300s TTL）搭配时，30s 已足够「盘中可见」。 */
export const OPEN_POLL_INTERVAL_MS = 30_000;

/** 状态自身的复查节奏（开/收盘两个边界：09:30、15:00）。 */
const STATUS_TICK_MS = 60_000;

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
 */
export function useMarketPolling(): { refetchInterval: number | false } {
  const [status, setStatus] = useState<MarketStatus>(() => marketStatus());

  useEffect(() => {
    const id = window.setInterval(() => {
      setStatus((prev) => {
        const next = marketStatus();
        return next === prev ? prev : next; // 同值返回原引用，不触发重渲染
      });
    }, STATUS_TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  return { refetchInterval: status === "open" ? OPEN_POLL_INTERVAL_MS : false };
}
