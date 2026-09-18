"""Finnhub client -- CONSENSUS BENCHMARK ONLY, not a panel source.

The brief lists Finnhub's ``price-target`` and ``recommendation-trends`` as
candidate sources. Both are pre-aggregated: ``price-target`` returns
high/low/mean/median across the panel, and ``recommendation-trends`` returns
counts of buy/hold/sell by month. Neither exposes a single analyst's action,
which is exactly the input the design says not to substitute a vendor's
consensus for.

So this client deliberately refuses to emit :class:`PriceTargetRecord` rows.
What it does instead is fetch the consensus as a BENCHMARK, which Stage 8's
consensus test needs: "does the de-biased composite beat the number a free
website gives you" requires having that number, recorded point-in-time, with
the vendor's own staleness baked in.

STATUS: endpoint existence confirmed (a keyless request returns a 401 from the
documented URLs); response parsing is written to the published schema and is
unverified against a live authenticated response.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .. import config
from . import http
from .base import VendorClient

BASE_URL = "https://finnhub.io/api/v1"


@dataclass(frozen=True)
class ConsensusSnapshot:
    """A vendor's aggregated view, stored to be beaten, never to be an input."""

    ticker: str
    as_of: date
    target_mean: float | None
    target_median: float | None
    target_high: float | None
    target_low: float | None
    n_analysts: int | None
    source: str
    retrieved_at: datetime


class FinnhubConsensusClient(VendorClient):
    name = "finnhub"
    is_real_data = True
    response_shape_verified = False

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL):
        self.api_key = api_key or config.vendor_key("finnhub")
        self.base_url = base_url.rstrip("/")

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"set {config.VENDOR_KEY_ENV['finnhub']} to enable"
        return True, "ok"

    def fetch(self, ticker: str, start: date | None = None, end: date | None = None):
        raise NotImplementedError(
            "Finnhub exposes only pre-aggregated consensus; use "
            "fetch_consensus() and treat the result as a benchmark, not as a "
            "panel input."
        )

    def fetch_consensus(self, ticker: str) -> ConsensusSnapshot:
        ok, reason = self.available()
        if not ok:
            raise http.FetchError(f"finnhub unavailable: {reason}")
        payload = http.fetch_json(
            f"{self.base_url}/stock/price-target",
            params={"symbol": ticker.upper(), "token": self.api_key},
        )
        last = payload.get("lastUpdated")
        try:
            as_of = datetime.fromisoformat(str(last).replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            as_of = datetime.now(timezone.utc).date()
        return ConsensusSnapshot(
            ticker=ticker.upper(),
            as_of=as_of,
            target_mean=payload.get("targetMean"),
            target_median=payload.get("targetMedian"),
            target_high=payload.get("targetHigh"),
            target_low=payload.get("targetLow"),
            n_analysts=payload.get("numberOfAnalysts"),
            source="finnhub",
            retrieved_at=datetime.now(timezone.utc),
        )
