"""Configuration and tunable constants for the equity price-target forecaster.

Everything here that could be a hidden assumption is named, given a default,
and made overridable by environment variable, so that a reader can tell at a
glance which numbers are conventions and which are fitted. Constants that the
design brief says must NOT be hard-coded (the age-decay lambda, the analyst
skill weights) are deliberately absent -- they are fitted in
``src/model/debias.py`` and reported with their own diagnostics.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

DATA_DIR = Path(os.environ.get("EQF_DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = Path(os.environ.get("EQF_DB_PATH", DATA_DIR / "equity_forecaster.duckdb"))

# ---------------------------------------------------------------------------
# Vendor credentials
#
# No vendor key is bundled and none is required to run the pipeline; the
# synthetic source exists so the code paths are exercisable without one. A key
# in any of these variables switches the corresponding real client on.
# ---------------------------------------------------------------------------

VENDOR_KEY_ENV = {
    "benzinga": "BENZINGA_API_KEY",
    "fmp": "FMP_API_KEY",
    "finnhub": "FINNHUB_API_KEY",
}


def vendor_key(vendor: str) -> str | None:
    """Return the API key for ``vendor`` from the environment, or None."""
    env_name = VENDOR_KEY_ENV.get(vendor)
    return os.environ.get(env_name) if env_name else None


# ---------------------------------------------------------------------------
# Horizon and staleness conventions
# ---------------------------------------------------------------------------

#: Sell-side price targets are conventionally 12-month targets. Used both as
#: the evaluation horizon and as the window over which a target is assumed to
#: be "about" the price.
FORECAST_HORIZON_DAYS = 365

#: Records older than this are dropped from the LEVEL estimate (the brief's
#: Stage 2b) but are retained in the store and in revision history. This is a
#: convention, not a fitted number -- the fitted decay is separate.
MAX_LEVEL_AGE_DAYS = 180

#: Which price to treat as the price the analyst anchored on.
#:
#: "prior_close" -- the last close strictly before action_date. This is the
#:   default because it is the last price knowable before the analyst acted,
#:   so it cannot absorb the market's same-day reaction to the target itself.
#:   Using the action-date close instead lets a target that moved the stock
#:   contaminate its own denominator and shrinks the measured implied return.
#: "action_close" -- the close on action_date. Closer to the brief's literal
#:   wording; kept so the difference can be measured rather than argued about.
SPOT_AT_ACTION_CONVENTION = os.environ.get("EQF_SPOT_CONVENTION", "prior_close")

#: Trailing window for the PIT beta of a stock against its sector proxy.
BETA_WINDOW_DAYS = 250

#: Minimum trailing observations before a beta is trusted; below this the
#: sector adjustment falls back to beta = 1.0 and says so.
BETA_MIN_OBS = 120

#: Prior standard deviation of true betas across stocks, for Vasicek shrinkage
#: of the fitted beta toward 1.0. The conventional value; the shrinkage weight
#: it produces is reported, so a reader can see how much work the prior did.
BETA_PRIOR_SD = 0.30

#: Below this R^2 the sector proxy explains so little of the stock's variance
#: that the sector adjustment is worth flagging. Not an error -- for some
#: stocks it is simply the truth.
BETA_LOW_R2 = 0.10

#: |implied return| above this is flagged as probably a corporate-action or
#: currency error rather than a real forecast. Not silently dropped -- flagged,
#: counted in the data-quality report, and excluded from fitting.
EXTREME_IMPLIED_RETURN = 2.0

#: Empirical-Bayes prior strength floor for firm anchoring offsets. Firms with
#: fewer observations than this are shrunk hard toward the panel mean.
FIRM_OFFSET_MIN_OBS = 3

# ---------------------------------------------------------------------------
# Sector proxies
#
# A real deployment takes GICS from the same vendor that supplies the targets.
# This map exists so the sector adjustment is testable end to end on free data;
# anything not listed falls back to SPY, which is recorded in the output as a
# reduced-quality adjustment rather than silently treated as a sector.
# ---------------------------------------------------------------------------

SECTOR_ETF_FALLBACK = "SPY"

TICKER_SECTOR_ETF = {
    # Information technology
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK", "AMD": "XLK",
    "CRM": "XLK", "ORCL": "XLK", "ADBE": "XLK", "INTC": "XLK", "CSCO": "XLK",
    "QCOM": "XLK", "TXN": "XLK", "MU": "XLK", "AMAT": "XLK",
    # Communication services
    "GOOGL": "XLC", "GOOG": "XLC", "META": "XLC", "NFLX": "XLC", "DIS": "XLC",
    "T": "XLC", "VZ": "XLC",
    # Consumer discretionary
    "AMZN": "XLY", "TSLA": "XLY", "HD": "XLY", "MCD": "XLY", "NKE": "XLY",
    "SBUX": "XLY", "LOW": "XLY",
    # Consumer staples
    "WMT": "XLP", "COST": "XLP", "PG": "XLP", "KO": "XLP", "PEP": "XLP",
    # Financials
    "JPM": "XLF", "BAC": "XLF", "WFC": "XLF", "GS": "XLF", "MS": "XLF",
    "C": "XLF", "BLK": "XLF", "SCHW": "XLF",
    # Health care
    "JNJ": "XLV", "UNH": "XLV", "LLY": "XLV", "PFE": "XLV", "MRK": "XLV",
    "ABBV": "XLV", "TMO": "XLV", "AMGN": "XLV",
    # Energy
    "XOM": "XLE", "CVX": "XLE", "COP": "XLE", "SLB": "XLE", "OXY": "XLE",
    # Industrials
    "BA": "XLI", "CAT": "XLI", "GE": "XLI", "HON": "XLI", "UPS": "XLI",
    "LMT": "XLI", "RTX": "XLI",
    # Materials / utilities / real estate
    "LIN": "XLB", "APD": "XLB", "NEE": "XLU", "DUK": "XLU", "SO": "XLU",
    "AMT": "XLRE", "PLD": "XLRE",
}


def sector_etf(ticker: str) -> tuple[str, bool]:
    """Return ``(etf, is_real_sector)`` for ``ticker``.

    ``is_real_sector`` is False when the fallback broad-market proxy is used,
    so callers can downgrade their confidence instead of pretending SPY is a
    sector.
    """
    t = ticker.upper()
    if t in TICKER_SECTOR_ETF:
        return TICKER_SECTOR_ETF[t], True
    return SECTOR_ETF_FALLBACK, False
