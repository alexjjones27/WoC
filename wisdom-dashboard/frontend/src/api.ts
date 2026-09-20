import type {
  AssetInfo,
  DashboardPayload,
  SmartMoneyBacktestPayload,
  SmartMoneyPortfolioPayload,
  ThreeCrowdsAsset,
  ThreeCrowdsPayload,
  TickerLookupPayload,
} from "./types";

export async function getAssets(): Promise<AssetInfo[]> {
  const res = await fetch("/api/assets");
  if (!res.ok) throw new Error(`GET /api/assets -> ${res.status}`);
  const data = await res.json();
  return data.assets;
}

export async function getForecast(symbol: string, refresh: boolean): Promise<DashboardPayload> {
  const res = await fetch(`/api/forecast/${symbol}${refresh ? "?refresh=true" : ""}`);
  if (!res.ok) throw new Error(`GET /api/forecast/${symbol} -> ${res.status}`);
  return res.json();
}

export async function getSmartMoneyPortfolio(): Promise<SmartMoneyPortfolioPayload> {
  const res = await fetch("/api/smart-money/portfolio");
  if (!res.ok) throw new Error(`GET /api/smart-money/portfolio -> ${res.status}`);
  return res.json();
}

export async function getSmartMoneyBacktest(): Promise<SmartMoneyBacktestPayload> {
  const res = await fetch("/api/smart-money/backtest");
  if (!res.ok) throw new Error(`GET /api/smart-money/backtest -> ${res.status}`);
  return res.json();
}

export async function lookupTicker(ticker: string): Promise<TickerLookupPayload> {
  const res = await fetch(`/api/lookup/${encodeURIComponent(ticker)}`);
  if (!res.ok) throw new Error(`GET /api/lookup/${ticker} -> ${res.status}`);
  return res.json();
}

export async function getThreeCrowds(asset: ThreeCrowdsAsset): Promise<ThreeCrowdsPayload> {
  const res = await fetch(`/api/three-crowds/${asset}`);
  if (!res.ok) throw new Error(`GET /api/three-crowds/${asset} -> ${res.status}`);
  return res.json();
}
