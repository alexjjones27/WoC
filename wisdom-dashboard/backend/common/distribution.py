"""The common intermediate representation every source adapter must output.

This is the one contract that makes the system pluggable: a prediction-market
adapter, an options adapter, and a futures adapter describe price information
in totally different native units (discrete Yes/No bets, an IV surface, a
funding rate) but they all compress down to the same thing here -- a
piecewise-constant probability distribution over price at a fixed future
date, plus a liquidity-derived weight. The aggregation engine (see
../aggregation.py) only ever operates on lists of `PriceDistribution`; it has
no idea whether a given one came from Polymarket, Deribit, or a futures curve.
Adding a new source is "write an adapter that returns `PriceDistribution`
objects," never "teach the aggregator about a new data shape."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class PriceBucket:
    """One piece of a piecewise-constant price distribution.

    Represents "probability `prob` that the asset's price at the
    distribution's target_date falls in (low, high]". `low=None` means the
    bucket extends to -inf; `high=None` means it extends to +inf. Adapters
    are expected (but not required -- the aggregator renormalizes) to return
    buckets whose `prob` values sum to ~1.0 for a given distribution.
    """

    low: float | None
    high: float | None
    prob: float
    label: str = ""


@dataclass
class PriceDistribution:
    """A single source's forecast of an asset's price at one future date.

    One of these is produced per (adapter, target date) pair. A single
    adapter call typically returns several of these -- e.g. Polymarket has
    one "Bitcoin price on <date>" market per upcoming date, so the
    Polymarket adapter returns one PriceDistribution per date it found an
    active market for.
    """

    asset: str  # e.g. "BTC"
    source_type: str  # "prediction_market" | "options" | "futures"
    source_name: str  # "polymarket" | "kalshi" | "deribit_options" | ...
    target_date: date  # the date this distribution describes
    period_label: str  # human label, e.g. "Sep 19, 2026"
    buckets: list[PriceBucket]
    weight: float  # liquidity/volume-derived contribution weight, >= 0
    resolve_datetime_utc: str | None = None  # ISO timestamp, if more precise than target_date
    volume: float | None = None
    open_interest: float | None = None
    liquidity: float | None = None
    source_url: str | None = None
    raw_note: str | None = None  # free-text: what exactly this distribution represents
    is_play_money: bool = False  # volume/weight are in a non-redeemable token, not USD (see e.g. adapters/manifold.py)
    concentration_discount: float | None = None  # 1.0 = none applied; see backend/concentration.py
    concentration_effective_traders: float | None = None  # 1/HHI of volume-by-wallet, if measured
    stale: bool = False
    error: str | None = None
    fetched_at_utc: str | None = None


@dataclass
class SourceFetchResult:
    """Wraps one adapter's output for one asset so failures are visible
    rather than silently swallowed -- a dead API or an asset an adapter
    doesn't cover both come back as an (empty distributions, error) result,
    never an exception that takes down the whole dashboard.
    """

    source_name: str
    source_type: str
    distributions: list[PriceDistribution] = field(default_factory=list)
    error: str | None = None


@dataclass
class TouchThreshold:
    """One "does price ever cross $X by date T" market. Deliberately NOT a
    PriceBucket: touch markets are independent barrier bets, not a
    partition of outcome space, so their probabilities are neither meant
    to sum to 1 nor safe to run through the point-in-time PDF/mean/std
    machinery in aggregation.py -- see TouchForecast below."""

    direction: str  # "above" | "below"
    price: float
    prob_touch: float
    volume: float | None = None
    label: str = ""


@dataclass
class TouchForecast:
    """One source's touch-probability ladder for one expiry date. Kept
    entirely separate from PriceDistribution/AggregateForecast -- these
    answer "ever crosses $X by T" (structurally higher than "is above $X
    AT T"), so they are surfaced in their own dashboard section, per
    platform, never blended into the point-in-time aggregate."""

    asset: str
    source_name: str
    expiry_date: date
    period_label: str
    thresholds: list[TouchThreshold]
    total_volume: float
    source_url: str | None = None
    raw_note: str | None = None
    resolve_datetime_utc: str | None = None
    fetched_at_utc: str | None = None


@dataclass
class TouchFetchResult:
    source_name: str
    touches: list[TouchForecast] = field(default_factory=list)
    error: str | None = None
