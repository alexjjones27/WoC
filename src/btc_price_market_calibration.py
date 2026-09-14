"""BTC prediction-market calibration backtest.

Measures, purely empirically, how accurate and how well-calibrated
Polymarket's recurring "Bitcoin price on <date>?" daily range market has
actually been -- at several lead times before resolution -- against the
realized BTC price. This is the necessary first step before building any
correction into wisdom-dashboard/backend/aggregation.py's live forecast,
which currently just takes the market's numbers at face value with no
empirical check on whether they're actually well-calibrated. Same
measure-first-then-build convention as this repo's other favorite-longshot
bias work (src/football_favorite_bias.py, src/tennis_favorite_bias.py):
find out whether there's a real, exploitable bias before designing around one.

Sections:
    1. DATA FETCH        -- historical event discovery, bucket price history
                             (Gamma + CLOB, same endpoints as
                             src/polymarket_final_pct.py, independently
                             re-implemented here to keep this module
                             self-contained).
    2. GROUND TRUTH       -- realized BTC-USD spot price (yfinance), used
                             instead of "which $2k Polymarket bucket won"
                             for dollar-denominated error metrics.
    3. DISTRIBUTION       -- reconstructing the market's implied
       RECONSTRUCTION        mean/median/CI at a given lead time before
                             resolution, from each bucket's price history.
    4. CALIBRATION         -- forecast error by lead time, CI coverage,
                             per-bucket probability calibration (Brier
                             score + reliability curve), vs. a naive
                             "price doesn't move" baseline.
    5. REPORT

Key empirical findings from building this (documented here because they
drove design decisions, same convention as polymarket_final_pct.py):
  * The "Bitcoin price on <date>" event slug is a predictable
    `bitcoin-price-on-{month}-{day}-{year}` pattern; every guessed date in
    range returned a real (200) event via the slug lookup. The product's
    live history starts between May 1, 2026 (404) and June 1, 2026 (200) --
    EARLIEST_KNOWN_DATE below is deliberately a little after that boundary
    to avoid picking up a partial/malformed first few days.
  * A resolved event's markets carry the ground truth directly:
    outcomePrices collapses to ["1","0"] for the winning bucket and
    ["0","1"] for every other -- confirmed live across multiple resolved
    events. Not used here as the primary ground truth (yfinance spot is
    more precise -- exact dollars, not "which $2k bucket"), but used as a
    cross-check.
  * Each event opens ~7 days before its resolution (confirmed: event
    startDate to endDate spans ~168h across every event checked), so a
    single prices-history call per bucket (no 15-day-cap chunking needed)
    covers the whole lead-up window.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "raw" / "btc_calibration"
EVENTS_CACHE_DIR = CACHE_DIR / "events"
PRICES_CACHE_DIR = CACHE_DIR / "prices"
RESULTS_DIR = REPO_ROOT / "results" / "btc_price_market_calibration"

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
USER_AGENT = "Mozilla/5.0 (research backtest; contact via repo)"

# First date confirmed to have a real "Bitcoin price on <date>" event
# (2026-06-01 confirmed live; 2026-05-01 confirmed 404). Started a few days
# later to skip any first-week teething.
EARLIEST_KNOWN_DATE = datetime(2026, 6, 4, tzinfo=timezone.utc).date()

RANGE_RE = re.compile(r"^([<>]?)\s*\$?([\d,]+(?:\.\d+)?)\s*(?:-\s*\$?([\d,]+(?:\.\d+)?))?$")

# Lead times (hours before resolution) at which we reconstruct the market's
# implied forecast and score it against the eventual realized price. Capped
# at 144h (6d), not the full ~168h an event is open: events open right at
# ~167-168h before close, so a 168h lookback sometimes lands a few minutes
# before the first available price point and gets dropped -- 144h leaves a
# safe ~24h margin so it's available for virtually every event.
LEAD_HOURS = [144, 72, 24, 6]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _request_json(url: str, retries: int = 4, timeout: float = 20.0) -> object | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(2 ** attempt, 15))
                last_err = exc
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_err = exc
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 15))
    raise RuntimeError(f"GET {url} failed after {retries} retries: {last_err}")


def _parse_bucket_label(label: str) -> tuple[float | None, float | None]:
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


# ---------------------------------------------------------------------------
# 1. DATA FETCH
# ---------------------------------------------------------------------------

def _event_slug(d) -> str:
    return f"bitcoin-price-on-{d.strftime('%B').lower()}-{d.day}-{d.year}"


def discover_resolved_dates(end_date) -> list:
    """Every calendar date from EARLIEST_KNOWN_DATE to end_date (exclusive)
    -- the event resolving "today" isn't resolved yet, so callers should
    pass "today" as end_date to naturally exclude it."""
    out = []
    d = EARLIEST_KNOWN_DATE
    while d < end_date:
        out.append(d)
        d += timedelta(days=1)
    return out


def fetch_event(d) -> dict | None:
    slug = _event_slug(d)
    cache_path = EVENTS_CACHE_DIR / f"{slug}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    ev = _request_json(f"{GAMMA_BASE}/events/slug/{slug}")
    EVENTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(ev) if ev is not None else "null")
    return ev


def fetch_bucket_price_history(token: str, start_ts: int, end_ts: int) -> list[dict]:
    cache_path = PRICES_CACHE_DIR / f"{token}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    data = _request_json(
        f"{CLOB_BASE}/prices-history?market={token}&startTs={start_ts}&endTs={end_ts}&fidelity=60"
    )
    hist = (data or {}).get("history", [])
    PRICES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(hist))
    return hist


@dataclass
class BucketSeries:
    low: float | None
    high: float | None
    label: str
    resolved_yes: bool
    history: list[tuple[int, float]]  # (unix_ts, yes_price)


@dataclass
class ResolvedEvent:
    date: object
    resolve_ts: int
    buckets: list[BucketSeries]


def build_resolved_event(d) -> ResolvedEvent | None:
    ev = fetch_event(d)
    if ev is None or not ev.get("closed"):
        return None
    end_date = ev.get("endDate")
    start_date = ev.get("startDate")
    if not end_date or not start_date:
        return None
    resolve_ts = int(datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp())
    start_ts = int(datetime.fromisoformat(start_date.replace("Z", "+00:00")).timestamp())

    buckets: list[BucketSeries] = []
    for m in ev.get("markets", []):
        label = m.get("groupItemTitle") or m.get("question", "")
        low, high = _parse_bucket_label(label)
        if low is None and high is None:
            continue
        try:
            outcomes = m["outcomes"] if isinstance(m["outcomes"], list) else json.loads(m["outcomes"])
            prices = m["outcomePrices"] if isinstance(m["outcomePrices"], list) else json.loads(m["outcomePrices"])
            yes_idx = outcomes.index("Yes")
            resolved_yes = float(prices[yes_idx]) > 0.5
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        toks = json.loads(m.get("clobTokenIds") or "[]")
        if not toks:
            continue
        hist_raw = fetch_bucket_price_history(toks[0], start_ts, resolve_ts)
        history = [(int(p["t"]), float(p["p"])) for p in hist_raw if "t" in p and "p" in p]
        if not history:
            continue
        buckets.append(BucketSeries(low=low, high=high, label=label, resolved_yes=resolved_yes, history=history))

    if len(buckets) < 3 or not any(b.resolved_yes for b in buckets):
        return None
    return ResolvedEvent(date=d, resolve_ts=resolve_ts, buckets=buckets)


def fetch_all_resolved_events(end_date, max_workers: int = 8) -> list[ResolvedEvent]:
    dates = discover_resolved_dates(end_date)
    events: list[ResolvedEvent] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(build_resolved_event, d): d for d in dates}
        for fut in as_completed(futures):
            r = fut.result()
            if r is not None:
                events.append(r)
    events.sort(key=lambda e: e.date)
    return events


# ---------------------------------------------------------------------------
# 2. GROUND TRUTH (yfinance BTC-USD spot, independent of Polymarket's own
#    resolution bucket -- gives an exact dollar figure rather than "which
#    $2k-wide bucket won", which is what we need for dollar-error metrics)
# ---------------------------------------------------------------------------

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
COINBASE_MAX_CANDLES = 300  # per request, imposed by the endpoint


def _fetch_spot_yfinance(start, end) -> pd.Series:
    import yfinance as yf

    df = yf.download("BTC-USD", start=start, end=end, interval="1h", progress=False, auto_adjust=True)
    if df.empty:
        return pd.Series(dtype=float)
    close = df["Close"]
    if hasattr(close, "columns"):  # yfinance sometimes returns a 1-col DataFrame
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index, utc=True)
    return close


def _fetch_spot_coinbase(start, end) -> pd.Series:
    """Hourly BTC-USD closes from Coinbase Exchange's public candles
    endpoint, paged backwards 300 candles at a time (its per-request cap).

    A real spot venue rather than an aggregated index, so it is arguably
    the better ground truth for "what did BTC actually trade at", but it is
    a DIFFERENT series from yfinance's -- see fetch_spot_series for why
    which one a run used is recorded rather than assumed.
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    rows: list[tuple[int, float]] = []
    cursor = end_ts
    while cursor > start_ts:
        chunk_start = max(start_ts, cursor - timedelta(hours=COINBASE_MAX_CANDLES))
        params = urllib.parse.urlencode({
            "granularity": 3600,
            "start": chunk_start.isoformat(),
            "end": cursor.isoformat(),
        })
        batch = _request_json(f"{COINBASE_CANDLES}?{params}")
        if not batch:
            break
        # [time, low, high, open, close, volume], newest first
        rows.extend((int(r[0]), float(r[4])) for r in batch if isinstance(r, list) and len(r) >= 5)
        cursor = chunk_start
        time.sleep(0.25)  # public endpoint, be polite
    if not rows:
        return pd.Series(dtype=float)
    rows.sort()
    idx = pd.to_datetime([t for t, _ in rows], unit="s", utc=True)
    return pd.Series([p for _, p in rows], index=idx).groupby(level=0).last()


