"""End-to-end tests for aggregate_group / build_dashboard / build_touch_groups.

These build PriceDistribution objects by hand -- no network, no adapters --
so they pin the aggregation contract itself rather than any one source.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import aggregation
from aggregation import (
    HIGH_DIVERGENCE_PCT,
    aggregate_group,
    build_dashboard,
    build_touch_groups,
)
from common.distribution import (
    PriceBucket,
    PriceDistribution,
    SourceFetchResult,
    TouchFetchResult,
    TouchForecast,
)

TARGET = date(2026, 12, 31)


@pytest.fixture(autouse=True)
def _no_calibration(monkeypatch):
    """Most of these assert the mixture math, which the calibration
    correction would otherwise move. Tests that want it say so explicitly."""
    monkeypatch.setattr(aggregation, "APPLY_CALIBRATION_CORRECTIONS", False)


def _dist(name, buckets, weight=1.0, volume=None, resolve=None, **kw):
    return PriceDistribution(
        asset="BTC",
        source_type="prediction_market",
        source_name=name,
        target_date=TARGET,
        period_label="Dec 31, 2026",
        buckets=[PriceBucket(low=lo, high=hi, prob=p) for lo, hi, p in buckets],
        weight=weight,
        volume=volume if volume is not None else weight,
        resolve_datetime_utc=resolve,
        **kw,
    )


def _with_longshots(lo=90_000, step=2_000):
    """Eight sub-10% buckets plus two favourites -- the shape a real daily
    range market has, and the one the longshot correction acts on."""
    probs = [0.05] * 8 + [0.30, 0.30]
    return [(lo + step * i, lo + step * (i + 1), p) for i, p in enumerate(probs)]


def _uniform_over(lo, hi):
    """A flat distribution with closed edges -- mean is exactly (lo+hi)/2,
    so any drift shows up immediately."""
    n = 10
    step = (hi - lo) / n
    return [(lo + i * step, lo + (i + 1) * step, 1.0 / n) for i in range(n)]


# --- the mixture ----------------------------------------------------------

def test_single_source_round_trips_its_own_mean():
    f = aggregate_group([_dist("polymarket", _uniform_over(90_000, 110_000))])
    assert f is not None
    assert f.mean == pytest.approx(100_000, rel=1e-3)
    assert f.median == pytest.approx(100_000, rel=1e-3)
    assert sum(f.pdf) == pytest.approx(1.0)


def test_mixture_is_volume_weighted_not_a_simple_average():
    """The headline claim in the README: 10x the volume means 10x the
    probability mass, not 10x the vote on one number."""
    low = _dist("a", _uniform_over(90_000, 100_000), weight=9.0)
    high = _dist("b", _uniform_over(100_000, 110_000), weight=1.0)
    f = aggregate_group([low, high])
    simple_average = 100_000.0
    weighted = (0.9 * 95_000) + (0.1 * 105_000)
    assert f.mean == pytest.approx(weighted, rel=2e-3)
    assert f.mean < simple_average


def test_zero_volume_source_is_kept_but_negligible():
    """EPS_WEIGHT exists so an all-zero-volume group is still defined."""
    real = _dist("a", _uniform_over(90_000, 100_000), weight=1000.0)
    dead = _dist("b", _uniform_over(200_000, 210_000), weight=0.0)
    f = aggregate_group([real, dead])
    assert {s.source_name for s in f.sources} == {"a", "b"}
    assert f.mean == pytest.approx(95_000, rel=1e-2)

    both_dead = aggregate_group([
        _dist("a", _uniform_over(90_000, 100_000), weight=0.0),
        _dist("b", _uniform_over(100_000, 110_000), weight=0.0),
    ])
    assert both_dead is not None
    assert both_dead.mean == pytest.approx(100_000, rel=1e-2)


def test_empty_and_unusable_groups_return_none():
    assert aggregate_group([]) is None
    assert aggregate_group([_dist("a", [(None, None, 1.0)])]) is None


# --- disagreement ---------------------------------------------------------

def test_agreeing_sources_report_no_disagreement():
    f = aggregate_group([
        _dist("a", _uniform_over(99_000, 101_000)),
        _dist("b", _uniform_over(99_000, 101_000)),
    ])
    assert f.disagreement_pct == pytest.approx(0.0, abs=0.05)
    assert f.high_divergence is False


def test_far_apart_sources_are_flagged_rather_than_smoothed():
    """Two tight-but-distant curves average into one plausible-looking
    curve; the disagreement score is what catches that."""
    f = aggregate_group([
        _dist("a", _uniform_over(80_000, 82_000)),
        _dist("b", _uniform_over(118_000, 120_000)),
    ])
    assert f.disagreement_pct > HIGH_DIVERGENCE_PCT
    assert f.high_divergence is True


def test_single_source_is_never_flagged_divergent():
    f = aggregate_group([_dist("a", _uniform_over(80_000, 120_000))])
    assert f.disagreement_pct == 0.0
    assert f.high_divergence is False


# --- confidence -----------------------------------------------------------

def test_confidence_rises_with_volume():
    thin = aggregate_group([_dist("a", _uniform_over(99_000, 101_000), weight=100.0)])
    thick = aggregate_group([_dist("a", _uniform_over(99_000, 101_000), weight=1_000_000.0)])
    assert thick.confidence_score > thin.confidence_score
    assert thick.confidence_tier == "high"
    assert thin.confidence_tier == "low"


def test_concentrated_volume_scores_lower_confidence_than_the_same_dollars_spread_wide():
    """A $6M market backed by two wallets is not the same evidence as $6M
    backed by a crowd, and the badge is the number a reader acts on."""
    broad = aggregate_group([
        _dist("a", _uniform_over(99_000, 101_000), weight=500_000.0, concentration_discount=1.0)
    ])
    concentrated = aggregate_group([
        _dist("a", _uniform_over(99_000, 101_000), weight=500_000.0, concentration_discount=0.05)
    ])
    assert concentrated.confidence_score < broad.confidence_score
    assert concentrated.confidence_tier != "high"
    # Raw dollars traded are still reported truthfully for display.
    assert concentrated.total_volume == pytest.approx(broad.total_volume)
    assert concentrated.effective_volume < concentrated.total_volume


def test_unmeasured_concentration_gets_the_benefit_of_the_doubt():
    """Kalshi/Manifold expose no wallet data, so `None` must not be read as
    'maximally concentrated'."""
    unmeasured = aggregate_group([_dist("k", _uniform_over(99_000, 101_000), weight=500_000.0)])
    assert unmeasured.effective_volume == pytest.approx(unmeasured.total_volume)


# --- confidence bands -----------------------------------------------------

def test_confidence_bands_are_nested_and_ordered():
    f = aggregate_group([_dist("a", _uniform_over(80_000, 120_000))])
    assert [b.level for b in f.confidence_bands] == aggregation.CONFIDENCE_LEVELS
    for inner, outer in zip(f.confidence_bands, f.confidence_bands[1:]):
        assert outer.low <= inner.low
        assert outer.high >= inner.high
    assert f.ci_68[0] > f.ci_95[0]
    assert f.ci_68[1] < f.ci_95[1]
    assert f.ci_95[0] < f.median < f.ci_95[1]


# --- the calibration correction, end to end -------------------------------

def test_correction_reports_the_factor_it_actually_delivered(monkeypatch):
    monkeypatch.setattr(aggregation, "APPLY_CALIBRATION_CORRECTIONS", True)
    resolve = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    f = aggregate_group([_dist("polymarket", _with_longshots(), resolve=resolve)])

    expected = aggregation._log_interp(aggregation.SHRINK_BY_LEAD_HOURS, f.lead_hours)
    assert f.longshot_shrink_applied == pytest.approx(expected, rel=1e-6)
    assert f.lead_hours == pytest.approx(6.0, abs=0.1)
    # The CI width correction is retired: the 41%-coverage measurement it
    # was built on was an artifact of reading the interval off bucket
    # midpoints. Width is now handled by model.WIDTH_SCALE instead.
    assert f.ci_width_mult_applied == 1.0


def test_correction_can_be_switched_off_wholesale(monkeypatch):
    monkeypatch.setattr(aggregation, "APPLY_CALIBRATION_CORRECTIONS", False)
    resolve = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    f = aggregate_group([_dist("a", _uniform_over(90_000, 110_000), resolve=resolve)])
    assert f.longshot_shrink_applied == 1.0
    assert f.ci_width_mult_applied == 1.0


def test_lead_time_does_not_depend_on_source_order(monkeypatch):
    """Regression: the group used to read its lead time off distributions[0],
    so which source won depended on adapter completion order and a card's
    correction could change between refreshes with identical data."""
    monkeypatch.setattr(aggregation, "APPLY_CALIBRATION_CORRECTIONS", True)
    now = datetime.now(timezone.utc)
    a = _dist("a", _uniform_over(99_000, 101_000), weight=1.0,
              resolve=(now + timedelta(hours=6)).isoformat())
    b = _dist("b", _uniform_over(99_000, 101_000), weight=1.0,
              resolve=(now + timedelta(hours=120)).isoformat())

    forward = aggregate_group([a, b])
    backward = aggregate_group([b, a])
    # The residual here is wall-clock drift between the two calls, orders of
    # magnitude below the swing an order-dependent lead time would produce
    # (the 6h and 120h corrections differ by ~0.5 and ~0.9 respectively).
    assert forward.lead_hours == pytest.approx(backward.lead_hours, abs=1e-3)
    assert forward.longshot_shrink_applied == pytest.approx(backward.longshot_shrink_applied, abs=1e-5)

    # And it is genuinely a blend, not whichever source happened to be first.
    assert 6.0 < forward.lead_hours < 120.0
    near_only = aggregate_group([a]).lead_hours
    far_only = aggregate_group([b]).lead_hours
    assert near_only < forward.lead_hours < far_only


def test_each_source_carries_its_own_lead_time(monkeypatch):
    monkeypatch.setattr(aggregation, "APPLY_CALIBRATION_CORRECTIONS", True)
    now = datetime.now(timezone.utc)
    f = aggregate_group([
        _dist("near", _with_longshots(), resolve=(now + timedelta(hours=6)).isoformat()),
        _dist("far", _with_longshots(), resolve=(now + timedelta(hours=140)).isoformat()),
    ])
    by_name = {s.source_name: s for s in f.sources}
    assert by_name["near"].lead_hours == pytest.approx(6.0, abs=0.1)
    assert by_name["far"].lead_hours == pytest.approx(140.0, abs=0.1)
    assert by_name["near"].longshot_shrink_applied < by_name["far"].longshot_shrink_applied


# --- thresholds -----------------------------------------------------------

def test_threshold_probabilities_are_monotone_and_per_source():
    f = aggregate_group([
        _dist("a", _uniform_over(90_000, 100_000)),
        _dist("b", _uniform_over(100_000, 110_000)),
    ])
    probs = [r.prob_gt_aggregate for r in f.thresholds]
    assert probs == sorted(probs, reverse=True)
    for row in f.thresholds:
        assert set(row.per_source_prob_gt) == {"a", "b"}
        assert row.per_source_prob_gt["b"] >= row.per_source_prob_gt["a"]


# --- grouping -------------------------------------------------------------

def test_build_dashboard_groups_by_date_and_keeps_partial_results():
    d2 = date(2027, 1, 15)
    ok = SourceFetchResult(
        source_name="a", source_type="prediction_market",
        distributions=[
            _dist("a", _uniform_over(99_000, 101_000)),
            PriceDistribution(
                asset="BTC", source_type="prediction_market", source_name="a",
                target_date=d2, period_label="Jan 15, 2027",
                buckets=[PriceBucket(low=lo, high=hi, prob=p) for lo, hi, p in _uniform_over(100_000, 120_000)],
                weight=1.0, volume=1.0,
            ),
        ],
    )
    broken = SourceFetchResult(source_name="b", source_type="prediction_market", error="discovery failed")

    forecasts, errors = build_dashboard([ok, broken])
    assert [f.target_date for f in forecasts] == [TARGET.isoformat(), d2.isoformat()]
    assert errors == [{"source": "b", "source_type": "prediction_market", "error": "discovery failed"}]


def test_touch_groups_keep_the_higher_volume_event_per_source_and_date():
    """One platform can have two touch events landing on the same date; a
    UI that keys rows by source name would otherwise collide."""
    exp = date(2026, 12, 31)
    small = TouchForecast(asset="BTC", source_name="polymarket", expiry_date=exp,
                          period_label="2026", thresholds=[], total_volume=1_000.0)
    big = TouchForecast(asset="BTC", source_name="polymarket", expiry_date=exp,
                        period_label="2026", thresholds=[], total_volume=50_000.0)
    other = TouchForecast(asset="BTC", source_name="kalshi", expiry_date=exp,
                          period_label="2026", thresholds=[], total_volume=2_000.0)

    groups, errors = build_touch_groups([
        TouchFetchResult(source_name="polymarket", touches=[small, big]),
        TouchFetchResult(source_name="kalshi", touches=[other]),
        TouchFetchResult(source_name="manifold", error="no oil markets"),
    ])
    assert len(groups) == 1
    names = [t.source_name for t in groups[0].sources]
    assert names == ["kalshi", "polymarket"]  # sorted, one row each
    kept = [t for t in groups[0].sources if t.source_name == "polymarket"][0]
    assert kept.total_volume == 50_000.0
    assert errors == [{"source": "manifold", "error": "no oil markets"}]


def test_thresholds_land_where_probability_actually_varies():
    """Regression: the ladder used to span the whole padded grid, so most
    rows sat out in the exponential tails reading 100% or 0% -- filler, and
    actively bad now that these probabilities are the card's headline."""
    f = aggregate_group([_dist("a", _uniform_over(99_000, 101_000), weight=50_000.0)])
    probs = [r.prob_gt_aggregate for r in f.thresholds]
    assert len(probs) >= 3
    informative = [p for p in probs if 0.02 < p < 0.98]
    assert len(informative) >= 2, probs
    # And they bracket the distribution rather than sitting off in a tail.
    assert min(r.threshold for r in f.thresholds) < f.median
    assert max(r.threshold for r in f.thresholds) > f.median


