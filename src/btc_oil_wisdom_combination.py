"""The capstone unification: everything built for individual stocks this
session (options-implied distributions, Wikipedia retail attention) has an
analog for BTC and oil -- and BTC/oil already have something stocks don't:
a real prediction-market consensus (the original wisdom-dashboard, built
earlier this session, aggregating Polymarket/Kalshi/Manifold). This module
brings all three together for BTC and oil specifically.

Two of the three signals give a FULL PROBABILITY DISTRIBUTION for a future
date, not just a point estimate -- the prediction-market aggregate (a
piecewise PDF over price buckets, volume-weighted across platforms) and the
options-implied distribution (Breeden-Litzenberger from a real options
chain). That makes a genuinely rigorous comparison possible: not just "do
these two crowds roughly agree," but "given the options market's own
distribution, how likely is the specific price the prediction market is
calling for" -- the same kind of cross-check already built for analyst
targets vs. options data on the stock side.

Real proxy-instrument caveats, stated up front:
  - BTC's options signal comes from IBIT (BlackRock's spot Bitcoin ETF).
    IBIT tracks BTC's price closely (small expense-ratio drag, negligible
    over a ~1-year horizon) -- a good proxy.
  - Oil's options signal comes from USO, which holds WTI FUTURES and rolls
    them monthly. Futures-based commodity ETFs are well documented to
    diverge from spot over multi-month horizons due to contango/backward-
    ation roll costs -- USO is a meaningfully worse proxy for "real" WTI
    spot returns than IBIT is for BTC. Treated as directional, not precise.
  - Both signals are converted to IMPLIED RETURN (% change from today),
    not price levels -- this sidesteps the proxy instruments' totally
    different share-price bases (IBIT trades near $46, actual BTC trades
    near $110k+; USO trades near real WTI's price by construction but with
    roll-cost drift) and is what actually makes the two-distribution
    comparison valid.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "wisdom-dashboard" / "backend"))

from options_implied_distribution import build_options_implied_distribution  # noqa: E402
from wikipedia_attention import get_attention_for_company  # noqa: E402

ASSET_CONFIG = {
    "BTC": {"proxy_ticker": "IBIT", "real_spot_ticker": "BTC-USD", "wiki_query": "Bitcoin", "proxy_note": "IBIT (spot Bitcoin ETF) -- tight tracker"},
    "OIL": {"proxy_ticker": "USO", "real_spot_ticker": "CL=F", "wiki_query": "Price of oil", "proxy_note": "USO (WTI futures ETF) -- meaningfully looser tracker due to roll/contango"},
    "ETH": {"proxy_ticker": "ETHA", "real_spot_ticker": "ETH-USD", "wiki_query": "Ethereum", "proxy_note": "ETHA (spot Ethereum ETF) -- tight tracker, same structure as IBIT"},
    "GOLD": {"proxy_ticker": "GLD", "real_spot_ticker": "GC=F", "wiki_query": "Gold as an investment", "proxy_note": "GLD (physically-backed gold ETF) -- tight tracker, unlike USO no futures-roll drag"},
}


def fetch_real_spot(ticker: str) -> float | None:
    import yfinance as yf
    hist = yf.Ticker(ticker).history(period="5d")
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def _confidence_bands_to_ci(bands: list[dict] | None, level: float) -> tuple[float, float] | None:
    """Gap-fill (interpolated) forecasts carry confidence_bands (a list of
    {level, low, high}) instead of the real forecasts' direct ci_68/ci_95
    fields -- this looks up the matching band."""
    if not bands:
        return None
    for b in bands:
        if abs(b["level"] - level) < 0.01:
            return (b["low"], b["high"])
    return None


def get_prediction_market_signal(asset: str, target_lead_hours: float | None = None) -> dict | None:
    """Reuses the existing wisdom-dashboard orchestrator (Polymarket/Kalshi/
    Manifold aggregation, already built and backtested earlier this
    session) for a fresh current snapshot. Without a target horizon, picks
    the nearest forecast (a short-term view -- confirmed live BTC's
    nearest is ~3 hours out, useless next to a ~1-year options horizon).
    With target_lead_hours, picks whichever of the real forecasts OR the
    gap-fill (GBM term-structure interpolated) forecasts lands closest to
    it -- BTC's real markets reach out to ~2.3 years, comfortably
    bracketing a 1-year options horizon for genuine interpolation; oil's
    real markets only reach ~44 days, nowhere near a ~1-year horizon, so
    for oil this necessarily returns the longest available forecast with
    an honest, reported mismatch rather than an extrapolation dressed up
    as a match."""
    from orchestrator import get_dashboard_payload
    payload = get_dashboard_payload(asset)
    forecasts = list(payload.get("forecasts", []))
    gap_fill = list(payload.get("gap_fill_forecasts", []))
    all_candidates = [(f, False) for f in forecasts] + [(f, True) for f in gap_fill]
    if not all_candidates:
        return {"error": payload.get("source_errors") or "no forecasts returned"}

    if target_lead_hours is None:
        nearest, is_interp = min(all_candidates, key=lambda t: t[0].get("lead_hours", 1e9))
    else:
        nearest, is_interp = min(all_candidates, key=lambda t: abs(t[0].get("lead_hours", 1e9) - target_lead_hours))

    return {
        "target_date": nearest["target_date"], "period_label": nearest["period_label"],
        "lead_hours": nearest["lead_hours"],
        "horizon_mismatch_hours": (nearest["lead_hours"] - target_lead_hours) if target_lead_hours else 0.0,
        "is_interpolated": is_interp,
        "mean": nearest["mean"], "median": nearest["median"],
        "ci_68": nearest.get("ci_68") or _confidence_bands_to_ci(nearest.get("confidence_bands"), 0.68),
        "ci_95": nearest.get("ci_95") or _confidence_bands_to_ci(nearest.get("confidence_bands"), 0.95),
        "grid_edges": nearest.get("grid_edges"), "pdf": nearest.get("pdf"),  # None for interpolated forecasts -- no full PDF, summary stats only
        "confidence_tier": nearest.get("confidence_tier"), "total_volume": nearest.get("total_volume"),
    }


def get_options_return_distribution(asset: str) -> dict | None:
    cfg = ASSET_CONFIG[asset]
    try:
        dist = build_options_implied_distribution(cfg["proxy_ticker"])
    except Exception as e:
        return {"error": str(e)}
    real_spot = fetch_real_spot(cfg["real_spot_ticker"])
    strikes = np.array(dist.strikes)
    probs = np.array(dist.probabilities)
    returns = strikes / dist.spot - 1  # return relative to the PROXY's own spot -- basis-independent
    cdf = np.cumsum(probs)
    mean_return = float(np.sum(probs * returns))
    median_idx = int(np.searchsorted(cdf, 0.5))
    return {
        "proxy_ticker": cfg["proxy_ticker"], "proxy_note": cfg["proxy_note"],
        "real_spot": real_spot, "expiration": dist.expiration, "t_years": dist.t_years,
        "mean_return": mean_return, "median_return": float(returns[min(median_idx, len(returns) - 1)]),
        "p10_return": float(returns[min(int(np.searchsorted(cdf, 0.1)), len(returns) - 1)]),
        "p90_return": float(returns[min(int(np.searchsorted(cdf, 0.9)), len(returns) - 1)]),
        "returns": returns, "probs": probs,  # kept for the cross-check function
    }


def get_retail_attention(asset: str) -> dict | None:
    return get_attention_for_company(ASSET_CONFIG[asset]["wiki_query"])


def cross_check_pm_vs_options(pm_signal: dict, options_signal: dict, real_spot: float) -> float | None:
    """Given the options market's own return distribution, what probability
    does it assign to actually reaching the prediction market's median
    forecast? Both sides are converted to a common return basis (%
    change from the same real spot) before comparing, since the options
    side's own distribution is centered on the PROXY instrument's return,
    which is assumed to move together with the real asset."""
    if not real_spot or "error" in options_signal:
        return None
    pm_implied_return = pm_signal["median"] / real_spot - 1
    returns = options_signal["returns"]
    probs = options_signal["probs"]
    cdf = np.cumsum(probs)
    idx = int(np.searchsorted(returns, pm_implied_return))
    return float(1 - (cdf[idx] if idx < len(cdf) else 1.0))
