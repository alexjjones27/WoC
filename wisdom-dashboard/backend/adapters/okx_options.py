"""OKX options adapter (live): BTC and ETH.

OKX is the second-largest crypto options venue, with a different (heavily
Asia-based) client base from Deribit's. Three public calls per asset,
confirmed live 2026-09-23:

  - /public/opt-summary     mark IV (`markVol`) and forward (`fwdPx`) per option
  - /public/open-interest   open interest in USD (`oiUsd`) per option
  - /market/tickers         24h volume in coin terms (`volCcy24h`)

Only the coin-margined `BTC-USD` / `ETH-USD` family is used. OKX also lists
a USDT-margined `_UM` twin of nearly every contract; including both would
count one strike twice. Expiries settle at 08:00 UTC.

Venue independence caveat: arbitrage keeps OKX's and Deribit's smiles close
together, so a second options venue mostly adds breadth and redundancy (the
options crowd keeps working if one venue is down), not a new opinion.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from adapters.base import SourceAdapter
from adapters.http import get_json
from adapters.options_common import SmilePoint, build_options_distribution
from common.distribution import SourceFetchResult

OKX_BASE = "https://www.okx.com/api/v5"
FAMILY = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
EXPIRY_HOUR_UTC = 8


def parse_inst_id(inst_id: str) -> tuple[datetime, float] | None:
    """"BTC-USD-261030-90000-C" -> (2026-10-30 08:00 UTC, 90000.0)."""
    parts = inst_id.split("-")
    if len(parts) != 5 or parts[1] != "USD":
        return None
    try:
        day = datetime.strptime(parts[2], "%y%m%d")
        strike = float(parts[3])
    except ValueError:
        return None
    return day.replace(hour=EXPIRY_HOUR_UTC, tzinfo=timezone.utc), strike


def chain_to_smiles(summary: list[dict], oi_rows: list[dict], tickers: list[dict]) -> dict[datetime, tuple[float, list[SmilePoint]]]:
    oi_usd = {r["instId"]: float(r.get("oiUsd") or 0.0) for r in oi_rows}
    vol_ccy = {r["instId"]: float(r.get("volCcy24h") or 0.0) for r in tickers}
    smiles: dict[datetime, list[SmilePoint]] = defaultdict(list)
    forwards: dict[datetime, float] = {}
    for r in summary:
        parsed = parse_inst_id(r.get("instId", ""))
        try:
            iv = float(r.get("markVol") or 0.0)
            fwd = float(r.get("fwdPx") or 0.0)
        except ValueError:
            continue
        if parsed is None or iv <= 0 or fwd <= 0:
            continue
        expiry, strike = parsed
        forwards[expiry] = fwd
        smiles[expiry].append(SmilePoint(
            strike=strike,
            iv=iv,
            open_interest_usd=oi_usd.get(r["instId"], 0.0),
            volume_usd=vol_ccy.get(r["instId"], 0.0) * fwd,
        ))
    return {e: (forwards[e], smiles[e]) for e in smiles}


class OkxOptionsAdapter(SourceAdapter):
    name = "okx_options"
    source_type = "options"

    def fetch(self, asset: str) -> SourceFetchResult:
        family = FAMILY.get(asset)
        if family is None:
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)
        try:
            summary = get_json(f"{OKX_BASE}/public/opt-summary", {"instFamily": family})["data"]
            oi_rows = get_json(f"{OKX_BASE}/public/open-interest", {"instType": "OPTION", "instFamily": family})["data"]
            tickers = get_json(f"{OKX_BASE}/market/tickers", {"instType": "OPTION", "instFamily": family})["data"]
        except Exception as exc:  # noqa: BLE001
            return SourceFetchResult(source_name=self.name, source_type=self.source_type, error=f"fetch failed: {exc}")

        distributions = []
        for expiry, (forward, smile) in chain_to_smiles(summary, oi_rows, tickers).items():
            d = build_options_distribution(
                asset=asset,
                source_name=self.name,
                expiry=expiry,
                forward=forward,
                smile=smile,
                source_url="https://www.okx.com/trade-option-chain",
                venue_note="OKX options (coin-margined family).",
            )
            if d is not None:
                distributions.append(d)
        distributions.sort(key=lambda d: d.target_date)
        return SourceFetchResult(source_name=self.name, source_type=self.source_type, distributions=distributions)
