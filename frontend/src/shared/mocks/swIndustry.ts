import { MOCK_STOCKS } from "./stocks";
import type { StockRecord } from "@/shared/types";

export interface SwIndustryLevel3 {
  code: string;
  name: string;
  symbols: string[];
}

export interface SwIndustryLevel2 {
  code: string;
  name: string;
  children: SwIndustryLevel3[];
}

export interface SwIndustryLevel1 {
  code: string;
  name: string;
  children: SwIndustryLevel2[];
}

export const SW_INDUSTRY_TREE: SwIndustryLevel1[] = [
  {
    code: "l1-consumer",
    name: "大消费",
    children: [
      {
        code: "l2-food-beverage",
        name: "食品饮料",
        children: [
          { code: "l3-liquor", name: "白酒", symbols: ["600519", "000858"] },
          { code: "l3-soft-drink", name: "软饮料", symbols: [] },
        ],
      },
      {
        code: "l2-home-appliance",
        name: "家用电器",
        children: [
          { code: "l3-white-goods", name: "白电", symbols: ["000333"] },
          { code: "l3-small-appliance", name: "小家电", symbols: [] },
        ],
      },
    ],
  },
  {
    code: "l1-finance-real-estate",
    name: "金融地产",
    children: [
      {
        code: "l2-banks",
        name: "银行",
        children: [
          { code: "l3-state-owned-banks", name: "国有大行", symbols: ["601398"] },
          { code: "l3-joint-stock-banks", name: "股份制银行", symbols: ["600036"] },
        ],
      },
      {
        code: "l2-non-bank-finance",
        name: "非银金融",
        children: [
          { code: "l3-insurance", name: "保险", symbols: ["601318"] },
          { code: "l3-brokerage", name: "证券", symbols: ["600030", "300059"] },
        ],
      },
      {
        code: "l2-real-estate",
        name: "房地产",
        children: [{ code: "l3-residential-dev", name: "住宅开发", symbols: ["000002"] }],
      },
    ],
  },
  {
    code: "l1-tech-manufacture",
    name: "科技制造",
    children: [
      {
        code: "l2-electronics",
        name: "电子",
        children: [
          { code: "l3-semiconductor", name: "半导体", symbols: ["688981"] },
          { code: "l3-consumer-electronics", name: "消费电子", symbols: ["002475"] },
        ],
      },
      {
        code: "l2-computer",
        name: "计算机",
        children: [
          { code: "l3-office-software", name: "办公软件", symbols: ["688111"] },
          { code: "l3-industry-software", name: "行业软件", symbols: ["002415", "830799"] },
        ],
      },
      {
        code: "l2-power-equipment",
        name: "电力设备",
        children: [
          { code: "l3-lithium-chain", name: "锂电产业链", symbols: ["601012", "300750", "430139"] },
        ],
      },
      {
        code: "l2-automobile",
        name: "汽车",
        children: [{ code: "l3-nev", name: "新能源整车", symbols: ["002594"] }],
      },
      {
        code: "l2-machinery",
        name: "机械设备",
        children: [
          { code: "l3-industrial-control", name: "工控自动化", symbols: ["300124"] },
          { code: "l3-precision-manufacturing", name: "精密制造", symbols: ["430510"] },
        ],
      },
    ],
  },
  {
    code: "l1-healthcare",
    name: "医药健康",
    children: [
      {
        code: "l2-pharma-biotech",
        name: "医药生物",
        children: [
          { code: "l3-chemical-pharma", name: "化学制药", symbols: ["600276"] },
          { code: "l3-medical-devices", name: "医疗器械", symbols: ["300760"] },
          { code: "l3-biopharma", name: "生物制药", symbols: ["430047"] },
        ],
      },
    ],
  },
  {
    code: "l1-utilities-energy",
    name: "公用事业与能源",
    children: [
      {
        code: "l2-utilities",
        name: "公用事业",
        children: [{ code: "l3-hydropower", name: "水电运营", symbols: ["600900"] }],
      },
    ],
  },
];

function pickStocks(symbols: string[]): StockRecord[] {
  const symbolSet = new Set(symbols);
  return MOCK_STOCKS.filter((stock) => symbolSet.has(stock.symbol));
}

export function getLevel1Stocks(level1Code: string): StockRecord[] {
  const level1 = SW_INDUSTRY_TREE.find((node) => node.code === level1Code);
  if (!level1) return [];
  return pickStocks(level1.children.flatMap((level2) => level2.children.flatMap((level3) => level3.symbols)));
}

export function getLevel1Node(level1Code: string): SwIndustryLevel1 | undefined {
  return SW_INDUSTRY_TREE.find((node) => node.code === level1Code);
}

export function getLevel2Node(level1Code: string, level2Code: string): SwIndustryLevel2 | undefined {
  return getLevel1Node(level1Code)?.children.find((node) => node.code === level2Code);
}

export function getLevel2Stocks(level1Code: string, level2Code: string): StockRecord[] {
  const level2 = getLevel2Node(level1Code, level2Code);
  if (!level2) return [];
  return pickStocks(level2.children.flatMap((level3) => level3.symbols));
}

export function getLevel3Stocks(
  level1Code: string,
  level2Code: string,
  level3Code: string
): StockRecord[] {
  const level2 = getLevel2Node(level1Code, level2Code);
  const level3 = level2?.children.find((node) => node.code === level3Code);
  if (!level3) return [];
  return pickStocks(level3.symbols);
}
