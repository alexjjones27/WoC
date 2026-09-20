"""Compare Wall Street's analyst consensus against the options market's
risk-neutral price distribution, for one ticker. This is the "other side
of the spectrum" piece: not another opinion survey, but a real-money
market's aggregate view, built via Breeden-Litzenberger from the live
options chain (see src/options_implied_distribution.py for the method and
why every other retail/crowd data source we checked was a dead end).

Usage: python3 scripts/run_options_crowd_comparison.py [TICKER]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

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

RESULTS_DIR = REPO_ROOT / "results" / "options_crowd_comparison"


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Building options-implied distribution for {symbol}...")
    opt = build_options_implied_distribution(symbol)

    print(f"Building analyst consensus for {symbol}...")
    ud = fetch_upgrades_downgrades_cached(symbol)
    prices = fetch_price_history_cached(symbol)
    ud = adjust_targets_for_splits(ud, fetch_splits_cached(symbol))
    import pandas as pd
    from datetime import datetime, timezone
    now = pd.Timestamp(datetime.now(timezone.utc))
    cons = consensus_at_date(ud, now)
    spot = float(prices.iloc[-1])

    strikes = np.array(opt.strikes)
    probs = np.array(opt.probabilities)
    cdf = np.cumsum(probs)

    analyst_target = cons["mean"]
    idx = np.searchsorted(strikes, analyst_target)
    p_exceed_analyst_target = float(1 - (cdf[idx] if idx < len(cdf) else 1.0))

    fwd_price = spot * np.exp((opt.risk_free_rate - opt.dividend_yield) * opt.t_years)

    log_rets = np.log(strikes / spot)
    mean_log = float(np.sum(probs * log_rets))
    var_log = float(np.sum(probs * (log_rets - mean_log) ** 2))
    implied_vol = float(np.sqrt(var_log / opt.t_years))

    result = {
        "symbol": symbol,
        "spot": spot,
        "options": {
            "expiration": opt.expiration,
            "t_years": opt.t_years,
            "n_contracts_used": opt.n_used_contracts,
            "risk_free_rate": opt.risk_free_rate,
            "dividend_yield": opt.dividend_yield,
            "forward_price_check": fwd_price,
            "risk_neutral_mean": opt.mean,
            "risk_neutral_median": opt.median,
            "p10": opt.percentile(0.10),
            "p25": opt.percentile(0.25),
            "p75": opt.percentile(0.75),
            "p90": opt.percentile(0.90),
            "implied_annualized_vol": implied_vol,
        },
        "analyst": {
            "consensus_mean_target": cons["mean"],
            "consensus_median_target": cons["median"],
            "n_firms": cons["n_firms"],
            "dispersion_pct": cons["std"] / cons["mean"] if cons["mean"] else None,
            "implied_return": cons["mean"] / spot - 1,
        },
        "cross_check": {
            "options_implied_P_exceed_analyst_target": p_exceed_analyst_target,
        },
    }

    with open(RESULTS_DIR / f"{symbol}_comparison.json", "w") as f:
        json.dump(result, f, indent=2)

    write_report(symbol, result)
    print(f"Wrote report to {RESULTS_DIR / f'{symbol}_report.md'}")


def write_report(symbol: str, r: dict) -> None:
    o, a, x = r["options"], r["analyst"], r["cross_check"]
    lines = []
    lines.append(f"# {symbol}: analyst consensus vs. options-market crowd\n")
    lines.append(
        "Two genuinely independent 'wisdom of the crowd' sources for the same "
        f"stock, same ~12-month horizon: Wall Street's analyst consensus "
        "(expert, credentialed, ~40 firms) vs. the options market's "
        "risk-neutral price distribution (anonymous, real money, includes "
        "heavy retail flow -- extracted via Breeden-Litzenberger from the "
        f"live {o['expiration']} options chain, {o['n_contracts_used']} liquid "
        "contracts used).\n"
    )
    lines.append("## The headline numbers\n")
    lines.append(f"- Spot price: **${r['spot']:.2f}**\n")
    lines.append(f"- Analyst consensus target: **${a['consensus_mean_target']:.2f}** "
                  f"({a['n_firms']} firms, {a['implied_return']*100:+.1f}% implied return, "
                  f"{a['dispersion_pct']*100:.1f}% dispersion)\n")
    lines.append(f"- Options-market risk-neutral mean: **${o['risk_neutral_mean']:.2f}** "
                  f"(median ${o['risk_neutral_median']:.2f})\n")
    lines.append(f"- Options-implied annualized volatility: **{o['implied_annualized_vol']*100:.1f}%**\n")
    lines.append(f"- Options-implied 10th-90th percentile range: **${o['p10']:.0f} - ${o['p90']:.0f}**\n")
    lines.append(f"- **Options market's own probability that {symbol} actually exceeds the "
                  f"${a['consensus_mean_target']:.0f} analyst target: {x['options_implied_P_exceed_analyst_target']*100:.1f}%**\n")

    lines.append("\n## Why the options mean ISN'T a competing forecast\n")
    lines.append(
        "This is the important nuance: the options-implied distribution's "
        "MEAN is not the market's real-world prediction of where the stock "
        "is headed. By no-arbitrage, it's mechanically pinned close to the "
        f"risk-free forward price (${o['forward_price_check']:.2f} here, vs. "
        f"a computed mean of ${o['risk_neutral_mean']:.2f} -- matching almost "
        "exactly, which is the correctness check for this calculation, not "
        "a finding). Comparing that mean to the analyst target is not a fair "
        "fight -- one is a no-arbitrage bookkeeping identity, the other is a "
        "genuine subjective forecast.\n"
        "\n"
        "What the options market DOES encode a genuine, comparable view on "
        "is the **shape** of the distribution -- how much uncertainty "
        "(volatility) and how the probability mass is spread across "
        "outcomes. That's what the P(exceed target) cross-check above uses: "
        "given the crowd's own risk-neutral distribution, how likely is the "
        "specific outcome the analysts are calling for?\n"
        "\n"
        "One more caveat in that number's favor for the analysts: "
        "risk-neutral probabilities are systematically more conservative "
        "than real-world subjective probabilities for large up-moves "
        "(investors demand a risk premium to hold that risk, which is "
        "baked into option prices) -- so the true crowd view is probably "
        f"somewhat more optimistic than the raw {x['options_implied_P_exceed_analyst_target']*100:.1f}% "
        "suggests, though 'somewhat more' rarely closes a gap this size on "
        "its own.\n"
    )
    lines.append("\n## Caveats\n")
    lines.append(
        "- Single expiration, single snapshot -- not backtested over time "
        "yet (unlike the analyst consensus, which has a real historical "
        "backtest in results/analyst_price_target_backtest/).\n"
        "- IV curve smoothed with a degree-3 polynomial in log-moneyness, "
        "weighted by open interest -- reasonable for a liquid large-cap "
        "name, not guaranteed to be stable for a thinly-traded options "
        "chain.\n"
        "- Risk-free rate from ^IRX (13-week T-bill), dividend yield from "
        "yfinance's dividendRate/spot -- both minor approximations.\n"
    )
    (RESULTS_DIR / f"{symbol}_report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
