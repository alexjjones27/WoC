"""Financial Modeling Prep client (``price-target`` and ``upgrades-downgrades``).

FMP is the cheapest per-analyst source, with two caveats that the pipeline has
to carry rather than hide:

1. FMP's ``publishedDate`` is when the NEWS carrying the action was published,
   not when the analyst acted. For same-day coverage those coincide; for
   back-filled history they do not. The record therefore keeps that timestamp
   in ``vendor_published_at`` as well as using it for ``action_date``, and the
   data-quality report counts how often a target's implied return against our
   own price history looks like it was set a day or more earlier.

2. ``priceWhenPosted`` is FMP's own claim about the prevailing price. It is
   kept in ``spot_at_action`` and then CHECKED against our price history rather
   than trusted -- a vendor that back-fills this field with the current price
   would otherwise silently destroy every implied return in the sample.

STATUS: written against FMP's published v3 schema. No key was available here,
so parsing is unverified against a live response; :func:`parse_price_targets`
and :func:`parse_grades` are isolated for testing against captured payloads.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from .. import config
from . import http
from .base import PriceTargetRecord, VendorClient

BASE_URL = "https://financialmodelingprep.com/api/v3"


def parse_price_targets(payload, retrieved_at: datetime) -> list[PriceTargetRecord]:
    out: list[PriceTargetRecord] = []
    for row in payload or []:
        firm = row.get("analystCompany") or row.get("newsPublisher")
        published = row.get("publishedDate")
        if not (row.get("symbol") and firm and published):
            continue
        try:
            pub_dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        except ValueError:
            pub_dt = None
        out.append(
            PriceTargetRecord.build(
                ticker=row["symbol"],
                analyst_firm=firm,
                analyst_name=row.get("analystName"),
                action_date=published,
                rating=None,
                rating_prev=None,
                # adjPriceTarget is FMP's split-adjusted target; prefer it when
                # present, but keep the raw one so the adjustment is auditable.
                price_target=row.get("adjPriceTarget") or row.get("priceTarget"),
                price_target_prev=None,
                spot_at_action=row.get("priceWhenPosted"),
                fiscal_year_covered=None,
                source="fmp",
                retrieved_at=retrieved_at,
                vendor_published_at=pub_dt,
                raw_price_target=row.get("priceTarget"),
                news_url=row.get("newsURL"),
            )
        )
    return out


def parse_grades(payload, retrieved_at: datetime) -> list[PriceTargetRecord]:
    """Normalise ``upgrades-downgrades`` rows (ratings, no price targets)."""
    out: list[PriceTargetRecord] = []
    for row in payload or []:
        firm = row.get("gradingCompany")
        published = row.get("publishedDate")
        if not (row.get("symbol") and firm and published):
            continue
        out.append(
            PriceTargetRecord.build(
                ticker=row["symbol"],
                analyst_firm=firm,
                analyst_name=None,
                action_date=published,
                rating=row.get("newGrade"),
                rating_prev=row.get("previousGrade"),
                price_target=None,
                price_target_prev=None,
                spot_at_action=row.get("priceWhenPosted"),
                fiscal_year_covered=None,
                source="fmp",
                retrieved_at=retrieved_at,
                action=row.get("action"),
            )
        )
    return out


class FMPClient(VendorClient):
    name = "fmp"
    is_real_data = True
    response_shape_verified = False

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL):
        self.api_key = api_key or config.vendor_key("fmp")
        self.base_url = base_url.rstrip("/")

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"set {config.VENDOR_KEY_ENV['fmp']} to enable"
        return True, "ok"

    def fetch(self, ticker: str, start: date | None = None, end: date | None = None):
        ok, reason = self.available()
        if not ok:
            raise http.FetchError(f"fmp unavailable: {reason}")
        retrieved_at = datetime.now(timezone.utc)
        params = {"symbol": ticker.upper(), "apikey": self.api_key}
        targets = parse_price_targets(
            http.fetch_json(f"{self.base_url}/price-target", params=params), retrieved_at
        )
        grades = parse_grades(
            http.fetch_json(f"{self.base_url}/upgrades-downgrades", params=params),
            retrieved_at,
        )
        records = targets + grades
        # Filtering is done here rather than server-side because the v3
        # endpoints ignore date bounds on several plan tiers.
        if start:
            records = [r for r in records if r.action_date >= start]
        if end:
            records = [r for r in records if r.action_date <= end]
        return records
