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

export interface BackendDashboard {
  industry: {
    key: string;
    name: string;
    description: string;
    sw_l3_codes: string[];
  };
  as_of: string;
  strip: BackendMetricLatest[];
  quick_view: BackendMetricLatest[];
  trends: Record<string, BackendTrendSeries>;
  cycle: BackendCycle;
  signal: BackendSignal;
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

export interface Dashboard {
  industry: { key: string; name: string; description: string; swL3Codes: string[] };
  asOf: string;
  strip: MetricLatest[];
  quickView: MetricLatest[];
  trends: Record<string, TrendSeries>;
  cycle: Cycle;
  signal: Signal;
  signalHistory: Signal[];
}

export interface IndustrySummary {
  key: string;
  name: string;
  description: string;
  swL3Codes: string[];
  metricTotal: number;
  metricWithData: number;
  lastPeriod: string | null;
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

function mapDashboard(d: BackendDashboard): Dashboard {
  return {
    industry: {
      key: d.industry.key,
      name: d.industry.name,
      description: d.industry.description,
      swL3Codes: d.industry.sw_l3_codes,
    },
    asOf: d.as_of,
    strip: d.strip.map(mapMetricLatest),
    quickView: d.quick_view.map(mapMetricLatest),
    trends: Object.fromEntries(
      Object.entries(d.trends).map(([k, v]) => [k, mapTrendSeries(v)])
    ),
    cycle: {
      phase: d.cycle.phase,
      phaseIndex: d.cycle.phase_index,
      phases: d.cycle.phases,
      reasons: d.cycle.reasons,
      basis: d.cycle.basis,
    },
    signal: mapSignal(d.signal),
    signalHistory: d.signal_history.map(mapSignal),
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
      lastPeriod: r.last_period,
    }))
  );
}

export function fetchIndustryDashboard(industryKey: string): Promise<Dashboard> {
  return apiGet<BackendDashboard>(`/api/v1/industries/${industryKey}/dashboard`).then(mapDashboard);
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
