import { useState } from "react";
import type { TickerLookupPayload } from "../types";
import { lookupTicker } from "../api";
import { formatCompactUsd, formatPct } from "../format";

export default function StockLookupPage() {
  const [input, setInput] = useState("");
  const [result, setResult] = useState<TickerLookupPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const ticker = input.trim().toUpperCase();
    if (!ticker) return;
    setLoading(true);
    setError(null);
    lookupTicker(ticker)
      .then(setResult)
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  };

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">Stock lookup</h2>
          <span className="subtitle">Live four-signal read on any ticker — smart money, analyst consensus, options market, retail attention</span>
        </div>
      </div>

      <form onSubmit={submit} className="lookup-form">
        <input
          className="lookup-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ticker, e.g. NVDA"
          autoFocus
        />
        <button className="refresh-btn" type="submit" disabled={loading}>
          {loading ? "Looking up…" : "Look up"}
        </button>
      </form>

      <p className="sm-note">
        Smart money weight only shows for stocks held by the 50-fund objective 13F panel (most stocks show 0% — that's expected, not missing data).
        Analyst and retail attention fetch live from cached-where-possible sources; options data needs a liquid chain and fails for a real share
        of even large-cap names — that's a genuine data limitation, not a bug, if you see "n/a".
      </p>

      {error && <div className="banner banner-error">{error}</div>}

      {result && (
        <div className="lookup-result">
          <h3>{result.ticker}</h3>
          <div className="stat-strip">
            <div className="stat-tile">
              <div className="stat-label">Smart money weight</div>
              <div className="stat-value">{result.smart_money_weight ? formatPct(result.smart_money_weight, 1) : "0%"}</div>
            </div>
            <div className="stat-tile">
              <div className="stat-label">Analyst implied return</div>
              <div className={`stat-value ${result.analyst && result.analyst.implied_return >= 0 ? "value-gain" : "value-loss"}`}>
                {result.analyst ? formatPct(result.analyst.implied_return, 1) : "n/a"}
              </div>
              {result.analyst && <div className="stat-sub">target {formatCompactUsd(result.analyst.consensus_target)} · {result.analyst.n_firms} firms</div>}
            </div>
            <div className="stat-tile">
              <div className="stat-label">Options P(hit analyst target)</div>
              <div className="stat-value">
                {result.options?.p_exceed_analyst_target !== null && result.options?.p_exceed_analyst_target !== undefined
                  ? `${Math.round(result.options.p_exceed_analyst_target * 100)}%`
                  : "n/a"}
              </div>
              {result.options?.implied_vol !== null && result.options?.implied_vol !== undefined && (
                <div className="stat-sub">implied vol {formatPct(result.options.implied_vol, 0)}</div>
              )}
            </div>
            <div className="stat-tile">
              <div className="stat-label">Retail attention</div>
              <div className="stat-value">{result.retail_attention ? `${result.retail_attention.attention_ratio.toFixed(2)}×` : "n/a"}</div>
              {result.retail_attention && <div className="stat-sub">vs. 90-day baseline</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
