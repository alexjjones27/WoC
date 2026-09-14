import type { ConfidenceTier } from "../types";
import { formatCompactUsd } from "../format";

// Status colors are fixed, never themed, and never carry meaning by hue
// alone -- each badge pairs its color with a distinct shape glyph and a
// text label (see dataviz skill: status palette + icon+label rule).
const TIER_STYLE: Record<ConfidenceTier, { color: string; glyph: string; label: string }> = {
  high: { color: "#0ca30c", glyph: "●", label: "High confidence" },
  medium: { color: "#fab219", glyph: "▲", label: "Medium confidence" },
  low: { color: "#ec835a", glyph: "■", label: "Low confidence" },
};

export function ConfidenceBadge({
  tier,
  totalVolume,
  effectiveVolume,
}: {
  tier: ConfidenceTier;
  totalVolume: number;
  effectiveVolume?: number;
}) {
  const s = TIER_STYLE[tier];
  // The tier is scored on concentration-ADJUSTED volume, so when the two
  // differ materially the badge says so -- otherwise a $6M market backed by
  // two wallets reads the same as $6M backed by a crowd.
  const discounted = effectiveVolume !== undefined && effectiveVolume < totalVolume * 0.9;
  const title = discounted
    ? `Volume behind this forecast: ${formatCompactUsd(totalVolume)} traded, but concentrated among few wallets — scored as ${formatCompactUsd(effectiveVolume)}. See backend/concentration.py.`
    : `Total market volume behind this forecast: ${formatCompactUsd(totalVolume)}`;
  return (
    <span className="badge" style={{ color: s.color, borderColor: s.color }} title={title}>
      <span aria-hidden="true">{s.glyph}</span> {s.label}
      {discounted && <span className="badge-note"> (concentration-adjusted)</span>}
    </span>
  );
}

export function DivergenceBadge({ pct, high, sourceCount }: { pct: number; high: boolean; sourceCount: number }) {
  if (sourceCount < 2) {
    return (
      <span className="badge badge-muted" title="Only one platform has an active market for this date -- nothing to compare against">
        single source
      </span>
    );
  }
  if (!high) {
    return (
      <span className="badge badge-muted" title="How much platforms' individual mean forecasts differ from each other">
        sources agree (±{pct.toFixed(1)}%)
      </span>
    );
  }
  return (
    <span className="badge" style={{ color: "#fab219", borderColor: "#fab219" }} title="Platforms imply meaningfully different prices for this date -- treat the aggregate with caution">
      <span aria-hidden="true">▲</span> sources diverge (±{pct.toFixed(1)}%)
    </span>
  );
}
