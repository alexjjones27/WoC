"""Maps adapter names (as referenced in common/assets.py's AssetSpec.adapters)
to instances. Adding a new source: write the adapter module, import it here,
add one line below. Nothing else in the system needs to change.
"""
from __future__ import annotations

from adapters.base import SourceAdapter
from adapters.deribit_options import DeribitOptionsAdapter
from adapters.kalshi import KalshiAdapter
from adapters.manifold import ManifoldAdapter
from adapters.perp_futures import PerpFuturesAdapter
from adapters.polymarket import PolymarketAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    a.name: a
    for a in [
        PolymarketAdapter(),
        KalshiAdapter(),
        ManifoldAdapter(),
        DeribitOptionsAdapter(),
        PerpFuturesAdapter(),
    ]
}


def get_adapter(name: str) -> SourceAdapter | None:
    return ADAPTERS.get(name)
