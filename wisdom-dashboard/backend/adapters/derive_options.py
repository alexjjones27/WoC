"""Derive (formerly Lyra) options adapter (live): BTC and ETH.

Derive is an on-chain options exchange: its own rollup, self-custodied
margin accounts and a DeFi-native trader base, which makes it the options
venue here with the least overlap with Deribit/OKX's clients (arbitrage
still ties the prices together). Public JSON-RPC API, no key, confirmed
live 2026-09-23:

  - public/get_instruments  every live option, with its expiry timestamp
  - public/get_tickers      per expiry: mark IV (`option_pricing.i`),
                            forward (`option_pricing.f`) and open interest
                            in coins (`stats.oi`)

OI is much smaller than Deribit's (hundreds of BTC per expiry, not tens of
thousands), which the open-interest weight already reflects.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from adapters.base import SourceAdapter
from adapters.http import post_json
from adapters.options_common import SmilePoint, build_options_distribution
from common.distribution import SourceFetchResult

DERIVE_BASE = "https://api.lyra.finance/public"
CURRENCY = {"BTC": "BTC", "ETH": "ETH"}


def expiries_from_instruments(instruments: list[dict]) -> dict[str, datetime]:
    """{"20261030": expiry datetime} for every active option expiry."""
    out: dict[str, datetime] = {}
    for inst in instruments:
        if not inst.get("is_active"):
            continue
        parts = inst.get("instrument_name", "").split("-")
        ts = (inst.get("option_details") or {}).get("expiry")
        if len(parts) == 4 and ts:
            out[parts[1]] = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return out


def tickers_to_smile(tickers: dict[str, dict]) -> tuple[float, list[SmilePoint]] | None:
    forward = 0.0
    smile: list[SmilePoint] = []
    for name, t in tickers.items():
        parts = name.split("-")
        pricing = t.get("option_pricing") or {}
        stats = t.get("stats") or {}
        try:
            strike = float(parts[2])
            iv = float(pricing.get("i") or 0.0)
            fwd = float(pricing.get("f") or 0.0)
        except (IndexError, ValueError):
            continue
        if iv <= 0 or fwd <= 0:
            continue
        forward = fwd
        smile.append(SmilePoint(
            strike=strike,
            iv=iv,
            open_interest_usd=float(stats.get("oi") or 0.0) * fwd,
            volume_usd=float(stats.get("v") or 0.0) * fwd,
        ))
    return (forward, smile) if smile else None


class DeriveOptionsAdapter(SourceAdapter):
    name = "derive_options"
    source_type = "options"

    def fetch(self, asset: str) -> SourceFetchResult:
        currency = CURRENCY.get(asset)
        if currency is None:
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)
        try:
            instruments = post_json(
                f"{DERIVE_BASE}/get_instruments",
                {"currency": currency, "instrument_type": "option", "expired": False},
            )["result"]
        except Exception as exc:  # noqa: BLE001
            return SourceFetchResult(source_name=self.name, source_type=self.source_type, error=f"instrument list failed: {exc}")

        expiries = expiries_from_instruments(instruments)

        def one(code: str):
            res = post_json(
                f"{DERIVE_BASE}/get_tickers",
                {"instrument_type": "option", "currency": currency, "expiry_date": code},
            )
            return code, res["result"]["tickers"]

        distributions = []
        errors = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(one, code) for code in expiries]
            for fut in futures:
                try:
                    code, tickers = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
                    continue
                parsed = tickers_to_smile(tickers)
                if parsed is None:
                    continue
                d = build_options_distribution(
                    asset=asset,
                    source_name=self.name,
                    expiry=expiries[code],
                    forward=parsed[0],
                    smile=parsed[1],
                    source_url=f"https://www.derive.xyz/options/{currency.lower()}",
                    venue_note="Derive (on-chain options exchange).",
                )
                if d is not None:
                    distributions.append(d)
        distributions.sort(key=lambda d: d.target_date)
        return SourceFetchResult(
            source_name=self.name,
            source_type=self.source_type,
            distributions=distributions,
            error="; ".join(errors) if errors and not distributions else None,
        )
