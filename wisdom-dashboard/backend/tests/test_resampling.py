"""Tests for the bucket -> common-grid resamplers in aggregation.py.

These are the functions that decide how much a coarse market histogram
distorts the mean/std it gets scored on, so they are checked against the
analytic answer, not just for "looks plausible".
"""
import numpy as np
import pytest

from aggregation import (
    PAD_HALF_LIVES,
    _build_group_grid,
    _percentile,
    _cdf_at_edges,
    _prob_gt,
    _log_interp,
    _resample_bucket,
    _resample_open_high,
    _resample_open_low,
)
from common.distribution import PriceBucket, PriceDistribution


def _grid(lo, hi, n=4000):
    return np.linspace(lo, hi, n + 1)


def _mean(grid, pdf):
    centers = (grid[:-1] + grid[1:]) / 2.0
    return float(np.sum(centers * pdf) / np.sum(pdf))


# --- closed buckets -------------------------------------------------------

def test_closed_bucket_preserves_mass_and_is_uniform():
    grid = _grid(0.0, 100.0)
    pdf = _resample_bucket(20.0, 40.0, 0.3, grid)
    assert pdf.sum() == pytest.approx(0.3)
    assert _mean(grid, pdf) == pytest.approx(30.0, abs=0.05)


def test_closed_bucket_off_grid_edges_are_clipped_not_wrapped():
    grid = _grid(0.0, 100.0)
    pdf = _resample_bucket(90.0, 200.0, 1.0, grid)
    # Only the in-grid tenth of the bucket's width lands on the grid.
    assert pdf.sum() == pytest.approx(10.0 / 110.0, rel=1e-3)


def test_degenerate_zero_width_bucket_lands_in_one_cell():
    grid = _grid(0.0, 100.0)
    pdf = _resample_bucket(50.0, 50.0, 0.25, grid)
    assert pdf.sum() == pytest.approx(0.25)
    assert np.count_nonzero(pdf) == 1


# --- open tails -----------------------------------------------------------

def test_open_high_tail_has_exponential_mean():
    """f(x) = (1/scale)exp(-(x-low)/scale) for x >= low, so E[x] = low + scale."""
    low, scale, prob = 100.0, 10.0, 0.4
    grid = _grid(low - 50.0, low + 30 * scale)  # far enough out to ignore truncation
    pdf = _resample_open_high(low, prob, scale, grid)
    assert pdf.sum() == pytest.approx(prob, rel=1e-6)
    assert _mean(grid, pdf) == pytest.approx(low + scale, rel=1e-3)


def test_open_low_tail_has_exponential_mean():
    high, scale, prob = 100.0, 10.0, 0.4
    grid = _grid(high - 30 * scale, high + 50.0)
    pdf = _resample_open_low(high, prob, scale, grid)
    assert pdf.sum() == pytest.approx(prob, rel=1e-6)
    assert _mean(grid, pdf) == pytest.approx(high - scale, rel=1e-3)


def test_open_tails_put_no_mass_on_the_wrong_side():
    grid = _grid(0.0, 200.0)
    hi_tail = _resample_open_high(100.0, 1.0, 10.0, grid)
    lo_tail = _resample_open_low(100.0, 1.0, 10.0, grid)
    centers = (grid[:-1] + grid[1:]) / 2.0
    assert hi_tail[centers < 100.0].sum() == pytest.approx(0.0, abs=1e-12)
    assert lo_tail[centers > 100.0].sum() == pytest.approx(0.0, abs=1e-12)


def test_tail_shape_does_not_drift_with_grid_padding():
    """The bug the exponential tails replaced uniform padding to fix: with
    a uniform tail the implied mean moves with whatever padding width the
    grid happened to choose, without bound. The exponential tail converges
    instead -- by PAD_HALF_LIVES (the padding the module actually uses) it
    has essentially stopped moving."""
    low, scale = 100.0, 10.0
    exp_means, uni_means = [], []
    for pad in (PAD_HALF_LIVES, 15.0, 40.0):
        grid = _grid(low - scale * pad, low + scale * pad)
        exp_means.append(_mean(grid, _resample_open_high(low, 1.0, scale, grid)))
        uni_means.append(_mean(grid, _resample_bucket(low, float(grid[-1]), 1.0, grid)))

    # Exponential: converged, and on the analytic answer (low + scale).
    assert max(exp_means) - min(exp_means) < 0.05
    assert all(m == pytest.approx(low + scale, abs=0.05) for m in exp_means)

    # Uniform: the same tail's mean grows without bound as padding widens,
    # which is exactly why it biased mean/std by an arbitrary amount.
    assert uni_means[-1] - uni_means[0] > 100.0


def test_open_tail_captures_essentially_all_its_mass_at_module_padding():
    """PAD_HALF_LIVES is only defensible if the mass falling off the end of
    the grid really is negligible -- pin the number rather than trusting the
    comment."""
    low, scale = 100.0, 10.0
    grid = _grid(low - scale * PAD_HALF_LIVES, low + scale * PAD_HALF_LIVES)
    captured = float(_resample_open_high(low, 1.0, scale, grid).sum())
    assert captured > 0.999
    assert 1.0 - captured == pytest.approx(np.exp(-PAD_HALF_LIVES), rel=0.01)


