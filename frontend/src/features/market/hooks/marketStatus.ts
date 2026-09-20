/**
 * A 股交易时段判定（上海时区）。
 *
 * 时区来源显式写成 `Asia/Shanghai`（与后端 APScheduler 的 `ZoneInfo("Asia/Shanghai")` 同源），
 * 不读宿主 local 时间：开发机可能是 JST/UTC，任何依赖 `getHours()` 的写法都会随机器漂。
 * `Intl` 的 tz 库由运行时提供（含历史规则），比手写 `+8h` 常量更不容易随规则变更而失真。
 *
 * 只判工作日与时段，**不判法定节假日**（前端无交易日历）：节假日里 `open` 只是让前端
 * 多做几次轮询，不改变任何展示口径；反之把节假日误判成 `closed` 会让盘中数据停止刷新——
 * 两害相权，宁可多轮询。
 */
export type MarketStatus = "open" | "preopen" | "closed";

/** 复用 formatter（`formatToParts` 每次调用都会分配对象，实例可长期复用）。 */
const SHANGHAI_CLOCK = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Shanghai",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const OPEN_MINUTE = 9 * 60 + 30; // 09:30 集合竞价结束、连续竞价开始
const CLOSE_MINUTE = 15 * 60; // 15:00 收盘

/**
 * @param now 当前时刻（默认 `new Date()`；显式传入便于测试与固定时钟）
 * @returns 上海时区下：工作日 09:30–15:00 → `open`；工作日 09:30 前 → `preopen`；其余 → `closed`
 */
export function marketStatus(now: Date = new Date()): MarketStatus {
  const parts = SHANGHAI_CLOCK.formatToParts(now);
  const part = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((p) => p.type === type)?.value ?? "";
  const weekday = part("weekday"); // Mon / Tue / … / Sun
  if (weekday === "Sat" || weekday === "Sun") return "closed";
  // 部分 ICU 版本在 hour12:false 下把午夜格式化成 "24" → 取模归一，避免 24:10 被当成当天 24 点
  const minuteOfDay = (Number(part("hour")) % 24) * 60 + Number(part("minute"));
  if (minuteOfDay < OPEN_MINUTE) return "preopen";
  if (minuteOfDay < CLOSE_MINUTE) return "open";
  return "closed";
}
