"""Expert crowd for oil: the US Energy Information Administration's
Short-Term Energy Outlook (STEO).

The STEO is the EIA analysts' official monthly forecast, published about
the second week of each month, running roughly 15-27 months ahead. It is
about as independent of the market crowds as a forecast gets: a government
model with analyst judgment on top, not a traded price. Series used,
confirmed live 2026-09-23 via the public v2 API:

  WTIPUUS  WTI crude oil spot price, monthly average, $/barrel
  BREPUUS  Brent crude oil spot price, monthly average, $/barrel

Two things to keep in mind when comparing it with the market crowds:
  - These are MONTHLY AVERAGES, not the price on one day. Each month is
    placed on the 15th when plotted.
  - The series mixes history and forecast. Months before the current
    calendar month are treated as actuals (the EIA's own estimates of
    what happened); the current month onward is the forecast.

The EIA's public DEMO_KEY works without registering but is rate-limited
(on the order of tens of calls per hour per IP), so results are cached for
12 hours -- the forecast only changes monthly anyway. Set EIA_API_KEY to a
free registered key (https://www.eia.gov/opendata/register.php) to lift
the limit.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from adapters.http import get_json
from cache import TTLCache

EIA_STEO_URL = "https://api.eia.gov/v2/steo/data/"
SERIES = {"WTIPUUS": "WTI", "BREPUUS": "Brent"}
HISTORY_MONTHS = 6

_cache = TTLCache(ttl_seconds=12 * 3600)


def parse_steo(rows: list[dict], today: datetime) -> dict:
    current_month = today.strftime("%Y-%m")
    out: dict[str, list[dict]] = {name: [] for name in SERIES.values()}
    for r in rows:
        name = SERIES.get(r.get("seriesId"))
        try:
            value = float(r["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if name is None:
            continue
        period = r["period"]
        out[name].append({
            "period": period,
            "date": f"{period}-15",
            "price": value,
            "is_forecast": period >= current_month,
        })
    for name in out:
        pts = sorted(out[name], key=lambda p: p["period"])
        history = [p for p in pts if not p["is_forecast"]][-HISTORY_MONTHS:]
        out[name] = history + [p for p in pts if p["is_forecast"]]
    return out


def fetch_eia_oil_forecast() -> dict:
    cached = _cache.get("steo")
    if cached is not None:
        return cached
    params = {
        "api_key": os.environ.get("EIA_API_KEY", "DEMO_KEY"),
        "frequency": "monthly",
        "data[0]": "value",
        "facets[seriesId][]": list(SERIES),
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 120,
    }
    try:
        data = get_json(EIA_STEO_URL, params)
        series = parse_steo(data["response"]["data"], datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001
        stale = _cache.get_stale("steo")
        if stale is not None:
            return {**stale[0], "stale": True}
        return {"error": f"EIA STEO fetch failed: {exc}"}
    result = {
        "source": "US EIA Short-Term Energy Outlook",
        "source_url": "https://www.eia.gov/outlooks/steo/",
        "note": "Monthly-average price forecast by EIA analysts; months before the current one are EIA estimates of actuals.",
        "series": series,
    }
    _cache.set("steo", result)
    return result
