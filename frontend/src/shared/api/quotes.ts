import { ApiError, apiGet } from "./client";
import { exchangeOrder } from "@/shared/config/exchange";
import type { AdjustMode, KLinePoint, KlineResult } from "@/shared/types";

interface BackendDailyQuote {
  trade_date: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close: number;
  volume?: number | null;
  amount?: number | null;
}

interface BackendKlineResponse {
  symbol: string;
  name: string;
  exchange: string;
  data: BackendDailyQuote[];
  /** 后端复权能力声明（P2 之前后端不返回该字段，前端按可用处理） */
  adjust_available?: boolean;
}

export async function fetchKlineBySymbol(
  symbol: string,
  days: number,
  adjust: AdjustMode = "raw",
): Promise<KlineResult> {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days);
  const startDate = start.toISOString().slice(0, 10);
  const endDate = end.toISOString().slice(0, 10);

  // 按代码前缀直连最可能的交易所，候选落空再按原顺序兜底（P0-4 / R9）
  for (const exchange of exchangeOrder(symbol)) {
    try {
      const response = await apiGet<BackendKlineResponse>(
        `/api/v1/exchanges/${exchange}/stocks/${symbol}/quotes/daily`,
        { start: startDate, end: endDate, adjust },
      );
      if (!response.data.length) {
        continue;
      }
      const points: KLinePoint[] = response.data.map((item) => {
        const open = item.open ?? item.close;
        const high = item.high ?? Math.max(open, item.close);
        const low = item.low ?? Math.min(open, item.close);
        return {
          date: item.trade_date,
          open,
          close: item.close,
          high,
          low,
          volume: item.volume ?? 0,
          amount: item.amount ?? undefined,
        };
      });
      return { points, adjustAvailable: response.adjust_available !== false };
    } catch (err) {
      if (err instanceof ApiError && err.status !== 404) {
        throw err;
      }
      // Try the next exchange on 404.
    }
  }

  return { points: [], adjustAvailable: true };
}
