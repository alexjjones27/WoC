"""Futures/perpetual curve adapter -- Phase 3, DESIGN ONLY, not wired up.

Same status as deribit_options.py: conforms to `SourceAdapter`, returns a
valid empty result, changes nothing about today's dashboard until `fetch()`
is filled in.

--- How this would actually be implemented ---

Two related but distinct signals live under "futures/perps":

1. Dated futures term structure (e.g. CME BTC futures, or a dated-futures
   venue): each contract's price vs. spot gives a market-implied forward
   price for that contract's expiry directly -- no distribution-fitting
   needed for the point estimate. Basis = (futures_price / spot - 1),
   annualized, tells you contango (positive, "market expects higher") vs
   backwardation (negative). Confidence band should be *tighter* for
   liquid, near-dated contracts and widen for illiquid/far-dated ones --
   e.g. band width scaled inversely to open interest, or by historical
   basis volatility for that tenor if available. This gives a point
   estimate + band, which the aggregator's common format handles as a
   PriceDistribution with a small number of coarse buckets (e.g. a
   discretized normal around the forward price) rather than requiring a
   full fitted PDF the way options do.

2. Perpetual funding rate (no fixed expiry, so no direct "price on date X"
   read): funding rate sign/magnitude is a *sentiment* signal, not a price
   target -- persistently positive funding (longs pay shorts) means
   leveraged demand is skewed long, and vice versa. This does not map
   cleanly to "price at date X" and should NOT be forced into the same
   distribution format by itself; the honest use of perp funding here is
   as a secondary confidence/skew adjustment on top of a dated-futures or
   options distribution for the same asset, not a standalone
   PriceDistribution. (If a standalone perp-derived estimate is wanted
   later, the least-bad approach is compounding the current funding rate
   forward to the target date as a drift on spot -- but this assumes
   funding stays constant, which it does not, so treat any such estimate
   as low-confidence/wide-banded almost by construction.)

3. Weight: open interest in the dated contract (for signal 1), or notional
   position value for the sentiment adjustment (signal 2) -- again the
   same "how much liquidity backs this number" role as elsewhere.
"""
from __future__ import annotations

from adapters.base import SourceAdapter
from common.distribution import SourceFetchResult

NOT_YET_IMPLEMENTED = True


class PerpFuturesAdapter(SourceAdapter):
    name = "perp_futures"
    source_type = "futures"

    def fetch(self, asset: str) -> SourceFetchResult:
        return SourceFetchResult(
            source_name=self.name,
            source_type=self.source_type,
            distributions=[],
            error=None,
        )
