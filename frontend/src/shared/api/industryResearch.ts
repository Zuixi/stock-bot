import { apiGet, apiPost } from "./client";

// ── Backend payloads (snake_case) ─────────────────────────────────────

export interface BackendMetricDelta {
  pct: number | null;
  direction: "up" | "down" | "flat";
  label: string;
}

export interface BackendMetricLatest {
  metric_key: string;
  name: string;
  value: number | null;
  unit: string | null;
  tier: string;
  source: string | null;
  freq: string | null;
  period: string | null;
  delta: BackendMetricDelta | null;
  warn: string | null;
  warn_severity: string | null;
  spark: number[] | null;
  description: string;
}

export interface BackendReference {
  label: string;
  value: number;
  note: string | null;
  effective_from: string;
}

export interface BackendTrendSeries {
  periods: string[];
  series: Record<string, (number | null)[]>;
  reference: BackendReference | null;
}

export interface BackendPhase {
  key: string;
  label: string;
  desc: string;
  active: boolean;
}

export interface BackendPositionSlice {
  name: string;
  role: string;
  desc: string;
  pct: number;
  color: string;
}

export interface BackendSignal {
  signal_type: string;
  phase: string | null;
  effective_date: string;
  reason: string | null;
  positions: BackendPositionSlice[];
}

export interface BackendCycle {
  phase: string;
  phase_index: number;
  phases: BackendPhase[];
  reasons: string[];
  basis: Record<string, unknown>;
}

export interface BackendMetricQuality {
  metric_key: string;
  status: string;
  source: string | null;
  freq: string | null;
  period: string | null;
  age_days: number | null;
  reason: string | null;
  entity_coverage: number | null;
}

export interface BackendDataQuality {
  as_of: string;
  status: string;
  signal_ready: boolean;
  ready_count: number;
  missing_count: number;
  stale_count: number;
  rejected_count: number;
  partial_count: number;
  details: BackendMetricQuality[];
}

export interface BackendEvaluationCriterion {
  metric_key: string;
  status: string;
  weight?: number;
  score: string | null;
  start_value?: string;
  end_value?: string;
  change_pct?: string | null;
}

export interface BackendSignalEvaluation {
  horizon_days: number;
  status: string;
  target_date: string;
  score: number | null;
  criteria_results: BackendEvaluationCriterion[];
  insufficient_reasons: string[];
  evaluated_at: string | null;
}

export interface BackendSignalEvent {
  event_date: string;
  event_sequence: number;
  signal_type: string;
  phase: string;
  previous_signal_type: string | null;
  previous_phase: string | null;
  rule_version: string;
  verification_supported: boolean;
  evaluations: BackendSignalEvaluation[];
}

export interface BackendVerificationSummary {
  completed_directional_evaluations: number;
  confirmed: number;
  partially_confirmed: number;
  invalidated: number;
  inconclusive: number;
  pending: number;
  accuracy_pct: number | null;
}

export interface BackendDashboard {
  industry: {
    key: string;
    name: string;
    description: string;
    sw_l3_codes: string[];
  };
  as_of: string;
  data_source: string;
  strip: BackendMetricLatest[];
  quick_view: BackendMetricLatest[];
  trends: Record<string, BackendTrendSeries>;
  cycle: BackendCycle | null;
  signal: BackendSignal | null;
  signal_is_stale: boolean;
  data_quality: BackendDataQuality;
  signal_events: BackendSignalEvent[];
  verification_summary: BackendVerificationSummary;
  signal_history: BackendSignal[];
}

export interface BackendIndustrySummary {
  key: string;
  name: string;
  description: string;
  sw_l3_codes: string[];
  metric_total: number;
  metric_with_data: number;
  coverage: Record<string, boolean>;
  last_period: string | null;
  phase: string | null;
  signal_type: string | null;
  signal_date: string | null;
}

export interface BackendCompanyColumn {
  key: string;
  label: string;
  unit: string | null;
  numeric: boolean;
  tier: string | null;
}

export interface BackendCompanyRow {
  symbol: string;
  name: string;
  latest_price: number | null;
  total_mv_yi: number | null;
  pe_ttm: number | null;
  pb: number | null;
  has_company_data: boolean;
  metrics: Record<string, number | null>;
}

export interface BackendIndustryCompanies {
  industry: {
    key: string;
    name: string;
    description: string;
    sw_l3_codes: string[];
  };
  columns: BackendCompanyColumn[];
  rows: BackendCompanyRow[];
}

export interface BackendSecurityDailyPoint {
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  pre_close: number | null;
  volume: number | null;
  amount: number | null;
}

