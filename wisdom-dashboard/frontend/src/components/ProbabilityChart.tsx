import { useMemo, useRef, useState } from "react";
import type { SourceBreakdown } from "../types";
import { formatPrice, formatPct } from "../format";

// Stable per-source-name color assignment (categorical slots 2-5 from the
// palette -- slot 1/blue is reserved for the aggregate curve itself, which
// is a magnitude/value mark, not one of the "identity" series). Keeping
// this a fixed lookup rather than "next unused slot" means a given
// platform is always the same color across cards and refreshes.
const SOURCE_COLORS: Record<string, string> = {
  polymarket: "#d95926", // slot 2 orange (dark-surface step)
  kalshi: "#199e70", // slot 3 aqua
  manifold: "#c98500", // slot 4 yellow
  futuur: "#d55181", // slot 5 magenta
};
const FALLBACK_COLOR = "#9085e9"; // slot 7 violet

const AGGREGATE_COLOR = "#3987e5"; // slot 1 blue (dark-surface step) -- sequential/value hue

function sourceColor(name: string): string {
  return SOURCE_COLORS[name] ?? FALLBACK_COLOR;
}

interface Props {
  asset: string;
  gridEdges: number[];
  pdf: number[];
  mean: number;
  median: number;
  ci68: [number, number];
  ci95: [number, number];
  sources: SourceBreakdown[];
}

const W = 560;
const H = 190;
const MARGIN = { top: 26, right: 10, bottom: 24, left: 10 };
const INNER_W = W - MARGIN.left - MARGIN.right;
const INNER_H = H - MARGIN.top - MARGIN.bottom;

function stepAreaPath(edges: number[], values: number[], x: (v: number) => number, y: (v: number) => number): string {
  if (edges.length < 2) return "";
  let d = `M ${x(edges[0]).toFixed(1)} ${y(0).toFixed(1)}`;
  for (let i = 0; i < values.length; i++) {
    d += ` L ${x(edges[i]).toFixed(1)} ${y(values[i]).toFixed(1)} L ${x(edges[i + 1]).toFixed(1)} ${y(values[i]).toFixed(1)}`;
  }
  d += ` L ${x(edges[edges.length - 1]).toFixed(1)} ${y(0).toFixed(1)} Z`;
  return d;
}

function stepLinePath(edges: number[], values: number[], x: (v: number) => number, y: (v: number) => number): string {
  if (edges.length < 2) return "";
  let d = `M ${x(edges[0]).toFixed(1)} ${y(values[0]).toFixed(1)}`;
  for (let i = 0; i < values.length; i++) {
    d += ` L ${x(edges[i]).toFixed(1)} ${y(values[i]).toFixed(1)} L ${x(edges[i + 1]).toFixed(1)} ${y(values[i]).toFixed(1)}`;
  }
  return d;
}

function niceTicks(lo: number, hi: number, count = 5): number[] {
  const span = hi - lo;
  if (span <= 0) return [lo];
  const rawStep = span / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const candidates = [1, 2, 2.5, 5, 10];
  let step = magnitude * 10;
  for (const m of candidates) {
    if (m * magnitude >= rawStep) {
      step = m * magnitude;
      break;
    }
  }
  const start = Math.ceil(lo / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= hi; v += step) ticks.push(v);
  return ticks;
}

