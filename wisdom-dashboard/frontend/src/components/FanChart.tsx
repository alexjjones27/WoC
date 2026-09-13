import { useMemo, useRef, useState } from "react";
import type { AggregateForecast, ConfidenceBand, SpotHistoryPoint } from "../types";
import { formatCompactUsd, formatPrice } from "../format";

// Fan-chart uncertainty cone: one hue (the app's accent blue), opacity
// graduated by confidence level -- narrower/more-likely bands read darker,
// wider/less-likely bands read as a faint wash. See dataviz skill:
// "Sequential = one hue, light->dark" for magnitude encodings; confidence
// level here plays the same role a magnitude would.
const ACCENT = "#3987e5";
const HISTORY_COLOR = "#c3c2b7"; // secondary ink -- "actual" is deliberately NOT the forecast hue

// Forecast dates further apart than this are NOT connected by a filled
// band polygon -- there is no real market data in between (see
// wisdom-dashboard/README.md's note on the 1-week-to-3-month gap), so
// bridging them with an interpolated cone would imply data that doesn't
// exist. Each such isolated date instead renders as its own small stacked
// marker, joined to its neighbor only by a dashed median line.
const MAX_CONNECT_GAP_DAYS = 3;

const W = 960;
const H = 340;
const MARGIN = { top: 20, right: 16, bottom: 30, left: 66 };
const INNER_W = W - MARGIN.left - MARGIN.right;
const INNER_H = H - MARGIN.top - MARGIN.bottom;

interface Point {
  t: number;
  f: AggregateForecast;
}

function clusterForecasts(forecasts: AggregateForecast[]): Point[][] {
  if (!forecasts.length) return [];
  const sorted = [...forecasts]
    .sort((a, b) => a.target_date.localeCompare(b.target_date))
    .map((f) => ({ t: new Date(f.target_date).getTime(), f }));
  const clusters: Point[][] = [[sorted[0]]];
  for (let i = 1; i < sorted.length; i++) {
    const gapDays = (sorted[i].t - sorted[i - 1].t) / 86_400_000;
    if (gapDays <= MAX_CONNECT_GAP_DAYS) {
      clusters[clusters.length - 1].push(sorted[i]);
    } else {
      clusters.push([sorted[i]]);
    }
  }
  return clusters;
}

function bandOpacity(level: number, minLevel: number, maxLevel: number): number {
  const t = maxLevel === minLevel ? 1 : (maxLevel - level) / (maxLevel - minLevel);
  const MIN_OP = 0.06;
  const MAX_OP = 0.30;
  return MIN_OP + t * (MAX_OP - MIN_OP);
}

function niceTicks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo;
  if (span <= 0) return [lo];
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  let step = mag * 10;
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (m * mag >= raw) {
      step = m * mag;
      break;
    }
  }
  const start = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= hi; v += step) out.push(v);
  return out;
}

