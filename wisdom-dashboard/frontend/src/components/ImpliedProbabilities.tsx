import type { ThresholdRow } from "../types";
import { formatPct, formatPrice } from "../format";

/**
 * The card's headline: a few "N% chance {asset} is above $X" rows.
 *
 * These lead instead of the point forecast deliberately. The calibration
 * backtest (results/btc_price_market_calibration/report.md) found the
 * market's implied MEAN loses to "assume nothing changes" at every lead
 * time tested, and its whole distribution loses to a trailing-volatility
 * random walk -- but bucket-level probabilities are the part that measured
 * well (mid-range bins track the diagonal; only the cheap tail is biased,
 * and that bias is corrected for in aggregation.py). So the number given
 * the most visual weight is the one the evidence actually supports.
 */
export default function ImpliedProbabilities({
  asset,
  thresholds,
  count = 3,
}: {
  asset: string;
  thresholds: ThresholdRow[];
  count?: number;
}) {
  const rows = pickInformative(thresholds, count);
  if (!rows.length) return null;

  return (
    <div className="implied-probs">
      <div className="implied-probs-label">Market-implied probability</div>
      <ul>
        {rows.map((t) => (
          <li key={t.threshold}>
            <span className="ip-bar" aria-hidden="true">
              <span className="ip-bar-fill" style={{ width: `${Math.round(t.prob_gt_aggregate * 100)}%` }} />
            </span>
            <span className="ip-pct">{formatPct(t.prob_gt_aggregate)}</span>
            <span className="ip-text">
              {asset} above {formatPrice(t.threshold)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Pick the rows that actually say something. A ladder of thresholds mostly
 * contains near-0% and near-100% rows that carry no information ("99%
 * chance BTC is above $10"); the informative ones sit around the middle of
 * the distribution. Targets ~75/50/25% and takes the nearest available
 * threshold to each, without repeating one.
 */
function pickInformative(thresholds: ThresholdRow[], count: number): ThresholdRow[] {
  const usable = thresholds.filter((t) => t.prob_gt_aggregate > 0.02 && t.prob_gt_aggregate < 0.98);
  const pool = usable.length >= count ? usable : thresholds;
  if (!pool.length) return [];

  // Evenly spaced across the informative middle: 0.75 / 0.50 / 0.25 for
  // the default three rows.
  const targets =
    count === 1 ? [0.5] : Array.from({ length: count }, (_, i) => 0.75 - i * (0.5 / (count - 1)));

  const chosen: ThresholdRow[] = [];
  for (const target of targets) {
    const remaining = pool.filter((t) => !chosen.includes(t));
    if (!remaining.length) break;
    remaining.sort((a, b) => Math.abs(a.prob_gt_aggregate - target) - Math.abs(b.prob_gt_aggregate - target));
    chosen.push(remaining[0]);
  }
  return chosen.sort((a, b) => a.threshold - b.threshold);
}
