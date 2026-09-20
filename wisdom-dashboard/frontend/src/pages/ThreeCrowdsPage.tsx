import { useEffect, useState } from "react";
import type { ThreeCrowdsAsset, ThreeCrowdsPayload } from "../types";
import { getThreeCrowds } from "../api";
import { formatCompactUsd, formatPct } from "../format";

const ASSETS: { id: ThreeCrowdsAsset; label: string }[] = [
  { id: "BTC", label: "Bitcoin" },
  { id: "OIL", label: "WTI Oil" },
  { id: "ETH", label: "Ethereum" },
  { id: "GOLD", label: "Gold" },
];

function RangeChart({ data }: { data: ThreeCrowdsPayload }) {
  const W = 760, H = 150, padL = 130, padR = 50, padT = 16, padB = 30;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const pmCi = data.prediction_market.ci_95;
  const pmMedianReturn = data.pm_implied_return ?? 0;
  const optCi = data.options.p10_return !== undefined ? [data.options.p10_return, data.options.p90_return] : null;

  const allVals = [
    pmCi ? pmCi[0] / data.real_spot - 1 : pmMedianReturn,
    pmCi ? pmCi[1] / data.real_spot - 1 : pmMedianReturn,
    optCi ? optCi[0] : data.options.median_return,
    optCi ? optCi[1] : data.options.median_return,
  ];
  const minV = Math.min(...allVals) * 1.1;
  const maxV = Math.max(...allVals) * 1.1;
  const x = (v: number) => padL + ((v - minV) / (maxV - minV)) * plotW;

  const step = maxV - minV > 1.5 ? 0.5 : 0.2;
  const gridlines: number[] = [];
  for (let g = Math.ceil(minV / step) * step; g <= maxV; g += step) gridlines.push(g);

  const rows = [
    { label: "Prediction market", y: padT + plotH * 0.3, color: "var(--pm-color)", median: pmMedianReturn, ci95: pmCi ? [pmCi[0] / data.real_spot - 1, pmCi[1] / data.real_spot - 1] : null },
    { label: "Options market", y: padT + plotH * 0.78, color: "var(--options-color)", median: data.options.median_return, ci95: optCi },
  ];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {gridlines.map((g) => (
        <g key={g}>
          <line x1={x(g)} y1={padT} x2={x(g)} y2={H - padB} className="chart-axis-line" />
          <text x={x(g)} y={H - padB + 16} fontSize="10.5" textAnchor="middle" className="chart-axis-text">
            {g >= 0 ? "+" : ""}{Math.round(g * 100)}%
          </text>
        </g>
      ))}
      <line x1={x(0)} y1={padT} x2={x(0)} y2={H - padB} className="chart-zero-line" />
      {rows.map((row) => (
        <g key={row.label}>
          {row.ci95 && <line x1={x(row.ci95[0])} y1={row.y} x2={x(row.ci95[1])} y2={row.y} stroke={row.color} strokeWidth={2} opacity={0.4} />}
          <circle cx={x(row.median)} cy={row.y} r={6} fill={row.color} stroke="var(--surface-raised)" strokeWidth={2} />
          <text x={x(row.median)} y={row.y - 14} fontSize="12" fontWeight={600} textAnchor="middle" fill={row.color}>
            {row.median >= 0 ? "+" : ""}{(row.median * 100).toFixed(1)}%
          </text>
          <text x={padL - 10} y={row.y + 4} fontSize="12" textAnchor="end" className="chart-row-label">{row.label}</text>
        </g>
      ))}
    </svg>
  );
}

function AssetView({ asset }: { asset: ThreeCrowdsAsset }) {
  const [data, setData] = useState<ThreeCrowdsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    getThreeCrowds(asset)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [asset]);

  if (error) return <div className="banner banner-error">{error}</div>;
  if (!data) return <div className="empty-state">Loading {asset}…</div>;
  if (data.error) return <div className="banner banner-error">{data.error}</div>;

  const horizonMismatchDays = Math.abs(data.prediction_market.horizon_mismatch_hours) / 24;
  const bigMismatch = horizonMismatchDays > 20;

  return (
    <div>
      <div className="stat-strip">
        <div className="stat-tile">
          <div className="stat-label">Spot</div>
          <div className="stat-value">{formatCompactUsd(data.real_spot)}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Prediction market implied</div>
          <div className={`stat-value ${(data.pm_implied_return ?? 0) >= 0 ? "value-gain" : "value-loss"}`}>
            {data.pm_implied_return !== null ? formatPct(data.pm_implied_return, 1) : "n/a"}
          </div>
          <div className="stat-sub">{data.prediction_market.is_interpolated ? "interpolated to match horizon" : "real market"}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Options market implied</div>
          <div className={`stat-value ${data.options.median_return >= 0 ? "value-gain" : "value-loss"}`}>{formatPct(data.options.median_return, 1)}</div>
          <div className="stat-sub">median of full distribution</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Cross-check</div>
          <div className="stat-value">
            {!bigMismatch && data.cross_check_p_options_exceed_pm_median !== null
              ? `${Math.round(data.cross_check_p_options_exceed_pm_median * 100)}%`
              : "n/a"}
          </div>
          <div className="stat-sub">{bigMismatch ? "horizon mismatch too large" : "P(options confirms PM median)"}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-label">Retail attention</div>
          <div className="stat-value">{data.retail_attention ? `${data.retail_attention.attention_ratio.toFixed(2)}×` : "n/a"}</div>
          <div className="stat-sub">vs. 90-day baseline</div>
        </div>
      </div>

      {bigMismatch && (
        <div className="banner banner-warning">
          Horizon mismatch: the options view runs {(data.options.t_years * 365.25).toFixed(0)} days out, but the closest real prediction
          market only reaches {Math.round(data.prediction_market.lead_hours / 24)} days. The chart below shows each on its own native horizon —
          not forced into one number.
        </div>
      )}

      <div className="chart-card">
        <div className="chart-legend">
          <span className="legend-item"><span className="legend-swatch" style={{ background: "var(--pm-color)" }} />Prediction market</span>
          <span className="legend-item"><span className="legend-swatch" style={{ background: "var(--options-color)" }} />Options market</span>
          <span>dot = median · band = {bigMismatch ? "10th–90th pct" : "95% CI"}</span>
        </div>
        <RangeChart data={data} />
      </div>
    </div>
  );
}

export default function ThreeCrowdsPage() {
  const [asset, setAsset] = useState<ThreeCrowdsAsset>("BTC");

  return (
    <div>
      <div className="page-toolbar">
        <div>
          <h2 className="page-title">Three crowds</h2>
          <span className="subtitle">Prediction markets vs. options markets vs. retail attention, for the assets that have a real prediction-market consensus</span>
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