def test_thresholds_adapt_to_a_tight_distribution():
    """A near-term card with a $2k-wide distribution needs a finer ladder
    than a far-dated one spanning $40k; a fixed grid-span ladder gave the
    tight one nothing usable."""
    tight = aggregate_group([_dist("a", _uniform_over(99_500, 100_500))])
    wide = aggregate_group([_dist("a", _uniform_over(80_000, 120_000))])
    tight_step = tight.thresholds[1].threshold - tight.thresholds[0].threshold
    wide_step = wide.thresholds[1].threshold - wide.thresholds[0].threshold
    assert tight_step < wide_step
    assert all(0.0 < r.prob_gt_aggregate < 1.0 for r in tight.thresholds[1:-1])


# --- the forecast model (model.py) -----------------------------------------
#
# What separates "what the market says" from "what we predict". These pin
# the behaviour the held-out backtest justified, not the transform's
# internals (those are in test_model.py).

def test_spot_anchoring_moves_the_forecast_onto_spot():
    """The single biggest measured improvement: the market's own location
    is discarded and the curve is centred on the current price."""
    dists = [_dist("polymarket", _uniform_over(90_000, 110_000))]
    raw = aggregate_group(dists)
    anchored = aggregate_group(dists, spot=104_000.0)
    assert raw.median == pytest.approx(100_000, rel=1e-2)
    assert anchored.median == pytest.approx(104_000, rel=1e-3)
    assert anchored.spot_anchor_price == 104_000.0