# Which source the last fetch_spot_series() call actually used.
SPOT_SOURCE_USED = "none"


def fetch_spot_series(start, end) -> pd.Series:
    """Hourly BTC spot over [start, end].

    yfinance first (what the original run of this backtest used, so numbers
    stay comparable), Coinbase Exchange as a fallback when Yahoo is
    unreachable. These are not the same series -- an aggregated index vs.
    one venue's BTC-USD tape -- and at the dollar-error scale this backtest
    reports the difference is not nothing, so SPOT_SOURCE_USED records
    which one produced a given run's numbers rather than leaving the
    provenance implicit.
    """
    global SPOT_SOURCE_USED
    try:
        series = _fetch_spot_yfinance(start, end)
    except Exception:
        series = pd.Series(dtype=float)
    if not series.empty:
        SPOT_SOURCE_USED = "yfinance BTC-USD (hourly)"
        return series

    series = _fetch_spot_coinbase(start, end)
    SPOT_SOURCE_USED = "Coinbase Exchange BTC-USD (hourly)" if not series.empty else "none"
    return series


def spot_at(spot: pd.Series, ts: int) -> float | None:
    if spot.empty:
        return None
    target = pd.Timestamp(ts, unit="s", tz="UTC")
    idx = spot.index.searchsorted(target)
    idx = min(max(idx, 0), len(spot) - 1)
    # nearest of the two neighbors
    candidates = [i for i in (idx - 1, idx) if 0 <= i < len(spot)]
    best = min(candidates, key=lambda i: abs((spot.index[i] - target).total_seconds()))
    if abs((spot.index[best] - target).total_seconds()) > 6 * 3600:
        return None
    return float(spot.iloc[best])


