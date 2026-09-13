"""Asset registry: which adapters are applicable to which asset.

This is the piece that lets the dashboard grow from "just BTC" to "BTC,
AAPL, gold, ..." without touching the aggregator or the adapters themselves
-- you add a row here (and, for a brand new asset class, an adapter that
knows how to map that asset's ticker to the source's native symbol).

Only assets with `enabled=True` are exposed by the API / selectable in the
dashboard. BTC is the only one wired up end-to-end today (Phase 1); the
others are listed disabled so the registry itself documents the intended
shape of "add a new asset" without pretending those adapters exist yet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AssetSpec:
    symbol: str  # canonical ticker used throughout this app, e.g. "BTC"
    display_name: str
    # Adapter names (see adapters/registry.py) applicable to this asset.
    # An adapter listed here but not live (Phase 2/3 stubs) simply
    # contributes zero distributions -- see adapters/base.py.
    adapters: list[str]
    enabled: bool = True


ASSET_REGISTRY: dict[str, AssetSpec] = {
    "BTC": AssetSpec(
        symbol="BTC",
        display_name="Bitcoin",
        adapters=["polymarket", "kalshi", "manifold", "deribit_options", "perp_futures"],
        enabled=True,
    ),
    "OIL": AssetSpec(
        symbol="OIL",
        display_name="Crude Oil (WTI)",
        adapters=["polymarket", "kalshi", "manifold", "deribit_options", "perp_futures"],
        enabled=True,
    ),
    # Not wired up yet -- listed to show how a new asset is added once its
    # adapters exist. Equities/commodities would mostly reuse the Phase 2/3
    # options and futures adapters (different underlying symbol mapping);
    # they'd need no prediction-market adapter unless one covers them.
    "AAPL": AssetSpec(
        symbol="AAPL",
        display_name="Apple Inc.",
        adapters=["deribit_options", "perp_futures"],
        enabled=False,
    ),
    "GOLD": AssetSpec(
        symbol="GOLD",
        display_name="Gold",
        adapters=["perp_futures"],
        enabled=False,
    ),
}


def get_enabled_assets() -> list[AssetSpec]:
    return [a for a in ASSET_REGISTRY.values() if a.enabled]


def get_asset(symbol: str) -> AssetSpec | None:
    return ASSET_REGISTRY.get(symbol.upper())
