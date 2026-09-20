"""Backtest: how accurate has Wall Street's aggregate analyst price-target
consensus actually been at predicting where a stock's price would be ~12
months later -- and does it beat doing nothing?

Data source: yfinance's `Ticker.upgrades_downgrades` -- confirmed live
(2026-09-19) to return REAL historical per-firm price-target-setting
events (not just a current snapshot), one row per rating/target action,
going back to 2012-2019 depending on the ticker (AAPL/MSFT since 2012,
NVDA since 2017, TSLA since 2019 -- checked directly, not assumed).
Columns: Firm, ToGrade, FromGrade, Action (main/reit/up/down/init),
priceTargetAction (Raises/Lowers/Maintains), currentPriceTarget,
priorPriceTarget, indexed by GradeDate. A currentPriceTarget of 0.0 means
that action was rating-only (confirmed live, e.g. a Needham "reit" row)
and is excluded. Free, no API key -- already a dependency elsewhere in
this repo (requirements.txt). Unofficial/scraped from Yahoo Finance, not
a documented stable API -- the free alternative to a paid institutional
feed (Refinitiv/FactSet IBES consensus), treat accordingly. Doesn't work
for ETFs/indices (confirmed: SPY 404s -- "no fundamentals data") --
individual equities only.

--- Method, mirroring btc_price_market_calibration.py's approach ---

1. RECONSTRUCT THE CONSENSUS AS OF DATE T, without lookahead: for each
   date T being backtested, forward-fill every covering firm's most
   recent price target set on or before T (excluding firms whose last
   action is older than COVERAGE_WINDOW_MONTHS -- yfinance's feed has no
   explicit "dropped coverage" signal, so a firm that quietly stopped
   covering the stock would otherwise contribute an indefinitely stale
   target; this is a documented judgment call, not a measured fact).
   Average across firms currently covering it.

2. SCORE AS AN IMPLIED RETURN, not a raw price level: `target/price_at_T
   - 1` compared against the REALIZED return `price_at_T+H / price_at_T -
   1` over the same horizon. This normalizes away price scale across
   stocks and over time, the same reason the trader-skill backtest scored
   "edge" (P&L/notional) rather than raw P&L.

3. COMPARE AGAINST A NAIVE BASELINE (implied return = 0%, i.e. "the stock
   doesn't move") -- the same sanity check the BTC calibration backtest
   ran, and for the same reason: a forecast is only interesting if it
   beats doing nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "raw" / "analyst_price_targets"
RESULTS_DIR = REPO_ROOT / "results" / "analyst_price_target_backtest"

HORIZON_MONTHS = 12  # the de facto standard convention for a "price target"
EVAL_FREQ_MONTHS = 3  # how often to take a backtest snapshot
COVERAGE_WINDOW_MONTHS = 15  # a firm's target older than this isn't counted -- see module docstring
MIN_FIRMS = 3  # require at least this many covering firms for a date to count


def fetch_upgrades_downgrades_cached(symbol: str) -> pd.DataFrame | None:
    cache_path = CACHE_DIR / f"{symbol}_upgrades_downgrades.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached is None:
            return None
        df = pd.DataFrame(cached["rows"])
        df.index = pd.to_datetime(df.pop("_grade_date"), utc=True)
        return df
    import yfinance as yf
    try:
        df = yf.Ticker(symbol).upgrades_downgrades
    except Exception:
        df = None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if df is None or df.empty:
        cache_path.write_text("null")
        return None
    df = df.copy()
    df.index = pd.to_datetime(df.index, utc=True)
    out = df.reset_index().rename(columns={df.index.name or "index": "_grade_date"})
    out["_grade_date"] = out["_grade_date"].astype(str)
    cache_path.write_text(json.dumps({"rows": out.to_dict(orient="records")}))
    return df


def fetch_price_history_cached(symbol: str) -> pd.Series | None:
    cache_path = CACHE_DIR / f"{symbol}_prices.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        if cached is None:
            return None
        s = pd.Series(cached["close"], index=pd.to_datetime(cached["dates"], utc=True))
        return s
    import yfinance as yf
    # Note: yfinance's "Close" column is always retroactively split-adjusted
    # (confirmed live -- pre-split NVDA prices already reflect the June 2024
    # 10:1 split even with auto_adjust=False); only "Adj Close" folds in
    # dividends on top. auto_adjust=False here just avoids the dividend
    # adjustment, which is immaterial next to the split issue handled below.
    df = yf.download(symbol, period="max", interval="1d", progress=False, auto_adjust=False)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if df.empty:
        cache_path.write_text("null")
        return None
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index, utc=True)
    cache_path.write_text(json.dumps({
        "dates": [str(d) for d in close.index],
        "close": [float(v) for v in close.values],
    }))
    return close


def fetch_splits_cached(symbol: str) -> pd.Series:
    cache_path = CACHE_DIR / f"{symbol}_splits.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        return pd.Series(cached["ratio"], index=pd.to_datetime(cached["dates"], utc=True))
    import yfinance as yf
    try:
        s = yf.Ticker(symbol).splits
    except Exception:
        s = None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if s is None or s.empty:
        cache_path.write_text(json.dumps({"dates": [], "ratio": []}))
        return pd.Series(dtype=float)
    s = s.copy()
    s.index = pd.to_datetime(s.index, utc=True)
    cache_path.write_text(json.dumps({"dates": [str(d) for d in s.index], "ratio": [float(v) for v in s.values]}))
    return s


def adjust_targets_for_splits(ud: pd.DataFrame, splits: pd.Series) -> pd.DataFrame:
    """currentPriceTarget is quoted in nominal, as-of-the-time share terms and
    is NOT retroactively adjusted when a stock later splits, but the Close
    price series always is (see fetch_price_history_cached) -- so a target set
    before a split must be divided by every split ratio that happened after it
    to become comparable. Confirmed live: NVDA split 4:1 (2021) and 10:1
    (2024); without this, an old target looked like a ~40x/+4000% "implied
    return" against the split-adjusted price series.
    """
    ud = ud.copy()
    if splits.empty:
        ud["adj_target"] = ud["currentPriceTarget"]
        return ud
    splits_sorted = splits.sort_index()
    total_factor = float(splits_sorted.prod())
    cum_up_to = splits_sorted.cumprod()
    split_dates = splits_sorted.index
    factors_after = []
    for gd in ud.index:
        n_before_or_on = split_dates.searchsorted(gd, side="right")
        factor_up_to = float(cum_up_to.iloc[n_before_or_on - 1]) if n_before_or_on > 0 else 1.0
        factors_after.append(total_factor / factor_up_to)
    ud["adj_target"] = ud["currentPriceTarget"] / np.array(factors_after)
    return ud


def consensus_at_date(ud: pd.DataFrame, as_of: pd.Timestamp, coverage_window_months: int = COVERAGE_WINDOW_MONTHS, spot_price: float | None = None) -> dict | None:
    window_start = as_of - pd.DateOffset(months=coverage_window_months)
    valid = ud[(ud.index <= as_of) & (ud.index >= window_start) & (ud["currentPriceTarget"] > 0)]
    if valid.empty:
        return None
    latest_per_firm = valid.sort_index().groupby("Firm").tail(1)
    targets = latest_per_firm["adj_target"]
    if spot_price:
        # confirmed live (BKNG's 2026-04-06 25:1 split): a firm's grade
        # timestamp can fall technically after a split's recorded effective
        # time while the target value itself is still quoted pre-split --
        # adjust_targets_for_splits' timestamp-based logic then leaves it
        # unadjusted, producing a ~25x outlier next to genuinely
        # post-split targets. A firm's target more than 5x or less than 0.2x
        # the current spot is essentially always a residual split artifact,
        # not a real 400%+ conviction call -- drop rather than adjust,
        # since which direction it should have been adjusted isn't
        # recoverable from the data alone.
        ratio = targets / spot_price
        targets = targets[(ratio >= 0.2) & (ratio <= 5.0)]
    if len(targets) < MIN_FIRMS:
        return None
    return {
        "mean": float(targets.mean()),
        "median": float(targets.median()),
        "std": float(targets.std()) if len(targets) > 1 else 0.0,
        "n_firms": int(len(targets)),
    }


def _price_at_or_before(prices: pd.Series, ts: pd.Timestamp) -> float | None:
    usable = prices[prices.index <= ts]
    if usable.empty:
        return None
    return float(usable.iloc[-1])


def build_backtest(symbols: list[str], horizon_months: int = HORIZON_MONTHS, eval_freq_months: int = EVAL_FREQ_MONTHS) -> pd.DataFrame:
    rows = []
    now = datetime.now(timezone.utc)
    end_eval = now - pd.DateOffset(months=horizon_months)  # need a realized outcome to already exist

    for sym in symbols:
        ud = fetch_upgrades_downgrades_cached(sym)
        prices = fetch_price_history_cached(sym)
        if ud is None or prices is None or ud.empty or prices.empty:
            continue
        ud = adjust_targets_for_splits(ud, fetch_splits_cached(sym))

        start_eval = ud.index.min() + pd.DateOffset(months=COVERAGE_WINDOW_MONTHS)
        if start_eval >= end_eval:
            continue
        eval_dates = pd.date_range(start_eval, end_eval, freq=f"{eval_freq_months}MS", tz="UTC")

        for d in eval_dates:
            cons = consensus_at_date(ud, d)
            if cons is None:
                continue
            price_at_d = _price_at_or_before(prices, d)
            price_at_horizon = _price_at_or_before(prices, d + pd.DateOffset(months=horizon_months))
            if price_at_d is None or price_at_horizon is None or price_at_d <= 0:
                continue
            implied_return = cons["mean"] / price_at_d - 1
            realized_return = price_at_horizon / price_at_d - 1
            rows.append({
                "symbol": sym, "as_of": d, "n_firms": cons["n_firms"], "target_dispersion_pct": cons["std"] / cons["mean"] if cons["mean"] else None,
                "target_mean": cons["mean"], "price_at_eval": price_at_d, "price_at_horizon": price_at_horizon,
                "implied_return": implied_return, "realized_return": realized_return,
                "error": implied_return - realized_return,
                "naive_error": 0.0 - realized_return,
                "direction_correct": bool(np.sign(implied_return) == np.sign(realized_return)),
            })
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    return {
        "n": int(len(df)),
        "n_symbols": int(df["symbol"].nunique()),
        "mean_error": float(df["error"].mean()),
        "mad_error": float(df["error"].abs().mean()),
        "rmse": float(np.sqrt((df["error"] ** 2).mean())),
        "naive_mad_error": float(df["naive_error"].abs().mean()),
        "naive_rmse": float(np.sqrt((df["naive_error"] ** 2).mean())),
        "direction_hit_rate": float(df["direction_correct"].mean()),
        "mean_implied_return": float(df["implied_return"].mean()),
        "mean_realized_return": float(df["realized_return"].mean()),
        "mean_n_firms": float(df["n_firms"].mean()),
    }
