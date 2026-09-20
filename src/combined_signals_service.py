"""The single reusable module behind the dashboard's new pages -- pulled
out of scripts/run_combined_wisdom_signals.py and
scripts/run_expanded_wisdom_portfolio.py (which had near-duplicate
get_analyst_signal/get_options_signal functions) so the live API endpoints
and the offline batch scripts share one implementation instead of two
copies drifting apart.

Two different data-freshness regimes, by design:
  - load_smart_money_portfolio / load_smart_money_backtest read PRE-COMPUTED
    results off disk. The S&P 500 sweep takes ~50 minutes (options liquidity
    checks and Wikipedia rate limits are the bottleneck) and the fixed-panel
    backtest takes hours -- neither is recomputed per request.
  - lookup_ticker / three_crowds compute LIVE, per request. A single
    ticker's four signals take a few seconds; BTC/oil's prediction-market
    aggregation already has its own ~45s cache in the orchestrator.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "wisdom-dashboard" / "backend"))

from analyst_price_target_backtest import (  # noqa: E402
    adjust_targets_for_splits,
    consensus_at_date,
    fetch_price_history_cached,
    fetch_splits_cached,
    fetch_upgrades_downgrades_cached,
)
from options_implied_distribution import get_options_distribution_cached  # noqa: E402
from wikipedia_attention import get_attention_for_company  # noqa: E402
from analyst_factor_backtest import revision_momentum_at_date  # noqa: E402
from btc_oil_wisdom_combination import (  # noqa: E402
    ASSET_CONFIG as BTC_OIL_ASSET_CONFIG,
    cross_check_pm_vs_options,
    fetch_real_spot,
    get_options_return_distribution,
    get_prediction_market_signal,
    get_retail_attention as get_btc_oil_retail_attention,
)

PORTFOLIO_RESULTS = REPO_ROOT / "results" / "expanded_wisdom_portfolio"
FIXED_BACKTEST_RESULTS = REPO_ROOT / "results" / "sec_13f_backtest"
SCALING_BACKTEST_RESULTS = REPO_ROOT / "results" / "sec_13f_scaling_backtest"
SIGNIFICANCE_RESULTS = REPO_ROOT / "results" / "sec_13f_significance"
SMART_MONEY_WEIGHTS_PATH = REPO_ROOT / "data" / "raw" / "sec_13f" / "n50_ticker_weights.json"


# ---------------------------------------------------------------- static ---

def load_smart_money_portfolio() -> dict:
    portfolio_path = PORTFOLIO_RESULTS / "portfolio.json"
    signals_path = PORTFOLIO_RESULTS / "all_signals.json"
    if not portfolio_path.exists():
        raise FileNotFoundError("Smart-money portfolio not yet built -- run scripts/run_expanded_wisdom_portfolio.py")
    portfolio = json.loads(portfolio_path.read_text())
    coverage = {}
    if signals_path.exists():
        all_signals = json.loads(signals_path.read_text())
        n = len(all_signals)
        coverage = {
            "universe_size": n,
            "smart_money": sum(1 for r in all_signals if (r.get("smart_money_weight") or 0) > 0),
            "analyst": sum(1 for r in all_signals if r.get("analyst_implied_return") is not None),
            "options": sum(1 for r in all_signals if r.get("options_p_exceed_target") is not None),
            "retail": sum(1 for r in all_signals if r.get("retail_attention_ratio") is not None),
        }
    return {"portfolio": portfolio, "coverage": coverage}


def load_smart_money_backtest() -> dict:
    out = {}
    fixed_stats = FIXED_BACKTEST_RESULTS / "stats.json"
    fixed_nav = FIXED_BACKTEST_RESULTS / "nav_series.json"
    if fixed_stats.exists() and fixed_nav.exists():
        out["fixed_panel"] = {"stats": json.loads(fixed_stats.read_text()), "nav": json.loads(fixed_nav.read_text())}

    scaling_stats = SCALING_BACKTEST_RESULTS / "stats.json"
    scaling_nav = SCALING_BACKTEST_RESULTS / "nav_series.json"
    if scaling_stats.exists() and scaling_nav.exists():
        out["scaling_panels"] = {"stats": json.loads(scaling_stats.read_text()), "nav": json.loads(scaling_nav.read_text())}

    sig_path = SIGNIFICANCE_RESULTS / "scaling_panels.json"
    if sig_path.exists():
        out["significance"] = json.loads(sig_path.read_text())

    if not out:
        raise FileNotFoundError("Smart-money backtest results not yet built")
    return out


# ------------------------------------------------------------------ live ---

def get_smart_money_weights() -> dict[str, float]:
    if not SMART_MONEY_WEIGHTS_PATH.exists():
        return {}
    return json.loads(SMART_MONEY_WEIGHTS_PATH.read_text())


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
    # Accuracy improvement: the S&P 500 cross-sectional factor backtest
    # (results/analyst_factor_backtest) already showed the STATIC implied
    # return alone has weak, statistically insignificant predictive power
    # (IC t-stat 0.58), while REVISION momentum -- recent target
    # raises/cuts -- was the one signal that actually showed something real
    # (IC t-stat 1.64, 66% win rate). The portfolio-construction combined
    # score should weight revision momentum, not the static level, since
    # that's the one with actual backtested evidence behind it.
    revision = revision_momentum_at_date(ud, now)
    return {"spot": spot, "consensus_target": cons["mean"], "n_firms": cons["n_firms"],
            "implied_return": cons["mean"] / spot - 1, "revision_momentum": revision}


def get_options_signal(ticker: str, analyst_target: float | None) -> dict | None:
    dist = get_options_distribution_cached(ticker)
    if dist is None:
        return None
    strikes = np.array(dist.strikes)
    probs = np.array(dist.probabilities)
    cdf = np.cumsum(probs)
    log_rets = np.log(strikes / dist.spot)
    mean_log = float(np.sum(probs * log_rets))
    var_log = float(np.sum(probs * (log_rets - mean_log) ** 2))
    result = {"implied_vol": float(np.sqrt(var_log / dist.t_years)) if dist.t_years > 0 else None}
    if analyst_target:
        idx = np.searchsorted(strikes, analyst_target)
        result["p_exceed_analyst_target"] = float(1 - (cdf[idx] if idx < len(cdf) else 1.0))
    else:
        result["p_exceed_analyst_target"] = None
    return result


def _company_name_for_wikipedia(ticker: str) -> str:
    """A bare ticker resolves poorly on Wikipedia's opensearch (confirmed
    live: querying "AAPL" directly returned no usable match, while "Apple
    Inc." resolves cleanly) -- yfinance's longName is a fast, free way to
    get a real company name to search with instead."""
    import yfinance as yf
    try:
        name = yf.Ticker(ticker).info.get("longName")
        return name or ticker
    except Exception:
        return ticker


def lookup_ticker(ticker: str) -> dict:
    weights = get_smart_money_weights()
    analyst = get_analyst_signal(ticker)
    options = get_options_signal(ticker, analyst["consensus_target"] if analyst else None)
    attn = get_attention_for_company(_company_name_for_wikipedia(ticker))
    return {
        "ticker": ticker,
        "smart_money_weight": weights.get(ticker, 0.0),
        "analyst": analyst,
        "options": options,
        "retail_attention": attn,
    }


def three_crowds(asset: str) -> dict:
    options = get_options_return_distribution(asset)
    if "error" in options:
        return {"asset": asset, "error": options["error"]}
    target_hours = options["t_years"] * 365.25 * 24
    pm = get_prediction_market_signal(asset, target_lead_hours=target_hours)
    attn = get_btc_oil_retail_attention(asset)
    real_spot = fetch_real_spot(BTC_OIL_ASSET_CONFIG[asset]["real_spot_ticker"])
    xcheck = cross_check_pm_vs_options(pm, options, real_spot) if "error" not in pm else None
    pm_implied_return = (pm["median"] / real_spot - 1) if "error" not in pm and real_spot else None
    return {
        "asset": asset,
        "real_spot": real_spot,
        "prediction_market": {k: v for k, v in pm.items() if k not in ("returns", "probs")} if "error" not in pm else pm,
        "pm_implied_return": pm_implied_return,
        "options": {k: v for k, v in options.items() if k not in ("returns", "probs")},
        "retail_attention": attn,
        "cross_check_p_options_exceed_pm_median": xcheck,
    }
