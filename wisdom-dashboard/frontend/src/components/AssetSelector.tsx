import type { AssetInfo } from "../types";

export default function AssetSelector({
  assets,
  selected,
  onSelect,
}: {
  assets: AssetInfo[];
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  return (
    <div className="asset-selector">
      {assets.map((a) => (
        <button
          key={a.symbol}
          className={`asset-pill ${selected === a.symbol ? "active" : ""} ${a.enabled ? "" : "disabled"}`}
          disabled={!a.enabled}
          title={a.enabled ? a.display_name : `${a.display_name} — coming soon`}
          onClick={() => a.enabled && onSelect(a.symbol)}
        >
          {a.symbol}
        </button>
      ))}
    </div>
  );
}
