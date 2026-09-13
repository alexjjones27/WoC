import { useCallback, useEffect, useState } from "react";
import type { AssetInfo, DashboardPayload } from "./types";
import { getAssets, getForecast } from "./api";
import { timeAgo } from "./format";
import AssetSelector from "./components/AssetSelector";
import ForecastCard from "./components/ForecastCard";
import TouchSection from "./components/TouchSection";
import FanChart from "./components/FanChart";

const AUTO_REFRESH_MS = 60_000;

export default function App() {
  const [assets, setAssets] = useState<AssetInfo[]>([]);
  const [symbol, setSymbol] = useState("BTC");
  const [data, setData] = useState<DashboardPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getAssets()
      .then(setAssets)
      .catch((e) => setLoadError(String(e)));
  }, []);

  const load = useCallback((forceRefresh: boolean) => {
    setLoading(true);
    setLoadError(null);
    getForecast(symbol, forceRefresh)
      .then((payload) => {
        if (payload.error) {
          setLoadError(payload.error);
        } else {
          setData(payload);
        }
      })
      .catch((e) => setLoadError(String(e)))
      .finally(() => setLoading(false));
  }, [symbol]);

  useEffect(() => {
    load(false);
    const id = setInterval(() => load(false), AUTO_REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-left">
          <h1>Wisdom of the Markets</h1>
          <span className="subtitle">Cross-platform prediction-market price forecasts</span>
        </div>
        <AssetSelector assets={assets} selected={symbol} onSelect={setSymbol} />
        <div className="topbar-right">
          {data && <span className="last-updated">updated {timeAgo(data.generated_at_utc)}</span>}
          <button className="refresh-btn" onClick={() => load(true)} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {data?.stale && (
        <div className="banner banner-warning">
          Showing the last successful fetch{data.stale_as_of_utc ? ` (${timeAgo(data.stale_as_of_utc)})` : ""} — all sources failed on the latest refresh.
        </div>
      )}

      {data && data.source_errors.length > 0 && (
        <div className="banner banner-muted">
          {data.source_errors.map((e) => (
            <div key={e.source}>
              {e.source}: {e.error}
            </div>
          ))}
        </div>
      )}

      {loadError && <div className="banner banner-error">{loadError}</div>}

      {data && (data.spot_history.length > 0 || data.forecasts.length > 0) && (
        <section className="fanchart-section">
          <div className="fanchart-section-header">
            <h2>{data.display_name} price &amp; forward confidence cone</h2>
            <p>Actual price leading up to now, then the aggregate forecast's median and nested confidence bands going forward.</p>
          </div>
          <FanChart asset={data.asset} history={data.spot_history} forecasts={data.forecasts} />
        </section>
      )}

      <main className="grid">
        {data?.forecasts.length === 0 && !loading && (
          <div className="empty-state">No active markets found for {symbol} right now.</div>
        )}
        {data?.forecasts.map((f) => (
          <ForecastCard key={f.target_date} asset={data.asset} forecast={f} />
        ))}
      </main>

      {data && data.touch_errors.length > 0 && (
        <div className="banner banner-muted">
          {data.touch_errors.map((e) => (
            <div key={e.source}>
              {e.source} (touch data): {e.error}
            </div>
          ))}
        </div>
      )}
      {data && <TouchSection asset={data.asset} groups={data.touch_forecasts} />}
    </div>
  );
}
