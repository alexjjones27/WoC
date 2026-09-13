"""Polymarket adapter (Phase 1, live). Covers BTC and crude oil (WTI) --
see ASSET_CONFIG below for how a new asset gets added.

Polymarket runs a recurring "Bitcoin price on <date>?" event for each of the
next several days. Each one is a *mutually exclusive range market*: a set of
binary sub-markets like "<68,000", "68,000-70,000", ..., ">86,000", each
with its own Yes price. Because the ranges partition the outcome space,
that Yes price *is* the market's implied probability mass for that bucket --
no threshold-differencing needed, unlike Kalshi (see kalshi.py). This is
confirmed live behavior (checked 2026-09-13): e.g.
gamma-api.polymarket.com/events/slug/bitcoin-price-on-september-19-2026
returns 11 range buckets whose Yes prices sum to ~1.

Crude oil has NO equivalent range-bucket family on Polymarket (confirmed
live, 2026-09-13: searched "oil price"/"crude oil" -- every WTI event found
is one of the touch-style "What will WTI Crude Oil (WTI) hit ...?" markets
below, never a "WTI price on <date>" partition). ASSET_CONFIG["OIL"]'s
`terminal_title_re` is therefore None and `fetch()` correctly returns
nothing for OIL on this platform -- Kalshi (KXWTI/KXWTIW) is the only
point-in-time oil source today; see kalshi.py.

Excluded from `fetch()` (the point-in-time pipeline) for any asset:
touch-style markets like "What price will Bitcoin hit in 2026?" / "What
will WTI Crude Oil (WTI) hit in September 2026?" or "Bitcoin all time high
by ___?". Those answer "will price cross X at any point before expiry", not
"what is price AT date X" -- their probabilities run structurally higher
than a same-strike point-in-time probability and would bias the aggregate
distribution if mixed in.

The touch-style events ARE used, but separately, via `fetch_touch()` (see
adapters/base.py) -- never merged into `fetch()`'s output; the dashboard
shows them in their own "touch probability" section instead. Confirmed
live: both BTC's and OIL's touch events use the same shape -- one
sub-market per price level, `groupItemTitle` like "↑ 100,000" / "↑ $130"
(touch above) or "↓ 60,000" / "↓ $95" (touch below); Yes price is P(ever
crosses that level before the event's endDate). Several already-resolved
(closed=true) duplicate strikes from past re-listings can be present in
the same event and are filtered out, keeping only currently-active
(closed=false) ones. Oil additionally has more than one touch event open
at once (a weekly AND a monthly "what will WTI hit" event) -- unlike BTC's
single one, so touch discovery returns a list, not a single slug.

Discovery: Polymarket has no "list markets by exact family" endpoint, so we
use the public full-text search endpoint and keep only active events whose
title matches a per-asset pattern. Confirmed live: this endpoint needs no
auth and returns event summaries; the per-event bucket detail still requires
a follow-up call to /events/slug/<slug> (the search response's own `markets`
field was not verified to carry outcome prices, so we always do this
follow-up rather than risk stale/missing prices). Where an asset has a
verified tag_slug (only "bitcoin" so far), that's tried too as a fallback,
since search is relevance-ranked, not exhaustive.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

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

GAMMA_BASE = "https://gamma-api.polymarket.com"

RANGE_RE = re.compile(r"^([<>]?)\s*\$?([\d,]+(?:\.\d+)?)\s*(?:-\s*\$?([\d,]+(?:\.\d+)?))?$")
TOUCH_LABEL_RE = re.compile(r"^([↑↓])\s*\$?([\d,]+(?:\.\d+)?)$")

# Per-asset discovery config. `terminal_title_re`/`terminal_search_term` are
# None for an asset with no point-in-time range-bucket family on this
# platform (confirmed live before leaving them unset, not assumed) -- add a
# row here (an AssetSpec entry in common/assets.py too, if new) to cover a
# new asset.
ASSET_CONFIG = {
    "BTC": {
        "terminal_title_re": re.compile(r"^bitcoin price on\b", re.IGNORECASE),
        "terminal_search_term": "bitcoin price on",
        "tag_slug": "bitcoin",
        "touch_title_re": re.compile(r"^what price will bitcoin hit\b", re.IGNORECASE),
        "touch_search_term": "bitcoin hit",
    },
    "OIL": {
        "terminal_title_re": None,
        "terminal_search_term": None,
        "tag_slug": None,
        "touch_title_re": re.compile(r"^what will wti crude oil \(wti\) hit\b", re.IGNORECASE),
        "touch_search_term": "WTI crude oil hit",
    },
}


def _parse_touch_label(label: str) -> tuple[str, float] | None:
    m = TOUCH_LABEL_RE.match(label.strip())
    if not m:
        return None
    arrow, num = m.groups()
    return ("above" if arrow == "↑" else "below"), float(num.replace(",", ""))


def _parse_bucket_label(label: str) -> tuple[float | None, float | None]:
    """"<68,000" -> (None, 68000); "68,000-70,000" -> (68000, 70000);
    ">86,000" -> (86000, None)."""
    m = RANGE_RE.match(label.strip())
    if not m:
        return (None, None)
    sign, first, second = m.groups()
    first_v = float(first.replace(",", ""))
    if second:
        return (first_v, float(second.replace(",", "")))
    if sign == "<":
        return (None, first_v)
    if sign == ">":
        return (first_v, None)
    return (first_v, first_v)


def _period_label(dt: datetime) -> str:
    return dt.strftime("%b %-d, %Y") if hasattr(dt, "strftime") else str(dt)


class PolymarketAdapter(SourceAdapter):
    name = "polymarket"
    source_type = "prediction_market"

    def fetch(self, asset: str) -> SourceFetchResult:
        cfg = ASSET_CONFIG.get(asset)
        if cfg is None or cfg["terminal_title_re"] is None:
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)
        try:
            slugs = self._discover_event_slugs(cfg)
        except Exception as exc:  # noqa: BLE001 - adapters must never raise
            return SourceFetchResult(
                source_name=self.name, source_type=self.source_type,
                error=f"discovery failed: {exc}",
            )

        distributions: list[PriceDistribution] = []
        errors: list[str] = []
        fetched_at = datetime.now(timezone.utc).isoformat()

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self._fetch_event, asset, slug): slug for slug in slugs}
            for fut in as_completed(futures):
                slug = futures[fut]
                try:
                    dist = fut.result()
                    if dist is not None:
                        dist.fetched_at_utc = fetched_at
                        distributions.append(dist)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{slug}: {exc}")

        distributions.sort(key=lambda d: d.target_date)
        return SourceFetchResult(
            source_name=self.name,
            source_type=self.source_type,
            distributions=distributions,
            error="; ".join(errors) if errors and not distributions else None,
        )

    def _discover_event_slugs(self, cfg: dict) -> list[str]:
        slugs: dict[str, bool] = {}
        title_re = cfg["terminal_title_re"]

        search = get_json(f"{GAMMA_BASE}/public-search", {"q": cfg["terminal_search_term"], "limit_per_type": 50})
        for ev in search.get("events", []):
            if ev.get("closed") is False and title_re.match(ev.get("title", "")):
                slugs[ev["slug"]] = True

        # Fallback discovery via the asset's tag, in case search misses a
        # currently-active date (search is relevance-ranked, not exhaustive).
        if cfg.get("tag_slug"):
            try:
                tagged = get_json(
                    f"{GAMMA_BASE}/events",
                    {"tag_slug": cfg["tag_slug"], "closed": "false", "limit": 100, "order": "endDate", "ascending": "true"},
                )
                for ev in tagged:
                    if title_re.match(ev.get("title", "")):
                        slugs[ev["slug"]] = True
            except Exception:
                pass  # search-only discovery is enough; this is a best-effort extra

        return list(slugs.keys())

    def _fetch_event(self, asset: str, slug: str) -> PriceDistribution | None:
        ev = get_json(f"{GAMMA_BASE}/events/slug/{slug}")
        end_date = ev.get("endDate")
        if not end_date:
            return None
        target_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))

        buckets: list[PriceBucket] = []
        total_volume = 0.0
        for m in ev.get("markets", []):
            label = m.get("groupItemTitle") or m.get("question", "")
            low, high = _parse_bucket_label(label)
            if low is None and high is None:
                continue
            try:
                outcomes = m["outcomes"] if isinstance(m["outcomes"], list) else json.loads(m["outcomes"])
                prices = m["outcomePrices"] if isinstance(m["outcomePrices"], list) else json.loads(m["outcomePrices"])
                yes_idx = outcomes.index("Yes")
                prob = float(prices[yes_idx])
            except (KeyError, ValueError, TypeError):
                continue
            buckets.append(PriceBucket(low=low, high=high, prob=prob, label=label))
            total_volume += float(m.get("volumeNum") or 0.0)

        if not buckets:
            return None

        return PriceDistribution(
            asset=asset,
            source_type=self.source_type,
            source_name=self.name,
            target_date=target_dt.date(),
            period_label=_period_label(target_dt),
            buckets=buckets,
            weight=max(total_volume, float(ev.get("volume") or 0.0)),
            resolve_datetime_utc=end_date,
            volume=float(ev.get("volume") or 0.0),
            open_interest=float(ev.get("openInterest")) if ev.get("openInterest") is not None else None,
            liquidity=float(ev.get("liquidity")) if ev.get("liquidity") is not None else None,
            source_url=f"https://polymarket.com/event/{slug}",
            raw_note="Mutually-exclusive range-bucket event; bucket Yes-price = bucket probability directly.",
        )

    def fetch_touch(self, asset: str) -> TouchFetchResult:
        cfg = ASSET_CONFIG.get(asset)
        if cfg is None or cfg.get("touch_title_re") is None:
            return TouchFetchResult(source_name=self.name)
        try:
            slugs = self._discover_touch_event_slugs(cfg)
        except Exception as exc:  # noqa: BLE001
            return TouchFetchResult(source_name=self.name, error=f"discovery failed: {exc}")
        if not slugs:
            return TouchFetchResult(source_name=self.name)

        touches: list[TouchForecast] = []
        errors: list[str] = []
        for slug in slugs:
            try:
                touch = self._fetch_touch_event(asset, slug)
                if touch is not None:
                    touches.append(touch)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{slug}: {exc}")
        return TouchFetchResult(source_name=self.name, touches=touches, error="; ".join(errors) if errors and not touches else None)

    def _discover_touch_event_slugs(self, cfg: dict) -> list[str]:
        slugs: dict[str, bool] = {}
        title_re = cfg["touch_title_re"]

        search = get_json(f"{GAMMA_BASE}/public-search", {"q": cfg["touch_search_term"], "limit_per_type": 50})
        for ev in search.get("events", []):
            if ev.get("closed") is False and title_re.match(ev.get("title", "")):
                slugs[ev["slug"]] = True

        if cfg.get("tag_slug"):
            try:
                tagged = get_json(f"{GAMMA_BASE}/events", {"tag_slug": cfg["tag_slug"], "closed": "false", "limit": 100})
                for ev in tagged:
                    if title_re.match(ev.get("title", "")):
                        slugs[ev["slug"]] = True
            except Exception:
                pass
        return list(slugs.keys())

    def _fetch_touch_event(self, asset: str, slug: str) -> TouchForecast | None:
        ev = get_json(f"{GAMMA_BASE}/events/slug/{slug}")
        end_date = ev.get("endDate")
        if not end_date:
            return None
        target_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))

        # The event can contain already-resolved (closed=true) duplicate
        # strikes from past re-listings alongside the live ones -- keep only
        # active markets, and if a (direction, price) pair somehow still
        # duplicates, keep the higher-volume one.
        best: dict[tuple[str, float], TouchThreshold] = {}
        for m in ev.get("markets", []):
            if m.get("closed"):
                continue
            parsed = _parse_touch_label(m.get("groupItemTitle") or "")
            if parsed is None:
                continue
            direction, price = parsed
            try:
                outcomes = m["outcomes"] if isinstance(m["outcomes"], list) else json.loads(m["outcomes"])
                prices = m["outcomePrices"] if isinstance(m["outcomePrices"], list) else json.loads(m["outcomePrices"])
                prob = float(prices[outcomes.index("Yes")])
            except (KeyError, ValueError, TypeError):
                continue
            vol = float(m.get("volumeNum") or 0.0)
            key = (direction, price)
            if key not in best or vol > (best[key].volume or 0.0):
                best[key] = TouchThreshold(direction=direction, price=price, prob_touch=prob, volume=vol, label=m.get("groupItemTitle", ""))

        thresholds = sorted(best.values(), key=lambda t: (t.direction, t.price))
        if not thresholds:
            return None
        return TouchForecast(
            asset=asset,
            source_name=self.name,
            expiry_date=target_dt.date(),
            period_label=_period_label(target_dt),
            thresholds=thresholds,
            total_volume=sum(t.volume or 0.0 for t in thresholds),
            source_url=f"https://polymarket.com/event/{slug}",
            raw_note=f"Touch probability: chance {asset} crosses this price at ANY point before expiry, not price-at-expiry.",
            resolve_datetime_utc=end_date,
        )