export interface BackendSecuritySeries {
  ts_code: string;
  name: string | null;
  latest: BackendSecurityDailyPoint | null;
  change_pct: number | null;
  series: BackendSecurityDailyPoint[];
}

export interface BackendIndustrySecurities {
  type: "etf" | "cb";
  codes: BackendSecuritySeries[];
}

// ── 知识库（P6）：payload 透传（后端 JSONB 内容行，形状见 industry_knowledge_seed） ──

export interface KnowledgeOrg {
  name: string;
  group: string; // 官方 | 协会 | 数据平台 | 期货
  tier: string;  // SourceBadge 权威性层级（official/highfreq/...）
  desc: string;
  urls: string[];
}

export interface KnowledgePrinciple {
  title: string;
  items: string[];
}

export interface MindmapNode {
  name: string;
  children?: MindmapNode[];
}

export interface IndustryKnowledge {
  org: KnowledgeOrg[];
  principle: KnowledgePrinciple | null;
  mindmap: MindmapNode | null;
}

// ── UI models (camelCase) ─────────────────────────────────────────────

export interface MetricDelta {
  pct: number | null;
  direction: "up" | "down" | "flat";
  label: string;
}

export interface MetricLatest {
  metricKey: string;
  name: string;
  value: number | null;
  unit: string | null;
  tier: string;
  source: string | null;
  freq: string | null;
  period: string | null;
  delta: MetricDelta | null;
  warn: string | null;
  warnSeverity: string | null;
  spark: number[] | null;
  description: string;
}

export interface Reference {
  label: string;
  value: number;
  note: string | null;
  effectiveFrom: string;
}

export interface TrendSeries {
  periods: string[];
  series: Record<string, (number | null)[]>;
  reference: Reference | null;
}

export interface Phase {
  key: string;
  label: string;
  desc: string;
  active: boolean;
}

export interface PositionSlice {
  name: string;
  role: string;
  desc: string;
  pct: number;
  color: string;
}

export interface Signal {
  signalType: string;
  phase: string | null;
  effectiveDate: string;
  reason: string | null;
  positions: PositionSlice[];
}

export interface Cycle {
  phase: string;
  phaseIndex: number;
  phases: Phase[];
  reasons: string[];
  basis: Record<string, unknown>;
}

export interface MetricQuality {
  metricKey: string;
  status: string;
  source: string | null;
  freq: string | null;
  period: string | null;
  ageDays: number | null;
  reason: string | null;
  entityCoverage: number | null;
}

export interface DataQuality {
  asOf: string;
  status: string;
  signalReady: boolean;
  readyCount: number;
  missingCount: number;
  staleCount: number;
  rejectedCount: number;
  partialCount: number;
  details: MetricQuality[];
}

export interface EvaluationCriterion {
  metricKey: string;
  status: string;
  weight?: number;
  score: string | null;
  startValue?: string;
  endValue?: string;
  changePct?: string | null;
}

export interface SignalEvaluation {
  horizonDays: number;
  status: string;
  targetDate: string;
  score: number | null;
  criteriaResults: EvaluationCriterion[];
  insufficientReasons: string[];
  evaluatedAt: string | null;
}

export interface SignalEvent {
  eventDate: string;
  eventSequence: number;
  signalType: string;
  phase: string;
  previousSignalType: string | null;
  previousPhase: string | null;
  ruleVersion: string;
  verificationSupported: boolean;
  evaluations: SignalEvaluation[];
}

export interface VerificationSummary {
  completedDirectionalEvaluations: number;
  confirmed: number;
  partiallyConfirmed: number;
  invalidated: number;
  inconclusive: number;
  pending: number;
  accuracyPct: number | null;
}

export interface Dashboard {
  industry: { key: string; name: string; description: string; swL3Codes: string[] };
  asOf: string;
  dataSource: string;
  strip: MetricLatest[];
  quickView: MetricLatest[];
  trends: Record<string, TrendSeries>;
  cycle: Cycle | null;
  signal: Signal | null;
  signalIsStale: boolean;
  dataQuality: DataQuality;
  signalEvents: SignalEvent[];
  verificationSummary: VerificationSummary;
  signalHistory: Signal[];
}

export interface IndustrySummary {
  key: string;
  name: string;
  description: string;
  swL3Codes: string[];
  metricTotal: number;
  metricWithData: number;
  coverage: Record<string, boolean>;
  lastPeriod: string | null;
  phase: string | null;
  signalType: string | null;
  signalDate: string | null;
}

export interface CompanyColumn {
  key: string;
  label: string;
  unit: string | null;
  numeric: boolean;
  tier: string | null;
}

