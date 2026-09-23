"""Asset registry: which adapters are applicable to which asset.

This is the piece that lets the dashboard grow from "just BTC" to "BTC,
AAPL, gold, ..." without touching the aggregator or the adapters themselves
-- you add a row here (and, for a brand new asset class, an adapter that
knows how to map that asset's ticker to the source's native symbol).

Only assets with `enabled=True` are exposed by the API / selectable in the
dashboard. AAPL is listed disabled so the registry itself documents the
intended shape of "add a new asset" without pretending its adapters exist.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AssetSpec:
    symbol: str  # canonical ticker used throughout this app, e.g. "BTC"
    display_name: str
    # Adapter names (see adapters/registry.py) applicable to this asset.
    # An adapter with nothing for this asset right now simply contributes
    # zero distributions -- see adapters/base.py.
    adapters: list[str]
    enabled: bool = True


ASSET_REGISTRY: dict[str, AssetSpec] = {
    "BTC": AssetSpec(
        symbol="BTC",
        display_name="Bitcoin",
        adapters=["polymarket", "kalshi", "manifold", "futuur", "limitless", "deribit_options", "okx_options", "derive_options"],
        enabled=True,
    ),
    "OIL": AssetSpec(
        symbol="OIL",
        display_name="Crude Oil (WTI)",
        # No free options chain for WTI itself (the old USO-ETF route is
        # too illiquid, see src/btc_oil_wisdom_combination.py).
        adapters=["polymarket", "kalshi", "manifold", "futuur", "limitless"],
        enabled=True,
    ),
    "ETH": AssetSpec(
        symbol="ETH",
        display_name="Ethereum",
        adapters=["kalshi", "manifold", "futuur", "limitless", "deribit_options", "okx_options", "derive_options"],
        enabled=True,
    ),
    # Kalshi runs real gold ladders (KXGOLDD/KXGOLDW), same shape as
    # BTC/oil's. No crypto options venue lists gold.
    "GOLD": AssetSpec(
        symbol="GOLD",
        display_name="Gold",
        adapters=["kalshi", "manifold", "futuur", "limitless"],
        enabled=True,
    ),
    # Not wired up yet -- listed to show how a new asset is added once its
    # adapters exist. Equities would need a listed-options adapter (the
    # crypto options venues don't list stocks); no
    # prediction-market adapter covers individual stocks (see
    # src/sec_13f_wisdom.py's module docstring for why -- likely
    # regulatory).
    "AAPL": AssetSpec(
        symbol="AAPL",
        display_name="Apple Inc.",
        adapters=[],
        enabled=False,
    ),
}


def get_enabled_assets() -> list[AssetSpec]:
    return [a for a in ASSET_REGISTRY.values() if a.enabled]


def get_asset(symbol: str) -> AssetSpec | None:
    return ASSET_REGISTRY.get(symbol.upper())
