import type { SourceBreakdown } from "../types";
import { formatCompactNumber, formatCompactUsd, formatPrice } from "../format";

export default function PlatformBreakdown({ sources }: { sources: SourceBreakdown[] }) {
  return (
    <table className="breakdown-table">
      <thead>
        <tr>
          <th>Platform</th>
          <th>Implied mean</th>
          <th>Volume</th>
          <th>Weight</th>
        </tr>
      </thead>
      <tbody>
        {sources.map((s) => (
          <tr key={s.source_name}>
            <td>
              {s.source_url ? (
                <a href={s.source_url} target="_blank" rel="noreferrer">
                  {s.source_name}
                </a>
              ) : (
                s.source_name
              )}
              {s.is_play_money && (
                <span className="play-money-badge" title={s.raw_note ?? "Play-money volume, not USD -- weight discounted accordingly"}>
                  play money
                </span>
              )}
            </td>
            <td className="num">{formatPrice(s.mean)}</td>
            {/* Play-money volume is a token count, not USD -- formatCompactUsd would print a misleading "$" */}
            <td className="num">{s.is_play_money ? formatCompactNumber(s.volume) : formatCompactUsd(s.volume)}</td>
            <td className="num">{s.is_play_money ? formatCompactNumber(s.weight) : formatCompactUsd(s.weight)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
