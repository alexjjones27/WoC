"""Expand the four-signal wisdom-of-crowds combination from the 23-stock
smart-money-holdings-only universe to the full S&P 500, then construct an
actual portfolio from the combined score.

Signals (see run_combined_wisdom_signals.py for the full rationale):
  1. SMART MONEY: weight in the N=50 objective 13F panel's current-quarter
     holdings (broader coverage than the curated 10-fund panel -- most S&P
     500 names will show 0, which is itself informative: no institutional
     conviction backing it, not missing data).
  2. ANALYST CONSENSUS: ranked by REVISION MOMENTUM (recent target
     raises/cuts), not the static implied-return level. The S&P 500
     cross-sectional factor backtest (results/analyst_factor_backtest)
     already tested both: static implied return had weak, statistically
     insignificant predictive power (IC t-stat 0.58), while revision
     momentum was the one signal that showed something real (IC t-stat
     1.64, 66% win rate). The static implied return is still shown for
     context but no longer drives the ranking -- using it would ignore
     evidence already sitting in this project's own prior backtest.
  3. OPTIONS MARKET: probability of exceeding the analyst target (a
     cross-check on signal 2 by a different, real-money population, not an
     independent forecast of its own -- expect real gaps here, options
     liquidity fails for a meaningful fraction of even large-cap names).
  4. RETAIL ATTENTION: Wikipedia pageviews vs. 90-day baseline.

Long-running (500+ tickers across three genuinely slow, rate-limited or
liquidity-gated data sources) -- meant for the background, with per-ticker
disk caching on every signal so a crash doesn't lose progress.

Usage: python3 scripts/run_expanded_wisdom_portfolio.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from analyst_factor_backtest import fetch_sp500_universe  # noqa: E402
from wikipedia_attention import get_attention_for_company  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "wisdom-dashboard" / "backend"))
from combined_signals_service import get_analyst_signal, get_options_signal  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results" / "expanded_wisdom_portfolio"
SMART_MONEY_WEIGHTS_PATH = REPO_ROOT / "data" / "raw" / "sec_13f" / "n50_ticker_weights.json"

MIN_SIGNALS_FOR_PORTFOLIO = 2  # require at least this many of the 4 signals to be eligible
PORTFOLIO_SIZE = 25
MAX_PER_SECTOR = 5  # simple diversification cap


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_smart_money_weights() -> dict[str, float]:
    if SMART_MONEY_WEIGHTS_PATH.exists():
        return json.loads(SMART_MONEY_WEIGHTS_PATH.read_text())
    raise FileNotFoundError(
        f"{SMART_MONEY_WEIGHTS_PATH} not found -- build it first from the "
        "N=50 objective panel's current-quarter holdings (resolve CUSIPs to "
        "tickers via sec_13f_wisdom.resolve_cusips_to_tickers)."
    )


def zscore(s: pd.Series) -> pd.Series:
    return (s - s.mean()) / s.std() if s.std() > 0 else s * 0


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    universe = fetch_sp500_universe()
    smart_money_weights = load_smart_money_weights()
    log(f"Universe: {len(universe)} S&P 500 tickers. Smart-money weights available for {len(smart_money_weights)} tickers.")

    rows = []
    for i, u in enumerate(universe):
        ticker = u["symbol"]
        analyst = get_analyst_signal(ticker)
        options = get_options_signal(ticker, analyst["consensus_target"] if analyst else None)
        attn = get_attention_for_company(u.get("security") or ticker)
        rows.append({
            "ticker": ticker, "sector": u.get("sector", ""),
            "smart_money_weight": smart_money_weights.get(ticker, 0.0),
            "analyst_implied_return": analyst["implied_return"] if analyst else None,
            "analyst_revision_momentum": analyst["revision_momentum"] if analyst else None,
            "analyst_n_firms": analyst["n_firms"] if analyst else None,
            "options_p_exceed_target": options["p_exceed_analyst_target"] if options else None,
            "options_implied_vol": options["implied_vol"] if options else None,
            "retail_attention_ratio": attn["attention_ratio"] if attn else None,
        })
        if (i + 1) % 25 == 0:
            log(f"  processed {i + 1}/{len(universe)}")
            # checkpoint progress to disk in case of a crash
            pd.DataFrame(rows).to_json(RESULTS_DIR / "_progress_checkpoint.json", orient="records", indent=2)

    df = pd.DataFrame(rows)
    df.to_json(RESULTS_DIR / "all_signals.json", orient="records", indent=2)
    log(f"Raw signals for {len(df)} tickers written.")

    build_portfolio(df)


def build_portfolio(df: pd.DataFrame) -> None:
    df = df.copy()
    df["z_smart_money"] = zscore(df["smart_money_weight"])
    df["z_analyst"] = zscore(df["analyst_revision_momentum"])  # evidence-backed choice -- see module docstring
    df["z_options_confirm"] = zscore(df["options_p_exceed_target"])
    df["z_retail"] = zscore(df["retail_attention_ratio"] - 1.0)
    z_cols = ["z_smart_money", "z_analyst", "z_options_confirm", "z_retail"]
    df["n_signals_available"] = df[z_cols].notna().sum(axis=1)
    df["combined_score"] = df[z_cols].mean(axis=1, skipna=True)

    eligible = df[df["n_signals_available"] >= MIN_SIGNALS_FOR_PORTFOLIO].sort_values("combined_score", ascending=False)

    portfolio_rows = []
    sector_counts: dict[str, int] = {}
    for _, r in eligible.iterrows():
        if len(portfolio_rows) >= PORTFOLIO_SIZE:
            break
        sector = r["sector"] or "Unknown"
        if sector_counts.get(sector, 0) >= MAX_PER_SECTOR:
            continue
        portfolio_rows.append(r)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    portfolio = pd.DataFrame(portfolio_rows)
    # weight by combined score (shifted positive), normalized -- more conviction gets more weight, capped implicitly by the sector rule already applied
    shifted = portfolio["combined_score"] - portfolio["combined_score"].min() + 0.1
    portfolio["portfolio_weight"] = shifted / shifted.sum()

    portfolio.to_json(RESULTS_DIR / "portfolio.json", orient="records", indent=2)
    with open(RESULTS_DIR / "sector_counts.json", "w") as f:
        json.dump(sector_counts, f, indent=2)

    write_report(df, portfolio, sector_counts)
    log(f"Wrote report to {RESULTS_DIR / 'report.md'}")


def write_report(df: pd.DataFrame, portfolio: pd.DataFrame, sector_counts: dict) -> None:
    lines = []
    lines.append("# Expanded wisdom-of-crowds portfolio (S&P 500)\n")
    lines.append(
        f"Four signals -- smart money (13F, N=50 panel), analyst consensus, "
        f"options-market confirmation, retail attention -- computed across "
        f"all {len(df)} S&P 500 constituents, standardized, and averaged. "
        f"Top {len(portfolio)} by combined score, capped at {MAX_PER_SECTOR} "
        f"per GICS sector, requiring at least {MIN_SIGNALS_FOR_PORTFOLIO} of "
        f"4 signals available. **This is a current snapshot, not a "
        f"backtest** -- no free source of historical options chains means "
        f"this can't be tested against history the way the smart-money and "
        f"analyst signals alone have been elsewhere in this project.\n"
    )
    lines.append("## Data coverage\n")
    lines.append(f"- Smart money (>0 weight): {(df['smart_money_weight'] > 0).sum()}/{len(df)}\n")
    lines.append(f"- Analyst consensus: {df['analyst_implied_return'].notna().sum()}/{len(df)}\n")
    lines.append(f"- Analyst revision momentum (drives ranking): {df['analyst_revision_momentum'].notna().sum()}/{len(df)}\n")
    lines.append(f"- Options confirmation: {df['options_p_exceed_target'].notna().sum()}/{len(df)}\n")
    lines.append(f"- Retail attention: {df['retail_attention_ratio'].notna().sum()}/{len(df)}\n")

    lines.append("\n## The portfolio\n")
    lines.append("| Ticker | Sector | Weight | Smart money | Analyst impl. ret | Revision momentum | Options P(hit) | Retail attn | Combined score |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in portfolio.sort_values("portfolio_weight", ascending=False).iterrows():
        sm = f"{r['smart_money_weight']*100:.1f}%" if r['smart_money_weight'] else "0%"
        an = f"{r['analyst_implied_return']*100:+.1f}%" if pd.notna(r['analyst_implied_return']) else "n/a"
        rv = f"{r['analyst_revision_momentum']*100:+.1f}%" if pd.notna(r['analyst_revision_momentum']) else "n/a"
        op = f"{r['options_p_exceed_target']*100:.0f}%" if pd.notna(r['options_p_exceed_target']) else "n/a"
        rt = f"{r['retail_attention_ratio']:.2f}x" if pd.notna(r['retail_attention_ratio']) else "n/a"
        lines.append(f"| {r['ticker']} | {r['sector']} | {r['portfolio_weight']*100:.1f}% | {sm} | {an} | {rv} | {op} | {rt} | {r['combined_score']:+.2f} |")

    lines.append("\n## Sector breakdown\n")
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {sector}: {count}\n")

    lines.append("\n## Caveats\n")
    lines.append(
        "- Snapshot only, not backtested -- validate by watching forward, "
        "not by trusting the combined score as a proven predictor.\n"
        "- Zero smart-money weight for most of the universe is expected, "
        "not a gap -- most S&P 500 stocks aren't held by any of the 50 "
        "funds in the panel.\n"
        "- Options and analyst signals are not fully independent (options "
        "confirmation is defined relative to the analyst target).\n"
        "- The analyst signal ranks by revision momentum, not the static "
        "implied-return level -- backed by results/analyst_factor_backtest, "
        "which found the static level had no real predictive power (IC "
        "t-stat 0.58) while revision momentum did (IC t-stat 1.64). Smart "
        "money, options, and retail attention haven't had the same "
        "standalone predictive-power test run on them -- they're included "
        "on the strength of the argument for genuinely independent crowds, "
        "not (yet) their own proven track record.\n"
        "- Sector cap is a simple diversification heuristic, not a real "
        "risk model -- no factor/beta neutrality, no correlation-aware "
        "position sizing.\n"
        "- Gross, hypothetical construction -- no transaction costs, no "
        "consideration of trade size vs. liquidity.\n"
    )
    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
