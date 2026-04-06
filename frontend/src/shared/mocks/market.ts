import type { MarketIndex, SectorSummary } from "@/shared/types";

export const MOCK_INDICES: MarketIndex[] = [
  { code: "000001", name: "上证指数", value: 3880.10, change: -39.19, changePercent: -1.00, exchange: "Shanghai_Stocks", asof: "2026-04-07T15:00:00Z" },
  { code: "399001", name: "深证成指", value: 13352.90, change: -134.04, changePercent: -0.99, exchange: "Shenzen_Stocks", asof: "2026-04-07T15:00:00Z" },
  { code: "399006", name: "创业板指", value: 2754.38, change: 12.56, changePercent: 0.46, exchange: "Shenzen_Stocks", asof: "2026-04-07T15:00:00Z" },
  { code: "899050", name: "北证50", value: 1128.65, change: -8.32, changePercent: -0.73, exchange: "Beijing_Stocks", asof: "2026-04-07T15:00:00Z" },
  { code: "000016", name: "上证50", value: 2816.53, change: -17.50, changePercent: -0.62, exchange: "Shanghai_Stocks", asof: "2026-04-07T15:00:00Z" },
  { code: "000300", name: "沪深300", value: 4452.99, change: -31.94, changePercent: -0.71, exchange: "Shanghai_Stocks", asof: "2026-04-07T15:00:00Z" },
  { code: "000905", name: "中证500", value: 5341.68, change: 29.19, changePercent: 0.55, exchange: "Shanghai_Stocks", asof: "2026-04-07T15:00:00Z" },
  { code: "000852", name: "中证1000", value: 5450.33, change: 73.00, changePercent: 1.36, exchange: "Shanghai_Stocks", asof: "2026-04-07T15:00:00Z" },
];

export const MOCK_SECTORS: SectorSummary[] = [
  { name: "银行", changePercent: -0.74, totalMarketCap: 12.5e12, stockCount: 42, topStocks: [{ symbol: "601398", name: "工商银行", changePercent: -0.85 }, { symbol: "601939", name: "建设银行", changePercent: -0.62 }] },
  { name: "电子", changePercent: 2.15, totalMarketCap: 8.3e12, stockCount: 385, topStocks: [{ symbol: "002475", name: "立讯精密", changePercent: 3.21 }, { symbol: "603501", name: "韦尔股份", changePercent: 2.87 }] },
  { name: "医药生物", changePercent: -0.24, totalMarketCap: 7.8e12, stockCount: 420, topStocks: [{ symbol: "600276", name: "恒瑞医药", changePercent: -1.02 }, { symbol: "300760", name: "迈瑞医疗", changePercent: 0.53 }] },
  { name: "食品饮料", changePercent: -0.03, totalMarketCap: 6.2e12, stockCount: 115, topStocks: [{ symbol: "600519", name: "贵州茅台", changePercent: 0.12 }, { symbol: "000858", name: "五粮液", changePercent: -0.35 }] },
  { name: "电力设备", changePercent: 1.56, totalMarketCap: 5.9e12, stockCount: 342, topStocks: [{ symbol: "300750", name: "宁德时代", changePercent: 2.45 }, { symbol: "601012", name: "隆基绿能", changePercent: 1.23 }] },
  { name: "计算机", changePercent: 3.42, totalMarketCap: 4.1e12, stockCount: 312, topStocks: [{ symbol: "002415", name: "海康威视", changePercent: 1.89 }, { symbol: "688111", name: "金山办公", changePercent: 4.56 }] },
  { name: "有色金属", changePercent: -1.35, totalMarketCap: 3.6e12, stockCount: 148, topStocks: [{ symbol: "601899", name: "紫金矿业", changePercent: -2.10 }, { symbol: "002466", name: "天齐锂业", changePercent: -1.58 }] },
  { name: "非银金融", changePercent: -0.44, totalMarketCap: 5.4e12, stockCount: 98, topStocks: [{ symbol: "601318", name: "中国平安", changePercent: -0.56 }, { symbol: "600030", name: "中信证券", changePercent: -0.12 }] },
  { name: "汽车", changePercent: 0.97, totalMarketCap: 4.8e12, stockCount: 210, topStocks: [{ symbol: "002594", name: "比亚迪", changePercent: 1.45 }, { symbol: "601238", name: "广汽集团", changePercent: 0.67 }] },
  { name: "房地产", changePercent: -2.11, totalMarketCap: 1.8e12, stockCount: 105, topStocks: [{ symbol: "001979", name: "招商蛇口", changePercent: -1.89 }, { symbol: "600048", name: "保利发展", changePercent: -2.34 }] },
  { name: "传媒", changePercent: 1.22, totalMarketCap: 1.5e12, stockCount: 145, topStocks: [{ symbol: "300413", name: "芒果超媒", changePercent: 2.10 }, { symbol: "002602", name: "世纪华通", changePercent: 1.56 }] },
  { name: "机械设备", changePercent: 0.88, totalMarketCap: 3.2e12, stockCount: 405, topStocks: [{ symbol: "601100", name: "恒立液压", changePercent: 1.34 }, { symbol: "300124", name: "汇川技术", changePercent: 0.98 }] },
];

export const MOCK_DISTRIBUTION = [
  { range: "跌停", count: 38 },
  { range: ">-7%", count: 56 },
  { range: "-5~-7%", count: 215 },
  { range: "-3~-5%", count: 260 },
  { range: "-1~-3%", count: 27 },
  { range: "0~-1%", count: 481 },
  { range: "0~1%", count: 3618 },
  { range: "1~3%", count: 42 },
  { range: "3~5%", count: 41 },
  { range: ">5%", count: 18 },
  { range: "涨停", count: 32 },
];

export const MOCK_CAPITAL_FLOW = [
  { name: "银行", inflow: 18.5, outflow: -17.1 },
  { name: "酿酒", inflow: 12.3, outflow: -8.9 },
  { name: "电力设备", inflow: 14.2, outflow: -6.3 },
  { name: "半导体", inflow: 22.8, outflow: -15.2 },
  { name: "汽车", inflow: 9.5, outflow: -7.2 },
  { name: "医药", inflow: 8.1, outflow: -11.4 },
  { name: "房地产", inflow: 3.2, outflow: -8.6 },
  { name: "计算机", inflow: 16.7, outflow: -9.8 },
];
