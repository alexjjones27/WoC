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
COINGECKO_ID = {"BTC": "bitcoin"}


def fetch_recent_history(asset: str, days: int = 30) -> list[dict]:
    """Returns [{"t_ms": epoch_millis, "price": float}, ...], oldest first.
    Empty list (never raises) if the asset isn't mapped, the request fails,
    or the response isn't the shape we expect -- the fan chart just omits
    the historical line rather than breaking the whole dashboard.

    The parsing below is inside the same try as the request on purpose. It
    used to sit outside it, which made this function's "never raises"
    contract untrue for exactly the failure most likely to actually happen:
    not the request erroring (handled) but CoinGecko returning a 200 whose
    body has a different shape (a list instead of an object, a null price,
    an error envelope). That raised straight through the orchestrator's one
    unguarded future and blanked the entire dashboard -- including every
    forecast that had fetched perfectly well."""
    coin_id = COINGECKO_ID.get(asset.upper())
    if not coin_id:
        return []
    points: list[dict] = []
    try:
        data = get_json(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
            {"vs_currency": "usd", "days": days, "interval": "daily"},
        )
        for ts_ms, price in (data or {}).get("prices", []):
            if ts_ms is None or price is None:
                continue
            points.append({"t_ms": int(ts_ms), "price": float(price)})
    except Exception:  # noqa: BLE001 -- best-effort, never breaks the dashboard
        return []
    return points
