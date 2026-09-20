"""Combine four structurally independent wisdom-of-crowd signals for the
same stock universe (the smart-money 13F consensus holdings), to test
Surowiecki's actual thesis: does genuine diversity of independent
estimators -- not just more of the same type of voter -- reveal something
none of them show alone?

The four signals, each drawn from a different population with different
incentives and information:
  1. SMART MONEY (professional, capital-weighted, real positions) --
     the 13F consensus weight/conviction already built.
  2. ANALYST CONSENSUS (professional opinion, no capital at risk, sell-side
     incentives) -- implied 12-month return from price targets.
  3. OPTIONS MARKET (real money, but market-makers/hedgers, not directional
     conviction) -- NOT the risk-neutral mean (mechanically anchored to the
     forward price, not a forecast -- see options_implied_distribution.py's
     docstring), but the options market's own probability that the stock
     reaches the analyst target. A cross-check of signal 2 by a different,
     real-money population, not an independent 5th opinion.
  4. RETAIL ATTENTION (mass crowd, no capital or credentials) -- Wikipedia
     pageview activity relative to baseline.

Important limitation, stated up front: signals 3 and 4 can only be
computed as a CURRENT SNAPSHOT. There is no free source of historical
options chains (yfinance only serves the live chain), so unlike the
smart-money and analyst signals -- both separately backtested with years of
history earlier in this project -- this combined view cannot be
backtested. It's a cross-sectional snapshot, and where it becomes genuinely
useful is watched forward from here, which has the advantage of zero
lookahead risk.

Usage: python3 scripts/run_combined_wisdom_signals.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from analyst_price_target_backtest import (  # noqa: E402
    adjust_targets_for_splits,
    consensus_at_date,
    fetch_price_history_cached,
    fetch_splits_cached,
    fetch_upgrades_downgrades_cached,
)
from options_implied_distribution import build_options_implied_distribution  # noqa: E402
from analyst_factor_backtest import revision_momentum_at_date  # noqa: E402

RESULTS_DIR = REPO_ROOT / "results" / "combined_wisdom_signals"


def load_smart_money() -> pd.DataFrame:
    snap = json.loads((REPO_ROOT / "results" / "sec_13f_snapshot" / "snapshot.json").read_text())
    rows = []
    for h in snap["consensus_portfolio"]:
        if not h["ticker"]:
            continue
        rows.append({"ticker": h["ticker"], "name": h["name"], "smart_money_weight": h["weight"], "n_funds": h["n_funds"]})
    df = pd.DataFrame(rows)
    # merge GOOG/GOOGL (two share classes of the same company) into one row under GOOGL
    if "GOOG" in df["ticker"].values and "GOOGL" in df["ticker"].values:
        goog = df[df["ticker"] == "GOOG"].iloc[0]
        idx = df[df["ticker"] == "GOOGL"].index[0]
        df.loc[idx, "smart_money_weight"] += goog["smart_money_weight"]
        df.loc[idx, "n_funds"] = max(df.loc[idx, "n_funds"], goog["n_funds"])
        df = df[df["ticker"] != "GOOG"]
    return df.reset_index(drop=True)


def load_retail_attention() -> dict[str, float]:
    data = json.loads((REPO_ROOT / "results" / "wikipedia_attention_comparison" / "comparison.json").read_text())
    out = {}
    for r in data["smart_money"]:
        if r["ticker"] and r["attention"]:
            out[r["ticker"]] = r["attention"]["attention_ratio"]
    return out


def get_analyst_signal(ticker: str) -> dict | None:
    ud = fetch_upgrades_downgrades_cached(ticker)
    prices = fetch_price_history_cached(ticker)
    if ud is None or prices is None or ud.empty or prices.empty:
        return None
    ud = adjust_targets_for_splits(ud, fetch_splits_cached(ticker))
    now = pd.Timestamp.now(tz="UTC")
    spot = float(prices.iloc[-1])
    cons = consensus_at_date(ud, now, spot_price=spot)
    if cons is None:
        return None
    # Accuracy improvement: results/analyst_factor_backtest already found
    # the static implied return has no real predictive power (IC t-stat
    # 0.58) while revision momentum does (IC t-stat 1.64) -- the combined
    # score below ranks on this, not the static level.
    return {
        "spot": spot, "consensus_target": cons["mean"], "n_firms": cons["n_firms"],
        "dispersion": cons["std"] / cons["mean"] if cons["mean"] else None,
        "implied_return": cons["mean"] / spot - 1,
        "revision_momentum": revision_momentum_at_date(ud, now),
    }


def get_options_signal(ticker: str, analyst_target: float | None) -> dict | None:
    try:
        opt = build_options_implied_distribution(ticker)
    except Exception as e:
        print(f"    options failed for {ticker}: {e}")
        return None
    strikes = np.array(opt.strikes)
    probs = np.array(opt.probabilities)
    cdf = np.cumsum(probs)
    result = {"implied_vol": None, "p_exceed_analyst_target": None}

    log_rets = np.log(strikes / opt.spot)
    mean_log = float(np.sum(probs * log_rets))
    var_log = float(np.sum(probs * (log_rets - mean_log) ** 2))
    result["implied_vol"] = float(np.sqrt(var_log / opt.t_years))

    if analyst_target:
        idx = np.searchsorted(strikes, analyst_target)
        result["p_exceed_analyst_target"] = float(1 - (cdf[idx] if idx < len(cdf) else 1.0))
    return result


def zscore(s: pd.Series) -> pd.Series:
    return (s - s.mean()) / s.std() if s.std() > 0 else s * 0


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    smart_money = load_smart_money()
    retail = load_retail_attention()

    rows = []
    for _, sm in smart_money.iterrows():
        ticker = sm["ticker"]
        print(f"Processing {ticker}...")
        analyst = get_analyst_signal(ticker)
        options = get_options_signal(ticker, analyst["consensus_target"] if analyst else None)
        rows.append({
            "ticker": ticker, "name": sm["name"],
            "smart_money_weight": sm["smart_money_weight"], "n_funds": sm["n_funds"],
            "analyst_implied_return": analyst["implied_return"] if analyst else None,
            "analyst_revision_momentum": analyst["revision_momentum"] if analyst else None,
            "analyst_dispersion": analyst["dispersion"] if analyst else None,
            "analyst_n_firms": analyst["n_firms"] if analyst else None,
            "options_p_exceed_target": options["p_exceed_analyst_target"] if options else None,
            "options_implied_vol": options["implied_vol"] if options else None,
            "retail_attention_ratio": retail.get(ticker),
        })

    df = pd.DataFrame(rows)

    # standardize each signal, then average the ones available per row
    df["z_smart_money"] = zscore(df["smart_money_weight"])
    df["z_analyst"] = zscore(df["analyst_revision_momentum"])  # evidence-backed choice -- see get_analyst_signal
    df["z_options_confirm"] = zscore(df["options_p_exceed_target"])  # high = options market agrees analysts are achievable
    df["z_retail"] = zscore(df["retail_attention_ratio"] - 1.0)

    z_cols = ["z_smart_money", "z_analyst", "z_options_confirm", "z_retail"]
    df["n_signals_available"] = df[z_cols].notna().sum(axis=1)
    df["combined_score"] = df[z_cols].mean(axis=1, skipna=True)

    # agreement: how spread out are the available z-scores for this stock (low = signals agree, high = they conflict)
    df["signal_dispersion"] = df[z_cols].std(axis=1, skipna=True)

    df = df.sort_values("combined_score", ascending=False)
    df.to_json(RESULTS_DIR / "combined_signals.json", orient="records", indent=2)

    write_report(df)
    print(f"\nWrote report to {RESULTS_DIR / 'report.md'}")


def write_report(df: pd.DataFrame) -> None:
    lines = []
    lines.append("# Combining four independent wisdom-of-crowd signals\n")
    lines.append(
        "Smart money (13F), analyst consensus, options-market confirmation, "
        "and retail attention (Wikipedia), for the same 23-stock smart-money "
        "universe. Each is standardized (z-scored across this universe) and "
        "averaged into one combined score. **This is a current snapshot, "
        "not a backtest** -- there is no free source of historical options "
        "chains, so signals 3 and 4 can't be tested against history the way "
        "the smart-money and analyst signals already have been elsewhere in "
        "this project.\n"
    )
    lines.append("## Full comparison, ranked by combined score\n")
    lines.append("| Ticker | Smart money wt | Analyst impl. ret | Options P(exceed target) | Retail attention | Combined score | Signal spread |")
    lines.append("|---|---|---|---|---|---|---|")
    for _, r in df.iterrows():
        sm = f"{r['smart_money_weight']*100:.1f}%"
        an = f"{r['analyst_implied_return']*100:+.1f}%" if pd.notna(r["analyst_implied_return"]) else "n/a"
        op = f"{r['options_p_exceed_target']*100:.0f}%" if pd.notna(r["options_p_exceed_target"]) else "n/a"
        rt = f"{r['retail_attention_ratio']:.2f}x" if pd.notna(r["retail_attention_ratio"]) else "n/a"
        lines.append(f"| {r['ticker']} | {sm} | {an} | {op} | {rt} | {r['combined_score']:+.2f} | {r['signal_dispersion']:.2f} |")

    agree = df[df["n_signals_available"] >= 3].nsmallest(5, "signal_dispersion")
    diverge = df[df["n_signals_available"] >= 3].nlargest(5, "signal_dispersion")

    lines.append("\n## Strongest agreement across signals\n")
    lines.append("| Ticker | Combined score | Signal spread |")
    lines.append("|---|---|---|")
    for _, r in agree.iterrows():
        lines.append(f"| {r['ticker']} | {r['combined_score']:+.2f} | {r['signal_dispersion']:.2f} |")

    lines.append("\n## Strongest disagreement across signals\n")
    lines.append("| Ticker | Combined score | Signal spread |")
    lines.append("|---|---|---|")
    for _, r in diverge.iterrows():
        lines.append(f"| {r['ticker']} | {r['combined_score']:+.2f} | {r['signal_dispersion']:.2f} |")

    lines.append("\n## Caveats\n")
    lines.append(
        "- Not independent in the pure sense: the options signal is defined "
        "relative to the analyst target, so it's a cross-check on the "
        "analyst signal, not a fully independent 5th vote.\n"
        "- Snapshot only -- can't be backtested with free data (no "
        "historical options chains). The honest way to validate this is to "
        "track it forward from today, which has zero lookahead risk.\n"
        "- Small universe (23 stocks, all smart-money holdings) -- this "
        "isn't a market-wide screen, just a lens on stocks ten funds "
        "already like.\n"
    )
    (RESULTS_DIR / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