export default function ProbabilityChart({ asset, gridEdges, pdf, mean, median, ci68, ci95, sources }: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const overlaySources = useMemo(
    () => sources.filter((s) => s.pdf.length === pdf.length && s.pdf.length > 0),
    [sources, pdf.length]
  );

  if (!gridEdges.length || !pdf.length) {
    return <div className="chart-empty">No distribution data</div>;
  }

  const lo = gridEdges[0];
  const hi = gridEdges[gridEdges.length - 1];
  const maxPdf = Math.max(...pdf, ...overlaySources.flatMap((s) => s.pdf), 1e-12) * 1.18;

  const x = (v: number) => MARGIN.left + ((v - lo) / (hi - lo)) * INNER_W;
  const y = (v: number) => MARGIN.top + INNER_H - (v / maxPdf) * INNER_H;

  const areaPath = stepAreaPath(gridEdges, pdf, x, y);
  const linePath = stepLinePath(gridEdges, pdf, x, y);
  const ticks = niceTicks(lo, hi, 5);

  const cellWidth = (hi - lo) / (gridEdges.length - 1);

  function handleMove(e: React.PointerEvent<SVGRectElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const price = lo + ((px - MARGIN.left) / INNER_W) * (hi - lo);
    let idx = Math.floor((price - lo) / cellWidth);
    idx = Math.max(0, Math.min(pdf.length - 1, idx));
    setHoverIdx(idx);
  }

  // A single grid cell is a very narrow slice of the full range (the grid
  // is always exactly GRID_CELLS wide, so a broader distribution just
  // means a physically wider chart divided the same number of ways) --
  // reading one cell's mass in isolation reads as misleadingly small next
  // to a visibly tall peak the eye takes in as a whole. Sum a real
  // neighborhood of cells around the hover point instead, and show its
  // actual price bounds so the number's meaning is unambiguous.
  const HOVER_WINDOW_CELLS = 10; // each side => ~7% of the grid's total width
  const hoverWindow = useMemo(() => {
    if (hoverIdx === null) return null;
    const loIdx = Math.max(0, hoverIdx - HOVER_WINDOW_CELLS);
    const hiIdx = Math.min(pdf.length - 1, hoverIdx + HOVER_WINDOW_CELLS);
    let mass = 0;
    for (let i = loIdx; i <= hiIdx; i++) mass += pdf[i];
    return { mass, lo: gridEdges[loIdx], hi: gridEdges[hiIdx + 1] };
  }, [hoverIdx, pdf, gridEdges]);

  return (
    <div className="chart-wrap">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="chart-svg" role="img" aria-label={`Probability distribution of ${asset} price`}>
        {/* CI bands, behind everything */}
        <rect x={x(ci95[0])} y={MARGIN.top} width={Math.max(0, x(ci95[1]) - x(ci95[0]))} height={INNER_H} fill={AGGREGATE_COLOR} opacity={0.07} />
        <rect x={x(ci68[0])} y={MARGIN.top} width={Math.max(0, x(ci68[1]) - x(ci68[0]))} height={INNER_H} fill={AGGREGATE_COLOR} opacity={0.1} />

        {/* baseline */}
        <line x1={MARGIN.left} y1={MARGIN.top + INNER_H} x2={W - MARGIN.right} y2={MARGIN.top + INNER_H} stroke="#383835" strokeWidth={1} />

        {/* aggregate distribution */}
        <path d={areaPath} fill={AGGREGATE_COLOR} opacity={0.14} />
        <path d={linePath} fill="none" stroke={AGGREGATE_COLOR} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />

        {/* per-source overlay lines */}
        {overlaySources.map((s) => (
          <path
            key={s.source_name}
            d={stepLinePath(gridEdges, s.pdf, x, y)}
            fill="none"
            stroke={sourceColor(s.source_name)}
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            opacity={0.9}
          />
        ))}

        {/* median (dashed, muted) */}
        <line x1={x(median)} y1={MARGIN.top} x2={x(median)} y2={MARGIN.top + INNER_H} stroke="#c3c2b7" strokeWidth={1} strokeDasharray="3,3" />
        <text x={x(median)} y={MARGIN.top - 14} className="chart-refline-label" textAnchor="middle" fill="#c3c2b7">
          median {formatPrice(median)}
        </text>

        {/* mean (solid, accent) */}
        <line x1={x(mean)} y1={MARGIN.top} x2={x(mean)} y2={MARGIN.top + INNER_H} stroke={AGGREGATE_COLOR} strokeWidth={1.5} />
        <text x={x(mean)} y={MARGIN.top - 2} className="chart-refline-label" textAnchor="middle" fill={AGGREGATE_COLOR}>
          mean {formatPrice(mean)}
        </text>

        {/* x ticks */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} y1={MARGIN.top + INNER_H} x2={x(t)} y2={MARGIN.top + INNER_H + 4} stroke="#383835" strokeWidth={1} />
            <text x={x(t)} y={H - 6} className="chart-tick-label" textAnchor="middle">
              {formatPrice(t)}
            </text>
          </g>
        ))}

        {/* hover crosshair */}
        {hoverIdx !== null && (
          <line
            x1={x((gridEdges[hoverIdx] + gridEdges[hoverIdx + 1]) / 2)}
            y1={MARGIN.top}
            x2={x((gridEdges[hoverIdx] + gridEdges[hoverIdx + 1]) / 2)}
            y2={MARGIN.top + INNER_H}
            stroke="#ffffff"
            strokeWidth={1}
            opacity={0.35}
          />
        )}

        {/* hit layer */}
        <rect
          x={MARGIN.left}
          y={MARGIN.top}
          width={INNER_W}
          height={INNER_H}
          fill="transparent"
          onPointerMove={handleMove}
          onPointerLeave={() => setHoverIdx(null)}
        />
      </svg>

      {hoverWindow !== null && (
        <div className="chart-tooltip">
          <strong>{formatPct(hoverWindow.mass, 1)}</strong> chance between {formatPrice(hoverWindow.lo)} and {formatPrice(hoverWindow.hi)}
        </div>
      )}

      {overlaySources.length > 1 && (
        <div className="chart-legend">
          <span className="legend-item">
            <span className="legend-swatch" style={{ background: AGGREGATE_COLOR }} />
            aggregate
          </span>
          {overlaySources.map((s) => (
            <span className="legend-item" key={s.source_name}>
              <span className="legend-swatch" style={{ background: sourceColor(s.source_name) }} />
              {s.source_name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