export interface CompanyRow {
  symbol: string;
  name: string;
  latestPrice: number | null;
  totalMvYi: number | null;
  peTtm: number | null;
  pb: number | null;
  hasCompanyData: boolean;
  metrics: Record<string, number | null>;
}

export interface IndustryCompanies {
  industry: { key: string; name: string; description: string; swL3Codes: string[] };
  columns: CompanyColumn[];
  rows: CompanyRow[];
}

export interface SecurityDailyPoint {
  tradeDate: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  preClose: number | null;
  volume: number | null;
  amount: number | null;
}

export interface SecuritySeries {
  tsCode: string;
  name: string | null;
  latest: SecurityDailyPoint | null;
  changePct: number | null;
  series: SecurityDailyPoint[];
}

export interface IndustrySecurities {
  type: "etf" | "cb";
  codes: SecuritySeries[];
}

// ── Mappers ───────────────────────────────────────────────────────────

function mapMetricLatest(m: BackendMetricLatest): MetricLatest {
  return {
    metricKey: m.metric_key,
    name: m.name,
    value: m.value,
    unit: m.unit,
    tier: m.tier,
    source: m.source,
    freq: m.freq,
    period: m.period,
    delta: m.delta ? { pct: m.delta.pct, direction: m.delta.direction, label: m.delta.label } : null,
    warn: m.warn,
    warnSeverity: m.warn_severity,
    spark: m.spark,
    description: m.description,
  };
}

function mapTrendSeries(t: BackendTrendSeries): TrendSeries {
  return {
    periods: t.periods,
    series: t.series,
    reference: t.reference
      ? {
          label: t.reference.label,
          value: t.reference.value,
          note: t.reference.note,
          effectiveFrom: t.reference.effective_from,
        }
      : null,
  };
}

function mapSignal(s: BackendSignal): Signal {
  return {
    signalType: s.signal_type,
    phase: s.phase,
    effectiveDate: s.effective_date,
    reason: s.reason,
    positions: s.positions,
  };
}

function mapMetricQuality(q: BackendMetricQuality): MetricQuality {
  return {
    metricKey: q.metric_key,
    status: q.status,
    source: q.source,
    freq: q.freq,
    period: q.period,
    ageDays: q.age_days,
    reason: q.reason,
    entityCoverage: q.entity_coverage,
  };
}

function mapDataQuality(q: BackendDataQuality): DataQuality {
  return {
    asOf: q.as_of,
    status: q.status,
    signalReady: q.signal_ready,
    readyCount: q.ready_count,
    missingCount: q.missing_count,
    staleCount: q.stale_count,
    rejectedCount: q.rejected_count,
    partialCount: q.partial_count,
    details: q.details.map(mapMetricQuality),
  };
}

function mapSignalEvaluation(e: BackendSignalEvaluation): SignalEvaluation {
  return {
    horizonDays: e.horizon_days,
    status: e.status,
    targetDate: e.target_date,
    score: e.score,
    criteriaResults: e.criteria_results.map((criterion) => ({
      metricKey: criterion.metric_key,
      status: criterion.status,
      weight: criterion.weight,
      score: criterion.score,
      startValue: criterion.start_value,
      endValue: criterion.end_value,
      changePct: criterion.change_pct,
    })),
    insufficientReasons: e.insufficient_reasons,
    evaluatedAt: e.evaluated_at,
  };
}

function mapSignalEvent(e: BackendSignalEvent): SignalEvent {
  return {
    eventDate: e.event_date,
    eventSequence: e.event_sequence,
    signalType: e.signal_type,
    phase: e.phase,
    previousSignalType: e.previous_signal_type,
    previousPhase: e.previous_phase,
    ruleVersion: e.rule_version,
    verificationSupported: e.verification_supported,
    evaluations: e.evaluations.map(mapSignalEvaluation),
  };
}

function mapVerificationSummary(s: BackendVerificationSummary): VerificationSummary {
  return {
    completedDirectionalEvaluations: s.completed_directional_evaluations,
    confirmed: s.confirmed,
    partiallyConfirmed: s.partially_confirmed,
    invalidated: s.invalidated,
    inconclusive: s.inconclusive,
    pending: s.pending,
    accuracyPct: s.accuracy_pct,
  };
}

