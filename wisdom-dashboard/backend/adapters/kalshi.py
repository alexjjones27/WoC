"""Kalshi adapter (Phase 1, live).

Two series are used, both confirmed live (2026-09-13) to be genuine
point-in-time price distributions -- "what will price BE at this exact
timestamp" -- as opposed to a touch/range-within-period product:

  KXBTCD ("Bitcoin price on <date> at <time>?"): a ladder of binary
  "price > $strike" markets, ~$100 apart, all sharing one close time (one
  `event_ticker` = one ladder = one point in time). GET
  /trade-api/v2/markets?event_ticker=KXBTCD-26SEP1313 returns 188 strikes
  from $67,600 to $86,300 with a clean, monotonically-decreasing
  "P(price > strike)" (yes_bid/yes_ask) as strike increases -- a real CDF
  ladder, unlike Polymarket's pre-bucketed ranges. We turn it into a PDF by
  differencing consecutive strikes' implied P(price > strike). Near-dated
  only: currently open ladders span at most a few days out.

  KXBTCY ("Bitcoin price at the end of <year>"): pre-bucketed ranges
  (`strike_type` "less"/"between"/"greater", e.g. "60,000 to 64,999.99"),
  same shape as Polymarket's range events -- each bucket's Yes price *is*
  its probability directly, no differencing needed. This is the one
  far-horizon point either platform currently offers with real
  point-in-time semantics: as of 2026-09-13 there is exactly one open
  KXBTCY event, resolving Jan 1, 2027 (i.e. "end of this year"), $25M+
  combined volume across 28 buckets from <$20k to >$150k.

No auth is required for either series' public GET endpoints (confirmed
live).

Two refinements on top of the raw quotes, both standard techniques for
turning noisy order-book quotes into probabilities (see
../../results/btc_price_market_calibration/ for the empirical calibration
work this sits alongside):
  - MICROPRICE instead of naive (bid+ask)/2: each strike's probability is
    the size-weighted price `bid * ask_size/(bid_size+ask_size) + ask *
    bid_size/(bid_size+ask_size)` -- weighting each side by the OTHER
    side's resting size, the standard market-microstructure convention
    (heavy size stacked at the ask pulls the estimate toward the bid, since
    that's the side more likely to get run through). Falls back to plain
    midpoint when depth data isn't available.
  - ISOTONIC REGRESSION on the KXBTCD ladder specifically: P(price>strike)
    must be non-increasing in strike, but adjacent live quotes can violate
    that from ordinary bid/ask noise. Previously this adapter just clipped
    each negative difference to 0 locally; it now fits the closest
    monotone curve to the WHOLE ladder at once (weighted by each strike's
    own depth/spread-derived confidence) via ../../isotonic.py before
    differencing -- see that module's docstring for why this is better
    than local clipping.

Excluded from `fetch()` (the point-in-time pipeline): KXBTCMAXM/KXBTCMAXQ
("how high will Bitcoin get this month/quarter" -- a one-touch max within
the period) and KXBTCMINY/KXBTCMAXY ("will Bitcoin be above/below $X by
[date]" -- a touch-before-expiry ladder, not a price-at-expiry read). Same
reasoning as polymarket.py: only same-semantics ("price at a fixed date")
sources go into `fetch()`'s point-in-time aggregate.

KXBTCMAXY (touch above) and KXBTCMINY (touch below) ARE used, but
separately, via `fetch_touch()` -- never merged into `fetch()`'s output.
Confirmed live (2026-09-13): both are flat lists of "Will Bitcoin be
above/below $X by [date]" markets (`strike_type` "greater"/"less"), no
event-ticker ladder-grouping needed since each market already carries its
own strike and shares one close date (2027-01-01, same as KXBTCY's -- these
three series describe the same year-end horizon from three different
angles: price-at-expiry, touch-above, touch-below).

Two-step fetch per series, matching Kalshi's own API shape:
  1. GET /events?series_ticker=<X>&status=open -- cheap, no nested
     strikes, just tells us which close times currently have an open market.
  2. GET /markets?event_ticker=<ticker> -- the full ladder/bucket set for
     one close time.
KXBTCD opens on a rolling, not-strictly-daily basis (observed: 2 open
"today" plus 1 several days out, not one per day) -- so, like Polymarket,
this adapter reports whatever's currently live rather than a fixed
calendar grid. Where more than one KXBTCD ladder closes on the same
calendar date, only the latest close time for that date is kept
(documented simplification: avoids silently blending an early-afternoon
and an end-of-day forecast for the same "period" into one number).
"""
from __future__ import annotations

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
from isotonic import isotonic_nonincreasing

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
LADDER_SERIES_TICKER = "KXBTCD"
RANGE_SERIES_TICKER = "KXBTCY"
TOUCH_ABOVE_SERIES_TICKER = "KXBTCMAXY"
TOUCH_BELOW_SERIES_TICKER = "KXBTCMINY"


