import type { StockRecord, KLinePoint, Exchange } from "@/shared/types";

function r(min: number, max: number, decimals = 2): number {
  return +(min + Math.random() * (max - min)).toFixed(decimals);
}

const STOCK_BASE: Pick<StockRecord, "symbol" | "name" | "exchange" | "category" | "industry">[] = [
  { symbol: "600519", name: "贵州茅台", exchange: "Shanghai_Stocks", category: "主板A股", industry: "食品饮料" },
  { symbol: "601398", name: "工商银行", exchange: "Shanghai_Stocks", category: "主板A股", industry: "银行" },
  { symbol: "600036", name: "招商银行", exchange: "Shanghai_Stocks", category: "主板A股", industry: "银行" },
  { symbol: "601318", name: "中国平安", exchange: "Shanghai_Stocks", category: "主板A股", industry: "非银金融" },
  { symbol: "600900", name: "长江电力", exchange: "Shanghai_Stocks", category: "主板A股", industry: "公用事业" },
  { symbol: "600276", name: "恒瑞医药", exchange: "Shanghai_Stocks", category: "主板A股", industry: "医药生物" },
  { symbol: "601012", name: "隆基绿能", exchange: "Shanghai_Stocks", category: "主板A股", industry: "电力设备" },
  { symbol: "600030", name: "中信证券", exchange: "Shanghai_Stocks", category: "主板A股", industry: "非银金融" },
  { symbol: "688111", name: "金山办公", exchange: "Shanghai_Stocks", category: "科创板", industry: "计算机" },
  { symbol: "688981", name: "中芯国际", exchange: "Shanghai_Stocks", category: "科创板", industry: "电子" },
  { symbol: "000858", name: "五粮液", exchange: "Shenzen_Stocks", category: "主板A股", industry: "食品饮料" },
  { symbol: "000002", name: "万科A", exchange: "Shenzen_Stocks", category: "主板A股", industry: "房地产" },
  { symbol: "000333", name: "美的集团", exchange: "Shenzen_Stocks", category: "主板A股", industry: "家用电器" },
  { symbol: "002594", name: "比亚迪", exchange: "Shenzen_Stocks", category: "中小板", industry: "汽车" },
  { symbol: "002475", name: "立讯精密", exchange: "Shenzen_Stocks", category: "中小板", industry: "电子" },
  { symbol: "300750", name: "宁德时代", exchange: "Shenzen_Stocks", category: "创业板", industry: "电力设备" },
  { symbol: "300760", name: "迈瑞医疗", exchange: "Shenzen_Stocks", category: "创业板", industry: "医药生物" },
  { symbol: "300059", name: "东方财富", exchange: "Shenzen_Stocks", category: "创业板", industry: "非银金融" },
  { symbol: "002415", name: "海康威视", exchange: "Shenzen_Stocks", category: "中小板", industry: "计算机" },
  { symbol: "300124", name: "汇川技术", exchange: "Shenzen_Stocks", category: "创业板", industry: "机械设备" },
  { symbol: "430047", name: "诺思兰德", exchange: "Beijing_Stocks", category: "北交所", industry: "医药生物" },
  { symbol: "430139", name: "贝特瑞", exchange: "Beijing_Stocks", category: "北交所", industry: "电力设备" },
  { symbol: "430510", name: "丰光精密", exchange: "Beijing_Stocks", category: "北交所", industry: "机械设备" },
  { symbol: "830799", name: "艾融软件", exchange: "Beijing_Stocks", category: "北交所", industry: "计算机" },
];

function buildStock(base: typeof STOCK_BASE[number]): StockRecord {
  const price = r(5, 2000);
  const chg = r(-8, 8);
  return {
    ...base,
    latestPrice: price,
    change: +(price * chg / 100).toFixed(2),
    changePercent: chg,
    volume: r(1e6, 5e8, 0),
    turnover: r(1e8, 5e10, 0),
    marketCap: r(1e10, 2e12, 0),
    circulatingCap: r(5e9, 1.5e12, 0),
    pe: r(5, 80),
    pb: r(0.5, 15),
    roe: r(2, 35),
    revenueGrowth: r(-30, 60),
    profitGrowth: r(-50, 100),
    asof: "2026-04-07T15:00:00Z",
  };
}

export const MOCK_STOCKS: StockRecord[] = STOCK_BASE.map(buildStock);

export function generateKLine(days: number): KLinePoint[] {
  const points: KLinePoint[] = [];
  let close = r(20, 200);
  const now = new Date("2026-04-07");
  for (let i = days; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    if (d.getDay() === 0 || d.getDay() === 6) continue;
    const change = r(-0.05, 0.05);
    const open = close;
    close = +(open * (1 + change)).toFixed(2);
    const high = +(Math.max(open, close) * (1 + Math.random() * 0.02)).toFixed(2);
    const low = +(Math.min(open, close) * (1 - Math.random() * 0.02)).toFixed(2);
    points.push({
      date: d.toISOString().slice(0, 10),
      open,
      close,
      high,
      low,
      volume: r(1e6, 8e7, 0),
    });
  }
  return points;
}

export function getStockBySymbol(symbol: string): StockRecord | undefined {
  return MOCK_STOCKS.find((s) => s.symbol === symbol);
}

export function filterStocks(params: {
  exchange?: Exchange;
  industry?: string;
  sortBy?: string;
  sortOrder?: "asc" | "desc";
}): StockRecord[] {
  let list = [...MOCK_STOCKS];
  if (params.exchange) list = list.filter((s) => s.exchange === params.exchange);
  if (params.industry) list = list.filter((s) => s.industry === params.industry);
  if (params.sortBy) {
    const key = params.sortBy as keyof StockRecord;
    const dir = params.sortOrder === "asc" ? 1 : -1;
    list.sort((a, b) => {
      const va = a[key] ?? 0;
      const vb = b[key] ?? 0;
      return va > vb ? dir : va < vb ? -dir : 0;
    });
  }
  return list;
}