# ---------------------------------------------------------------------------
# 3. DISTRIBUTION RECONSTRUCTION AT A GIVEN LEAD TIME
# ---------------------------------------------------------------------------

def _price_at_or_before(history: list[tuple[int, float]], ts: int) -> float | None:
    usable = [p for t, p in history if t <= ts]
    return usable[-1] if usable else None


@dataclass
class ReconstructedForecast:
    lead_hours: int
    mean: float
    median: float
    ci68: tuple[float, float]
    bucket_probs: list[tuple[BucketSeries, float]]  # normalized
    # (bucket midpoint, probability), sorted by price -- the discrete
    # representation the mean/median are computed from, exposed so the
    # distributional scoring in section 3b scores exactly the same object
    # rather than rebuilding it under different conventions.
    points: list[tuple[float, float]] = field(default_factory=list)


def reconstruct_forecast(event: ResolvedEvent, lead_hours: int) -> ReconstructedForecast | None:
    query_ts = event.resolve_ts - lead_hours * 3600
    earliest = min(t for b in event.buckets for t, _ in b.history)
    if query_ts < earliest:
        return None  # requested lead time predates this event's own opening

    raw = []
    for b in event.buckets:
        p = _price_at_or_before(b.history, query_ts)
        if p is not None:
            raw.append((b, max(p, 0.0)))
    total = sum(p for _, p in raw)
    if total <= 0:
        return None
    probs = [(b, p / total) for b, p in raw]

    # Bucket midpoints; open tails approximated by extending one bucket-width
    # (same convention as wisdom-dashboard's aggregation.py grid padding).
    widths = [b.high - b.low for b, _ in probs if b.low is not None and b.high is not None and b.high > b.low]
    med_width = float(np.median(widths)) if widths else 2000.0
    points = []
    for b, p in probs:
        if b.low is not None and b.high is not None:
            mid = (b.low + b.high) / 2
        elif b.low is not None:
            mid = b.low + med_width / 2
        else:
            mid = b.high - med_width / 2
        points.append((mid, p))
    points.sort(key=lambda x: x[0])

    prices = np.array([x[0] for x in points])
    ps = np.array([x[1] for x in points])
    mean = float(np.sum(prices * ps))
    cdf = np.cumsum(ps)

    def percentile(q):
        idx = int(np.searchsorted(cdf, q))
        idx = min(idx, len(prices) - 1)
        return float(prices[idx])

    return ReconstructedForecast(
        lead_hours=lead_hours, mean=mean, median=percentile(0.5),
        ci68=(percentile(0.16), percentile(0.84)), bucket_probs=probs,
        points=points,
    )


