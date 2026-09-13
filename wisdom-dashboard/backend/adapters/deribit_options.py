"""Options-implied distribution adapter -- Phase 2, DESIGN ONLY, not wired up.

This file exists so the plugin interface, the asset registry, and the
aggregator can all be exercised end-to-end against the real shape Phase 2
will have, without yet spending the implementation effort. It conforms to
`SourceAdapter` and returns a valid (empty) `SourceFetchResult`, so wiring
this in today changes nothing about the dashboard's behavior -- BTC's
aggregate forecast is still Polymarket+Kalshi only until `fetch()` below is
filled in.

--- How this would actually be implemented ---

Target: for an asset with a liquid options market (BTC/ETH on Deribit;
equities/ETFs on standard listed exchanges), turn the options chain for one
expiry into a risk-neutral price distribution.

1. Pull the chain for the target expiry: strikes, calls/puts mid prices (or
   mark IV directly, if the venue publishes it -- Deribit's ticker/book
   summary endpoints do). Deribit's public market-data endpoints do not
   require auth; exact endpoint/field names should be re-verified against
   Deribit's current API docs before wiring this up (not verified live as
   part of this build).

2. Preferred method -- Breeden-Litzenberger: the risk-neutral PDF at strike
   K is the second derivative of the call price curve w.r.t. strike:
       f(K) = e^{rT} * d2C/dK2
   In practice this means fitting a smooth curve (e.g. a cubic spline) to
   mid IV across strikes, converting back to call prices via Black-Scholes,
   then finite-differencing that curve twice. This is the "correct" method
   because it uses the whole smile/skew, not just ATM IV, so it captures
   fat tails and skew the way the market is actually pricing them.

3. Simpler fallback -- lognormal-from-ATM-IV: if a full clean chain isn't
   available, approximate price at expiry as lognormal with
       sigma = ATM_IV * sqrt(T)   (T in years)
       mu = log(forward_price) - 0.5 * sigma^2
   and discretize that lognormal into PriceBuckets on the same kind of grid
   the other adapters use. Much cruder (assumes no skew/fat tails) but
   requires only a forward price and one IV number.

4. Weight: use total open interest (or 24h notional volume) across the
   strikes actually used to build the distribution, in the expiry's quote
   currency converted to USD -- the same "liquidity behind this number"
   role that prediction-market volume plays in polymarket.py/kalshi.py.

5. Emit one `PriceDistribution` per available expiry, with
   `source_type="options"`, so the aggregator can combine it with
   prediction-market distributions for the same asset/date exactly like any
   other source (see aggregation.py's module docstring for how weights
   across source *types* are reconciled -- this is the part worth reviewing
   before flipping this adapter on, since an options market's open interest
   and a prediction market's volume are not naturally the same unit).
"""
from __future__ import annotations

from adapters.base import SourceAdapter
from common.distribution import SourceFetchResult

NOT_YET_IMPLEMENTED = True


class DeribitOptionsAdapter(SourceAdapter):
    name = "deribit_options"
    source_type = "options"

    def fetch(self, asset: str) -> SourceFetchResult:
        # Returning an empty-but-valid result (not an error) is deliberate:
        # from the orchestrator's point of view "adapter not implemented
        # yet" and "adapter implemented but has nothing for this asset
        # right now" should look identical -- both just contribute zero
        # distributions to the aggregate.
        return SourceFetchResult(
            source_name=self.name,
            source_type=self.source_type,
            distributions=[],
            error=None,
        )
