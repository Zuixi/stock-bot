import { apiGet } from "./client";

/** 后端 `GET /api/v1/provenance/{metric_key}` 的响应（P7）。 */

export interface ProvenanceMetric {
  key: string;
  label: string;
  unit: string;
  caliber_version: string;
  definition: string;
  consumers: string[];
}

export interface ProvenanceSource {
  table: string;
  domain: string;
  rows: number;
  batch_id?: number | null;
  raw_path?: string | null;
  content_hash?: string | null;
  batch_status?: string | null;
}

export interface ProvenanceQuality {
  key: string;
  domain: string;
  severity: "block" | "warn";
  status: "pass" | "warn" | "fail" | "error";
  observed: number | null;
  threshold: number | null;
  description: string;
  detail: string | null;
}

export interface ProvenancePayload {
  metric: ProvenanceMetric;
  value: { as_of: string; value: number | null; scope_key: string | null };
  sources: ProvenanceSource[];
  quality: ProvenanceQuality[];
  freshness: {
    expected_as_of: string | null;
    lag_trading_days: number | null;
    stale: boolean | null;
  };
}

export interface ProvenanceQuery {
  date?: string | null;
  scopeKey?: string | null;
}

export function fetchProvenance(
  metricKey: string,
  query: ProvenanceQuery = {}
): Promise<ProvenancePayload> {
  return apiGet<ProvenancePayload>(`/api/v1/provenance/${metricKey}`, {
    date: query.date ?? undefined,
    scope_key: query.scopeKey ?? undefined,
  });
}
