"""Does combining platforms actually help?

This is the backtest for the claim the whole dashboard rests on. Every
other piece of measurement in this repo scores ONE source: the calibration
backtest scores Polymarket alone, the concentration and trader-skill work
score Polymarket alone. The dashboard's actual thesis -- that a
volume-weighted mixture of Polymarket, Kalshi and Manifold is a better
forecast than any one of them -- had never been measured. This module
measures it.

--- Why the test window is short, and why that is the data's fault ---

Combining two sources can only be scored where two sources actually quote
the same thing at the same time. For BTC that window is narrow:

  * Polymarket's "Bitcoin price on <date>" events open exactly 7 days
    before they resolve, and every one resolves at 16:00:00Z.
  * Kalshi's KXBTCD ladder is a sequence of ONE-HOUR markets. A ladder
    closing at 16:00:00Z opens at 15:00:00Z.

So the two platforms describe the same future instant for exactly the last
hour before resolution, and nowhere else. Lead times of 6h/24h/72h/144h --
the ones the calibration backtest uses and the ones most of the dashboard
displays -- have Polymarket quotes and no Kalshi quotes at all. LEAD_MINUTES
below therefore samples inside that final hour. Read the results as "does
combining help at very short horizons", which is the only question this
data can answer, not as a verdict on the multi-day cards.

That limitation is itself a finding about the live dashboard, and a sharper
one than it first looks: because aggregation.py groups by CALENDAR DATE,
a live card for date D can blend Polymarket's 16:00Z distribution with
whichever Kalshi ladder closed latest that day -- a different instant, up
to eight hours away, at an asset that moves ~0.4%/hour. This backtest
deliberately pairs the ladder that closes at Polymarket's own resolution
instant, so it measures aggregation rather than that mismatch. See the
report for what that implies.

--- What is compared ---

At each lead time, five forecasts of the same 16:00:00Z BTC price:

  polymarket   the Polymarket event's buckets alone
  kalshi       the KXBTCD ladder alone, differenced into buckets
  aggregate    both, through the REAL engine -- wisdom-dashboard's
               aggregation.aggregate_group, imported rather than
               reimplemented, so this scores the code that actually ships
  naive        a point mass at spot when the forecast was made
  random_walk  spot, widened by trailing realized volatility

scored by CRPS (in dollars, and reducing to |error| for the point
forecast, so all five are comparable), by MAD of the mean and of the
median, and by 68%-interval coverage.

The headline question is not "is the aggregate good" but "does the
aggregate beat the better of its own two inputs" -- a mixture that merely
lands between its inputs has added nothing that picking the better one
would not.
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
# The engine under test, imported from the app rather than reimplemented.
sys.path.insert(0, str(REPO_ROOT / "wisdom-dashboard" / "backend"))

import btc_price_market_calibration as btccal  # noqa: E402
import aggregation  # noqa: E402
from common.distribution import PriceBucket, PriceDistribution  # noqa: E402
from isotonic import isotonic_nonincreasing  # noqa: E402

CACHE_DIR = REPO_ROOT / "data" / "raw" / "aggregate_backtest"
KALSHI_CACHE_DIR = CACHE_DIR / "kalshi"
RESULTS_DIR = REPO_ROOT / "results" / "aggregate_forecast_backtest"

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = "KXBTCD"
USER_AGENT = "Mozilla/5.0 (research backtest; contact via repo)"

# Inside the one hour where both platforms quote the same instant.
LEAD_MINUTES = [60, 45, 30, 15, 5]

# Polymarket's daily BTC events all resolve at this UTC time.
RESOLVE_HOUR_UTC = 16

MIN_STRIKE_QUOTES = 20  # a ladder with fewer usable strikes is not a distribution


def _request_json(url: str, retries: int = 4, timeout: float = 30.0) -> object | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
                continue
            return None
    return None


# ---------------------------------------------------------------------------
# 1. KALSHI: the hourly ladder closing at Polymarket's own resolution instant
# ---------------------------------------------------------------------------

def kalshi_event_ticker(resolve_dt: datetime) -> str:
    """KXBTCD-YYMMMDDHH, where HH is the close hour in US/Eastern.

    Derived from the UTC instant rather than hardcoded, so this stays
    correct across the DST change (16:00Z is 12:00 ET in EDT, 11:00 ET in
    EST) instead of silently pointing at the wrong hour after November.
    """
    # US Eastern is UTC-4 (EDT) between the 2nd Sunday in March and the 1st
    # Sunday in November, UTC-5 (EST) otherwise. Computed rather than
    # imported so this module keeps its stdlib-only footprint.
    year = resolve_dt.year
    march = date(year, 3, 8)
    dst_start = march + timedelta(days=(6 - march.weekday()) % 7)  # 2nd Sunday in March
    nov = date(year, 11, 1)
    dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)        # 1st Sunday in November
    offset = 4 if dst_start <= resolve_dt.date() < dst_end else 5
    eastern = resolve_dt - timedelta(hours=offset)
    return f"{KALSHI_SERIES}-{eastern.strftime('%y%b%d%H').upper()}"


@dataclass
class KalshiStrike:
    strike: float
    # (unix_ts, yes_bid, yes_ask) per minute over the market's final hour
    quotes: list[tuple[int, float, float]]


@dataclass
class KalshiLadder:
    event_ticker: str
    close_ts: int
    strikes: list[KalshiStrike]


def fetch_kalshi_ladder(resolve_dt: datetime) -> KalshiLadder | None:
    """The full KXBTCD ladder closing at `resolve_dt`, with per-minute
    bid/ask for each strike over its final hour. Cached whole, since it is
    ~190 requests per date."""
    ticker = kalshi_event_ticker(resolve_dt)
    cache_path = KALSHI_CACHE_DIR / f"{ticker}.json"
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
        if raw is None:
            return None
        return KalshiLadder(
            event_ticker=raw["event_ticker"],
            close_ts=raw["close_ts"],
            strikes=[KalshiStrike(strike=s["strike"], quotes=[tuple(q) for q in s["quotes"]]) for s in raw["strikes"]],
        )

    data = _request_json(f"{KALSHI_BASE}/markets?event_ticker={ticker}&limit=500")
    markets = (data or {}).get("markets", [])
    resolve_ts = int(resolve_dt.timestamp())
    markets = [
        m for m in markets
        if m.get("close_time")
        and int(datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp()) == resolve_ts
        and m.get("floor_strike") is not None
    ]
    if len(markets) < MIN_STRIKE_QUOTES:
        KALSHI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("null")
        return None

    def fetch_one(m: dict) -> KalshiStrike | None:
        params = urllib.parse.urlencode({
            "start_ts": resolve_ts - 3600, "end_ts": resolve_ts, "period_interval": 1,
        })
        res = _request_json(f"{KALSHI_BASE}/series/{KALSHI_SERIES}/markets/{m['ticker']}/candlesticks?{params}")
        candles = (res or {}).get("candlesticks", [])
        quotes = []
        for c in candles:
            try:
                bid = float(c["yes_bid"]["close_dollars"])
                ask = float(c["yes_ask"]["close_dollars"])
                quotes.append((int(c["end_period_ts"]), bid, ask))
            except (KeyError, TypeError, ValueError):
                continue
        return KalshiStrike(strike=float(m["floor_strike"]), quotes=quotes) if quotes else None

    strikes: list[KalshiStrike] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for fut in as_completed([pool.submit(fetch_one, m) for m in markets]):
            s = fut.result()
            if s is not None:
                strikes.append(s)
    strikes.sort(key=lambda s: s.strike)

    KALSHI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "event_ticker": ticker, "close_ts": resolve_ts,
        "strikes": [{"strike": s.strike, "quotes": s.quotes} for s in strikes],
    }))
    return KalshiLadder(event_ticker=ticker, close_ts=resolve_ts, strikes=strikes)


def _quote_at_or_before(quotes: list[tuple[int, float, float]], ts: int) -> tuple[float, float] | None:
    usable = [(b, a) for t, b, a in quotes if t <= ts]
    return usable[-1] if usable else None


def kalshi_distribution(ladder: KalshiLadder, query_ts: int, target_date: date) -> PriceDistribution | None:
    """Turn the ladder's P(price > strike) sequence at `query_ts` into
    bucket probabilities, the same way adapters/kalshi.py does: midpoint
    quote per strike, isotonic regression across the whole curve to enforce
    monotonicity, then difference into buckets."""
    rungs: list[tuple[float, float, float]] = []  # (strike, p_gt, confidence weight)
    for s in ladder.strikes:
        q = _quote_at_or_before(s.quotes, query_ts)
        if q is None:
            continue
        bid, ask = q
        if ask < bid:
            continue
        spread = max(ask - bid, 1e-4)
        rungs.append((s.strike, (bid + ask) / 2.0, 1.0 / spread))
    if len(rungs) < MIN_STRIKE_QUOTES:
        return None

    rungs.sort(key=lambda r: r[0])
    strikes = [r[0] for r in rungs]
    fitted = isotonic_nonincreasing([r[1] for r in rungs], [r[2] for r in rungs])

    buckets = [PriceBucket(low=None, high=strikes[0], prob=max(0.0, 1.0 - fitted[0]), label=f"< {strikes[0]:,.0f}")]
    for i in range(len(strikes) - 1):
        buckets.append(PriceBucket(
            low=strikes[i], high=strikes[i + 1],
            prob=max(0.0, fitted[i] - fitted[i + 1]),
            label=f"{strikes[i]:,.0f}-{strikes[i + 1]:,.0f}",
        ))
    buckets.append(PriceBucket(low=strikes[-1], high=None, prob=max(0.0, fitted[-1]), label=f"> {strikes[-1]:,.0f}"))

    total = sum(b.prob for b in buckets)
    if total <= 0:
        return None
    return PriceDistribution(
        asset="BTC", source_type="prediction_market", source_name="kalshi",
        target_date=target_date, period_label=str(target_date), buckets=buckets,
        # Weight: this backtest deliberately gives both sources equal weight
        # (see score_date) so the comparison isolates the aggregation math
        # rather than being decided by a volume figure the historical API
        # does not expose per-minute.
        weight=1.0, volume=None,
        resolve_datetime_utc=datetime.fromtimestamp(ladder.close_ts, timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# 2. POLYMARKET: the same instant, at the same sampling resolution
#
# The calibration backtest caches Polymarket bucket history at fidelity=60
# (hourly), which is fine for its 6h-144h lead times and useless here: at a
# 5-minute lead an hourly series can hand back a quote 59 minutes stale,
# while Kalshi's candlesticks are one minute old. Comparing those would
# measure sampling resolution, not forecast quality, and would flatter
# Kalshi for no real reason. So the final hour is refetched at fidelity=1
# into this module's own cache, leaving the calibration module's cache
# alone.
# ---------------------------------------------------------------------------

POLYMARKET_CACHE_DIR = CACHE_DIR / "polymarket_final_hour"


@dataclass
class PolymarketFinalHour:
    resolve_ts: int
    # bucket label -> (unix_ts, yes_price) per minute over the final hour
    quotes: dict[str, list[tuple[int, float]]]


def fetch_polymarket_final_hour(event: btccal.ResolvedEvent) -> PolymarketFinalHour | None:
    cache_path = POLYMARKET_CACHE_DIR / f"{event.date}.json"
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
        if raw is None:
            return None
        return PolymarketFinalHour(
            resolve_ts=raw["resolve_ts"],
            quotes={k: [tuple(q) for q in v] for k, v in raw["quotes"].items()},
        )

    ev = btccal.fetch_event(event.date)
    if ev is None:
        return None
    label_to_token: dict[str, str] = {}
    for m in ev.get("markets", []):
        label = m.get("groupItemTitle") or m.get("question", "")
        low, high = btccal._parse_bucket_label(label)
        if low is None and high is None:
            continue
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except (ValueError, TypeError):
            continue
        if toks:
            label_to_token[label] = toks[0]

    def fetch_one(item):
        label, token = item
        data = btccal._request_json(
            f"{btccal.CLOB_BASE}/prices-history?market={token}"
            f"&startTs={event.resolve_ts - 3600}&endTs={event.resolve_ts}&fidelity=1"
        )
        hist = (data or {}).get("history", [])
        return label, [(int(p["t"]), float(p["p"])) for p in hist if "t" in p and "p" in p]

    quotes: dict[str, list[tuple[int, float]]] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for fut in as_completed([pool.submit(fetch_one, it) for it in label_to_token.items()]):
            label, series = fut.result()
            if series:
                quotes[label] = series

    POLYMARKET_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not quotes:
        cache_path.write_text("null")
        return None
    cache_path.write_text(json.dumps({"resolve_ts": event.resolve_ts, "quotes": quotes}))
    return PolymarketFinalHour(resolve_ts=event.resolve_ts, quotes=quotes)


def polymarket_distribution(
    event: btccal.ResolvedEvent,
    query_ts: int,
    final_hour: PolymarketFinalHour | None = None,
) -> PriceDistribution | None:
    raw = []
    for b in event.buckets:
        series = final_hour.quotes.get(b.label) if final_hour else None
        p = btccal._price_at_or_before(series, query_ts) if series else None
        if p is None:  # fall back to the hourly series where minute data is missing
            p = btccal._price_at_or_before(b.history, query_ts)
        if p is not None:
            raw.append((b, max(p, 0.0)))
    if len(raw) < 3:
        return None
    total = sum(p for _, p in raw)
    if total <= 0:
        return None
    buckets = [
        PriceBucket(low=b.low, high=b.high, prob=p / total, label=b.label)
        for b, p in raw
    ]
    return PriceDistribution(
        asset="BTC", source_type="prediction_market", source_name="polymarket",
        target_date=event.date, period_label=str(event.date), buckets=buckets,
        weight=1.0, volume=None,
        resolve_datetime_utc=datetime.fromtimestamp(event.resolve_ts, timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# 2b. SPOT, AT THE SAME RESOLUTION AS EVERYTHING ELSE
#
# btccal.fetch_spot_series is hourly, which silently breaks the naive
# baseline here: asked for spot "5 minutes before 16:00" it returns the
# nearest hourly point, which is the 16:00 print -- the realized value
# itself. The baseline then scores a perfect zero and the whole comparison
# is meaningless. Inside the final hour spot has to be sampled per minute
# too, and strictly at or before the query instant.
# ---------------------------------------------------------------------------

SPOT_MINUTE_CACHE_DIR = CACHE_DIR / "spot_minutes"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"


def fetch_spot_minutes(resolve_ts: int) -> dict[int, float]:
    """{unix_ts -> BTC price at that minute} over the final hour, keyed by
    the instant each candle CLOSES (Coinbase labels candles by start)."""
    cache_path = SPOT_MINUTE_CACHE_DIR / f"{resolve_ts}.json"
    if cache_path.exists():
        return {int(k): float(v) for k, v in json.loads(cache_path.read_text()).items()}

    start = datetime.fromtimestamp(resolve_ts - 3600, timezone.utc)
    end = datetime.fromtimestamp(resolve_ts, timezone.utc)
    params = urllib.parse.urlencode({
        "granularity": 60, "start": start.isoformat(), "end": end.isoformat(),
    })
    rows = _request_json(f"{COINBASE_CANDLES}?{params}") or []
    out: dict[int, float] = {}
    for r in rows:
        if isinstance(r, list) and len(r) >= 5:
            out[int(r[0]) + 60] = float(r[4])  # candle close instant -> close price
    SPOT_MINUTE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out))
    time.sleep(0.2)
    return out


def spot_minute_at(minutes: dict[int, float], ts: int, max_staleness: int = 600) -> float | None:
    """Latest price at or before `ts`. Strictly backward-looking -- the
    point of this function is that the naive baseline must not be allowed
    to see the future."""
    usable = [t for t in minutes if t <= ts]
    if not usable:
        return None
    best = max(usable)
    return minutes[best] if ts - best <= max_staleness else None


# ---------------------------------------------------------------------------
# 3. SCORING
# ---------------------------------------------------------------------------

def crps_from_grid(grid_edges: np.ndarray, pdf: np.ndarray, realized: float) -> float:
    """CRPS of a piecewise-constant density on a grid:
    integral over x of (F(x) - 1{x >= y})^2 dx."""
    edges = np.asarray(grid_edges, dtype=float)
    cdf = np.concatenate(([0.0], np.cumsum(np.asarray(pdf, dtype=float))))
    centers = (edges[:-1] + edges[1:]) / 2.0
    widths = np.diff(edges)
    cdf_mid = (cdf[:-1] + cdf[1:]) / 2.0
    heaviside = (centers >= realized).astype(float)
    return float(np.sum((cdf_mid - heaviside) ** 2 * widths))


@dataclass
class Scored:
    name: str
    crps: float
    abs_error_mean: float
    abs_error_median: float
    # None where the forecast has no interval to check. The naive point
    # forecast is the case: it is a point mass, so "did the realized price
    # fall inside its 68% interval" has no answer, and reporting it as a
    # miss would print a meaningless 0% next to real coverage numbers.
    ci68_hit: bool | None


def score_forecast(name: str, forecast, realized: float) -> Scored:
    return Scored(
        name=name,
        crps=crps_from_grid(np.array(forecast.grid_edges), np.array(forecast.pdf), realized),
        abs_error_mean=abs(forecast.mean - realized),
        abs_error_median=abs(forecast.median - realized),
        ci68_hit=bool(forecast.ci_68[0] <= realized <= forecast.ci_68[1]),
    )


@dataclass
class DateResult:
    target_date: date
    lead_minutes: int
    realized: float
    spot_at_lead: float | None
    scores: dict[str, Scored] = field(default_factory=dict)
    # Polymarket's share of the mixture -> CRPS of the resulting blend.
    blend_crps: dict[float, float] = field(default_factory=dict)


def score_date(
    event: btccal.ResolvedEvent,
    ladder: KalshiLadder | None,
    spot,
    lead_minutes: int,
    pm_final_hour: PolymarketFinalHour | None = None,
    spot_minutes: dict[int, float] | None = None,
) -> DateResult | None:
    """Score every forecast for one (date, lead time). Returns None unless
    BOTH platforms quote -- a date where only one does cannot say anything
    about whether combining helps."""
    query_ts = event.resolve_ts - lead_minutes * 60
    minutes = spot_minutes if spot_minutes is not None else {}
    realized = spot_minute_at(minutes, event.resolve_ts) or btccal.spot_at(spot, event.resolve_ts)
    if realized is None:
        return None

    pm = polymarket_distribution(event, query_ts, pm_final_hour)
    k = kalshi_distribution(ladder, query_ts, event.date) if ladder else None
    if pm is None or k is None:
        return None

    res = DateResult(
        target_date=event.date, lead_minutes=lead_minutes, realized=realized,
        # Minute data only -- never the hourly series, which inside the
        # final hour would hand back the resolution price itself.
        spot_at_lead=spot_minute_at(minutes, query_ts),
    )

    single = {"polymarket": [pm], "kalshi": [k], "aggregate": [pm, k]}
    for name, dists in single.items():
        fc = aggregation.aggregate_group(dists)
        if fc is None:
            return None
        res.scores[name] = score_forecast(name, fc, realized)

    # Same two distributions, blended at a range of fixed weights.
    for w in BLEND_WEIGHTS:
        pm_w = replace(pm, weight=max(w, 1e-9))
        k_w = replace(k, weight=max(1.0 - w, 1e-9))
        fc = aggregation.aggregate_group([pm_w, k_w])
        if fc is not None:
            res.blend_crps[w] = crps_from_grid(np.array(fc.grid_edges), np.array(fc.pdf), realized)

    if res.spot_at_lead is not None:
        # A point forecast: CRPS collapses to absolute error.
        err = abs(res.spot_at_lead - realized)
        res.scores["naive"] = Scored("naive", err, err, err, None)
        sigma = btccal.realized_vol_sigma(spot, query_ts, max(lead_minutes / 60.0, 1e-3))
        if sigma is not None and sigma > 0:
            half = 0.9944578832097535 * sigma
            res.scores["random_walk"] = Scored(
                "random_walk",
                btccal.crps_normal(res.spot_at_lead, sigma, realized),
                abs(res.spot_at_lead - realized),
                abs(res.spot_at_lead - realized),
                bool(res.spot_at_lead - half <= realized <= res.spot_at_lead + half),
            )
    return res


FORECAST_NAMES = ["polymarket", "kalshi", "aggregate", "naive", "random_walk"]

# Blend weights swept in score_date, as Polymarket's share of the mixture
# (so 0.0 is Kalshi alone, 1.0 is Polymarket alone, 0.5 is the equal-weight
# "aggregate" above). This is what turns "the equal-weight mixture lost"
# into a statement about mixing in general: if CRPS is monotone in this
# weight, no FIXED blend of the two beats simply taking the better one, and
# the dashboard's volume-based weighting cannot rescue it either -- volume
# would just be picking a point on this curve, and it does not know which
# end is better.
BLEND_WEIGHTS = [0.0, 0.25, 0.5, 0.75, 1.0]


def summarize(results: list[DateResult]) -> list[dict]:
    """Per lead time: each forecast's mean CRPS/MAD/coverage, plus the only
    number that settles the dashboard's thesis -- how often the aggregate
    beats the better of its own two inputs."""
    out = []
    by_lead: dict[int, list[DateResult]] = {}
    for r in results:
        by_lead.setdefault(r.lead_minutes, []).append(r)

    for lead in sorted(by_lead, reverse=True):
        rows = by_lead[lead]
        entry: dict = {"lead_minutes": lead, "n_dates": len(rows), "forecasts": {}}
        for name in FORECAST_NAMES:
            scored = [r.scores[name] for r in rows if name in r.scores]
            if not scored:
                continue
            hits = [s.ci68_hit for s in scored if s.ci68_hit is not None]
            entry["forecasts"][name] = {
                "n": len(scored),
                "crps": float(np.mean([s.crps for s in scored])),
                "mad_mean": float(np.mean([s.abs_error_mean for s in scored])),
                "mad_median": float(np.mean([s.abs_error_median for s in scored])),
                "ci68_coverage": float(np.mean(hits)) if hits else None,
            }

        blended = [r for r in rows if len(r.blend_crps) == len(BLEND_WEIGHTS)]
        if blended:
            entry["blend_sweep"] = {
                "n": len(blended),
                "polymarket_share_to_crps": {
                    str(w): float(np.mean([r.blend_crps[w] for r in blended])) for w in BLEND_WEIGHTS
                },
            }

        paired = [r for r in rows if {"polymarket", "kalshi", "aggregate"} <= set(r.scores)]
        if paired:
            best_single = [min(r.scores["polymarket"].crps, r.scores["kalshi"].crps) for r in paired]
            agg = [r.scores["aggregate"].crps for r in paired]
            entry["aggregate_vs_best_single"] = {
                "n": len(paired),
                "aggregate_crps": float(np.mean(agg)),
                "best_single_crps": float(np.mean(best_single)),
                "aggregate_beats_best_single_rate": float(np.mean([a < b for a, b in zip(agg, best_single)])),
                # The fair benchmark: you cannot know in advance which single
                # source will win, so the honest comparison is against each
                # source on its own, not against an oracle.
                "aggregate_beats_polymarket_rate": float(
                    np.mean([r.scores["aggregate"].crps < r.scores["polymarket"].crps for r in paired])
                ),
                "aggregate_beats_kalshi_rate": float(
                    np.mean([r.scores["aggregate"].crps < r.scores["kalshi"].crps for r in paired])
                ),
            }
        out.append(entry)
    return out


def run_backtest(end_date: date, max_dates: int | None = None, max_workers: int = 4) -> tuple[list[DateResult], dict]:
    dates = btccal.discover_resolved_dates(end_date)
    if max_dates:
        dates = dates[-max_dates:]

    events = []
    for d in dates:
        ev = btccal.build_resolved_event(d)
        if ev is not None:
            events.append(ev)

    if not events:
        return [], {}

    spot = btccal.fetch_spot_series(
        min(e.date for e in events) - timedelta(days=40),
        max(e.date for e in events) + timedelta(days=2),
    )

    def sources_for(ev):
        return (
            ev,
            fetch_kalshi_ladder(datetime.fromtimestamp(ev.resolve_ts, timezone.utc)),
            fetch_polymarket_final_hour(ev),
            fetch_spot_minutes(ev.resolve_ts),
        )

    triples = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for fut in as_completed([pool.submit(sources_for, e) for e in events]):
            triples.append(fut.result())

    results: list[DateResult] = []
    for ev, ladder, pm_hour, spot_min in triples:
        for lead in LEAD_MINUTES:
            r = score_date(ev, ladder, spot, lead, pm_hour, spot_min)
            if r is not None:
                results.append(r)

    meta = {
        "n_polymarket_events": len(events),
        "n_with_kalshi_ladder": sum(1 for _, l, _, _ in triples if l is not None),
        "n_with_polymarket_minute_data": sum(1 for _, _, h, _ in triples if h is not None),
        "n_with_minute_spot": sum(1 for _, _, _, m in triples if m),
        "spot_source": btccal.SPOT_SOURCE_USED,
        "date_range": [str(min(e.date for e in events)), str(max(e.date for e in events))],
        "lead_minutes": LEAD_MINUTES,
    }
    return results, meta
