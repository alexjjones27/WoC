"""Deribit options adapter (live): BTC and ETH.

Deribit is the largest crypto options venue. One public, unauthenticated
call returns every live option for a currency with its mark IV, the forward
("underlying_price") for that option's expiry, open interest and 24h volume
(confirmed live 2026-09-23: 960 BTC options). Each expiry's smile becomes
one PriceDistribution via adapters/options_common.py.

Deribit has no gold or oil options, so GOLD/OIL return an empty result.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from adapters.base import SourceAdapter
from adapters.http import get_json
from adapters.options_common import SmilePoint, build_options_distribution
from common.distribution import SourceFetchResult

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"
CURRENCY = {"BTC": "BTC", "ETH": "ETH"}
EXPIRY_HOUR_UTC = 8  # every Deribit expiry settles at 08:00 UTC


def parse_instrument(name: str) -> tuple[datetime, float] | None:
    """"BTC-24SEP26-90000-C" -> (2026-09-24 08:00 UTC, 90000.0)."""
    parts = name.split("-")
    if len(parts) != 4:
        return None
    try:
        day = datetime.strptime(parts[1], "%d%b%y")
        strike = float(parts[2].replace("d", "."))
    except ValueError:
        return None
    return day.replace(hour=EXPIRY_HOUR_UTC, tzinfo=timezone.utc), strike


def chain_to_smiles(rows: list[dict]) -> dict[datetime, tuple[float, list[SmilePoint]]]:
    """Group Deribit book-summary rows into {expiry: (forward, smile)}."""
    smiles: dict[datetime, list[SmilePoint]] = defaultdict(list)
    forwards: dict[datetime, float] = {}
    for r in rows:
        parsed = parse_instrument(r.get("instrument_name", ""))
        iv = r.get("mark_iv")
        fwd = r.get("underlying_price")
        if parsed is None or not iv or not fwd:
            continue
        expiry, strike = parsed
        forwards[expiry] = float(fwd)
        smiles[expiry].append(SmilePoint(
            strike=strike,
            iv=float(iv) / 100.0,
            open_interest_usd=float(r.get("open_interest") or 0.0) * float(fwd),
            volume_usd=float(r.get("volume_usd") or 0.0),
        ))
    return {e: (forwards[e], smiles[e]) for e in smiles}


class DeribitOptionsAdapter(SourceAdapter):
    name = "deribit_options"
    source_type = "options"

    def fetch(self, asset: str) -> SourceFetchResult:
        currency = CURRENCY.get(asset)
        if currency is None:
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)
        try:
            data = get_json(f"{DERIBIT_BASE}/get_book_summary_by_currency", {"currency": currency, "kind": "option"})
        except Exception as exc:  # noqa: BLE001
            return SourceFetchResult(source_name=self.name, source_type=self.source_type, error=f"fetch failed: {exc}")

        distributions = []
        for expiry, (forward, smile) in chain_to_smiles(data.get("result", [])).items():
            d = build_options_distribution(
                asset=asset,
                source_name=self.name,
                expiry=expiry,
                forward=forward,
                smile=smile,
                source_url=f"https://www.deribit.com/options/{currency}",
                venue_note="Deribit (largest crypto options venue).",
            )
            if d is not None:
                distributions.append(d)
        distributions.sort(key=lambda d: d.target_date)
        return SourceFetchResult(source_name=self.name, source_type=self.source_type, distributions=distributions)
