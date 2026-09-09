/** 数据覆盖矩阵：契约 docs/design/landing-market-theme.md §5 的 10 行原文 */
const COVERAGE_ROWS: Array<{ domain: string; content: string; freq: string; source: string }> = [
  { domain: "A股行情", content: "沪深北 5,500+ 只日线 OHLCV", freq: "日度（盘后）", source: "TuShare" },
  { domain: "估值指标", content: "PE/PB/换手/市值/量比", freq: "日度", source: "TuShare" },
  { domain: "全球指数", content: "上证/深证/创业板/恒生/日经/KOSPI/标普/纳指等", freq: "实时快照+日度", source: "东财/AKShare" },
  { domain: "行业体系", content: "申万 31 L1/全层级分类+成分", freq: "静态+季度", source: "申万 2021 版" },
  { domain: "资金流向", content: "大盘四档/板块/个股主力净流入", freq: "盘中+盘后", source: "东财" },
  { domain: "北向资金", content: "沪深港通净流入", freq: "盘后", source: "东财" },
  { domain: "龙虎榜/大宗/解禁/回购", content: "每日榜单", freq: "盘后", source: "东财" },
  { domain: "ETF/可转债", content: "日线行情", freq: "日度", source: "TuShare" },
  { domain: "财务三表", content: "利润/资产负债/现金流+衍生指标", freq: "季度", source: "TuShare/巨潮" },
  { domain: "行业产能指标", content: "生猪价格/能繁存栏/猪粮比等（多源分级）", freq: "日度/月度", source: "统计局/协会/生意社/期货" },
];

const HEADS = ["数据域", "内容", "频率", "来源"];

/** 数据版图：统计带 + 10 行覆盖矩阵（级别列统一绿色「免费」徽章） */
export function DataCoverage() {
  return (
    <section id="data" className="landing-section">
      <div className="landing-container">
        <div className="landing-section-head">
          <h2 className="landing-section-title">数据版图</h2>
          <p className="landing-section-sub">来源与频率如实标注，不夸大口径</p>
        </div>

        <div className="landing-stats-band">
          <div className="landing-stat">
            <div className="landing-stat-num">10</div>
            <div className="landing-stat-label">大数据域</div>
          </div>
          <div className="landing-stat">
            <div className="landing-stat-num">5,500+</div>
            <div className="landing-stat-label">标的</div>
          </div>
          <div className="landing-stat">
            <div className="landing-stat-num">4</div>
            <div className="landing-stat-label">级数据权威分级</div>
          </div>
          <div className="landing-stat">
            <div className="landing-stat-num">100%</div>
            <div className="landing-stat-label">全部免费</div>
          </div>
        </div>

        <div className="landing-coverage-table" role="table" aria-label="数据覆盖矩阵">
          <div className="landing-coverage-row landing-coverage-row--head" role="row">
            {HEADS.map((h) => (
              <div key={h} role="columnheader">
                {h}
              </div>
            ))}
            <div role="columnheader">级别</div>
          </div>
          {COVERAGE_ROWS.map((row) => (
            <div key={row.domain} className="landing-coverage-row" role="row">
              <div className="landing-coverage-domain" role="cell">
                {row.domain}
              </div>
              <div className="landing-coverage-cell" role="cell">
                {row.content}
              </div>
              <div className="landing-coverage-cell" role="cell">
                {row.freq}
              </div>
              <div className="landing-coverage-cell" role="cell">
                {row.source}
              </div>
              <div role="cell">
                <span className="landing-free-badge">免费</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
