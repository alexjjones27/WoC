"""The aggregation engine -- the one place that knows how to turn several
sources' `PriceDistribution` objects (common/distribution.py) into a single
forecast. This module has no knowledge of Polymarket, Kalshi, Deribit, or
any other source; it only ever sees the common format, which is what makes
adding a new source type ("write an adapter") never require touching this
file.

--- Method, in order ---

1.  GROUPING (done by the caller, build_dashboard() below): distributions
    from every adapter are pooled and grouped by `target_date`. Everything
    from here on operates on one date's group at a time.

2.  COMMON GRID: each source describes its distribution as a *different* set
    of buckets (Polymarket's $2k-wide ranges vs. Kalshi's $100 ladder vs., in
    future, an options-implied continuous curve). To combine them they're
    resampled onto one shared, fine price grid built from that group's own
    bucket edges (_build_group_grid). CLOSED buckets (both edges known)
    spread their probability mass *uniformly* within the bucket
    (_resample_bucket) -- the standard, simplest assumption for turning a
    coarse histogram into a finer one; it slightly smooths real
    distribution shape but does not bias total mass or, for buckets much
    narrower than the true curvature of the underlying distribution (true
    here: Kalshi's buckets are $100 wide against a $10k+ price),
    meaningfully bias the mean/std either. The two OPEN tail buckets every
    distribution has ("< lowest strike", "> highest strike") instead decay
    exponentially outward (_resample_open_low / _resample_open_high) --
    the standard "digital option" tail shape, and a real predictive-quality
    fix over an earlier version that just spread each tail's mass
    *uniformly* across an arbitrary padding window (which materially
    biased mean/std by however wide that padding happened to be, and had
    no basis in the market's own actual survival-curve shape). The decay
    scale is this group's own median finite bucket width, and the grid
    pads out PAD_HALF_LIVES widths past the outermost real edge to capture
    (nearly) all of that tail's mass.

3.  PER-SOURCE NORMALIZATION: each source's resampled histogram is
    renormalized to sum to exactly 1.0 before combining -- guards against
    bid/ask noise meaning a platform's raw bucket probabilities don't sum
    to precisely 1.

4.  VOLUME-WEIGHTED MIXTURE: the aggregate PDF is
        agg_pdf = sum_s(weight_s * pdf_s) / sum_s(weight_s)
    i.e. a weighted average of *distributions*, not of point estimates --
    this is what "volume-weighted, not a simple average" means in practice:
    a platform with 10x the volume contributes 10x the probability mass to
    the combined curve, not just 10x the influence on a single number.
    `weight_s` is source.weight (see each adapter's docstring for how that
    source computes it -- for Phase 1 both are USD-ish volume; a future
    options/futures adapter's weight is a different liquidity unit and
    RECONCILING units across source *types* fairly is flagged here as an
    open design question, not silently assumed solved -- today's dashboard
    only ever mixes weights within one source_type (prediction markets), so
    this doesn't yet bite).

5.  STATS FROM THE AGGREGATE PDF: mean/variance/std analytically from the
    grid; median and confidence intervals (68%/95%, plus every level in
    CONFIDENCE_LEVELS for the fan chart) from the *actual* cumulative
    distribution (not assumed normal) via linear interpolation between grid
    points -- this matters when the combined curve is skewed, which
    prediction-market-implied distributions often are.

6.  CONFIDENCE SCORE: a monotonic function of total USD-ish volume behind
    the group (see CONFIDENCE_TIERS / _confidence_score below) -- a
    thinly-traded date's forecast is flagged low-confidence even if its
    math is otherwise identical to a liquid one's.

7.  DISAGREEMENT SCORE: computed from each *source's own* mean (not from
    the mixture), as the volume-weighted standard deviation of those means,
    expressed as a percent of the aggregate mean. This is deliberately
    surfaced as its own top-level number (see PriceDistribution.raw_note /
    AggregateForecast.disagreement_pct) rather than only being implicit in
    a wider std dev -- two sources that agree tightly but sit far apart
    (e.g. 55% vs 85% for the same threshold) can still average to a
    plausible-looking single curve; the disagreement score is what catches
    that.

8.  CALIBRATION CORRECTION (applied before steps 3-5, i.e. to each source's
    raw buckets before they're resampled/mixed): two adjustments, both
    derived from an empirical backtest (see
    ../../../results/btc_price_market_calibration/report.md and
    ../../../scripts/run_btc_price_market_calibration.py in the parent
    Finance repo) that scored 96 resolved Polymarket "Bitcoin price on
    <date>" markets against realized BTC spot price, by lead time before
    resolution:
      - LONGSHOT SHRINKAGE: buckets priced under ~10% consistently resolved
        Yes *less* often than their stated probability (e.g. at a 6h lead
        time, buckets priced ~5% actually hit only ~0.8% of the time) --
        the same favorite-longshot-bias shape already found and traded in
        this repo's football/tennis work. Each such bucket's probability is
        shrunk by a lead-time-dependent factor (SHRINK_BY_LEAD_HOURS) before
        the removed mass gets redistributed across the rest of that
        source's buckets by the normal per-source renormalization (step 3).
      - CI WIDTH RECALIBRATION: the backtest's stated 68% interval actually
        covered the realized price only ~42% of the time at a 6-hour lead
        (badly overconfident) but ~74-88% of the time at 3-6 days out (a
        bit underconfident) -- CI_WIDTH_MULT_BY_LEAD_HOURS rescales the
        distance from median to each bound by a factor derived from that
        miscalibration (see _ci_width_multiplier), applied AFTER step 5's
        percentile computation, not to the raw buckets.
    Both tables were measured on Polymarket's own pre-bucketed range
    markets specifically; applying them to Kalshi's differenced-ladder
    buckets and to the mixed aggregate curve is an extrapolation, not a
    separate direct measurement -- and the backtest itself covers one
    ~3.5-month, one-direction (trending) window, so both tables are a
    documented, adjustable starting point (flip APPLY_CALIBRATION_CORRECTIONS
    to False to see the raw uncorrected numbers), not a settled result.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np

from common.distribution import PriceDistribution, SourceFetchResult, TouchFetchResult, TouchForecast

# A source with literally zero recorded volume/OI still gets a tiny nonzero
# weight so it isn't silently dropped from a mixture where every other
# source also happens to show zero (which would otherwise make the
# weighted-average undefined, 0/0) -- it just ends up contributing
# negligibly whenever any other source has real volume.
EPS_WEIGHT = 1e-6

# Confidence tiers: (minimum total group USD-ish volume, label). Checked
# top-down. Tune these to taste -- they are a judgment call, not derived
# from anything statistical.
CONFIDENCE_TIERS = [
    (250_000.0, "high"),
    (25_000.0, "medium"),
    (0.0, "low"),
]
# Reference volume for the continuous 0-100 confidence score's log scale --
# a group with this much total volume scores 100.
CONFIDENCE_SCORE_REF_VOLUME = 500_000.0

# Disagreement above this (source means' weighted std, as % of the
# aggregate mean) is flagged `high_divergence=True` in the API response.
HIGH_DIVERGENCE_PCT = 3.0

GRID_CELLS = 300

# Confidence levels shown as nested bands on the fan chart (see FanChart.tsx
# and the /api/forecast payload's per-forecast `confidence_bands`). Edit
# freely -- any set of levels in (0, 1) works, each just becomes one more
# nested band computed the same way as ci_68/ci_95 below.
CONFIDENCE_LEVELS = [0.70, 0.80, 0.90, 0.95, 0.99]

# --- Calibration correction (see module docstring step 8 and
# results/btc_price_market_calibration/report.md in the parent Finance repo
# for how these were measured) ---
APPLY_CALIBRATION_CORRECTIONS = True

# Bucket-probability shrink factor by lead time (hours before resolution),
# applied to buckets priced under LONGSHOT_PROB_THRESHOLD. Values are
# (empirical hit rate) / (stated probability) for the ~5%-priced bucket,
# pooled across 96 resolved events per lead time -- e.g. at a 6h lead,
# buckets priced ~5% actually resolved Yes only ~16% of that stated rate.
LONGSHOT_PROB_THRESHOLD = 0.10
SHRINK_BY_LEAD_HOURS = {6.0: 0.16, 24.0: 0.20, 72.0: 0.30, 144.0: 0.74}

# CI half-width multiplier by lead time, derived from measured 68%-CI
# coverage (42% at 6h -> badly overconfident -> widen; ~74-88% at 72-144h
# -> mildly underconfident -> narrow slightly). Computed as
# Phi^-1(0.84) / Phi^-1((coverage+1)/2), i.e. "how much would a
# normal-approximation interval need to be rescaled to hit the nominal 68%".
CI_WIDTH_MULT_BY_LEAD_HOURS = {6.0: 1.792, 24.0: 1.156, 72.0: 0.908, 144.0: 0.884}


def _log_interp(anchors: dict[float, float], x: float) -> float:
    """Interpolate `anchors` ({hours: value}) in log-hours space; clamps to
    the nearest measured value outside the anchors' range rather than
    extrapolating unboundedly (we have no data past 144h or under 6h)."""
    pts = sorted(anchors.items())
    xs = [math.log(h) for h, _ in pts]
    ys = [v for _, v in pts]
    lx = math.log(max(x, 1e-3))
    if lx <= xs[0]:
        return ys[0]
    if lx >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= lx <= xs[i + 1]:
            frac = (lx - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + frac * (ys[i + 1] - ys[i])
    return ys[-1]  # unreachable, satisfies type checkers


def _lead_hours(resolve_iso: str | None, target_date: date) -> float:
    if resolve_iso:
        try:
            resolve_dt = datetime.fromisoformat(resolve_iso.replace("Z", "+00:00"))
        except ValueError:
            resolve_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    else:
        resolve_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    return max((resolve_dt - datetime.now(timezone.utc)).total_seconds() / 3600.0, 0.0)


@dataclass
class ThresholdRow:
    threshold: float
    prob_gt_aggregate: float
    per_source_prob_gt: dict[str, float]


@dataclass
class SourceBreakdown:
    source_name: str
    source_type: str
    period_label: str
    mean: float
    weight: float
    volume: float | None
    open_interest: float | None
    liquidity: float | None
    source_url: str | None
    resolve_datetime_utc: str | None
    pdf: list[float]
    raw_note: str | None = None
    is_play_money: bool = False
    concentration_discount: float | None = None
    concentration_effective_traders: float | None = None
    stale: bool = False
    error: str | None = None


@dataclass
class ConfidenceBand:
    level: float  # e.g. 0.90 = 90%
    low: float
    high: float


@dataclass
class AggregateForecast:
    asset: str
    target_date: str
    period_label: str
    grid_edges: list[float]
    pdf: list[float]
    mean: float
    median: float
    std: float
    ci_68: tuple[float, float]
    ci_95: tuple[float, float]
    confidence_score: float
    confidence_tier: str
    total_volume: float
    disagreement_pct: float
    high_divergence: bool
    thresholds: list[ThresholdRow]
    lead_hours: float
    longshot_shrink_applied: float  # 1.0 = no correction
    ci_width_mult_applied: float    # 1.0 = no correction
    confidence_bands: list[ConfidenceBand] = field(default_factory=list)
    sources: list[SourceBreakdown] = field(default_factory=list)


def _resample_bucket(low: float, high: float, prob: float, grid_edges: np.ndarray) -> np.ndarray:
    """Spread `prob` uniformly across whichever grid cells [low, high)
    overlaps. Degenerate (zero-width) buckets go entirely into the cell
    that contains them. Used for ordinary CLOSED buckets only -- open
    ("< X" / "> X") tail buckets use the exponential-decay resamplers
    below instead, not this uniform one."""
    n = len(grid_edges) - 1
    width = high - low
    if width <= 0:
        idx = int(np.clip(np.searchsorted(grid_edges, low, side="right") - 1, 0, n - 1))
        out = np.zeros(n)
        out[idx] = prob
        return out
    overlap_lo = np.maximum(grid_edges[:-1], low)
    overlap_hi = np.minimum(grid_edges[1:], high)
    overlap = np.clip(overlap_hi - overlap_lo, 0.0, None)
    return (overlap / width) * prob


def _resample_open_low(high: float, prob: float, scale: float, grid_edges: np.ndarray) -> np.ndarray:
    """Resample a "< high" (unbounded-below) bucket as an exponential tail
    -- density f(x) = (1/scale) * exp((x-high)/scale) for x <= high --
    instead of spreading `prob` uniformly across whatever padding
    _build_group_grid happened to add. This is the standard shape for a
    "how much mass sits beyond the last quoted strike" tail (same idea as
    a digital-option tail assumption): decaying, never literally flat, and
    it no longer depends on the somewhat arbitrary padding width the way
    uniform spreading did -- `scale` (TAIL_SCALE, this group's own median
    finite bucket width) sets how fast it decays, and the grid just needs
    to extend far enough to capture it (see _build_group_grid's PAD_HALF_LIVES)."""
    capped = np.minimum(grid_edges, high)
    cdf = np.exp((capped - high) / scale)  # =1 exactly at/beyond `high`; ->0 as x decreases
    return prob * np.diff(cdf)


def _resample_open_high(low: float, prob: float, scale: float, grid_edges: np.ndarray) -> np.ndarray:
    """Mirror of _resample_open_low for a "> low" (unbounded-above) bucket:
    f(x) = (1/scale) * exp(-(x-low)/scale) for x >= low."""
    capped = np.maximum(grid_edges, low)
    cdf = 1.0 - np.exp(-(capped - low) / scale)  # =0 exactly at/below `low`; ->1 as x increases
    return prob * np.diff(cdf)


# How many exponential half-lives (in units of TAIL_SCALE) the grid pads
# beyond the outermost real bucket edge -- at PAD_HALF_LIVES=8,
# exp(-8) ~= 0.0003 of that tail's mass falls outside the grid entirely
# (silently dropped, not renormalized back in -- negligible at this depth).
PAD_HALF_LIVES = 8.0


def _build_group_grid(distributions: list[PriceDistribution]) -> tuple[np.ndarray, float] | tuple[None, None]:
    finite_edges: list[float] = []
    widths: list[float] = []
    for d in distributions:
        for b in d.buckets:
            if b.low is not None:
                finite_edges.append(b.low)
            if b.high is not None:
                finite_edges.append(b.high)
            if b.low is not None and b.high is not None and b.high > b.low:
                widths.append(b.high - b.low)
    if not finite_edges:
        return None, None
    lo, hi = min(finite_edges), max(finite_edges)
    span = max(hi - lo, 1.0)
    tail_scale = float(np.median(widths)) if widths else span * 0.02
    # Pad the open tails out far enough for the exponential resamplers
    # above to capture (nearly) all of their mass -- see PAD_HALF_LIVES.
    pad = max(tail_scale * PAD_HALF_LIVES, span * 0.08)
    grid_lo = max(0.0, lo - pad)
    grid_hi = hi + pad
    return np.linspace(grid_lo, grid_hi, GRID_CELLS + 1), tail_scale


def _cdf_at_edges(grid_edges: np.ndarray, pdf: np.ndarray) -> np.ndarray:
    """Cumulative probability at each grid edge (edges[0] -> 0)."""
    return np.concatenate(([0.0], np.cumsum(pdf)))


def _percentile(grid_edges: np.ndarray, cdf_edges: np.ndarray, q: float) -> float:
    idx = int(np.searchsorted(cdf_edges, q))
    idx = min(max(idx, 1), len(cdf_edges) - 1)
    cdf_lo, cdf_hi = cdf_edges[idx - 1], cdf_edges[idx]
    px_lo, px_hi = grid_edges[idx - 1], grid_edges[idx]
    if cdf_hi == cdf_lo:
        return float(px_hi)
    frac = (q - cdf_lo) / (cdf_hi - cdf_lo)
    return float(px_lo + frac * (px_hi - px_lo))


def _prob_gt(grid_edges: np.ndarray, cdf_edges: np.ndarray, threshold: float) -> float:
    if threshold <= grid_edges[0]:
        return 1.0
    if threshold >= grid_edges[-1]:
        return 0.0
    idx = int(np.searchsorted(grid_edges, threshold))
    idx = min(max(idx, 1), len(grid_edges) - 1)
    px_lo, px_hi = grid_edges[idx - 1], grid_edges[idx]
    cdf_lo, cdf_hi = cdf_edges[idx - 1], cdf_edges[idx]
    frac = 0.0 if px_hi == px_lo else (threshold - px_lo) / (px_hi - px_lo)
    cdf_at_t = cdf_lo + frac * (cdf_hi - cdf_lo)
    return float(1.0 - cdf_at_t)


def _nice_step(span: float, target_n: int) -> float:
    raw = span / max(target_n, 1)
    if raw <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * magnitude
        if step >= raw:
            return step
    return magnitude * 10


def _nice_thresholds(lo: float, hi: float, target_n: int = 6) -> list[float]:
    step = _nice_step(hi - lo, target_n)
    start = math.ceil(lo / step) * step
    out = []
    x = start
    while x < hi:
        out.append(round(x, 2))
        x += step
    return out


def _confidence_score(total_volume: float) -> float:
    score = 100.0 * math.log10(1 + max(total_volume, 0.0)) / math.log10(1 + CONFIDENCE_SCORE_REF_VOLUME)
    return float(np.clip(score, 0.0, 100.0))


def _confidence_tier(total_volume: float) -> str:
    for threshold, label in CONFIDENCE_TIERS:
        if total_volume >= threshold:
            return label
    return "low"


def aggregate_group(distributions: list[PriceDistribution]) -> AggregateForecast | None:
    """Combine every source's distribution for ONE target date into one
    AggregateForecast. Returns None if there's nothing usable (e.g. every
    bucket resampled to zero mass)."""
    if not distributions:
        return None
    grid_edges, tail_scale = _build_group_grid(distributions)
    if grid_edges is None:
        return None
    centers = (grid_edges[:-1] + grid_edges[1:]) / 2.0

    ref = distributions[0]
    lead_hours = _lead_hours(ref.resolve_datetime_utc, ref.target_date)
    shrink = _log_interp(SHRINK_BY_LEAD_HOURS, lead_hours) if APPLY_CALIBRATION_CORRECTIONS else 1.0

    weighted_pdf_sum = np.zeros(len(centers))
    total_eff_weight = 0.0
    total_real_volume = 0.0
    sources: list[SourceBreakdown] = []
    source_means: list[tuple[float, float]] = []  # (mean, weight) for disagreement calc

    for d in distributions:
        pdf = np.zeros(len(centers))
        for b in d.buckets:
            # Calibration correction (step 8): shrink longshot buckets
            # toward the empirically-measured lower hit rate; the mass this
            # removes is restored proportionally to the rest of this
            # source's buckets by the renormalization a few lines below.
            prob = b.prob * shrink if b.prob < LONGSHOT_PROB_THRESHOLD else b.prob
            if b.low is None and b.high is not None:
                pdf += _resample_open_low(b.high, prob, tail_scale, grid_edges)
            elif b.high is None and b.low is not None:
                pdf += _resample_open_high(b.low, prob, tail_scale, grid_edges)
            else:
                lo = b.low if b.low is not None else float(grid_edges[0])
                hi = b.high if b.high is not None else float(grid_edges[-1])
                pdf += _resample_bucket(lo, hi, prob, grid_edges)
        mass = float(pdf.sum())
        if mass <= 0:
            continue
        pdf /= mass  # per-source renormalization (step 3)

        real_weight = max(d.weight, 0.0)
        eff_weight = real_weight if real_weight > 0 else EPS_WEIGHT
        weighted_pdf_sum += eff_weight * pdf
        total_eff_weight += eff_weight
        total_real_volume += d.volume or 0.0

        mean_s = float(np.sum(centers * pdf))
        source_means.append((mean_s, eff_weight))
        sources.append(SourceBreakdown(
            source_name=d.source_name,
            source_type=d.source_type,
            period_label=d.period_label,
            mean=mean_s,
            weight=real_weight,
            volume=d.volume,
            open_interest=d.open_interest,
            liquidity=d.liquidity,
            source_url=d.source_url,
            resolve_datetime_utc=d.resolve_datetime_utc,
            pdf=pdf.tolist(),
            raw_note=d.raw_note,
            is_play_money=d.is_play_money,
            concentration_discount=d.concentration_discount,
            concentration_effective_traders=d.concentration_effective_traders,
            stale=d.stale,
            error=d.error,
        ))

    if total_eff_weight <= 0 or not sources:
        return None

    agg_pdf = weighted_pdf_sum / total_eff_weight
    mean = float(np.sum(centers * agg_pdf))
    variance = float(np.sum(agg_pdf * (centers - mean) ** 2))
    std = math.sqrt(max(variance, 0.0))

    cdf_edges = _cdf_at_edges(grid_edges, agg_pdf)
    median = _percentile(grid_edges, cdf_edges, 0.5)

    # Rescale each bound's distance from the median by the measured
    # coverage-derived multiplier (step 8) -- preserves any skew (the two
    # sides can be rescaled by different absolute amounts even though the
    # multiplier itself is the same on both sides) while correcting the
    # interval's width. The multiplier was only directly measured at 68%
    # coverage; the same value is applied to every other level here (95%,
    # and CONFIDENCE_LEVELS below) for lack of a level-specific measurement
    # -- see module docstring step 8.
    ci_mult = _log_interp(CI_WIDTH_MULT_BY_LEAD_HOURS, lead_hours) if APPLY_CALIBRATION_CORRECTIONS else 1.0

    def _band(level: float) -> tuple[float, float]:
        half = level / 2.0
        lo = _percentile(grid_edges, cdf_edges, 0.5 - half)
        hi = _percentile(grid_edges, cdf_edges, 0.5 + half)
        return (median - (median - lo) * ci_mult, median + (hi - median) * ci_mult)

    ci_68 = _band(0.68)
    ci_95 = _band(0.95)
    confidence_bands = []
    for lvl in CONFIDENCE_LEVELS:
        lo, hi = _band(lvl)
        confidence_bands.append(ConfidenceBand(level=lvl, low=lo, high=hi))

    if len(source_means) >= 2:
        means_arr = np.array([m for m, _ in source_means])
        w_arr = np.array([w for _, w in source_means])
        w_mean = float(np.sum(w_arr * means_arr) / np.sum(w_arr))
        disagreement_std = math.sqrt(float(np.sum(w_arr * (means_arr - w_mean) ** 2) / np.sum(w_arr)))
        disagreement_pct = (disagreement_std / mean * 100.0) if mean else 0.0
    else:
        disagreement_pct = 0.0

    # Compute per-source threshold probabilities from each source's own pdf
    # (built above) rather than re-deriving from raw buckets.
    threshold_rows: list[ThresholdRow] = []
    for t in _nice_thresholds(float(grid_edges[0]), float(grid_edges[-1])):
        per_source_p = {}
        for src in sources:
            src_cdf = _cdf_at_edges(grid_edges, np.array(src.pdf))
            per_source_p[src.source_name] = _prob_gt(grid_edges, src_cdf, t)
        threshold_rows.append(ThresholdRow(
            threshold=t,
            prob_gt_aggregate=_prob_gt(grid_edges, cdf_edges, t),
            per_source_prob_gt=per_source_p,
        ))

    return AggregateForecast(
        asset=ref.asset,
        target_date=ref.target_date.isoformat(),
        period_label=ref.period_label,
        grid_edges=[float(x) for x in grid_edges],
        pdf=[float(x) for x in agg_pdf],
        mean=mean,
        median=median,
        std=std,
        ci_68=ci_68,
        ci_95=ci_95,
        confidence_score=_confidence_score(total_real_volume),
        confidence_tier=_confidence_tier(total_real_volume),
        total_volume=total_real_volume,
        disagreement_pct=disagreement_pct,
        high_divergence=disagreement_pct > HIGH_DIVERGENCE_PCT and len(sources) >= 2,
        thresholds=threshold_rows,
        lead_hours=lead_hours,
        longshot_shrink_applied=shrink,
        ci_width_mult_applied=ci_mult,
        confidence_bands=confidence_bands,
        sources=sources,
    )


def build_dashboard(results: list[SourceFetchResult]) -> tuple[list[AggregateForecast], list[dict]]:
    """Top-level entry point: pool every source's distributions, group by
    target date, aggregate each group. Returns (forecasts sorted by date,
    per-source errors so the UI can show "Kalshi: discovery failed" etc.
    without losing whatever other sources DID return)."""
    groups: dict[date, list[PriceDistribution]] = defaultdict(list)
    source_errors: list[dict] = []
    for r in results:
        if r.error:
            source_errors.append({"source": r.source_name, "source_type": r.source_type, "error": r.error})
        for d in r.distributions:
            groups[d.target_date].append(d)

    forecasts = []
    for target_date in sorted(groups):
        forecast = aggregate_group(groups[target_date])
        if forecast is not None:
            forecasts.append(forecast)
    return forecasts, source_errors


@dataclass
class TouchGroup:
    """Every source's touch-probability ladder for one expiry date,
    grouped for display only -- deliberately NOT run through
    aggregate_group's grid/mixture math. Combining "chance of ever
    crossing $X" across platforms the way we combine point-in-time
    distributions would need its own (currently unbuilt) method: these
    are independent barrier bets, not a partition of outcome space, so a
    volume-weighted mixture of them doesn't mean the same thing it means
    for a PDF. Each platform's ladder is shown side by side instead, so the
    reader compares them directly rather than trusting a blended number
    that isn't statistically justified yet."""

    expiry_date: str
    period_label: str
    sources: list[TouchForecast] = field(default_factory=list)


def build_touch_groups(results: list[TouchFetchResult]) -> tuple[list[TouchGroup], list[dict]]:
    # keyed (expiry_date, source_name) -> best TouchForecast for that pair.
    # A source can have more than one touch event landing on the same
    # calendar date (e.g. Polymarket's weekly AND daily "what will WTI
    # hit" events both closing "today") -- rather than listing the same
    # source twice under one date (a real key collision for anything that
    # keys UI rows by source_name), keep only the higher-volume one.
    best: dict[tuple[date, str], TouchForecast] = {}
    errors: list[dict] = []
    for r in results:
        if r.error:
            errors.append({"source": r.source_name, "error": r.error})
        for t in r.touches:
            key = (t.expiry_date, t.source_name)
            if key not in best or t.total_volume > best[key].total_volume:
                best[key] = t

    groups: dict[date, list[TouchForecast]] = defaultdict(list)
    for t in best.values():
        groups[t.expiry_date].append(t)

    out = []
    for expiry_date in sorted(groups):
        sources = sorted(groups[expiry_date], key=lambda t: t.source_name)
        out.append(TouchGroup(expiry_date=expiry_date.isoformat(), period_label=sources[0].period_label, sources=sources))
    return out, errors
