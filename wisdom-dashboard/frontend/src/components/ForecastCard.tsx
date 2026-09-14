import { useState } from "react";
import type { AggregateForecast } from "../types";
import { formatPrice } from "../format";
import ProbabilityChart from "./ProbabilityChart";
import { ConfidenceBadge, DivergenceBadge } from "./Badges";
import PlatformBreakdown from "./PlatformBreakdown";
import ThresholdList from "./ThresholdList";
import ImpliedProbabilities from "./ImpliedProbabilities";

/**
 * Card layout note: the probabilities lead and the point forecast is a
 * footnote, which is the reverse of how this started out.
 *
 * The reason is the calibration backtest
 * (results/btc_price_market_calibration/report.md). It found the market's
 * implied mean loses to "assume nothing changes" by 34-43% at every lead
 * time tested, that its median is no better (so that is not an artifact of
 * how the open tails are reconstructed), and that its whole distribution
 * loses to a trailing-volatility random walk on both sharpness and
 * interval coverage. Bucket-level probabilities are the part that measured
 * well. Giving the biggest, boldest number on the page to the estimate our
 * own evidence says to distrust would be the dashboard arguing with its
 * own research, so it does not.
 */
export default function ForecastCard({ asset, forecast }: { asset: string; forecast: AggregateForecast }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="card">
      <div className="card-header">
        <h3>{forecast.period_label}</h3>
        <div className="badge-row">
          <ConfidenceBadge
            tier={forecast.confidence_tier}
            totalVolume={forecast.total_volume}
            effectiveVolume={forecast.effective_volume}
          />
          <DivergenceBadge pct={forecast.disagreement_pct} high={forecast.high_divergence} sourceCount={forecast.sources.length} />
        </div>
      </div>

      <ImpliedProbabilities asset={asset} thresholds={forecast.thresholds} />

      <ProbabilityChart
        asset={asset}
        gridEdges={forecast.grid_edges}
        pdf={forecast.pdf}
        mean={forecast.mean}
        median={forecast.median}
        ci68={forecast.ci_68}
        ci95={forecast.ci_95}
        sources={forecast.sources}
      />

      <div className="point-estimate">
        <div className="pe-row">
          <span className="pe-label">
            Point estimate
            <span
              className="pe-caveat"
              title={
                "Backtested on 97 resolved Polymarket events: the market's implied mean was 34-43% worse than simply assuming the price does not move, at every lead time from 6h to 6 days, and its median was no better. The full distribution also lost to a random walk widened by trailing realized volatility, including on interval coverage. Shown for reference and comparison between platforms -- the probabilities above are the part that measured well. See results/btc_price_market_calibration/report.md."
              }
            >
              ⓘ backtests worse than spot
            </span>
          </span>
          <span className="pe-values">
            mean {formatPrice(forecast.mean)} · median {formatPrice(forecast.median)} · σ {formatPrice(forecast.std)}
          </span>
        </div>
        <div className="pe-row pe-row-sub">
          <span className="pe-label">Interval</span>
          <span className="pe-values">
            68% {formatPrice(forecast.ci_68[0])} – {formatPrice(forecast.ci_68[1])} · 95%{" "}
            {formatPrice(forecast.ci_95[0])} – {formatPrice(forecast.ci_95[1])}
          </span>
        </div>
      </div>

      <button className="expand-btn" onClick={() => setExpanded((e) => !e)}>
        {expanded ? "Hide details" : `Details (${forecast.sources.length} source${forecast.sources.length === 1 ? "" : "s"})`}
      </button>

      {expanded && (
        <div className="card-details">
          <PlatformBreakdown sources={forecast.sources} />
          <ThresholdList asset={asset} thresholds={forecast.thresholds} />
          <CorrectionNote forecast={forecast} />
        </div>
      )}
    </div>
  );
}

/** What the calibration correction actually did to this card, so a reader
 *  can see it rather than having to trust it. */
function CorrectionNote({ forecast }: { forecast: AggregateForecast }) {
  const shrink = forecast.longshot_shrink_applied;
  const widen = forecast.ci_width_mult_applied;
  if (shrink >= 1 && widen === 1) return null;
  return (
    <p className="correction-note">
      Calibration correction at {forecast.lead_hours.toFixed(0)}h lead:{" "}
      {shrink < 1 && (
        <>
          buckets priced under 10% shrunk to <strong>{(shrink * 100).toFixed(0)}%</strong> of their quoted
          probability
        </>
      )}
      {shrink < 1 && widen !== 1 && "; "}
      {widen !== 1 && (
        <>
          intervals {widen > 1 ? "widened" : "narrowed"} by <strong>{Math.abs((widen - 1) * 100).toFixed(0)}%</strong>
        </>
      )}
      . Measured, not assumed — see <code>SHRINK_BY_LEAD_HOURS</code> in <code>aggregation.py</code>.
    </p>
  );
}
