import { useMemo, useRef, useState } from "react";
import type { AggregateForecast, ConfidenceBand, InterpolatedForecast, SpotHistoryPoint } from "../types";
import { formatCompactUsd, formatPrice } from "../format";

// Fan-chart uncertainty cone: one hue (the app's accent blue), opacity
// graduated by confidence level -- narrower/more-likely bands read darker,
// wider/less-likely bands read as a faint wash. See dataviz skill:
// "Sequential = one hue, light->dark" for magnitude encodings; confidence
// level here plays the same role a magnitude would.
const ACCENT = "#3987e5";
const HISTORY_COLOR = "#c3c2b7"; // secondary ink -- "actual" is deliberately NOT the forecast hue

// A gap this wide between two adjacent points (real or model-filled)
// means neither real market data nor the term-structure model bridges
// it -- draw an isolated marker instead of a connecting band. In normal
// operation this should rarely fire: aggregation.py's build_gap_fill_forecasts
// bridges every real-to-real gap with model points, so "isolated" now
// only means the model itself couldn't be built (e.g. fewer than 2 real
// forecasts total).
const ISOLATED_GAP_DAYS = 45;

const W = 960;
const H = 340;
const MARGIN = { top: 20, right: 16, bottom: 30, left: 66 };
const INNER_W = W - MARGIN.left - MARGIN.right;
const INNER_H = H - MARGIN.top - MARGIN.bottom;

interface CPoint {
  t: number;
  median: number;
  bands: ConfidenceBand[];
  isInterpolated: boolean;
  label: string;
}

function toCombinedPoints(forecasts: AggregateForecast[], gapFill: InterpolatedForecast[]): CPoint[] {
  const real: CPoint[] = forecasts.map((f) => ({
    t: new Date(f.target_date).getTime(), median: f.median, bands: f.confidence_bands, isInterpolated: false, label: f.period_label,
  }));
  const interp: CPoint[] = gapFill.map((f) => ({
    t: new Date(f.target_date).getTime(), median: f.median, bands: f.confidence_bands, isInterpolated: true, label: f.period_label,
  }));
  return [...real, ...interp].sort((a, b) => a.t - b.t);
}

