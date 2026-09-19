"""Ties the pieces together for one API request: look up which adapters
apply to the requested asset (common/assets.py), run them concurrently
(each isolated so one failing source can't take down the others), hand
their output to the aggregation engine, and serialize the result to plain
JSON-safe dicts. This is the only module app.py (the FastAPI layer) needs
to import for actual dashboard data.
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


def get_dashboard_payload(symbol: str, force_refresh: bool = False) -> dict:
    asset = get_asset(symbol)
    if asset is None or not asset.enabled:
        return {"error": f"unknown or disabled asset: {symbol}", "asset": symbol}

    cache_key = asset.symbol
    if not force_refresh:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

    results: list[SourceFetchResult] = []
    touch_results: list[TouchFetchResult] = []
    spot_history: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(len(asset.adapters) * 2 + 1, 1)) as pool:
        futures = {pool.submit(_run_adapter, name, asset.symbol): ("point", name) for name in asset.adapters}
        futures.update({pool.submit(_run_adapter_touch, name, asset.symbol): ("touch", name) for name in asset.adapters})
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
            for name in asset.adapters
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
