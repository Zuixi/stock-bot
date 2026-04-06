export const USE_MOCK = true;

export const CACHE_TIME = {
  marketOverview: 30_000,
  stockList: 60_000,
  stockDetail: 15_000,
  kline: 5 * 60_000,
} as const;

export const PAGE_SIZES = [20, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 20;
