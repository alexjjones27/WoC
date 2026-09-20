"""The 'other side of the spectrum' from Wall Street analyst opinion: what
does the OPTIONS MARKET think the price distribution looks like?

Every other retail/crowd source we checked live for this (StockTwits,
Reddit, Estimize, TradingView ideas) is now behind an auth wall, a
Cloudflare block, or isn't ToS-safe to scrape. Prediction markets (Kalshi,
Polymarket) don't offer individual stock price contracts at all -- likely a
regulatory choice (single-stock price contracts run into securities law in
a way BTC/oil/index contracts don't). Options markets are the one source
that is: free (no API key), fully public, structurally the polar opposite
of analyst opinion (continuous, anonymous, real money at risk, includes
massive retail options flow for exactly the kind of stock a retail crowd
would speculate on), and directly convertible into a real probability
distribution -- not sentiment, an actual market-clearing price for every
outcome.

Method: Breeden-Litzenberger (1978). Under the risk-neutral measure, the
price of a call option is C(K) = e^{-rT} * E[max(S_T - K, 0)]. Differentiate
twice with respect to strike K and you get the risk-neutral PROBABILITY
DENSITY of S_T directly: f(K) = e^{rT} * d^2 C / dK^2. This is standard,
textbook derivatives theory, not a heuristic -- it's exactly how the
options market's aggregate view of the future price distribution is
recovered in practice (index providers like CBOE's SKEW index use the same
idea).

Because raw market option prices are noisy (wide bid-ask spreads, sparse
strikes, illiquid tails), we don't differentiate the raw price curve
directly -- we smooth the IMPLIED VOLATILITY curve first (a low-order
polynomial in log-moneyness, weighted by open interest so illiquid strikes
don't dominate), reconstruct clean Black-Scholes call prices from that
smoothed curve on a fine strike grid, and differentiate THAT. This is the
standard practitioner approach (smooth in vol space, not price space)
because implied vol varies much more smoothly across strikes than price
does.
"""
from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "raw" / "options_implied"


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def bs_call_price(S: float, K: np.ndarray, T: float, r: float, q: float, sigma: np.ndarray) -> np.ndarray:
    sigma = np.clip(sigma, 1e-4, None)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


@dataclass
class OptionsImpliedDistribution:
    symbol: str
    spot: float
    expiration: str
    t_years: float
    risk_free_rate: float
    dividend_yield: float
    strikes: list[float]  # grid points (bin centers); probabilities[i] is the mass in a grid_step-wide bin around strikes[i]
    probabilities: list[float]
    n_raw_contracts: int
    n_used_contracts: int

    @property
    def mean(self) -> float:
        return float(np.sum(np.array(self.strikes) * np.array(self.probabilities)))

    @property
    def median(self) -> float:
        return self.percentile(0.5)

    def percentile(self, p: float) -> float:
        cdf = np.cumsum(self.probabilities)
        idx = int(np.searchsorted(cdf, p))
        idx = min(idx, len(self.strikes) - 1)
        return float(self.strikes[idx])


def fetch_risk_free_rate() -> float:
    """13-week T-bill yield (^IRX) as a proxy for r, falling back to 4% if
    unavailable -- a small, standard approximation; it barely moves the
    Breeden-Litzenberger result relative to getting the IV curve right."""
    import yfinance as yf
    try:
        hist = yf.Ticker("^IRX").history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1]) / 100.0
    except Exception:
        pass
    return 0.04


