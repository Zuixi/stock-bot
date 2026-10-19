import { expect, test } from "@playwright/test";

import { exchangeCandidates, exchangeOrder } from "@/shared/config/exchange";

/**
 * P0-4 单元测试（R10：前端无单测 runner，用 `@playwright/test` 原生跑 TS，零新依赖）。
 *
 * 被测对象是纯函数：从 6 位 A 股代码推断交易所候选。它决定详情页首次请求打哪个
 * 交易所 —— 猜对就省掉最多 2 次串行 404 探活，猜错必须仍能兜底（旧行为不回归），
 * 所以这里同时钉住"候选"和"候选 + 其余交易所"的完整顺序。
 */

test.describe("exchangeCandidates：代码前缀 → 交易所候选", () => {
  const cases: Array<{ symbol: string; expected: string[]; reason: string }> = [
    { symbol: "600519", expected: ["Shanghai_Stocks"], reason: "6 开头 → 上交所（600 主板）" },
    { symbol: "000001", expected: ["Shenzen_Stocks"], reason: "0 开头 → 深交所（000 主板）" },
    { symbol: "300750", expected: ["Shenzen_Stocks"], reason: "3 开头 → 深交所（创业板 300）" },
    { symbol: "430047", expected: ["Beijing_Stocks"], reason: "4 开头 → 北交所（430）" },
    { symbol: "830799", expected: ["Beijing_Stocks"], reason: "8 开头 → 北交所（830）" },
    { symbol: "920001", expected: ["Beijing_Stocks"], reason: "92 开头 → 北交所（920 新代码段）" },
    {
      symbol: "999999",
      expected: ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"],
      reason: "未知前缀 → 三所全返回（保持旧行为）",
    },
  ];

  for (const { symbol, expected, reason } of cases) {
    test(`${symbol} → ${expected.join(" / ")}（${reason}）`, () => {
      expect(exchangeCandidates(symbol)).toEqual(expected);
    });
  }

  test("候选是副本：改返回值不会污染共享的交易所常量", () => {
    const unknown = exchangeCandidates("999999");
    unknown.pop();
    expect(exchangeCandidates("999999")).toHaveLength(3);
  });
});

test.describe("exchangeOrder：候选在前，其余交易所按原序兜底（R9）", () => {
  test('exchangeOrder("600519") 以上交所打头、长度 3、无重复', () => {
    const order = exchangeOrder("600519");
    expect(order[0]).toBe("Shanghai_Stocks");
    expect(order).toHaveLength(3);
    expect(new Set(order).size).toBe(3);
    // 兜底顺序与改造前的串行探活一致，保证"候选全失"时不回归
    expect(order).toEqual(["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"]);
  });

  test("深市 / 北交所代码同样把猜中的那所提到最前，其余保持原序", () => {
    expect(exchangeOrder("000001")).toEqual(["Shenzen_Stocks", "Shanghai_Stocks", "Beijing_Stocks"]);
    expect(exchangeOrder("920001")).toEqual(["Beijing_Stocks", "Shanghai_Stocks", "Shenzen_Stocks"]);
  });

  test("未知前缀时 exchangeOrder 与 exchangeCandidates 一致（都是三所）", () => {
    expect(exchangeOrder("999999")).toEqual(exchangeCandidates("999999"));
    expect(exchangeOrder("999999")).toHaveLength(3);
  });
});
