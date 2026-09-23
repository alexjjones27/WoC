"""Ties the pieces together for one API request: look up which adapters
apply to the requested asset (common/assets.py), run them concurrently
(each isolated so one failing source can't take down the others), hand
their output to the aggregation engine, and serialize the result to plain
JSON-safe dicts.

Two entry points, one per distribution crowd:
  - get_dashboard_payload: the prediction-market forecast (the Prediction
    Markets page), including touch ladders and gap-fill.
  - get_options_forecasts: the options crowd, one aggregate per expiry,
    in a compact form for the Crowds page.
"""
from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import spot_price
from adapters.registry import get_adapter
from aggregation import build_dashboard, build_gap_fill_forecasts, build_touch_groups
from cache import TTLCache
from common.assets import get_asset
from common.distribution import SourceFetchResult, TouchFetchResult

CACHE_TTL_SECONDS = 45.0
_cache = TTLCache(ttl_seconds=CACHE_TTL_SECONDS)

# How far back the fan chart's historical ("actuals") line goes.
SPOT_HISTORY_DAYS = 30


def _run_adapter(name: str, symbol: str) -> SourceFetchResult:
    adapter = get_adapter(name)
    if adapter is None:
        return SourceFetchResult(source_name=name, source_type="unknown", error="adapter not registered")
    try:
        return adapter.fetch(symbol)
    except Exception as exc:  # noqa: BLE001 - belt-and-suspenders; adapters should already catch internally
        return SourceFetchResult(source_name=name, source_type=getattr(adapter, "source_type", "unknown"), error=str(exc))


def _run_adapter_touch(name: str, symbol: str) -> TouchFetchResult:
    adapter = get_adapter(name)
    if adapter is None:
        return TouchFetchResult(source_name=name, error="adapter not registered")
    try:
        return adapter.fetch_touch(symbol)
    except Exception as exc:  # noqa: BLE001
        return TouchFetchResult(source_name=name, error=str(exc))


def _adapters_of_type(asset, source_type: str) -> list[str]:
    return [n for n in asset.adapters if getattr(get_adapter(n), "source_type", None) == source_type]


def get_dashboard_payload(symbol: str, force_refresh: bool = False) -> dict:
    asset = get_asset(symbol)
    if asset is None or not asset.enabled:
        return {"error": f"unknown or disabled asset: {symbol}", "asset": symbol}

    cache_key = asset.symbol
    if not force_refresh:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

    pm_adapters = _adapters_of_type(asset, "prediction_market")
    results: list[SourceFetchResult] = []
    touch_results: list[TouchFetchResult] = []
    spot_history: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(len(pm_adapters) * 2 + 1, 1)) as pool:
        futures = {pool.submit(_run_adapter, name, asset.symbol): ("point", name) for name in pm_adapters}
        futures.update({pool.submit(_run_adapter_touch, name, asset.symbol): ("touch", name) for name in pm_adapters})
        spot_future = pool.submit(spot_price.fetch_recent_history, asset.symbol, SPOT_HISTORY_DAYS)
        for fut in as_completed(futures):
            kind, _name = futures[fut]
            (results if kind == "point" else touch_results).append(fut.result())
        spot_history = spot_future.result()

    forecasts, source_errors = build_dashboard(results)
    touch_groups, touch_errors = build_touch_groups(touch_results)
    gap_fill_forecasts = build_gap_fill_forecasts(forecasts)

    payload = {
        "asset": asset.symbol,
        "display_name": asset.display_name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "stale": False,
        "forecasts": [_forecast_to_dict(f) for f in forecasts],
        "source_errors": source_errors,
        "touch_forecasts": [_touch_group_to_dict(g) for g in touch_groups],
        "touch_errors": touch_errors,
        "gap_fill_forecasts": [dataclasses.asdict(g) for g in gap_fill_forecasts],
        "spot_history": spot_history,
        "sources_queried": [
            {"name": name, "source_type": getattr(get_adapter(name), "source_type", "unknown")}
            for name in pm_adapters
        ],
    }

    if forecasts or touch_groups:
        _cache.set(cache_key, payload)
        return payload

    # Nothing usable this run (e.g. every source down/rate-limited) -- serve
    # the last good payload if we have one, rather than an empty dashboard.
    stale_entry = _cache.get_stale(cache_key)
    if stale_entry is not None:
        stale_payload, stale_ts = stale_entry
        return {
            **stale_payload,
            "stale": True,
            "stale_as_of_utc": datetime.fromtimestamp(stale_ts, tz=timezone.utc).isoformat(),
            "source_errors": source_errors or stale_payload.get("source_errors", []),
        }
    return payload


def _forecast_to_dict(forecast) -> dict:
    d = dataclasses.asdict(forecast)
    d["ci_68"] = list(d["ci_68"])
    d["ci_95"] = list(d["ci_95"])
    return d


def _touch_group_to_dict(group) -> dict:
    return dataclasses.asdict(group)


_options_cache = TTLCache(ttl_seconds=CACHE_TTL_SECONDS)


def get_options_forecasts(symbol: str) -> dict:
    """The options crowd for one asset: every options adapter's
    distributions, aggregated per expiry (open-interest weighted, no
    prediction-market calibration corrections), summarized to the numbers
    the Crowds page draws. Empty `forecasts` for assets no options venue
    lists."""
    asset = get_asset(symbol)
    if asset is None or not asset.enabled:
        return {"forecasts": [], "source_errors": [], "sources_queried": []}
    cached = _options_cache.get(asset.symbol)
    if cached is not None:
        return cached
    names = _adapters_of_type(asset, "options")
    results: list[SourceFetchResult] = []
    if names:
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            results = list(pool.map(lambda n: _run_adapter(n, asset.symbol), names))
    forecasts, errors = build_dashboard(results, source_type="options")
    payload = {
        "forecasts": [summarize_forecast(f) for f in forecasts],
        "source_errors": errors,
        "sources_queried": names,
    }
    if forecasts or not names:
        _options_cache.set(asset.symbol, payload)
    return payload


def _pdf_median(grid_edges: list[float], pdf: list[float]) -> float | None:
    total = 0.0
    for i, p in enumerate(pdf):
        if total + p >= 0.5 and p > 0:
            frac = (0.5 - total) / p
            return grid_edges[i] + frac * (grid_edges[i + 1] - grid_edges[i])
        total += p
    return None


def summarize_forecast(f) -> dict:
    """Compact form of an AggregateForecast: the headline numbers plus each
    source's own median, without the per-source PDFs (which are only
    needed by the Prediction Markets page's detail charts)."""
    return {
        "target_date": f.target_date,
        "period_label": f.period_label,
        "lead_hours": f.lead_hours,
        "mean": f.mean,
        "median": f.median,
        "ci_68": list(f.ci_68),
        "ci_95": list(f.ci_95),
        "total_volume": f.total_volume,
        "total_open_interest": sum(s.open_interest or 0.0 for s in f.sources),
        "disagreement_pct": f.disagreement_pct,
        "sources": [
            {
                "source_name": s.source_name,
                "mean": s.mean,
                "median": _pdf_median(f.grid_edges, s.pdf),
                "volume": s.volume,
                "open_interest": s.open_interest,
                "source_url": s.source_url,
            }
            for s in f.sources
        ],
    }
