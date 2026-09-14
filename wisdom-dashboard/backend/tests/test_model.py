"""Tests for model.py -- the location-scale transform that turns the
market's implied distribution into a forecast."""
import numpy as np
import pytest

import model


def _gaussian(lo=50.0, hi=150.0, mu=100.0, sd=10.0, n=400):
    edges = np.linspace(lo, hi, n + 1)
    c = (edges[:-1] + edges[1:]) / 2.0
    pdf = np.exp(-0.5 * ((c - mu) / sd) ** 2)
    return edges, pdf / pdf.sum()


def _stats(edges, pdf):
    c = (edges[:-1] + edges[1:]) / 2.0
    mean = float(np.sum(c * pdf))
    sd = float(np.sqrt(np.sum(pdf * (c - mean) ** 2)))
    return mean, sd


def test_recentres_on_spot_and_rescales_width():
    edges, pdf = _gaussian()
    e2, p2 = model.anchor_to_spot(edges, pdf, 120.0, lam=0.0, scale=0.65)
    mean, sd = _stats(e2, p2)
    assert mean == pytest.approx(120.0, abs=0.2)
    assert sd == pytest.approx(6.5, rel=0.03)


def test_mass_is_preserved():
    edges, pdf = _gaussian()
    for spot in (60.0, 100.0, 140.0, 400.0):
        _, p2 = model.anchor_to_spot(edges, pdf, spot)
        assert p2.sum() == pytest.approx(1.0)
        assert np.all(p2 >= 0.0)


def test_lambda_one_leaves_location_alone():
    edges, pdf = _gaussian()
    e2, p2 = model.anchor_to_spot(edges, pdf, 130.0, lam=1.0, scale=1.0)
    mean, sd = _stats(e2, p2)
    assert mean == pytest.approx(100.0, abs=0.2)
    assert sd == pytest.approx(10.0, rel=0.03)


def test_lambda_interpolates_between_market_and_spot():
    edges, pdf = _gaussian(mu=100.0)
    e2, p2 = model.anchor_to_spot(edges, pdf, 140.0, lam=0.5, scale=1.0)
    mean, _ = _stats(e2, p2)
    assert mean == pytest.approx(120.0, abs=0.3)


def test_skew_survives_the_transform():
    """The transform is location-scale on purpose: whatever asymmetry the
    market expressed is the one thing the model keeps from it."""
    edges = np.linspace(0.0, 200.0, 401)
    c = (edges[:-1] + edges[1:]) / 2.0
    pdf = np.where(c > 0, np.exp(-((np.log(np.maximum(c, 1e-9)) - 4.3) ** 2) / (2 * 0.25 ** 2)) / np.maximum(c, 1e-9), 0.0)
    pdf = pdf / pdf.sum()

    def skew(edges_, pdf_):
        cc = (edges_[:-1] + edges_[1:]) / 2.0
        m = np.sum(cc * pdf_)
        sd = np.sqrt(np.sum(pdf_ * (cc - m) ** 2))
        return float(np.sum(pdf_ * ((cc - m) / sd) ** 3))

    before = skew(edges, pdf)
    e2, p2 = model.anchor_to_spot(edges, pdf, 90.0)
    assert before > 0.3  # genuinely skewed to start with
    assert skew(e2, p2) == pytest.approx(before, rel=0.12)


def test_grid_follows_a_distant_spot():
    """Shifting the original grid would push the distribution off its end;
    the rebin has to move the grid with it."""
    edges, pdf = _gaussian()
    e2, p2 = model.anchor_to_spot(edges, pdf, 900.0)
    mean, _ = _stats(e2, p2)
    assert mean == pytest.approx(900.0, abs=2.0)
    assert e2[0] < 900.0 < e2[-1]
    assert p2.sum() == pytest.approx(1.0)


def test_degenerate_inputs_return_the_original():
    edges, pdf = _gaussian()
    for bad in (0.0, -10.0, float("nan")):
        e2, p2 = model.anchor_to_spot(edges, pdf, bad)
        assert np.array_equal(e2, edges) and np.array_equal(p2, pdf)
    zero = np.zeros_like(pdf)
    e2, p2 = model.anchor_to_spot(edges, zero, 100.0)
    assert np.array_equal(p2, zero)


def test_shipped_constants_match_what_the_backtest_justified():
    """If these move, results/forecast_model/report.md is stale."""
    assert model.RECENTRE_LAMBDA == 0.0
    assert model.WIDTH_SCALE == 0.65
    assert model.APPLY_SPOT_ANCHORING is True
