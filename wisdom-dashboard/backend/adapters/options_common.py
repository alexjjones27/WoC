"""Shared options math: one expiry's implied-volatility smile -> a
PriceDistribution. Used by every options adapter (deribit_options.py,
okx_options.py, derive_options.py) so the three venues differ only in how
they fetch and parse their chains, never in the maths.

Method (Breeden-Litzenberger, discrete form):

  1. Each venue publishes a mark implied vol per strike plus the forward
     price for that expiry. Using the venue's own forward (not spot) means
     no interest-rate or funding assumption is needed: everything below is
     in forward (undiscounted) terms.
  2. Re-price an undiscounted call at every strike with Black-76 using that
     strike's own IV, so the smile/skew is kept:
         C(K) = F N(d1) - K N(d2)
  3. The risk-neutral probability of finishing above the midpoint between
     two adjacent strikes is minus the call-price slope between them:
         P(S_T > (K_i + K_{i+1})/2) = -(C(K_{i+1}) - C(K_i)) / (K_{i+1} - K_i)
     This is the standard finite-difference digital; it needs no spline or
     smoothing parameter.
  4. Noisy marks can make that survival curve tick up between neighbours,
     which is impossible for a real distribution. It's forced
     non-increasing with a running minimum and clipped to [0, 1].
  5. Differences of the survival curve give bucket probabilities between
     midpoints, plus two open tails -- exactly the PriceBucket shape the
     prediction-market adapters already produce, so aggregation.py treats
     an options chain like any other source.

Risk-neutral, not real-world: options prices include a variance risk
premium, so these distributions are usually a little WIDER than what
traders actually expect. That's a known, documented bias, and is one reason
options are shown as their own crowd rather than blended into the
prediction-market headline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from common.distribution import PriceBucket, PriceDistribution

# Strikes further than this multiple from the forward are dropped: their
# marks are extrapolated by the venue's own model, not traded.
MAX_MONEYNESS = 2.5
MIN_STRIKES = 6
# Expiries closer than this are dropped -- inside a few hours the smile
# collapses and the finite differences get numerically ugly.
MIN_HOURS_TO_EXPIRY = 6.0


@dataclass
class SmilePoint:
    strike: float
    iv: float  # annualized, as a fraction (0.45 = 45%)
    open_interest_usd: float = 0.0
    volume_usd: float = 0.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76_call(forward: float, strike: float, iv: float, t_years: float) -> float:
    if iv <= 0 or t_years <= 0:
        return max(forward - strike, 0.0)
    sd = iv * math.sqrt(t_years)
    d1 = (math.log(forward / strike) + 0.5 * sd * sd) / sd
    return forward * _norm_cdf(d1) - strike * _norm_cdf(d1 - sd)


def smile_to_buckets(forward: float, t_years: float, smile: list[SmilePoint]) -> list[PriceBucket] | None:
    """Returns buckets that sum to 1, or None if the chain is too thin."""
    by_strike: dict[float, SmilePoint] = {}
    for p in smile:
        if p.iv <= 0 or not (forward / MAX_MONEYNESS <= p.strike <= forward * MAX_MONEYNESS):
            continue
        # Calls and puts at one strike usually share a mark IV; if they
        # don't, keep the one with more open interest behind it.
        if p.strike not in by_strike or p.open_interest_usd > by_strike[p.strike].open_interest_usd:
            by_strike[p.strike] = p
    strikes = sorted(by_strike)
    if len(strikes) < MIN_STRIKES:
        return None

    calls = [black76_call(forward, k, by_strike[k].iv, t_years) for k in strikes]
    mids: list[float] = []
    survival: list[float] = []
    running = 1.0
    for i in range(len(strikes) - 1):
        k0, k1 = strikes[i], strikes[i + 1]
        p_above = -(calls[i + 1] - calls[i]) / (k1 - k0)
        running = min(running, max(0.0, min(1.0, p_above)))
        mids.append((k0 + k1) / 2.0)
        survival.append(running)

    buckets = [PriceBucket(low=None, high=mids[0], prob=1.0 - survival[0], label=f"< {mids[0]:,.0f}")]
    for i in range(len(mids) - 1):
        prob = survival[i] - survival[i + 1]
        buckets.append(PriceBucket(low=mids[i], high=mids[i + 1], prob=prob, label=f"{mids[i]:,.0f}-{mids[i + 1]:,.0f}"))
    buckets.append(PriceBucket(low=mids[-1], high=None, prob=survival[-1], label=f"> {mids[-1]:,.0f}"))
    return buckets


def build_options_distribution(
    *,
    asset: str,
    source_name: str,
    expiry: datetime,
    forward: float,
    smile: list[SmilePoint],
    source_url: str,
    venue_note: str,
    now: datetime | None = None,
) -> PriceDistribution | None:
    now = now or datetime.now(timezone.utc)
    hours = (expiry - now).total_seconds() / 3600.0
    if hours < MIN_HOURS_TO_EXPIRY or forward <= 0:
        return None
    t_years = hours / (24.0 * 365.25)
    buckets = smile_to_buckets(forward, t_years, smile)
    if buckets is None:
        return None
    oi = sum(p.open_interest_usd for p in smile)
    vol = sum(p.volume_usd for p in smile)
    return PriceDistribution(
        asset=asset,
        source_type="options",
        source_name=source_name,
        target_date=expiry.date(),
        period_label=expiry.strftime("%b %-d, %Y"),
        buckets=buckets,
        # Open interest is the "money behind this number" for options (the
        # analogue of prediction-market volume). It's only ever compared
        # against other options venues -- see aggregation.build_dashboard.
        weight=oi,
        resolve_datetime_utc=expiry.isoformat(),
        volume=vol,
        open_interest=oi,
        source_url=source_url,
        raw_note=(
            f"{venue_note} Risk-neutral distribution from the mark-IV smile (Breeden-Litzenberger), "
            f"forward {forward:,.2f}; typically a little wider than real-world expectations."
        ),
        fetched_at_utc=now.isoformat(),
    )
