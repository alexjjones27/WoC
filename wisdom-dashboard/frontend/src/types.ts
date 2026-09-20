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

// A model-filled gap point (geometric-Brownian-motion term-structure
// interpolation between two real forecast dates too far apart to connect
// directly) -- NOT a market-implied forecast. See aggregation.py's
// "CROSS-DATE TERM STRUCTURE" section. Deliberately a narrower shape than
// AggregateForecast (no sources, no pdf/grid, no confidence score) since
// there's no market liquidity behind this number.
export interface InterpolatedForecast {
  asset: string;
  target_date: string;
  period_label: string;
  mean: number;
  median: number;
  confidence_bands: ConfidenceBand[];
  lead_hours: number;
  is_interpolated: true;
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
  gap_fill_forecasts: InterpolatedForecast[];
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

// ---- Smart money (SEC 13F) + combined four-signal wisdom-of-crowds ----

export interface PortfolioHolding {
  ticker: string;
  sector: string;
  smart_money_weight: number;
  analyst_implied_return: number | null;
  options_p_exceed_target: number | null;
  retail_attention_ratio: number | null;
  combined_score: number;
  portfolio_weight: number;
  n_signals_available: number;
}

export interface SmartMoneyPortfolioPayload {
  portfolio: PortfolioHolding[];
  coverage: {
    universe_size: number;
    smart_money: number;
    analyst: number;
    options: number;
    retail: number;
  };
}

export interface BacktestStats {
  total_return: number;
  cagr: number | null;
  annualized_vol: number | null;
  max_drawdown: number;
  final_nav: number;
}

export interface SmartMoneyBacktestPayload {
  fixed_panel?: { stats: Record<string, BacktestStats>; nav: { period_labels: string[]; nav: Record<string, number[]> } };
  scaling_panels?: { stats: Record<string, BacktestStats>; nav: { period_labels: string[]; nav: Record<string, number[]> } };
  significance?: Record<string, {
    significance_vs_spy: { p_value: number; annualized_spread_pct: number; significant_at_5pct: boolean };
    factor_regression_vs_spy: { alpha_annualized_pct: number; alpha_p_value: number; beta: number; r_squared: number };
  }>;
}

export interface TickerLookupPayload {
  ticker: string;
  smart_money_weight: number;
  analyst: { spot: number; consensus_target: number; n_firms: number; implied_return: number } | null;
  options: { implied_vol: number | null; p_exceed_analyst_target: number | null } | null;
  retail_attention: { attention_ratio: number; resolved_title: string } | null;
}

export type ThreeCrowdsAsset = "BTC" | "OIL" | "ETH" | "GOLD";

export interface ThreeCrowdsPayload {
  asset: ThreeCrowdsAsset;
  error?: string;
  real_spot: number;
  prediction_market: {
    target_date: string;
    lead_hours: number;
    horizon_mismatch_hours: number;
    is_interpolated: boolean;
    mean: number;
    median: number;
    ci_68: [number, number] | null;
    ci_95: [number, number] | null;
  };
  pm_implied_return: number | null;
  options: {
    proxy_ticker: string;
    proxy_note: string;
    t_years: number;
    mean_return: number;
    median_return: number;
    p10_return: number;
    p90_return: number;
  };
  retail_attention: { attention_ratio: number; resolved_title: string } | null;
  cross_check_p_options_exceed_pm_median: number | null;
}
