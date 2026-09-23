import { useEffect, useState } from "react";
import type { CotGroup, CrowdAsset, CrowdKey, CrowdsPayload } from "../types";
import { getCrowds } from "../api";
import { formatCompactNumber, formatCompactUsd, formatPct, timeAgo } from "../format";

const ASSETS: { id: CrowdAsset; label: string }[] = [
  { id: "BTC", label: "Bitcoin" },
  { id: "ETH", label: "Ethereum" },
  { id: "GOLD", label: "Gold" },
  { id: "OIL", label: "WTI Oil" },
];

const CROWDS: { key: CrowdKey; label: string; color: string; what: string }[] = [
  { key: "prediction_markets", label: "Prediction markets", color: "var(--pm-color)", what: "median of the combined market distribution" },
  { key: "options", label: "Options", color: "var(--options-color)", what: "median of the risk-neutral distribution" },
  { key: "futures", label: "Futures", color: "var(--futures-color)", what: "forward price (includes cost of carry)" },
  { key: "experts", label: "EIA experts", color: "var(--expert-color)", what: "monthly-average forecast" },
];

const HORIZON_LABEL: Record<number, string> = { 7: "1 week", 30: "1 month", 90: "3 months", 365: "1 year" };
const DAY_MS = 86_400_000;
const CHART_MAX_DAYS = 400;

function formatAssetPrice(v: number, asset: CrowdAsset): string {
  const digits = asset === "OIL" ? 2 : 0;
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signedPct(v: number, digits = 1): string {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
}

function niceStep(span: number, target: number): number {
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * mag >= raw) return m * mag;
  return 10 * mag;
}

// ---------------------------------------------------------------- chart ---