# ---------------------------------------------------------------------------
# 3b. DISTRIBUTIONAL SCORING
#
# The mean-error table answers "is the market's point estimate good", and
# the answer turned out to be no -- it loses to "assume nothing changes" at
# every lead time. But that comparison is unfair to the market in one
# direction and uninformative in another:
#
#   * UNFAIR: a bucketed distribution's MEAN is sensitive to how its two
#     open tails are reconstructed, which is our modelling choice, not the
#     market's quote. The MEDIAN barely moves under those choices. Scoring
#     both separates "the market is wrong" from "our reconstruction of its
#     tails is wrong" -- a distinction that matters a lot, because the
#     dashboard displays that reconstructed mean.
#
#   * UNINFORMATIVE: a point forecast is not what a prediction market is
#     for. The market emits a DISTRIBUTION, and its value should be judged
#     as one. CRPS does that on the same scale as absolute error -- for a
#     point forecast it reduces exactly to |error|, so the market's
#     distribution and the naive point baseline are directly comparable in
#     dollars.
#
# And a distribution needs a distributional baseline, not just a point one.
# RANDOM_WALK below is the obvious one: centre on spot at the lead time,
# with a width set by trailing realized volatility over the same horizon --
# the "no view, just history" forecast anyone can produce for free. If the
# market's distribution cannot beat that, the interval it prints is not
# adding information either.
# ---------------------------------------------------------------------------

