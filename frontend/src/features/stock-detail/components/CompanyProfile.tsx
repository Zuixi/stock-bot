import { Card } from "antd";
import { formatCnDate } from "@/shared/ui";
import type { StockRecord } from "@/shared/types";
import "./CompanyProfile.css";

/**
 * 「公司概况」（P2-3 / F17）：全称 / 上市日期 / 地区 / 实控人。
 *
 * F17 的根因不是"后端没有"——`StockOut` 一直带着这四个字段（`full_name` / `list_date` /
 * `area` / `act_name`），是前端连类型都没映射，页面于是白丢了一块既有信息。
 * `check-api-fields.mjs` 现在会为这四个字段逐条比对后端 schema。
 *
 * ## 缺失 ≠ 0（本组件的唯一硬纪律）
 *
 * 任一字段为 `null` / 空串 ⇒ **该行整条不渲染**，既不画 `--` 也不留空行：
 * 名录数据里"实控人未知"与"实控人是 --"是两件事，后者会被读成一个值。
 * 四项全空 ⇒ 整个卡片不渲染（返回 `null`），而不是渲染一张只有标题的空卡。
 *
 * 上市日期用 `formatCnDate` 拼年份而不是新写一个日期格式化器：月/日的写法全站只有
 * `shared/ui/date.ts` 一处（计划 §3.9），但 `formatCnDate` 只到"月日"粒度 ——
 * 上市日期没有年份就完全没用（"8月27日"是哪一年？），故在这里补年份前缀，
 * 不复制月/日解析逻辑。
 */

interface Props {
  stock: StockRecord;
}

interface ProfileRow {
  label: string;
  value: string;
}

/** `2001-08-27` → `2001年8月27日`；不可解析时原样返回（不吞掉后端的怪值）。 */
function formatListDate(iso: string): string {
  const year = iso.slice(0, 4);
  return /^\d{4}$/.test(year) ? `${year}年${formatCnDate(iso)}` : iso;
}

/** 只保留"真的有值"的行（`null` / `undefined` / 纯空白一律丢弃）。 */
function profileRows(stock: StockRecord): ProfileRow[] {
  const candidates: Array<{ label: string; value: string | undefined }> = [
    { label: "全称", value: stock.fullName },
    { label: "上市日期", value: stock.listDate ? formatListDate(stock.listDate) : undefined },
    { label: "地区", value: stock.area },
    { label: "实控人", value: stock.actName },
  ];
  return candidates.filter(
    (row): row is ProfileRow => typeof row.value === "string" && row.value.trim() !== ""
  );
}

export function CompanyProfile({ stock }: Props) {
  const rows = profileRows(stock);
  // 四项全缺 ⇒ 没有任何可陈述的事实，整卡不渲染（不是一张空卡）。
  if (rows.length === 0) return null;

  return (
    <Card size="small" title="公司概况" data-testid="company-profile">
      <dl className="company-profile">
        {rows.map((row) => (
          <div className="company-profile__row" key={row.label} data-field={row.label}>
            <dt className="company-profile__label">{row.label}</dt>
            <dd className="company-profile__value">{row.value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
