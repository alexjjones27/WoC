"""How concentrated is trading behind one Polymarket market's implied
probability -- used to discount that market's weight in the cross-platform
mixture when its volume is really just a handful of large wallets, not a
broad crowd.

Motivated by a direct empirical check (see
../../results/polymarket_trader_concentration/report.md for the full
writeup): pulled full trade history for 6 live BTC/OIL Polymarket markets
spanning $1.7k-$6.8M volume. Every one showed real trader concentration
(effective independent traders, via 1/HHI of volume-by-wallet, ranged from
~1 on the thinnest daily buckets to ~20 on the most diffuse) AND a
consistent positive lag-1 autocorrelation of trade direction (0.02-0.33) --
a momentum/herding signature, not independent fresh assessment each trade.
Previously this dashboard's weighting used raw dollar volume as the only
liquidity proxy, which a concentrated market can rack up via a few large
traders just as easily as via genuine broad participation -- this module
is the fix for that specific gap.

Kalshi (a regulated DCM, no public wallet-level trade data) and Manifold
(not checked) don't get this treatment -- Polymarket-only, not by choice.
That's a real, documented asymmetry: a Polymarket market can end up MORE
discounted than an equally-concentrated Kalshi one purely because we can
measure one and not the other -- see the wisdom-dashboard README.

HERDING (the autocorrelation finding) is NOT used for any live correction
here -- it's a real, consistently-observed signature but this module
stops at concentration, which has a much more direct, well-understood
translation into "how much should this market's weight count for" (the
same design-effect logic survey statisticians use for clustered samples).
Folding herding into a correction too would need a defensible model for
exactly how much autocorrelated trading degrades independence, which the
raw correlation number alone doesn't give you -- left as a further
open question, not quietly assumed away.
"""
from __future__ import annotations

import numpy as np

from adapters.http import get_json
from cache import TTLCache

DATA_API_BASE = "https://data-api.polymarket.com"

# Trading concentration changes slowly (it's about who has traded over a
# market's whole lifetime) -- a long TTL keeps this from re-fetching full
# trade history on every 45s dashboard refresh.
CACHE_TTL_SECONDS = 1800.0
_cache = TTLCache(ttl_seconds=CACHE_TTL_SECONDS)

# A market with this many "effective" (1/HHI) independent traders or more
# gets no discount at all; below that, its weight shrinks linearly toward
# MIN_DISCOUNT. 20 was chosen as roughly the most diffuse trading observed
# in the 6-market check this is based on -- a documented judgment call,
# not a statistically derived threshold; tune if you disagree.
REFERENCE_EFFECTIVE_TRADERS = 20.0
MIN_DISCOUNT = 0.05  # floor: even a single-wallet market keeps a little weight, not zero

MIN_TRADES_FOR_ESTIMATE = 10  # below this, the HHI estimate is too noisy to trust -- treat as unmeasured
MAX_TRADES_FETCHED = 2000  # bounds latency on a cache miss; a recency-biased sample, not exhaustive


def _fetch_trades(condition_id: str) -> list[dict]:
    trades: list[dict] = []
    offset = 0
    page_size = 500
    while offset < MAX_TRADES_FETCHED:
        batch = get_json(f"{DATA_API_BASE}/trades", {"market": condition_id, "limit": page_size, "offset": offset})
        if not batch:
            break
        trades.extend(batch)
        offset += len(batch)
        if len(batch) < page_size:
            break
    return trades


def _effective_traders(condition_id: str) -> float | None:
    """1/HHI of volume-by-wallet -- see module docstring. None if there's
    too little trade history for the estimate to mean anything."""
    trades = _fetch_trades(condition_id)
    if len(trades) < MIN_TRADES_FOR_ESTIMATE:
        return None
    notional_by_wallet: dict[str, float] = {}
    for t in trades:
        try:
            w = t["proxyWallet"]
            notional_by_wallet[w] = notional_by_wallet.get(w, 0.0) + float(t["size"]) * float(t["price"])
        except (KeyError, TypeError, ValueError):
            continue
    total = sum(notional_by_wallet.values())
    if total <= 0 or not notional_by_wallet:
        return None
    shares = np.array(list(notional_by_wallet.values())) / total
    hhi = float(np.sum(shares ** 2))
    return (1.0 / hhi) if hhi > 0 else None


def discount_for(condition_id: str) -> tuple[float, float | None]:
    """Returns (discount, effective_traders). discount is 1.0 (no
    adjustment) if trade history couldn't be fetched or was too thin to
    estimate -- unmeasured is treated as "benefit of the doubt", not
    penalized, since a failed API call shouldn't silently punish a market.
    Cached (CACHE_TTL_SECONDS) since this never needs to be fresher than
    that and fetching it is comparatively expensive (full trade history)."""
    cached = _cache.get(condition_id)
    if cached is not None:
        return cached
    try:
        eff = _effective_traders(condition_id)
    except Exception:  # noqa: BLE001 -- this is a secondary enhancement; never break the primary fetch
        eff = None
    if eff is None:
        result = (1.0, None)
    else:
        discount = float(np.clip(eff / REFERENCE_EFFECTIVE_TRADERS, MIN_DISCOUNT, 1.0))
        result = (discount, eff)
    _cache.set(condition_id, result)
    return result
