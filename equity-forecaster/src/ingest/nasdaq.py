"""Nasdaq consensus client. A BENCHMARK to beat, never an input.

``api.nasdaq.com/api/analyst/<sym>/targetprice`` is keyless and returns the
current consensus target plus a dated monthly history of it:

    {"consensusOverview": {"lowPriceTarget": 245.0, "highPriceTarget": 400.0,
                           "priceTarget": 336.26, "buy": 16, "sell": 4, "hold": 10},
     "historicalConsensus": [{"x": 1756684800, "y": 232.14,
                              "z": {"buy": 13, "hold": 13, "sell": 2,
                                    "date": "09/01/2025", "consensus": "Buy"}}, ...]}

That dated history is the one genuinely useful thing here, and it is useful for
one specific purpose: Stage 8's consensus test asks whether the de-biased
composite beats raw consensus, and until now there was nothing to compare
against. Roughly thirteen monthly points per ticker.

Like ``finnhub.py``, this client REFUSES to emit :class:`PriceTargetRecord`
rows. It publishes only aggregated figures, and consuming a vendor's consensus
as a panel input silently imports that vendor's staleness window and inclusion
rules, which is the failure mode the whole design exists to avoid.

STATUS: verified against live responses on 2026-09-18. No credential required.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

from . import http
from .base import VendorClient
from .finnhub import ConsensusSnapshot

TARGET_URL = "https://api.nasdaq.com/api/analyst/{ticker}/targetprice"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _count(z: dict, key: str) -> int:
    try:
        return int(z.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def parse_target_price(payload: dict, ticker: str, retrieved_at: datetime):
    """Return ``(current_snapshot, historical_snapshots)``.

    The historical points carry only the consensus TARGET and the rating counts,
    not a high/low band, so those fields are left None rather than back-filled
    from the current overview. Stamping today's band onto a 2025 observation
    would be exactly the restatement contamination the store is built to avoid.
    """
    data = payload.get("data") or {}
    overview = data.get("consensusOverview") or {}

    current = ConsensusSnapshot(
        ticker=ticker.upper(),
        as_of=retrieved_at.date(),
        target_mean=overview.get("priceTarget"),
        target_median=None,
        target_high=overview.get("highPriceTarget"),
        target_low=overview.get("lowPriceTarget"),
        n_analysts=(_count(overview, "buy") + _count(overview, "hold")
                    + _count(overview, "sell")) or None,
        source="nasdaq",
        retrieved_at=retrieved_at,
    )

    history: list[ConsensusSnapshot] = []
    for point in data.get("historicalConsensus") or []:
        z = point.get("z") or {}
        raw = z.get("date")
        as_of = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                as_of = datetime.strptime(str(raw), fmt).date()
                break
            except (TypeError, ValueError):
                continue
        if as_of is None and point.get("x"):
            as_of = datetime.fromtimestamp(int(point["x"]), tz=timezone.utc).date()
        if as_of is None:
            continue
        history.append(
            ConsensusSnapshot(
                ticker=ticker.upper(),
                as_of=as_of,
                target_mean=point.get("y"),
                target_median=None,
                target_high=None,
                target_low=None,
                n_analysts=(_count(z, "buy") + _count(z, "hold") + _count(z, "sell")) or None,
                source="nasdaq",
                retrieved_at=retrieved_at,
            )
        )
    return current, history


class NasdaqConsensusClient(VendorClient):
    name = "nasdaq"
    is_real_data = True
    response_shape_verified = True

    request_delay_s = 1.0

    def __init__(self, url: str = TARGET_URL, delay: float | None = None):
        self.url = url
        if delay is not None:
            self.request_delay_s = delay

    def available(self) -> tuple[bool, str]:
        return True, "no credential required"

    def fetch(self, ticker: str, start: date | None = None, end: date | None = None):
        raise NotImplementedError(
            "Nasdaq publishes only aggregated consensus; use fetch_consensus() "
            "and treat the result as a benchmark, not as a panel input."
        )

    def fetch_consensus(self, ticker: str):
        payload = http.fetch_json(
            self.url.format(ticker=ticker.upper()),
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=40.0,
        )
        out = parse_target_price(payload, ticker, datetime.now(timezone.utc))
        time.sleep(self.request_delay_s)
        return out
