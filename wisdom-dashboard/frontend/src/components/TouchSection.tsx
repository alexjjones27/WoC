import type { TouchGroup } from "../types";
import { formatCompactUsd, formatPct, formatPrice } from "../format";

function ThresholdBars({ items }: { items: { price: number; prob: number }[] }) {
  if (!items.length) return <div className="touch-empty">none</div>;
  const max = Math.max(...items.map((i) => i.prob), 0.01);
  return (
    <div className="touch-bars">
      {items.map((i) => (
        <div className="touch-bar-row" key={i.price}>
          <span className="touch-bar-label">{formatPrice(i.price)}</span>
          <div className="touch-bar-track">
            <div className="touch-bar-fill" style={{ width: `${(i.prob / max) * 100}%` }} />
          </div>
          <span className="touch-bar-value">{formatPct(i.prob)}</span>
        </div>
      ))}
    </div>
  );
}

export default function TouchSection({ asset, groups }: { asset: string; groups: TouchGroup[] }) {
  if (!groups.length) return null;
  return (
    <section className="touch-section">
      <div className="touch-section-header">
        <h2>Touch probability (longer horizon)</h2>
        <p>
          Chance {asset} ever crosses a price before the date shown — a different question from the price-at-date
          forecasts above ("is it above $X on this date"), and deliberately not combined with them.
        </p>
      </div>
      <div className="grid">
        {groups.map((g) => (
          <div className="card touch-card" key={g.expiry_date}>
            <div className="card-header">
              <h3>By {g.period_label}</h3>
            </div>
            {g.sources.map((s) => {
              const above = s.thresholds
                .filter((t) => t.direction === "above")
                .sort((a, b) => a.price - b.price)
                .map((t) => ({ price: t.price, prob: t.prob_touch }));
              const below = s.thresholds
                .filter((t) => t.direction === "below")
                .sort((a, b) => b.price - a.price)
                .map((t) => ({ price: t.price, prob: t.prob_touch }));
              return (
                <div className="touch-source" key={s.source_name}>
                  <div className="touch-source-header">
                    <span className="touch-source-name">
                      {s.source_url ? (
                        <a href={s.source_url} target="_blank" rel="noreferrer">
                          {s.source_name}
                        </a>
                      ) : (
                        s.source_name
                      )}
                    </span>
                    <span className="touch-source-vol">{formatCompactUsd(s.total_volume)} volume</span>
                  </div>
                  <div className="touch-columns">
                    <div>
                      <div className="touch-col-label">Ever goes above</div>
                      <ThresholdBars items={above} />
                    </div>
                    <div>
                      <div className="touch-col-label">Ever goes below</div>
                      <ThresholdBars items={below} />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}
