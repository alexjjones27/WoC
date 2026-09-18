"""Stage 2 mechanics, checked against known ground truth where possible."""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from conftest import make_history
from src.ingest.synthetic import generate_panel
from src.model.debias import (
    Distribution,
    apply_firm_offsets,
    attach_implied_returns,
    attach_sector_adjustment,
    effective_n,
    estimate_beta,
    estimate_firm_offsets,
    firm_variance_share,
    weighted_quantile,
)

NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)


# --- weighted statistics ---------------------------------------------------


def test_weighted_quantile_matches_unweighted_when_weights_are_equal():
    v = np.array([1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    w = np.ones_like(v)
    assert weighted_quantile(v, w, 0.5) == pytest.approx(np.median(v), abs=0.3)


def test_weighted_quantile_follows_the_weights():
    v = np.array([0.0, 10.0])
    assert weighted_quantile(v, np.array([99.0, 1.0]), 0.5) < 1.0
    assert weighted_quantile(v, np.array([1.0, 99.0]), 0.5) > 9.0


def test_effective_n():
    assert effective_n(np.ones(10)) == pytest.approx(10.0)
    # One dominant weight collapses the effective sample toward one.
    assert effective_n(np.array([100.0, 1, 1, 1])) < 1.2
    assert effective_n(np.zeros(5)) == 0.0


def test_distribution_handles_empty_input():
    d = Distribution.summarise([], [])
    assert d.n == 0 and np.isnan(d.median)


# --- implied returns and the guards ---------------------------------------


def _panel(rows):
    df = pd.DataFrame(rows)
    df.attrs["pit_mode"] = "test"
    return df


def test_implied_return_uses_the_anchor_price_not_todays_price(history):
    d = history.dates[400]
    anchor = history.close_strictly_before(d)[1]
    today = history.closes[-1]
    df = attach_implied_returns(
        _panel([{"ticker": "TEST", "analyst_firm": "A", "action_date": d,
                 "price_target": anchor * 1.2, "vendor_spot_at_action": None}]),
        history,
    )
    assert df.iloc[0]["spot_pit"] == pytest.approx(anchor)
    assert df.iloc[0]["implied_return"] == pytest.approx(0.2, abs=1e-9)
    assert df.iloc[0]["spot_pit"] != pytest.approx(today)


def test_split_contaminated_target_is_rescued_not_silently_wrong():
    h = make_history(splits=((date(2020, 6, 1), 4.0),))
    d = date(2019, 3, 4)
    anchor = h.close_strictly_before(d)[1]
    as_quoted = anchor * 1.15 * 4.0  # the money-of-the-day target

    df = attach_implied_returns(
        _panel([{"ticker": "TEST", "analyst_firm": "A", "action_date": d,
                 "price_target": as_quoted, "vendor_spot_at_action": None}]),
        h,
    )
    row = df.iloc[0]
    assert bool(row["flag_split_rescued"])
    assert row["implied_return"] == pytest.approx(0.15, abs=1e-9)
    assert not bool(row["flag_extreme"])


def test_extreme_returns_are_flagged_and_excluded_not_winsorised(history):
    d = history.dates[400]
    anchor = history.close_strictly_before(d)[1]
    df = attach_implied_returns(
        _panel([{"ticker": "TEST", "analyst_firm": "A", "action_date": d,
                 "price_target": anchor * 50, "vendor_spot_at_action": None}]),
        history,
    )
    assert bool(df.iloc[0]["flag_extreme"]) and not bool(df.iloc[0]["usable"])
    # the value is preserved, not clipped -- exclusion is visible, clipping is not
    assert df.iloc[0]["implied_return"] > 10


def test_vendor_spot_mismatch_is_detected(history):
    d = history.dates[400]
    anchor = history.close_strictly_before(d)[1]
    df = attach_implied_returns(
        _panel([{"ticker": "TEST", "analyst_firm": "A", "action_date": d,
                 "price_target": anchor * 1.2,
                 "vendor_spot_at_action": anchor * 1.9}]),
        history,
    )
    assert bool(df.iloc[0]["flag_vendor_spot_mismatch"])


def test_missing_target_and_missing_spot_are_flagged_separately(history):
    early = date(1990, 1, 2)  # before the history starts
    df = attach_implied_returns(
        _panel([
            {"ticker": "TEST", "analyst_firm": "A", "action_date": history.dates[400],
             "price_target": None, "vendor_spot_at_action": None},
            {"ticker": "TEST", "analyst_firm": "A", "action_date": early,
             "price_target": 100.0, "vendor_spot_at_action": None},
        ]),
        history,
    )
    assert bool(df.iloc[0]["flag_no_target"])
    assert bool(df.iloc[1]["flag_no_spot"])
    assert not df["usable"].any()


# --- firm offsets ----------------------------------------------------------


def test_firm_offsets_recover_the_injected_anchoring_bias():
    """The whole point of Stage 2a, checked against the generator's truth."""
    h = make_history(n=2600)
    records, truth = generate_panel("TEST", h, seed=5, n_firms=16)
    panel = pd.DataFrame([
        {"ticker": r.ticker, "analyst_firm": r.analyst_firm,
         "action_date": r.action_date, "price_target": r.price_target,
         "vendor_spot_at_action": None}
        for r in records
    ])
    df = attach_implied_returns(panel, h)
    offsets = estimate_firm_offsets(df)

    merged = offsets.table.merge(truth.firms, on="analyst_firm")
    corr = float(np.corrcoef(merged["mu_return"], merged["mu_f"])[0, 1])
    mae = float(np.mean(np.abs(merged["mu_return"] - merged["mu_f"])))
    assert corr > 0.9, f"estimated offsets barely track the true ones (r={corr:.2f})"
    assert mae < 0.05, f"estimated offsets off by {mae:.3f} on average"


def test_firm_correction_removes_the_firm_variance_component():
    h = make_history(n=2600)
    records, _ = generate_panel("TEST", h, seed=5, n_firms=16)
    panel = pd.DataFrame([
        {"ticker": r.ticker, "analyst_firm": r.analyst_firm,
         "action_date": r.action_date, "price_target": r.price_target,
         "vendor_spot_at_action": None}
        for r in records
    ])
    df = apply_firm_offsets(attach_implied_returns(panel, h),
                            estimate_firm_offsets(attach_implied_returns(panel, h)))
    before = firm_variance_share(df, "log_implied")
    after = firm_variance_share(df, "log_after_firm")
    assert before > 0.10, "generator did not produce a firm effect to remove"
    assert after < before / 5


def test_thin_panel_is_shrunk_hard_toward_the_mean():
    """A firm with one observation must not get its own offset."""
    rows = []
    for i in range(30):
        rows.append({"ticker": "T", "analyst_firm": "Broad",
                     "log_implied": 0.10 + 0.01 * (i % 5), "usable": True})
    rows.append({"ticker": "T", "analyst_firm": "Thin", "log_implied": 0.90,
                 "usable": True})
    offsets = estimate_firm_offsets(pd.DataFrame(rows))
    thin = offsets.table.set_index("analyst_firm").loc["Thin"]
    assert thin["shrinkage"] > 0.5
    assert thin["mu_log"] < 0.5, "a single wild observation became its own offset"


def test_single_ticker_panel_raises_the_separability_warning():
    rows = [{"ticker": "T", "analyst_firm": f"F{i%4}", "log_implied": 0.1,
             "usable": True} for i in range(20)]
    offsets = estimate_firm_offsets(pd.DataFrame(rows))
    assert any("1 ticker" in w for w in offsets.warnings)


def test_leave_one_out_excludes_the_records_own_contribution():
    rows = [{"ticker": "T", "analyst_firm": "A", "log_implied": v, "usable": True}
            for v in (0.0, 0.1, 0.2, 0.3, 1.0)]
    rows += [{"ticker": "T", "analyst_firm": "B", "log_implied": v, "usable": True}
             for v in (0.05, 0.07, 0.09, 0.11, 0.13)]
    df = pd.DataFrame(rows)
    offsets = estimate_firm_offsets(df)
    loo = apply_firm_offsets(df, offsets, leave_one_out=True)
    full = apply_firm_offsets(df, offsets, leave_one_out=False)
    outlier = loo[(loo["analyst_firm"] == "A") & (loo["log_implied"] == 1.0)]
    # The 1.0 record must not have pulled its own reference point up with it.
    assert float(outlier["firm_mu_log"].iloc[0]) < float(
        full[(full["analyst_firm"] == "A")]["firm_mu_log"].iloc[0]
    )


# --- sector adjustment -----------------------------------------------------


def test_sector_adjustment_strips_the_beta_scaled_move(history, sector_history):
    asof = history.dates[-1]
    d = history.dates[-200]
    anchor = history.close_strictly_before(d)[1]
    df = attach_implied_returns(
        _panel([{"ticker": "TEST", "analyst_firm": "A", "action_date": d,
                 "price_target": anchor * 1.30, "vendor_spot_at_action": None}]),
        history,
    )
    df["log_after_firm"] = df["log_implied"]
    beta = estimate_beta(history, sector_history, asof, sector_symbol="SECT")
    out = attach_sector_adjustment(df, sector_history, beta, asof)

    sec_then = sector_history.close_on_or_before(df.iloc[0]["spot_price_date"])[1]
    sec_now = sector_history.close_on_or_before(asof)[1]
    expected = np.log(1 + beta.beta * (sec_now / sec_then - 1))
    assert out.iloc[0]["sector_log"] == pytest.approx(expected)
    assert out.iloc[0]["log_after_sector"] == pytest.approx(
        out.iloc[0]["log_implied"] - expected
    )


def test_beta_is_shrunk_toward_one_and_reports_how_much(history, sector_history):
    fit = estimate_beta(history, sector_history, history.dates[-1], sector_symbol="SECT")
    assert fit.fitted
    assert 0.0 <= fit.shrinkage <= 1.0
    assert min(fit.beta_raw, 1.0) <= fit.beta <= max(fit.beta_raw, 1.0)


def test_beta_refuses_a_short_window_rather_than_fitting_noise():
    short = make_history(n=60)
    fit = estimate_beta(short, make_history("S", n=60, seed=3), short.dates[-1])
    assert not fit.fitted and fit.beta == 1.0
    assert "overlapping days" in fit.reason and fit.n_obs < 120


# --- age decay -------------------------------------------------------------


def test_age_decay_refuses_when_the_history_is_too_short(history):
    short = make_history(n=200)
    records, _ = generate_panel("TEST", make_history(n=2600), seed=3)
    panel = pd.DataFrame([
        {"ticker": r.ticker, "analyst_firm": r.analyst_firm,
         "action_date": r.action_date, "price_target": r.price_target,
         "vendor_spot_at_action": None}
        for r in records
    ])
    from src.model.debias import fit_age_decay

    df = attach_implied_returns(panel, short)
    fit = fit_age_decay(df, short, asof=short.dates[-1])
    assert not fit.fitted and fit.lam is None
    assert fit.reason


def test_decay_weights_are_zero_past_the_hard_cutoff():
    from src.model.debias import AgeDecayFit

    fit = AgeDecayFit(True, 0.004, 173.0, 0.001, 4.0, (0.002, 0.006), 500, 30,
                      "ok", pd.DataFrame())
    w = fit.weights(np.array([0.0, 90.0, 179.0, 181.0, 400.0]), max_age=180)
    assert w[0] == pytest.approx(1.0)
    assert w[1] > w[2] > 0
    assert w[3] == 0.0 and w[4] == 0.0


def test_unfitted_decay_falls_back_to_flat_weights_not_an_invented_half_life():
    from src.model.debias import AgeDecayFit

    fit = AgeDecayFit(False, None, None, None, None, None, 0, 0, "no fit",
                      pd.DataFrame())
    w = fit.weights(np.array([0.0, 100.0, 179.0, 181.0]), max_age=180)
    assert list(w[:3]) == [1.0, 1.0, 1.0] and w[3] == 0.0


def test_forecast_error_grows_with_record_age():
    """The empirical premise behind step (b), checked rather than assumed."""
    from src.model.debias import fit_age_decay

    h = make_history(n=2600)
    records, _ = generate_panel("TEST", h, seed=9)
    panel = pd.DataFrame([
        {"ticker": r.ticker, "analyst_firm": r.analyst_firm,
         "action_date": r.action_date, "price_target": r.price_target,
         "vendor_spot_at_action": None}
        for r in records
    ])
    df = attach_implied_returns(panel, h)
    fit = fit_age_decay(df, h, asof=h.dates[-1])
    assert len(fit.buckets) >= 5
    rmse = fit.buckets["rmse_log"].to_numpy()
    assert rmse[-1] > rmse[0], "stale targets were not less accurate than fresh ones"
