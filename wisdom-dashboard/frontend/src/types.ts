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
  stocktwits?: StockTwitsReading | null;
}

export type CrowdAsset = "BTC" | "ETH" | "GOLD" | "OIL";

export interface CrowdForecastPoint {
  target_date: string;
  period_label: string;
  lead_hours: number;
  mean: number;
  median: number;
  ci_68: [number, number];
  ci_95: [number, number];
  total_volume: number;
}

export interface OptionsForecastPoint extends CrowdForecastPoint {
  total_open_interest: number;
  disagreement_pct: number;
  sources: { source_name: string; mean: number; median: number | null; open_interest: number | null; source_url: string | null }[];
}

export interface FuturesCurvePoint {
  expiry: string;
  t_years: number;
  price: number;
  open_interest_usd: number;
  venues: string[];
  annualized_basis: number | null;
}

export interface PerpReading {
  venue: string;
  instrument: string;
  price: number;
  funding_annualized: number;
  open_interest_usd: number;
}

export interface EiaPoint {
  period: string;
  date: string;
  price: number;
  is_forecast: boolean;
}

export interface CotGroup {
  group: string;
  long_contracts: number;
  short_contracts: number;
  net_contracts: number;
  net_pct_open_interest: number;
  weekly_change_contracts: number | null;
  percentile_3y: number;
  history: { date: string; net_pct_oi: number }[];
}

export interface HorizonCrowdView {
  median: number;
  ci_68: [number, number] | null;
  date: string;
  change_vs_spot?: number;
}

export type CrowdKey = "prediction_markets" | "options" | "futures" | "experts";

export interface CrowdsPayload {
  asset: CrowdAsset;
  error?: string;
  display_name: string;
  generated_at_utc: string;
  spot: number | null;
  spot_source: string;
  prediction_markets: {
    forecasts: (CrowdForecastPoint & { confidence_tier: string; sources: string[] })[];
    sources_queried: string[];
    source_errors: SourceError[];
  };
  options: { forecasts: OptionsForecastPoint[]; source_errors: SourceError[]; sources_queried: string[] };
  futures: {
    curve: FuturesCurvePoint[];
    perps: PerpReading[];
    funding_annualized_oi_weighted: number | null;
    errors: string[];
  };
  experts: { error?: string; source: string; source_url: string; note: string; series: Record<string, EiaPoint[]> } | null;
  positioning: { error?: string; contract: string; report_date: string; open_interest: number; groups: CotGroup[]; source_url: string };
  sentiment: {
    stocktwits: StockTwitsReading | null;
    fear_greed: { error?: string; value: number; classification: string; avg_30d: number | null; source_url: string } | null;
    mvrv: { error?: string; value: number; as_of: string; percentile_history: number; history_days: number; source_url: string } | null;
    wikipedia: { error?: string; attention_ratio: number; resolved_title: string } | null;
  };
  horizon_views: { horizon_days: number; date: string; crowds: Partial<Record<CrowdKey, HorizonCrowdView>> }[];
}

export interface StockTwitsReading {
  error?: string;
  symbol: string;
  watchers: number | null;
  posts_read: number;
  bullish: number;
  bearish: number;
  untagged: number;
  bullish_share: number | null;
  oldest_post: string | null;
  newest_post: string | null;
  source_url: string;
}