export default function FanChart({ history, forecasts }: { history: SpotHistoryPoint[]; forecasts: AggregateForecast[] }) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoverT, setHoverT] = useState<number | null>(null);

  const clusters = useMemo(() => clusterForecasts(forecasts), [forecasts]);

  if (!history.length && !forecasts.length) {
    return null;
  }

  const now = Date.now();
  const allLevels = forecasts.length ? forecasts[0].confidence_bands.map((b) => b.level) : [];
  const minLevel = allLevels.length ? Math.min(...allLevels) : 0.7;
  const maxLevel = allLevels.length ? Math.max(...allLevels) : 0.99;
  const levelsDesc = [...allLevels].sort((a, b) => b - a); // widest first (painted underneath)

  const times = [...history.map((p) => p.t_ms), now, ...forecasts.map((f) => new Date(f.target_date).getTime())];
  const minT = Math.min(...times);
  const maxT = Math.max(...times);

  const prices = [...history.map((p) => p.price)];
  for (const f of forecasts) for (const b of f.confidence_bands) prices.push(b.low, b.high);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const pricePad = (maxP - minP) * 0.06 || maxP * 0.05;
  const yLo = Math.max(0, minP - pricePad);
  const yHi = maxP + pricePad;

  const x = (t: number) => MARGIN.left + ((t - minT) / (maxT - minT || 1)) * INNER_W;
  const y = (p: number) => MARGIN.top + INNER_H - ((p - yLo) / (yHi - yLo || 1)) * INNER_H;

  const priceTicks = niceTicks(yLo, yHi, 5);

  // Evenly-spaced date ticks (calendar-based, not "nice numbers" -- a price
  // tick heuristic doesn't make sense for time).
  const dateTicks: number[] = [];
  const tickCount = 7;
  for (let i = 0; i <= tickCount; i++) dateTicks.push(minT + ((maxT - minT) * i) / tickCount);

  const lastHistory = history.length ? history[history.length - 1] : null;

  const hoverInfo = useMemo(() => {
    if (hoverT === null) return null;
    if (hoverT <= now) {
      if (!history.length) return null;
      let nearest = history[0];
      for (const p of history) if (Math.abs(p.t_ms - hoverT) < Math.abs(nearest.t_ms - hoverT)) nearest = p;
      return { dateLabel: new Date(nearest.t_ms).toLocaleDateString("en-US", { month: "short", day: "numeric" }), lines: [`actual: ${formatPrice(nearest.price)}`] };
    }
    if (!forecasts.length) return null;
    let nearest = forecasts[0];
    for (const f of forecasts) {
      if (Math.abs(new Date(f.target_date).getTime() - hoverT) < Math.abs(new Date(nearest.target_date).getTime() - hoverT)) nearest = f;
    }
    const ci95 = nearest.confidence_bands.find((b) => b.level === 0.95) ?? nearest.confidence_bands[nearest.confidence_bands.length - 1];
    return {
      dateLabel: nearest.period_label,
      lines: [`median: ${formatPrice(nearest.median)}`, ci95 ? `95%: ${formatPrice(ci95.low)} – ${formatPrice(ci95.high)}` : ""].filter(Boolean),
    };
  }, [hoverT, history, forecasts, now]);

  return (
    <div className="fanchart-wrap">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="fanchart-svg" role="img" aria-label="BTC price history and forward confidence cone">
        {/* y gridlines + labels */}
        {priceTicks.map((p) => (
          <g key={p}>
            <line x1={MARGIN.left} y1={y(p)} x2={W - MARGIN.right} y2={y(p)} stroke="#2c2c2a" strokeWidth={1} />
            <text x={MARGIN.left - 8} y={y(p) + 3} textAnchor="end" className="fanchart-axis-label">
              {formatCompactUsd(p)}
            </text>
          </g>
        ))}

        {/* confidence bands, one filled polygon per level per cluster, widest first */}
        {clusters.map((cluster, ci) =>
          cluster.length >= 2
            ? levelsDesc.map((level) => {
                const top = cluster.map((pt) => {
                  const b = pt.f.confidence_bands.find((bb) => bb.level === level) as ConfidenceBand;
                  return `${x(pt.t)},${y(b.high)}`;
                });
                const bottom = [...cluster]
                  .reverse()
                  .map((pt) => {
                    const b = pt.f.confidence_bands.find((bb) => bb.level === level) as ConfidenceBand;
                    return `${x(pt.t)},${y(b.low)}`;
                  });
                return (
                  <polygon
                    key={`${ci}-${level}`}
                    points={[...top, ...bottom].join(" ")}
                    fill={ACCENT}
                    opacity={bandOpacity(level, minLevel, maxLevel)}
                  />
                );
              })
            : null
        )}

        {/* isolated (unconnected) forecast points: stacked band markers */}
        {clusters
          .filter((c) => c.length === 1)
          .map((c) => {
            const pt = c[0];
            const bandsDesc = [...pt.f.confidence_bands].sort((a, b) => b.level - a.level);
            const bw = 14;
            return (
              <g key={pt.f.target_date}>
                {bandsDesc.map((b) => (
                  <rect
                    key={b.level}
                    x={x(pt.t) - bw / 2}
                    y={y(b.high)}
                    width={bw}
                    height={Math.max(1, y(b.low) - y(b.high))}
                    fill={ACCENT}
                    opacity={bandOpacity(b.level, minLevel, maxLevel)}
                  />
                ))}
              </g>
            );
          })}

        {/* median path: solid within a cluster, dashed across gaps (incl. from last actual to first forecast) */}
        {(() => {
          const segments: { d: string; dashed: boolean }[] = [];
          const waypoints: { t: number; p: number }[] = [];
          if (lastHistory) waypoints.push({ t: now, p: lastHistory.price });
          for (const cluster of clusters) {
            for (const pt of cluster) waypoints.push({ t: pt.t, p: pt.f.median });
          }
          for (let i = 0; i < waypoints.length - 1; i++) {
            const a = waypoints[i];
            const b = waypoints[i + 1];
            const gapDays = (b.t - a.t) / 86_400_000;
            segments.push({
              d: `M ${x(a.t)} ${y(a.p)} L ${x(b.t)} ${y(b.p)}`,
              dashed: gapDays > MAX_CONNECT_GAP_DAYS,
            });
          }
          return segments.map((s, i) => (
            <path key={i} d={s.d} fill="none" stroke={ACCENT} strokeWidth={1.5} strokeDasharray={s.dashed ? "4,4" : undefined} opacity={0.9} />
          ));
        })()}

        {/* median dots */}
        {forecasts.map((f) => (
          <circle key={f.target_date} cx={x(new Date(f.target_date).getTime())} cy={y(f.median)} r={2.5} fill={ACCENT} />
        ))}

        {/* historical actuals line */}
        {history.length > 1 && (
          <path
            d={history.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.t_ms)} ${y(p.price)}`).join(" ")}
            fill="none"
            stroke={HISTORY_COLOR}
            strokeWidth={2}
            strokeLinejoin="round"
          />
        )}

        {/* "now" marker */}
        <line x1={x(now)} y1={MARGIN.top} x2={x(now)} y2={MARGIN.top + INNER_H} stroke="#898781" strokeWidth={1} strokeDasharray="2,3" />
        <text x={x(now)} y={MARGIN.top - 6} textAnchor="middle" className="fanchart-now-label">
          now
        </text>

        {/* x ticks */}
        {dateTicks.map((t) => (
          <text key={t} x={x(t)} y={H - 8} textAnchor="middle" className="fanchart-axis-label">
            {new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
          </text>
        ))}

        {hoverT !== null && (
          <line x1={x(hoverT)} y1={MARGIN.top} x2={x(hoverT)} y2={MARGIN.top + INNER_H} stroke="#ffffff" strokeWidth={1} opacity={0.3} />
        )}

        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={INNER_W}
          height={INNER_H}
          fill="transparent"
          onPointerMove={(e) => {
            const rect = svgRef.current?.getBoundingClientRect();
            if (!rect) return;
            const px = ((e.clientX - rect.left) / rect.width) * W;
            const t = minT + ((px - MARGIN.left) / INNER_W) * (maxT - minT);
            setHoverT(t);
          }}
          onPointerLeave={() => setHoverT(null)}
        />
      </svg>

      {hoverInfo && (
        <div className="fanchart-tooltip">
          <strong>{hoverInfo.dateLabel}</strong>
          {hoverInfo.lines.map((l) => (
            <div key={l}>{l}</div>
          ))}
        </div>
      )}

      <div className="fanchart-legend">
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: HISTORY_COLOR }} />
          actual price
        </span>
        {[...allLevels].sort((a, b) => a - b).map((lvl) => (
          <span className="legend-item" key={lvl}>
            <span
              className="legend-swatch legend-swatch-block"
              style={{ background: ACCENT, opacity: bandOpacity(lvl, minLevel, maxLevel) + 0.15 }}
            />
            {Math.round(lvl * 100)}%
          </span>
        ))}
      </div>
      {clusters.some((c) => c.length === 1) && (
        <p className="fanchart-note">
          Gaps in the cone mark dates with no active point-in-time market between them (see the note on this in the README) --
          the isolated bars are real forecasts, just not connected to a continuous daily series.
        </p>
      )}
    </div>
  );
}