def test_spot_anchoring_narrows_rather_than_widens():
    """The correction this replaced widened intervals by up to 1.79x. The
    one that survived out-of-sample testing goes the other way."""
    dists = [_dist("a", _uniform_over(90_000, 110_000))]
    raw = aggregate_group(dists)
    anchored = aggregate_group(dists, spot=100_000.0)
    raw_width = raw.ci_68[1] - raw.ci_68[0]
    new_width = anchored.ci_68[1] - anchored.ci_68[0]
    assert new_width < raw_width
    assert new_width / raw_width == pytest.approx(aggregation.model.WIDTH_SCALE, rel=0.05)


def test_thresholds_and_bands_describe_the_anchored_forecast():
    """Everything downstream has to be read off the model's output, not the
    raw market curve -- otherwise the card's headline probabilities would
    describe a distribution the forecast no longer is."""
    dists = [_dist("a", _uniform_over(90_000, 110_000))]
    anchored = aggregate_group(dists, spot=106_000.0)
    # P(above spot) should sit near 50% once the curve is centred there.
    nearest = min(anchored.thresholds, key=lambda r: abs(r.threshold - 106_000))
    assert 0.3 < nearest.prob_gt_aggregate < 0.7
    assert anchored.ci_95[0] < 106_000 < anchored.ci_95[1]
    assert sum(anchored.pdf) == pytest.approx(1.0)


