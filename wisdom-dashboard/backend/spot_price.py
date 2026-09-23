"""Recent spot price history, for the fan chart's "actuals" line leading up
to "now". Deliberately not part of the adapters/ package -- this isn't a
forecast source, it's the ground-truth series the forecasts sit next to.
CoinGecko's public market_chart endpoint needs no auth (confirmed live,
2026-09-13): returns [ms_timestamp, price] pairs.
"""
from __future__ import annotations

from adapters.http import get_json, post_json

HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"

# Hyperliquid mid price per asset: (dex, coin). The empty dex is its main
# crypto perp venue; "xyz" is the HIP-3 venue that lists commodity perps.
# Confirmed live 2026-09-23: xyz:CL tracks the front WTI contract and
# xyz:GOLD tracks spot gold to within a few dollars. Used wherever a module
# needs "roughly where is the price right now" (e.g. deciding whether a
# bare "55,000" touch strike is a dip or a reach), never as a forecast.
HYPERLIQUID_SPOT = {"BTC": ("", "BTC"), "ETH": ("", "ETH"), "GOLD": ("xyz", "xyz:GOLD"), "OIL": ("xyz", "xyz:CL")}

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# CoinGecko symbol per asset. Only BTC is wired to an adapter today, but
# this stays a lookup (not a hardcoded URL) so a second asset's spot line is
# "add a row here", same shape as common/assets.py.
COINGECKO_ID = {"BTC": "bitcoin", "ETH": "ethereum"}


def fetch_recent_history(asset: str, days: int = 30) -> list[dict]:
    """Returns [{"t": iso8601, "price": float}, ...], oldest first. Empty
    list (never raises) if the asset isn't mapped or the request fails --
    the fan chart just omits the historical line rather than breaking the
    whole dashboard."""
    coin_id = COINGECKO_ID.get(asset.upper())
    if not coin_id:
        return []
    try:
        data = get_json(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": days, "interval": "daily"},
        )
    except Exception:  # noqa: BLE001 -- best-effort, never breaks the dashboard
        return []
    points = []
    for ts_ms, price in data.get("prices", []):
        points.append({"t_ms": int(ts_ms), "price": float(price)})
    return points


def fetch_spot(asset: str) -> float | None:
    """Current mid price from Hyperliquid, or None (never raises)."""
    spec = HYPERLIQUID_SPOT.get(asset.upper())
    if spec is None:
        return None
    dex, coin = spec
    try:
        body = {"type": "allMids", "dex": dex} if dex else {"type": "allMids"}
        mids = post_json(HYPERLIQUID_INFO, body)
        return float(mids[coin])
    except Exception:  # noqa: BLE001
        return None