def _period_label(dt: datetime) -> str:
    return dt.strftime("%b %-d, %Y")


# Confidence-weight floor: prevents a near-zero spread (which can happen on
# a thin, barely-quoted strike) from producing a near-infinite weight in
# the isotonic fit below. Spread is in probability units (0-1), so this is
# small on that scale; tune if strikes with tiny-but-real spreads are
# getting over-trusted.
SPREAD_WEIGHT_LAMBDA = 0.0025


def _market_price_and_weight(m: dict) -> tuple[float | None, float]:
    """Returns (microprice, confidence_weight) for one strike's market
    object. Microprice weights each side by the OPPOSITE side's resting
    size (standard microstructure convention -- see module docstring);
    falls back to a plain midpoint when size data isn't usable. Weight is
    depth / (spread^2 + SPREAD_WEIGHT_LAMBDA), used only to decide how much
    to trust this strike when fitting the isotonic curve (see
    isotonic_nonincreasing below) -- NOT the same as the distribution's
    overall cross-platform weight (that stays volume-based, see
    common/distribution.py's PriceDistribution.weight)."""
    try:
        bid, ask = float(m.get("yes_bid_dollars")), float(m.get("yes_ask_dollars"))
    except (TypeError, ValueError):
        return None, 0.0
    if bid == 0.0 and ask == 0.0:
        return None, 0.0
    if ask < bid:  # crossed/empty book edge case
        bid, ask = ask, bid
    spread = max(ask - bid, 0.0)

    try:
        bid_size, ask_size = float(m.get("yes_bid_size_fp") or 0.0), float(m.get("yes_ask_size_fp") or 0.0)
    except (TypeError, ValueError):
        bid_size = ask_size = 0.0

    depth = min(bid_size, ask_size) if (bid_size > 0 and ask_size > 0) else max(bid_size, ask_size)
    if bid_size + ask_size > 0:
        price = bid * (ask_size / (bid_size + ask_size)) + ask * (bid_size / (bid_size + ask_size))
    else:
        price = (bid + ask) / 2

    weight = depth / (spread ** 2 + SPREAD_WEIGHT_LAMBDA)
    return price, weight


