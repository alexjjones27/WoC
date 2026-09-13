"""Polymarket adapter (Phase 1, live).

Polymarket runs a recurring "Bitcoin price on <date>?" event for each of the
next several days. Each one is a *mutually exclusive range market*: a set of
binary sub-markets like "<68,000", "68,000-70,000", ..., ">86,000", each
with its own Yes price. Because the ranges partition the outcome space,
that Yes price *is* the market's implied probability mass for that bucket --
no threshold-differencing needed, unlike Kalshi (see kalshi.py). This is
confirmed live behavior (checked 2026-09-13): e.g.
gamma-api.polymarket.com/events/slug/bitcoin-price-on-september-19-2026
returns 11 range buckets whose Yes prices sum to ~1.

Excluded from `fetch()` (the point-in-time pipeline): Polymarket's other
Bitcoin markets such as "What price will Bitcoin hit in 2026?" or "Bitcoin
all time high by ___?". Those are *touch* markets (will price cross X at
any point before expiry), not "what is price AT date X" markets -- their
probabilities run structurally higher than a same-strike point-in-time
probability and would bias the aggregate distribution if mixed in. Only the
"Bitcoin price on <date>" family goes through `fetch()`.

"What price will Bitcoin hit in 2026?" IS used, but separately, via
`fetch_touch()` (see adapters/base.py) -- it is never merged into `fetch()`'s
output or the point-in-time aggregate; the dashboard shows it in its own
"touch probability" section instead. Confirmed live (2026-09-13):
gamma-api.polymarket.com/events/slug/what-price-will-bitcoin-hit-before-2027
has one sub-market per price level, `groupItemTitle` like "↑ 100,000" (touch
above) or "↓ 60,000" (touch below); its Yes price is P(ever crosses that
level before the event's endDate). Several already-resolved (closed=true)
duplicate strikes from past re-listings are present in the same event and
are filtered out, keeping only currently-active (closed=false) ones.

Discovery: Polymarket has no "list markets by exact family" endpoint, so we
use the public full-text search endpoint and keep only active events whose
title matches "Bitcoin price on ...". Confirmed live: this endpoint needs no
auth and returns event summaries; the per-event bucket detail still requires
a follow-up call to /events/slug/<slug> (the search response's own `markets`
field was not verified to carry outcome prices, so we always do this
follow-up rather than risk stale/missing prices).
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

# Matches the recurring daily/weekly product ("Bitcoin price on September 19?")
# and rejects the ultra-short-term 5m/15m "Up or Down" noise and long-dated
# touch markets, which use different titles.
TITLE_RE = re.compile(r"^bitcoin price on\b", re.IGNORECASE)

RANGE_RE = re.compile(r"^([<>]?)\s*\$?([\d,]+(?:\.\d+)?)\s*(?:-\s*\$?([\d,]+(?:\.\d+)?))?$")

# The single long-dated touch-probability event, e.g. "What price will
# Bitcoin hit in 2026?" -- deliberately distinct from TITLE_RE above.
TOUCH_TITLE_RE = re.compile(r"^what price will bitcoin hit\b", re.IGNORECASE)
TOUCH_LABEL_RE = re.compile(r"^([↑↓])\s*\$?([\d,]+(?:\.\d+)?)$")  # ↑=↑ ↓=↓


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
        if asset != "BTC":
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)
        try:
            slugs = self._discover_event_slugs()
        except Exception as exc:  # noqa: BLE001 - adapters must never raise
            return SourceFetchResult(
                source_name=self.name, source_type=self.source_type,
                error=f"discovery failed: {exc}",
            )

        distributions: list[PriceDistribution] = []
        errors: list[str] = []
        fetched_at = datetime.now(timezone.utc).isoformat()

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self._fetch_event, slug): slug for slug in slugs}
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

    def _discover_event_slugs(self) -> list[str]:
        slugs: dict[str, bool] = {}

        search = get_json(f"{GAMMA_BASE}/public-search", {"q": "bitcoin price on", "limit_per_type": 50})
        for ev in search.get("events", []):
            if ev.get("closed") is False and TITLE_RE.match(ev.get("title", "")):
                slugs[ev["slug"]] = True

        # Fallback discovery via the bitcoin tag, in case search misses a
        # currently-active date (search is relevance-ranked, not exhaustive).
        try:
            tagged = get_json(
                f"{GAMMA_BASE}/events",
                {"tag_slug": "bitcoin", "closed": "false", "limit": 100, "order": "endDate", "ascending": "true"},
            )
            for ev in tagged:
                if TITLE_RE.match(ev.get("title", "")):
                    slugs[ev["slug"]] = True
        except Exception:
            pass  # search-only discovery is enough; this is a best-effort extra

        return list(slugs.keys())

    def _fetch_event(self, slug: str) -> PriceDistribution | None:
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
            asset="BTC",
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
        if asset != "BTC":
            return TouchFetchResult(source_name=self.name)
        try:
            slug = self._discover_touch_event_slug()
        except Exception as exc:  # noqa: BLE001
            return TouchFetchResult(source_name=self.name, error=f"discovery failed: {exc}")
        if not slug:
            return TouchFetchResult(source_name=self.name)
        try:
            touch = self._fetch_touch_event(slug)
        except Exception as exc:  # noqa: BLE001
            return TouchFetchResult(source_name=self.name, error=str(exc))
        return TouchFetchResult(source_name=self.name, touches=[touch] if touch else [])

    def _discover_touch_event_slug(self) -> str | None:
        tagged = get_json(f"{GAMMA_BASE}/events", {"tag_slug": "bitcoin", "closed": "false", "limit": 100})
        for ev in tagged:
            if TOUCH_TITLE_RE.match(ev.get("title", "")):
                return ev["slug"]
        return None

    def _fetch_touch_event(self, slug: str) -> TouchForecast | None:
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
            asset="BTC",
            source_name=self.name,
            expiry_date=target_dt.date(),
            period_label=_period_label(target_dt),
            thresholds=thresholds,
            total_volume=sum(t.volume or 0.0 for t in thresholds),
            source_url=f"https://polymarket.com/event/{slug}",
            raw_note="Touch probability: chance BTC crosses this price at ANY point before expiry, not price-at-expiry.",
            resolve_datetime_utc=end_date,
        )
