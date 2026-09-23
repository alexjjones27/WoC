"""Limitless Exchange adapter (live, touch-probability only).

Limitless is an on-chain (Base, USDC) prediction market. Its asset-price
products, confirmed live 2026-09-23, are all touch ladders: grouped markets
titled "What price will Bitcoin hit September 21-27?" / "What will Gold
(XAUUSD) hit Week of September 21 2026?", whose sub-markets are labelled
"↑ 88,000" (reach) or "↓ 68,000" (dip), each with prices [yes, no]. It has
no point-in-time range markets for these assets, so fetch() contributes
nothing to the aggregate; everything goes through fetch_touch().

INDEPENDENCE CAVEAT: Limitless's own market metadata flags these groups
`isPolyArbitrage: true` -- they're kept in line with Polymarket's matching
ladders by arbitrage traders. Treat agreement between Limitless and
Polymarket as expected, not as confirmation from a separate crowd.
Volumes are also tiny (tens to low hundreds of USDC per group on the day
this was built).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from adapters.base import SourceAdapter
from adapters.http import get_json
from common.distribution import SourceFetchResult, TouchFetchResult, TouchForecast, TouchThreshold

LIMITLESS_BASE = "https://api.limitless.exchange"

ASSET_CONFIG = {
    "BTC": {"search": "bitcoin", "title_re": re.compile(r"what (?:price )?will bitcoin hit", re.I)},
    "ETH": {"search": "ethereum", "title_re": re.compile(r"what (?:price )?will ethereum hit", re.I)},
    "GOLD": {"search": "gold", "title_re": re.compile(r"what (?:price )?will gold .*hit", re.I)},
    "OIL": {"search": "oil", "title_re": re.compile(r"what (?:price )?will wti .*hit", re.I)},
}

ARROW_RE = re.compile(r"^\s*([↑↓])\s*\$?([\d,]+(?:\.\d+)?)\s*$")


def parse_arrow_label(label: str) -> tuple[str, float] | None:
    m = ARROW_RE.match(label or "")
    if not m:
        return None
    return ("above" if m.group(1) == "↑" else "below", float(m.group(2).replace(",", "")))


class LimitlessAdapter(SourceAdapter):
    name = "limitless"
    source_type = "prediction_market"

    def fetch(self, asset: str) -> SourceFetchResult:
        return SourceFetchResult(source_name=self.name, source_type=self.source_type)

    def fetch_touch(self, asset: str) -> TouchFetchResult:
        cfg = ASSET_CONFIG.get(asset)
        if cfg is None:
            return TouchFetchResult(source_name=self.name)
        try:
            data = get_json(f"{LIMITLESS_BASE}/markets/search", {"query": cfg["search"], "limit": 25})
        except Exception as exc:  # noqa: BLE001
            return TouchFetchResult(source_name=self.name, error=f"search failed: {exc}")
        fetched_at = datetime.now(timezone.utc).isoformat()
        touches = []
        for group in data.get("markets", []):
            if group.get("expired") or not cfg["title_re"].search(group.get("title", "")):
                continue
            t = self.group_to_touch(asset, group)
            if t is not None:
                t.fetched_at_utc = fetched_at
                touches.append(t)
        return TouchFetchResult(source_name=self.name, touches=touches)

    def group_to_touch(self, asset: str, group: dict) -> TouchForecast | None:
        ts = group.get("expirationTimestamp")
        if not ts:
            return None
        end = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        thresholds = []
        for sub in group.get("markets") or []:
            parsed = parse_arrow_label(sub.get("title", ""))
            prices = sub.get("prices") or []
            if parsed is None or not prices:
                continue
            thresholds.append(TouchThreshold(
                direction=parsed[0],
                price=parsed[1],
                prob_touch=float(prices[0]),
                volume=float(sub.get("volumeFormatted") or 0.0),
                label=sub["title"],
            ))
        if not thresholds:
            return None
        thresholds.sort(key=lambda t: (t.direction, t.price))
        return TouchForecast(
            asset=asset,
            source_name=self.name,
            expiry_date=end.date(),
            period_label=end.strftime("%b %-d, %Y"),
            thresholds=thresholds,
            total_volume=float(group.get("volumeFormatted") or 0.0),
            source_url=f"https://limitless.exchange/markets/{group.get('slug', '')}",
            raw_note=(
                "Touch probability. Limitless flags these markets as arbitraged against Polymarket, "
                "so agreement with Polymarket is expected rather than independent confirmation."
            ),
            resolve_datetime_utc=end.isoformat(),
        )
