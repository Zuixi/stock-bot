import { DataRow } from "@/shared/ui";
import type { SentimentKpis } from "@/shared/api/limitUp";

interface Props {
  kpis: SentimentKpis | null | undefined;
}

/** 晋级率行：小样本时数值后加 `（n=N，样本小）` 并弱化为次级色。 */
function PromoRow({
  title,
  value,
  n,
  noisy,
}: {
  title: string;
  value: number | null;
  n: number;
  noisy: boolean;
}) {
  return (
    <div className="datarow">
      <span className="datarow__id">
        <span className="datarow__names">
          <span className="datarow__title">{title}</span>
        </span>
      </span>
      <span className="datarow__val">
        <span
          className="datarow__price"
          style={noisy ? { color: "var(--text-secondary)" } : undefined}
        >
          {value == null ? "--" : `${value.toFixed(2)}%`}
          {noisy && value != null ? (
            <span className="datarow__unit"> （n={n}，样本小）</span>
          ) : null}
        </span>
      </span>
    </div>
  );
}

/**
 * 情绪温度计 KPI 面板：涨停/跌停/炸板、炸板率、昨日涨停均值、1进2/2进3、最高板。
 * 缺失值一律渲染 `--`，绝不 `?? 0`；`kpis` 可为 null（如端点降级），整板退化为 `--`。
 */
export function SentimentHeader({ kpis }: Props) {
  const k = kpis;
  return (
    <div
      className="sentiment-header"
      style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", columnGap: 24 }}
    >
      <DataRow title="涨停家数" value={k == null ? undefined : String(k.ztCount)} unit="家" valuePlaceholder="--" />
      <DataRow title="跌停家数" value={k == null ? undefined : String(k.dtCount)} unit="家" valuePlaceholder="--" />
      <DataRow title="炸板家数" value={k == null ? undefined : String(k.zbCount)} unit="家" valuePlaceholder="--" />
      <DataRow
        title="炸板率"
        value={k?.brokenRate == null ? undefined : k.brokenRate.toFixed(2)}
        unit="%"
        valuePlaceholder="--"
      />
      <DataRow title="昨日涨停均值" delta={k?.yztAvgPct ?? null} />
      <PromoRow title="1进2晋级率" value={k?.promo1to2 ?? null} n={k?.promo1to2N ?? 0} noisy={k?.promo1to2Noisy ?? false} />
      <PromoRow title="2进3晋级率" value={k?.promo2to3 ?? null} n={k?.promo2to3N ?? 0} noisy={k?.promo2to3Noisy ?? false} />
      <DataRow title="最高板" value={k == null ? undefined : String(k.maxStreak)} unit="板" valuePlaceholder="--" />
    </div>
  );
}
