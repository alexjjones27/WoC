"""Daily price history, split handling, and the point-in-time spot lookup.

This is the only leg of Stage 1 that runs on free data with no credentials, so
it is the one that is actually verified against live responses (Yahoo's
``/v8/finance/chart`` endpoint; shape confirmed 2026-09-18).

Two things here are load-bearing for correctness and are easy to get wrong:

1. **Split adjustment.** Yahoo's ``close`` series is back-adjusted for splits,
   so a 2019 AAPL close reads ~$50, not ~$200. Vendor price targets, by
   contrast, are usually stored as-quoted at the time. Dividing an as-quoted
   pre-split target by a back-adjusted spot produces an implied return that is
   wrong by the split ratio -- a 4:1 split turns a +5% target into a +320% one.
   :meth:`PriceHistory.split_factor_after` exists to undo that, and the
   data-quality report counts how many records it rescued.

2. **Which close is "spot at action".** See ``config.SPOT_AT_ACTION_CONVENTION``.
   The default is the last close STRICTLY BEFORE the action date, because the
   action-date close can already contain the market's reaction to the target
   itself.

Dividends are deliberately NOT used. A price target is a price forecast, not a
total-return forecast, so realised performance is measured on the split-adjusted
price series. This understates realised return by the dividend yield and that
understatement is reported rather than corrected.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np

from . import http

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


@dataclass(frozen=True)
class Split:
    effective: date
    ratio: float  # numerator / denominator, e.g. 4.0 for a 4:1 split


class PriceHistory:
    """Split-adjusted daily closes for one symbol, with PIT accessors.

    All accessors are written so that nothing can return a price from after the
    date being asked about. The lookups are the place look-ahead bias creeps
    into a backtest, so they are narrow on purpose.
    """

    def __init__(self, symbol: str, dates, closes, splits=(), retrieved_at=None):
        order = np.argsort(np.asarray(dates))
        self.symbol = symbol.upper()
        self.dates: list[date] = [list(dates)[i] for i in order]
        self.closes: np.ndarray = np.asarray(closes, dtype=float)[order]
        self.splits: list[Split] = sorted(splits, key=lambda s: s.effective)
        self.retrieved_at = retrieved_at or datetime.now(timezone.utc)
        if len(self.dates) != len(self.closes):
            raise ValueError("dates and closes length mismatch")

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def start(self) -> date | None:
        return self.dates[0] if self.dates else None

    @property
    def end(self) -> date | None:
        return self.dates[-1] if self.dates else None

    # -- point-in-time accessors -------------------------------------------

    def close_on_or_before(self, d: date) -> tuple[date, float] | None:
        """Last close at or before ``d``. None if the history starts later."""
        idx = bisect_right(self.dates, d) - 1
        while idx >= 0 and not np.isfinite(self.closes[idx]):
            idx -= 1
        if idx < 0:
            return None
        return self.dates[idx], float(self.closes[idx])

    def close_strictly_before(self, d: date) -> tuple[date, float] | None:
        """Last close strictly before ``d`` -- the default anchoring price."""
        idx = bisect_left(self.dates, d) - 1
        while idx >= 0 and not np.isfinite(self.closes[idx]):
            idx -= 1
        if idx < 0:
            return None
        return self.dates[idx], float(self.closes[idx])

    def close_on_or_after(self, d: date) -> tuple[date, float] | None:
        """First close at or after ``d``. Only for realised-outcome lookups."""
        idx = bisect_left(self.dates, d)
        while idx < len(self.dates) and not np.isfinite(self.closes[idx]):
            idx += 1
        if idx >= len(self.dates):
            return None
        return self.dates[idx], float(self.closes[idx])

    def forward_close(self, d: date, horizon_days: int) -> tuple[date, float] | None:
        """Close ``horizon_days`` after ``d``, or None if it has not happened yet.

        Returns None rather than the last available price when the horizon
        extends past the end of the history. Clamping to the last close is the
        classic way to manufacture a backtest: it silently evaluates a 12-month
        forecast against a 3-month outcome.
        """
        target = d + timedelta(days=horizon_days)
        if self.end is None or target > self.end:
            return None
        return self.close_on_or_after(target)

    def returns(self):
        """(dates, simple daily returns) with non-finite closes dropped."""
        ok = np.isfinite(self.closes)
        d = [self.dates[i] for i in range(len(self.dates)) if ok[i]]
        c = self.closes[ok]
        if len(c) < 2:
            return [], np.empty(0)
        return d[1:], c[1:] / c[:-1] - 1.0

    # -- corporate actions -------------------------------------------------

    def split_factor_after(self, d: date) -> float:
        """Cumulative split ratio effective strictly after ``d``.

        Multiply a back-adjusted price by this to get the as-quoted price of
        the day, or divide an as-quoted price by it to get the back-adjusted
        one. 1.0 when no split intervened.
        """
        factor = 1.0
        for s in self.splits:
            if s.effective > d:
                factor *= s.ratio
        return factor


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def fetch_price_history(
    symbol: str,
    start: date,
    end: date | None = None,
    timeout: float = 40.0,
) -> PriceHistory:
    """Fetch split-adjusted daily closes and split events from Yahoo Finance.

    Verified against the live endpoint. Yahoo is a convenience source, not a
    research-grade one: it silently back-adjusts, it revises, and it has no
    delisted-security coverage worth relying on. The brief's survivorship
    requirement cannot be met with Yahoo alone, and the README says so.
    """
    end = end or datetime.now(timezone.utc).date()
    url = YAHOO_CHART.format(symbol=symbol.upper())
    payload = http.fetch_json(
        url,
        params={
            "period1": _epoch(start - timedelta(days=5)),
            "period2": _epoch(end + timedelta(days=1)),
            "interval": "1d",
            "events": "div,split",
        },
        headers={"User-Agent": "Mozilla/5.0 (compatible; WoC-equity-forecaster/0.1)"},
        timeout=timeout,
    )
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise http.FetchError(f"Yahoo error for {symbol}: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise http.FetchError(f"Yahoo returned no result for {symbol}")
    res = results[0]

    stamps = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    dates, vals = [], []
    for ts, c in zip(stamps, closes):
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        dates.append(d)
        vals.append(float(c) if c is not None else float("nan"))

    splits = []
    for entry in (res.get("events", {}).get("splits") or {}).values():
        num = float(entry.get("numerator", 1) or 1)
        den = float(entry.get("denominator", 1) or 1)
        if den:
            splits.append(
                Split(
                    effective=datetime.fromtimestamp(
                        int(entry["date"]), tz=timezone.utc
                    ).date(),
                    ratio=num / den,
                )
            )

    return PriceHistory(symbol, dates, vals, splits)


def spot_at_action(
    history: PriceHistory, action_date: date, convention: str
) -> tuple[date | None, float | None, str]:
    """Resolve the anchoring price for an action on ``action_date``.

    Returns ``(price_date, price, note)``. ``note`` records which rule fired so
    the choice survives into the output instead of living only in this file.
    """
    if convention == "action_close":
        hit = history.close_on_or_before(action_date)
        note = "action_close"
    elif convention == "prior_close":
        hit = history.close_strictly_before(action_date)
        note = "prior_close"
    else:
        raise ValueError(f"unknown spot convention {convention!r}")
    if hit is None:
        return None, None, f"{note}:unavailable"
    return hit[0], hit[1], note
