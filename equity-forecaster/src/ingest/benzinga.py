"""Benzinga Analyst Ratings client.

Benzinga is the best per-analyst source in the brief's list that is reachable
without an institutional contract: every row is one firm's action on one day,
with both the new and the prior rating and price target, which is what the
revision-based features in Stage 7 need.

STATUS: written against Benzinga's published v2.1 ``calendar/ratings`` schema.
No key was available in this environment, so the response parsing here has NOT
been exercised against a live response. Treat the field mapping as a first
draft to be confirmed on the first real pull -- :func:`parse_ratings` is kept
separate from the fetch precisely so it can be unit-tested against a captured
payload.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from .. import config
from . import http
from .base import PriceTargetRecord, VendorClient

API_URL = "https://api.benzinga.com/api/v2.1/calendar/ratings"
PAGE_SIZE = 1000


def parse_ratings(payload: dict, retrieved_at: datetime) -> list[PriceTargetRecord]:
    """Normalise a Benzinga ratings payload into canonical records."""
    out: list[PriceTargetRecord] = []
    for row in payload.get("ratings") or []:
        ticker = row.get("ticker")
        firm = row.get("analyst")  # Benzinga: firm name
        action_date = row.get("date")
        if not (ticker and firm and action_date):
            continue
        published = None
        if row.get("time"):
            try:
                published = datetime.fromisoformat(
                    f"{action_date}T{row['time']}"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                published = None
        out.append(
            PriceTargetRecord.build(
                ticker=ticker,
                analyst_firm=firm,
                analyst_name=row.get("analyst_name"),
                action_date=action_date,
                rating=row.get("rating_current"),
                rating_prev=row.get("rating_prior"),
                price_target=row.get("pt_current"),
                price_target_prev=row.get("pt_prior"),
                # Benzinga does not publish the prevailing price; the pipeline
                # resolves the anchor from its own PIT price history instead.
                spot_at_action=None,
                fiscal_year_covered=None,
                source="benzinga",
                retrieved_at=retrieved_at,
                vendor_published_at=published,
                currency=row.get("currency") or "USD",
                action_pt=row.get("action_pt"),
                action_company=row.get("action_company"),
                vendor_id=row.get("id"),
            )
        )
    return out


class BenzingaClient(VendorClient):
    name = "benzinga"
    is_real_data = True
    response_shape_verified = False

    def __init__(self, api_key: str | None = None, url: str = API_URL):
        self.api_key = api_key or config.vendor_key("benzinga")
        self.url = url

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"set {config.VENDOR_KEY_ENV['benzinga']} to enable"
        return True, "ok"

    def fetch(self, ticker: str, start: date | None = None, end: date | None = None):
        ok, reason = self.available()
        if not ok:
            raise http.FetchError(f"benzinga unavailable: {reason}")
        end = end or datetime.now(timezone.utc).date()
        retrieved_at = datetime.now(timezone.utc)
        records: list[PriceTargetRecord] = []
        page = 0
        while True:
            params = {
                "token": self.api_key,
                "parameters[tickers]": ticker.upper(),
                "pagesize": PAGE_SIZE,
                "page": page,
            }
            if start:
                params["parameters[date_from]"] = start.isoformat()
            params["parameters[date_to]"] = end.isoformat()
            payload = http.fetch_json(
                self.url, params=params, headers={"Accept": "application/json"}
            )
            batch = parse_ratings(payload, retrieved_at)
            records.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        return records
