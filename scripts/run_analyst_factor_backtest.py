"""Run the cross-sectional analyst-signal factor backtest over the S&P 500
and write a report. Long-running (pulls per-firm history + full price
history for ~500 tickers via yfinance) -- meant to be run in the background.

Usage: python3 scripts/run_analyst_factor_backtest.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from analyst_factor_backtest import (  # noqa: E402
    RESULTS_DIR,
    add_combined_signal,
    build_panel,
    fetch_sp500_universe,
    score_signal,
)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    universe = fetch_sp500_universe()
    symbols = [u["symbol"] for u in universe]
    log(f"S&P 500 universe: {len(symbols)} symbols")

    t0 = time.time()
    panel = build_panel(symbols, log_every=25, log_fn=log)
    log(f"Panel built: {len(panel)} (date, symbol) rows in {time.time() - t0:.0f}s")

    if panel.empty:
        log("Panel is empty -- aborting.")
        return

    panel = add_combined_signal(panel)

    panel_out = panel.copy()
    panel_out["date"] = panel_out["date"].astype(str)
    (RESULTS_DIR / "panel.json").write_text(json.dumps(panel_out.to_dict(orient="records")))
    log(f"Wrote panel.json ({(RESULTS_DIR / 'panel.json').stat().st_size / 1e6:.1f} MB)")

    scores = {sig: score_signal(panel, sig) for sig in ["implied_return", "revision", "combined", "upside_lowdisp"]}
    with open(RESULTS_DIR / "scores.json", "w") as f:
        json.dump(scores, f, indent=2)
    log("Signal scores:")
    for sig, s in scores.items():
        log(f"  {sig}: {s}")

    write_report(panel, scores, len(symbols))
    log(f"Wrote report to {RESULTS_DIR / 'report.md'}")


def write_report(panel, scores, universe_size) -> None:
    lines = []
    lines.append("# Analyst wisdom-of-the-crowd: cross-sectional factor backtest\n")
    lines.append(
        "Does ranking S&P 500 stocks by analyst wisdom-of-the-crowd signals "
        "actually help pick stocks that outperform -- not just \"is the "
        "consensus price target accurate,\" but \"if I'd gone long the "
        "stocks the crowd was most bullish on, would I have beaten the "
        "stocks it was least bullish on?\"\n"
    )
    lines.append("## Method\n")
    lines.append(
        f"- Universe: current S&P 500 constituents ({universe_size} tickers, "
        "scraped from Wikipedia). **Not point-in-time correct** -- this is "
        "today's membership, so companies removed from the index since the "
        "backtest's start (bankruptcy, steep decline, acquisition) are "
        "invisible here. Real, unresolved survivorship bias; flagged, not "
        "fixed.\n"
        "- Quarterly rebalance, 3-month forward return (a shorter, more "
        "tradeable horizon than the 12-month single-stock backtest).\n"
        "- Three signals, all computed with no lookahead:\n"
        "  - **upside**: consensus 12-month target vs. current price\n"
        "  - **revision**: mean %-change of price targets revised in the "
        "trailing 3 months (recent re-rating direction/magnitude)\n"
        "  - **combined**: average of each period's cross-sectional "
        "percentile rank on upside and revision\n"
        "  - **upside_lowdisp**: average rank of upside and (inverted) "
        "analyst dispersion -- prefers high upside where analysts agree\n"
        "- Scored two ways: Information Coefficient (Spearman rank "
        "correlation between signal and the forward return that followed "
        "it, per period, then averaged) and quintile spread (equal-weight "
        "top 20% by signal minus bottom 20%, per period, then averaged).\n"
    )
    lines.append("## Results\n")
    lines.append("| Signal | Periods | Avg universe size | Mean IC | IC t-stat | % periods IC>0 | Quintile spread (per 3mo) | Spread win rate |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for sig, s in scores.items():
        if s.get("n_periods", 0) == 0:
            lines.append(f"| {sig} | 0 | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| {sig} | {s['n_periods']} | {s['mean_n_per_period']:.0f} | "
            f"{s['mean_ic']:.4f} | {s['ic_tstat']:.2f} | {s['pct_periods_ic_positive']*100:.0f}% | "
            f"{s['mean_quintile_spread']*100:+.2f}% | {s['spread_win_rate']*100:.0f}% |"
        )
    lines.append(
        "\nFor reference, an IC around 0.02-0.05 with a t-stat above ~2 is "
        "considered a real, usable factor in equity quant research -- most "
        "single factors are weak in isolation. A t-stat below ~2 means the "
        "average edge isn't reliably distinguishable from noise at this "
        "sample size.\n"
    )
    lines.append("## Caveats\n")
    lines.append(
        "- Survivorship bias from using today's S&P 500 membership (see "
        "above) -- the single biggest unresolved issue.\n"
        "- No transaction costs, slippage, or position limits modeled -- "
        "these are gross, not net, returns.\n"
        "- Quarterly non-overlapping windows keep the period-to-period IC "
        "values closer to independent, but ~50 periods is still a modest "
        "sample for a t-stat.\n"
        "- `upgrades_downgrades` occasionally contains corrupt rows (a "
        "confirmed ACN row claimed a prior target of $4 against an ~$80 "
        "stock) -- filtered out at the row level where detectable, but "
        "there is no guarantee every bad row was caught.\n"
    )
    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