def test_no_spot_leaves_the_market_curve_untouched():
    """A missing spot price degrades the forecast but must never break it."""
    dists = [_dist("a", _uniform_over(90_000, 110_000))]
    for spot in (None, 0.0, -5.0):
        f = aggregate_group(dists, spot=spot)
        assert f is not None
        assert f.median == pytest.approx(100_000, rel=1e-2)
        assert f.spot_anchor_price is None


def test_anchoring_survives_spot_far_outside_the_market_grid():
    """The regression the quantile-transform-and-rebin approach exists for:
    shifting the existing grid instead would push most of the mass off the
    end whenever spot sat well outside the market's range."""
    dists = [_dist("a", _uniform_over(90_000, 110_000))]
    f = aggregate_group(dists, spot=180_000.0)
    assert f.median == pytest.approx(180_000, rel=1e-3)
    assert sum(f.pdf) == pytest.approx(1.0)
    assert f.grid_edges[0] < 180_000 < f.grid_edges[-1]


def test_build_dashboard_passes_spot_through():
    ok = SourceFetchResult(
        source_name="a", source_type="prediction_market",
        distributions=[_dist("a", _uniform_over(90_000, 110_000))],
    )
    forecasts, _ = build_dashboard([ok], spot=97_500.0)
    assert forecasts[0].median == pytest.approx(97_500, rel=1e-3)
