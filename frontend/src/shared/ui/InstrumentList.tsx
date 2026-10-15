import type { ReactNode } from "react";
import "./InstrumentList.css";

export interface InstrumentIdentityProps {
  /** 代码（ticker）：名称右侧的小号标识，与 `DataRow` 的 `.datarow__ticker` 同语义同字号。 */
  symbol: string;
  /** 名称（可传节点：自选表里它是一个 `<a>`；链接目标仍是 `/company/:symbol`，未改动）。 */
  name: ReactNode;
}

/**
 * 标的清单的**标识格**（P1-2）：名称 + 小号代码，**占一列**。
 *
 * 为什么把「代码」「名称」两列并成一格：三元组在 390px 视口下要**完整可见**。
 * 实测（`/market` 数据面卡与 `/me` 自选卡在 390 下表格内容宽 = 316–342px）：
 * 标识格 110 + 三元组 200 = 310 ✔；而拆成两列（76 + 96 = 172）+ 三元组 = 372 ✗
 * ——涨跌幅那一列会被裁掉，正是"看起来没溢出、其实读不全"那种假绿。
 * 两个标识都没丢（`symbol` / `name` 都在），链接目标也没变。
 *
 * 刻意**单行**而不是"名称在上、代码在下"：两行会让每行高 +17px，320px 的表体里
 * 可见行数从 8 掉到 6——本轮的题目是**密排**，不是把行撑高。
 */
export function InstrumentIdentity({ symbol, name }: InstrumentIdentityProps) {
  return (
    <span className="inst-identity">
      <span className="inst-identity__name">{name}</span>
      <span className="inst-identity__code num">{symbol}</span>
    </span>
  );
}
