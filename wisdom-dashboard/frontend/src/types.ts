// Mirrors backend/aggregation.py's dataclasses (via orchestrator.py's JSON
// serialization) and backend/common/assets.py. Keep in sync by hand -- this
// is a small enough API surface that a codegen step isn't worth it.

export interface ThresholdRow {
  threshold: number;
  prob_gt_aggregate: number;
  per_source_prob_gt: Record<string, number>;
}

export interface SourceBreakdown {
  source_name: string;
  source_type: string;
  period_label: string;
  mean: number;
  weight: number;
  volume: number | null;
  open_interest: number | null;
  liquidity: number | null;
  source_url: string | null;
  resolve_datetime_utc: string | null;
  pdf: number[];
  raw_note: string | null;
  is_play_money: boolean;
  concentration_discount: number | null;
  concentration_effective_traders: number | null;
  stale: boolean;
  error: string | null;
}

export type ConfidenceTier = "high" | "medium" | "low";

export interface ConfidenceBand {
  level: number; // e.g. 0.90 = 90%
  low: number;
  high: number;
}

export interface AggregateForecast {
  asset: string;
  target_date: string;
  period_label: string;
  grid_edges: number[];
  pdf: number[];
  mean: number;
  median: number;
  std: number;
  ci_68: [number, number];
  ci_95: [number, number];
  confidence_score: number;
  confidence_tier: ConfidenceTier;
  total_volume: number;
  disagreement_pct: number;
  high_divergence: boolean;
  thresholds: ThresholdRow[];
  lead_hours: number;
  longshot_shrink_applied: number;
  ci_width_mult_applied: number;
  confidence_bands: ConfidenceBand[];
  sources: SourceBreakdown[];
}

export interface SpotHistoryPoint {
  t_ms: number;
  price: number;
}

export interface SourceError {
  source: string;
  source_type: string;
  error: string;
}

// "Does price ever cross $X by date T" -- a fundamentally different
// question from AggregateForecast's "what is price AT date T", so it's
// its own type and its own dashboard section, never blended in above.
export interface TouchThreshold {
  direction: "above" | "below";
  price: number;
  prob_touch: number;
  volume: number | null;
  label: string;
}

export interface TouchForecast {
  asset: string;
  source_name: string;
  expiry_date: string;
  period_label: string;
  thresholds: TouchThreshold[];
  total_volume: number;
  source_url: string | null;
  raw_note: string | null;
  resolve_datetime_utc: string | null;
}

export interface TouchGroup {
  expiry_date: string;
  period_label: string;
  sources: TouchForecast[];
}

export interface DashboardPayload {
  asset: string;
  display_name: string;
  generated_at_utc: string;
  stale: boolean;
  stale_as_of_utc?: string;
  forecasts: AggregateForecast[];
  source_errors: SourceError[];
  touch_forecasts: TouchGroup[];
  touch_errors: { source: string; error: string }[];
  spot_history: SpotHistoryPoint[];
  sources_queried: { name: string; source_type: string }[];
  error?: string;
}

export interface AssetInfo {
  symbol: string;
  display_name: string;
  adapters: string[];
  enabled: boolean;
}
