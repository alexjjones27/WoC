"""Run the Wall Street analyst price-target consensus backtest and write a report.

Usage: python3 scripts/run_analyst_price_target_backtest.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from analyst_price_target_backtest import (  # noqa: E402
    RESULTS_DIR,
    adjust_targets_for_splits,
    build_backtest,
    consensus_at_date,
    fetch_price_history_cached,
    fetch_splits_cached,
    fetch_upgrades_downgrades_cached,
    summarize,
)
import pandas as pd  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

# Large, liquid, long-history single stocks -- confirmed live to have deep
# upgrades_downgrades coverage. Deliberately spans several sectors/eras so
# the result isn't just "how good analysts are at covering one mega-cap."
SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META",
    "JPM", "JNJ", "XOM", "WMT", "DIS", "NFLX", "INTC", "BA",
]


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching analyst history + prices for {len(SYMBOLS)} symbols...")
    df = build_backtest(SYMBOLS)
    print(f"Built {len(df)} backtest snapshots across {df['symbol'].nunique()} symbols.")

    overall = summarize(df)
    per_symbol = {sym: summarize(g) for sym, g in df.groupby("symbol")}

    df_out = df.copy()
    df_out["as_of"] = df_out["as_of"].astype(str)
    (RESULTS_DIR / "backtest_snapshots.json").write_text(
        json.dumps(df_out.to_dict(orient="records"), indent=2)
    )

    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump({"overall": overall, "per_symbol": per_symbol}, f, indent=2)

    print("Building live consensus snapshot (aggregating current targets into one forecast per stock)...")
    live = build_live_consensus(SYMBOLS)
    with open(RESULTS_DIR / "live_consensus.json", "w") as f:
        json.dump(live, f, indent=2)

    write_report(df, overall, per_symbol, live)
    print(f"Wrote report to {RESULTS_DIR / 'report.md'}")


def build_live_consensus(symbols: list[str]) -> list[dict]:
    """The 'forward test' half of the ask: aggregate each stock's currently
    active analyst price targets into a single consensus 12-month forecast,
    the same reconstruction consensus_at_date() uses in the backtest, just
    run with as_of=now instead of a historical date. Whether this consensus
    turns out to be accurate won't be knowable for ~12 months -- that's the
    forward test itself, not something this script can score today.
    """
    now = pd.Timestamp(datetime.now(timezone.utc))
    out = []
    for sym in symbols:
        ud = fetch_upgrades_downgrades_cached(sym)
        prices = fetch_price_history_cached(sym)
        if ud is None or prices is None or ud.empty or prices.empty:
            continue
        ud = adjust_targets_for_splits(ud, fetch_splits_cached(sym))
        cons = consensus_at_date(ud, now)
        if cons is None:
            continue
        spot = float(prices.iloc[-1])
        out.append({
            "symbol": sym,
            "spot_price": spot,
            "consensus_target_mean": cons["mean"],
            "consensus_target_median": cons["median"],
            "target_dispersion_pct": cons["std"] / cons["mean"] if cons["mean"] else None,
            "n_firms": cons["n_firms"],
            "implied_return_pct": cons["mean"] / spot - 1,
            "as_of_utc": now.isoformat(),
        })
    return out


def write_report(df, overall, per_symbol, live) -> None:
    lines = []
    lines.append("# Analyst price-target consensus backtest\n")
    lines.append(
        "How accurate has Wall Street's aggregate 12-month analyst price-target "
        "consensus actually been, historically, at predicting where a stock's "
        "price would actually be a year later -- and does it beat assuming the "
        "price doesn't move at all?\n"
    )
    lines.append("## Method\n")
    lines.append(
        "- Data: `yfinance`'s `Ticker.upgrades_downgrades` -- free, no API key, "
        "real per-firm price-target history (not just a current snapshot).\n"
        "- For each historical date T (quarterly steps), the consensus is "
        "reconstructed using only data available as of T: each covering firm's "
        "most recently set price target (targets older than 15 months are "
        "dropped as stale/likely-uncovered), averaged across at least 3 firms.\n"
        "- Scored as *implied return* (`target/price_T - 1`) vs. *realized "
        "return* 12 months later, to normalize across stocks and time.\n"
        "- Compared against a naive baseline that assumes 0% return (flat "
        "price).\n"
    )
    lines.append("## Headline result\n")
    lines.append(f"- **{overall['n']} backtest snapshots** across {overall['n_symbols']} stocks "
                  f"(avg {overall['mean_n_firms']:.1f} covering firms per snapshot)\n")
    lines.append(f"- Analyst consensus mean absolute error: **{overall['mad_error']*100:.1f} percentage points** "
                  f"of return (RMSE {overall['rmse']*100:.1f}pp)\n")
    lines.append(f"- Naive \"flat price\" baseline mean absolute error: **{overall['naive_mad_error']*100:.1f} percentage points** "
                  f"(RMSE {overall['naive_rmse']*100:.1f}pp)\n")
    beats_naive = overall['mad_error'] < overall['naive_mad_error']
    lines.append(f"- Consensus {'BEATS' if beats_naive else 'DOES NOT beat'} the naive baseline on mean absolute error.\n")
    lines.append(f"- Directional hit rate (correctly called up vs. down): **{overall['direction_hit_rate']*100:.1f}%** (50% = coin flip)\n")
    lines.append(f"- Mean analyst-implied return: {overall['mean_implied_return']*100:.1f}%  |  "
                  f"Mean realized return: {overall['mean_realized_return']*100:.1f}%\n")

    lines.append("\n## Per-stock breakdown\n")
    lines.append("| Symbol | N | Avg firms | MAE | Naive MAE | Direction hit rate | Mean implied ret | Mean realized ret |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for sym, s in sorted(per_symbol.items()):
        if s["n"] == 0:
            continue
        lines.append(
            f"| {sym} | {s['n']} | {s['mean_n_firms']:.1f} | {s['mad_error']*100:.1f}pp | "
            f"{s['naive_mad_error']*100:.1f}pp | {s['direction_hit_rate']*100:.0f}% | "
            f"{s['mean_implied_return']*100:.1f}% | {s['mean_realized_return']*100:.1f}% |"
        )

    lines.append("\n## Live consensus (today's aggregate 12-month forecast per stock)\n")
    lines.append(
        "This is the 'forward test' half of the ask: today's active analyst "
        "price targets, aggregated into one consensus forecast per stock the "
        "same way the backtest reconstructs history. Nobody can score this "
        "for ~12 months -- that's the nature of a forward test.\n"
    )
    lines.append("| Symbol | Spot | Consensus target (mean) | Consensus target (median) | Dispersion | N firms | Implied return |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in sorted(live, key=lambda r: r["symbol"]):
        disp = f"{row['target_dispersion_pct']*100:.1f}%" if row["target_dispersion_pct"] is not None else "n/a"
        lines.append(
            f"| {row['symbol']} | ${row['spot_price']:.2f} | ${row['consensus_target_mean']:.2f} | "
            f"${row['consensus_target_median']:.2f} | {disp} | {row['n_firms']} | "
            f"{row['implied_return_pct']*100:+.1f}% |"
        )

    lines.append("\n## Caveats\n")
    lines.append(
        "- 12 months is an approximation -- not every firm explicitly states a "
        "12-month horizon, and horizons vary.\n"
        "- `upgrades_downgrades` is Yahoo Finance's scrape, not an official "
        "stable API -- treat it as \"best available free source,\" not "
        "institutional-grade (e.g. Refinitiv/FactSet IBES).\n"
        "- No survivorship-bias correction beyond what yfinance itself returns "
        "-- delisted/failed companies are not represented in this symbol set.\n"
        "- The 15-month staleness cutoff for \"still covering\" is a judgment "
        "call, not a measured fact (yfinance has no explicit coverage-dropped "
        "signal).\n"
    )

    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
