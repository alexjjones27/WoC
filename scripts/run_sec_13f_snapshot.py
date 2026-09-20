"""Current-quarter snapshot: today's top-10 active hedge funds by SEC 13F,
their aggregated consensus portfolio, and quarter-over-quarter position
changes. Fast (uses cached SEC data) -- the historical NAV backtest is a
separate, much longer-running script: run_sec_13f_backtest.py.

Usage: python3 scripts/run_sec_13f_snapshot.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sec_13f_wisdom import (  # noqa: E402
    download_period,
    select_top_funds,
    extract_holdings,
    build_consensus_portfolio,
    classify_position_changes,
)

RESULTS_DIR = REPO_ROOT / "results" / "sec_13f_snapshot"
CURR_HREF = "/files/datastandardsinnovation/data/form-13f-data-sets/01jun2026-31aug2026_form13f.zip"
PREV_HREF = "/files/structureddata/data/form-13f-data-sets/01mar2026-31may2026_form13f.zip"


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("Downloading current + prior quarter 13F data...")
    pdir_curr = download_period(CURR_HREF)
    pdir_prev = download_period(PREV_HREF)

    top10_curr = select_top_funds(pdir_curr)
    top10_prev = select_top_funds(pdir_prev)
    print(f"Top 10 funds ({top10_curr['report_period'].iloc[0]}):")
    for _, row in top10_curr.iterrows():
        print(f"  {row['FILINGMANAGER_NAME']:40s} ${row['TABLEVALUETOTAL']/1e9:8.1f}B  {row['TABLEENTRYTOTAL']} positions")

    fund_names_curr = dict(zip(top10_curr["ACCESSION_NUMBER"], top10_curr["FILINGMANAGER_NAME"]))
    fund_names_prev = dict(zip(top10_prev["ACCESSION_NUMBER"], top10_prev["FILINGMANAGER_NAME"]))
    cik_curr = dict(zip(top10_curr["ACCESSION_NUMBER"], top10_curr["CIK"]))
    cik_prev = dict(zip(top10_prev["ACCESSION_NUMBER"], top10_prev["CIK"]))
    display_names = dict(zip(top10_curr["CIK"], top10_curr["FILINGMANAGER_NAME"]))

    holdings_curr = extract_holdings(pdir_curr, top10_curr["ACCESSION_NUMBER"].tolist())
    holdings_prev = extract_holdings(pdir_prev, top10_prev["ACCESSION_NUMBER"].tolist())

    print("Building consensus portfolio...")
    consensus = build_consensus_portfolio(holdings_curr, fund_names_curr, top_n=25)

    print("Classifying quarter-over-quarter position changes...")
    changes = classify_position_changes(holdings_prev, holdings_curr, cik_prev, cik_curr, display_names)

    # net buying pressure per stock: how many (fund, position) events were
    # NEW/INCREASED vs CLOSED/DECREASED, aggregated across the whole panel
    changes["direction"] = changes["change"].map({"NEW": 1, "INCREASED": 1, "CLOSED": -1, "DECREASED": -1, "UNCHANGED": 0})
    net_flow = changes.groupby("name").agg(
        net_funds=("direction", "sum"),
        n_events=("direction", "count"),
        cusip=("CUSIP", "first"),
    ).reset_index().sort_values("net_funds", ascending=False)

    result = {
        "top10_funds": top10_curr[["CIK", "FILINGMANAGER_NAME", "TABLEVALUETOTAL", "TABLEENTRYTOTAL"]].to_dict(orient="records"),
        "consensus_portfolio": [
            {"cusip": h.cusip, "name": h.name, "ticker": h.ticker, "n_funds": h.n_funds,
             "total_value": h.total_value, "weight": h.weight, "fund_names": h.fund_names}
            for h in consensus
        ],
        "position_changes_summary": changes["change"].value_counts().to_dict(),
    }
    with open(RESULTS_DIR / "snapshot.json", "w") as f:
        json.dump(result, f, indent=2)

    write_report(top10_curr, consensus, changes, net_flow)
    print(f"Wrote report to {RESULTS_DIR / 'report.md'}")


def write_report(top10, consensus, changes, net_flow) -> None:
    lines = []
    period = top10["report_period"].iloc[0]
    lines.append(f"# Smart-money consensus: top 10 active hedge funds, {period}\n")
    lines.append(
        "Ranked from SEC 13F filings: restricted to a curated list of "
        "well-known ACTIVE, concentrated stock-picking funds (excludes "
        "BlackRock/Vanguard/State Street-style passive giants and "
        "quant/multi-strategy shops with thousands of systematic positions "
        "-- see src/sec_13f_wisdom.py for why), then ranked by their own "
        "reported 13F portfolio value.\n"
    )
    lines.append("## The panel\n")
    lines.append("| Fund | 13F value | Positions |")
    lines.append("|---|---|---|")
    for _, row in top10.iterrows():
        lines.append(f"| {row['FILINGMANAGER_NAME']} | ${row['TABLEVALUETOTAL']/1e9:.1f}B | {row['TABLEENTRYTOTAL']} |")

    lines.append("\n## Consensus portfolio (dollar-value weighted across all 10 funds)\n")
    lines.append("| Ticker | Company | Held by | Weight | Total $ across funds |")
    lines.append("|---|---|---|---|---|")
    for h in consensus:
        lines.append(
            f"| {h.ticker or 'n/a'} | {h.name.title()} | {h.n_funds}/10 | {h.weight*100:.1f}% | ${h.total_value/1e9:.2f}B |"
        )

    lines.append("\n## Biggest quarter-over-quarter net buying (across the panel)\n")
    lines.append("| Company | Net fund flow (buys minus sells) | Events |")
    lines.append("|---|---|---|")
    for _, row in net_flow.head(10).iterrows():
        lines.append(f"| {row['name'].title()} | {row['net_funds']:+d} | {row['n_events']} |")

    lines.append("\n## Biggest quarter-over-quarter net selling (across the panel)\n")
    lines.append("| Company | Net fund flow (buys minus sells) | Events |")
    lines.append("|---|---|---|")
    for _, row in net_flow.tail(10).iloc[::-1].iterrows():
        lines.append(f"| {row['name'].title()} | {row['net_funds']:+d} | {row['n_events']} |")

    counts = {k: int(v) for k, v in changes["change"].value_counts().items()}
    lines.append(f"\n## All position changes: {counts}\n")
    lines.append("## Caveats\n")
    lines.append(
        "- 13F covers long US-listed equity + listed options only -- no "
        "shorts, no international holdings, no cash/bonds/private "
        "investments.\n"
        "- Up to 45-day filing lag -- this is a snapshot of what was true "
        "as of the filing deadline, not real-time.\n"
        "- 'Net buying/selling' counts fund-level position changes >10% "
        "as increased/decreased; it does not weight by dollar size of the "
        "change.\n"
    )
    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
