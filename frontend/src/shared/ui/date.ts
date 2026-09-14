/** ISO 日期 → 「9月9日」（与 features/market/components/format.ts 既有实现同形）。 */
export function formatCnDate(iso: string): string {
  const [, month, day] = iso.split("-");
  return month && day ? `${Number(month)}月${Number(day)}日` : iso;
}