REALIZED_VOL_WINDOW_HOURS = 30 * 24  # trailing window for the random-walk baseline's width


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _normal_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def crps_normal(mu: float, sigma: float, y: float) -> float:
    """Closed-form CRPS for a Normal forecast. Degenerates to |mu - y| as
    sigma -> 0, which is what makes it comparable with a point forecast."""
    if sigma <= 0:
        return abs(mu - y)
    z = (y - mu) / sigma
    return float(sigma * (z * (2.0 * _normal_cdf(z) - 1.0) + 2.0 * _normal_pdf(z) - 1.0 / math.sqrt(math.pi)))


def crps_discrete(points: np.ndarray, probs: np.ndarray, y: float) -> float:
    """CRPS of a discrete forecast distribution, kernel form:

        CRPS = E|X - y| - 0.5 * E|X - X'|

    Exact for a point-mass representation, and needs no grid -- which keeps
    it free of the padding choices that the mean is sensitive to. For a
    single point mass the second term vanishes and this is just |x - y|,
    so a point forecast and a full distribution score on one scale.
    """
    points = np.asarray(points, dtype=float)
    probs = np.asarray(probs, dtype=float)
    total = probs.sum()
    if total <= 0:
        return float("nan")
    probs = probs / total
    term1 = float(np.sum(probs * np.abs(points - y)))
    term2 = float(np.sum(np.outer(probs, probs) * np.abs(points[:, None] - points[None, :])))
    return term1 - 0.5 * term2


def realized_vol_sigma(spot: pd.Series, as_of_ts: int, horizon_hours: int) -> float | None:
    """Std of the price at `horizon_hours` ahead, implied by trailing
    realized volatility of hourly log returns. Strictly backward-looking
    from `as_of_ts` -- no look-ahead into the window being forecast."""
    if spot.empty:
        return None
    as_of = pd.Timestamp(as_of_ts, unit="s", tz="UTC")
    window = spot[(spot.index <= as_of) & (spot.index > as_of - timedelta(hours=REALIZED_VOL_WINDOW_HOURS))]
    if len(window) < 48:
        return None
    log_returns = np.diff(np.log(window.to_numpy(dtype=float)))
    log_returns = log_returns[np.isfinite(log_returns)]
    if len(log_returns) < 24:
        return None
    sigma_hourly = float(np.std(log_returns, ddof=1))
    spot_now = float(window.iloc[-1])
    return spot_now * sigma_hourly * math.sqrt(horizon_hours)


# ---------------------------------------------------------------------------
# 4. CALIBRATION METRICS
# ---------------------------------------------------------------------------

@dataclass
class LeadTimeReport:
    lead_hours: int
    n_events: int
    mean_error: float          # mean(predicted_mean - realized), signed -> bias direction
    mad_error: float           # mean(|predicted_mean - realized|)
    rmse: float
    naive_mad_error: float     # naive "price at lead time = final price" baseline
    ci68_coverage: float       # fraction of events where realized price fell in the market's own 68% CI
    brier_score: float         # pooled per-bucket Brier score
    log_loss: float            # pooled per-bucket log loss (penalizes confident-and-wrong harder than Brier)
    calibration_bins: list[tuple[float, float, int]]  # (predicted_bin_center, empirical_hit_rate, n)
    # --- point estimate: median vs mean (see section 3b) ---
    # The median is far less sensitive to how the open tails are
    # reconstructed, so a large mad_error alongside a small
    # mad_median_error means the tail convention is doing the damage, not
    # the market.
    median_mean_error: float = float("nan")   # signed
    mad_median_error: float = float("nan")
    # --- distributional scoring, all on the same dollar scale ---
    crps_market: float = float("nan")          # the market's own distribution
    crps_naive: float = float("nan")           # point mass at spot; equals naive_mad_error by construction
    crps_random_walk: float = float("nan")     # spot + trailing realized vol over the same horizon
    rw_ci68_coverage: float = float("nan")     # the same coverage check, for the baseline
    n_random_walk: int = 0                     # events the baseline could be computed for