class KalshiAdapter(SourceAdapter):
    name = "kalshi"
    source_type = "prediction_market"

    def fetch(self, asset: str) -> SourceFetchResult:
        if asset != "BTC":
            return SourceFetchResult(source_name=self.name, source_type=self.source_type)

        # (event_ticker, close_iso, which parser) for every currently-open
        # market across both series.
        jobs: list[tuple[str, str, str]] = []
        errors: list[str] = []
        try:
            for ticker, close_iso in self._discover_latest_per_date(LADDER_SERIES_TICKER).values():
                jobs.append((ticker, close_iso, "ladder"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{LADDER_SERIES_TICKER} discovery failed: {exc}")
        try:
            for ticker, close_iso in self._discover_latest_per_date(RANGE_SERIES_TICKER).values():
                jobs.append((ticker, close_iso, "range"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{RANGE_SERIES_TICKER} discovery failed: {exc}")

        if not jobs and errors:
            return SourceFetchResult(source_name=self.name, source_type=self.source_type, error="; ".join(errors))

        distributions: list[PriceDistribution] = []
        fetched_at = datetime.now(timezone.utc).isoformat()

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(self._fetch_ladder if kind == "ladder" else self._fetch_range, ticker, close_iso): ticker
                for ticker, close_iso, kind in jobs
            }
            for fut in as_completed(futures):
                ticker = futures[fut]
                try:
                    dist = fut.result()
                    if dist is not None:
                        dist.fetched_at_utc = fetched_at
                        distributions.append(dist)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{ticker}: {exc}")

        distributions.sort(key=lambda d: d.target_date)
        return SourceFetchResult(
            source_name=self.name,
            source_type=self.source_type,
            distributions=distributions,
            error="; ".join(errors) if errors and not distributions else None,
        )

    def _discover_latest_per_date(self, series_ticker: str) -> dict:
        """Returns {calendar_date: (event_ticker, close_time_iso)} for one
        series, keeping only the latest close time per date."""
        latest: dict = {}
        cursor = None
        for _ in range(10):  # hard cap: these series are sparse, not paginated deep
            params = {"series_ticker": series_ticker, "status": "open", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            page = get_json(f"{KALSHI_BASE}/events", params)
            for ev in page.get("events", []):
                close_iso = ev.get("strike_date") or ev.get("close_time")
                ticker = ev.get("event_ticker")
                if not close_iso or not ticker:
                    continue
                dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
                d = dt.date()
                if d not in latest or dt > datetime.fromisoformat(latest[d][1].replace("Z", "+00:00")):
                    latest[d] = (ticker, close_iso)
            cursor = page.get("cursor")
            if not cursor:
                break
        return latest

    def _fetch_ladder(self, event_ticker: str, close_iso: str) -> PriceDistribution | None:
        """KXBTCD: a threshold ladder ('price > $strike'). Each strike's
        probability is a depth/spread microprice (_market_price_and_weight);
        the whole P(>strike) sequence is then fit to the closest monotone
        (non-increasing) curve via isotonic regression BEFORE differencing
        into bucket probabilities -- see module docstring."""
        page = get_json(f"{KALSHI_BASE}/markets", {"event_ticker": event_ticker, "limit": 1000})
        markets = page.get("markets", [])
        rungs = []
        for m in markets:
            strike = m.get("floor_strike")
            if strike is None or m.get("strike_type") != "greater":
                continue
            p_gt, weight = _market_price_and_weight(m)
            if p_gt is None:
                continue
            rungs.append((float(strike), p_gt, weight, m))
        if len(rungs) < 2:
            return None
        rungs.sort(key=lambda r: r[0])

        smoothed = isotonic_nonincreasing([r[1] for r in rungs], [r[2] for r in rungs])
        strikes = [r[0] for r in rungs]

        buckets: list[PriceBucket] = []
        buckets.append(PriceBucket(low=None, high=strikes[0], prob=max(0.0, 1.0 - smoothed[0]), label=f"<{strikes[0]:,.0f}"))
        for i in range(len(strikes) - 1):
            mass = max(0.0, smoothed[i] - smoothed[i + 1])
            buckets.append(PriceBucket(low=strikes[i], high=strikes[i + 1], prob=mass, label=f"{strikes[i]:,.0f}-{strikes[i+1]:,.0f}"))
        buckets.append(PriceBucket(low=strikes[-1], high=None, prob=max(0.0, smoothed[-1]), label=f">{strikes[-1]:,.0f}"))

        return self._finalize(
            buckets, [(r[0], r[1], r[3]) for r in rungs], close_iso,
            source_url=f"https://kalshi.com/markets/{LADDER_SERIES_TICKER.lower()}",
            raw_note=(
                "Threshold ladder ('price > $strike'); each strike priced by depth/spread microprice, fit to a "
                "monotone curve via isotonic regression, then differenced into bucket probabilities."
            ),
        )

    def _fetch_range(self, event_ticker: str, close_iso: str) -> PriceDistribution | None:
        """KXBTCY: pre-bucketed mutually-exclusive ranges (like Polymarket's
        range events) -- each bucket's Yes price is its probability directly."""
        page = get_json(f"{KALSHI_BASE}/markets", {"event_ticker": event_ticker, "limit": 1000})
        markets = page.get("markets", [])
        buckets: list[PriceBucket] = []
        rungs = []  # reused only for volume/OI/liquidity totals below
        for m in markets:
            strike_type = m.get("strike_type")
            p, _weight = _market_price_and_weight(m)
            if p is None:
                continue
            if strike_type == "less":
                low, high = None, m.get("cap_strike")
            elif strike_type == "greater":
                low, high = m.get("floor_strike"), None
            elif strike_type == "between":
                low, high = m.get("floor_strike"), m.get("cap_strike")
            else:
                continue
            if low is None and high is None:
                continue
            buckets.append(PriceBucket(low=low, high=high, prob=p, label=m.get("yes_sub_title", "")))
            rungs.append((0.0, 0.0, m))
        if len(buckets) < 2:
            return None

        return self._finalize(
            buckets, rungs, close_iso,
            source_url=f"https://kalshi.com/markets/{RANGE_SERIES_TICKER.lower()}",
            raw_note="Pre-bucketed mutually-exclusive price range; bucket Yes-price = bucket probability directly.",
        )

    def _finalize(self, buckets: list[PriceBucket], rungs: list, close_iso: str, source_url: str, raw_note: str) -> PriceDistribution | None:
        total = sum(b.prob for b in buckets)
        if total <= 0:
            return None
        for b in buckets:
            b.prob /= total

        # Kalshi contracts settle $1; volume_fp/open_interest_fp are contract
        # counts, treated here as an approximate USD-notional proxy (documented
        # assumption -- not exact dollar volume the way Polymarket's is).
        total_volume = sum(float(m.get("volume_fp") or 0.0) for _, _, m in rungs)
        total_oi = sum(float(m.get("open_interest_fp") or 0.0) for _, _, m in rungs)
        total_liq = sum(float(m.get("liquidity_dollars") or 0.0) for _, _, m in rungs)

        target_dt = datetime.fromisoformat(close_iso.replace("Z", "+00:00"))
        return PriceDistribution(
            asset="BTC",
            source_type=self.source_type,
            source_name=self.name,
            target_date=target_dt.date(),
            period_label=_period_label(target_dt),
            buckets=buckets,
            weight=total_volume,
            resolve_datetime_utc=close_iso,
            volume=total_volume,
            open_interest=total_oi,
            liquidity=total_liq,
            source_url=source_url,
            raw_note=raw_note,
        )

    def fetch_touch(self, asset: str) -> TouchFetchResult:
        if asset != "BTC":
            return TouchFetchResult(source_name=self.name)
        errors: list[str] = []
        thresholds: list[TouchThreshold] = []
        expiry: str | None = None
        for series, direction in ((TOUCH_ABOVE_SERIES_TICKER, "above"), (TOUCH_BELOW_SERIES_TICKER, "below")):
            try:
                page = get_json(f"{KALSHI_BASE}/markets", {"series_ticker": series, "status": "open", "limit": 100})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{series}: {exc}")
                continue
            for m in page.get("markets", []):
                strike = m.get("floor_strike") if direction == "above" else m.get("cap_strike")
                if strike is None:
                    continue
                p, _weight = _market_price_and_weight(m)
                if p is None:
                    continue
                vol = float(m.get("volume_fp") or 0.0)
                thresholds.append(TouchThreshold(direction=direction, price=float(strike), prob_touch=p, volume=vol, label=m.get("yes_sub_title", "")))
                expiry = expiry or m.get("close_time")

        if not thresholds:
            return TouchFetchResult(source_name=self.name, error="; ".join(errors) if errors else None)

        thresholds.sort(key=lambda t: (t.direction, t.price))
        target_dt = datetime.fromisoformat((expiry or "").replace("Z", "+00:00"))
        touch = TouchForecast(
            asset="BTC",
            source_name=self.name,
            expiry_date=target_dt.date(),
            period_label=_period_label(target_dt),
            thresholds=thresholds,
            total_volume=sum(t.volume or 0.0 for t in thresholds),
            source_url=f"https://kalshi.com/markets/{TOUCH_ABOVE_SERIES_TICKER.lower()}",
            raw_note="Touch probability: chance BTC crosses this price at ANY point before expiry, not price-at-expiry.",
            resolve_datetime_utc=expiry,
        )
        return TouchFetchResult(source_name=self.name, touches=[touch], error="; ".join(errors) if errors else None)