def build_options_implied_distribution(
    symbol: str,
    target_expiration: str | None = None,
    min_open_interest: int = 20,
    grid_step: float = 1.0,
) -> OptionsImpliedDistribution:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    expirations = ticker.options
    if not expirations:
        raise ValueError(f"No options chain available for {symbol}")

    if target_expiration is None:
        # default: nearest expiration to 12 months out, to match the
        # analyst consensus's ~12-month horizon
        now = pd.Timestamp.now(tz="UTC")
        target = now + pd.DateOffset(months=12)
        exp_ts = [pd.Timestamp(e, tz="UTC") for e in expirations]
        target_expiration = expirations[int(np.argmin([abs((e - target).days) for e in exp_ts]))]

    spot = float(ticker.history(period="1d")["Close"].iloc[-1])
    chain = ticker.option_chain(target_expiration)
    calls = chain.calls.copy()
    n_raw = len(calls)

    calls["mid"] = (calls["bid"] + calls["ask"]) / 2
    valid = calls[
        (calls["bid"] > 0)
        & (calls["ask"] > calls["bid"])
        & (calls["openInterest"] >= min_open_interest)
        & (calls["impliedVolatility"] > 0.02)
        & (calls["impliedVolatility"] < 3.0)
    ].sort_values("strike")
    n_used = len(valid)
    if n_used < 8:
        raise ValueError(f"Only {n_used} liquid strikes for {symbol} {target_expiration} -- too few for a stable fit")

    exp_date = pd.Timestamp(target_expiration, tz="UTC")
    t_years = max((exp_date - pd.Timestamp.now(tz="UTC")).days, 1) / 365.25
    r = fetch_risk_free_rate()
    try:
        # yfinance's `dividendYield` field is inconsistently united across
        # versions (confirmed live: NVDA reports 0.45, which is percentage
        # POINTS -- i.e. 0.45%, not a 45% yield). dividendRate (annual $/share)
        # divided by spot is unambiguous and avoids that quirk entirely.
        info = ticker.info
        rate = info.get("dividendRate")
        q = float(rate) / spot if rate else float(info.get("trailingAnnualDividendYield") or 0.0)
        q = max(0.0, min(q, 0.20))  # sanity clip -- no S&P 500 stock yields >20%
    except Exception:
        q = 0.0

    log_moneyness = np.log(valid["strike"].values / spot)
    iv = valid["impliedVolatility"].values
    weights = np.log1p(valid["openInterest"].values)
    # degree-3 poly in log-moneyness, weighted by open interest -- smooths
    # over bid-ask noise in illiquid strikes without overfitting the smile
    coeffs = np.polyfit(log_moneyness, iv, deg=3, w=weights)

    k_min, k_max = float(valid["strike"].min()), float(valid["strike"].max())
    fine_strikes = np.arange(k_min, k_max + grid_step, grid_step)
    fine_log_m = np.log(fine_strikes / spot)
    fine_iv = np.clip(np.polyval(coeffs, fine_log_m), 0.02, 3.0)

    call_prices = bs_call_price(spot, fine_strikes, t_years, r, q, fine_iv)
    # discrete second derivative -> f(K) = e^{rT} * d^2C/dK^2, evaluated at
    # bucket midpoints between consecutive fine-grid strikes
    d2c = np.diff(call_prices, 2) / (grid_step ** 2)
    density = np.exp(r * t_years) * np.clip(d2c, 0, None)
    bucket_edges = fine_strikes[1:-1]  # midpoint strikes where the 2nd derivative is evaluated

    probs = density * grid_step
    total = probs.sum()
    if total <= 0:
        raise ValueError(f"Degenerate density for {symbol} {target_expiration}")
    probs = probs / total  # normalize over the observed strike range (truncated tails not modeled)

    return OptionsImpliedDistribution(
        symbol=symbol, spot=spot, expiration=target_expiration, t_years=t_years,
        risk_free_rate=r, dividend_yield=q,
        strikes=[float(x) for x in bucket_edges], probabilities=[float(x) for x in probs],
        n_raw_contracts=n_raw, n_used_contracts=n_used,
    )


def get_options_distribution_cached(symbol: str, **kwargs) -> OptionsImpliedDistribution | None:
    """Caches build_options_implied_distribution to disk per symbol -- at
    S&P-500 scale this is a genuinely slow, failure-prone fetch (illiquid
    options chains are common even among large caps; confirmed in a 24-stock
    pilot that ~40% failed the liquid-strikes threshold), so losing progress
    to a crash partway through a large run would be costly. Only the
    deliberate ValueError cases raised inside build_options_implied_distribution
    (no chain, too few liquid strikes, degenerate density) are treated as a
    real negative result worth caching -- those are properties of the
    ticker's current liquidity, not going to change within a session. Any
    other exception (network errors etc.) is transient and is NOT cached,
    so a later rerun retries it instead of remembering a false negative
    forever (the exact bug already hit twice this session with CUSIP and
    Wikipedia lookups).
    """
    cache_path = CACHE_DIR / f"{symbol}.json"
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
        return OptionsImpliedDistribution(**raw) if raw is not None else None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        dist = build_options_implied_distribution(symbol, **kwargs)
    except ValueError:
        cache_path.write_text("null")
        return None
    cache_path.write_text(json.dumps(dataclasses.asdict(dist)))
    return dist
