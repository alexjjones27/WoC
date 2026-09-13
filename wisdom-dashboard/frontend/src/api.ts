import type { AssetInfo, DashboardPayload } from "./types";

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
