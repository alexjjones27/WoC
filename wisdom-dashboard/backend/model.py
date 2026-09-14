"""The forecast model: what turns the market's implied distribution into an
actual price forecast.

Everything upstream of this file answers "what does the market say." This
file answers "what should we predict," and they are not the same question.
Measured on 97 resolved Polymarket events with a chronological train/test
split (see ../../results/forecast_model/report.md and
../../src/forecast_model.py), the market's raw distribution loses to a
trivial random walk at every horizon. Two corrections fix that, and both
are about as simple as a correction can be.

--- 1. Anchor the location to spot (RECENTRE_LAMBDA = 0) ---

The distribution is moved so its median sits on the CURRENT SPOT PRICE,
keeping its shape, skew and relative width exactly.

The fitted weight on the market's own location came out at zero. Not
"small": the grid search put it at the boundary, on pooled training data
and independently at three of four horizons. The market's view of WHERE
bitcoin will be carries no measurable information over simply reading the
current price, and acting on it costs 15-35% of forecast accuracy.

That should not be shocking. For an approximately efficient market, spot
IS the optimal point forecast, and any deviation the reconstruction shows
is dominated by stale quotes, bid-ask noise and bucket discretization. The
surprising part was that this dashboard fetched spot on every refresh and
used it only to draw the grey history line behind the fan chart.

--- 2. Narrow the width (WIDTH_SCALE = 0.65) ---

Spread about the new centre is multiplied by 0.65. Once a distribution is
correctly centred it misses less often, so the width that was appropriate
for a mis-centred curve is too wide for a well-centred one.

This reverses the direction of the correction this file replaces.
`aggregation.py` used to WIDEN intervals, by up to 1.79x at a 6h lead,
on the strength of a backtest that measured 41% coverage against a nominal
68%. That measurement was an artifact: it computed the interval as the gap
between two bucket MIDPOINTS, which at short horizons, where the
distribution collapses into one or two buckets, is far narrower than the
interval the market actually expressed. Measured against a faithful
reconstruction the same events cover 72%, not 41% -- mildly over-covered.
The old correction was widening a distribution that was already too wide.

--- What the market does contribute ---

Shape. The two corrections above discard the market's location and rescale
its width, which leaves its SHAPE: skew, fat tails, the lumps a real
threshold ladder has and a normal distribution structurally cannot
express. That residual is worth something. With the corrections applied
the model beats a trailing-volatility random walk at all four horizons out
of sample, by 1.5-4.3%, where the raw market lost to it by 15-45%.

A narrow win, and honestly reported as one. But it is the difference
between a forecast that adds information over free public data and one
that does not.

--- Caveats that belong on every number here ---

One asset, one platform, one ~3.5 month trending window, 97 events whose
daily overlap makes them far fewer than 97 independent draws. Both
constants are fitted values from that window, not laws. They live at the
top of this file precisely so they can be refitted:
`python3 scripts/run_forecast_model.py`.
"""
from __future__ import annotations

import numpy as np

# --- fitted constants (see module docstring; refit with the runner) ---

# Weight on the market's own location. 0.0 = centre entirely on spot,
# 1.0 = keep the market's median where it is.
RECENTRE_LAMBDA = 0.0

# Multiplier on spread about the new centre. Fitted at 0.60 by CRPS alone;
# 0.65 is shipped because it costs under 0.6% of CRPS at every horizon and
# is materially better calibrated (68%-interval coverage 63-82% against
# nominal 68%, versus 61-69% at the CRPS optimum, which under-covers at the
# long horizons). The dashboard displays intervals, so interval calibration
# is worth that much CRPS.
WIDTH_SCALE = 0.65

APPLY_SPOT_ANCHORING = True

# Probability grid the transform works on.
_N_Q = 401
_PROBS = np.linspace(0.5 / _N_Q, 1.0 - 0.5 / _N_Q, _N_Q)

# Grid padding past the outermost transformed quantile, as a fraction of
# the transformed interquantile span.
_PAD_FRAC = 0.12


def anchor_to_spot(
    grid_edges: np.ndarray,
    pdf: np.ndarray,
    spot: float,
    lam: float = RECENTRE_LAMBDA,
    scale: float = WIDTH_SCALE,
) -> tuple[np.ndarray, np.ndarray]:
    """Recentre `pdf` on `spot` and rescale its width, returning a new
    (grid_edges, pdf).

    The transform is applied to the distribution's QUANTILE curve and then
    re-binned, rather than by shifting the grid, so the result keeps the
    original shape exactly (skew and tail weight survive a location-scale
    move) and lands on a grid that actually spans it. Shifting the existing
    grid instead would push mass off whichever end the distribution moved
    toward whenever spot sat far from the market's median, which is exactly
    the case this correction exists for.
    """
    edges = np.asarray(grid_edges, dtype=float)
    p = np.asarray(pdf, dtype=float)
    mass = p.sum()
    if mass <= 0 or not np.isfinite(spot) or spot <= 0:
        return edges, p

    cdf = np.concatenate(([0.0], np.cumsum(p / mass)))
    q = np.interp(_PROBS, cdf, edges)
    median = float(np.interp(0.5, _PROBS, q))
    new_centre = spot + lam * (median - spot)
    q_new = new_centre + scale * (q - median)

    span = float(q_new[-1] - q_new[0])
    if not np.isfinite(span) or span <= 0:
        return edges, p
    pad = span * _PAD_FRAC
    new_edges = np.linspace(max(0.0, q_new[0] - pad), q_new[-1] + pad, len(edges))

    new_cdf = np.interp(new_edges, q_new, _PROBS, left=0.0, right=1.0)
    new_pdf = np.clip(np.diff(new_cdf), 0.0, None)
    total = new_pdf.sum()
    if total <= 0:
        return edges, p
    return new_edges, new_pdf / total
