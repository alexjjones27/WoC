"""Finviz per-analyst ratings client.

Finviz's quote page embeds its ratings history as JSON inside the HTML, in the
same blob that feeds the price chart's event markers:

    {"dateTimestamp": 1769749200, "eventType": "chartEvent/ratings",
     "ratings": [{"action": "Reiterated", "analyst": "JP Morgan",
                  "rating": "Overweight", "targetPrice": "$315 &rarr; $325"}]}

That is the brief's required schema almost exactly: action date, firm, rating
and prior rating, price target and prior target. It is the deepest per-analyst
history reachable without a credential, at roughly 16 months and 25-40 firms per
large-cap name.

STATUS: verified against live responses on 2026-09-18 across AAPL, MSFT, NVDA,
JPM, XOM, PLTR, JNJ, WMT, BA, DIS, GS and CAT. The rating vocabulary in
``base._RATING_MAP`` was extended from what those responses actually contained
rather than from guesswork, and no live rating string is currently unmapped.

Two limits worth knowing before relying on it:

* **No analyst name.** ``analyst`` is the FIRM. Person-level skill scoring
  (Stage 3) needs ``stockanalysis.py`` as well.
* **Not point-in-time.** This is a current snapshot of back-history, so it can
  only be read under ``pit_mode="assume_vendor_history"``. The append-only store
  fixes this going forward: snapshot daily and ``retrieved_at`` becomes real.

TERMS OF USE: Finviz's robots.txt permits ``/quote.ashx`` and ``/stock`` (it
disallows ``/export``, ``/screener?*`` and the CSV endpoints), but robots.txt is
not the terms of service, and Finviz sells a tier that includes data export.
Keep to roughly one request per ticker per day, which is all this pipeline
needs, and read their terms before running it on a schedule.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import date, datetime, timezone

from . import http
from .base import PriceTargetRecord, VendorClient

QUOTE_URL = "https://finviz.com/quote.ashx?t={ticker}"

#: The browser UA is required: Finviz serves a redirect loop to unknown agents.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_EVENT_RE = re.compile(
    r'\{"dateTimestamp":(\d+),"eventType":"chartEvent/ratings","ratings":(\[.*?\])\}'
)

#: "$600 &rarr; $650" -> ("600", "650");  "$392" -> (None, "392")
_ARROW = "→"


def _split_arrow(value: str | None) -> tuple[str | None, str | None]:
    """Split a vendor 'old -> new' string. Returns (previous, current)."""
    if not value:
        return None, None
    text = html.unescape(value).replace("&rarr;", _ARROW).strip()
    if _ARROW in text:
        prev, _, cur = text.partition(_ARROW)
        return prev.strip() or None, cur.strip() or None
    return None, text or None


def _money(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    try:
        out = float(cleaned)
    except ValueError:
        return None
    return out if out > 0 else None


def parse_quote_page(
    html_text: str, ticker: str, retrieved_at: datetime
) -> list[PriceTargetRecord]:
    """Extract per-analyst records from a Finviz quote page.

    Kept separate from the fetch so it can be tested against a captured page.

    The event timestamp is midnight US/Eastern of the action date, so reading it
    in UTC recovers the correct calendar date. This was checked rather than
    assumed: across 43 live timestamps, a UTC read produced zero weekend dates,
    while a fixed -5h read produced six Sundays.
    """
    records: list[PriceTargetRecord] = []
    for raw_ts, blob in _EVENT_RE.findall(html_text):
        try:
            ratings = json.loads(html.unescape(blob.replace("\\u0026", "&")))
        except json.JSONDecodeError:
            continue
        action_date = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc).date()
        for item in ratings:
            firm = html.unescape((item.get("analyst") or "").strip())
            if not firm:
                continue
            rating_prev, rating_new = _split_arrow(item.get("rating"))
            pt_prev_s, pt_new_s = _split_arrow(item.get("targetPrice"))
            records.append(
                PriceTargetRecord.build(
                    ticker=ticker,
                    analyst_firm=firm,
                    # Finviz publishes the firm only, never the individual.
                    analyst_name=None,
                    action_date=action_date,
                    rating=rating_new,
                    rating_prev=rating_prev,
                    price_target=_money(pt_new_s),
                    price_target_prev=_money(pt_prev_s),
                    # Finviz does not publish the prevailing price; the pipeline
                    # resolves the anchor from its own point-in-time history.
                    spot_at_action=None,
                    fiscal_year_covered=None,
                    source="finviz",
                    retrieved_at=retrieved_at,
                    action=html.unescape((item.get("action") or "").strip()) or None,
                )
            )
    return records


class FinvizClient(VendorClient):
    name = "finviz"
    is_real_data = True
    response_shape_verified = True

    #: Politeness delay between tickers. One request per ticker per day is all
    #: the pipeline needs, so there is no reason to go faster than this.
    request_delay_s = 1.0

    def __init__(self, url: str = QUOTE_URL, delay: float | None = None):
        self.url = url
        if delay is not None:
            self.request_delay_s = delay

    def available(self) -> tuple[bool, str]:
        return True, "no credential required"

    def fetch(self, ticker: str, start: date | None = None, end: date | None = None):
        retrieved_at = datetime.now(timezone.utc)
        body = http.fetch_bytes(
            self.url.format(ticker=ticker.upper()),
            headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
            timeout=40.0,
        )
        records = parse_quote_page(
            body.decode("utf-8", "replace"), ticker, retrieved_at
        )
        if start:
            records = [r for r in records if r.action_date >= start]
        if end:
            records = [r for r in records if r.action_date <= end]
        time.sleep(self.request_delay_s)
        return records
