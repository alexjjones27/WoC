"""Cross-sectional factor backtest: does wisdom-of-the-crowd analyst data
actually help PICK STOCKS, not just forecast one stock's price in isolation?

This is a different (and stricter) question than
analyst_price_target_backtest.py answers. That module asks "how accurate is
the consensus price target" per stock, scored against its own history --
useful, but not what a trading strategy needs. A signal can have a large
absolute forecast error and still be a great STOCK-PICKING signal, as long
as it correctly ranks which stocks will outperform which others. This
module tests that directly: at each rebalance date, rank the whole universe
by a wisdom-of-crowd signal, and see whether stocks ranked highly actually
go on to outperform stocks ranked lowly.

Universe: current S&P 500 constituents (free, scraped from Wikipedia).
This is NOT point-in-time-correct -- it is today's membership, so
companies that were removed from the index (bankruptcy, steep decline,
etc.) between the backtest start and today are invisible to this backtest.
That is a real, unresolved survivorship bias; a proper fix needs a paid or
much harder-to-source point-in-time membership history. What this DOES fix
relative to a hand-picked list of mega-caps is "I chose 15 stocks I already
knew had done well" -- a much cruder and more obviously biased selection.

Signals tested, each computed with NO lookahead (only data on or before the
rebalance date):
  - upside: consensus_target / price - 1 (the static "underpriced" signal)
  - revision: mean %-change of price targets revised within the last 3
    months (recent analyst re-rating direction/magnitude -- a faster signal
    than the static level; well documented in the literature as "revision
    momentum")
  - combined: average of the two signals' cross-sectional ranks (a cheap,
    standard way to combine signals without needing to fit weights)

Each signal is scored two ways:
  - Information Coefficient (IC): the Spearman rank correlation, each
    period, between the signal and the forward return that followed it.
    This is the standard factor-investing metric for "does the ranking
    work" -- unlike MAE, it doesn't care about the signal's absolute scale.
  - Quintile spread: go long the top 20% of the universe by signal, short
    (or just compare against) the bottom 20%, equal-weighted, and measure
    the average forward-return spread between them.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from analyst_price_target_backtest import (  # noqa: E402
    CACHE_DIR,
    COVERAGE_WINDOW_MONTHS,
    MIN_FIRMS,
    adjust_targets_for_splits,
    consensus_at_date,
    fetch_price_history_cached,
    fetch_splits_cached,
    fetch_upgrades_downgrades_cached,
)

RESULTS_DIR = REPO_ROOT / "results" / "analyst_factor_backtest"
UNIVERSE_CACHE = CACHE_DIR / "sp500_universe.json"

REVISION_LOOKBACK_MONTHS = 3
REBALANCE_FREQ_MONTHS = 3
HOLD_MONTHS = 3
QUINTILE_FRACTION = 0.2
WINSOR_LIMIT = 1.5  # clip implied/revision returns to +-150% before ranking -- guards against any remaining data artifacts (see split-adjustment bug found earlier), doesn't affect rank-based stats much either way


def fetch_sp500_universe(force: bool = False) -> list[dict]:
    """Returns list of {"symbol": ..., "date_added": ..., "sector": ...,
    "security": ...}. Cached on disk -- delete the cache file (or pass
    force=True) to re-scrape a fresh membership list. Older cached copies
    written before "sector"/"security" were added will simply lack those
    keys; callers should use .get() rather than assume they're present."""
    if UNIVERSE_CACHE.exists() and not force:
        return json.loads(UNIVERSE_CACHE.read_text())
    headers = {"User-Agent": "Mozilla/5.0 (research script; contact alexjoneswork05@gmail.com)"}
    r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=headers, timeout=20)
    r.raise_for_status()
    df = pd.read_html(StringIO(r.text))[0]
    out = []
    for _, row in df.iterrows():
        sym = str(row["Symbol"]).strip().replace(".", "-")  # yfinance uses BRK-B not BRK.B
        out.append({
            "symbol": sym, "date_added": str(row.get("Date added", "")),
            "sector": str(row.get("GICS Sector", "")), "security": str(row.get("Security", "")),
        })
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    UNIVERSE_CACHE.write_text(json.dumps(out, indent=2))
    return out


def revision_momentum_at_date(ud: pd.DataFrame, as_of: pd.Timestamp, lookback_months: int = REVISION_LOOKBACK_MONTHS) -> float | None:
    window_start = as_of - pd.DateOffset(months=lookback_months)
    recent = ud[(ud.index > window_start) & (ud.index <= as_of) & (ud["currentPriceTarget"] > 0) & (ud["priorPriceTarget"] > 0)]
    if recent.empty:
        return None
    ratio = recent["currentPriceTarget"] / recent["priorPriceTarget"]
    # yfinance's priorPriceTarget occasionally has garbage values (confirmed
    # live: an ACN row claimed priorPriceTarget=$4 against a ~$80 stock,
    # implying a fake +1900% revision) -- a single such row can dominate a
    # 3-month window that otherwise has only a handful of revisions, so
    # exclude implausible single-revision jumps rather than rely on the
    # aggregate-level clip alone.
    sane = recent[(ratio >= 0.2) & (ratio <= 5.0)]
    if sane.empty:
        return None
    pct_changes = sane["currentPriceTarget"] / sane["priorPriceTarget"] - 1
    return float(pct_changes.mean())