function TermChart({ data }: { data: CrowdsPayload }) {
  const W = 980, H = 340, padL = 70, padR = 20, padT = 18, padB = 34;
  const now = new Date(data.generated_at_utc).getTime();
  const daysOut = (iso: string) => (new Date(iso).getTime() - now) / DAY_MS;

  const pm = data.prediction_markets.forecasts.filter((f) => f.lead_hours / 24 <= CHART_MAX_DAYS && f.lead_hours > 0);
  const opt = data.options.forecasts.filter((f) => f.lead_hours / 24 <= CHART_MAX_DAYS);
  const curve = data.futures.curve.filter((c) => c.t_years * 365.25 <= CHART_MAX_DAYS);
  const eia = (data.experts?.series?.WTI ?? []).filter((p) => p.is_forecast && daysOut(p.date) <= CHART_MAX_DAYS);

  const xs = [
    ...pm.map((f) => f.lead_hours / 24),
    ...opt.map((f) => f.lead_hours / 24),
    ...curve.map((c) => c.t_years * 365.25),
    ...eia.map((p) => daysOut(p.date)),
  ];
  if (!xs.length) return <div className="chart-empty">No forward-looking price data for this asset right now.</div>;
  const maxDays = Math.max(30, Math.min(CHART_MAX_DAYS, Math.max(...xs) * 1.04));

  const ys: number[] = [];
  if (data.spot) ys.push(data.spot);
  pm.forEach((f) => ys.push(f.ci_68[0], f.ci_68[1]));
  opt.forEach((f) => ys.push(f.ci_68[0], f.ci_68[1]));
  curve.forEach((c) => ys.push(c.price));
  eia.forEach((p) => ys.push(p.price));
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const pad = (hi - lo) * 0.08 || hi * 0.05;
  const yMin = lo - pad, yMax = hi + pad;

  const plotW = W - padL - padR, plotH = H - padT - padB;
  const x = (d: number) => padL + (Math.max(0, d) / maxDays) * plotW;
  const y = (v: number) => padT + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  const yStep = niceStep(yMax - yMin, 5);
  const yTicks: number[] = [];
  for (let v = Math.ceil(yMin / yStep) * yStep; v <= yMax; v += yStep) yTicks.push(v);

  const xTicks: { d: number; label: string }[] = [];
  const start = new Date(now);
  for (let i = 1; i <= 14; i++) {
    const m = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + i, 1));
    const d = (m.getTime() - now) / DAY_MS;
    if (d > maxDays) break;
    const every = maxDays > 200 ? 2 : 1;
    if (i % every === 0) xTicks.push({ d, label: m.toLocaleDateString("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }) });
  }

  const curvePts = data.spot ? [{ d: 0, v: data.spot }, ...curve.map((c) => ({ d: c.t_years * 365.25, v: c.price }))] : [];
  const optLine = opt.map((f) => `${x(f.lead_hours / 24)},${y(f.median)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg" role="img" aria-label="Where each crowd puts the price over the next year">
      {yTicks.map((v) => (
        <g key={v}>
          <line x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} className="chart-axis-line" />
          <text x={padL - 8} y={y(v) + 4} textAnchor="end" fontSize="10.5" className="chart-axis-text">
            {formatAssetPrice(v, data.asset)}
          </text>
        </g>
      ))}
      {xTicks.map((t) => (
        <text key={t.d} x={x(t.d)} y={H - padB + 18} textAnchor="middle" fontSize="10.5" className="chart-axis-text">
          {t.label}
        </text>
      ))}
      {data.spot && (
        <g>
          <line x1={padL} x2={W - padR} y1={y(data.spot)} y2={y(data.spot)} className="chart-zero-line" strokeDasharray="4 4" />
          <text x={W - padR} y={y(data.spot) - 5} textAnchor="end" fontSize="10.5" className="chart-axis-text">
            now {formatAssetPrice(data.spot, data.asset)}
          </text>
        </g>
      )}

      {curvePts.length > 1 && (
        <polyline points={curvePts.map((p) => `${x(p.d)},${y(p.v)}`).join(" ")} fill="none" stroke="var(--futures-color)" strokeWidth={2} />
      )}

      {opt.length > 1 && <polyline points={optLine} fill="none" stroke="var(--options-color)" strokeWidth={1.2} opacity={0.5} />}
      {opt.map((f) => (
        <g key={`o-${f.target_date}`}>
          <line x1={x(f.lead_hours / 24)} x2={x(f.lead_hours / 24)} y1={y(f.ci_68[0])} y2={y(f.ci_68[1])} stroke="var(--options-color)" strokeWidth={2} opacity={0.45} />
          <circle cx={x(f.lead_hours / 24)} cy={y(f.median)} r={3.5} fill="var(--options-color)">
            <title>{`Options ${f.period_label}: median ${formatAssetPrice(f.median, data.asset)}, 68% ${formatAssetPrice(f.ci_68[0], data.asset)}–${formatAssetPrice(f.ci_68[1], data.asset)}`}</title>
          </circle>
        </g>
      ))}

      {pm.map((f) => (
        <g key={`p-${f.target_date}`}>
          <line x1={x(f.lead_hours / 24) + 3} x2={x(f.lead_hours / 24) + 3} y1={y(f.ci_68[0])} y2={y(f.ci_68[1])} stroke="var(--pm-color)" strokeWidth={2} opacity={0.5} />
          <circle cx={x(f.lead_hours / 24) + 3} cy={y(f.median)} r={4.5} fill="var(--pm-color)" stroke="var(--surface)" strokeWidth={1.5}>
            <title>{`Prediction markets ${f.period_label}: median ${formatAssetPrice(f.median, data.asset)}, 68% ${formatAssetPrice(f.ci_68[0], data.asset)}–${formatAssetPrice(f.ci_68[1], data.asset)}`}</title>
          </circle>
        </g>
      ))}

      {eia.map((p) => (
        <rect key={p.period} x={x(daysOut(p.date)) - 4} y={y(p.price) - 4} width={8} height={8} fill="var(--expert-color)">
          <title>{`EIA forecast, ${p.period} average: ${formatAssetPrice(p.price, data.asset)}`}</title>
        </rect>
      ))}
    </svg>
  );
}

// ------------------------------------------------------------ sections ---

function HorizonTable({ data }: { data: CrowdsPayload }) {
  const present = CROWDS.filter((c) => data.horizon_views.some((h) => h.crowds[c.key]));
  if (!present.length) return null;
  return (
    <div className="table-scroll">
      <table className="data-table crowd-table">
        <thead>
          <tr>
            <th>Crowd</th>
            {data.horizon_views.map((h) => (
              <th key={h.horizon_days} className="num">{HORIZON_LABEL[h.horizon_days] ?? `${h.horizon_days}d`}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {present.map((c) => (
            <tr key={c.key}>
              <td>
                <span className="legend-swatch" style={{ background: c.color }} /> {c.label}
                <div className="crowd-what">{c.what}</div>
              </td>
              {data.horizon_views.map((h) => {
                const v = h.crowds[c.key];
                if (!v) return <td key={h.horizon_days} className="num muted">—</td>;
                return (
                  <td key={h.horizon_days} className="num">
                    <div>{formatAssetPrice(v.median, data.asset)}</div>
                    {v.change_vs_spot !== undefined && (
                      <div className={v.change_vs_spot >= 0 ? "value-gain" : "value-loss"}>{signedPct(v.change_vs_spot)}</div>
                    )}
                    {v.ci_68 && (
                      <div className="crowd-what">
                        68%: {formatAssetPrice(v.ci_68[0], data.asset)}–{formatAssetPrice(v.ci_68[1], data.asset)}
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PercentileBar({ value, label }: { value: number; label: string }) {
  return (
    <div className="pct-bar" aria-label={`${label}: ${Math.round(value * 100)}th percentile`}>
      <div className="pct-bar-track">
        <div className="pct-bar-mid" />
        <div className="pct-bar-marker" style={{ left: `${Math.min(100, Math.max(0, value * 100))}%` }} />
      </div>
      <div className="pct-bar-ends">
        <span>most short</span>
        <span>{Math.round(value * 100)}th pct</span>
        <span>most long</span>
      </div>
    </div>
  );
}

function FuturesCard({ data }: { data: CrowdsPayload }) {
  const f = data.futures;
  const longCurve = f.curve.filter((c) => c.t_years > 7 / 365);
  return (
    <div className="card crowd-card">
      <div className="card-header"><h3><span className="legend-swatch" style={{ background: "var(--futures-color)" }} /> Futures &amp; perpetuals</h3></div>
      {f.perps.length > 0 && (
        <>
          <div className="crowd-sub">Perpetual funding: positive means leveraged longs are paying shorts, so traders lean long.</div>
          <table className="data-table">
            <thead><tr><th>Venue</th><th className="num">Price</th><th className="num">Funding / yr</th><th className="num">Open interest</th></tr></thead>
            <tbody>
              {f.perps.map((p) => (
                <tr key={p.venue + p.instrument}>
                  <td>{p.venue} <span className="muted">{p.instrument}</span></td>
                  <td className="num">{formatAssetPrice(p.price, data.asset)}</td>
                  <td className={`num ${p.funding_annualized >= 0 ? "value-gain" : "value-loss"}`}>{signedPct(p.funding_annualized)}</td>
                  <td className="num">{formatCompactUsd(p.open_interest_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {longCurve.length > 0 ? (
        <>
          <div className="crowd-sub">Dated futures curve. Basis is the yearly premium over today's price; a few % a year is normal carry, not a forecast.</div>
          <table className="data-table">
            <thead><tr><th>Expiry</th><th className="num">Forward</th><th className="num">Basis / yr</th><th className="num">Open interest</th></tr></thead>
            <tbody>
              {longCurve.map((c) => (
                <tr key={c.expiry}>
                  <td>{new Date(c.expiry).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })} <span className="muted">{c.venues.join(", ")}</span></td>
                  <td className="num">{formatAssetPrice(c.price, data.asset)}</td>
                  <td className="num">{c.annualized_basis !== null ? signedPct(c.annualized_basis) : "—"}</td>
                  <td className="num">{formatCompactUsd(c.open_interest_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <div className="crowd-sub">No free dated-futures curve for {data.display_name}: CME data isn't free, and crypto venues only list it as a perpetual.</div>
      )}
      {f.errors.length > 0 && <div className="banner banner-warning">{f.errors.join("; ")}</div>}
    </div>
  );
}

function OptionsCard({ data }: { data: CrowdsPayload }) {
  const o = data.options;
  if (!o.forecasts.length) {
    return (
      <div className="card crowd-card crowd-empty">
        <div className="card-header"><h3><span className="legend-swatch" style={{ background: "var(--options-color)" }} /> Options</h3></div>
        <div className="crowd-sub">No free options chain covers {data.display_name}. Deribit, OKX and Derive list only crypto.</div>
      </div>
    );
  }
  const venues = o.sources_queried;
  return (
    <div className="card crowd-card crowd-wide">
      <div className="card-header"><h3><span className="legend-swatch" style={{ background: "var(--options-color)" }} /> Options</h3></div>
      <div className="crowd-sub">
        Each expiry combines {venues.join(", ").replace(/_options/g, "")} by open interest. Arbitrage keeps venues close, so they add depth more than new opinions.
        A risk-neutral median sits a little below the futures forward by construction.
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Expiry</th><th className="num">Median</th><th className="num">68% range</th>
              {venues.map((v) => <th key={v} className="num">{v.replace("_options", "")}</th>)}
              <th className="num">Open interest</th>
            </tr>
          </thead>
          <tbody>
            {o.forecasts.map((f) => (
              <tr key={f.target_date}>
                <td>{f.period_label}</td>
                <td className="num">{formatAssetPrice(f.median, data.asset)}</td>
                <td className="num">{formatAssetPrice(f.ci_68[0], data.asset)}–{formatAssetPrice(f.ci_68[1], data.asset)}</td>
                {venues.map((v) => {
                  const s = f.sources.find((x) => x.source_name === v);
                  return <td key={v} className="num">{s?.median ? formatAssetPrice(s.median, data.asset) : "—"}</td>;
                })}
                <td className="num">{formatCompactUsd(f.total_open_interest)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {o.source_errors.length > 0 && <div className="banner banner-warning">{o.source_errors.map((e) => `${e.source}: ${e.error}`).join("; ")}</div>}
    </div>
  );
}

function ExpertsCard({ data }: { data: CrowdsPayload }) {
  const e = data.experts;
  return (
    <div className={`card crowd-card ${e ? "" : "crowd-empty"}`}>
      <div className="card-header"><h3><span className="legend-swatch" style={{ background: "var(--expert-color)" }} /> Experts</h3></div>
      {!e ? (
        <div className="crowd-sub">
          No free, regularly updated expert forecast for {data.display_name}. The EIA's outlook covers oil only; analyst price targets for stocks are on the Stock Lookup page.
        </div>
      ) : e.error ? (
        <div className="banner banner-error">{e.error}</div>
      ) : (
        <>
          <div className="crowd-sub">
            <a href={e.source_url} target="_blank" rel="noreferrer">{e.source}</a>: government analysts' monthly-average forecast, updated monthly.
            Faded rows are EIA estimates of recent actual prices.
          </div>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>Month</th><th className="num">WTI</th><th className="num">Brent</th></tr></thead>
              <tbody>
                {(e.series.WTI ?? []).map((p) => {
                  const brent = (e.series.Brent ?? []).find((b) => b.period === p.period);
                  return (
                    <tr key={p.period} className={p.is_forecast ? "" : "muted"}>
                      <td>{new Date(p.date).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" })}{p.is_forecast ? "" : " (actual)"}</td>
                      <td className="num">{formatAssetPrice(p.price, "OIL")}</td>
                      <td className="num">{brent ? formatAssetPrice(brent.price, "OIL") : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function PositioningCard({ data }: { data: CrowdsPayload }) {
  const p = data.positioning;
  const crypto = data.asset === "BTC" || data.asset === "ETH";
  return (
    <div className="card crowd-card">
      <div className="card-header"><h3>Positioning <span className="muted">CFTC Commitments of Traders</span></h3></div>
      {p.error ? (
        <div className="banner banner-error">{p.error}</div>
      ) : (
        <>
          <div className="crowd-sub">
            {p.contract}, week of {p.report_date}. The marker shows how long or short each group is against its own last 3 years.
            {crypto && " Leveraged funds are structurally short CME crypto futures (they hedge ETF holdings), so read their position against their own history, not as a bet."}
          </div>
          {p.groups.map((g: CotGroup) => (
            <div key={g.group} className="cot-row">
              <div className="cot-head">
                <span className="cot-name">{g.group}</span>
                <span className={g.net_contracts >= 0 ? "value-gain" : "value-loss"}>
                  net {g.net_contracts >= 0 ? "long" : "short"} {formatCompactNumber(Math.abs(g.net_contracts))} ({formatPct(g.net_pct_open_interest, 0)} of OI)
                </span>
                {g.weekly_change_contracts !== null && (
                  <span className="muted">{g.weekly_change_contracts >= 0 ? "+" : "−"}{formatCompactNumber(Math.abs(g.weekly_change_contracts))} this week</span>
                )}
              </div>
              <PercentileBar value={g.percentile_3y} label={g.group} />
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function SentimentCard({ data }: { data: CrowdsPayload }) {
  const s = data.sentiment;
  const st = s.stocktwits;
  return (
    <div className="card crowd-card">
      <div className="card-header"><h3>Retail sentiment</h3></div>
      <div className="stat-strip">
        <div className="stat-tile">
          <div className="stat-label">StockTwits bullish</div>
          <div className="stat-value">{st && !st.error && st.bullish_share !== null ? formatPct(st.bullish_share, 0) : "n/a"}</div>
          <div className="stat-sub">
            {st && !st.error ? `${st.bullish} bull / ${st.bearish} bear tags · ${st.symbol}` : st?.error ?? "not covered"}
          </div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Wikipedia attention</div>
          <div className="stat-value">{s.wikipedia && !s.wikipedia.error ? `${s.wikipedia.attention_ratio.toFixed(2)}×` : "n/a"}</div>
          <div className="stat-sub">{s.wikipedia?.error ?? "page views vs 90-day baseline"}</div>
        </div>
        {s.fear_greed && (
          <div className="stat-tile">
            <div className="stat-label">Crypto Fear &amp; Greed</div>
            <div className="stat-value">{s.fear_greed.error ? "n/a" : s.fear_greed.value}</div>
            <div className="stat-sub">
              {s.fear_greed.error ?? `${s.fear_greed.classification}${s.fear_greed.avg_30d !== null ? ` · 30-day avg ${Math.round(s.fear_greed.avg_30d)}` : ""}`}
            </div>
          </div>
        )}
        {s.mvrv && (
          <div className="stat-tile">
            <div className="stat-label">MVRV (on-chain)</div>
            <div className="stat-value">{s.mvrv.error ? "n/a" : s.mvrv.value.toFixed(2)}</div>
            <div className="stat-sub">
              {s.mvrv.error ?? `${Math.round(s.mvrv.percentile_history * 100)}th pct of last ${(s.mvrv.history_days / 365).toFixed(1)} yrs`}
            </div>
          </div>
        )}
      </div>
      <div className="crowd-sub">
        Posters mostly own what they post about, so StockTwits and Fear &amp; Greed lean bullish; compare them with their own history rather than reading them as 50/50 polls.
        MVRV compares market value with what holders paid: near 1 means the average holder is at break-even.
      </div>
    </div>
  );
}

// --------------------------------------------------------------- page ---

function AssetView({ asset }: { asset: CrowdAsset }) {
  const [data, setData] = useState<CrowdsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    getCrowds(asset).then(setData).catch((e) => setError(String(e)));
  }, [asset]);

  if (error) return <div className="banner banner-error">{error}</div>;
  if (!data) return <div className="empty-state">Asking every crowd about {asset}… (5–15 seconds)</div>;
  if (data.error) return <div className="banner banner-error">{data.error}</div>;

  const reporting = CROWDS.filter((c) =>
    c.key === "prediction_markets" ? data.prediction_markets.forecasts.length > 0
      : c.key === "options" ? data.options.forecasts.length > 0
      : c.key === "futures" ? data.futures.curve.length > 0 || data.futures.perps.length > 0
      : !!data.experts && !data.experts.error,
  );

  return (
    <div className="crowds">
      <div className="stat-strip">
        <div className="stat-tile">
          <div className="stat-label">Price now</div>
          <div className="stat-value">{data.spot ? formatAssetPrice(data.spot, data.asset) : "n/a"}</div>
          <div className="stat-sub">{data.spot_source}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Price crowds reporting</div>
          <div className="stat-value">{reporting.length} of 4</div>
          <div className="stat-sub">{reporting.map((c) => c.label).join(", ") || "none"}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Perp funding / yr</div>
          <div className={`stat-value ${(data.futures.funding_annualized_oi_weighted ?? 0) >= 0 ? "value-gain" : "value-loss"}`}>
            {data.futures.funding_annualized_oi_weighted !== null ? signedPct(data.futures.funding_annualized_oi_weighted) : "n/a"}
          </div>
          <div className="stat-sub">open-interest weighted</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Updated</div>
          <div className="stat-value">{timeAgo(data.generated_at_utc)}</div>
          <div className="stat-sub">cached for 1 minute</div>
        </div>
      </div>

      <section className="sm-section">
        <h3 className="section-title">Where each crowd puts the price</h3>
        <div className="chart-card">
          <div className="chart-legend">
            {CROWDS.filter((c) => reporting.includes(c) && c.key !== "futures").map((c) => (
              <span key={c.key} className="legend-item"><span className="legend-swatch" style={{ background: c.color }} />{c.label}</span>
            ))}
            {data.futures.curve.length > 0 && (
              <span className="legend-item"><span className="legend-swatch" style={{ background: "var(--futures-color)" }} />Futures curve</span>
            )}
            <span className="muted">dot = median · bar = 68% range</span>
          </div>
          <TermChart data={data} />
        </div>
        <HorizonTable data={data} />
      </section>

      <div className="crowd-grid">
        <OptionsCard data={data} />
        <FuturesCard data={data} />
        <ExpertsCard data={data} />
        <PositioningCard data={data} />
        <SentimentCard data={data} />
      </div>

      {data.prediction_markets.source_errors.length > 0 && (
        <div className="banner banner-warning">
          Prediction market sources with errors: {data.prediction_markets.source_errors.map((e) => `${e.source}: ${e.error}`).join("; ")}
        </div>
      )}
    </div>
  );
}

export default function CrowdsPage() {
  const [asset, setAsset] = useState<CrowdAsset>("BTC");
  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">Crowds</h2>
          <span className="subtitle">
            Bettors, options traders, futures traders, government forecasters, big-money positioning and retail mood, side by side
          </span>
        </div>
        <div className="asset-toggle">
          {ASSETS.map((a) => (
            <button key={a.id} className={asset === a.id ? "toggle-btn active" : "toggle-btn"} onClick={() => setAsset(a.id)}>{a.label}</button>
          ))}
        </div>
      </div>
      <AssetView asset={asset} />
    </div>
  );
}
