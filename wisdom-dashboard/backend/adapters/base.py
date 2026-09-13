"""The adapter contract. Every source -- live or stubbed -- implements this.

    class MyAdapter(SourceAdapter):
        name = "my_source"
        source_type = "prediction_market"  # or "options" | "futures"

        def fetch(self, asset: str) -> SourceFetchResult:
            ...  # build PriceDistribution objects, return them wrapped

Rules an adapter must follow so the rest of the system can treat every
source identically:
  * Never raise out of `fetch`. Network/parse errors are caught internally
    and reported via `SourceFetchResult.error`; the orchestrator still
    expects a `SourceFetchResult` back so one dead source degrades
    gracefully instead of crashing the whole aggregation run.
  * An asset this adapter has no data for (or doesn't cover) is not an
    error -- return an empty `distributions` list with `error=None`.
  * `PriceDistribution.weight` must be a liquidity/confidence proxy in the
    same rough units the adapter's own docstring explains (e.g. USD volume
    for prediction markets). Weights are only ever compared *within* an
    aggregation group where every contributing adapter's weighting method
    is documented -- see aggregation.py's module docstring for how
    cross-source-type weights are reconciled.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from common.distribution import SourceFetchResult, TouchFetchResult


class SourceAdapter(ABC):
    name: str
    source_type: str  # "prediction_market" | "options" | "futures"

    @abstractmethod
    def fetch(self, asset: str) -> SourceFetchResult:
        """Fetch and return this source's current point-in-time price
        distributions for `asset` (a canonical symbol, e.g. "BTC"). Must not
        raise."""
        raise NotImplementedError

    def fetch_touch(self, asset: str) -> TouchFetchResult:
        """Optional: sources that also have longer-dated *touch-probability*
        markets ("does price ever cross $X by date T", not "is price above
        $X AT T") override this. These are never merged into `fetch()`'s
        output or into the point-in-time aggregate -- see
        common/distribution.py's TouchForecast docstring for why. Default:
        no touch data. Must not raise."""
        return TouchFetchResult(source_name=self.name)
