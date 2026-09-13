"""Manifold Markets adapter (Phase 1, live -- but play-money, unlike
Polymarket/Kalshi).

Manifold's `MULTI_NUMERIC` market type ("shouldAnswersSumToOne": true) is
structurally the same as Polymarket's range events and Kalshi's KXBTCY: a
set of mutually-exclusive numeric-range buckets ("Below $25000",
"$25000-$50000", ..., "Above $200000"), each answer's `probability` field
*is* that bucket's probability directly. Confirmed live (2026-09-13) on
"[ACX 2026] What will be the price of Bitcoin at the end of 2026?" (an
Astral Codex Ten annual prediction-contest market, closing 2027-01-07,
~$18k MANA volume, 12 buckets).

Two real differences from Polymarket/Kalshi, both handled explicitly rather
than silently:

  1. PLAY MONEY. Manifold's token is MANA -- free, not redeemable for
     anything close to face value. Mixing raw MANA volume into the same
     volume-weighted mixture as Polymarket/Kalshi's real dollars would let
     a very actively-traded but stakes-free market outweigh a thinner but
     real-money one. Every distribution's weight is multiplied by
     PLAY_MONEY_WEIGHT_DISCOUNT before the aggregator ever sees it, so
     Manifold can contribute to (and diversify) a forecast but structurally
     can't dominate one. This is a documented judgment call, not a
     calibrated MANA->USD conversion -- no verified exchange rate exists to
     compute one from; tune the constant if you disagree with 10%.
  2. USER-GENERATED, noisy long tail. Unlike Polymarket/Kalshi's own
     curated recurring products, anyone can create a Manifold market, so a
     search for "bitcoin" surfaces a long tail of one-off, oddly-scoped, or
     abandoned markets alongside genuinely good ones. Filtered down to:
     outcomeType MULTI_NUMERIC + shouldAnswersSumToOne (rules out the far
     more common BINARY touch-probability and freeform MULTIPLE_CHOICE
     markets, which don't have this module's needed partition-of-price
     semantics) and a minimum volume floor (MIN_VOLUME_MANA) to skip
     markets with no real betting activity behind them. Confirmed live:
     with these filters, the near-term recurring "Bitcoin price on <date>"
     daily product that used to run on Manifold has no currently-open
     instances (all resolved) -- so today this adapter's only live
     contribution is the ACX 2026 year-end market above, not a full weekly
     series the way Polymarket/Kalshi are. That may change as new markets
     get created; the discovery query itself doesn't hardcode any specific
     market.

Covers BTC and crude oil (WTI) -- see ASSET_SEARCH_TERMS below for how a
new asset gets added. Oil's search terms currently only turn up
touch-style "highest/lowest price this year" MULTI_NUMERIC markets
(confirmed live 2026-09-13, e.g. "What will be the highest price of crude
oil in 2026?"), which the existing `shouldAnswersSumToOne` check already
correctly excludes (those markets have independent, not partitioned,
answers -- shouldAnswersSumToOne is False) -- so oil currently has no live
Manifold contribution, same non-issue as its dormant near-term BTC series.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from adapters.base import SourceAdapter
from adapters.http import get_json
from common.distribution import PriceBucket, PriceDistribution, SourceFetchResult

MANIFOLD_BASE = "https://api.manifold.markets/v0"

PLAY_MONEY_WEIGHT_DISCOUNT = 0.10
MIN_VOLUME_MANA = 50.0

# Per-asset full-text search terms tried against Manifold's search API. Add
# a row here (plus, if new, an AssetSpec entry in common/assets.py) to
# cover a new asset -- nothing else in this file is asset-specific.
ASSET_SEARCH_TERMS = {
    "BTC": ["bitcoin", "btc"],
    "OIL": ["crude oil", "wti oil", "oil price"],
}

# User-generated titles aren't consistently formatted -- confirmed live,
# all of these appear across different markets: "Below $116,000" / "Under
# $25000" (comma optional), "Above $200000" / "Over $X", "$25000-$50000" /
# "$116,000 - $117,999" (dash spacing/commas optional), and "250,000+".
# Matched as distinct patterns rather than one dense regex so each is easy
# to verify and extend.
LOW_RE = re.compile(r"^(?:below|under)\s*\$?([\d,]+)$", re.IGNORECASE)
HIGH_RE = re.compile(r"^(?:above|over)\s*\$?([\d,]+)$", re.IGNORECASE)
PLUS_RE = re.compile(r"^\$?([\d,]+)\s*\+$")
RANGE_RE = re.compile(r"^\$?([\d,]+)\s*(?:-|to)\s*\$?([\d,]+)$", re.IGNORECASE)


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_bucket_label(label: str) -> tuple[float | None, float | None]:
    s = label.strip()
    if m := LOW_RE.match(s):
        return (None, _num(m.group(1)))
    if m := HIGH_RE.match(s):
        return (_num(m.group(1)), None)
    if m := PLUS_RE.match(s):
        return (_num(m.group(1)), None)
    if m := RANGE_RE.match(s):
        return (_num(m.group(1)), _num(m.group(2)))
    return (None, None)


def _period_label(dt: datetime) -> str:
    return dt.strftime("%b %-d, %Y")


class ManifoldAdapter(SourceAdapter):
    name = "manifold"
    source_type = "prediction_market"

    def fetch(self, asset: str) -> SourceFetchResult:
        terms = ASSET_SEARCH_TERMS.get(asset)
        if terms is None:
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)
        try:
            slugs = self._discover_slugs(terms)
        except Exception as exc:  # noqa: BLE001
            return SourceFetchResult(source_name=self.name, source_type=self.source_type, error=f"discovery failed: {exc}")

        distributions: list[PriceDistribution] = []
        errors: list[str] = []
        fetched_at = datetime.now(timezone.utc).isoformat()

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(self._fetch_market, asset, slug): slug for slug in slugs}
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

    def _discover_slugs(self, terms: list[str]) -> list[str]:
        slugs: dict[str, bool] = {}
        for term in terms:
            results = get_json(
                f"{MANIFOLD_BASE}/search-markets",
                {"term": term, "filter": "open", "sort": "close-date", "limit": 100},
            )
            for m in results:
                # search-markets' lightweight response doesn't reliably
                # populate shouldAnswersSumToOne (confirmed live: always
                # None there even for markets where the full detail fetch
                # shows True) -- that check happens in _fetch_market below,
                # against the full-detail response, instead.
                if m.get("outcomeType") != "MULTI_NUMERIC":
                    continue
                if (m.get("volume") or 0.0) < MIN_VOLUME_MANA:
                    continue
                slug = m.get("slug")
                if slug:
                    slugs[slug] = True
        return list(slugs.keys())

    def _fetch_market(self, asset: str, slug: str) -> PriceDistribution | None:
        m = get_json(f"{MANIFOLD_BASE}/slug/{slug}")
        if not m.get("shouldAnswersSumToOne"):
            return None
        close_ms = m.get("closeTime")
        if not close_ms:
            return None
        target_dt = datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc)

        buckets: list[PriceBucket] = []
        for a in m.get("answers", []):
            low, high = _parse_bucket_label(a.get("text", ""))
            if low is None and high is None:
                continue
            buckets.append(PriceBucket(low=low, high=high, prob=float(a.get("probability", 0.0)), label=a.get("text", "")))
        if len(buckets) < 2:
            return None

        volume = float(m.get("volume") or 0.0)
        return PriceDistribution(
            asset=asset,
            source_type=self.source_type,
            source_name=self.name,
            target_date=target_dt.date(),
            period_label=_period_label(target_dt),
            buckets=buckets,
            weight=volume * PLAY_MONEY_WEIGHT_DISCOUNT,
            resolve_datetime_utc=target_dt.isoformat(),
            volume=volume,
            open_interest=None,
            liquidity=float(m["totalLiquidity"]) if m.get("totalLiquidity") is not None else None,
            source_url=m.get("url"),
            is_play_money=True,
            raw_note=(
                f"Play-money (MANA) market, user-created -- weight discounted {PLAY_MONEY_WEIGHT_DISCOUNT:.0%} "
                "vs. real-dollar platforms. Mutually-exclusive numeric-range buckets; bucket probability taken directly."
            ),
        )
