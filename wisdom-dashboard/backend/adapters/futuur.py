"""Futuur adapter (live, real-money USDC side only).

Futuur runs both a play-money (OOM) and a real-money (USDC) book on the same
markets, each with its own price. Only the USDC price and
`volume_real_money` are used here -- the OOM book is the same free-token
problem Manifold has, and on these markets it often disagrees wildly with
the real-money book (confirmed live 2026-09-23: "Reach $87,500" was 0.21 in
OOM vs 0.78 in USDC), so mixing the two would just add noise.

Two market shapes are used, confirmed live 2026-09-23 for all four assets:

  1. POINT-IN-TIME range markets ("What will be the price of Bitcoin at the
     end of 2026?", markets_correlation="linked"): mutually exclusive
     buckets labelled "Below $ 60,000", "Between $ 75,000 and $ 89,999.99",
     "$ 105,000 or higher". Same partition semantics as Polymarket's range
     events, so each becomes a PriceDistribution. Only BTC currently has
     one.
  2. TOUCH ladders ("What price will Gold (XAUUSD) hit in September 2026?",
     "Which price will Bitcoin hit in 2026?"): independent "Reach $X" /
     "Dip to $X" / "Hit $X" outcomes, or bare numbers ("55,000") whose
     direction is only knowable relative to the current price. These go to
     fetch_touch(), never into the point-in-time aggregate.

Volumes are small (hundreds to ~$30k per market), so Futuur mostly adds a
genuinely separate trader base rather than weight -- the volume-weighted
mixture keeps it from moving the aggregate much.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import spot_price
from adapters.base import SourceAdapter
from adapters.http import get_json
from common.distribution import (
    PriceBucket,
    PriceDistribution,
    SourceFetchResult,
    TouchFetchResult,
    TouchForecast,
    TouchThreshold,
)

FUTUUR_BASE = "https://api.futuur.com/api/v1"
CURRENCY = "USDC"

# Per asset: the search term, and a title regex that confirms a result is
# about this asset's price (the search is full-text, so "gold" also returns
# "Vegas Golden Knights" and "oil" returns "Edmonton Oilers").
ASSET_CONFIG = {
    "BTC": {"search": "bitcoin", "title_re": re.compile(r"\bbitcoin\b", re.I)},
    "ETH": {"search": "ethereum", "title_re": re.compile(r"\bethereum\b", re.I)},
    "GOLD": {"search": "gold", "title_re": re.compile(r"\bgold \((?:xauusd|gc)\)", re.I)},
    "OIL": {"search": "oil", "title_re": re.compile(r"\bwti\b", re.I)},
}

POINT_TITLE_RE = re.compile(r"what will be the price of .+ at the end of", re.I)
TOUCH_TITLE_RE = re.compile(r"(?:what|which) (?:price )?will .+ hit\b", re.I)

_NUM = r"\$?\s*([\d,]+(?:\.\d+)?)"
BELOW_RE = re.compile(rf"^below\s*{_NUM}$", re.I)
BETWEEN_RE = re.compile(rf"^between\s*{_NUM}\s*and\s*{_NUM}$", re.I)
ABOVE_RE = re.compile(rf"^{_NUM}\s*or (?:higher|more|above)$", re.I)
TOUCH_UP_RE = re.compile(rf"^(?:reach|hit|above)\s*{_NUM}$", re.I)
TOUCH_DOWN_RE = re.compile(rf"^(?:dip to|fall to|below)\s*{_NUM}$", re.I)
BARE_RE = re.compile(rf"^{_NUM}$")


def _num(s: str) -> float:
    return float(s.replace(",", "").strip())


def parse_range_label(label: str) -> tuple[float | None, float | None] | None:
    s = label.strip()
    if m := BELOW_RE.match(s):
        return (None, _num(m.group(1)))
    if m := BETWEEN_RE.match(s):
        return (_num(m.group(1)), _num(m.group(2)))
    if m := ABOVE_RE.match(s):
        return (_num(m.group(1)), None)
    return None


def parse_touch_label(label: str, spot: float | None) -> tuple[str, float] | None:
    s = label.strip()
    if m := TOUCH_UP_RE.match(s):
        return ("above", _num(m.group(1)))
    if m := TOUCH_DOWN_RE.match(s):
        return ("below", _num(m.group(1)))
    if (m := BARE_RE.match(s)) and spot:
        price = _num(m.group(1))
        return ("above" if price > spot else "below", price)
    return None


def _usdc_price(outcome: dict) -> float | None:
    p = (outcome.get("price") or {}).get(CURRENCY)
    return float(p) if p is not None else None


def _end_dt(m: dict) -> datetime | None:
    raw = m.get("bet_end_date")
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _market_url(m: dict) -> str:
    return f"https://futuur.com/q/{m['id']}/{m.get('slug', '')}"


class FutuurAdapter(SourceAdapter):
    name = "futuur"
    source_type = "prediction_market"

    def _markets(self, asset: str) -> list[dict]:
        cfg = ASSET_CONFIG[asset]
        data = get_json(f"{FUTUUR_BASE}/markets/", {"search": cfg["search"], "limit": 100})
        out = []
        for m in data.get("results", []):
            if m.get("status") != "open" or not m.get("real_currency_available"):
                continue
            if cfg["title_re"].search(m.get("title", "")):
                out.append(m)
        return out

    def fetch(self, asset: str) -> SourceFetchResult:
        if asset not in ASSET_CONFIG:
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)
        try:
            markets = self._markets(asset)
        except Exception as exc:  # noqa: BLE001
            return SourceFetchResult(source_name=self.name, source_type=self.source_type, error=f"discovery failed: {exc}")

        fetched_at = datetime.now(timezone.utc).isoformat()
        distributions = []
        for m in markets:
            if not POINT_TITLE_RE.search(m["title"]):
                continue
            dist = self.market_to_distribution(asset, m)
            if dist is not None:
                dist.fetched_at_utc = fetched_at
                distributions.append(dist)
        distributions.sort(key=lambda d: d.target_date)
        return SourceFetchResult(source_name=self.name, source_type=self.source_type, distributions=distributions)

    def market_to_distribution(self, asset: str, m: dict) -> PriceDistribution | None:
        end = _end_dt(m)
        if end is None:
            return None
        buckets = []
        for o in m.get("outcomes", []):
            rng = parse_range_label(o.get("title", ""))
            prob = _usdc_price(o)
            if rng is None or prob is None:
                continue
            buckets.append(PriceBucket(low=rng[0], high=rng[1], prob=prob, label=o["title"]))
        if len(buckets) < 2:
            return None
        volume = float(m.get("volume_real_money") or 0.0)
        return PriceDistribution(
            asset=asset,
            source_type=self.source_type,
            source_name=self.name,
            target_date=end.date(),
            period_label=end.strftime("%b %-d, %Y"),
            buckets=buckets,
            weight=volume,
            resolve_datetime_utc=end.isoformat(),
            volume=volume,
            liquidity=float(m.get("liquidity_real_money") or 0.0),
            source_url=_market_url(m),
            raw_note="Real-money (USDC) book only; mutually-exclusive price ranges, last traded price per range.",
        )

    def fetch_touch(self, asset: str) -> TouchFetchResult:
        if asset not in ASSET_CONFIG:
            return TouchFetchResult(source_name=self.name)
        try:
            markets = self._markets(asset)
        except Exception as exc:  # noqa: BLE001
            return TouchFetchResult(source_name=self.name, error=f"discovery failed: {exc}")
        spot = spot_price.fetch_spot(asset)
        fetched_at = datetime.now(timezone.utc).isoformat()
        touches = []
        for m in markets:
            if not TOUCH_TITLE_RE.search(m["title"]):
                continue
            t = self.market_to_touch(asset, m, spot)
            if t is not None:
                t.fetched_at_utc = fetched_at
                touches.append(t)
        return TouchFetchResult(source_name=self.name, touches=touches)

    def market_to_touch(self, asset: str, m: dict, spot: float | None) -> TouchForecast | None:
        end = _end_dt(m)
        if end is None:
            return None
        thresholds = []
        for o in m.get("outcomes", []):
            parsed = parse_touch_label(o.get("title", ""), spot)
            prob = _usdc_price(o)
            if parsed is None or prob is None:
                continue
            thresholds.append(TouchThreshold(direction=parsed[0], price=parsed[1], prob_touch=prob, label=o["title"]))
        if not thresholds:
            return None
        thresholds.sort(key=lambda t: (t.direction, t.price))
        return TouchForecast(
            asset=asset,
            source_name=self.name,
            expiry_date=end.date(),
            period_label=end.strftime("%b %-d, %Y"),
            thresholds=thresholds,
            total_volume=float(m.get("volume_real_money") or 0.0),
            source_url=_market_url(m),
            raw_note=f"Touch probability (real-money USDC book): chance {asset} trades at this price at any point before expiry.",
            resolve_datetime_utc=end.isoformat(),
        )
