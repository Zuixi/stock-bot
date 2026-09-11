import "./DataCoverageMatrix.css";

interface CoverageRow {
  domain: string;
  content: string;
  freq: string;
  source: string;
  /**
   * 尚未上线：来源/频率留空、徽章改「规划中」。
   * 北向资金 `northbound_daily` 实测全表 0 行（非仅滞后），公开文案不得把规划中的
   * 数据域算进「已上线」统计，也不得标注来源（无源可溯即不署名）。
   */
  planned?: boolean;
}

/** 数据覆盖矩阵：契约 docs/design/landing-market-theme.md §5 的 10 行 */
const COVERAGE_ROWS: CoverageRow[] = [
  { domain: "A股行情", content: "沪深北 5,500+ 只日线 OHLCV", freq: "日度（盘后）", source: "TuShare" },
  { domain: "估值指标", content: "PE/PB/换手/市值/量比", freq: "日度", source: "TuShare" },
  { domain: "全球指数", content: "上证/深证/创业板/恒生/日经/KOSPI/标普/纳指等", freq: "实时快照+日度", source: "东财/AKShare" },
  { domain: "行业体系", content: "申万 31 L1/全层级分类+成分", freq: "静态+季度", source: "申万 2021 版" },
  { domain: "资金流向", content: "大盘四档/板块/个股主力净流入", freq: "盘中+盘后", source: "东财" },
  // 规划中：北向 `northbound_daily` 0 行，首页已整卡移除，公开矩阵不宣称已覆盖
  { domain: "北向资金", content: "沪深港通净流入", freq: "—", source: "—", planned: true },
  { domain: "龙虎榜/大宗/解禁/回购", content: "每日榜单", freq: "盘后", source: "东财" },
  { domain: "ETF/可转债", content: "日线行情", freq: "日度", source: "TuShare" },
  { domain: "财务三表", content: "利润/资产负债/现金流+衍生指标", freq: "季度", source: "TuShare/巨潮" },
  { domain: "行业产能指标", content: "生猪价格/能繁存栏/猪粮比等（多源分级）", freq: "日度/月度", source: "统计局/协会/生意社/期货" },
];

const HEADS = ["数据域", "内容", "频率", "来源"];

/**
 * 统计带口径 = 实际已上线的 9 个数据域（10 行中北向为规划中，不计入）。
 * 上游契约 §5 的「10 大数据域」含规划中一项，公开文案改为「已上线」以免夸大口径。
 */
const STATS: Array<{ num: string; label: string }> = [
  { num: "9", label: "已上线数据域" },
  { num: "5,500+", label: "标的" },
  { num: "4", label: "级数据权威分级" },
  { num: "100%", label: "已上线数据免费" },
];

interface Props {
  /** 统计带（已上线数据域 · 5,500+ 标的 · …）：landing 展示，市场页精简矩阵可省 */
  withStats?: boolean;
}

/**
 * 数据版图：统计带（可选）+ 10 行覆盖矩阵（级别列统一绿色「免费」徽章，规划中为灰色）。
 * Stage C 自 landing/sections/DataCoverage 提取为共享组件，宣传页与市场页共用。
 */
export function DataCoverageMatrix({ withStats = true }: Props) {
  return (
    <div>
      {withStats && (
        <div className="dc-stats-band">
          {STATS.map((s) => (
            <div className="dc-stat" key={s.label}>
              <div className="dc-stat-num">{s.num}</div>
              <div className="dc-stat-label">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      <div className="dc-table" role="table" aria-label="数据覆盖矩阵">
        <div className="dc-row dc-row--head" role="row">
          {HEADS.map((h) => (
            <div key={h} role="columnheader">
              {h}
            </div>
          ))}
          <div role="columnheader">级别</div>
        </div>
        {COVERAGE_ROWS.map((row) => (
          <div key={row.domain} className="dc-row" role="row">
            <div className="dc-domain" role="cell">
              {row.domain}
            </div>
            <div className="dc-cell" role="cell">
              {row.content}
            </div>
            <div className="dc-cell" role="cell">
              {row.freq}
            </div>
            <div className="dc-cell" role="cell">
              {row.source}
            </div>
            <div role="cell">
              {row.planned ? (
                <span className="dc-planned-badge">规划中</span>
              ) : (
                <span className="dc-free-badge">免费</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
