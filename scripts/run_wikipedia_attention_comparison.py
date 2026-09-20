"""The other end of the error bar: compare smart money's dollar-weighted
13F consensus against genuine retail attention (Wikipedia pageviews) for
the same stocks, plus a curated set of well-known retail-favorite names
that institutions largely ignore -- to surface the DIVERGENCE, not just
put both lists side by side.

Usage: python3 scripts/run_wikipedia_attention_comparison.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from wikipedia_attention import get_attention_for_company  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results" / "wikipedia_attention_comparison"

# Well-known retail-favorite / "meme stock" names that get outsized retail
# attention relative to institutional interest -- included specifically to
# surface stocks smart money's 13F panel doesn't touch at all, which the
# smart-money-holdings-only comparison can't show by construction.
RETAIL_FAVORITES = [
    ("GME", "GameStop"), ("AMC", "AMC Entertainment"), ("PLTR", "Palantir Technologies"),
    ("RIVN", "Rivian"), ("LCID", "Lucid Motors"), ("HOOD", "Robinhood Markets"),
    ("COIN", "Coinbase"), ("MARA", "MARA Holdings"), ("RIOT", "Riot Platforms"),
    ("SOFI", "SoFi Technologies"), ("TSLA", "Tesla, Inc."), ("NIO", "Nio Inc."),
]


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = json.loads((REPO_ROOT / "results" / "sec_13f_snapshot" / "snapshot.json").read_text())

    smart_money_rows = []
    print("Fetching Wikipedia attention for smart-money consensus holdings...")
    for h in snapshot["consensus_portfolio"]:
        query = h["name"].title()
        attn = get_attention_for_company(query)
        smart_money_rows.append({
            "ticker": h["ticker"], "name": h["name"], "smart_money_weight": h["weight"],
            "n_funds": h["n_funds"], "attention": attn,
        })
        print(f"  {h['ticker'] or '?':6s} {h['name'][:30]:30s} weight={h['weight']*100:5.2f}%  "
              f"attention_ratio={attn['attention_ratio']:.2f}" if attn else f"  {h['ticker'] or '?':6s} {h['name'][:30]:30s} -- no attention data")

    print("\nFetching Wikipedia attention for retail-favorite names...")
    retail_rows = []
    for ticker, query in RETAIL_FAVORITES:
        attn = get_attention_for_company(query)
        in_smart_money = any(r["ticker"] == ticker for r in smart_money_rows)
        retail_rows.append({
            "ticker": ticker, "name": query, "in_smart_money_panel": in_smart_money, "attention": attn,
        })
        print(f"  {ticker:6s} {query[:30]:30s}  attention_ratio={attn['attention_ratio']:.2f}"
              f"  in_smart_money_panel={in_smart_money}" if attn else f"  {ticker:6s} {query[:30]:30s} -- no attention data")

    result = {"smart_money": smart_money_rows, "retail_favorites": retail_rows}
    with open(RESULTS_DIR / "comparison.json", "w") as f:
        json.dump(result, f, indent=2)

    write_report(smart_money_rows, retail_rows)
    print(f"\nWrote report to {RESULTS_DIR / 'report.md'}")


def write_report(smart_money_rows, retail_rows) -> None:
    lines = []
    lines.append("# The other end of the error bar: retail attention vs. smart money\n")
    lines.append(
        "Smart money's 13F consensus is a dollar-weighted vote by ~10 "
        "concentrated active managers. This compares it against genuine "
        "retail attention -- Wikipedia pageviews for the same companies, "
        "relative to each company's own 90-day baseline (a ratio above 1.0 "
        "means more attention lately than usual; this is attention, not "
        "opinion -- it doesn't say whether that attention is bullish or "
        "bearish).\n"
    )
    lines.append("## Smart-money holdings, with retail attention alongside\n")
    lines.append("| Ticker | Company | Smart-money weight | Held by | Retail attention ratio |")
    lines.append("|---|---|---|---|---|")
    for r in sorted(smart_money_rows, key=lambda x: -x["smart_money_weight"]):
        attn = r["attention"]
        ratio = f"{attn['attention_ratio']:.2f}x" if attn else "n/a"
        lines.append(f"| {r['ticker'] or 'n/a'} | {r['name'].title()} | {r['smart_money_weight']*100:.1f}% | {r['n_funds']}/10 | {ratio} |")

    lines.append("\n## Retail-favorite names smart money's panel doesn't hold\n")
    lines.append("| Ticker | Company | In smart-money panel? | Retail attention ratio |")
    lines.append("|---|---|---|---|")
    for r in retail_rows:
        attn = r["attention"]
        ratio = f"{attn['attention_ratio']:.2f}x" if attn else "n/a"
        lines.append(f"| {r['ticker']} | {r['name']} | {'yes' if r['in_smart_money_panel'] else 'no'} | {ratio} |")

    lines.append("\n## Caveats\n")
    lines.append(
        "- Attention, not opinion: a pageview spike doesn't say whether the "
        "crowd is bullish or bearish, only that they're paying attention.\n"
        "- No historical backtest here (unlike the smart-money side) -- "
        "this is a current snapshot, not yet tested for predictive value.\n"
        "- Company-name to Wikipedia-article resolution is best-effort "
        "(MediaWiki's opensearch, top match) -- a handful of ambiguous "
        "names could resolve to the wrong article.\n"
    )
    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
