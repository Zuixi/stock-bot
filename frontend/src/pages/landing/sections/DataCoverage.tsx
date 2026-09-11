import { DataCoverageMatrix } from "@/features/market/components/DataCoverageMatrix";

/** 数据版图区块（landing 第 6 区块）：矩阵本体已提取至 features/market 共享（Stage C） */
export function DataCoverage() {
  return (
    <section id="data" className="landing-section">
      <div className="landing-container">
        <div className="landing-section-head">
          <h2 className="landing-section-title">数据版图</h2>
          <p className="landing-section-sub">来源与频率如实标注，不夸大口径</p>
        </div>
        <DataCoverageMatrix />
      </div>
    </section>
  );
}
