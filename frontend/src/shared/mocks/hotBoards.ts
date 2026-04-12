export type HotBoardCategory = "industry" | "concept" | "region";

export interface HotBoardLeader {
  symbol: string;
  name: string;
  changePercent: number;
}

export interface HotBoardItem {
  id: string;
  name: string;
  code: string;
  changePercent: number;
  upCount: number;
  flatCount: number;
  downCount: number;
  leaders: HotBoardLeader[];
}

export interface HotBoardCategoryConfig {
  key: HotBoardCategory;
  label: string;
  description: string;
}

export const HOT_BOARD_CATEGORIES: HotBoardCategoryConfig[] = [
  { key: "industry", label: "行业板块", description: "按申万行业划分的热门板块" },
  { key: "concept", label: "概念板块", description: "按市场主题概念聚合的热门板块" },
  { key: "region", label: "地域板块", description: "按地区维度聚合的热门板块" },
];

export const HOT_BOARD_DATA: Record<HotBoardCategory, HotBoardItem[]> = {
  industry: [
    { id: "ind-power", name: "电力", code: "630700", changePercent: 3.93, upCount: 97, flatCount: 1, downCount: 9, leaders: [{ symbol: "600995", name: "南网能源", changePercent: 10.02 }, { symbol: "600905", name: "三峡能源", changePercent: 5.21 }] },
    { id: "ind-agri", name: "农机装备", code: "610200", changePercent: 3.26, upCount: 15, flatCount: 0, downCount: 1, leaders: [{ symbol: "601038", name: "一拖股份", changePercent: 8.12 }, { symbol: "300159", name: "新研股份", changePercent: 4.08 }] },
    { id: "ind-broker", name: "证券Ⅱ", code: "490100", changePercent: 2.56, upCount: 50, flatCount: 0, downCount: 0, leaders: [{ symbol: "600030", name: "中信证券", changePercent: 4.81 }, { symbol: "000166", name: "申万宏源", changePercent: 3.12 }] },
    { id: "ind-metals", name: "能源金属", code: "240500", changePercent: 2.27, upCount: 10, flatCount: 0, downCount: 3, leaders: [{ symbol: "002460", name: "赣锋锂业", changePercent: 6.33 }, { symbol: "002466", name: "天齐锂业", changePercent: 4.66 }] },
    { id: "ind-chem", name: "其他电子Ⅱ", code: "270400", changePercent: 2.17, upCount: 25, flatCount: 0, downCount: 8, leaders: [{ symbol: "002938", name: "鹏鼎控股", changePercent: 5.91 }, { symbol: "603501", name: "韦尔股份", changePercent: 4.22 }] },
    { id: "ind-grid", name: "其他电源设备Ⅱ", code: "630300", changePercent: 2.06, upCount: 24, flatCount: 1, downCount: 5, leaders: [{ symbol: "300274", name: "阳光电源", changePercent: 7.03 }, { symbol: "601012", name: "隆基绿能", changePercent: 3.77 }] },
    { id: "ind-textile", name: "纺织制造", code: "350100", changePercent: 1.85, upCount: 25, flatCount: 1, downCount: 6, leaders: [{ symbol: "002612", name: "朗姿股份", changePercent: 5.02 }, { symbol: "002293", name: "罗莱生活", changePercent: 2.88 }] },
    { id: "ind-logistics", name: "物流", code: "420800", changePercent: 1.68, upCount: 43, flatCount: 2, downCount: 3, leaders: [{ symbol: "603128", name: "华贸物流", changePercent: 4.55 }, { symbol: "601156", name: "东航物流", changePercent: 3.48 }] },
    { id: "ind-photonics", name: "光伏设备", code: "630500", changePercent: 1.67, upCount: 59, flatCount: 0, downCount: 11, leaders: [{ symbol: "300763", name: "锦浪科技", changePercent: 8.17 }, { symbol: "300316", name: "晶盛机电", changePercent: 3.92 }] },
    { id: "ind-edu", name: "教育", code: "461100", changePercent: 1.66, upCount: 15, flatCount: 0, downCount: 1, leaders: [{ symbol: "300010", name: "豆神教育", changePercent: 7.21 }, { symbol: "002607", name: "中公教育", changePercent: 4.13 }] },
  ],
  concept: [
    { id: "con-ai-agent", name: "AI Agent", code: "GN001", changePercent: 4.21, upCount: 68, flatCount: 3, downCount: 9, leaders: [{ symbol: "688111", name: "金山办公", changePercent: 6.32 }, { symbol: "300033", name: "同花顺", changePercent: 5.15 }] },
    { id: "con-robot", name: "人形机器人", code: "GN002", changePercent: 3.74, upCount: 54, flatCount: 2, downCount: 11, leaders: [{ symbol: "300124", name: "汇川技术", changePercent: 4.25 }, { symbol: "002050", name: "三花智控", changePercent: 3.11 }] },
    { id: "con-chip", name: "先进封装", code: "GN003", changePercent: 2.89, upCount: 45, flatCount: 4, downCount: 12, leaders: [{ symbol: "002475", name: "立讯精密", changePercent: 5.63 }, { symbol: "688981", name: "中芯国际", changePercent: 3.74 }] },
    { id: "con-low-alt", name: "低空经济", code: "GN004", changePercent: 2.65, upCount: 41, flatCount: 2, downCount: 10, leaders: [{ symbol: "000099", name: "中信海直", changePercent: 7.18 }, { symbol: "300696", name: "爱乐达", changePercent: 4.82 }] },
    { id: "con-energy-storage", name: "储能", code: "GN005", changePercent: 2.34, upCount: 50, flatCount: 1, downCount: 15, leaders: [{ symbol: "300274", name: "阳光电源", changePercent: 5.14 }, { symbol: "300750", name: "宁德时代", changePercent: 2.76 }] },
    { id: "con-data-element", name: "数据要素", code: "GN006", changePercent: 2.11, upCount: 32, flatCount: 0, downCount: 8, leaders: [{ symbol: "600570", name: "恒生电子", changePercent: 3.96 }, { symbol: "603019", name: "中科曙光", changePercent: 3.18 }] },
    { id: "con-internet-fin", name: "互联网金融", code: "GN007", changePercent: 1.92, upCount: 27, flatCount: 2, downCount: 6, leaders: [{ symbol: "300059", name: "东方财富", changePercent: 2.68 }, { symbol: "300803", name: "指南针", changePercent: 2.31 }] },
    { id: "con-innov-drug", name: "创新药", code: "GN008", changePercent: 1.56, upCount: 34, flatCount: 1, downCount: 9, leaders: [{ symbol: "600276", name: "恒瑞医药", changePercent: 2.31 }, { symbol: "300347", name: "泰格医药", changePercent: 1.87 }] },
  ],
  region: [
    { id: "reg-yangtze", name: "长三角", code: "DQ001", changePercent: 1.86, upCount: 102, flatCount: 6, downCount: 24, leaders: [{ symbol: "600570", name: "恒生电子", changePercent: 3.21 }, { symbol: "600309", name: "万华化学", changePercent: 2.57 }] },
    { id: "reg-greater-bay", name: "粤港澳大湾区", code: "DQ002", changePercent: 1.74, upCount: 89, flatCount: 4, downCount: 30, leaders: [{ symbol: "000333", name: "美的集团", changePercent: 2.66 }, { symbol: "002594", name: "比亚迪", changePercent: 2.33 }] },
    { id: "reg-beijing-tianjin-hebei", name: "京津冀", code: "DQ003", changePercent: 1.38, upCount: 60, flatCount: 5, downCount: 18, leaders: [{ symbol: "600161", name: "天坛生物", changePercent: 4.12 }, { symbol: "600560", name: "金自天正", changePercent: 3.58 }] },
    { id: "reg-hainan", name: "海南自贸区", code: "DQ004", changePercent: 1.12, upCount: 22, flatCount: 2, downCount: 8, leaders: [{ symbol: "600515", name: "海南机场", changePercent: 4.03 }, { symbol: "000735", name: "罗牛山", changePercent: 2.19 }] },
    { id: "reg-chengdu-chongqing", name: "成渝双城", code: "DQ005", changePercent: 0.95, upCount: 44, flatCount: 3, downCount: 16, leaders: [{ symbol: "000155", name: "川能动力", changePercent: 2.84 }, { symbol: "600666", name: "奥瑞德", changePercent: 2.41 }] },
    { id: "reg-xiongan", name: "雄安新区", code: "DQ006", changePercent: 0.88, upCount: 36, flatCount: 2, downCount: 15, leaders: [{ symbol: "600340", name: "华夏幸福", changePercent: 3.02 }, { symbol: "300137", name: "先河环保", changePercent: 2.21 }] },
    { id: "reg-west-dev", name: "西部大开发", code: "DQ007", changePercent: 0.77, upCount: 71, flatCount: 5, downCount: 26, leaders: [{ symbol: "600217", name: "中再资环", changePercent: 4.44 }, { symbol: "000792", name: "盐湖股份", changePercent: 2.64 }] },
    { id: "reg-northeast-revitalization", name: "东北振兴", code: "DQ008", changePercent: 0.51, upCount: 25, flatCount: 1, downCount: 13, leaders: [{ symbol: "600188", name: "兖矿能源", changePercent: 2.17 }, { symbol: "600864", name: "哈投股份", changePercent: 1.53 }] },
  ],
};

export function getHotBoardCategoryLabel(category: HotBoardCategory): string {
  return HOT_BOARD_CATEGORIES.find((item) => item.key === category)?.label ?? "热门板块";
}
