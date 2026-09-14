"""A fitted post-processing model that turns a prediction market's raw
implied distribution into an actual price forecast.

--- Why this exists ---

The calibration backtest (results/btc_price_market_calibration/report.md)
found the market's raw distribution loses to a trivial random walk at every
horizon: 23-44% worse on CRPS, and far worse calibrated (at a 6h lead its
68% interval covers 41% against a nominal 68%). The aggregate backtest
found that mixing platforms makes things worse, not better.

Neither result says the market knows nothing. They say the market's
distribution, used raw, is not a forecast. This module is the missing
layer between the two.

The structural gap it closes: the dashboard already fetches current SPOT
PRICE on every refresh -- and uses it only to draw the grey history line
behind the fan chart. It never reaches the forecast. That is a strange
place to leave the single best-performing predictor in the whole backtest.

--- The model ---

Three parameters, fitted per lead time, applied in this order to a
market distribution with median m, given spot S and a horizon volatility
sigma_h:

  1. RECENTRE (lambda). Every support point moves so the distribution's
     centre goes from m to  S + lambda * (m - S).
     lambda = 1 keeps the market's own location; lambda = 0 discards it and
     centres on spot. Shape, skew and width are untouched -- this asks
     only "is the market's view of WHERE price will be worth anything?"

  2. RESCALE (s). Spread about the new centre is multiplied by s. This
     asks "is the market's view of HOW UNCERTAIN it is worth anything,
     once its width is free to be corrected?" It is a shape-preserving
     stretch, not a normal approximation: skew and fat tails survive.

  3. POOL (w). The rescaled market is combined with a random walk
     N(S, sigma_h) by QUANTILE AVERAGING (Vincentization):
         Q_pool(p) = w * Q_market(p) + (1 - w) * Q_rw(p)
     rather than by averaging densities. A linear pool of two densities
     is always wider than its parts, which buys coverage by giving up
     sharpness; quantile averaging keeps a combined distribution about as
     sharp as its inputs. w = 1 is the market alone, w = 0 the random walk
     alone.

Each step is deliberately a transform the market's own shape survives, so
that if the market carries information about skew or fat tails -- the
thing a normal random walk structurally cannot express -- the fit can keep
it while discarding the parts that measured badly.

--- Fitting honestly ---

Three parameters against 97 correlated daily events would overfit happily
and report a wonderful in-sample number. So:

  * Parameters are fitted on a chronological TRAIN split and every headline
    number is reported on the held-out TEST split. No test event touches
    the fit.
  * The objective is mean CRPS, the same proper scoring rule the baselines
    are judged by -- not coverage, which can be gamed by widening, and not
    MAD, which ignores the distribution.
  * A NESTED sequence is reported (raw -> recentred -> +rescaled ->
    +pooled) so each parameter has to earn its place out of sample rather
    than being justified by the final number alone.
  * The random-walk baseline's sigma is strictly backward-looking, so the
    comparison is never flattered by hindsight.

A caveat no split can fix: this is one asset over one ~3.5 month trending
window on one platform. Read the fitted values as "what this window says,"
not as constants.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wisdom-dashboard" / "backend"))

import btc_price_market_calibration as btccal

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results" / "forecast_model"
DATASET_PATH = REPO_ROOT / "data" / "raw" / "forecast_model" / "dataset.json"

# Probability grid the quantile machinery works on. Endpoints excluded:
# Q(0) and Q(1) are +/-inf for the normal arm.
N_QUANTILES = 401
_PROBS = np.linspace(0.5 / N_QUANTILES, 1.0 - 0.5 / N_QUANTILES, N_QUANTILES)

_NORMAL = statistics.NormalDist()


# ---------------------------------------------------------------------------
# 1. The distribution transforms
# ---------------------------------------------------------------------------

def bucket_quantiles(buckets: list[tuple[float | None, float | None, float]], at: np.ndarray = _PROBS) -> np.ndarray:
    """Quantile curve from raw market BUCKETS, reconstructed the way the
    live dashboard reconstructs them: probability spread uniformly inside
    each closed bucket, and the two open tails decaying exponentially with
    a scale set by the median bucket width.

    This matters more than it looks. Collapsing each bucket to its midpoint
    -- the obvious shortcut, and what the calibration backtest does for its
    mean/median -- turns a $2,000-wide bucket into a point mass, which
    fabricates a spiky, lumpy distribution the market never expressed and
    then scores it for the lumpiness. Any verdict on whether the market's
    SHAPE carries information has to be run against a faithful
    reconstruction, not that artifact, so this imports the aggregation
    engine's own resamplers rather than approximating them again.
    """
    from common.distribution import PriceBucket, PriceDistribution  # noqa: PLC0415
    import aggregation as agg  # noqa: PLC0415

    pb = [PriceBucket(low=lo, high=hi, prob=max(p, 0.0)) for lo, hi, p in buckets]
    dist = PriceDistribution(
        asset="BTC", source_type="prediction_market", source_name="polymarket",
        target_date=date(2026, 1, 1), period_label="", buckets=pb, weight=1.0,
    )
    grid_edges, tail_scale = agg._build_group_grid([dist])
    if grid_edges is None:
        return np.full(len(at), np.nan)

    pdf = np.zeros(len(grid_edges) - 1)
    for b in pb:
        if b.low is None and b.high is not None:
            pdf += agg._resample_open_low(b.high, b.prob, tail_scale, grid_edges)
        elif b.high is None and b.low is not None:
            pdf += agg._resample_open_high(b.low, b.prob, tail_scale, grid_edges)
        else:
            lo = b.low if b.low is not None else float(grid_edges[0])
            hi = b.high if b.high is not None else float(grid_edges[-1])
            pdf += agg._resample_bucket(lo, hi, b.prob, grid_edges)
    mass = pdf.sum()
    if mass <= 0:
        return np.full(len(at), np.nan)
    cdf = np.concatenate(([0.0], np.cumsum(pdf / mass)))
    return np.interp(at, cdf, grid_edges)


def discrete_quantiles(points: np.ndarray, probs: np.ndarray, at: np.ndarray = _PROBS) -> np.ndarray:
    """Quantile function of a discrete distribution, evaluated at `at`.

    Uses the midpoint convention on the cumulative mass so a bucketed
    market distribution maps to a smooth-ish quantile curve rather than a
    staircase whose steps are an artifact of bucket width.
    """
    order = np.argsort(points)
    x = np.asarray(points, dtype=float)[order]
    p = np.asarray(probs, dtype=float)[order]
    total = p.sum()
    if total <= 0:
        return np.full(len(at), float(x[0]) if len(x) else np.nan)
    p = p / total
    cum = np.cumsum(p)
    mid = cum - p / 2.0  # plotting-position style; avoids a step at each atom
    return np.interp(at, mid, x)


def recentre_and_rescale(q: np.ndarray, spot: float, lam: float, scale: float) -> np.ndarray:
    """Apply steps 1 and 2 to a quantile curve.

    Centre is the median (the curve's midpoint), not the mean: it is the
    robust centre of a skewed bucketed distribution and does not move when
    the open tails are reconstructed differently.
    """
    centre = float(np.interp(0.5, _PROBS, q))
    new_centre = spot + lam * (centre - spot)
    return new_centre + scale * (q - centre)


def random_walk_quantiles(spot: float, sigma: float, at: np.ndarray = _PROBS) -> np.ndarray:
    if sigma <= 0:
        return np.full(len(at), spot)
    return spot + sigma * np.array([_NORMAL.inv_cdf(float(p)) for p in at])


def pool_quantiles(q_market: np.ndarray, q_rw: np.ndarray, w: float) -> np.ndarray:
    """Vincentized (quantile-averaged) pool -- see the module docstring for
    why this rather than averaging densities."""
    return w * q_market + (1.0 - w) * q_rw


def crps_from_quantiles(q: np.ndarray, y: float) -> float:
    """CRPS of a distribution represented by equally-weighted quantile
    samples. Same kernel form the rest of the repo uses:
        CRPS = E|X - y| - 0.5 * E|X - X'|
    The second term is computed from the sorted sample in O(n) rather than
    as an n^2 pairwise matrix.
    """
    x = np.sort(np.asarray(q, dtype=float))
    n = len(x)
    if n == 0:
        return float("nan")
    term1 = float(np.mean(np.abs(x - y)))
    # E|X - X'| for an equally weighted sample, closed form on sorted x.
    i = np.arange(1, n + 1)
    term2 = float(2.0 * np.sum((2 * i - n - 1) * x) / (n * n))
    return term1 - 0.5 * term2


def interval_from_quantiles(q: np.ndarray, level: float = 0.68) -> tuple[float, float]:
    lo = float(np.interp(0.5 - level / 2.0, _PROBS, q))
    hi = float(np.interp(0.5 + level / 2.0, _PROBS, q))
    return lo, hi


@dataclass(frozen=True)
class ModelParams:
    """lambda / scale / weight -- see the module docstring."""
    lam: float = 1.0     # 1.0 = keep the market's location
    scale: float = 1.0   # 1.0 = keep the market's width
    weight: float = 1.0  # 1.0 = market only, no random-walk arm

    def as_dict(self) -> dict:
        return {"lambda": self.lam, "scale": self.scale, "weight": self.weight}


def apply_model(
    q_market_raw: np.ndarray,
    spot: float,
    sigma: float | None,
    params: ModelParams,
) -> np.ndarray:
    """The whole model: recentre, rescale, pool. Returns a quantile curve."""
    q = recentre_and_rescale(q_market_raw, spot, params.lam, params.scale)
    if params.weight >= 1.0 or sigma is None or sigma <= 0:
        return q
    return pool_quantiles(q, random_walk_quantiles(spot, sigma), params.weight)


# ---------------------------------------------------------------------------
# 2. Dataset
# ---------------------------------------------------------------------------

@dataclass
class Case:
    """One (event, lead time) pair: everything needed to score any model."""
    event_date: str
    lead_hours: int
    realized: float
    spot: float
    sigma: float | None
    q_market: list[float]        # faithful bucket reconstruction (the default)
    q_midpoint: list[float]      # the midpoint-collapse version, kept for comparison
    buckets: list[list]          # raw (low, high, prob), for threshold-probability tests

    def to_json(self) -> dict:
        return {
            "event_date": self.event_date, "lead_hours": self.lead_hours,
            "realized": self.realized, "spot": self.spot, "sigma": self.sigma,
            "q_market": [round(v, 4) for v in self.q_market],
            "q_midpoint": [round(v, 4) for v in self.q_midpoint],
            "buckets": self.buckets,
        }

    @staticmethod
    def from_json(d: dict) -> "Case":
        return Case(d["event_date"], d["lead_hours"], d["realized"], d["spot"], d["sigma"],
                    d["q_market"], d.get("q_midpoint", d["q_market"]), d.get("buckets", []))


def build_dataset(events: list, spot: pd.Series) -> list[Case]:
    cases: list[Case] = []
    for event in events:
        for lead_hours in btccal.LEAD_HOURS:
            query_ts = event.resolve_ts - lead_hours * 3600
            realized = btccal.spot_at(spot, event.resolve_ts)
            spot_at_lead = btccal.spot_at(spot, query_ts)
            if realized is None or spot_at_lead is None:
                continue
            fc = btccal.reconstruct_forecast(event, lead_hours)
            if fc is None or not fc.points:
                continue
            pts = np.array([x for x, _ in fc.points], dtype=float)
            prb = np.array([p for _, p in fc.points], dtype=float)
            raw_buckets = [[b.low, b.high, float(p)] for b, p in fc.bucket_probs]
            q_faithful = bucket_quantiles([(b[0], b[1], b[2]) for b in raw_buckets])
            if not np.all(np.isfinite(q_faithful)):
                continue
            cases.append(Case(
                event_date=str(event.date),
                lead_hours=lead_hours,
                realized=float(realized),
                spot=float(spot_at_lead),
                sigma=btccal.realized_vol_sigma(spot, query_ts, lead_hours),
                q_market=[float(v) for v in q_faithful],
                q_midpoint=[float(v) for v in discrete_quantiles(pts, prb)],
                buckets=raw_buckets,
            ))
    return cases


def save_dataset(cases: list[Case], path: Path = DATASET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([c.to_json() for c in cases]))


def load_dataset(path: Path = DATASET_PATH) -> list[Case]:
    return [Case.from_json(d) for d in json.loads(path.read_text())]


# ---------------------------------------------------------------------------
# 3. Fitting
# ---------------------------------------------------------------------------

def mean_crps(cases: list[Case], params: ModelParams) -> float:
    if not cases:
        return float("nan")
    tot = 0.0
    for c in cases:
        q = apply_model(np.array(c.q_market), c.spot, c.sigma, params)
        tot += crps_from_quantiles(q, c.realized)
    return tot / len(cases)


# Search grids. Deliberately coarse: with ~48 correlated training events per
# lead time, a finer grid would be fitting noise, and the interesting
# question is which region of the space wins, not the third decimal.
LAMBDA_GRID = [round(x, 2) for x in np.arange(0.0, 1.301, 0.05)]
SCALE_GRID = [round(x, 2) for x in np.arange(0.3, 3.01, 0.05)]
WEIGHT_GRID = [round(x, 2) for x in np.arange(0.0, 1.001, 0.05)]


def fit(cases: list[Case], stages: int = 3, rounds: int = 3) -> ModelParams:
    """Coordinate descent over the three grids.

    `stages` controls how much of the model is fitted: 1 = recentre only,
    2 = recentre + rescale, 3 = the whole thing. The staged form is what
    lets the report show each parameter earning its place out of sample
    instead of only the final number.
    """
    best = ModelParams()
    for _ in range(rounds):
        changed = False
        for lam in LAMBDA_GRID:
            cand = ModelParams(lam, best.scale, best.weight)
            if mean_crps(cases, cand) < mean_crps(cases, best) - 1e-9:
                best, changed = cand, True
        if stages >= 2:
            for sc in SCALE_GRID:
                cand = ModelParams(best.lam, sc, best.weight)
                if mean_crps(cases, cand) < mean_crps(cases, best) - 1e-9:
                    best, changed = cand, True
        if stages >= 3:
            for w in WEIGHT_GRID:
                cand = ModelParams(best.lam, best.scale, w)
                if mean_crps(cases, cand) < mean_crps(cases, best) - 1e-9:
                    best, changed = cand, True
        if not changed:
            break
    return best


def split_chronologically(cases: list[Case], train_frac: float = 0.5) -> tuple[list[Case], list[Case]]:
    """Split by DATE, not by row: every lead time of a given event lands on
    the same side, so a test case is never a different view of an event the
    fit has already seen."""
    dates = sorted({c.event_date for c in cases})
    cut = dates[int(len(dates) * train_frac)]
    return ([c for c in cases if c.event_date < cut],
            [c for c in cases if c.event_date >= cut])


# ---------------------------------------------------------------------------
# 4. Evaluation
# ---------------------------------------------------------------------------

@dataclass
class Scored:
    name: str
    n: int
    crps: float
    mad: float
    coverage68: float
    params: dict | None = None


def score(cases: list[Case], params: ModelParams, name: str) -> Scored:
    crps, mad, hits = [], [], []
    for c in cases:
        q = apply_model(np.array(c.q_market), c.spot, c.sigma, params)
        crps.append(crps_from_quantiles(q, c.realized))
        mad.append(abs(float(np.interp(0.5, _PROBS, q)) - c.realized))
        lo, hi = interval_from_quantiles(q)
        hits.append(lo <= c.realized <= hi)
    return Scored(name, len(cases), float(np.mean(crps)), float(np.mean(mad)),
                  float(np.mean(hits)), params.as_dict())


def score_baseline_random_walk(cases: list[Case]) -> Scored:
    crps, mad, hits = [], [], []
    for c in cases:
        if c.sigma is None or c.sigma <= 0:
            continue
        q = random_walk_quantiles(c.spot, c.sigma)
        crps.append(crps_from_quantiles(q, c.realized))
        mad.append(abs(c.spot - c.realized))
        lo, hi = interval_from_quantiles(q)
        hits.append(lo <= c.realized <= hi)
    if not crps:
        return Scored("random walk", 0, float("nan"), float("nan"), float("nan"))
    return Scored("random walk", len(crps), float(np.mean(crps)), float(np.mean(mad)), float(np.mean(hits)))


def score_baseline_naive(cases: list[Case]) -> Scored:
    errs = [abs(c.spot - c.realized) for c in cases]
    return Scored("naive (spot)", len(errs), float(np.mean(errs)), float(np.mean(errs)), float("nan"))


# The nested sequence -- each stage adds exactly one parameter.
STAGES = [
    ("market, raw", 0),
    ("+ recentred on spot", 1),
    ("+ width rescaled", 2),
    ("+ pooled with random walk", 3),
]


def evaluate_by_lead(cases: list[Case], train_frac: float = 0.5) -> list[dict]:
    """Fit on train, report on test, one row per lead time per stage."""
    out = []
    for lead in sorted({c.lead_hours for c in cases}, reverse=True):
        subset = [c for c in cases if c.lead_hours == lead]
        train, test = split_chronologically(subset, train_frac)
        if len(train) < 10 or len(test) < 10:
            continue

        entry = {
            "lead_hours": lead,
            "n_train": len(train),
            "n_test": len(test),
            "baselines": {
                "naive": score_baseline_naive(test).__dict__,
                "random_walk": score_baseline_random_walk(test).__dict__,
            },
            "stages": [],
        }
        for name, n_stage in STAGES:
            params = ModelParams() if n_stage == 0 else fit(train, stages=n_stage)
            s_test = score(test, params, name)
            s_train = score(train, params, name + " (train)")
            entry["stages"].append({
                "name": name,
                "params": params.as_dict(),
                "test": s_test.__dict__,
                "train_crps": s_train.crps,
            })
        out.append(entry)
    return out


def fit_production_params(cases: list[Case]) -> dict[int, ModelParams]:
    """Refit the full model on ALL data, per lead time -- what the live
    dashboard should use once the held-out evaluation has justified the
    shape of the model. Reported alongside the out-of-sample numbers, never
    instead of them."""
    return {
        lead: fit([c for c in cases if c.lead_hours == lead], stages=3)
        for lead in sorted({c.lead_hours for c in cases}, reverse=True)
    }