def test_group_grid_pads_far_enough_to_hold_the_tails():
    dists = [
        PriceDistribution(
            asset="BTC", source_type="prediction_market", source_name="s",
            target_date=__import__("datetime").date(2026, 1, 1), period_label="p",
            buckets=[
                PriceBucket(low=None, high=100.0, prob=0.2),
                PriceBucket(low=100.0, high=110.0, prob=0.6),
                PriceBucket(low=110.0, high=None, prob=0.2),
            ],
            weight=1.0,
        )
    ]
    grid, scale = _build_group_grid(dists)
    assert scale == pytest.approx(10.0)
    lost = float(np.exp(-(grid[-1] - 110.0) / scale))
    assert lost < 1e-3


def test_group_grid_returns_none_when_every_edge_is_open():
    dists = [
        PriceDistribution(
            asset="BTC", source_type="prediction_market", source_name="s",
            target_date=__import__("datetime").date(2026, 1, 1), period_label="p",
            buckets=[PriceBucket(low=None, high=None, prob=1.0)],
            weight=1.0,
        )
    ]
    assert _build_group_grid(dists) == (None, None)


# --- percentile / threshold helpers ---------------------------------------

def test_percentile_recovers_a_known_uniform_distribution():
    grid = _grid(0.0, 100.0)
    pdf = _resample_bucket(0.0, 100.0, 1.0, grid)
    cdf = _cdf_at_edges(grid, pdf)
    for q, expected in [(0.1, 10.0), (0.5, 50.0), (0.9, 90.0)]:
        assert _percentile(grid, cdf, q) == pytest.approx(expected, abs=0.05)


def test_prob_gt_is_one_below_the_grid_and_zero_above():
    grid = _grid(0.0, 100.0)
    pdf = _resample_bucket(0.0, 100.0, 1.0, grid)
    cdf = _cdf_at_edges(grid, pdf)
    assert _prob_gt(grid, cdf, -10.0) == 1.0
    assert _prob_gt(grid, cdf, 110.0) == 0.0
    assert _prob_gt(grid, cdf, 25.0) == pytest.approx(0.75, abs=0.01)


# --- lead-time interpolation ----------------------------------------------

def test_log_interp_hits_its_anchors_exactly():
    anchors = {6.0: 0.16, 24.0: 0.20, 72.0: 0.30, 144.0: 0.74}
    for h, v in anchors.items():
        assert _log_interp(anchors, h) == pytest.approx(v)


def test_log_interp_clamps_rather_than_extrapolating():
    """There is no measurement past 144h or under 6h, so the correction
    must flatten out rather than run off to an invented value."""
    anchors = {6.0: 0.16, 144.0: 0.74}
    assert _log_interp(anchors, 0.5) == 0.16
    assert _log_interp(anchors, 10_000.0) == 0.74


def test_log_interp_is_monotone_between_anchors():
    anchors = {6.0: 0.16, 24.0: 0.20, 72.0: 0.30, 144.0: 0.74}
    xs = [6, 12, 24, 48, 72, 100, 144]
    ys = [_log_interp(anchors, float(x)) for x in xs]
    assert all(a <= b for a, b in zip(ys, ys[1:]))


# --- CI width multiplier --------------------------------------------------

def test_ci_multiplier_matches_its_anchors():
    from aggregation import CI_WIDTH_MULT_BY_LEAD_HOURS, _ci_width_multiplier

    for h, v in CI_WIDTH_MULT_BY_LEAD_HOURS.items():
        assert _ci_width_multiplier(h) == pytest.approx(v)


def test_ci_multiplier_tapers_to_no_correction_below_the_measured_range():
    """Clamping at the 6h anchor applied a 1.79x widening to forecasts
    minutes from resolution, which pushed 68% intervals to 94-100%
    coverage in the aggregate backtest. Below the lowest measured lead the
    correction tapers toward 1.0 instead."""
    from aggregation import _ci_width_multiplier

    assert _ci_width_multiplier(6.0) == pytest.approx(1.792)
    assert _ci_width_multiplier(3.0) == pytest.approx(1.396, abs=0.001)
    assert _ci_width_multiplier(5 / 60) < 1.02
    assert _ci_width_multiplier(0.0) == pytest.approx(1.0)

    # Monotone from 0 up to the lowest anchor -- no discontinuity at 6h.
    xs = [0.0, 0.5, 1.0, 2.0, 4.0, 5.9, 6.0]
    ys = [_ci_width_multiplier(x) for x in xs]
    assert all(a <= b + 1e-12 for a, b in zip(ys, ys[1:]))


def test_longshot_shrink_still_clamps_below_the_measured_range():
    """Deliberately NOT tapered: a cheap contract minutes from expiry is if
    anything less likely to pay off than the 6h measurement says, so
    tapering toward 'no correction' would be the unjustified move."""
    from aggregation import SHRINK_BY_LEAD_HOURS, _log_interp

    assert _log_interp(SHRINK_BY_LEAD_HOURS, 5 / 60) == pytest.approx(0.16)
    assert _log_interp(SHRINK_BY_LEAD_HOURS, 0.5) == pytest.approx(0.16)
