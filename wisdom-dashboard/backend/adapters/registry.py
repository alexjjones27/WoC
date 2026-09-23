"""Maps adapter names (as referenced in common/assets.py's AssetSpec.adapters)
to instances. Adding a new source: write the adapter module, import it here,
add one line below. Nothing else in the system needs to change.

Futures/perps, the EIA forecast, CFTC positioning and sentiment are not
adapters: they don't produce price distributions. They live in signals/.
"""
from __future__ import annotations

from adapters.base import SourceAdapter
from adapters.deribit_options import DeribitOptionsAdapter
from adapters.derive_options import DeriveOptionsAdapter
from adapters.futuur import FutuurAdapter
from adapters.kalshi import KalshiAdapter
from adapters.limitless import LimitlessAdapter
from adapters.manifold import ManifoldAdapter
from adapters.okx_options import OkxOptionsAdapter
from adapters.polymarket import PolymarketAdapter

ADAPTERS: dict[str, SourceAdapter] = {
    a.name: a
    for a in [
        PolymarketAdapter(),
        KalshiAdapter(),
        ManifoldAdapter(),
        FutuurAdapter(),
        LimitlessAdapter(),
        DeribitOptionsAdapter(),
        OkxOptionsAdapter(),
        DeriveOptionsAdapter(),
    ]
}


def get_adapter(name: str) -> SourceAdapter | None:
    return ADAPTERS.get(name)