function mapDashboard(d: BackendDashboard): Dashboard {
  return {
    industry: {
      key: d.industry.key,
      name: d.industry.name,
      description: d.industry.description,
      swL3Codes: d.industry.sw_l3_codes,
    },
    asOf: d.as_of,
    dataSource: d.data_source,
    strip: d.strip.map(mapMetricLatest),
    quickView: d.quick_view.map(mapMetricLatest),
    trends: Object.fromEntries(
      Object.entries(d.trends).map(([k, v]) => [k, mapTrendSeries(v)])
    ),
    cycle: d.cycle
      ? {
          phase: d.cycle.phase,
          phaseIndex: d.cycle.phase_index,
          phases: d.cycle.phases,
          reasons: d.cycle.reasons,
          basis: d.cycle.basis,
        }
      : null,
    signal: d.signal ? mapSignal(d.signal) : null,
    signalIsStale: d.signal_is_stale,
    dataQuality: mapDataQuality(d.data_quality),
    signalEvents: d.signal_events.map(mapSignalEvent),
    verificationSummary: mapVerificationSummary(d.verification_summary),
    signalHistory: d.signal_history.map(mapSignal),
  };
}

function mapCompanyRow(r: BackendCompanyRow): CompanyRow {
  return {
    symbol: r.symbol,
    name: r.name,
    latestPrice: r.latest_price,
    totalMvYi: r.total_mv_yi,
    peTtm: r.pe_ttm,
    pb: r.pb,
    hasCompanyData: r.has_company_data,
    metrics: r.metrics,
  };
}

function mapIndustryCompanies(c: BackendIndustryCompanies): IndustryCompanies {
  return {
    industry: {
      key: c.industry.key,
      name: c.industry.name,
      description: c.industry.description,
      swL3Codes: c.industry.sw_l3_codes,
    },
    columns: c.columns.map((col) => ({
      key: col.key,
      label: col.label,
      unit: col.unit,
      numeric: col.numeric,
      tier: col.tier,
    })),
    rows: c.rows.map(mapCompanyRow),
  };
}

function mapSecurityPoint(p: BackendSecurityDailyPoint): SecurityDailyPoint {
  return {
    tradeDate: p.trade_date,
    open: p.open,
    high: p.high,
    low: p.low,
    close: p.close,
    preClose: p.pre_close,
    volume: p.volume,
    amount: p.amount,
  };
}

function mapIndustrySecurities(s: BackendIndustrySecurities): IndustrySecurities {
  return {
    type: s.type,
    codes: s.codes.map((c) => ({
      tsCode: c.ts_code,
      name: c.name,
      latest: c.latest ? mapSecurityPoint(c.latest) : null,
      changePct: c.change_pct,
      series: c.series.map(mapSecurityPoint),
    })),
  };
}

// ── Fetchers ──────────────────────────────────────────────────────────

export function fetchIndustries(): Promise<IndustrySummary[]> {
  return apiGet<BackendIndustrySummary[]>("/api/v1/industries").then((rows) =>
    rows.map((r) => ({
      key: r.key,
      name: r.name,
      description: r.description,
      swL3Codes: r.sw_l3_codes,
      metricTotal: r.metric_total,
      metricWithData: r.metric_with_data,
      coverage: r.coverage,
      lastPeriod: r.last_period,
      phase: r.phase,
      signalType: r.signal_type,
      signalDate: r.signal_date,
    }))
  );
}

export function fetchIndustryDashboard(industryKey: string): Promise<Dashboard> {
  return apiGet<BackendDashboard>(`/api/v1/industries/${industryKey}/dashboard`).then(mapDashboard);
}

export function fetchIndustryCompanies(industryKey: string): Promise<IndustryCompanies> {
  return apiGet<BackendIndustryCompanies>(`/api/v1/industries/${industryKey}/companies`).then(
    mapIndustryCompanies
  );
}

export function fetchIndustrySecurities(
  industryKey: string,
  type: "etf" | "cb"
): Promise<IndustrySecurities> {
  return apiGet<BackendIndustrySecurities>(
    `/api/v1/industries/${industryKey}/securities?type=${type}`
  ).then(mapIndustrySecurities);
}

export function fetchIndustryKnowledge(industryKey: string): Promise<IndustryKnowledge> {
  // payload 为后端内容表透传（字段名与 UI 模型一致），无需 mapper
  return apiGet<IndustryKnowledge>(`/api/v1/industries/${industryKey}/knowledge`);
}

export function triggerFetchSecurities(
  industryKey: string,
  backfillDays?: number
): Promise<{ id: string; status: string }> {
  return apiPost("/api/v1/tasks/fetch-securities", {
    industry_key: industryKey,
    ...(backfillDays ? { backfill_days: backfillDays } : {}),
  });
}

export function triggerFetchIndustryMetrics(
  industryKey: string,
  source?: "mock" | "akshare"
): Promise<{ id: string; status: string }> {
  return apiPost("/api/v1/tasks/fetch-industry-metrics", {
    industry_key: industryKey,
    ...(source ? { source } : {}),
  });
}
