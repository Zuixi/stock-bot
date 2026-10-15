import { formatCnDate } from "@/shared/ui";
import type { SentimentComposite } from "../api";
import { NOTE_COMPOSITE } from "../moduleTable";

/**
 * 综合情绪指数 hero（S1.13）——整页**唯一**的卡片、唯一的"重"元素（`borderRadius: 6`，无阴影）。
 *
 * 它是这个页面存在的理由：/(market) 已有一眼可读的温度计，这里要能**复核**。
 * 四件事按顺序上屏（顺序是批准稿的一部分）：
 *  ① 合成值；② 0–100 分位轨（10 / 90 两条极值带 + 当日标记）；③ 「本日由 N/11 条腿合成 · 生效权重 w」
 *  （Ruling 66 的硬要求：最新一日的值是"薄"的，不许被读成满权重）；④ `window` / `sample_count` 窗口标签。
 *
 * 三种诚实退化（**都不给假位置、不给 0**）：
 *  - 端点未就绪 / 读数失败 → 「暂不可得」+ 端点名，不显示任何占位数字；
 *  - `insufficient_history` 或 `percentile=null` → 显示「样本不足」，轨道上**不画标记**；
 *  - `value=null` → 大数字区显示 `--`，其余披露照常上屏（"算不出"与"等于 0"是两件事）。
 */

function fmtWeight(v: number | null): string {
  return v == null ? "--" : v.toFixed(2);
}

export interface CompositeHeroProps {
  data?: SentimentComposite | null;
  isLoading: boolean;
  isError: boolean;
}

export function CompositeHero({ data, isLoading, isError }: CompositeHeroProps) {
  const p = data?.percentile ?? null;
  const hasPercentile = p != null;
  // 标记的位置：0–100 分位；贴边时留 0.3% 余量，防 2px 标记溢出轨道
  const markerLeft = hasPercentile ? Math.min(99.7, Math.max(0.3, p)) : null;
  const tone = !hasPercentile ? "mid" : p < 10 ? "cold" : p > 90 ? "hot" : "mid";
  // 后端两档"没有分位"：① 有效样本 < 60；② 当期 value 为 NULL（没有今日值可分位）。
  // 两档都不给数字、不画标记，但文案必须分开——把②说成"样本不足"是另一种谎。
  const noPercentileText = data?.insufficientHistory
    ? "样本不足：不给历史分位（也不在轨道上画标记）"
    : "当期 value 为 NULL，没有可定位的今日值：不给历史分位、也不在轨道上画标记";
  const railLabel = hasPercentile
    ? `0 至 100 历史分位轨道；低于 10 为极冷、高于 90 为极热；当日标记在 ${p.toFixed(1)}`
    : "0 至 100 历史分位轨道；低于 10 为极冷、高于 90 为极热；当日不给分位标记";
  // 旧口径行（extra 缺 participants / n_constituents）后端给 legs=null / 计数 0：
  // 这时"由 N/11 条腿合成"这句话没有事实可依，明说"未登记"而不是印一个 0/0。
  const legsRegistered = (data?.totalLegs ?? 0) > 0 || (data?.legs.length ?? 0) > 0;

  return (
    <section className="sent-hero" id="sentiment-module-composite" data-testid="sentiment-hero">
      <header className="sent-hero__head">
        <h3 className="sent-hero__title">市场情绪综合指数</h3>
        <span className="sent-hero__asof num">
          {data?.asOf ? `数据截至 ${formatCnDate(data.asOf)}` : "无口径日"}
        </span>
        {data?.caliberVersion ? <span className="sent-hero__caliber num">口径 {data.caliberVersion}</span> : null}
      </header>

      {isLoading ? (
        <p className="sent-hero__state" data-testid="sentiment-composite-loading">
          正在读取综合指数…
        </p>
      ) : isError || !data ? (
        <p className="sent-hero__state" data-testid="sentiment-composite-unavailable">
          综合情绪指数暂不可得：端点 /api/v1/market/sentiment/composite 未就绪或读数失败。本页不显示任何占位数字，
          下方各模块的指标仍逐条标注口径与数据 vintage。
        </p>
      ) : (
        <>
          <div className="sent-hero__value-row">
            <span className="sent-hero__value num" data-testid="sentiment-composite-value">
              {data.value == null ? "--" : data.value.toFixed(2)}
            </span>
            <span className="sent-hero__unit">分</span>
            {data.value == null ? (
              <span className="sent-missing">本期 value = NULL（算不出，不是 0 分）</span>
            ) : (
              <span className="sent-hero__raw num">原始值 {data.value}</span>
            )}
          </div>

          <div className="sent-rail" data-testid="sentiment-composite-rail" role="img" aria-label={railLabel}>
            <span className="sent-rail__band sent-rail__band--cold" />
            <span className="sent-rail__band sent-rail__band--hot" />
            {markerLeft != null ? (
              <span
                className={`sent-rail__marker sent-rail__marker--${tone}`}
                style={{ left: `${markerLeft}%` }}
                data-testid="sentiment-composite-marker"
              />
            ) : null}
          </div>
          <div className="sent-rail__scale num">
            <span>0</span>
            <span className="sent-rail__legend sent-rail__legend--cold">低于 10 极冷</span>
            <span className="sent-rail__legend sent-rail__legend--hot">高于 90 极热</span>
            <span>100</span>
          </div>

          {hasPercentile ? (
            <p className={`sent-hero__percentile sent-hero__percentile--${tone} num`}>
              <span data-testid="sentiment-composite-percentile">历史分位 {p.toFixed(1)}</span>
            </p>
          ) : (
            <p className="sent-hero__percentile sent-hero__percentile--none num">
              <span
                data-testid={
                  data.insufficientHistory
                    ? "sentiment-composite-insufficient"
                    : "sentiment-composite-no-position"
                }
              >
                {noPercentileText}
              </span>
            </p>
          )}

          {legsRegistered ? (
            <p className="sent-hero__legs num" data-testid="sentiment-composite-legs-line">
              本日由 {data.legCount ?? "--"}/{data.totalLegs ?? "--"} 条腿合成 · 生效权重{" "}
              {fmtWeight(data.effectiveWeight)}
              {data.minEffectiveWeight == null ? null : `（下限 ${fmtWeight(data.minEffectiveWeight)}）`}
            </p>
          ) : (
            <p className="sent-hero__legs num" data-testid="sentiment-composite-legs-unregistered">
              该行未登记参与腿与生效权重（extra 缺 participants / n_constituents），无法披露「N/11 条腿」。
            </p>
          )}
          {data.legs.length > 0 ? (
            <p className="sent-hero__legs-list num">参与腿：{data.legs.join("、")}</p>
          ) : null}
          <p className="sent-hero__window num">
            {data.window == null && data.sampleCount == null
              ? "窗口信息未下发"
              : `分位窗口 ${data.window ?? "--"} 个交易日 · 有效样本 ${data.sampleCount ?? "--"}`}
          </p>
        </>
      )}

      <p className="sent-hero__caliber num" data-testid={`sentiment-caliber-${NOTE_COMPOSITE.id}`}>
        <span className="sent-caliber__src">{NOTE_COMPOSITE.source}</span>
        <span className="sent-caliber__text">{NOTE_COMPOSITE.text}</span>
      </p>
    </section>
  );
}