def build_calibration_report(events: list[ResolvedEvent], spot: pd.Series) -> list[LeadTimeReport]:
    reports = []
    for lead_hours in LEAD_HOURS:
        errors, naive_errors, coverage_hits = [], [], []
        median_errors = []
        crps_market, crps_naive, crps_rw, rw_coverage = [], [], [], []
        bucket_preds, bucket_hits = [], []

        for event in events:
            realized = spot_at(spot, event.resolve_ts)
            query_ts = event.resolve_ts - lead_hours * 3600
            spot_at_lead = spot_at(spot, query_ts)
            if realized is None:
                continue
            fc = reconstruct_forecast(event, lead_hours)
            if fc is None:
                continue

            errors.append(fc.mean - realized)
            median_errors.append(fc.median - realized)
            if spot_at_lead is not None:
                naive_errors.append(spot_at_lead - realized)
            coverage_hits.append(fc.ci68[0] <= realized <= fc.ci68[1])

            # Distributional scoring (section 3b). The market's own
            # distribution, the naive point forecast, and a random walk
            # widened by trailing realized vol, all in CRPS dollars.
            points = np.array([mid for mid, _ in fc.points])
            probs = np.array([p for _, p in fc.points])
            crps_market.append(crps_discrete(points, probs, realized))
            if spot_at_lead is not None:
                crps_naive.append(abs(spot_at_lead - realized))
                sigma = realized_vol_sigma(spot, query_ts, lead_hours)
                if sigma is not None and sigma > 0:
                    crps_rw.append(crps_normal(spot_at_lead, sigma, realized))
                    # 68% interval of the same baseline, for a like-for-like
                    # comparison with the market's own CI coverage.
                    half = 0.9944578832097535 * sigma  # Phi^-1(0.84)
                    rw_coverage.append(spot_at_lead - half <= realized <= spot_at_lead + half)

            for b, p in fc.bucket_probs:
                hit = b.resolved_yes
                bucket_preds.append(p)
                bucket_hits.append(1.0 if hit else 0.0)

        if not errors:
            continue

        errors_arr = np.array(errors)
        preds_for_loss = np.array(bucket_preds)
        hits_for_loss = np.array(bucket_hits)
        brier = float(np.mean((preds_for_loss - hits_for_loss) ** 2)) if bucket_preds else float("nan")
        if bucket_preds:
            eps = 1e-6  # avoids log(0) on a bucket priced at exactly 0% or 100%
            clipped = np.clip(preds_for_loss, eps, 1 - eps)
            log_loss = float(-np.mean(hits_for_loss * np.log(clipped) + (1 - hits_for_loss) * np.log(1 - clipped)))
        else:
            log_loss = float("nan")

        bins = np.linspace(0, 1, 11)
        cal_bins = []
        preds_arr, hits_arr = np.array(bucket_preds), np.array(bucket_hits)
        for lo, hi in zip(bins[:-1], bins[1:]):
            mask = (preds_arr >= lo) & (preds_arr < hi if hi < 1 else preds_arr <= hi)
            n = int(mask.sum())
            if n == 0:
                continue
            cal_bins.append(((lo + hi) / 2, float(hits_arr[mask].mean()), n))

        reports.append(LeadTimeReport(
            lead_hours=lead_hours,
            n_events=len(errors),
            mean_error=float(np.mean(errors_arr)),
            mad_error=float(np.mean(np.abs(errors_arr))),
            rmse=float(np.sqrt(np.mean(errors_arr ** 2))),
            naive_mad_error=float(np.mean(np.abs(naive_errors))) if naive_errors else float("nan"),
            ci68_coverage=float(np.mean(coverage_hits)),
            brier_score=brier,
            log_loss=log_loss,
            calibration_bins=cal_bins,
            median_mean_error=float(np.mean(median_errors)),
            mad_median_error=float(np.mean(np.abs(median_errors))),
            crps_market=float(np.mean(crps_market)) if crps_market else float("nan"),
            crps_naive=float(np.mean(crps_naive)) if crps_naive else float("nan"),
            crps_random_walk=float(np.mean(crps_rw)) if crps_rw else float("nan"),
            rw_ci68_coverage=float(np.mean(rw_coverage)) if rw_coverage else float("nan"),
            n_random_walk=len(crps_rw),
        ))
    return reports