function bandOpacity(level: number, minLevel: number, maxLevel: number, faded: boolean): number {
  const t = maxLevel === minLevel ? 1 : (maxLevel - level) / (maxLevel - minLevel);
  const MIN_OP = faded ? 0.03 : 0.06;
  const MAX_OP = faded ? 0.15 : 0.3;
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

export default function FanChart({
  asset, history, forecasts, gapFillForecasts,
}: {
  asset: string; history: SpotHistoryPoint[]; forecasts: AggregateForecast[]; gapFillForecasts: InterpolatedForecast[];
}) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoverT, setHoverT] = useState<number | null>(null);

  const points = useMemo(() => toCombinedPoints(forecasts, gapFillForecasts), [forecasts, gapFillForecasts]);

  if (!history.length && !points.length) {
    return null;
  }

  const now = Date.now();
  const allLevels = points.length ? points[0].bands.map((b) => b.level) : [];
  const minLevel = allLevels.length ? Math.min(...allLevels) : 0.7;
  const maxLevel = allLevels.length ? Math.max(...allLevels) : 0.99;
  const levelsDesc = [...allLevels].sort((a, b) => b - a); // widest first (painted underneath)

  const times = [...history.map((p) => p.t_ms), now, ...points.map((p) => p.t)];
  const minT = Math.min(...times);
  const maxT = Math.max(...times);

  const prices = [...history.map((p) => p.price)];
  for (const pt of points) for (const b of pt.bands) prices.push(b.low, b.high);
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

  // Pairwise segments across the full combined (real + model-filled)
  // timeline. A segment is only skipped (leaving an isolated marker on
  // each side instead) when the gap is wider than the model itself
  // bridges -- see ISOLATED_GAP_DAYS.
  const segments = useMemo(() => {
    const out: { a: CPoint; b: CPoint; faded: boolean }[] = [];
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i], b = points[i + 1];
      const gapDays = (b.t - a.t) / 86_400_000;
      if (gapDays > ISOLATED_GAP_DAYS) continue;
      out.push({ a, b, faded: a.isInterpolated || b.isInterpolated });
    }
    return out;
  }, [points]);

  const connectedIdx = new Set<number>();
  segments.forEach((s) => {
    connectedIdx.add(points.indexOf(s.a));
    connectedIdx.add(points.indexOf(s.b));
  });
  const isolatedPoints = points.filter((_, i) => !connectedIdx.has(i));

  const hasInterpolated = points.some((p) => p.isInterpolated);

  const hoverInfo = useMemo(() => {
    if (hoverT === null) return null;
    if (hoverT <= now) {
      if (!history.length) return null;
      let nearest = history[0];
      for (const p of history) if (Math.abs(p.t_ms - hoverT) < Math.abs(nearest.t_ms - hoverT)) nearest = p;
      return { dateLabel: new Date(nearest.t_ms).toLocaleDateString("en-US", { month: "short", day: "numeric" }), lines: [`actual: ${formatPrice(nearest.price)}`] };
    }
    if (!points.length) return null;
    let nearest = points[0];
    for (const p of points) if (Math.abs(p.t - hoverT) < Math.abs(nearest.t - hoverT)) nearest = p;
    const ci95 = nearest.bands.find((b) => b.level === 0.95) ?? nearest.bands[nearest.bands.length - 1];
    return {
      dateLabel: nearest.label + (nearest.isInterpolated ? " (modeled)" : ""),
      lines: [`median: ${formatPrice(nearest.median)}`, ci95 ? `95%: ${formatPrice(ci95.low)} – ${formatPrice(ci95.high)}` : ""].filter(Boolean),
    };
  }, [hoverT, history, points, now]);

  return (
    <div className="fanchart-wrap">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="fanchart-svg" role="img" aria-label={`${asset} price history and forward confidence cone`}>
        {/* y gridlines + labels */}
        {priceTicks.map((p) => (
          <g key={p}>
            <line x1={MARGIN.left} y1={y(p)} x2={W - MARGIN.right} y2={y(p)} stroke="#2c2c2a" strokeWidth={1} />
            <text x={MARGIN.left - 8} y={y(p) + 3} textAnchor="end" className="fanchart-axis-label">
              {formatCompactUsd(p)}
            </text>
          </g>
        ))}

        {/* confidence bands, one filled polygon per level per segment, widest first */}
        {segments.map((seg, si) =>
          levelsDesc.map((level) => {
            const ba = seg.a.bands.find((bb) => bb.level === level) as ConfidenceBand;
            const bb = seg.b.bands.find((bb) => bb.level === level) as ConfidenceBand;
            const pts = [`${x(seg.a.t)},${y(ba.high)}`, `${x(seg.b.t)},${y(bb.high)}`, `${x(seg.b.t)},${y(bb.low)}`, `${x(seg.a.t)},${y(ba.low)}`];
            return <polygon key={`${si}-${level}`} points={pts.join(" ")} fill={ACCENT} opacity={bandOpacity(level, minLevel, maxLevel, seg.faded)} />;
          })
        )}

        {/* dashed outline on the outermost band of a faded (model-filled) segment -- reinforces "this part is modeled" beyond just lower opacity */}
        {segments.filter((s) => s.faded).map((seg, si) => {
          const outer = levelsDesc[0];
          const ba = seg.a.bands.find((bb) => bb.level === outer) as ConfidenceBand;
          const bb = seg.b.bands.find((bb) => bb.level === outer) as ConfidenceBand;
          return (
            <g key={`outline-${si}`} opacity={0.5}>
              <line x1={x(seg.a.t)} y1={y(ba.high)} x2={x(seg.b.t)} y2={y(bb.high)} stroke={ACCENT} strokeWidth={1} strokeDasharray="3,3" />
              <line x1={x(seg.a.t)} y1={y(ba.low)} x2={x(seg.b.t)} y2={y(bb.low)} stroke={ACCENT} strokeWidth={1} strokeDasharray="3,3" />
            </g>
          );
        })}

        {/* isolated points: no neighbor within ISOLATED_GAP_DAYS -- stacked band markers */}
        {isolatedPoints.map((pt) => {
          const bandsDesc = [...pt.bands].sort((a, b) => b.level - a.level);
          const bw = 14;
          return (
            <g key={pt.t}>
              {bandsDesc.map((b) => (
                <rect
                  key={b.level}
                  x={x(pt.t) - bw / 2}
                  y={y(b.high)}
                  width={bw}
                  height={Math.max(1, y(b.low) - y(b.high))}
                  fill={ACCENT}
                  opacity={bandOpacity(b.level, minLevel, maxLevel, pt.isInterpolated)}
                />
              ))}
            </g>
          );
        })}

        {/* median path: solid where both endpoints are real, dashed where either is model-filled or unconnected */}
        {(() => {
          const lines: { d: string; dashed: boolean }[] = [];
          if (lastHistory && points.length) {
            const first = points[0];
            const gapDays = (first.t - now) / 86_400_000;
            lines.push({ d: `M ${x(now)} ${y(lastHistory.price)} L ${x(first.t)} ${y(first.median)}`, dashed: first.isInterpolated || gapDays > ISOLATED_GAP_DAYS });
          }
          for (const seg of segments) {
            lines.push({ d: `M ${x(seg.a.t)} ${y(seg.a.median)} L ${x(seg.b.t)} ${y(seg.b.median)}`, dashed: seg.faded });
          }
          return lines.map((s, i) => (
            <path key={i} d={s.d} fill="none" stroke={ACCENT} strokeWidth={1.5} strokeDasharray={s.dashed ? "4,4" : undefined} opacity={s.dashed ? 0.55 : 0.9} />
          ));
        })()}

        {/* median dots -- model-filled points draw smaller/hollow to stay visually subordinate to real ones */}
        {points.map((pt) =>
          pt.isInterpolated ? (
            <circle key={pt.t} cx={x(pt.t)} cy={y(pt.median)} r={1.6} fill="none" stroke={ACCENT} strokeWidth={1} opacity={0.6} />
          ) : (
            <circle key={pt.t} cx={x(pt.t)} cy={y(pt.median)} r={2.5} fill={ACCENT} />
          )
        )}

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
              style={{ background: ACCENT, opacity: bandOpacity(lvl, minLevel, maxLevel, false) + 0.15 }}
            />
            {Math.round(lvl * 100)}%
          </span>
        ))}
        {hasInterpolated && (
          <span className="legend-item">
            <span className="legend-swatch legend-swatch-block" style={{ background: ACCENT, opacity: 0.18, border: "1px dashed " + ACCENT }} />
            modeled (no market data)
          </span>
        )}
      </div>
      {hasInterpolated && (
        <p className="fanchart-note">
          Faded, dashed-outline regions are filled by a term-structure model (see the README's "joint cross-date model"),
          not by an active market -- it linearly interpolates the variance and drift implied by the real forecasts on
          either side, the standard way to bridge a deterministic-but-time-varying-volatility process. Solid regions are
          real, market-implied forecasts.
        </p>
      )}
      {isolatedPoints.some((p) => !p.isInterpolated) && (
        <p className="fanchart-note">
          One or more forecasts have no neighbor close enough (in time or model reach) to connect to, and show as an isolated marker instead.
        </p>
      )}
    </div>
  );
}
