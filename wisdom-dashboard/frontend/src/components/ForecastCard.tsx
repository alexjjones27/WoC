import { useState } from "react";
import type { AggregateForecast } from "../types";
import { formatPrice } from "../format";
import ProbabilityChart from "./ProbabilityChart";
import { ConfidenceBadge, DivergenceBadge } from "./Badges";
import PlatformBreakdown from "./PlatformBreakdown";
import ThresholdList from "./ThresholdList";

export default function ForecastCard({ forecast }: { forecast: AggregateForecast }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="card">
      <div className="card-header">
        <h3>{forecast.period_label}</h3>
        <div className="badge-row">
          <ConfidenceBadge tier={forecast.confidence_tier} totalVolume={forecast.total_volume} />
          <DivergenceBadge pct={forecast.disagreement_pct} high={forecast.high_divergence} sourceCount={forecast.sources.length} />
        </div>
      </div>

      <div className="card-hero">
        <div className="hero-value">{formatPrice(forecast.mean)}</div>
        <div className="hero-sub">
          median {formatPrice(forecast.median)} · σ {formatPrice(forecast.std)}
        </div>
        <div className="hero-ci">
          <span>68%: {formatPrice(forecast.ci_68[0])} – {formatPrice(forecast.ci_68[1])}</span>
          <span>95%: {formatPrice(forecast.ci_95[0])} – {formatPrice(forecast.ci_95[1])}</span>
        </div>
      </div>

      <ProbabilityChart
        gridEdges={forecast.grid_edges}
        pdf={forecast.pdf}
        mean={forecast.mean}
        median={forecast.median}
        ci68={forecast.ci_68}
        ci95={forecast.ci_95}
        sources={forecast.sources}
      />

      <button className="expand-btn" onClick={() => setExpanded((e) => !e)}>
        {expanded ? "Hide details" : `Details (${forecast.sources.length} source${forecast.sources.length === 1 ? "" : "s"})`}
      </button>

      {expanded && (
        <div className="card-details">
          <PlatformBreakdown sources={forecast.sources} />
          <ThresholdList thresholds={forecast.thresholds} />
        </div>
      )}
    </div>
  );
}
