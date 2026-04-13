import { apiGet } from "./client";
import type { KLinePoint } from "@/shared/types";

interface BackendDailyQuote {
  trade_date: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close: number;
  volume?: number | null;
}

interface BackendKlineResponse {
  symbol: string;
  name: string;
  exchange: string;
  data: BackendDailyQuote[];
}

const QUOTE_EXCHANGES = ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"] as const;

export async function fetchKlineBySymbol(symbol: string, days: number): Promise<KLinePoint[]> {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days);
  const startDate = start.toISOString().slice(0, 10);
  const endDate = end.toISOString().slice(0, 10);

  for (const exchange of QUOTE_EXCHANGES) {
    try {
      const response = await apiGet<BackendKlineResponse>(
        `/api/v1/exchanges/${exchange}/stocks/${symbol}/quotes/daily`,
        { start: startDate, end: endDate }
      );
      if (!response.data.length) {
        continue;
      }
      return response.data.map((item) => {
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
        };
      });
    } catch {
      // Try the next exchange.
    }
  }

  return [];
}
