"""Recent spot price history, for the fan chart's "actuals" line leading up
to "now". Deliberately not part of the adapters/ package -- this isn't a
forecast source, it's the ground-truth series the forecasts sit next to.
CoinGecko's public market_chart endpoint needs no auth (confirmed live,
2026-09-13): returns [ms_timestamp, price] pairs.
"""
from __future__ import annotations

from adapters.http import get_json

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
