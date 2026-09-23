"""The Crowds page: every independent crowd this dashboard can read for one
asset, side by side.

  Crowd                What it says                         Source module
  -------------------  -----------------------------------  ---------------------------
  Prediction markets   full price distribution per date     orchestrator (Polymarket,
                                                            Kalshi, Manifold, Futuur)
  Options              risk-neutral distribution per expiry orchestrator (Deribit, OKX,
                                                            Derive)
  Futures & perps      forward price per expiry; funding    signals/futures.py
  Experts              monthly price forecast (oil only)    signals/eia.py
  Positioning          what large traders hold              signals/cot.py
  Retail sentiment     mood and attention                   signals/sentiment.py,
                                                            src/wikipedia_attention.py

`horizon_views` lines the price-forecasting crowds up at fixed horizons
(1 week, 1 month, 3 months, 1 year) so they can be compared directly. Each
crowd contributes only where it has a real data point near that horizon --
nothing is extrapolated past a crowd's own furthest date:
  - prediction markets / options: the forecast date closest to the horizon,
    if within HORIZON_TOLERANCE of it
  - futures: the forward curve interpolated to the exact horizon (between
    listed expiries only)
  - EIA: the monthly average for the month containing the horizon date
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import spot_price
from cache import TTLCache
from orchestrator import get_dashboard_payload, get_options_forecasts
from signals.cot import fetch_positioning
from signals.eia import fetch_eia_oil_forecast
from signals.futures import fetch_futures_crowd, forward_at
from signals.sentiment import fetch_sentiment

HORIZONS_DAYS = [7, 30, 90, 365]
HORIZON_TOLERANCE = 0.35  # a forecast within +/-35% of the horizon's length counts
CROWD_ASSETS = ("BTC", "ETH", "GOLD", "OIL")
WIKI_QUERY = {"BTC": "Bitcoin", "ETH": "Ethereum", "GOLD": "Gold as an investment", "OIL": "Price of oil"}

_cache = TTLCache(ttl_seconds=60.0)


def _pm_summary(payload: dict) -> list[dict]:
    out = []
    for f in payload.get("forecasts", []):
        out.append({
            "target_date": f["target_date"],
            "period_label": f["period_label"],
            "lead_hours": f["lead_hours"],
            "mean": f["mean"],
            "median": f["median"],
            "ci_68": f["ci_68"],
            "ci_95": f["ci_95"],
            "total_volume": f["total_volume"],
            "confidence_tier": f["confidence_tier"],
            "sources": [s["source_name"] for s in f["sources"]],
        })
    return out


def _nearest(points: list[dict], target_days: float) -> dict | None:
    best = None
    for p in points:
        days = p["lead_hours"] / 24.0
        if days <= 0 or abs(days - target_days) > target_days * HORIZON_TOLERANCE:
            continue
        if best is None or abs(days - target_days) < abs(best["lead_hours"] / 24.0 - target_days):
            best = p
    return best


def build_horizon_views(
    spot: float | None,
    pm: list[dict],
    options: list[dict],
    curve: list[dict],
    eia_wti: list[dict] | None,
    now: datetime,
) -> list[dict]:
    views = []
    for days in HORIZONS_DAYS:
        when = now + timedelta(days=days)
        row: dict = {"horizon_days": days, "date": when.date().isoformat(), "crowds": {}}
        for key, points in (("prediction_markets", pm), ("options", options)):
            p = _nearest(points, days)
            if p:
                row["crowds"][key] = {"median": p["median"], "ci_68": p["ci_68"], "date": p["target_date"]}
        if spot and curve:
            fwd = forward_at(curve, spot, when, now)
            if fwd:
                row["crowds"]["futures"] = {"median": fwd, "ci_68": None, "date": when.date().isoformat()}
        if eia_wti:
            month = when.strftime("%Y-%m")
            match = next((p for p in eia_wti if p["period"] == month and p["is_forecast"]), None)
            if match:
                row["crowds"]["experts"] = {"median": match["price"], "ci_68": None, "date": match["date"]}
        if spot:
            for c in row["crowds"].values():
                c["change_vs_spot"] = c["median"] / spot - 1
        views.append(row)
    return views


def _wikipedia(asset: str) -> dict | None:
    try:
        from wikipedia_attention import get_attention_for_company  # src/, on sys.path via app.py

        return get_attention_for_company(WIKI_QUERY[asset])
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Wikipedia fetch failed: {exc}"}


def build_crowds_payload(asset: str) -> dict:
    asset = asset.upper()
    if asset not in CROWD_ASSETS:
        return {"asset": asset, "error": f"asset must be one of {', '.join(CROWD_ASSETS)}"}
    cached = _cache.get(asset)
    if cached is not None:
        return cached

    now = datetime.now(timezone.utc)
    spot = spot_price.fetch_spot(asset)
    with ThreadPoolExecutor(max_workers=7) as pool:
        f_pm = pool.submit(get_dashboard_payload, asset)
        f_opt = pool.submit(get_options_forecasts, asset)
        f_fut = pool.submit(fetch_futures_crowd, asset, spot)
        f_eia = pool.submit(fetch_eia_oil_forecast) if asset == "OIL" else None
        f_cot = pool.submit(fetch_positioning, asset)
        f_sent = pool.submit(fetch_sentiment, asset)
        f_wiki = pool.submit(_wikipedia, asset)

        pm_payload = f_pm.result()
        options = f_opt.result()
        futures = f_fut.result()
        eia = f_eia.result() if f_eia else None
        positioning = f_cot.result()
        sentiment = f_sent.result()
        sentiment["wikipedia"] = f_wiki.result()

    pm = _pm_summary(pm_payload)
    eia_wti = (eia or {}).get("series", {}).get("WTI") if eia and "error" not in eia else None
    payload = {
        "asset": asset,
        "display_name": pm_payload.get("display_name", asset),
        "generated_at_utc": now.isoformat(),
        "spot": spot,
        "spot_source": "Hyperliquid mid price" + (" (front-month WTI perp)" if asset == "OIL" else ""),
        "prediction_markets": {
            "forecasts": pm,
            "sources_queried": [s["name"] for s in pm_payload.get("sources_queried", [])],
            "source_errors": pm_payload.get("source_errors", []),
        },
        "options": options,
        "futures": futures,
        "experts": eia,
        "positioning": positioning,
        "sentiment": sentiment,
        "horizon_views": build_horizon_views(spot, pm, options.get("forecasts", []), futures.get("curve", []), eia_wti, now),
    }
    _cache.set(asset, payload)
    return payload
