"""stockanalysis.com per-analyst ratings client.

Its SvelteKit data endpoint, ``/stocks/<sym>/ratings/__data.json``, returns the
richest free per-analyst records found: firm, the INDIVIDUAL analyst's name and
slug, the action date AND an intraday timestamp, the new and prior rating, and
the new and prior price target.

    {"action_rt": "Maintains", "pt_now": 296, "pt_old": null, "firm": "UBS",
     "analyst": "David Vogt", "slug": "david-vogt", "date": "2026-09-18",
     "rating_new": "Hold", "rating_old": "", "time": "07:25:15", ...}

STATUS: verified against live responses on 2026-09-18 across twelve tickers.

Two things to know:

* **Only the eight most recent actions per ticker.** Every pagination parameter
  tried (``p``, ``page``, ``range``, ``r``, ``limit``) returned the same eight
  rows. So this is the *breadth* source, not the *depth* one: use it alongside
  ``finviz.py``, which reaches back about sixteen months. Running both is also
  what makes the data-quality gate's cross-vendor disagreement check meaningful,
  since it cannot measure anything from a single source.

* **The ``scores`` block is look-ahead contaminated and must never be a skill
  weight.** Each record carries the analyst's success rate, average return, star
  rating and rank. Those are computed over that analyst's ENTIRE history,
  including everything after the date on the record. Using them to weight a
  forecast made in 2025 would be scoring that forecast with knowledge of how it
  turned out. They are stored under the deliberately awkward key
  ``lookahead_contaminated_scores`` so they cannot be picked up by accident, and
  they are useful for exactly one thing: cross-checking the point-in-time skill
  estimates Stage 3 will compute for itself.

TERMS OF USE: robots.txt disallows only ``/e/`` and ``/p/``, so this path is
permitted by their crawler rules. That is not the same as their terms of
service. One request per ticker per day, which is all this needs.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from . import http
from .base import PriceTargetRecord, VendorClient

DATA_URL = "https://stockanalysis.com/stocks/{ticker}/ratings/__data.json"

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

#: The site reports US market hours; its bare "time" field is Eastern.
SITE_TZ = ZoneInfo("America/New_York")


def decode_devalue(flat: list, root: int = 0):
    """Decode SvelteKit's ``devalue`` payload.

    The wire format is a flat array in which every integer is a REFERENCE to
    another slot rather than a literal. Walking it naively yields a structure
    full of small integers where strings should be.
    """
    def walk(index):
        if not isinstance(index, int):
            return index
        if index < 0:               # -1 is undefined, -2 is a hole
            return None
        value = flat[index]
        if isinstance(value, list):
            return [walk(i) for i in value]
        if isinstance(value, dict):
            return {k: walk(i) for k, i in value.items()}
        return value

    return walk(root)


def extract_ratings_node(payload: dict) -> dict | None:
    """Find the decoded node that carries the ratings list."""
    for node in payload.get("nodes") or []:
        if not node or node.get("type") != "data":
            continue
        decoded = decode_devalue(node.get("data") or [])
        if isinstance(decoded, dict) and "ratings" in decoded:
            return decoded
    return None


def parse_ratings(payload: dict, ticker: str, retrieved_at: datetime):
    """Normalise a ``__data.json`` payload into canonical records."""
    node = extract_ratings_node(payload)
    if not node:
        return []

    records: list[PriceTargetRecord] = []
    for row in node.get("ratings") or []:
        firm = (row.get("firm") or "").strip()
        day = row.get("date")
        if not (firm and day):
            continue

        published = None
        if row.get("time"):
            try:
                published = datetime.strptime(
                    f"{day} {row['time']}", "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=SITE_TZ)
            except ValueError:
                published = None

        records.append(
            PriceTargetRecord.build(
                ticker=ticker,
                analyst_firm=firm,
                analyst_name=(row.get("analyst") or "").strip() or None,
                action_date=day,
                rating=row.get("rating_new") or None,
                rating_prev=row.get("rating_old") or None,
                price_target=row.get("pt_now"),
                price_target_prev=row.get("pt_old"),
                spot_at_action=None,
                fiscal_year_covered=None,
                source="stockanalysis",
                retrieved_at=retrieved_at,
                vendor_published_at=published,
                currency=row.get("curr") or "USD",
                action=row.get("action_rt"),
                analyst_slug=row.get("slug"),
                # See the module docstring: computed over the analyst's whole
                # history, so unusable as a point-in-time weight. Named to be
                # impossible to reach for without meaning to.
                lookahead_contaminated_scores=row.get("scores"),
            )
        )
    return records


class StockAnalysisClient(VendorClient):
    name = "stockanalysis"
    is_real_data = True
    response_shape_verified = True

    request_delay_s = 1.0

    def __init__(self, url: str = DATA_URL, delay: float | None = None):
        self.url = url
        if delay is not None:
            self.request_delay_s = delay

    def available(self) -> tuple[bool, str]:
        return True, "no credential required (8 most recent actions per ticker)"

    def fetch(self, ticker: str, start: date | None = None, end: date | None = None):
        retrieved_at = datetime.now(timezone.utc)
        payload = http.fetch_json(
            self.url.format(ticker=ticker.lower()),
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=40.0,
        )
        records = parse_ratings(payload, ticker, retrieved_at)
        if start:
            records = [r for r in records if r.action_date >= start]
        if end:
            records = [r for r in records if r.action_date <= end]
        time.sleep(self.request_delay_s)
        return records

    def fetch_consensus_widget(self, ticker: str) -> dict | None:
        """The site's own consensus block. A benchmark, never an input."""
        payload = http.fetch_json(
            self.url.format(ticker=ticker.lower()),
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            timeout=40.0,
        )
        node = extract_ratings_node(payload) or {}
        return (node.get("widget") or {}).get("all")