def _load_symbol_data(sym: str) -> dict | None:
    ud = fetch_upgrades_downgrades_cached(sym)
    prices = fetch_price_history_cached(sym)
    if ud is None or prices is None or ud.empty or prices.empty:
        return None
    ud = adjust_targets_for_splits(ud, fetch_splits_cached(sym))
    return {"ud": ud, "prices": prices}


def _price_at_or_before(prices: pd.Series, ts: pd.Timestamp) -> float | None:
    usable = prices[prices.index <= ts]
    if usable.empty:
        return None
    return float(usable.iloc[-1])


def build_panel(symbols: list[str], log_every: int = 25, log_fn=print) -> pd.DataFrame:
    """One row per (rebalance_date, symbol) with signals + forward return."""
    now = pd.Timestamp(datetime.now(timezone.utc))
    end_eval = now - pd.DateOffset(months=HOLD_MONTHS)

    loaded = {}
    for i, sym in enumerate(symbols):
        d = _load_symbol_data(sym)
        if d is not None:
            loaded[sym] = d
        if log_every and (i + 1) % log_every == 0:
            log_fn(f"  loaded {i + 1}/{len(symbols)} symbols ({len(loaded)} usable so far)")
    log_fn(f"Usable symbols: {len(loaded)}/{len(symbols)}")

    if not loaded:
        return pd.DataFrame()

    earliest = min(d["ud"].index.min() for d in loaded.values())
    start_eval = earliest + pd.DateOffset(months=COVERAGE_WINDOW_MONTHS)
    rebalance_dates = pd.date_range(start_eval, end_eval, freq=f"{REBALANCE_FREQ_MONTHS}MS", tz="UTC")

    rows = []
    for rd in rebalance_dates:
        for sym, d in loaded.items():
            ud, prices = d["ud"], d["prices"]
            cons = consensus_at_date(ud, rd)
            price_now = _price_at_or_before(prices, rd)
            price_fwd = _price_at_or_before(prices, rd + pd.DateOffset(months=HOLD_MONTHS))
            if cons is None or price_now is None or price_fwd is None or price_now <= 0:
                continue
            implied_return = cons["mean"] / price_now - 1
            forward_return = price_fwd / price_now - 1
            rev = revision_momentum_at_date(ud, rd)
            rows.append({
                "date": rd, "symbol": sym,
                "implied_return": float(np.clip(implied_return, -WINSOR_LIMIT, WINSOR_LIMIT)),
                "dispersion": cons["std"] / cons["mean"] if cons["mean"] else None,
                "n_firms": cons["n_firms"],
                "revision": float(np.clip(rev, -WINSOR_LIMIT, WINSOR_LIMIT)) if rev is not None else None,
                "forward_return": float(np.clip(forward_return, -WINSOR_LIMIT, WINSOR_LIMIT)),
            })
    return pd.DataFrame(rows)


def _rank_pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True)


def add_combined_signal(panel: pd.DataFrame) -> pd.DataFrame:
    """combined = average of each period's cross-sectional percentile rank on
    upside and on revision momentum -- a simple, weight-free way to combine
    two signals that isn't just refitting to this same backtest."""
    panel = panel.copy()
    panel["combined"] = np.nan
    for date, g in panel.groupby("date"):
        has_both = g.dropna(subset=["implied_return", "revision"])
        if len(has_both) < 10:
            continue
        r1 = _rank_pct(has_both["implied_return"])
        r2 = _rank_pct(has_both["revision"])
        panel.loc[has_both.index, "combined"] = ((r1 + r2) / 2).values

    panel["upside_lowdisp"] = np.nan
    for date, g in panel.groupby("date"):
        has_both = g.dropna(subset=["implied_return", "dispersion"])
        if len(has_both) < 10:
            continue
        r1 = _rank_pct(has_both["implied_return"])
        r2 = _rank_pct(-has_both["dispersion"])  # low dispersion -> high rank
        panel.loc[has_both.index, "upside_lowdisp"] = ((r1 + r2) / 2).values
    return panel


def score_signal(panel: pd.DataFrame, signal_col: str) -> dict:
    ics, spreads, universe_rets, n_per_period = [], [], [], []
    for date, g in panel.dropna(subset=[signal_col, "forward_return"]).groupby("date"):
        if len(g) < 10:
            continue
        ic = g[signal_col].corr(g["forward_return"], method="spearman")
        if pd.isna(ic):
            continue
        ranked = g.sort_values(signal_col)
        n = len(ranked)
        k = max(1, int(round(n * QUINTILE_FRACTION)))
        bottom = ranked.iloc[:k]["forward_return"].mean()
        top = ranked.iloc[-k:]["forward_return"].mean()
        ics.append(ic)
        spreads.append(top - bottom)
        universe_rets.append(g["forward_return"].mean())
        n_per_period.append(n)

    if not ics:
        return {"n_periods": 0}
    ics_arr = np.array(ics)
    spreads_arr = np.array(spreads)
    return {
        "n_periods": len(ics),
        "mean_n_per_period": float(np.mean(n_per_period)),
        "mean_ic": float(ics_arr.mean()),
        "ic_tstat": float(ics_arr.mean() / (ics_arr.std(ddof=1) / np.sqrt(len(ics_arr)))) if len(ics_arr) > 1 else None,
        "pct_periods_ic_positive": float((ics_arr > 0).mean()),
        "mean_quintile_spread": float(spreads_arr.mean()),
        "spread_win_rate": float((spreads_arr > 0).mean()),
        "mean_universe_return": float(np.mean(universe_rets)),
    }
