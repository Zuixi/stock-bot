import { Alert } from "antd";

/** `degraded_reason` → 中文文案；不同原因不同文案，未知原因回退原串，不静默吞掉。
 * 本映射被「情绪温度计」卡（长文案）、概念详情页与次新股情绪卡共用；三张数据卡各自用短占位文案（见组件内）。
 * `no_members`（概念板块无成分名录）与其余原因同一条原则：缺失要显式暴露，不静默空态。
 */
export const DEGRADED_REASON_TEXT: Record<string, string> = {
  price_limits_missing: "涨跌停价尚未回补，连板梯队暂不可用（每个交易日 16:50 自动补齐）",
  partial_day: "当日行情未回补完整，暂不展示梯队",
  no_quotes: "库内暂无行情数据",
  no_limit_up_rows: "当日无涨停股（候选为空）",
  insufficient_trade_days: "交易日不足 2 天",
  no_members: "成分数据尚未采集（每日 18:20 刷新）",
};

/** 降级原因横幅（`degraded_reason` 闭集见 §2.3）；未知原因原样显示，便于发现契约漂移。 */
export function DegradedNotice({ reason }: { reason: string }) {
  const text = DEGRADED_REASON_TEXT[reason] ?? reason;
  return <Alert type="warning" showIcon message={text} />;
}
