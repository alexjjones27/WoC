import type { ThresholdRow } from "../types";
import { formatPct, formatPrice } from "../format";

export default function ThresholdList({ thresholds }: { thresholds: ThresholdRow[] }) {
  if (!thresholds.length) return null;
  return (
    <ul className="threshold-list">
      {thresholds.map((t) => (
        <li key={t.threshold}>
          <span className="threshold-prob">{formatPct(t.prob_gt_aggregate)}</span>
          <span className="threshold-text">chance BTC {'>'} {formatPrice(t.threshold)}</span>
        </li>
      ))}
    </ul>
  );
}
